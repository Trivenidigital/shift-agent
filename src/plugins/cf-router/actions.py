"""cf-router subprocess + state helpers.

All file paths and command paths are deployed-system constants — the plugin
runs on the VPS, so /opt/shift-agent and /usr/local/bin are stable.

Test override: set the module-level path constants before invoking hooks
(see tests/test_cf_router_plugin.py for the pattern).
"""
from __future__ import annotations

import hashlib
import json
import contextvars
import hashlib
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, get_args

# Deployed-system paths (mutable for tests)
CONFIG_PATH = Path("/opt/shift-agent/config.yaml")
PENDING_PATH = Path("/opt/shift-agent/state/pending.json")
REVENUE_ROUTE_CLARIFICATION_PATH = Path("/opt/shift-agent/state/revenue-route-clarifications.json")
LEADS_PATH = Path("/opt/shift-agent/state/catering-leads.json")
PROPOSALS_PATH = Path("/opt/shift-agent/state/catering-proposals.json")
MENU_PENDING_PATH = Path("/opt/shift-agent/state/catering-menu-pending.json")
FLYER_PROJECTS_PATH = Path("/opt/shift-agent/state/flyer/projects.json")
FLYER_CUSTOMERS_PATH = Path("/opt/shift-agent/state/flyer/customers.json")
FLYER_GUEST_ORDERS_PATH = Path("/opt/shift-agent/state/flyer/guest_orders.json")
FLYER_REFERENCE_SCOPE_PATH = Path("/opt/shift-agent/state/flyer/reference_scope_pending.json")
FLYER_QUOTE_ECHO_PENDING_PATH = Path("/opt/shift-agent/state/flyer/quote_echo_pending.json")
FLYER_OUTBOUND_DEDUPE_PATH = Path("/opt/shift-agent/state/flyer/outbound_dedupe.json")
_DEFAULT_CF_ROUTER_INBOUND_DEDUPE_PATH = Path("/opt/shift-agent/state/cf-router-inbound-dedupe.json")
CF_ROUTER_INBOUND_DEDUPE_PATH = _DEFAULT_CF_ROUTER_INBOUND_DEDUPE_PATH
ROSTER_PATH = Path("/opt/shift-agent/roster.json")
LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")
THROTTLE_PATH = Path("/opt/shift-agent/state/cf-router-throttle.json")

APPLY_OWNER_DECISION_BIN = Path("/usr/local/bin/apply-catering-owner-decision")
APPLY_MENU_UPDATE_BIN = Path("/usr/local/bin/apply-menu-update")
NOTIFY_OWNER_BIN = Path("/usr/local/bin/shift-agent-notify-owner")
CREATE_LEAD_BIN = Path("/usr/local/bin/create-catering-lead")  # F7 path
CREATE_CATERING_PROPOSALS_BIN = Path("/usr/local/bin/create-catering-proposal-options")
SELECT_CATERING_PROPOSAL_BIN = Path("/usr/local/bin/select-catering-proposal")
CREATE_FLYER_PROJECT_BIN = Path("/usr/local/bin/create-flyer-project")
BARE_FLYER_SEND_BIN = Path("/usr/local/bin/bare-flyer-render-and-send")  # Approach B async render+send
HANDLE_SHIFT_SICK_CALL_BIN = Path("/usr/local/bin/handle-shift-sick-call")
HANDLE_FLYER_ONBOARDING_BIN = Path("/usr/local/bin/handle-flyer-onboarding")
HANDLE_FLYER_INTAKE_BIN = Path("/usr/local/bin/handle-flyer-intake")
STORE_FLYER_BRAND_ASSET_BIN = Path("/usr/local/bin/store-flyer-brand-asset")
MANAGE_FLYER_ACCOUNT_BIN = Path("/usr/local/bin/manage-flyer-account")
MANAGE_FLYER_GUEST_ORDER_BIN = Path("/usr/local/bin/manage-flyer-guest-order")
CHECK_FLYER_REFERENCE_SCOPE_BIN = Path("/usr/local/bin/check-flyer-reference-scope")

PYTHON_BIN = Path("/usr/local/lib/hermes-agent/venv/bin/python")
PLATFORM_DIR = Path("/opt/shift-agent")  # Where schemas.py lives
SRC_DIR = Path("/opt/shift-agent/src")
IDENTIFY_SENDER_BIN = Path("/usr/local/bin/identify-sender")
SEND_CATERING_ACK_BIN = Path("/usr/local/bin/send-catering-ack")

SUBPROCESS_TIMEOUT_SEC = 30
FLYER_RENDER_TIMEOUT_SEC = 900
ALERT_THROTTLE_SEC = 300  # Suppress duplicate Pushover alerts within 5 min
F7_DISPATCHER_LOOKBACK_SEC = 5  # Grace window when scanning audit log
                                 # for dispatcher_routed (matches deployed F7
                                 # daemon's `since_ts - 5` clock-skew tolerance)
FLYER_OUTBOUND_DEDUPE_TTL_SEC = 600
FLYER_OUTBOUND_DEDUPE_MAX = 256
CF_ROUTER_INBOUND_DEDUPE_TTL_SEC = 3600
CF_ROUTER_INBOUND_DEDUPE_MAX = 2048


def _ensure_platform_path() -> None:
    """Idempotently insert PLATFORM_DIR onto sys.path. Called once before any
    safe_io / schemas import. Avoids per-call sys.path growth that the
    previous implementation caused."""
    p = str(PLATFORM_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def _ensure_local_src_path() -> None:
    """Allow local tests to import repo modules when plugin is loaded directly."""
    src = Path(__file__).resolve().parents[2]
    if (src / "agents").exists():
        p = str(src)
        if p not in sys.path:
            sys.path.insert(0, p)


# === Owner / employee identity ===

def is_owner_chat(chat_id: str) -> bool:
    """Check if chat_id matches owner per config.yaml OR via identify-sender.

    Two-step check (mirrors F8 watchdog F13 fix):
      1. Strict equality against owner.self_chat_jid (the phone-JID side).
      2. If chat_id ends with @lid, fall back to identify-sender → role check.
         The bridge inbound notify often surfaces the owner's LID
         (<digits>@lid) rather than the phone-JID configured in
         owner.self_chat_jid; without this fallback, owner #XXXXX commands
         on the LID side would silently fail to be intercepted.
    Returns False on any error (config unreadable, identify-sender failure).
    """
    try:
        import yaml  # type: ignore
        with CONFIG_PATH.open() as f:
            cfg = yaml.safe_load(f)
        owner_jid = (cfg or {}).get("owner", {}).get("self_chat_jid", "")
        if owner_jid and chat_id == owner_jid:
            return True
        # F13: LID fallback via identify-sender
        if chat_id.endswith("@lid"):
            try:
                result = subprocess.run(
                    [str(IDENTIFY_SENDER_BIN), chat_id],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    doc = json.loads(result.stdout)
                    return doc.get("role") == "owner"
            except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
                return False
        return False
    except Exception:
        return False


def is_employee_chat(chat_id: str) -> bool:
    """Check if chat_id matches a phone in roster.json employees[].phone.

    chat_id may be `<phone>@s.whatsapp.net` or `<lid>@lid`. We match the
    phone part of the JID against roster phones (E.164).
    """
    try:
        with ROSTER_PATH.open() as f:
            roster = json.load(f)
        # Strip suffix from chat_id
        chat_part = chat_id.split("@", 1)[0] if "@" in chat_id else chat_id
        # Normalize: roster phones are like "+19045550101"; chat_part is "19045550101"
        chat_normalized = chat_part.lstrip("+")
        for emp in roster.get("employees", []):
            phone = emp.get("phone", "").lstrip("+")
            lid = (emp.get("lid") or "").split("@", 1)[0]
            if phone and chat_normalized == phone:
                return emp.get("status", "active") == "active"
            if lid and chat_part == lid:
                return emp.get("status", "active") == "active"
        return False
    except Exception:
        return False


def is_verified_employee_chat(chat_id: str) -> bool:
    """True only when identify-sender and roster both resolve active employee.

    F9 used to be alert-only, so roster-only matching was enough. Once F9 can
    skip the LLM and invoke Shift directly, the route must be gated by the same
    identity authority as dispatch_shift_agent: identify-sender metadata.
    """
    identity = identify_sender_metadata(chat_id)
    if identity.get("role") != "employee":
        return False
    return is_employee_chat(chat_id)


def has_pending_candidate_response(chat_id: str) -> bool:
    """Return True if this employee has a sent proposal awaiting YES/NO."""
    identity = identify_sender_metadata(chat_id)
    emp_id = identity.get("employee_id")
    if not emp_id:
        return False
    try:
        with PENDING_PATH.open(encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    proposals = doc.get("proposals", {}) if isinstance(doc, dict) else {}
    if isinstance(proposals, dict):
        rows = proposals.values()
    elif isinstance(proposals, list):
        rows = proposals
    else:
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("status") == "sent" and row.get("candidate_employee_id") == emp_id:
            return True
    return False


# === State lookups ===

ACTIONABLE_LEAD_STATUSES = frozenset({
    "AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED",
    "OWNER_EDITED", "OWNER_APPROVED",
})


def find_catering_lead_by_code(code: str) -> Optional[dict]:
    """Look up a non-terminal catering lead by owner_approval_code.

    Returns the lead dict (full record) if found in an actionable status;
    None otherwise. Caller passes this dict to invoke_apply_owner_decision
    to avoid a second read of LEADS_PATH (TOCTOU mitigation).
    """
    try:
        with LEADS_PATH.open() as f:
            store = json.load(f)
        for lead in store.get("leads", []):
            if lead.get("owner_approval_code") == code:
                if lead.get("status") in ACTIONABLE_LEAD_STATUSES:
                    return lead
        return None
    except Exception:
        return None


def send_canonical_followup_reply(chat_id: str, lead_id: str) -> bool:
    """Send the UX-mitigation reply on F7 primary-mode followup-suppressed paths.

    PR-CF1d 2026-05-12. When cf-router F7 primary-mode detects an active
    lead for the sender (Branch B of the F7 path), the LLM is bypassed —
    which means the customer's follow-up gets no reply unless this helper
    fires. Without it, a customer asking "what's the status?" gets total
    silence and assumes the bot is dead.

    Uses the existing send-catering-ack subprocess (which already prepends
    the canonical "⚕ Catering Agent" bridge prefix and handles both
    @s.whatsapp.net and @lid JID formats). Mirrors the F6 customer-ack
    pattern create-catering-lead uses on initial inquiry.

    HARD RULES compliance: this template is hard-coded — no LLM composition,
    no prices, no menu items, no fabricated promises. Just a status pointer
    and an invitation to reply for changes.

    Returns True on send success, False otherwise. Failures are non-fatal
    (caller still writes the suppressed audit row and returns skip).
    """
    template = (
        f"Your inquiry {lead_id} is with the owner for review. "
        f"They'll send a final quote within 24 hours. "
        f"Reply here if you need to adjust the inquiry."
    )
    try:
        result = subprocess.run(
            [
                str(SEND_CATERING_ACK_BIN),
                "--customer-jid", chat_id,
                "--message-text", template,
                "--lead-id", lead_id,
            ],
            capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT_SEC,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def find_active_catering_lead_by_sender(
    phone: Optional[str], chat_id: Optional[str],
) -> Optional[dict]:
    """Look up a non-terminal catering lead by sender identity (phone OR LID).

    PR-CF1d 2026-05-12. Used by cf-router F7 primary-mode to detect whether
    a customer already has an open lead — if yes, suppress the inbound
    (Branch B) to prevent multi-lead-creation under customer pressure (the
    Phase 11 failure mode where Kimi created L0007..L0010 from one customer
    thread by violating HARD RULES with fabricated proposals + per-person
    price quotes).

    Sender identity is fuzzy across our state files due to the LID-only-
    customer_phone cosmetic bug (see tasks/hermes-v0-13-0-plugin-api-recon-
    2026-05-11.md §Bugs). Match priority:
      1. `phone` (E.164) matches `customer_phone`
      2. `chat_id` ends with @lid AND its full string matches `customer_lid`
      3. `chat_id` ends with @lid AND `customer_phone` equals f"+{lid_digits}"
         (legacy LID-as-fake-phone persistence — the actual deployed shape
         in L0004..L0010 as of 2026-05-12)

    Non-terminal set: ACTIONABLE_LEAD_STATUSES (shared with
    find_catering_lead_by_code; includes OWNER_APPROVED to cover the brief
    transient state between owner-approve and quote-sent). Returns the
    most-recent matching lead (sorted by created_at desc), or None.
    """
    if not phone and not chat_id:
        return None

    # Extract LID digits if chat_id is @lid-formatted (for priority 3 match)
    lid_digits: Optional[str] = None
    if chat_id and chat_id.endswith("@lid"):
        digits_part = chat_id[: -len("@lid")]
        if digits_part.isdigit():
            lid_digits = digits_part

    try:
        with LEADS_PATH.open() as f:
            store = json.load(f)
        matches: list[dict] = []
        for lead in store.get("leads", []):
            if lead.get("status") not in ACTIONABLE_LEAD_STATUSES:
                continue
            cp = lead.get("customer_phone")
            cl = lead.get("customer_lid")
            # Priority 1: E.164 phone match
            if phone and cp == phone:
                matches.append(lead)
                continue
            # Priority 2: LID direct match
            if chat_id and cl == chat_id:
                matches.append(lead)
                continue
            # Priority 3: LID-as-fake-phone legacy match (most common today)
            if lid_digits and cp == f"+{lid_digits}":
                matches.append(lead)
                continue
        if not matches:
            return None
        # Most-recent by created_at (ISO-8601 lexically sortable)
        matches.sort(key=lambda l: l.get("created_at", ""), reverse=True)
        return matches[0]
    except Exception:
        return None


def find_menu_pending_by_code(code: str) -> Optional[dict]:
    """Look up the pending menu update if its confirmation_code matches.

    Returns the pending dict if matched; None otherwise.
    """
    try:
        with MENU_PENDING_PATH.open() as f:
            pending = json.load(f)
        if pending.get("confirmation_code") == code:
            return pending
        return None
    except Exception:
        return None


# === Subprocess invocations ===

def invoke_apply_owner_decision(code: str, decision: str,
                                lead: Optional[dict] = None) -> int:
    """Invoke apply-catering-owner-decision; returns exit code.

    For `approve`: caller passes the lead dict (snapshot from
    find_catering_lead_by_code). Quote source priority:
      1. If lead has a real (non-legacy) quote_text: pipe via --quote-text-stdin
      2. Else if lead has selected_items (CUSTOMER_FINALIZED): use
         --quote-from-lead-state for server-side rendering (PR-CF1c 2026-05-12)
      3. Else return 2 so the LLM can handle
    For `reject`: passes --reason "owner_reject_via_cf_router". Lead dict ignored.

    Always passes --sender-role owner (PR-CF1c bugfix: required arg was
    previously omitted, causing every cf-router approve invocation to fail
    with EXIT_INVALID_INPUT before reaching the quote-text logic).
    """
    try:
        env = {**os.environ, "PYTHONPATH": str(PLATFORM_DIR)}
        # PR-CF1c bugfix: --sender-role owner is required by the script's
        # privilege check. cf-router intercepts owner self-chat messages so
        # the role is implicit; pass it explicitly.
        cmd = [str(PYTHON_BIN), str(APPLY_OWNER_DECISION_BIN),
               "--code", code, "--decision", decision,
               "--sender-role", "owner"]
        stdin_text: Optional[str] = None
        if decision == "approve":
            if lead is None:
                return 4  # EXIT_NOT_FOUND — caller forgot to pass lead
            legacy_quote = lead.get("quote_text", "")
            has_real_quote = (
                legacy_quote
                and not legacy_quote.startswith("<legacy")
            )
            if has_real_quote:
                # Path 1: real LLM-drafted quote in lead — pipe via stdin (legacy F14 path)
                stdin_text = legacy_quote
                cmd.append("--quote-text-stdin")
            elif lead.get("selected_items"):
                # Path 2 (PR-CF1c): customer finalized; render server-side from lead state
                cmd.append("--quote-from-lead-state")
                # No stdin; the script renders the quote itself
            else:
                # Path 3: no quote source — let LLM handle (return non-zero)
                return 2  # EXIT_INVALID_INPUT
        elif decision == "reject":
            cmd.extend(["--reason", "owner_reject_via_cf_router"])
        result = subprocess.run(
            cmd, input=stdin_text, capture_output=True, text=True,
            env=env, timeout=SUBPROCESS_TIMEOUT_SEC,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124
    except Exception:
        return 1


def invoke_apply_menu_update(code: str, decision: str) -> int:
    """Invoke apply-menu-update; returns exit code."""
    try:
        env = {**os.environ, "PYTHONPATH": str(PLATFORM_DIR)}
        cmd = [str(PYTHON_BIN), str(APPLY_MENU_UPDATE_BIN),
               "--code", code, "--decision", decision]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            env=env, timeout=SUBPROCESS_TIMEOUT_SEC,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124
    except Exception:
        return 1


def invoke_select_catering_proposal(lead_id: str, chat_id: str, message_id: str,
                                    text: str) -> int:
    """Invoke select-catering-proposal; returns exit code."""
    try:
        result = subprocess.run(
            [
                str(PYTHON_BIN),
                str(SELECT_CATERING_PROPOSAL_BIN),
                "--lead-id", lead_id,
                "--customer-jid", chat_id,
                "--customer-message-id", message_id,
                "--selection-text", text,
            ],
            capture_output=True, text=True,
            env=os.environ.copy(), timeout=SUBPROCESS_TIMEOUT_SEC,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124
    except Exception:
        return 1


def invoke_create_catering_proposals(lead_id: str, chat_id: str, message_id: str,
                                     text: str) -> int:
    """Invoke create-catering-proposal-options in deterministic menu mode."""
    try:
        result = subprocess.run(
            [
                str(PYTHON_BIN),
                str(CREATE_CATERING_PROPOSALS_BIN),
                "--lead-id", lead_id,
                "--customer-jid", chat_id,
                "--source-message-id", message_id,
                "--request-text", text,
                "--auto-generate-from-menu",
            ],
            capture_output=True, text=True,
            env=os.environ.copy(), timeout=SUBPROCESS_TIMEOUT_SEC,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124
    except Exception:
        return 1


def fire_pushover_alert(title: str, body: str, priority: int = 2) -> None:
    """Fire a Pushover alert via the deployed shift-agent-notify-owner script.

    The deployed script's argparse takes `message` as a POSITIONAL argument
    after the optional --title / --priority flags. We insert `--` before
    `body` so a leading-dash in the body (e.g. "- can't come tomorrow")
    isn't misinterpreted by argparse as a flag.
    Best-effort: failures are logged to stderr, never raised.
    """
    try:
        subprocess.run(
            [str(NOTIFY_OWNER_BIN),
             "--priority", str(priority),
             "--title", title, "--", body],
            check=False, timeout=10,
        )
    except Exception as e:
        sys.stderr.write(f"cf-router: Pushover alert failed (non-fatal): {e}\n")


# === Throttle (de-dup repeated alerts) ===

def was_recently_alerted(chat_id: str, kind: str) -> bool:
    """Returns True if an alert of `kind` was fired for `chat_id` within
    ALERT_THROTTLE_SEC. Throttle state is on-disk (JSON) so it survives
    plugin reloads + concurrent gateway turns.
    """
    try:
        if not THROTTLE_PATH.exists():
            return False
        with THROTTLE_PATH.open() as f:
            state = json.load(f)
        key = f"{kind}:{chat_id}"
        last_ts = state.get(key)
        if last_ts is None:
            return False
        return (time.time() - last_ts) < ALERT_THROTTLE_SEC
    except Exception:
        return False


def mark_alerted(chat_id: str, kind: str) -> None:
    """Record alert timestamp for throttle. Uses safe_io.atomic_write_json
    so concurrent gateway turns don't clobber each other (deployed-pattern
    requirement per CLAUDE.md Part 1). Best-effort: failures are swallowed
    (worst case: a duplicate Pushover fires).
    """
    try:
        _ensure_platform_path()
        from safe_io import atomic_write_json  # type: ignore

        state: dict = {}
        if THROTTLE_PATH.exists():
            try:
                with THROTTLE_PATH.open() as f:
                    state = json.load(f)
            except Exception:
                state = {}
        key = f"{kind}:{chat_id}"
        state[key] = time.time()
        # Prune old entries (keep file from growing unbounded)
        cutoff = time.time() - 3 * ALERT_THROTTLE_SEC
        state = {k: v for k, v in state.items() if isinstance(v, (int, float)) and v >= cutoff}
        THROTTLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(THROTTLE_PATH, state)
    except Exception:
        pass


# === Flyer Hermes intent shadow context ===

_FLYER_INTENT_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "flyer_intent_context",
    default=None,
)


def _ensure_src_path() -> None:
    for path in (PLATFORM_DIR, SRC_DIR, Path(__file__).resolve().parents[2]):
        text = str(path)
        if text not in sys.path and path.exists():
            sys.path.insert(0, text)


def _import_flyer_intent_module():
    _ensure_src_path()
    try:
        from agents.flyer import intent as flyer_intent  # type: ignore

        return flyer_intent
    except Exception:
        import flyer_intent  # type: ignore

        return flyer_intent


def _short_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()[:32]


def flyer_intent_shadow_candidate(text: str, *, has_media: bool = False) -> bool:
    body = str(text or "").lower()
    if has_media:
        return True
    return bool(
        re.search(
            r"\b(flyer|flier|poster|banner|design|logo|approve|status|update|change|replace|"
            r"business name|phone number|address|free trial|start free|pick an idea)\b",
            body,
        )
    )


def begin_flyer_intent_shadow(
    *,
    text: str,
    chat_id: str,
    message_id: str,
    has_media: bool = False,
    customer_status: str = "",
    project_status: str = "",
    intake_status: str = "",
) -> contextvars.Token | None:
    try:
        flyer_intent = _import_flyer_intent_module()
    except Exception:
        return None

    requested_mode = os.environ.get("FLYER_HERMES_INTENT_MODE", "shadow")
    mode = flyer_intent.mode_from_value(requested_mode)
    if str(mode) == "off":
        return None
    candidate = flyer_intent_shadow_candidate(text, has_media=has_media)
    decision = flyer_intent.FlyerIntentDecision(decision_source="none")
    validation = flyer_intent.validate_flyer_intent_decision(
        decision,
        flyer_intent.FlyerIntentContext(
            mode=mode,
            raw_request=text if candidate else "",
            risk_scope="pre_project_customer_visible" if candidate else "none",
        ),
    )
    context = {
        "mode": str(mode),
        "requested_mode": requested_mode or "shadow",
        "text": str(text or "")[:4000],
        "decision": decision,
        "validation": validation,
        "message_id_hash": _short_hash(message_id),
        "chat_key_hash": _short_hash(chat_id),
        "has_media": bool(has_media),
        "customer_status": customer_status,
        "project_status": project_status,
        "intake_status": intake_status,
        "selected_project_id": "",
        "prior_active_project_id": "",
        "risk_scope": "pre_project_customer_visible" if candidate else "none",
        "route_events": [],
        "candidate": candidate,
        "classifier_status": "off",
        "classifier_latency_ms": 0,
        "classifier_error_kind": "",
        "classifier_error_detail": "",
    }
    return _FLYER_INTENT_CONTEXT.set(context)


def reset_flyer_intent_shadow(token: contextvars.Token | None) -> None:
    if token is not None:
        _FLYER_INTENT_CONTEXT.reset(token)


def record_flyer_intent_route_event(
    *,
    reason: str,
    subprocess_rc: Optional[int] = None,
    detail: str = "",
) -> None:
    context = _FLYER_INTENT_CONTEXT.get()
    if not context or not str(reason or "").startswith("flyer_"):
        return
    project_match = re.search(r"\bproject_id=([A-Z]\d{4,})\b", detail or "")
    status_match = re.search(r"\bstatus=([^;]+)", detail or "")
    context["route_events"].append(
        {
            "reason": str(reason or "")[:120],
            "subprocess_rc": subprocess_rc,
            "project_id": project_match.group(1) if project_match else "",
            "status": status_match.group(1).strip()[:80] if status_match else "",
            "detail_hint": _detail_action_hint(detail),
        }
    )


def _detail_action_hint(detail: str) -> str:
    text = str(detail or "").lower()
    for marker in (
        "approve=true",
        "revision=true",
        "status_check=true",
        "queued_status_check=true",
        "intake_ready=true",
        "fresh_flyer_intent=true",
    ):
        if marker in text:
            return marker
    return ""


def _terminal_route_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {}
    for event in reversed(events):
        if event.get("reason") != "flyer_active_project_bypassed":
            return event
    return events[-1]


def _risk_scope_from_action(actual_action: str, route_events: list[dict[str, Any]], candidate: bool) -> str:
    if actual_action in {"new_project", "revision", "approval", "manual_review", "status", "failure"}:
        return "active_project" if any(e.get("project_id") for e in route_events) else "pre_project_customer_visible"
    if actual_action in {"account_update", "onboarding_or_intake"}:
        return "active_customer"
    if candidate:
        return "pre_project_customer_visible"
    return "none"


def finalize_flyer_intent_shadow(
    *,
    hook_result: Optional[dict] = None,
    error: Exception | None = None,
    gateway: Any = None,
) -> None:
    context = _FLYER_INTENT_CONTEXT.get()
    if not context:
        return
    route_events = list(context.get("route_events") or [])
    candidate = bool(context.get("candidate"))
    mode = str(context.get("mode") or "shadow")
    if not route_events and not candidate and mode != "unsupported_active_mode":
        return
    terminal = _terminal_route_event(route_events)
    branch_reason = str((hook_result or {}).get("reason") or "")
    actual_route = str(terminal.get("reason") or ("plugin_error_passthrough" if error else "llm_passthrough"))
    actual_reason = str(terminal.get("detail_hint") or branch_reason or actual_route)[:200]
    subprocess_rc = terminal.get("subprocess_rc")
    selected_project_id = str(terminal.get("project_id") or "")
    project_status = str(terminal.get("status") or context.get("project_status") or "")

    try:
        flyer_intent = _import_flyer_intent_module()
    except Exception:
        return
    actual_action = flyer_intent.normalize_actual_action(actual_route, branch_reason)
    risk_scope = _risk_scope_from_action(actual_action, route_events, candidate)
    decision = context["decision"]
    validation = context["validation"]
    classifier_status = "off"
    classifier_latency_ms = 0
    classifier_error_kind = ""
    classifier_error_detail = ""
    classifier_setting = flyer_intent.classifier_setting_from_env(os.environ.get("FLYER_HERMES_INTENT_CLASSIFIER"))
    if classifier_setting in {"shadow", "active"}:
        if not route_events:
            classifier_status = "skipped_passthrough" if candidate else "skipped_not_candidate"
        else:
            try:
                classifier = _flyer_classifier_callable_from_gateway(gateway)
            except Exception as exc:
                classifier = None
                classifier_status = "error"
                classifier_error_kind = type(exc).__name__
            request = flyer_intent.FlyerClassifierRequest(
                text=str(context.get("text") or ""),
                has_media=bool(context.get("has_media")),
                actual_route=actual_route,
                actual_action=actual_action,
                route_sequence=[str(event.get("reason") or "") for event in route_events],
                branch_return_reason=branch_reason,
                customer_status=str(context.get("customer_status") or ""),
                project_status=project_status,
                intake_status=str(context.get("intake_status") or ""),
                risk_scope=risk_scope,
            )
            if classifier is None and classifier_setting == "active":
                decision = flyer_intent.deterministic_baseline_decision(request)
                validation = flyer_intent.validate_flyer_intent_decision(
                    decision,
                    flyer_intent.FlyerIntentContext(
                        mode=flyer_intent.mode_from_value(str(context.get("requested_mode") or "active")),
                        raw_request=str(context.get("text") or ""),
                        risk_scope=risk_scope,
                    ),
                )
                classifier_status = "success"
            elif classifier is None:
                if classifier_status != "error":
                    classifier_status = "skipped_no_gateway"
            else:
                audit_kwargs = dict(
                    mode=mode,
                    message_id_hash=str(context.get("message_id_hash") or ""),
                    chat_key_hash=str(context.get("chat_key_hash") or ""),
                    has_media=bool(context.get("has_media")),
                    actual_route=actual_route,
                    actual_reason=actual_reason,
                    actual_action=actual_action,
                    route_sequence=[str(event.get("reason") or "") for event in route_events],
                    subprocess_rc=subprocess_rc if isinstance(subprocess_rc, int) else None,
                    branch_return_reason=branch_reason,
                    selected_project_id=selected_project_id,
                    prior_active_project_id=str(context.get("prior_active_project_id") or ""),
                    project_status=project_status,
                    customer_status=str(context.get("customer_status") or ""),
                    intake_status=str(context.get("intake_status") or ""),
                    risk_scope=risk_scope,
                    active_customer_risk=risk_scope != "none",
                )
                worker = threading.Thread(
                    target=_run_flyer_classifier_shadow_worker,
                    kwargs={
                        "classifier": classifier,
                        "request": request,
                        "requested_mode": str(context.get("requested_mode") or "shadow"),
                        "raw_request": str(context.get("text") or ""),
                        "risk_scope": risk_scope,
                        "timeout_ms": _flyer_classifier_timeout_ms(),
                        "audit_kwargs": audit_kwargs,
                    },
                    daemon=True,
                )
                worker.start()
                return
    audit_flyer_hermes_intent_decision(
        mode=mode,
        decision=decision,
        validation=validation,
        classifier_status=classifier_status,
        classifier_latency_ms=classifier_latency_ms,
        classifier_error_kind=classifier_error_kind,
        classifier_error_detail=classifier_error_detail,
        message_id_hash=str(context.get("message_id_hash") or ""),
        chat_key_hash=str(context.get("chat_key_hash") or ""),
        has_media=bool(context.get("has_media")),
        actual_route=actual_route,
        actual_reason=actual_reason,
        actual_action=actual_action,
        route_sequence=[str(event.get("reason") or "") for event in route_events],
        subprocess_rc=subprocess_rc if isinstance(subprocess_rc, int) else None,
        branch_return_reason=branch_reason,
        selected_project_id=selected_project_id,
        prior_active_project_id=str(context.get("prior_active_project_id") or ""),
        project_status=project_status,
        customer_status=str(context.get("customer_status") or ""),
        intake_status=str(context.get("intake_status") or ""),
        risk_scope=risk_scope,
        active_customer_risk=risk_scope != "none",
    )


def _flyer_classifier_timeout_ms() -> int:
    try:
        return max(1, min(250, int(os.environ.get("FLYER_HERMES_INTENT_CLASSIFIER_TIMEOUT_MS", "50"))))
    except Exception:
        return 50


def _flyer_classifier_callable_from_gateway(gateway: Any) -> Any:
    if gateway is None:
        return None
    for name in ("flyer_intent_classifier", "classify_flyer_intent"):
        candidate = getattr(gateway, name, None)
        if callable(candidate):
            return candidate
    return None


def _run_flyer_classifier_shadow_worker(
    *,
    classifier: Any,
    request: Any,
    requested_mode: str,
    raw_request: str,
    risk_scope: str,
    timeout_ms: int,
    audit_kwargs: dict[str, Any],
) -> None:
    try:
        flyer_intent = _import_flyer_intent_module()
        result = flyer_intent.run_classifier_shadow(classifier, request, timeout_ms=timeout_ms)
        decision = result.decision
        validation = flyer_intent.validate_flyer_intent_decision(
            decision,
            flyer_intent.FlyerIntentContext(
                mode=flyer_intent.mode_from_value(requested_mode),
                raw_request=raw_request,
                risk_scope=risk_scope,
            ),
        )
        audit_flyer_hermes_intent_decision(
            decision=decision,
            validation=validation,
            classifier_status=str(result.status),
            classifier_latency_ms=int(result.latency_ms),
            classifier_error_kind=str(result.error_kind),
            classifier_error_detail=str(result.error_detail),
            **audit_kwargs,
        )
    except Exception as exc:
        sys.stderr.write(f"cf-router: flyer intent classifier worker failed (non-fatal): {exc}\n")


def audit_flyer_hermes_intent_decision(
    *,
    mode: str,
    decision: Any,
    validation: Any,
    message_id_hash: str,
    chat_key_hash: str,
    has_media: bool,
    actual_route: str,
    actual_reason: str,
    actual_action: str,
    route_sequence: list[str],
    subprocess_rc: Optional[int],
    branch_return_reason: str,
    classifier_status: str = "off",
    classifier_latency_ms: int = 0,
    classifier_error_kind: str = "",
    classifier_error_detail: str = "",
    selected_project_id: str = "",
    prior_active_project_id: str = "",
    project_status: str = "",
    customer_status: str = "",
    intake_status: str = "",
    risk_scope: str = "none",
    active_customer_risk: bool = False,
) -> None:
    try:
        _ensure_platform_path()
        _ensure_src_path()
        from safe_io import ndjson_append  # type: ignore
        from schemas import FlyerHermesIntentDecision  # type: ignore

        entry = FlyerHermesIntentDecision(
            type="flyer_hermes_intent_decision",
            ts=datetime.now(timezone.utc),
            schema_version=1,
            mode=mode,
            decision_source=getattr(decision, "decision_source", "none"),
            classifier_status=classifier_status,  # type: ignore[arg-type]
            classifier_latency_ms=classifier_latency_ms,
            classifier_error_kind=classifier_error_kind,
            classifier_error_detail=classifier_error_detail,
            message_id_hash=message_id_hash or "unknown",
            chat_key_hash=chat_key_hash,
            has_media=has_media,
            validator_ok=bool(getattr(validation, "ok", False)),
            validator_reasons=list(getattr(validation, "reasons", ()) or []),
            advisory_intent=str(getattr(decision, "intent", "unknown")),
            advisory_action=str(getattr(decision, "action", "observe")),
            confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
            would_mutate=bool(getattr(validation, "would_mutate", False)),
            actual_route=actual_route,
            actual_reason=actual_reason,
            actual_action=actual_action,  # type: ignore[arg-type]
            route_sequence=[item[:120] for item in route_sequence[:20]],
            route_terminal=True,
            subprocess_rc=subprocess_rc,
            branch_return_reason=branch_return_reason[:300],
            selected_project_id=selected_project_id,
            prior_active_project_id=prior_active_project_id,
            project_status=project_status,
            customer_status=customer_status,
            intake_status=intake_status,
            preview_source="actual",
            live_route_changed=False,
            active_customer_risk=active_customer_risk,
            risk_scope=risk_scope,  # type: ignore[arg-type]
        )
        ndjson_append(LOG_PATH, entry.model_dump_json())
    except Exception as e:
        sys.stderr.write(f"cf-router: flyer intent audit emit failed (non-fatal): {e}\n")


# === Audit ===

_QUOTE_ATTR_PAT = re.compile(r"quot|context|reply|stanza|participant", re.IGNORECASE)


def audit_raw_body(event: Any, chat_id: str, message_id: str, text: str) -> None:
    """Best-effort raw-body diagnostic row (quoted-APPROVE prerequisite).
    Never raises — same contract as audit_intercepted."""
    try:
        attrs: list[str] = []
        quotes: dict[str, str] = {}
        for obj, prefix in ((event, ""), (getattr(event, "source", None), "source.")):
            if obj is None:
                continue
            for name in dir(obj):
                if name.startswith("_"):
                    continue
                try:
                    val = getattr(obj, name)
                except Exception:  # noqa: BLE001
                    continue
                if val is None or callable(val):
                    continue
                attrs.append(prefix + name)
                if _QUOTE_ATTR_PAT.search(name):
                    quotes[(prefix + name)[:60]] = str(val)[:200]
                elif name == "raw_message":
                    # The bridge's full message structure — the most likely
                    # carrier of quote/reply metadata (probe 2 evidence: clean
                    # body, empty quote_attrs, raw_message present-uncaptured).
                    quotes[(prefix + name)[:60]] = str(val)[:600]
        _ensure_platform_path()
        from safe_io import ndjson_append  # type: ignore
        from schemas import CfRouterRawBody  # type: ignore
        entry = CfRouterRawBody(
            type="cf_router_raw_body",
            ts=datetime.now(timezone.utc),
            message_id=(message_id or "")[:200],
            chat_id=(chat_id or "")[:200],
            body_head=(text or "")[:400],
            body_len=len(text or ""),
            event_attrs=attrs[:40],
            quote_attrs=quotes,
        )
        ndjson_append(LOG_PATH, entry.model_dump_json())
    except Exception as e:  # noqa: BLE001
        print(f"cf-router audit_raw_body failed (non-fatal): {e}", file=sys.stderr)


def audit_intercepted(reason: str, chat_id: str, code: Optional[str] = None,
                      subprocess_rc: Optional[int] = None, detail: str = "",
                      binding_source: str = "") -> None:
    """Emit a `cf_router_intercepted` audit row via the deployed
    safe_io.ndjson_append chokepoint.

    Best-effort: failures are logged to stderr; the plugin still returns
    its action so the gateway flow continues. The wrapping try/except is
    critical — if this raises, the outer plugin try/except converts a
    successful skip into a `None` (LLM re-runs after apply already fired).
    """
    record_flyer_intent_route_event(reason=reason, subprocess_rc=subprocess_rc, detail=detail)
    try:
        _ensure_platform_path()
        from safe_io import ndjson_append  # type: ignore
        from schemas import CfRouterIntercepted  # type: ignore
        entry = CfRouterIntercepted(
            type="cf_router_intercepted",
            ts=datetime.now(timezone.utc),
            reason=reason,  # type: ignore
            chat_id=chat_id,
            code=code,
            subprocess_rc=subprocess_rc,
            detail=detail[:2000],
            binding_source=binding_source,  # type: ignore[arg-type]
        )
        ndjson_append(LOG_PATH, entry.model_dump_json())
    except Exception as e:
        sys.stderr.write(f"cf-router: audit emit failed (non-fatal): {e}\n")


def identify_sender_metadata(identifier: str) -> dict:
    """Return identify-sender JSON for phone/LID/JID, or an unknown-role stub."""
    try:
        result = subprocess.run(
            [str(IDENTIFY_SENDER_BIN), identifier],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"role": "unknown"}
        doc = json.loads(result.stdout)
        if isinstance(doc, dict):
            return doc
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        pass
    return {"role": "unknown"}


def audit_dispatcher_routed(
    *,
    message_id: str,
    chat_id: str,
    routed_to_skill: str,
    message_shape: str = "text",
) -> None:
    """Emit the standard dispatcher_routed row for deterministic cf-router routes.

    F9 now claims verified employee sick-call traffic before the LLM. Writing the
    same route row keeps routing-accuracy monitoring aligned with the SKILL
    dispatcher contract instead of creating a parallel metric.
    """
    try:
        _ensure_platform_path()
        from safe_io import ndjson_append  # type: ignore
        from schemas import DispatcherRouted  # type: ignore

        identity = identify_sender_metadata(chat_id)
        sender_lid = identity.get("lid")
        if not sender_lid and chat_id.endswith("@lid"):
            sender_lid = chat_id
        entry = DispatcherRouted(
            type="dispatcher_routed",
            ts=datetime.now(timezone.utc),
            message_id=message_id,
            sender_role=identity.get("role", "unknown"),
            message_shape=message_shape,  # type: ignore[arg-type]
            routed_to_skill=routed_to_skill,
            sender_phone=identity.get("phone_normalized"),
            sender_lid=sender_lid,
        )
        ndjson_append(LOG_PATH, entry.model_dump_json())
    except Exception as e:
        sys.stderr.write(f"cf-router: dispatcher_routed audit emit failed (non-fatal): {e}\n")


def invoke_shift_sick_call(*, chat_id: str, text: str, message_id: str) -> tuple[int, str, str]:
    """Run the Shift-owned deterministic sick-call entrypoint."""
    try:
        result = subprocess.run(
            [
                str(PYTHON_BIN),
                str(HANDLE_SHIFT_SICK_CALL_BIN),
                "--chat-id", chat_id,
                "--message-text", text,
                "--message-id", message_id,
            ],
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or "timeout"
    except OSError as exc:
        return 127, "", str(exc)


def audit_source_vs_new(
    *,
    sender_phone: str = "",
    customer_id: str = "",
    original_intent: str = "exact_source_edit",
    choice: str = "clarification_sent",
    pending_age_sec: int = 0,
    customer_followup_instruction: str = "",
) -> None:
    """Emit a `flyer_source_vs_new_chosen` audit row via the deployed
    safe_io.ndjson_append chokepoint.

    Best-effort: failures are logged to stderr so a broken audit pipeline
    cannot regress the customer-facing behavior.
    """
    try:
        _ensure_platform_path()
        from safe_io import ndjson_append  # type: ignore
        from schemas import FlyerSourceVsNewChosen  # type: ignore
        entry = FlyerSourceVsNewChosen(
            type="flyer_source_vs_new_chosen",
            ts=datetime.now(timezone.utc),
            sender_phone=sender_phone,
            customer_id=customer_id,
            original_intent=original_intent,  # type: ignore
            choice=choice,  # type: ignore
            pending_age_sec=pending_age_sec,
            customer_followup_instruction=customer_followup_instruction[:500],
        )
        ndjson_append(LOG_PATH, entry.model_dump_json())
    except Exception as e:
        sys.stderr.write(f"cf-router: source_vs_new audit emit failed (non-fatal): {e}\n")


# === F7 path: catering-dispatcher-watchdog (PR-CF7) ===
#
# Replaces the standalone `catering-dispatcher-watchdog` daemon
# (~427 LOC + systemd unit) with a delayed-rescue path inside this plugin.
# The classifier + threshold logic is ported verbatim from the deployed
# daemon so the 26 classifier tests in tests/test_catering_dispatcher_classifier.py
# continue to pin the regex behavior.

# Conservative regex classifier — requires "catering" word OR (event/food+headcount).
# Tighter than what an LLM would do, intentionally biased toward false negatives.
_CATERING_PRIMARY = re.compile(r"\bcatering\b|\bcaterer\b", re.IGNORECASE)
_HEADCOUNT_PATTERNS = [
    re.compile(
        r"(\d+)\s*(?:people|persons?|guests?|ppl|attendees?|heads?|"
        r"meals?|plates?|covers?|settings?|members?|pax)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:for|of|serve|serving|feed|cater(?:ing)?\s+(?:to|for))\s+(\d+)\b", re.IGNORECASE),
    re.compile(r"\b(\d+)\s+(?:vegetarian|veg|non[\s-]?veg|vegan|jain|halal|kosher)\b", re.IGNORECASE),
]
_EVENT_KEYWORDS = re.compile(
    r"\b(?:event|reception|wedding|birthday|anniversary|graduation|baby\s*shower|"
    r"engagement|housewarming|festival|gathering|party|celebration|function)\b",
    re.IGNORECASE,
)
_FOOD_KEYWORDS = re.compile(
    r"\b(?:menu|food|dinner|lunch|breakfast|buffet|biryani|veg(?:etarian)?|"
    r"non[\s-]?veg|nonveg|halal|kosher|vegan|jain|spread|appetizers?)\b",
    re.IGNORECASE,
)
_DELIVERY_KEYWORDS = re.compile(
    r"\b(?:deliver(?:y|ed)?|pickup|drop[\s-]?off|setup)\b", re.IGNORECASE,
)
_FLYER_INTENT = re.compile(
    r"\b(?:"
    r"flyer|flyers|flier|fliers|poster|posters|banner|banners|"
    r"invite|invites|invitation|invitations|"
    r"social\s+post|instagram\s+(?:post|story)|ig\s+(?:post|story)|"
    r"graphic|creative|"
    r"(?:design|make|create|generate|build)\s+(?:a\s+|an\s+)?"
    r"(?:flyer|flier|poster|banner|invite|invitation|social\s+post|"
    r"instagram\s+(?:post|story)|ig\s+(?:post|story)|graphic)"
    r")\b",
    re.IGNORECASE,
)
_NEW_FLYER_REQUEST = re.compile(
    r"\b(?:need|create|creare|make|generate|design|build)\s+"
    r"(?:a\s+|an\s+)?(?:flyer|flier|poster|banner|creative|graphic)\b"
    r"|\b(?:flyer|flier|poster|banner)\s+for\b",
    re.IGNORECASE,
)
_NEW_FLYER_VERB = re.compile(r"\b(?:need|create|creare|make|generate|design|build)\b", re.IGNORECASE)
_FLYER_WORK_OBJECT = re.compile(
    r"\b(?:flyer|flyers|flier|fliers|poster|posters|banner|banners|"
    r"marketing\s+material|creative|graphic)\b",
    re.IGNORECASE,
)
_FRESH_FLYER_BRIEF_DETAIL = re.compile(
    r"\b(?:"
    r"from\s+\d{1,2}\s*(?:am|pm)\s+(?:to|-)\s+\d{1,2}\s*(?:am|pm)|"
    r"\d{1,2}\s*(?:am|pm)\s+(?:to|-)\s+\d{1,2}\s*(?:am|pm)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"today|tomorrow|weekend|event|special|sale|offer|discount|"
    r"menu|snacks?|items?|top\s+\d+|grand\s+opening|festival|"
    r"breakfast|lunch|dinner"
    r")\b",
    re.IGNORECASE,
)
_REGISTERED_CONTEXTUAL_FLYER_DETAIL = re.compile(
    r"\b(?:combo|combos|curries|biryani|pulav|pulao|menu|sale|special|offer|"
    r"discount|memorial\s+day|weekend|occasion|banner|visual\s+pictures?|"
    r"pictures?|photos?|logo)\b",
    re.IGNORECASE,
)
_PRICE_AMOUNT = re.compile(r"(?:\$|rs\.?\s*)?\b\d{1,4}(?:\.\d{2})\b", re.IGNORECASE)
_REVENUE_PRICE_AMOUNT = re.compile(
    r"(?:\$|rs\.?\s*)\s*\d{1,5}(?:\.\d{2})?\b"
    r"|\b\d{1,5}(?:\.\d{2})?\s*\$"
    r"|\b\d{1,5}(?:\.\d{2})?\s*(?:dollars?|usd)\b"
    r"|\b\d{1,5}\.\d{2}\b",
    re.IGNORECASE,
)
_REVENUE_ORDER_QUANTITY = re.compile(
    r"\b(?:customi[sz]ed\s+orders?|orders?|pre[\s-]?orders?|tray|trays|"
    r"count|counts|pcs|pieces|dozen|box|boxes|servings?)\b",
    re.IGNORECASE,
)
_REVENUE_FOOD_OR_MENU = re.compile(
    r"\b(?:menu|desserts?|sweets?|cake|cakes|custard|kheer|halwa|ladoo|laddu|"
    r"rasmalai|jamun|jalebi|katli|pak|biryani|pulav|pulao|snacks?|combo|combos)\b",
    re.IGNORECASE,
)
_REVENUE_PROMO_CONTEXT = re.compile(
    r"\b(?:graduation|celebrate|celebration|special|sale|offer|discount|"
    r"weekend|festival|holiday|party|event)\b",
    re.IGNORECASE,
)
_REVENUE_ROUTE_CHOICE_FLYER = re.compile(
    r"\b(?:flyer|flier|poster|design|graphic|creative|promo|promotional|"
    r"social\s+post|instagram|ad)\b",
    re.IGNORECASE,
)
_REVENUE_ROUTE_CHOICE_CATERING = re.compile(
    r"\b(?:catering|cater|order|orders|ordering|tray|trays|delivery|pickup|"
    r"quote|lead|menu\s+request)\b",
    re.IGNORECASE,
)
_FLYER_CAMPAIGN_CTA = re.compile(
    r"^\s*(?:"
    r"start\s+free\s+(?:trial|trail)"
    r"|create\s+one\s+flyer\s*-\s*\$?4"
    r"|create\s+one\s+flyer\s+for\s+\$?4"
    r"|quick\s+flyer\s*-\s*\$?4"
    r"|pay\s+and\s+create\s+flyer"
    r"|act\s+now!?\s+save\s+time\s+and\s+money"
    r"|help\s+me\s+create\s+a\s+beautiful\s+flyer\s+for\s+my\s+business"
    r"|i\s+want\s+to\s+set\s+up\s+flyer\s+studio\s+for\s+my\s+business"
    r")\s*[.!]*\s*$",
    re.IGNORECASE,
)
_MEDIA_TEMPLATE_EDIT = re.compile(
    r"\b(?:dosa|idly|menu|special|combo|price|prices?|item|items|breakfast|"
    r"lunch|dinner|offer|deal|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_MEDIA_FLYER_UPDATE = re.compile(
    r"\b(?:update|edit|change|modify|revise|correct|fix)\b.*"
    r"\b(?:this|attached|existing|old|previous|sample|reference)?\s*"
    r"(?:flyer|flier|poster|banner|invite|invitation|creative|graphic)\b"
    r"|\b(?:change|update|edit|modify|revise|correct|fix)\b.*"
    r"\b(?:date|time|name|phone|address|location|venue|title|headline|text)\b",
    re.IGNORECASE,
)
_CURRENT_BRAND_UPLOAD = re.compile(
    r"\b(?:logo|brand|font|color|colour|palette)\b",
    re.IGNORECASE,
)
_WRONG_FLYER_CORRECTION = re.compile(
    r"\b(?:wrong|different|not\s+what|responded\s+with|instead\s+of|"
    r"looks\s+completely\s+different)\b.*\b(?:dosa|breakfast|menu|special|flyer|flier|poster)\b",
    re.IGNORECASE,
)

_PROPOSAL_REQUEST_VERB = re.compile(
    r"\b(?:send|share|show|give|create|make|prepare|draft|suggest|propose|"
    r"build|generate|want|wants|wanted|need|needs|needed|like|likes|"
    r"request|requests)\b",
    re.IGNORECASE,
)
_PROPOSAL_REQUEST_OBJECT = re.compile(
    r"\b(?:proposal menus?|menu proposals?|proposal|proposals|"
    r"sample menus?|combination menus?|combinations? menus?|"
    r"menu options?|options?|packages?|menus?)\b",
    re.IGNORECASE,
)
_PROPOSAL_MENU_CONSTRAINT = re.compile(
    r"\bmenu\b.{0,80}\b(?:should|must|need|needs|contain|include|exclude|avoid|no|not)\b"
    r"|\b(?:add|include|exclude|avoid|remove|more)\b.{0,80}\b"
    r"(?:appetizers?|starters?|mains?|entrees?|veg|vegetarian|non[\s-]?veg|"
    r"non[\s-]?vegetarian|beef|pork|chicken|mutton|goat|fish|menu)\b",
    re.IGNORECASE,
)
_PROPOSAL_PASSIVE_WAIT = re.compile(
    r"\b(?:will\s+wait|waiting|wait\s+for|want\s+to\s+wait|"
    r"no\s+need\s+to\s+send|not\s+yet|any\s+update)\b",
    re.IGNORECASE,
)
_PROPOSAL_ACTION_VERB = r"(?:choose|chose|select|selected|pick|picked|take|taking|use|go\s+with|proceed\s+with|confirm|finalize)"
_PROPOSAL_SELECTION_NUMBERED = re.compile(
    rf"\b{_PROPOSAL_ACTION_VERB}\b.{{0,40}}\b(?:(?:option|proposal|menu)\s*)?#?\s*[1-3]\b",
    re.IGNORECASE | re.DOTALL,
)
_PROPOSAL_SELECTION_BARE_NUMBERED = re.compile(
    r"^\s*(?:(?:option|proposal|menu)\s*)?#?\s*[1-3]\s*$",
    re.IGNORECASE,
)
_PROPOSAL_SELECTION_NAMED = re.compile(
    rf"\b{_PROPOSAL_ACTION_VERB}\b.{{0,40}}\b(?:premium|balanced|classic)\b",
    re.IGNORECASE | re.DOTALL,
)


def is_proposal_request(text: str) -> bool:
    """Return True for actionable proposal/menu-option requests.

    Requires a request verb within 80 characters before a proposal object.
    Passive status texts such as "will wait for two menu proposals" are not
    requests when the whole text is passive/status.
    """
    normalized = " ".join((text or "").split())
    if not normalized:
        return False
    if _PROPOSAL_PASSIVE_WAIT.search(normalized):
        return False
    if _PROPOSAL_MENU_CONSTRAINT.search(normalized):
        return True
    for obj in _PROPOSAL_REQUEST_OBJECT.finditer(normalized):
        window = normalized[max(0, obj.start() - 80):obj.start()]
        if _PROPOSAL_REQUEST_VERB.search(window):
            return True
    return False


def is_proposal_selection(text: str) -> bool:
    """Return True for customer selections from a sent proposal set."""
    normalized = " ".join((text or "").split())
    if not normalized:
        return False
    return bool(
        _PROPOSAL_SELECTION_NUMBERED.search(normalized)
        or _PROPOSAL_SELECTION_BARE_NUMBERED.search(normalized)
        or _PROPOSAL_SELECTION_NAMED.search(normalized)
    )


def find_selectable_proposal_set(lead_id: str) -> Optional[dict]:
    """Return latest proposal row only when it is selectable."""
    if not lead_id:
        return None
    try:
        with PROPOSALS_PATH.open(encoding="utf-8") as f:
            state = json.load(f)
        sets = state.get("sets", [])
        if not isinstance(sets, list):
            return None
        matches = [
            row for row in sets
            if isinstance(row, dict)
            and row.get("lead_id") == lead_id
        ]
        if not matches:
            return None
        latest = max(matches, key=lambda row: _proposal_set_sequence(row))
        if latest.get("status") != "SENT":
            return None
        if not str(latest.get("outbound_message_id") or "").strip():
            return None
        return latest
    except Exception:
        return None


_PROPOSAL_SET_SEQUENCE_SUFFIX = re.compile(r"-(\d+)$")


def _proposal_set_sequence(row: dict) -> int:
    proposal_set_id = str(row.get("proposal_set_id") or "")
    match = _PROPOSAL_SET_SEQUENCE_SUFFIX.search(proposal_set_id)
    if not match:
        return -1
    try:
        return int(match.group(1))
    except ValueError:
        return -1


def classify_catering(text: str) -> tuple[bool, list[str]]:
    """Return (is_catering, signals). Conservative: needs strong evidence.

    Ported verbatim from the deployed F7 daemon. The 26 cases in
    tests/test_catering_dispatcher_classifier.py pin the multi-signal
    threshold (catering_keyword AND any-other) OR (headcount AND event)
    OR (headcount AND food AND (delivery OR event)).
    """
    if not text or len(text) < 10:
        return False, ["too_short"]

    signals: list[str] = []
    if _CATERING_PRIMARY.search(text):
        signals.append("primary:catering")
    for pat in _HEADCOUNT_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                hc = int(m.group(1))
                if 5 <= hc <= 10000:
                    signals.append(f"headcount:{hc}")
                    break
            except (ValueError, IndexError):
                pass
    if _EVENT_KEYWORDS.search(text):
        signals.append("event_keyword")
    if _FOOD_KEYWORDS.search(text):
        signals.append("food_keyword")
    if _DELIVERY_KEYWORDS.search(text):
        signals.append("delivery_keyword")

    has_catering = any(s.startswith("primary:catering") for s in signals)
    has_headcount = any(s.startswith("headcount:") for s in signals)
    has_event = "event_keyword" in signals
    has_food = "food_keyword" in signals
    has_delivery = "delivery_keyword" in signals

    is_catering = (
        (has_catering and (has_headcount or has_event or has_food or has_delivery))
        or (has_headcount and has_event)
        or (has_headcount and has_food and (has_delivery or has_event))
    )

    if not is_catering:
        signals.append("rejected:insufficient_evidence")

    return is_catering, signals


def classify_flyer_intent(text: str) -> tuple[bool, list[str]]:
    """Return True for explicit flyer/design requests.

    This classifier is intentionally narrower than the agent's LLM intake.
    cf-router only needs enough certainty to avoid stealing flyer work into
    Catering F7 primary-mode when messages mention food, events, or festivals.
    """
    if not text or len(text) < 4:
        return False, ["too_short"]
    if _FLYER_INTENT.search(text):
        return True, ["flyer_intent"]
    return False, ["rejected:no_flyer_intent"]


def classify_ambiguous_revenue_brief(text: str) -> tuple[bool, list[str]]:
    """Return True for concrete revenue-zone briefs that need one route question.

    This is router-only triage. It does not decide creative content or Catering
    fields; it catches the gap where a customer sends priced food/menu/event
    copy that could be either a promotional Flyer brief or a Catering/order
    request, but neither explicit classifier is allowed to claim it.
    """
    body = " ".join(flyer_visible_message_text(text).split())
    if len(body) < 40:
        return False, ["too_short"]
    if classify_flyer_intent(body)[0]:
        return False, ["explicit_flyer"]
    if classify_catering(body)[0]:
        return False, ["explicit_catering"]
    if is_flyer_onboarding_intent(body) or is_flyer_campaign_cta(body):
        return False, ["explicit_flyer_account"]

    signals: list[str] = []
    if _REVENUE_PRICE_AMOUNT.search(body):
        signals.append("price_amount")
    if _EVENT_KEYWORDS.search(body) or _REVENUE_PROMO_CONTEXT.search(body):
        signals.append("event_or_promo")
    if _FOOD_KEYWORDS.search(body) or _REVENUE_FOOD_OR_MENU.search(body):
        signals.append("food_or_menu")
    if _REVENUE_ORDER_QUANTITY.search(body):
        signals.append("order_quantity")

    required = {"price_amount", "event_or_promo", "food_or_menu", "order_quantity"}
    if required.issubset(set(signals)):
        return True, signals
    signals.append("rejected:insufficient_ambiguous_revenue_evidence")
    return False, signals


def classify_revenue_route_choice(text: str) -> Optional[str]:
    """Classify a reply to the Flyer-vs-Catering clarification."""
    body = " ".join(flyer_visible_message_text(text).split()).lower()
    if not body:
        return None
    if re.fullmatch(r"(?:both|both please|both pls|flyer and catering|catering and flyer)", body):
        return "both"
    wants_flyer = bool(_REVENUE_ROUTE_CHOICE_FLYER.search(body))
    wants_catering = bool(_REVENUE_ROUTE_CHOICE_CATERING.search(body))
    if wants_flyer and wants_catering:
        return "both"
    if wants_flyer:
        return "flyer"
    if wants_catering:
        return "catering"
    return None


def revenue_route_clarification_reply() -> str:
    return "I can help with this. Is this for a promotional flyer, or a catering/order request?"


def revenue_route_both_reply() -> str:
    return "I can help with both. Which should I start first: promotional flyer or catering/order request?"


def _load_revenue_route_clarification_doc() -> dict:
    _ensure_platform_path()
    from safe_io import safe_load_json  # type: ignore

    doc, _status = safe_load_json(
        REVENUE_ROUTE_CLARIFICATION_PATH,
        default={"version": 1, "pending": {}},
    )
    if not isinstance(doc, dict):
        doc = {"version": 1, "pending": {}}
    pending = doc.get("pending")
    if not isinstance(pending, dict):
        doc["pending"] = {}
    doc["version"] = 1
    return doc


def _write_revenue_route_clarification_doc(doc: dict) -> None:
    _ensure_platform_path()
    from safe_io import atomic_write_json  # type: ignore

    atomic_write_json(REVENUE_ROUTE_CLARIFICATION_PATH, doc)


def save_revenue_route_clarification(
    *,
    chat_id: str,
    original_text: str,
    message_id: str,
    sender_phone: Optional[str],
    sender_role: str,
    signals: list[str],
) -> None:
    _ensure_platform_path()
    from safe_io import flock  # type: ignore

    with flock(REVENUE_ROUTE_CLARIFICATION_PATH):
        doc = _load_revenue_route_clarification_doc()
        doc["pending"][chat_id] = {
            "chat_id": chat_id,
            "original_text": original_text[:4000],
            "message_id": message_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sender_phone": sender_phone,
            "sender_role": sender_role,
            "signals": [str(sig)[:80] for sig in signals[:20]],
        }
        _write_revenue_route_clarification_doc(doc)


def get_revenue_route_clarification(chat_id: str) -> Optional[dict]:
    _ensure_platform_path()
    from safe_io import flock  # type: ignore

    with flock(REVENUE_ROUTE_CLARIFICATION_PATH):
        doc = _load_revenue_route_clarification_doc()
        row = doc.get("pending", {}).get(chat_id)
        return row if isinstance(row, dict) else None


def pop_revenue_route_clarification(chat_id: str) -> Optional[dict]:
    _ensure_platform_path()
    from safe_io import flock  # type: ignore

    with flock(REVENUE_ROUTE_CLARIFICATION_PATH):
        doc = _load_revenue_route_clarification_doc()
        row = doc.get("pending", {}).pop(chat_id, None)
        _write_revenue_route_clarification_doc(doc)
        return row if isinstance(row, dict) else None


def is_registered_customer_contextual_flyer_brief(text: str) -> bool:
    """Return True for natural promo details from an already-active Flyer customer.

    Registered customers often continue a Flyer Studio request with
    business-promotion details rather than repeating the word "flyer" in every
    message. Keep this scoped to strong menu/promo signals so generic chat does
    not get stolen before Hermes.
    """
    body = " ".join(flyer_visible_message_text(text).split())
    if not body:
        return False
    if is_flyer_campaign_cta(body) or is_flyer_project_status_request(body):
        return False
    if classify_catering(body)[0]:
        return False
    lower = body.lower()
    if classify_flyer_intent(body)[0] and _REGISTERED_CONTEXTUAL_FLYER_DETAIL.search(body):
        return True
    if _PRICE_AMOUNT.search(body) and _REGISTERED_CONTEXTUAL_FLYER_DETAIL.search(body):
        return True
    return bool(
        re.search(r"\bget\s+the\s+(?:flyer|flier|poster|banner)\s+ready\b", lower)
        and _REGISTERED_CONTEXTUAL_FLYER_DETAIL.search(body)
    )


def is_flyer_onboarding_intent(text: str) -> bool:
    """Return True for explicit Flyer Studio registration/account setup text."""
    return bool(re.search(
        r"\b(register|sign\s*up|signup|onboard|setup|set\s+up|act\s+now|help\s+me\s+create\s+a\s+beautiful\s+flyer|flyer account|flyer studio|plan|free\s+trial|start\s+trial|try\s+free)\b",
        text or "",
        flags=re.IGNORECASE,
    ))


def flyer_visible_message_text(text: str) -> str:
    """Return the user-visible body after Hermes' sender block, if present."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if lines and lines[0].startswith("[shift-agent-sender "):
        return "\n".join(lines[1:]).strip()
    return (text or "").strip()


def flyer_campaign_cta_text(text: str) -> str:
    """Return normalized exact campaign CTA text, or empty string."""
    visible = flyer_visible_message_text(text)
    body = " ".join(visible.split())
    if _FLYER_CAMPAIGN_CTA.search(body):
        return body
    for line in reversed([line.strip() for line in visible.splitlines() if line.strip()]):
        candidate = " ".join(line.split())
        if _FLYER_CAMPAIGN_CTA.search(candidate):
            return candidate
    return ""


def is_quick_flyer_campaign_cta(text: str) -> bool:
    """Return True for the payment-first one-off flyer CTA."""
    body = flyer_campaign_cta_text(text).lower()
    return bool(body and ("$4" in body or "one flyer" in body or "quick flyer" in body))


def is_flyer_campaign_cta(text: str) -> bool:
    """Return True for exact inbound replies generated by campaign buttons."""
    return bool(flyer_campaign_cta_text(text))


def is_flyer_legacy_trial_link_followup(text: str) -> bool:
    """Return True for registered-customer replies caused by the old trial CTA.

    Older final-package messages used a WhatsApp deep link with START FREE
    TRIAL text even after the customer was already registered. When customers
    quote or complain about that link, the message is no longer an exact CTA
    reply, so route it to account-aware recovery instead of starter prompts.
    """
    body = " ".join(flyer_visible_message_text(text).split()).lower()
    if "start free trial" not in body and "start free trail" not in body:
        return False
    if "flyer" not in body:
        return False
    return bool(re.search(
        r"\b(?:already|free\s+tier|free\s+plan|clicked?|clicking|link|final\s+flyer|final\s+response)\b",
        body,
    ))


def flyer_campaign_source(text: str) -> str:
    """Map a campaign CTA reply to the intake source."""
    body = flyer_campaign_cta_text(text).lower()
    if not body:
        return ""
    if is_quick_flyer_campaign_cta(body):
        return "quick_flyer"
    if body.startswith("start free") or "help me create" in body:
        return "start_trial"
    if "act now" in body or "set up flyer studio" in body:
        return "act_now"
    return "new_flyer"


_FLYER_APPROVAL_ALIASES = {
    "approve",
    "approved",
    "ok",
    "yes",
    "looks good",
    "go ahead",
    "send it",
    "finalize",
    "finalise",
}
_FLYER_DELIVERY_STATE_APPROVAL_ALIASES = _FLYER_APPROVAL_ALIASES - {"ok", "yes"}
_FLYER_FINAL_APPROVAL_STATUSES = {"revising_design", "awaiting_final_approval", "delivered_with_warning"}


def is_flyer_approval_text(text: str) -> bool:
    """Return True for exact Flyer Studio final-approval replies."""
    body = " ".join(flyer_visible_message_text(text).split())
    normalized = body.lower().strip(" .!,:;")
    return normalized in _FLYER_APPROVAL_ALIASES


def flyer_routing_decision_preview(
    text: str,
    *,
    active_project: Optional[dict] = None,
    latest_message_id: str = "",
    has_media: bool = False,
) -> dict:
    """Compute a read-only Flyer routing decision summary for tests/reports."""
    body = " ".join(flyer_visible_message_text(text).split())
    project_id = str((active_project or {}).get("project_id") or "")
    fresh = should_start_new_flyer_over_active(body, has_media=has_media)
    fresh_bypasses_active = should_bypass_active_flyer_project_for_fresh_request(
        body,
        active_project,
        has_media=has_media,
    )
    active_status = str((active_project or {}).get("status") or "")
    if is_flyer_approval_text(body) and active_status in _FLYER_FINAL_APPROVAL_STATUSES:
        route = "approval"
        reason = "approval_text"
    elif fresh_bypasses_active:
        route = "new_project"
        reason = "fresh_new_request"
    elif active_status == "finalizing_assets" and (
        is_flyer_approval_text(body)
        or is_flyer_send_now_intent(body)
        or is_flyer_delivery_state_intent(body)
    ):
        route = "approval"
        reason = "finalizing_assets_retry"
    elif fresh and active_project:
        route = "active_intake"
        reason = "active_intake_similar_request"
    elif is_flyer_project_status_request(body):
        route = "status_reply"
        reason = "status_request"
    elif active_project and active_status == "manual_edit_required":
        route = "manual_queue"
        reason = "active_manual_review_project"
    elif active_project and is_flyer_revision_intent(body):
        route = "revision"
        reason = "revision_intent"
    elif active_project and body:
        route = "revision"
        reason = "active_project_default"
    else:
        route = "passthrough"
        reason = "no_flyer_route"
    return {
        "route": route,
        "selected_project_id": project_id,
        "reason": reason,
        "fresh_new_request_detected": fresh,
        "active_project_bypassed": bool(active_project and route == "new_project"),
        "latest_message_id": latest_message_id,
    }


def similar_to_active_project_request(body: str, active_project: dict) -> bool:
    current = " ".join(str(active_project.get("raw_request") or "").split()).lower()
    incoming = " ".join((body or "").split()).lower()
    if not current or not incoming:
        return False
    if incoming == current or incoming in current or current in incoming:
        return True
    return SequenceMatcher(None, incoming, current).ratio() >= 0.82


_LAYOUT_SIZE_REVISION = re.compile(
    r"\b(?:look|make|keep|show)\b.{0,80}\b(?:smaller|less\s+prominent|tiny|smaller\s+font)\b"
    r"|\b(?:smaller|less\s+prominent|tiny|smaller\s+font)\b.{0,80}\b(?:contact|phone|number|address|location)\b",
    re.IGNORECASE,
)
_LAYOUT_CONTACT_TARGET = re.compile(r"\b(?:contact|phone|number|address|location)\b", re.IGNORECASE)
# Revision-specific focus phrasing only. Generic "highlight/emphasize" is
# omitted: "create a new flyer highlighting our specials" is a plausible new
# brief, and routing it as a revision would corrupt an active project. The
# authoritative agent-side extractor still handles the broader set once a
# message is (conservatively) attached.
_LAYOUT_FOCUS_REVISION = re.compile(
    r"\b(?:main\s+focus|focus\s+should\s+be|focus\s+on)\b", re.IGNORECASE
)
_LAYOUT_OFFER_TARGET = re.compile(
    r"\b(?:service|services|offer|offers|items|menu|products|specials)\b", re.IGNORECASE
)
# New-campaign signal: a new date, time window, or occasion. Run against the
# business-name-stripped brief so a business named e.g. "Sunday Salon" does not
# trip its own revisions. Deliberately a date/time/occasion detector rather than
# a wholesale digit check, so the actual phone/street numbers being resized in a
# layout revision ("make phone 555-1234 smaller") do NOT read as a new campaign.
# Content nouns (menu/items/offer/special) are absent — valid emphasis targets.
_NEW_CAMPAIGN_SCHEDULE = re.compile(
    r"\b(?:"
    r"from\s+\d{1,2}\s*(?:am|pm)\s+(?:to|-)\s+\d{1,2}\s*(?:am|pm)|"
    r"\d{1,2}\s*(?:am|pm)\s+(?:to|-)\s+\d{1,2}\s*(?:am|pm)|"
    r"\d{1,2}\s*(?:am|pm)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"today|tomorrow|weekend|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|sept|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|sept|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)|"
    r"\d{1,2}\s*/\s*\d{1,2}|"
    r"event|grand\s+opening|festival|sale|top\s+\d+|"
    # Occasion/holiday names (this portfolio is ethnic SMBs — festival flyers
    # are a core campaign type, so these are realistic new-campaign signals).
    r"diwali|deepavali|holi|navratri|navaratri|ugadi|pongal|onam|eid|ramadan|"
    r"christmas|thanksgiving|halloween|valentine|easter|new\s+year"
    r")\b",
    re.IGNORECASE,
)


def _strip_business_scope_span(body: str, business_name: str) -> str:
    """Remove the (clean, stored) business-name span so new-campaign detection
    runs on the brief's remainder. A business literally named "Sunday Salon" /
    "Eid Market" must not have its own name counted as a new-campaign signal.
    Uses the stored business name (not the loosely-extracted requested scope,
    which can over-capture the whole brief tail)."""
    if not business_name:
        return body
    return re.sub(re.escape(business_name), " ", body, flags=re.IGNORECASE)


def _is_layout_emphasis_revision_wording(body: str) -> bool:
    """Router-local lexical mirror of agents/flyer
    ``workflow._extract_layout_emphasis_revision_instruction``: "make the
    contact/address smaller" or "focus should be on the services" style edits to
    an existing flyer. Kept here because cf-router classifies lexically and does
    not import the flyer agent (authoritative extraction still runs agent-side
    via ``invoke_update_flyer_project``)."""
    lower = body.lower()
    size_edit = bool(_LAYOUT_SIZE_REVISION.search(lower) and _LAYOUT_CONTACT_TARGET.search(lower))
    focus_edit = bool(_LAYOUT_FOCUS_REVISION.search(lower) and _LAYOUT_OFFER_TARGET.search(lower))
    return size_edit or focus_edit


def _is_same_business_layout_revision(body: str, active_project: Optional[dict]) -> bool:
    """Same-business layout/emphasis edits worded as "create a new flyer for
    <current business> with the address smaller / focus on the services" are
    active-project revisions, not fresh work orders. Without this guard the
    "create a new flyer" wording trips ``should_start_new_flyer_over_active`` and
    bypasses the active project — regression of the 42bdda5 contract documented
    in ``hooks._try_flyer_active_project_intercept``."""
    if not active_project:
        return False
    requested = flyer_requested_business_scope(body)
    if not requested:
        return False
    active_business = str(((active_project.get("fields") or {}).get("event_or_business_name")) or "").strip()
    if not active_business or not _business_scope_matches(requested, active_business):
        return False
    if not _is_layout_emphasis_revision_wording(body):
        return False
    # New-campaign detection runs on the brief WITHOUT the business-name span so a
    # business named e.g. "Sunday Salon" doesn't disqualify its own revisions. A
    # new date/time/occasion means a new work order; default to the bypass path
    # when present. False-bypass is the safe, pre-existing behaviour; false-attach
    # would corrupt an active project. Using a date/time/occasion detector (not a
    # wholesale digit check) keeps contact/address numbers being resized from
    # reading as a new campaign.
    remainder = _strip_business_scope_span(body, active_business)
    if _NEW_CAMPAIGN_SCHEDULE.search(remainder):
        return False
    return True


def should_bypass_active_flyer_project_for_fresh_request(
    text: str,
    active_project: Optional[dict],
    *,
    has_media: bool = False,
) -> bool:
    body = " ".join((text or "").split())
    if not should_start_new_flyer_over_active(body, has_media=has_media):
        return False
    if not active_project:
        return True
    status = str(active_project.get("status") or "")
    if _media_revision_targets_delivered_active_project(body, status=status, has_media=has_media):
        return False
    if not has_media and _is_same_business_layout_revision(body, active_project):
        return False
    return (
        has_media
        or status not in {"intake_started", "collecting_required_info", "awaiting_assets"}
        or not flyer_project_has_required_fields(active_project)
        or not similar_to_active_project_request(body, active_project)
    )


def _media_revision_targets_delivered_active_project(text: str, *, status: str, has_media: bool) -> bool:
    if status not in {"revising_design", "delivered", "delivered_with_warning"} or not has_media:
        return False
    body = " ".join(flyer_visible_message_text(text).split())
    if not body:
        return False
    if not is_exact_reference_edit_request(body, has_media=True):
        return False
    if not is_flyer_revision_intent(body):
        return False
    return bool(re.search(
        r"\b(?:existing|current|same)\s+"
        r"(?:flyer|flier|poster|banner|image|artwork|creative|graphic)\b",
        body,
        flags=re.IGNORECASE,
    ))


_REFERENCE_CONCEPT_ADAPTATION_RE = re.compile(
    r"\b(?:adopt|use|follow|borrow|take|match)\s+"
    r"(?:this|the|attached|uploaded)?\s*"
    r"(?:flyer|flier|poster|banner|image|artwork|creative|graphic)\s+"
    r"(?:concept|style|idea|look|theme|design|inspiration)\b"
    r"|"
    r"\b(?:use|treat)\s+(?:this|the|attached|uploaded)\s+"
    r"(?:flyer|flier|poster|banner|image|artwork|creative|graphic)\s+"
    r"as\s+(?:a\s+)?(?:reference|inspiration|concept)\b"
    r"|"
    r"\b(?:make|create|design|generate)\s+(?:a\s+)?(?:new\s+)?"
    r"(?:flyer|flier|poster|banner|creative|graphic)\s+"
    r"(?:similar\s+to|inspired\s+by)\s+(?:this|the|attached|uploaded)\b"
    r"|"
    r"\b(?:make|create|design|generate)\s+(?:a\s+|the\s+)?same\s+"
    r"(?:flyer|flier|poster|banner|creative|graphic)\b"
    r"(?=[\s\S]{0,180}\b(?:same\s+content|theme|style|look)\b)",
    re.IGNORECASE,
)


def is_reference_concept_adaptation_request(text: str, *, has_media: bool = False) -> bool:
    """Return True for attached-reference requests that want inspiration, not source preservation."""
    if not has_media:
        return False
    body = " ".join(flyer_visible_message_text(text).split())
    if not body:
        return False
    return bool(_REFERENCE_CONCEPT_ADAPTATION_RE.search(body))


def should_start_new_flyer_over_active(text: str, *, has_media: bool = False) -> bool:
    """Return True when inbound content should not attach to old flyer state.

    Active projects still own approval, concept selection, and natural revision
    notes. Explicit create/need flyer requests and media-backed menu/template
    edits are new work orders; routing them as revisions causes stale flyer
    projects to swallow unrelated customer jobs.
    """
    if is_flyer_campaign_cta(text):
        return False
    body = " ".join((text or "").split())
    if not body:
        return False
    if is_vague_flyer_start(body, has_media=has_media):
        return False
    if is_exact_reference_edit_request(body, has_media=has_media):
        return True
    if _NEW_FLYER_REQUEST.search(body):
        return True
    if _NEW_FLYER_VERB.search(body) and _FLYER_WORK_OBJECT.search(body):
        return True
    if (
        _FLYER_WORK_OBJECT.search(body)
        and _FRESH_FLYER_BRIEF_DETAIL.search(body)
        and not is_flyer_project_status_request(body)
        and not is_flyer_revision_intent(body)
    ):
        return True
    if _WRONG_FLYER_CORRECTION.search(body):
        return True
    if has_media and not _CURRENT_BRAND_UPLOAD.search(body):
        return bool(_MEDIA_TEMPLATE_EDIT.search(body) or _MEDIA_FLYER_UPDATE.search(body))
    return False


def is_exact_reference_edit_request(text: str, *, has_media: bool = False) -> bool:
    """Return True for source-preserving edits to an attached flyer/artwork.

    This is intentionally narrower than generic reference use. Requests like
    "extract items from this sample and create a flyer" should still enter the
    new-poster generation path, while "remove that extra 08:00" must not be
    regenerated from scratch.
    """
    if not has_media:
        return False
    body = " ".join(flyer_visible_message_text(text).split())
    lower = body.lower().strip(" .!,:;")
    if not lower:
        return False
    if _CURRENT_BRAND_UPLOAD.search(lower) and not re.search(
        r"\b(?:flyer|flier|poster|banner|image|artwork|date|time|text|item|price|extra)\b|\$\s*\d",
        lower,
        flags=re.IGNORECASE,
    ):
        return False
    if is_reference_concept_adaptation_request(body, has_media=has_media):
        return False
    edit_verb = re.search(
        r"\b(?:remove|delete|change|replace|swap|fix|correct|update|edit|modify|revise|add|make|set|put|say)\b",
        lower,
        flags=re.IGNORECASE,
    )
    edit_target = re.search(
        r"\b(?:extra|date|time|name|phone|address|location|venue|title|headline|text|item|price|logo|flyer|poster|image|artwork|say)\b"
        r"|\$\s*\d",
        lower,
        flags=re.IGNORECASE,
    )
    source_cue = re.search(
        r"\b(?:this|attached|uploaded|existing|current|same|source)\s+"
        r"(?:flyer|flier|poster|banner|image|artwork|creative|graphic)\b",
        lower,
        flags=re.IGNORECASE,
    )
    source_preserving_edit = bool(edit_verb and edit_target and source_cue)
    create_new = re.search(
        r"\b(?:create|make|generate|design|build)\b.*\b(?:flyer|flier|poster|banner|creative|graphic)\b"
        r"|\b(?:extract|use|take)\b.*\b(?:items?|prices?|menu|content)\b.*\b(?:from|in)\b",
        lower,
        flags=re.IGNORECASE,
    )
    if create_new and not source_preserving_edit:
        return False
    return bool(edit_verb and edit_target)


def is_vague_flyer_start(text: str, *, has_media: bool = False) -> bool:
    """Return True when a flyer request should enter guided/text preflight.

    Complete briefs should flow straight through. Short openers like "create
    flyer" or "help me make flyer" need language/mode assistance.
    """
    if has_media:
        return False
    body = " ".join(flyer_visible_message_text(text).split())
    lower = body.lower().strip(" .!,:;")
    if not lower:
        return False
    if not classify_flyer_intent(lower)[0] and not is_flyer_onboarding_intent(lower):
        return False
    # Explicit "flyer for ..." asks with concrete brief cues are new-work
    # intents, not vague starters. Keep this narrow so generic onboarding
    # lines like "create a marketing flyer for my business" still route to
    # guided intake.
    if re.search(r"\b(?:flyer|flier|poster|banner)\s+for\s+\S+", lower) and re.search(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"today|tomorrow|weekend|special|sale|offer|discount|menu|"
        r"contact|call|whatsapp)\b|\$\d|\b\d{1,2}\b",
        lower,
    ):
        return False
    has_detail = (
        "$" in lower
        or bool(re.search(r"\b\d{1,3}\s*%\s*(?:off|discount)?\b", lower))
        or ":" in body
        or bool(re.search(r"\b\d{1,2}\s*(?:am|pm)\b", lower))
        or bool(re.search(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|weekend|special|menu|sale|offer|discount|grand opening|graduation|party|parties|celebration|class|event|seo|aeo|geo|paid ads|content creation|combo|combos|banner|visual pictures?|photos?|pictures?)\b", lower))
    )
    if has_detail:
        return False
    return bool(re.search(r"\b(?:create|make|need|help|start|try|get)\b.*\b(?:flyer|flier|poster|marketing|flyer studio)\b", lower))


def flyer_message_has_brief_detail(text: str) -> bool:
    """True when a flyer message carries a concrete commercial brief — a dollar amount or a percentage
    discount — i.e. an actual offer to generate from, NOT a request for ideas.

    Used so a real creation request like "improvise this prompt for flyer generation: <brief with 10%
    off>" or "improve this flyer prompt: <combo for $9.99>" routes to generation/intake, not the
    sample-prompt menu, even when it says "prompt"/"improvise"/"improve" (operator 2026-06-07: "real
    flyer creation/intake must beat the sample-prompt menu").

    Deliberately narrow: only "$" and "N%" — signals that never appear in a pure request-for-ideas. We
    do NOT key off occasion/menu words (weekend, breakfast, graduation, ...): those occur in genuine
    sample requests ("give me weekend flyer ideas") and would wrongly suppress the menu, and per the
    operator's standing direction Python must not carry occasion keyword lists. This is ROUTING
    detection only (Python owns routing); it is NOT the scene/theme wiring.
    """
    lower = " ".join(flyer_visible_message_text(text).split()).lower()
    return "$" in lower or bool(re.search(r"\b\d{1,3}\s*%", lower))


# ─────────────────────────────────────────────────────────────────
# 2026-05-28 — intake-bypass helper + script detector.
# See plan + design at tasks/flyer-intake-bypass-{plan,design}-2026-05-28.md.
# Hermes-as-brain compliance: composes the three deployed classifiers
# (classify_flyer_intent, is_exact_reference_edit_request,
# should_start_new_flyer_over_active) — does NOT classify on its own.
# Pure functions, no I/O, no input mutation.
# ─────────────────────────────────────────────────────────────────


# Mirrors the inline `protected_statuses` set at hooks.py:2367-2376 exactly
# (8 actively-collecting-brief statuses where the wizard is in flight + must
# never be interrupted). Drift between this set and the inline definition
# would split bypass behavior; tests assert exact membership match.
_INTAKE_PROTECTED_STATUSES = frozenset({
    "choosing_sample_idea",
    "text_awaiting_brief",
    "guided_collecting_goal",
    "guided_collecting_schedule",
    "guided_collecting_items",
    "guided_collecting_location",
    "guided_collecting_assets",
    "brief_pending_approval",
})


# Account-lifecycle boundary — operator decision 2026-05-28 #1.
# Expired/cancelled/suspended customers stay in wizard / re-onboarding path;
# bypass is reserved for brand-new senders (customer is None) + active/trial.
_CUSTOMER_BYPASS_ELIGIBLE_STATUSES = frozenset({"active", "trial"})


def should_bypass_intake_for_clear_intent(
    text: str,
    customer: Optional[dict],
    intake_session: Optional[dict],
    *,
    has_media: bool = False,
) -> Optional[str]:
    """Returns the bypass_reason Literal value when intake should be skipped,
    else None.

    Two preconditions block bypass (evaluated first):
    1. intake_session.status in _INTAKE_PROTECTED_STATUSES — operator is
       mid-collection of a guided brief; never interrupt.
    2. customer is not None AND customer.status not in {"active","trial"} —
       expired/cancelled/suspended stay in wizard. Brand-new senders
       (customer is None) ARE bypass-eligible.

    Five signal branches (return the matching bypass_reason):
    - "edit_with_media": is_exact_reference_edit_request matches.
      The F0108 / 22.png case.
    - "new_flyer_with_media": should_start_new_flyer_over_active AND has_media.
    - "new_flyer_text_only": should_start_new_flyer_over_active AND not has_media.
    - "existing_active_customer_intent" / "existing_trial_customer_intent":
      classify_flyer_intent matches AND customer.status active/trial.
      Preserves pre-PR behavior of the lines 2378-2382 bypass for these states.

    Hermes-as-brain: composes deployed classifiers. Does not classify itself."""
    # Precondition 1: protected statuses block bypass.
    status = str((intake_session or {}).get("status") or "")
    if status in _INTAKE_PROTECTED_STATUSES:
        return None

    # Precondition 2: account-lifecycle boundary.
    if customer is not None and customer.get("status") not in _CUSTOMER_BYPASS_ELIGIBLE_STATUSES:
        return None

    # Signal 1: edit-with-media — most unambiguous.
    if is_exact_reference_edit_request(text, has_media=has_media):
        return "edit_with_media"

    # Signals 2 + 3: clear new-flyer request. Split on media for replay.
    if should_start_new_flyer_over_active(text, has_media=has_media):
        return "new_flyer_with_media" if has_media else "new_flyer_text_only"

    # Signals 4 + 5: existing-customer fast path. Trial vs active split for triage.
    intent_match, _reasons = classify_flyer_intent(text)
    if intent_match and customer:
        customer_status = customer.get("status")
        if customer_status == "trial":
            return "existing_trial_customer_intent"
        if customer_status == "active":
            return "existing_active_customer_intent"

    return None


# Unicode block ranges for the script detector. Basic Devanagari (U+0900–U+097F)
# covers Hindi/Marathi/Nepali common case; basic Tamil (U+0B80–U+0BFF) covers
# Tamil. Non-ASCII fallback ("other") catches Spanish/etc.
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_TAMIL_RE = re.compile(r"[஀-௿]")
_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")


def _detect_inbound_script(text: str) -> str:
    """Returns the dominant non-Latin script name from
    {"latin","devanagari","tamil","other"}. Default "latin" when the text
    is pure ASCII or empty.

    Used by the bypass wiring (Commit 3) to populate
    FlyerIntakeBypassed.inbound_script for regional-SMB telemetry —
    operator decision 2026-05-28 #3. Detection-and-act on non-Latin
    scripts is deferred; this field accumulates the data so a follow-up
    PR can act without backfill.

    Pure function; no I/O. When mixed-script text appears, the order
    of checks below establishes precedence: Devanagari > Tamil > other."""
    body = str(text or "")
    if not body or not _NON_ASCII_RE.search(body):
        return "latin"
    if _DEVANAGARI_RE.search(body):
        return "devanagari"
    if _TAMIL_RE.search(body):
        return "tamil"
    return "other"


# ─────────────────────────────────────────────────────────────────
# 2026-05-28 — intake-bypass shadow context + audit emit.
# Mirrors the flyer_intent_shadow pattern at lines 499-794 — same
# contextvars.Token lifecycle, same try/except non-fatal finalize
# discipline. See design §3 for the design-phase resolution.
# ─────────────────────────────────────────────────────────────────


_FLYER_INTAKE_BYPASS_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "flyer_intake_bypass_context",
    default=None,
)

# Pending-token stash — the intake intercept (deep in the dispatch impl)
# opens the bypass shadow and stashes the token here. The dispatch wrapper's
# finally block consumes + resets. Avoids plumbing tokens through every
# intercept return value.
_PENDING_BYPASS_TOKEN: contextvars.ContextVar[contextvars.Token | None] = contextvars.ContextVar(
    "pending_flyer_intake_bypass_token",
    default=None,
)


# F-pattern regex for outcome derivation per design §4 — primary-intercept
# success paths embed an F-pattern project_id in hook_result["reason"];
# intermediate-intercept paths don't. Build-phase replay gate verifies the
# asymmetry against a captured audit-log sample (see Commit 3 test fixture).
_FLYER_PROJECT_ID_RE = re.compile(r"\bF\d{4,}\b")


def begin_flyer_intake_bypass_shadow(
    *,
    chat_id: str,
    message_id: str,
    bypass_reason: str,
    has_media: bool,
    customer_state: str,
    intake_session_status: str,
    inbound_script: str,
) -> contextvars.Token | None:
    """Open a bypass-tracking context that survives until the dispatch
    wrapper's finally block calls finalize_flyer_intake_bypass_shadow.
    Returns a contextvars.Token the caller must hand to reset()."""
    context = {
        "chat_id_hash": _short_hash(chat_id),
        "message_id_hash": _short_hash(message_id),
        "bypass_reason": str(bypass_reason or ""),
        "has_media": bool(has_media),
        "customer_state": str(customer_state or ""),
        "intake_session_status": str(intake_session_status or ""),
        "inbound_script": str(inbound_script or "latin"),
        "begin_ts": datetime.now(timezone.utc),
    }
    return _FLYER_INTAKE_BYPASS_CONTEXT.set(context)


def reset_flyer_intake_bypass_shadow(token: contextvars.Token | None) -> None:
    """Reset the bypass-context ContextVar binding. Mirrors
    reset_flyer_intent_shadow — must be called in the dispatch wrapper's
    finally block, even when the begin() call returned None."""
    if token is not None:
        _FLYER_INTAKE_BYPASS_CONTEXT.reset(token)


def _derive_bypass_outcome(hook_result: Optional[dict]) -> tuple[str, str, str]:
    """Plan §9 pinned mechanism (post-revision): F-pattern regex extraction
    from hook_result['reason']. Returns (outcome, project_id, handler_intercept).

    - hook_result is None → ("unrouted", "", "")
    - hook_result["reason"] contains F-pattern → ("routed_to_project", "F...", "")
    - else → ("intermediate_intercept_handled", "", reason[:80])"""
    if hook_result is None:
        return ("unrouted", "", "")
    reason = str(hook_result.get("reason") or "")
    m = _FLYER_PROJECT_ID_RE.search(reason)
    if m:
        return ("routed_to_project", m.group(0), "")
    return ("intermediate_intercept_handled", "", reason[:80])


def finalize_flyer_intake_bypass_shadow(*, hook_result: Optional[dict] = None) -> None:
    """Emit FlyerIntakeBypassOutcome via the deployed audit chokepoint
    (safe_io.ndjson_append → LOG_PATH). No-op when no bypass fired during
    the dispatch (context is None).

    Mirrors finalize_flyer_intent_shadow exception-suppression discipline:
    any failure here is written to stderr and never propagated — the
    dispatch flow must NEVER be blocked by audit emit failures."""
    context = _FLYER_INTAKE_BYPASS_CONTEXT.get()
    if not context:
        return
    outcome, project_id, handler = _derive_bypass_outcome(hook_result)
    elapsed = datetime.now(timezone.utc) - context["begin_ts"]
    elapsed_ms = max(0, int(elapsed.total_seconds() * 1000))
    try:
        _ensure_platform_path()
        _ensure_src_path()
        from safe_io import ndjson_append  # type: ignore
        from schemas import FlyerIntakeBypassOutcome  # type: ignore

        entry = FlyerIntakeBypassOutcome(
            ts=datetime.now(timezone.utc),
            chat_id_hash=str(context.get("chat_id_hash") or ""),
            outcome=outcome,  # type: ignore[arg-type]
            project_id=project_id,
            handler_intercept=handler,
            elapsed_ms=elapsed_ms,
        )
        ndjson_append(LOG_PATH, entry.model_dump_json())
    except Exception as e:
        sys.stderr.write(
            f"cf-router: flyer_intake_bypass_outcome emit failed (non-fatal): "
            f"{type(e).__name__}: {e}\n"
        )


def note_flyer_intake_bypass_active(
    *,
    chat_id: str,
    message_id: str,
    bypass_reason: str,
    has_media: bool,
    customer_state: str,
    intake_session_status: str,
    inbound_script: str,
) -> None:
    """Called from _try_flyer_intake_intercept on bypass. Opens the bypass
    shadow + stashes the token in _PENDING_BYPASS_TOKEN. The dispatch
    wrapper's finally consumes the pending token via
    consume_pending_flyer_intake_bypass_token() for reset."""
    token = begin_flyer_intake_bypass_shadow(
        chat_id=chat_id,
        message_id=message_id,
        bypass_reason=bypass_reason,
        has_media=has_media,
        customer_state=customer_state,
        intake_session_status=intake_session_status,
        inbound_script=inbound_script,
    )
    _PENDING_BYPASS_TOKEN.set(token)


def consume_pending_flyer_intake_bypass_token() -> contextvars.Token | None:
    """Atomic read-and-clear of the pending bypass token. Called by the
    dispatch wrapper's finally block to hand the token to reset_*().
    Always sets None on exit even if the read raises."""
    token = _PENDING_BYPASS_TOKEN.get()
    _PENDING_BYPASS_TOKEN.set(None)
    return token


def audit_flyer_intake_bypassed(
    *,
    chat_id: str,
    bypass_reason: str,
    has_media: bool,
    customer_state: str,
    intake_session_status: str,
    inbound_script: str,
) -> None:
    """Emit the decision-time FlyerIntakeBypassed audit row via the deployed
    ndjson_append chokepoint. Best-effort; failures suppressed to stderr."""
    try:
        _ensure_platform_path()
        _ensure_src_path()
        from safe_io import ndjson_append  # type: ignore
        from schemas import FlyerIntakeBypassed  # type: ignore

        entry = FlyerIntakeBypassed(
            ts=datetime.now(timezone.utc),
            chat_id_hash=_short_hash(chat_id),
            bypass_reason=bypass_reason,  # type: ignore[arg-type]
            has_media=has_media,
            customer_state=customer_state,
            intake_session_status=intake_session_status,
            inbound_script=inbound_script,  # type: ignore[arg-type]
        )
        ndjson_append(LOG_PATH, entry.model_dump_json())
    except Exception as e:
        sys.stderr.write(
            f"cf-router: flyer_intake_bypassed emit failed (non-fatal): "
            f"{type(e).__name__}: {e}\n"
        )


def extract_flyer_request_after_confirm(text: str) -> str:
    """Return a flyer brief trailing a compound onboarding CONFIRM reply."""
    body = flyer_visible_message_text(text)
    match = re.match(
        r"^\s*(?:confirm|ok|yes)\b(?:\s*[\.:,;!\-]\s*|\s+)(.+?)\s*$",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    request = " ".join(match.group(1).split())
    if not request or request.lower().startswith("edit "):
        return ""
    return request


def flyer_project_has_required_fields(project: dict) -> bool:
    fields = project.get("fields") or {}
    notes = f"{fields.get('notes') or ''} {project.get('raw_request') or ''}".lower()
    assets = project.get("assets") or []

    def has(name: str) -> bool:
        return bool(str(fields.get(name) or "").strip())

    has_reference_asset = any(
        isinstance(asset, dict) and asset.get("kind") == "reference_image"
        for asset in assets
    )
    has_template_reference = has_reference_asset or any(
        marker in notes
        for marker in ("uploaded template", "uploaded reference", "reference image")
    )
    if flyer_project_needs_missing_reference(project):
        return False
    has_price_list = (
        "$" in notes
        or any(
            marker in notes
            for marker in (
                "menu item", "menu items", "items", "price", "combo",
                "/piece", "/lb", "tray", "special", "offer", "deal",
            )
        )
    )
    has_product_or_brand_promo = bool(
        re.search(r"\b(?:flyer|flier|poster|banner)\b", notes)
        and re.search(
            r"\b(?:hero image|tagline|badge|badges|certified|brand|branding|"
            r"product|featuring|premium|organic-style|organic style|grocery aesthetic)\b",
            notes,
        )
    )
    has_service_list = bool(re.search(
        r"\b(?:services?|social media marketing|performance marketing|seo|aeo|geo|"
        r"ai marketing|content creation|paid ads|digital marketing|marketing services?)\b",
        notes,
    ))
    has_recurring = any(
        marker in notes
        for marker in (
            "weekend", "saturday", "sunday", "daily", "weekday",
            "weekdays", "every ", "starts from", "start from",
        )
    )
    if has_template_reference:
        return has("event_or_business_name")
    if has_product_or_brand_promo:
        return has("event_or_business_name")
    if has_price_list or has_service_list:
        return has("event_or_business_name") and has("contact_info")
    required = ["event_or_business_name", "event_time", "venue_or_location", "contact_info"]
    if not has_recurring:
        required.insert(1, "event_date")
    return all(has(name) for name in required)


def flyer_project_needs_missing_reference(project: dict) -> bool:
    """Return True when the brief depends on an attached sample that is absent."""
    fields = project.get("fields") or {}
    notes = f"{fields.get('notes') or ''} {project.get('raw_request') or ''}".lower()
    assets = project.get("assets") or []
    has_reference_asset = any(
        isinstance(asset, dict) and asset.get("kind") == "reference_image"
        for asset in assets
    )
    if has_reference_asset:
        return False
    attachment_cue = any(
        marker in notes
        for marker in (
            "attached",
            "attachment",
            "sample flyer",
            "sample flier",
            "existing flyer",
            "existing flier",
            "reference flyer",
            "reference flier",
            "this flyer",
            "this flier",
            "from the flyer",
            "from this flyer",
            "from attached",
        )
    )
    extraction_cue = any(
        marker in notes
        for marker in (
            "extract",
            "use items in",
            "items in this",
            "take items from",
            "from the sample",
            "from sample",
        )
    )
    return attachment_cue and extraction_cue


def flyer_starter_brief_reply(customer: dict) -> str:
    """Return a category starter brief for a Flyer customer."""
    try:
        _ensure_local_src_path()
        from agents.flyer.starter_briefs import starter_brief_message  # type: ignore
    except Exception:
        try:
            _ensure_platform_path()
            from flyer_starter_briefs import starter_brief_message  # type: ignore
        except Exception:
            business_name = str(customer.get("business_name") or "").strip()
            name_line = f"Business: {business_name}\n" if business_name else ""
            return (
                "Flyer Studio\n"
                "------------\n"
                f"{flyer_starter_brief_marker()}.\n"
                f"{name_line}"
                "Edit anything below and send it back.\n\n"
                "Create a professional flyer for my business.\n\n"
                "Main heading:\nSpecial Offer\n\n"
                "Details:\nAdd what I am promoting, products or services, prices, dates, and contact details here.\n\n"
                "Use my saved business name, address, phone, and logo.\n\n"
                'Tip: reply "don\'t show sample prompts" anytime to turn off future examples for this business account.\n\n'
                "Reply with your edited version, or replace it with your own flyer request."
            )
    return starter_brief_message(
        str(customer.get("business_category") or ""),
        business_name=str(customer.get("business_name") or ""),
        include_opt_out_hint=True,
    )


def flyer_starter_brief_marker() -> str:
    try:
        _ensure_local_src_path()
        from agents.flyer.starter_briefs import STARTER_BRIEF_MARKER  # type: ignore
        return str(STARTER_BRIEF_MARKER)
    except Exception:
        try:
            _ensure_platform_path()
            from flyer_starter_briefs import STARTER_BRIEF_MARKER  # type: ignore
            return str(STARTER_BRIEF_MARKER)
        except Exception:
            return "Here is a starter flyer request"


def flyer_customer_not_active_reply(customer: dict) -> str:
    """Customer-facing reply when account is not in {trial, active}.

    PR-ζ.1a 2026-05-26 — customer-copy hotfix:
    - cancelled branch: "is cancelled" → "is no longer active" (avoids the
      forbidden completion verb `cancelled` which the PR-ζ chokepoint lint
      refuses; the actions.py allowlist masks this today but PR-ζ.1b will
      remove that mask).
    - generic fallback: drop the dynamic `{status}` interpolation entirely
      (forward-compat against future schema additions where the status name
      might itself be a forbidden completion verb).
    - payment_pending + suspended branches preserved verbatim.
    """
    status = str(customer.get("status") or "").strip() or "not_active"
    if status == "payment_pending":
        return (
            "Flyer Studio\n"
            "------------\n"
            "Your account is waiting for payment confirmation. I saved your account details, but flyer generation starts after activation."
        )
    if status == "suspended":
        return (
            "Flyer Studio\n"
            "------------\n"
            "This Flyer Studio account is suspended. Contact Support before creating a new flyer."
        )
    if status == "cancelled":
        return (
            "Flyer Studio\n"
            "------------\n"
            "This Flyer Studio account is no longer active. Contact Support or restart setup before creating a new flyer."
        )
    # Generic fallback for unexpected status values (legacy customer dicts,
    # future schema additions). Intentionally omits the status name to defend
    # against future forbidden-verb statuses (e.g. a future "refunded" enum
    # value would otherwise leak `refunded` into customer copy via the
    # f-string interpolation).
    return (
        "Flyer Studio\n"
        "------------\n"
        "This Flyer Studio account is not currently active. Contact Support before creating a new flyer."
    )


def flyer_project_missing_info_reply(project: dict) -> str:
    """Customer-facing prompt for an incomplete Flyer project."""
    if flyer_project_needs_missing_reference(project):
        return (
            "Flyer Studio\n"
            "------------\n"
            "I need the sample/reference flyer before I can create the design.\n\n"
            "Please attach the flyer image/PDF, or type the items, offers, prices, date/time if needed, and contact details."
        )
    return (
        "Flyer Studio\n"
        "------------\n"
        "I need a few more details before creating the design.\n\n"
        "What should this flyer promote? Send item/offer/event details, date/time if needed, location/contact, and any logo/photos."
    )


def is_flyer_enabled() -> bool:
    """Return cfg.flyer.enabled from config.yaml; false on missing config."""
    try:
        import yaml  # type: ignore
        with CONFIG_PATH.open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return bool((cfg.get("flyer") or {}).get("enabled"))
    except Exception:
        return False


def is_flyer_workflow_enabled() -> bool:
    """Return whether Flyer workflow routing should stay wired.

    `flyer.enabled` controls the legacy primary generation path. During the
    Hermes/OpenRouter bare-render rollout we still need the product workflow
    front door (sample ideas, intake, account commands, source gates). If a
    `flyer:` block exists, default workflow routing on unless explicitly
    disabled with `workflow_enabled: false`.
    """
    try:
        import yaml  # type: ignore
        with CONFIG_PATH.open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        flyer_cfg = cfg.get("flyer")
        if not isinstance(flyer_cfg, dict):
            return False
        if "workflow_enabled" in flyer_cfg:
            return bool(flyer_cfg.get("workflow_enabled"))
        return True
    except Exception:
        return False


def _flyer_account_phones(phone: Optional[str], chat_id: str) -> set[str]:
    """All canonical phone identifiers tied to the sender's flyer account.

    Used by every flyer project selector so the picker matches a project's
    `customer_phone` against any number registered on the customer record,
    not just the inbound sender's number.
    """
    account_phones: set[str] = set()
    canonical_sender = _canonical_phone(phone) or phone
    if canonical_sender:
        account_phones.add(canonical_sender)
    customer = find_flyer_customer_by_sender(phone, chat_id)
    if customer:
        for key in ("public_phone", "business_whatsapp_number", "onboarded_by_phone"):
            value = customer.get(key)
            canonical = _canonical_phone(value)
            if canonical:
                account_phones.add(canonical)
        for value in customer.get("authorized_request_numbers") or []:
            canonical = _canonical_phone(value)
            if canonical:
                account_phones.add(canonical)
    return account_phones


def _flyer_direct_account_phones(phone: Optional[str], chat_id: str, customer: Optional[dict] = None) -> set[str]:
    """Phones directly owned by the Flyer account.

    Excludes authorized_request_numbers. Legacy projects that predate
    customer_id/chat_id binding may still be matched by public/business phones,
    but active routing must not attach an orphan project just because a sender is
    listed as an authorized requester on another account.
    """
    direct_phones: set[str] = set()
    canonical_sender = _canonical_phone(phone) or phone
    if canonical_sender:
        direct_phones.add(canonical_sender)
    customer = customer if customer is not None else find_flyer_customer_by_sender(phone, chat_id)
    if customer:
        for key in ("public_phone", "business_whatsapp_number", "onboarded_by_phone"):
            canonical = _canonical_phone(customer.get(key))
            if canonical:
                direct_phones.add(canonical)
    return direct_phones


def _load_flyer_projects() -> list[dict]:
    if not FLYER_PROJECTS_PATH.exists():
        return []
    try:
        with FLYER_PROJECTS_PATH.open(encoding="utf-8") as f:
            store = json.load(f)
    except Exception:
        return []
    projects = store.get("projects", [])
    return projects if isinstance(projects, list) else []


def _flyer_candidate_projects_by_sender(phone: Optional[str], chat_id: str) -> list[dict]:
    """Non-terminal project rows owned by this sender's account.

    `closed_no_send` is intentionally excluded: an operator-closed row must
    not swallow legitimate new flyer requests from the same customer. Use
    `find_latest_flyer_project_for_status_by_sender` for status replies,
    which DOES need to surface closures.

    Shared by the active-routing picker (max updated_at), the quoted-mid
    binder, and the quote-echo guard so account scoping stays in one place —
    none of them may leak projects across customers.
    """
    if not FLYER_PROJECTS_PATH.exists():
        return []
    terminal = {"completed", "closed_no_send"}
    account_phones = _flyer_account_phones(phone, chat_id)
    if not account_phones:
        return []
    customer = find_flyer_customer_by_sender(phone, chat_id)
    customer_id = str((customer or {}).get("customer_id") or "")
    direct_account_phones = _flyer_direct_account_phones(phone, chat_id, customer)
    account_chat_ids = {chat_id} if chat_id else set()
    if customer:
        primary_chat_id = str(customer.get("primary_chat_id") or "")
        if primary_chat_id:
            account_chat_ids.add(primary_chat_id)
    matches = []
    for row in _load_flyer_projects():
        if not isinstance(row, dict) or row.get("status") in terminal:
            continue
        row_customer_id = str(row.get("customer_id") or "")
        row_chat_id = str(row.get("chat_id") or "")
        row_phone = _canonical_phone(row.get("customer_phone")) or str(row.get("customer_phone") or "")
        if row_customer_id:
            if customer_id and row_customer_id == customer_id:
                matches.append(row)
            continue
        if row_chat_id:
            if row_chat_id in account_chat_ids:
                matches.append(row)
            continue
        if customer_id and row_phone in direct_account_phones:
            matches.append(row)
            continue
        if not customer_id and row_phone in account_phones:
            matches.append(row)
    return matches


def find_active_flyer_project_by_sender(phone: Optional[str], chat_id: str) -> Optional[dict]:
    """Look up a non-terminal flyer project by sender phone, for routing
    new-request / revision / approval flow. Newest-updated heuristic; see
    `resolve_flyer_binding_project` for the quoted-mid precision override.
    """
    try:
        matches = _flyer_candidate_projects_by_sender(phone, chat_id)
        if not matches:
            return None
        return max(matches, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""))
    except Exception:
        return None


_FLYER_PROJECT_ID_RE = re.compile(r"\bF\d{4}\b", re.IGNORECASE)


def extract_flyer_project_id_mention(text: str) -> Optional[str]:
    """Return the FXXXX project id mentioned in the message body, if any.

    Customers asking "any update on F0058?" should reach the specific
    project they referenced, not whichever project the latest-updated
    heuristic happens to pick. Returns the upper-cased canonical id.
    """
    if not text:
        return None
    match = _FLYER_PROJECT_ID_RE.search(text)
    return match.group(0).upper() if match else None


def find_flyer_project_by_id_for_sender(
    phone: Optional[str], chat_id: str, project_id: str,
) -> Optional[dict]:
    """Return the project with `project_id` IF it belongs to this sender's
    account (any phone in `_flyer_account_phones`). Returns None otherwise —
    we do NOT leak project state across customers.
    """
    if not project_id or not FLYER_PROJECTS_PATH.exists():
        return None
    try:
        account_phones = _flyer_account_phones(phone, chat_id)
        if not account_phones:
            return None
        target = project_id.upper()
        for row in _load_flyer_projects():
            if (
                isinstance(row, dict)
                and str(row.get("project_id") or "").upper() == target
                and row.get("customer_phone") in account_phones
            ):
                return row
        return None
    except Exception:
        return None


def find_latest_flyer_project_for_status_by_sender(
    phone: Optional[str], chat_id: str,
) -> Optional[dict]:
    """Status-reply selector: includes closed_no_send, delivered, and every
    other non-`completed` status. Picks by max(updated_at).

    Distinct from `find_active_flyer_project_by_sender` (active-routing
    selector) because closed_no_send and delivered need to surface for
    "any update?" replies but MUST stay out of new-request / revision /
    approval routing.
    """
    if not FLYER_PROJECTS_PATH.exists():
        return None
    try:
        account_phones = _flyer_account_phones(phone, chat_id)
        if not account_phones:
            return None
        matches = [
            row for row in _load_flyer_projects()
            if isinstance(row, dict)
            and row.get("customer_phone") in account_phones
            and row.get("status") != "completed"
        ]
        if not matches:
            return None
        return max(matches, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""))
    except Exception:
        return None


def extract_quoted_message_id(event: Any) -> str:
    """Return the quoted (swipe-reply) message id from the bridge event, or "".

    Probe evidence (cf_router_raw_body, 2026-07-05): the bridge delivers
    swipe-replies with a CLEAN body and quote metadata in `event.raw_message`
    (dict): hasQuotedMessage=True, quotedMessageId=<id of quoted message>,
    quotedParticipant=<lid of quoted sender>. Defensive on every axis — dict,
    JSON string, missing, hostile attribute access — and NEVER raises: any
    parsing issue means "" and the caller falls back to newest-updated
    binding (fail-open contract).
    """
    try:
        for obj in (event, getattr(event, "source", None)):
            if obj is None:
                continue
            raw = getattr(obj, "raw_message", None)
            if raw is None and isinstance(obj, dict):
                raw = obj.get("raw_message")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (ValueError, TypeError):
                    continue
            if not isinstance(raw, dict):
                continue
            if not raw.get("hasQuotedMessage"):
                continue
            quoted = raw.get("quotedMessageId")
            if isinstance(quoted, str) and quoted.strip():
                return quoted.strip()[:200]
        return ""
    except Exception:
        return ""


def _flyer_project_outbound_mids(row: dict) -> set[str]:
    """All outbound message ids known for a project row: the preview-batch
    index (media + APPROVE-CTA text) plus per-asset delivery mids (concept
    previews and finals recorded by _record_flyer_concept_preview_delivery /
    send-flyer-package)."""
    mids: set[str] = set()
    for mid in row.get("preview_message_ids") or []:
        if isinstance(mid, str) and mid.strip():
            mids.add(mid.strip())
    for asset in row.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        mid = str(asset.get("outbound_message_id") or "").strip()
        if mid:
            mids.add(mid)
    return mids


def find_flyer_project_by_quoted_mid(
    phone: Optional[str], chat_id: str, quoted_mid: str,
) -> Optional[dict]:
    """Return the sender's non-terminal project whose known outbound mids
    contain `quoted_mid`, or None. Account-scoped via the same candidate set
    as the active picker — never binds across customers."""
    if not quoted_mid:
        return None
    try:
        hits = [
            row for row in _flyer_candidate_projects_by_sender(phone, chat_id)
            if quoted_mid in _flyer_project_outbound_mids(row)
        ]
        if not hits:
            return None
        return max(hits, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""))
    except Exception:
        return None


def _bind_override_strands_approval(
    override_project: dict, fallback_project: dict, text: str,
) -> bool:
    """True when a bind override would silently swallow an approval.

    F0213 live incident 2026-07-06T01:00Z: the customer swipe-replied APPROVE
    on the PREVIOUS project's CTA (F0212, already delivered an hour earlier)
    while the current project (F0213) sat in awaiting_final_approval. The
    quoted-mid override bound the delivered row, the intercept's
    delivered-status early return dropped the approval, and the message fell
    through to the delivery-state guard as a status reply — the approval was
    lost. An approval bound to an already-delivered project is a no-op, so
    when the newest-updated pick can actually act on the approval, keep it.
    """
    if str(override_project.get("status") or "") != "delivered":
        return False
    if not (is_flyer_approval_text(text) or is_flyer_send_now_intent(text)):
        return False
    fallback_status = str(fallback_project.get("status") or "")
    return fallback_status in _FLYER_FINAL_APPROVAL_STATUSES | {"finalizing_assets"}


def resolve_flyer_binding_project(
    active_project: Optional[dict],
    phone: Optional[str],
    chat_id: str,
    event: Any,
    text: str = "",
) -> tuple[Optional[dict], str]:
    """Bind an inbound to a flyer project, preferring the quoted message.

    When the customer swipe-replied on a specific outbound message (concept
    preview media, APPROVE CTA, final delivery), the quoted mid identifies the
    project they mean — more precise than the newest-updated heuristic when
    multiple projects are open. Returns (project, binding_source) where
    binding_source is "quoted_message_id" or "newest_updated". Fail-open: any
    missing/odd quote metadata or lookup failure keeps the newest-updated
    result untouched.

    Exception (F0213 incident): an approval must never bind to an
    already-delivered project while the newest-updated pick is approvable —
    that strands the approval as a status reply. See
    `_bind_override_strands_approval`; such binds fall back with
    binding_source "stale_quote_approve_fallback".
    """
    if active_project is None:
        return None, "newest_updated"
    override: Optional[dict] = None
    source = ""
    hinted_id = pop_flyer_echo_approve_bind_hint(chat_id)
    if hinted_id:
        for candidate in _flyer_candidate_projects_by_sender(phone, chat_id):
            if str(candidate.get("project_id") or "") == hinted_id:
                override, source = candidate, "quote_echo_choice"
                break
    if override is None:
        quoted_mid = extract_quoted_message_id(event)
        if quoted_mid:
            quoted_project = find_flyer_project_by_quoted_mid(phone, chat_id, quoted_mid)
            if quoted_project is not None:
                override, source = quoted_project, "quoted_message_id"
    if override is not None:
        if _bind_override_strands_approval(override, active_project, text):
            return active_project, "stale_quote_approve_fallback"
        return override, source
    return active_project, "newest_updated"


# Quote-echo guard (F0211 class) only considers projects touched inside this
# window. 14 days: weekly-specials customers re-send the same brief text on a
# ~7-day cadence (operator ruling 2026-07-05 — a verbatim re-send is a FEATURE
# of that cadence, never noise to drop), so the window must comfortably cover
# 7-days-apart re-sends; the guard's reply is a one-word NEW/APPROVE
# disambiguation, so firing on a genuine weekly re-send costs one reply, not
# a lost request.
FLYER_QUOTE_ECHO_RECENT_SEC = 14 * 24 * 3600
# Pending NEW/APPROVE disambiguation lives this long (matches the 4h
# proposal/pending-revision TTL convention).
FLYER_QUOTE_ECHO_PENDING_TTL_SEC = 4 * 60 * 60
# Prefix matches (echo + appended reply text) need a long brief to be safe;
# short briefs only match on exact equality.
_FLYER_QUOTE_ECHO_PREFIX_MIN_LEN = 80


# Echo-APPROVE bind hint (PR #558 review M1): the disambiguation described a
# SPECIFIC project; the customer's APPROVE means that one, not whatever
# newest-updated resolves to. Short TTL — it exists only for the immediately
# following approval routing.
FLYER_ECHO_APPROVE_HINT_TTL_SEC = 10 * 60


def set_flyer_echo_approve_bind_hint(chat_id: str, project_id: str) -> None:
    _ensure_platform_path()
    from safe_io import atomic_write_json, flock  # type: ignore

    with flock(FLYER_QUOTE_ECHO_PENDING_PATH):
        doc = _load_flyer_quote_echo_pending_doc()
        hints = doc.setdefault("approve_hints", {})
        hints[chat_id] = {"project_id": project_id,
                          "ts": datetime.now(timezone.utc).isoformat()}
        atomic_write_json(FLYER_QUOTE_ECHO_PENDING_PATH, doc)


def pop_flyer_echo_approve_bind_hint(chat_id: str) -> str:
    """Return the hinted project_id ("" if none/expired) and clear it."""
    try:
        _ensure_platform_path()
        from safe_io import atomic_write_json, flock  # type: ignore

        with flock(FLYER_QUOTE_ECHO_PENDING_PATH):
            doc = _load_flyer_quote_echo_pending_doc()
            hints = doc.get("approve_hints", {})
            row = hints.pop(chat_id, None)
            atomic_write_json(FLYER_QUOTE_ECHO_PENDING_PATH, doc)
        if not row:
            return ""
        ts = datetime.fromisoformat(str(row.get("ts")))
        if (datetime.now(timezone.utc) - ts).total_seconds() > FLYER_ECHO_APPROVE_HINT_TTL_SEC:
            return ""
        return str(row.get("project_id") or "")
    except Exception:  # noqa: BLE001 - hint is best-effort
        return ""


def has_awaiting_source_vs_new_choice(chat_id: str) -> bool:
    """M2 (PR #558 review): quote-echo choice yields to a live SOURCE/NEW row."""
    try:
        return peek_flyer_source_vs_new_pending(chat_id=chat_id, sender_phone="") is not None
    except Exception:  # noqa: BLE001
        return False


def find_flyer_quote_echo_project(
    phone: Optional[str], chat_id: str, body: str,
) -> Optional[dict]:
    """Detect a flattened quote-echo body (F0211 class) and return the echoed
    project, or None.

    One legacy bridge shape flattens the QUOTED message text into the inbound
    body on swipe-reply. When the quoted text is a project brief, the echo
    looks like a fresh brief and creates a duplicate project. Conservative
    match ONLY: whitespace/case-normalized body exactly equals a recent
    project's raw_request, or starts with it when the brief is long
    (> _FLYER_QUOTE_ECHO_PREFIX_MIN_LEN chars). No fuzzy matching.
    """
    normalized_body = " ".join((body or "").split()).casefold()
    if not normalized_body:
        return None
    try:
        now = datetime.now(timezone.utc)
        best: Optional[dict] = None
        for row in _flyer_candidate_projects_by_sender(phone, chat_id):
            raw_request = " ".join(str(row.get("raw_request") or "").split()).casefold()
            if not raw_request:
                continue
            if normalized_body != raw_request and not (
                len(raw_request) > _FLYER_QUOTE_ECHO_PREFIX_MIN_LEN
                and normalized_body.startswith(raw_request)
            ):
                continue
            ts_str = str(row.get("updated_at") or row.get("created_at") or "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if (now - ts).total_seconds() > FLYER_QUOTE_ECHO_RECENT_SEC:
                    continue
            except (ValueError, TypeError):
                continue  # unparseable timestamp — cannot prove recency, skip
            if best is None or str(row.get("updated_at") or "") > str(best.get("updated_at") or ""):
                best = row
        return best
    except Exception:
        return None


def classify_flyer_quote_echo_choice(text: str) -> Optional[str]:
    """Classify a reply to the quote-echo NEW/APPROVE disambiguation.

    "new" — customer wants a fresh flyer from the same brief text (weekly-
    specials cadence). "approve" — customer wants the existing flyer; the
    caller clears the pending state and lets normal approval routing handle
    it. None — anything else (pending state stays until TTL)."""
    body = " ".join(flyer_visible_message_text(text).split()).lower().strip(" .!,:;")
    if not body:
        return None
    if re.fullmatch(r"(?:new|new one|new flyer|fresh|fresh one)", body):
        return "new"
    if is_flyer_approval_text(text):
        return "approve"
    return None


def _load_flyer_quote_echo_pending_doc() -> dict:
    _ensure_platform_path()
    from safe_io import safe_load_json  # type: ignore

    doc, _status = safe_load_json(
        FLYER_QUOTE_ECHO_PENDING_PATH,
        default={"version": 1, "pending": {}},
    )
    if not isinstance(doc, dict):
        doc = {"version": 1, "pending": {}}
    pending = doc.get("pending")
    if not isinstance(pending, dict):
        doc["pending"] = {}
    doc["version"] = 1
    return doc


def save_flyer_quote_echo_pending(
    *,
    chat_id: str,
    original_text: str,
    message_id: str,
    project_id: str,
) -> None:
    """Remember the echoed brief so a one-word NEW reply can create the fresh
    project from it. Mirrors the revenue-route clarification state pattern."""
    _ensure_platform_path()
    from safe_io import atomic_write_json, flock  # type: ignore

    with flock(FLYER_QUOTE_ECHO_PENDING_PATH):
        doc = _load_flyer_quote_echo_pending_doc()
        doc["pending"][chat_id] = {
            "chat_id": chat_id,
            "original_text": original_text[:4000],
            "message_id": message_id,
            "project_id": project_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_json(FLYER_QUOTE_ECHO_PENDING_PATH, doc)


def _flyer_quote_echo_pending_fresh(row: Optional[dict]) -> Optional[dict]:
    if not isinstance(row, dict):
        return None
    try:
        created = datetime.fromisoformat(str(row.get("created_at") or ""))
        age = (datetime.now(timezone.utc) - created).total_seconds()
    except (ValueError, TypeError):
        return None
    return row if age <= FLYER_QUOTE_ECHO_PENDING_TTL_SEC else None


def get_flyer_quote_echo_pending(chat_id: str) -> Optional[dict]:
    _ensure_platform_path()
    from safe_io import flock  # type: ignore

    with flock(FLYER_QUOTE_ECHO_PENDING_PATH):
        doc = _load_flyer_quote_echo_pending_doc()
        return _flyer_quote_echo_pending_fresh(doc.get("pending", {}).get(chat_id))


def pop_flyer_quote_echo_pending(chat_id: str) -> Optional[dict]:
    _ensure_platform_path()
    from safe_io import atomic_write_json, flock  # type: ignore

    with flock(FLYER_QUOTE_ECHO_PENDING_PATH):
        doc = _load_flyer_quote_echo_pending_doc()
        row = doc.get("pending", {}).pop(chat_id, None)
        atomic_write_json(FLYER_QUOTE_ECHO_PENDING_PATH, doc)
        return _flyer_quote_echo_pending_fresh(row)


def has_non_delivered_flyer_project_by_sender(phone: Optional[str], chat_id: str) -> bool:
    project = find_active_flyer_project_by_sender(phone, chat_id)
    return bool(project and project.get("status") != "delivered")


# Per-status thresholds in hours beyond which an active project is "stale" — a
# new inbound that is NOT a clear status check or revision will bypass the
# active-project attach path. Empirical baseline: F0036/F0043/F0045 on prod
# sat at manual_edit_required for ~19h before any operator action; we don't
# want a 19h-old project swallowing today's distinct new flyer request.
_FLYER_STALE_HOURS: dict[str, float] = {
    "intake_started": 2.0,
    "collecting_required_info": 2.0,
    "awaiting_assets": 2.0,
    "generating_concepts": 2.0,
    "finalizing_assets": 2.0,
    "awaiting_concept_selection": 6.0,
    "awaiting_final_approval": 6.0,
    "revising_design": 6.0,
    "manual_edit_required": 24.0,
    "delivered": 24.0,
}


def is_stale_for_new_request(
    project: dict,
    *,
    now: Optional[datetime] = None,
    overrides: Optional[dict[str, float]] = None,
) -> bool:
    """Return True when an active project is old enough that a new inbound
    must NOT silently attach to it.

    Status check + revision-intent inbound continue to attach (the caller is
    expected to re-check those gates); anything else should bypass this
    project so the new-project path takes over.
    """
    status = str(project.get("status") or "")
    thresholds = {**_FLYER_STALE_HOURS, **(overrides or {})}
    threshold_hours = thresholds.get(status)
    if threshold_hours is None:
        return False
    raw = project.get("updated_at") or project.get("created_at")
    if not raw:
        return False
    try:
        if isinstance(raw, datetime):
            updated_at = raw
        else:
            # Pydantic emits ISO8601 with trailing 'Z' or '+00:00'; both parse.
            updated_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    age_hours = (now - updated_at).total_seconds() / 3600.0
    return age_hours >= threshold_hours


def is_flyer_revision_intent(text: str) -> bool:
    body = flyer_visible_message_text(text).lower()
    if re.search(
        r"\b(?:flyer|flier|poster|design|image|output|quality|this|it)\b"
        r"[\s\S]{0,120}\b(?:bad|poor|terrible|awful|miserable|unacceptable|not\s+acceptable|"
        r"not\s+good|can(?:not|'?t)\s+accept|cant\s+accept)\b"
        r"|"
        r"\b(?:bad|poor|terrible|awful|miserable|unacceptable|not\s+acceptable|"
        r"not\s+good|can(?:not|'?t)\s+accept|cant\s+accept)\b"
        r"[\s\S]{0,120}\b(?:flyer|flier|poster|design|image|output|quality|this|it)\b",
        body,
        flags=re.IGNORECASE,
    ):
        return True
    # "update" is intentionally EXCLUDED (origin/main golden-pinned): a terse "any update?"
    # is a STATUS check-in, not a revision — a separate status detector handles it. Keeping
    # "update" here mis-routes status check-ins as revisions (golden live_status_any_update).
    return bool(re.search(
        r"\b(apply|edit|modify|change|replace|swap|remove|exclude|add|fix|correct|problem|wrong|still|instead|not\s+in|looks?\s+great|design)\b",
        body,
        flags=re.IGNORECASE,
    ))


# --- Revision-apply routing recognizers (slice 2c) ----------------------------
# These run in the bare-flyer routing chain (hooks.py) so a follow-up resolves correctly:
#   (a) detect_uniform_price_toggle  -> --revision-apply  (the one supported 2c edit)
#   (b) is_strong_new_flyer_request  -> fresh render       (genuine new request wins)
#   (c) is_flyer_revision_intent     -> --revision         (broad feedback => "resend full details")
# Both are deterministic + bounded (no LLM). The toggle is also recent-session-gated at the call site,
# so it never matches a brand-new customer's fresh brief.

_STRONG_NEW_FLYER_RE = re.compile(
    r"\b(make|create|build|generate|start|design|need|want)\b[^.\n]{0,30}?\b(a|an|new|another)\b"
    r"[^.\n]{0,20}?\b(flyer|flier|poster|banner)\b",
    re.IGNORECASE,
)
# A prior-object reference means this is an edit of an existing flyer, NOT a new request.
_PRIOR_FLYER_OBJECT_RE = re.compile(
    r"\b(this|that|the|my|our|current|existing)\b[^.\n]{0,20}?\b(flyer|flier|poster|banner|design|image|one)\b",
    re.IGNORECASE,
)


def is_strong_new_flyer_request(text: str) -> bool:
    """True only for an unambiguous NEW-flyer request (e.g. 'design a flyer for Diwali', 'make a new
    poster'). Returns False when the message references a prior object ('change this flyer's design',
    'design the current flyer') so genuine revisions are not stolen into a fresh render."""
    body = flyer_visible_message_text(text)
    if _PRIOR_FLYER_OBJECT_RE.search(body):
        return False
    return bool(_STRONG_NEW_FLYER_RE.search(body))


def is_flyer_edit_of_existing(text: str) -> bool:
    """A revision that explicitly references a prior flyer object ("remove the prices from this
    flyer", "change my poster"). Used to stop the bare new-flyer intercept from stealing a recent
    customer's edit-of-an-existing-flyer into a fresh render — the broad resend arm (after the F7
    catering block) handles it instead, so catering is never hijacked."""
    body = flyer_visible_message_text(text)
    return bool(_PRIOR_FLYER_OBJECT_RE.search(body)) and is_flyer_revision_intent(text)


def detect_uniform_price_toggle(text: str) -> Optional[str]:
    """Bounded recognizer for the one supported 2c edit: 'every item is $X — put it in a header
    instead of per-item prices.' Returns the single price string (e.g. '$9.99') or None. Requires a
    header/common-price cue AND a per-item-price reference AND exactly one distinct price. Recent-
    session-gated at the call site, so a fresh 'make a flyer, all items $9.99' (no per-item-price
    reference) returns None and a new customer never reaches it."""
    body = flyer_visible_message_text(text).lower()
    prices = {re.sub(r"\s+", "", p) for p in re.findall(r"\$\s?\d+(?:\.\d{1,2})?", text or "")}
    if len(prices) != 1:
        return None
    header_cue = (
        re.search(r"\b(header|common|uniform|single|one|same)\b[^.\n]{0,25}?\bprices?\b", body)
        or re.search(r"\bprices?\b[^.\n]{0,25}?\b(header|banner|at\s+the\s+top|on\s+top|top)\b", body)
        or re.search(r"\b(any|every|all)\s+items?\b[^.\n]{0,15}?\$\s?\d", body)
        or re.search(r"\bheader\b[^.\n]{0,40}?\$\s?\d", body)
    )
    per_item_ref = (
        re.search(r"\b(per[-\s]?item|each\s+item|against\s+each|on\s+each|individual)\b[^.\n]{0,20}?\bprices?\b", body)
        or re.search(r"\bprices?\b[^.\n]{0,20}?\b(per[-\s]?item|each\s+item|against\s+each|on\s+each|individual)\b", body)
        or re.search(r"\bper[-\s]?item\s+prices?\b", body)
        or re.search(r"\bprice\b[^.\n]{0,20}?\beach\b", body)
    )
    if header_cue and per_item_ref:
        return next(iter(prices))
    return None


def detect_per_item_price_update(text: str) -> Optional[str]:
    """Bounded recognizer for recent-flyer edits that update item-card prices.

    This is intentionally recent-session-gated at the hook call site. It catches the
    customer patterns seen live:
      - "replace PENDING with item price ... every item is priced at $8.99"
      - "update price of each item in the generated flyer. Samosa $6.99, Tea $2.99"
    It does not classify new "all items $X" briefs by itself.
    """
    body = flyer_visible_message_text(text).lower()
    prices = re.findall(r"\$\s?\d+(?:\.\d{1,2})?", text or "")
    if not prices:
        return None
    if detect_uniform_price_toggle(text):
        return None
    placeholder_ref = re.search(r"\b(?:pending|tbd|placeholder|\[\s*price\s*\]|price\s+missing)\b", body)
    per_item_ref = (
        re.search(r"\b(per[-\s]?item|each\s+item|every\s+item|all\s+items?|item\s+price|item-card|card)\b", body)
        or re.search(r"\bprices?\b[^.\n]{0,35}?\b(each|every|all|item|items)\b", body)
    )
    update_ref = re.search(r"\b(update|edit|modify|change|replace|set|fix|correct)\b", body)
    if placeholder_ref and per_item_ref and update_ref:
        return "per_item_prices"
    if len({re.sub(r"\s+", "", p) for p in prices}) == 1 and per_item_ref and update_ref:
        return "per_item_prices"
    # Two or more explicit item-price pairs in a recent flyer edit are actionable.
    pairs = re.findall(
        r"[A-Za-z][A-Za-z0-9 '&/-]{1,60}?\s*(?:-|:)?\s*\$\s?\d+(?:\.\d{1,2})?",
        text or "",
        flags=re.IGNORECASE,
    )
    if len(pairs) >= 2 and (per_item_ref or update_ref or _PRIOR_FLYER_OBJECT_RE.search(flyer_visible_message_text(text))):
        return "per_item_prices"
    return None


def detect_bare_price_revision_apply(text: str) -> Optional[str]:
    """Return the bounded bare revision-apply mode for a recent flyer follow-up."""
    if detect_uniform_price_toggle(text):
        return "uniform_header"
    return detect_per_item_price_update(text)


def is_flyer_project_status_request(text: str) -> bool:
    """Return True for customer check-ins, not flyer edit instructions."""
    body = " ".join(flyer_visible_message_text(text).lower().split())
    if not body:
        return False
    if re.search(r"\bwhere(?:'s|\s+is)\s+(?:(?:the|my)\s+)?(?:updated?\s+)?(?:flyer|flier|design|preview)\b", body):
        return True
    edit_starter = re.search(
        r"\b(update|change|edit|modify|replace|remove|add|swap|fix|correct)\s+"
        r"(this|the|my|that|current|attached)?\s*"
        r"(flyer|design|poster|text|logo|item|price|date|time|name|phone|address)\b",
        body,
    )
    if edit_starter:
        return False
    if re.fullmatch(
        r"(status|any update|any updates|update|updates|eta(?:\s+(?:please|pls))?|ready(?:\s+yet)?|done|finished)\??",
        body,
    ):
        return True
    if re.fullmatch(r"f\d{4,}\s+status(?:\s+(?:please|pls))?\??", body):
        return True
    if re.fullmatch(r"status\s+(?:for|of|on)\s+project\s*:?\s*f\d{4,}(?:\s+(?:please|pls))?\??", body):
        return True
    if re.fullmatch(r"where\s+(?:my|the)\s+(?:flyer|flier|design|preview)\s+at\??", body):
        return True
    if re.fullmatch(r"eta\s+on\s+(?:my|the)\s+(?:flyer|flier|design|preview)(?:\s+(?:please|pls))?\??", body):
        return True
    if re.fullmatch(r"where\s+(?:is|are|s)\s+(?:the\s+)?updates?\??", body):
        return True
    return bool(re.search(
        r"\b("
        r"any\s+updates?|"
        r"(?:can|could)\s+(?:i|we)\s+get\s+an?\s+update|"
        r"(?:give|share)\s+(?:me|us)\s+an?\s+update\s+on\s+(?:the\s+)?(?:flyer|flier|design|preview)|"
        r"update\s+on\s+f\d{4,}|"
        r"update\s+on\s+project\s+f\d{4,}|"
        r"where(?:'s|\s+is)\s+(?:the\s+)?update\s+for\s+project\s+f\d{4,}|"
        r"update\s+on\s+(?:this|the|my)\s+(?:flyer|flier|design|preview)(?:\s+(?:please|pls))?|"
        r"status\s+update\s+for\s+project\s+f\d{4,}|"
        r"status\s+update\s+on\s+(?:this|the|my)\s+(?:flyer|flier|design|preview)|"
        r"status\s+for\s+f\d{4,}|"
        r"(?:need\s+)?status\s+of\s+f\d{4,}|"
        r"status\s+about\s+f\d{4,}|"
        r"status\s+(?:for|of)\s+project\s+f\d{4,}(?:\s+(?:please|pls))?|"
        r"(?:share|send|give)\s+status\s+of\s+(?:(?:this|the|my)\s+)?(?:flyer|flier|design|preview)|"
        r"status\s+of\s+(?:(?:this|the|my)\s+)?(?:flyer|flier|design|preview)|"
        r"queue\s+status\s+for\s+f\d{4,}|"
        r"(?:share|send|give)\s+progress\s+on\s+f\d{4,}|"
        r"where(?:'s|\s+is)\s+update\s+for\s+f\d{4,}|"
        r"status\s+on\s+(?:this|the)\s+(?:flyer|flier|design|preview)|"
        r"status\s+on\s+my\s+(?:flyer|flier|design|preview)(?:\s+(?:please|pls))?|"
        r"any\s+news(?:\s+on\s+(?:the\s+)?(?:flyer|flier|design|preview))?|"
        r"(what'?s|whats|what\s+is)\s+the\s+latest\s+update|"
        r"(?:did|have)\s+you\s+(?:finish|finished)\s+(?:the\s+)?(?:flyer|flier|design|preview)|"
        r"did\s+you\s+complete\s+(?:it|the\s+flyer|my\s+flyer)|"
        r"(what'?s|whats|what\s+is)\s+the\s+update\s+on\s+(?:the\s+)?(?:flyer|flier|design|preview)|"
        r"(what'?s|whats|what\s+is)\s+the\s+status|"
        r"what(?:'?s|s)?\s+happening\s+with\s+(?:my|the)\s+(?:flyer|flier|design|preview)|"
        r"what\s+about\s+(?:my|the)\s+(?:flyer|flier|design|preview)|"
        r"where(?:'s|\s+is)\s+(?:(?:the|my)\s+)?(?:updated?\s+)?(?:flyer|flier|design|preview)|"
        r"status\s+(please|pls|update)|"
        r"is\s+the\s+update\s+ready|"
        r"is\s+(it|the\s+flyer|my\s+flyer)\s+(ready(?:\s+yet)?|done|finished)|"
        r"when\s+(will|can)\s+(it|the\s+flyer|my\s+flyer)\s+be\s+(ready|done|finished)|"
        r"how\s+long|"
        r"still\s+waiting|"
        r"check\s+back|"
        r"progress"
        r")\b",
        body,
    ))


def flyer_manual_edit_status_reply(project: dict) -> str:
    reply = flyer_project_status_reply(project)
    generic_fallback = (
        "I have your flyer request open and am checking the latest status."
    )
    if generic_fallback not in reply:
        return reply
    manual = project.get("manual_review") if isinstance(project.get("manual_review"), dict) else {}
    reason_code = str(manual.get("reason_code") or "unclassified").strip().lower() or "unclassified"
    reason_text = str(manual.get("reason") or "")
    detail_text = str(manual.get("detail") or "")
    try:
        _ensure_platform_path()
        from flyer_workflow import MANUAL_REVIEW_REASON_LINES  # type: ignore
        from flyer_manual_queue import canonical_manual_reason_code  # type: ignore
    except Exception:
        try:
            _ensure_local_src_path()
            from agents.flyer.workflow import MANUAL_REVIEW_REASON_LINES  # type: ignore
            from agents.flyer.manual_queue import canonical_manual_reason_code  # type: ignore
        except Exception:
            def canonical_manual_reason_code(  # type: ignore[no-redef]
                raw_reason_code: str,
                *,
                reason: str = "",
                detail: str = "",
            ) -> str:
                code = (raw_reason_code or "").strip().lower() or "unclassified"
                if code != "unclassified":
                    return code
                lowered = f"{reason} {detail}".lower()
                if "source_edit_provider_unavailable" in lowered:
                    return "source_edit_provider_unavailable"
                if "visual_qa_failed" in lowered:
                    return "visual_qa_failed"
                if "reference_unsupported" in lowered:
                    return "reference_unsupported"
                if "reference_provider_unavailable" in lowered:
                    return "reference_provider_unavailable"
                return "unclassified"
            MANUAL_REVIEW_REASON_LINES = {
                "unclassified": (
                    "This project is queued for designer review. "
                    "I'll follow up here when it's ready."
                ),
                "source_edit_provider_unavailable": (
                    "Your edit is queued for a designer to apply by hand. "
                    "I have the requested changes and the saved account details "
                    "\u2014 no extra information needed from you."
                ),
                "visual_qa_failed": (
                    "The generated flyer didn't pass our quality checks. "
                    "It's queued for designer review and I'll send the corrected version here when it's ready."
                ),
                "reference_unsupported": (
                    "The uploaded file format is not supported for exact edit. "
                    "Please re-upload as JPG or PNG and we'll continue."
                ),
            }
    canonical_reason = canonical_manual_reason_code(
        reason_code,
        reason=reason_text,
        detail=detail_text,
    )
    line = MANUAL_REVIEW_REASON_LINES.get(
        canonical_reason,
        MANUAL_REVIEW_REASON_LINES["unclassified"],
    )
    return f"Flyer Studio\n------------\n{line}"


def normalize_manual_reason_code(reason_code: Any) -> str:
    return str(reason_code or "").strip().lower()


def is_source_edit_provider_unavailable_reason(reason_code: Any) -> bool:
    return normalize_manual_reason_code(reason_code) == "source_edit_provider_unavailable"


def flyer_project_status_reply(project: dict) -> str:
    try:
        _ensure_platform_path()
        from schemas import FlyerProject  # type: ignore
        from flyer_workflow import build_project_status_reply  # type: ignore
    except Exception:
        try:
            _ensure_local_src_path()
            from schemas import FlyerProject  # type: ignore
            from agents.flyer.workflow import build_project_status_reply  # type: ignore
        except Exception:
            return "Flyer Studio\n------------\nI have your flyer request open and am checking the latest status."
    try:
        return build_project_status_reply(FlyerProject.model_validate(project))
    except Exception:
        return "Flyer Studio\n------------\nI have your flyer request open and am checking the latest status."


def _canonical_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone.split("@", 1)[0])
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if 10 <= len(digits) <= 15:
        return "+" + digits
    return None


def _read_customer_state() -> dict:
    if not FLYER_CUSTOMERS_PATH.exists():
        return {}
    return json.loads(FLYER_CUSTOMERS_PATH.read_text(encoding="utf-8"))


def _starter_prompt_metadata(store: dict, customer_id: str) -> dict:
    preferences = store.get("starter_prompt_preferences") or {}
    sent_counts = store.get("starter_prompt_sent_counts") or {}
    return {
        "_starter_prompt_mode": str(preferences.get(customer_id) or "auto"),
        "_starter_prompt_sent_count": int(sent_counts.get(customer_id, 0) or 0),
    }


def _with_starter_prompt_metadata(customer: dict, store: dict) -> dict:
    customer_id = str(customer.get("customer_id") or "")
    enriched = dict(customer)
    enriched.update(_starter_prompt_metadata(store, customer_id))
    return enriched


def find_flyer_customer_by_sender(phone: Optional[str], chat_id: str) -> Optional[dict]:
    """Return registered Flyer customer for this sender, if any."""
    canonical = _canonical_phone(phone)
    if not canonical and chat_id.endswith("@s.whatsapp.net"):
        canonical = _canonical_phone(chat_id.split("@", 1)[0])
    if not FLYER_CUSTOMERS_PATH.exists():
        return None
    try:
        store = _read_customer_state()
        if not canonical and chat_id:
            matches = [
                customer for customer in store.get("customers", [])
                if isinstance(customer, dict) and customer.get("primary_chat_id") == chat_id
            ]
            return _with_starter_prompt_metadata(matches[0], store) if len(matches) == 1 else None
        if not canonical:
            return None
        matches = []
        for customer in store.get("customers", []):
            numbers = set(customer.get("authorized_request_numbers") or [])
            for key in ("business_whatsapp_number", "onboarded_by_phone", "public_phone"):
                value = customer.get(key)
                if value:
                    numbers.add(value)
            if canonical in numbers:
                matches.append(customer)
        return _with_starter_prompt_metadata(matches[0], store) if len(matches) == 1 else None
    except Exception:
        return None


def flyer_starter_prompts_enabled(customer: dict) -> bool:
    return str(customer.get("_starter_prompt_mode") or "auto") != "off"


def flyer_starter_prompt_already_sent(customer: dict) -> bool:
    try:
        return int(customer.get("_starter_prompt_sent_count") or 0) > 0
    except (TypeError, ValueError):
        return False


def claim_flyer_starter_prompt_send(customer_id: str) -> bool:
    if not customer_id:
        return False
    ok, _detail, doc = _trigger_flyer_account_state(
        "--claim-starter-prompt",
        customer_id,
    )
    return bool(ok and doc and doc.get("quota_allowed"))


def release_flyer_starter_prompt_claim(customer_id: str) -> None:
    if not customer_id:
        return
    _trigger_flyer_account_state("--release-starter-prompt", customer_id)


def _trigger_flyer_account_state(flag: str, customer_id: str) -> tuple[bool, str, Optional[dict]]:
    try:
        result = subprocess.run(
            [str(PYTHON_BIN), str(MANAGE_FLYER_ACCOUNT_BIN), flag, customer_id],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}", None
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"exit={result.returncode} {detail[:500]}", None
    try:
        return True, detail[:500], json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"account_state_json_parse_failed: {detail[:500]}", None


def flyer_vague_request_clarification_reply(customer: dict) -> str:
    name = str(customer.get("business_name") or "your business").strip() or "your business"
    return (
        "Flyer Studio\n"
        "------------\n"
        f"I can help create a flyer for {name}. What should this flyer promote? "
        "Please send the offer, event, product, or service details you want on it."
    )


def find_flyer_onboarding_session_by_sender(phone: Optional[str], chat_id: str) -> Optional[dict]:
    """Return in-progress Flyer onboarding session for this sender, if any."""
    canonical = _canonical_phone(phone)
    if not FLYER_CUSTOMERS_PATH.exists():
        return None
    try:
        store = json.loads(FLYER_CUSTOMERS_PATH.read_text(encoding="utf-8"))
        sessions = store.get("onboarding_sessions") or []
        for session in sessions:
            if not isinstance(session, dict):
                continue
            status = str(session.get("status") or "")
            if status in {"active", "trial"}:
                continue
            sender_phone = _canonical_phone(session.get("sender_phone"))
            if canonical and sender_phone == canonical:
                return session
            if session.get("sender_phone") is None and session.get("chat_id") == chat_id:
                return session
        return None
    except Exception:
        return None


def find_flyer_intake_session_by_sender(phone: Optional[str], chat_id: str) -> Optional[dict]:
    """Return in-progress Flyer intake session for this sender, if any."""
    canonical = _canonical_phone(phone)
    if not FLYER_CUSTOMERS_PATH.exists():
        return None
    try:
        store = json.loads(FLYER_CUSTOMERS_PATH.read_text(encoding="utf-8"))
        sessions = store.get("intake_sessions") or []
        for session in sessions:
            if not isinstance(session, dict):
                continue
            sender_phone = _canonical_phone(session.get("sender_phone"))
            if canonical and sender_phone == canonical:
                return session
            if session.get("sender_phone") is None and session.get("chat_id") == chat_id:
                return session
        return None
    except Exception:
        return None


def discard_flyer_intake_session_by_sender(phone: Optional[str], chat_id: str) -> bool:
    """Remove an in-progress Flyer intake session after successful handoff."""
    canonical = _canonical_phone(phone)
    if not FLYER_CUSTOMERS_PATH.exists():
        return False
    try:
        _ensure_platform_path()
        from safe_io import FileLock, atomic_write_text  # type: ignore

        with FileLock(Path(str(FLYER_CUSTOMERS_PATH) + ".lock")):
            store = json.loads(FLYER_CUSTOMERS_PATH.read_text(encoding="utf-8"))
            sessions = store.get("intake_sessions") or []
            kept = []
            removed = False
            for session in sessions:
                if not isinstance(session, dict):
                    kept.append(session)
                    continue
                sender_phone = _canonical_phone(session.get("sender_phone"))
                matches_phone = bool(canonical and sender_phone == canonical)
                matches_chat = bool(session.get("sender_phone") is None and session.get("chat_id") == chat_id)
                if matches_phone or matches_chat:
                    removed = True
                    continue
                kept.append(session)
            if not removed:
                return False
            store["intake_sessions"] = kept
            atomic_write_text(FLYER_CUSTOMERS_PATH, json.dumps(store, indent=2, ensure_ascii=False))
            return True
    except Exception:
        return False


_US_STATE_WORDS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "illinois": "IL", "indiana": "IN",
    "maryland": "MD", "michigan": "MI", "new jersey": "NJ", "new york": "NY",
    "north carolina": "NC", "ohio": "OH", "pennsylvania": "PA", "south carolina": "SC",
    "texas": "TX", "virginia": "VA", "washington": "WA",
}


_BUSINESS_SCOPE_TERMS = {
    "academy", "bakery", "barber", "bazaar", "bazar", "cafe", "clinic",
    "company", "dental", "grocery", "hotel", "inc", "kitchen",
    "llc", "market", "mart", "realty", "restaurant", "salon", "school",
    "spa", "store", "studio", "supermarket", "temple",
}


def _normalize_business_scope(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (value or "").lower())


def _strip_campaign_scope_suffix(label: str) -> str:
    stripped = re.sub(
        r"\b(?:store[-\s]*wide|site[-\s]*wide|all\s+items?|everything|weekly|weekend|holiday|diwali|festival)?\s*"
        r"(?:sale|sales|specials?|promotion|promo|offer|offers|deal|deals)\b.*$",
        "",
        label or "",
        flags=re.IGNORECASE,
    ).strip(" .,:;-'\"")
    return stripped or label


def _looks_like_campaign_title_scope(label: str) -> bool:
    tokens = _normalize_business_scope(label)
    joined = " ".join(tokens)
    if not tokens:
        return False
    campaign_patterns = (
        r"\brestaurant week\b",
        r"\bcafe style\b",
        r"\bbiryani bazaar\b",
        r"\bkitchen essentials\b",
        r"\bdosa\b",
        r"\bspecial\b",
        r"\bmenu\b",
        r"\bcombo\b",
        r"\bdiwali\b",
        r"\bholiday\b",
        r"\bfestival\b",
        r"\bweekend\b",
    )
    if any(re.search(pattern, joined) for pattern in campaign_patterns):
        return True
    return False


def _extract_requested_business_scope(raw_request: str) -> str:
    text = " ".join(flyer_visible_message_text(raw_request).split())
    if not text:
        return ""
    candidates: list[str] = []
    patterns = [
        r"\b(?:create|make|generate|design|build|need)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:flyer|flier|poster|banner|creative|graphic)\s+for\s+(.+?)(?=\s*(?:[.!?]|,?\s+\b(?:include|with|promoting|offering|featuring|advertising|announcing|about)\b|$))",
        r"\b(?:flyer|flier|poster|banner|creative|graphic)\s+for\s+(.+?)(?=\s*(?:[.!?]|,?\s+\b(?:include|with|promoting|offering|featuring|advertising|announcing|about)\b|$))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidates.append(match.group(1) or "")
    for match in re.finditer(r"\bfor\s+", text, flags=re.IGNORECASE):
        label = text[match.end():]
        label = re.split(
            r"\s*(?:[.!?]|,?\s+\b(?:include|with|promoting|offering|featuring|advertising|announcing|about)\b)",
            label,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        # Requests like "sales for Memorial Day on groceries for Triveni supermarket"
        # contain an offer-level "for" before the account-scope "for".
        nested_for = re.split(r"\bfor\s+", label, flags=re.IGNORECASE)
        candidates.append(nested_for[-1] if nested_for else label)
    for candidate in reversed(candidates):
        label = re.sub(r"[*_`]+", "", candidate or "")
        label = re.sub(r"^(?:customer|business|client)\s+", "", label, flags=re.IGNORECASE)
        label = label.strip(" .,:;-'\"")
        stripped = _strip_campaign_scope_suffix(label)
        if stripped != label:
            if _looks_like_campaign_title_scope(label):
                continue
            stripped_tokens = _normalize_business_scope(stripped)
            if len(stripped_tokens) == 1 and stripped_tokens[0] not in {"diwali", "holiday", "festival", "weekend"}:
                return stripped
            label = stripped
        elif _looks_like_campaign_title_scope(label):
            continue
        if _looks_like_business_scope(label):
            return label
    return ""


def flyer_requested_business_scope(raw_request: str) -> str:
    """Return the explicit business name requested by a flyer brief, if any."""
    return _extract_requested_business_scope(raw_request)


def _looks_like_business_scope(label: str) -> bool:
    tokens = _normalize_business_scope(label)
    if len(tokens) < 2:
        return False
    generic_prefixes = {"my", "our", "the", "this", "that", "your"}
    generic_nouns = {"business", "company", "restaurant", "store", "salon", "studio", "shop"}
    if tokens[0] in generic_prefixes and set(tokens[1:]).issubset(generic_nouns):
        return False
    return any(token in _BUSINESS_SCOPE_TERMS for token in tokens)


def _business_scope_matches(requested: str, account_name: str) -> bool:
    requested_tokens = set(_normalize_business_scope(requested))
    account_tokens = set(_normalize_business_scope(account_name))
    if not requested_tokens or not account_tokens:
        return False
    requested_joined = " ".join(_normalize_business_scope(requested))
    account_joined = " ".join(_normalize_business_scope(account_name))
    if requested_joined in account_joined or account_joined in requested_joined:
        return True
    return requested_tokens.issubset(account_tokens) or account_tokens.issubset(requested_tokens)


def flyer_business_scope_block_message(customer: dict, raw_request: str) -> str:
    """Return customer-safe copy when a brief names a different business.

    A registered account may ask for offers/products under its own brand, but
    explicit "flyer for <other business>" requests must not attach to an old
    active project or create work under the wrong customer account.
    """
    if not customer or str(customer.get("status") or "") not in {"trial", "active"}:
        return ""
    account_name = str(customer.get("business_name") or "").strip()
    if not account_name:
        return ""
    requested = _extract_requested_business_scope(raw_request)
    if not requested or not (_looks_like_business_scope(requested) or len(_normalize_business_scope(requested)) == 1):
        return ""
    if _business_scope_matches(requested, account_name):
        return ""
    return (
        "Flyer Studio\n"
        "------------\n"
        f"This account is set up for {account_name}. I can't create a flyer for {requested} under this account.\n\n"
        "To create it for that business, start a separate Flyer Studio setup for that business, "
        "or use Create One Flyer - $4."
    )


def flyer_location_block_message(customer: dict, raw_request: str) -> str:
    """Return denial copy when a request appears outside account location."""
    if not customer or str(customer.get("plan_id") or "") != "unlimited":
        return ""
    labels = [
        str(label).strip()
        for label in (customer.get("allowed_location_labels") or [])
        if str(label).strip()
    ]
    if not labels and customer.get("location_restriction_enabled"):
        labels = [str(customer.get("business_address") or "").strip()]
    if not labels:
        return ""
    requested = _detect_requested_location(raw_request, labels)
    if not requested:
        return ""
    allowed_text = _short_location_label(labels[0])
    if _location_matches_allowed(requested, labels):
        return ""
    return (
        "Flyer Studio\n"
        "------------\n"
        f"This account is set up for {allowed_text}. I can't create a flyer for {requested} under this subscription. Contact Support."
    )


def _detect_requested_location(raw_request: str, allowed_labels: list[str]) -> str:
    text = " ".join((raw_request or "").split())
    lower = text.lower()
    for label in _candidate_location_labels(allowed_labels):
        if label and re.search(rf"\b{re.escape(label.lower())}\b", lower):
            return label
    explicit = re.search(
        r"\b(?:at|in|location|branch|store)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})\b",
        text,
    )
    if explicit:
        candidate = explicit.group(1).strip()
        if candidate.lower() not in {"a", "the", "this", "my", "your"}:
            return candidate
    for name, abbr in _US_STATE_WORDS.items():
        if re.search(rf"\b{re.escape(name)}\b", lower):
            return name.title()
        if re.search(rf"\b{abbr.lower()}\b", lower):
            return abbr
    return ""


def _candidate_location_labels(labels: list[str]) -> list[str]:
    out: list[str] = []
    for label in labels:
        pieces = re.split(r"[,|-]", label)
        for piece in [label, *pieces]:
            cleaned = " ".join(piece.split()).strip()
            if cleaned and len(cleaned) >= 3:
                out.append(cleaned)
    return sorted(set(out), key=len, reverse=True)


def _short_location_label(label: str) -> str:
    if not label:
        return "this location"
    # Prefer a human branch/city token over the full address when available.
    for piece in re.split(r"[,|-]", label):
        cleaned = " ".join(piece.split()).strip()
        if cleaned and not re.search(r"\d", cleaned):
            return cleaned
    return " ".join(label.split()).strip()


def _location_matches_allowed(requested: str, allowed_labels: list[str]) -> bool:
    req = re.sub(r"[^a-z0-9]+", "", requested.lower())
    if not req:
        return True
    for label in allowed_labels:
        norm = re.sub(r"[^a-z0-9]+", "", label.lower())
        if req and norm and (req in norm or norm in req):
            return True
    return False


_FLYER_SAMPLE_PROMPT_PREFERENCE_PATTERN = (
    r"(?:"
    r"don'?t show sample prompts|do not show sample prompts|stop sample prompts|"
    r"hide sample prompts|turn off sample prompts|disable sample prompts|"
    r"stop showing examples|no sample prompts|no examples|"
    r"don'?t show examples|hide examples|stop examples|"
    r"show sample prompts again|show me sample prompts again|"
    r"enable sample prompts|turn on sample prompts|"
    r"bring back sample prompts|show examples again|bring back examples"
    r")"
)

_FLYER_OPTIONAL_POLITE_PREFIX = (
    r"^\s*(?:please\s+|can you\s+|could you\s+|kindly\s+|"
    r"hey[, ]+|hi[, ]+|hello[, ]+)?"
)

_FLYER_NATURAL_PLAN_CHANGE_PATTERN = (
    r"(?:"
    r"\b(?:upgrade|downgrade|switch|move|start|select|choose)(?:\s+me)?\s+"
    r"(?:(?:my|our)\s+)?(?:flyer\s+studio\s+)?(?:plan\s+)?(?:to\s+)?"
    r"(?:starter|growth|unlimited|49\.99|69\.99|199(?:\.99)?|30\s+flyers?|60\s+flyers?|unlimited\s+flyers?)\b|"
    r"\bi\s+(?:want|would\s+like|need)\s+(?:the\s+)?"
    r"(?:starter|growth|unlimited|49\.99|69\.99|199(?:\.99)?|30\s+flyers?|60\s+flyers?|unlimited\s+flyers?)"
    r"(?:\s+plan)?\b"
    r")"
)

_FLYER_REGULATED_ACCOUNT_PATTERN = re.compile(
    r"\b(?:"
    r"billing|checkout|invoice|card|stripe|razorpay|refund|"
    r"plan|starter|growth|unlimited|upgrade|downgrade|"
    r"account\s+owner|account\s+settings|business\s+name|business\s+address|"
    r"business\s+phone|business\s+whatsapp|whatsapp\s+number|authorized\s+(?:number|requester)|"
    # PR-α 2026-05-26 — verb-anchored account-change patterns. Closes the
    # operator's 24-pattern active-block list gaps: "change phone",
    # "change my phone number", "change address", and equivalents.
    # Verb anchor avoids false-positives on flyer briefs that mention
    # these fields without a mutation verb (e.g. "create a flyer with
    # our phone number"). NOTE: `email` deliberately excluded from this
    # slice per 2026-05-26 review — not in the operator's approved
    # 6-phrase-class PR-α scope. Add via a follow-up PR if/when scoped.
    r"(?:change|update|set|edit|modify|remove|delete)\s+(?:my\s+|the\s+|our\s+)?(?:flyer\s+|business\s+|account\s+|public\s+|contact\s+)?(?:phone(?:\s+number)?|address|number)"
    r")\b",
    re.IGNORECASE,
)

# PR-α follow-up 2026-05-26 — used by _try_flyer_regulated_account_guard to
# yield to active-project routing when both conditions hold:
#   (a) sender has an active flyer project, AND
#   (b) text matches an edit-instruction targeting a flyer attribute/field.
# Without this yield, PR-α's extended regulated-account regex would hijack
# legitimate flyer edits like "update this flyer, change the phone number"
# into a fail-closed account warning. Pattern intentionally mirrors the
# inline `edit_starter` regex in `is_flyer_project_status_request` (line ~2007)
# so the two stay consistent; consider de-duping in a future cleanup PR.
_FLYER_EDIT_INSTRUCTION_PATTERN = re.compile(
    r"\b(update|change|edit|modify|replace|remove|add|swap|fix|correct)\s+"
    r"(this|the|my|that|current|attached)?\s*"
    r"(flyer|design|poster|text|logo|item|price|date|time|name|phone|address)\b",
    re.IGNORECASE,
)


def flyer_text_targets_revision_field(text: str) -> bool:
    """Return True when text reads as an edit instruction against a flyer artifact/field.

    Matches phrases like "update this flyer", "change the phone number",
    "edit the price", "swap the logo". Used by the regulated-account guard
    in cf-router/hooks.py to yield to active-project routing when the
    sender has an active flyer project AND this returns True.
    """
    body = " ".join(flyer_visible_message_text(text).lower().split())
    return bool(_FLYER_EDIT_INSTRUCTION_PATTERN.search(body))

_FLYER_REGULATED_PAYMENT_PATTERN = re.compile(
    r"\b(?:"
    r"payment\s+(?:go\s+through|went\s+through|status|link|method|details?|failed|complete|completed|done)|"
    r"(?:paid|pay|paying)\s+(?:for|the|my|this|now|already)|"
    # PR-α 2026-05-26 — bare "I paid" + "mark paid" variants. Closes the
    # operator's 24-pattern active-block list payment-claim gaps. False
    # positive on phrases like "I paid attention" is acceptable per the
    # invariant (system may clarify, may not claim completion).
    r"i\s+(?:have\s+|just\s+|already\s+)?paid|"
    r"mark(?:ed|ing)?\s+(?:as\s+)?paid|"
    r"how\s+(?:do|can)\s+i\s+pay|"
    r"send\s+(?:me\s+)?(?:a\s+)?payment\s+link"
    r")\b",
    re.IGNORECASE,
)

# PR-β 2026-05-26 — delivery-state guard patterns. Tight phrase-anchored
# (NOT bare-token per the PR #250 false-positive lesson). Fires only when
# the active-project intercept has already yielded — i.e., no active or
# recently-closed flyer project resolved for the sender. The guard's job
# is to fail-closed with deterministic clarification copy instead of
# letting generic Hermes claim "I sent your flyer" with no evidence.
#
# Bare "approve" / "approve." is NOT in this pattern — handled separately
# via the existing is_flyer_approval_text semantics (entire-body equality
# after stripping punctuation) for consistency with the active-project
# intercept's approval check.
_FLYER_DELIVERY_STATE_PATTERN = re.compile(
    r"\b(?:"
    r"where(?:'s|\s+is)\s+(?:my\s+|the\s+)?flyer|"
    r"did\s+you\s+send\s+(?:me\s+|us\s+)?(?:my\s+|the\s+)?flyer|"
    r"send\s+(?:me\s+|us\s+)?(?:my\s+|the\s+)?flyer|"
    r"i\s+approve"
    r")\b",
    re.IGNORECASE,
)

# PR-β.1 2026-05-26 — "send now" style finalization request. START-ANCHORED
# (NOT searchable anywhere in body) so flyer briefs like "Create a flyer
# that says send now" cannot match — the brief's "send now" is inside the
# message body, not at the start. Customer-intent "send now" is dominant
# (start of message, optionally with polite prefix).
#
# Matches: "send now", "Send now.", "please send now", "kindly send now",
# "send me now", "send my flyer now", "send the flyer now", "send it now".
# Does NOT match: "send to customers Friday", "send me ideas",
# "send this to my team", "Create a flyer that says send now", any phrase
# where "send now" is embedded mid-message.
_FLYER_SEND_NOW_PATTERN = re.compile(
    r"^\s*(?:please\s+|kindly\s+)?send(?:\s+(?:me|us))?\s+"
    r"(?:(?:my|the|it)\s+)?(?:flyer\s+)?now\b",
    re.IGNORECASE,
)


def is_flyer_send_now_intent(text: str) -> bool:
    """Return True for explicit "send now" finalization requests at message start.

    PR-β.1 helper. Start-anchored to prevent matching flyer briefs that
    embed "send now" as copy text (e.g., "Create a flyer that says send now").
    Used by:
      - `_try_flyer_active_project_intercept` finalization gate (hooks.py:2808)
        as approval-equivalent in `revising_design` / `awaiting_final_approval`.
      - `_try_flyer_active_project_intercept` pending-revision-confirmation
        guard (hooks.py:2790) — same intent as "approve" when a revision is
        pending.
      - `_try_flyer_active_project_intercept` revision-text fallback
        (hooks.py:2894) — explicitly EXCLUDED so "send now" with delivered
        status falls through to PR-β guard for status-surface (instead of
        being mis-classified as a revision).
      - `is_flyer_delivery_state_intent` — included so PR-β guard catches
        "send now" with no active project (surfaces latest or fail-closes).
    """
    body = flyer_visible_message_text(text)
    return bool(_FLYER_SEND_NOW_PATTERN.search(body))


def is_flyer_delivery_state_intent(text: str) -> bool:
    """Return True for delivery/approval phrases that must not hit generic LLM.

    Used by `_try_flyer_delivery_state_guard` in cf-router/hooks.py. The
    guard runs AFTER `_try_flyer_active_project_intercept`, so this only
    matters when no active or recently-closed flyer project resolves —
    i.e., the message would otherwise fall through to generic Hermes.

    PR-β.1 2026-05-26 — "send now" is now included (was deferred in PR-β).
    The corresponding hooks.py wiring in `_try_flyer_active_project_intercept`
    routes "send now" through the existing approval/finalization path when
    the active project is in a finalizable state.
    """
    body = flyer_visible_message_text(text)
    lowered = body.lower()
    if _FLYER_DELIVERY_STATE_PATTERN.search(lowered):
        return True
    if is_flyer_send_now_intent(text):
        return True
    # Exact non-generic approval aliases. Bare "ok" / "yes" are meaningful
    # only when an active finalizable project already gates the approval path.
    return lowered.strip(" .!,:;") in _FLYER_DELIVERY_STATE_APPROVAL_ALIASES


def is_flyer_account_command(text: str) -> bool:
    body = flyer_visible_message_text(text)
    return bool(re.search(
        _FLYER_OPTIONAL_POLITE_PREFIX
        + r"(?:status|plan status|help|"
        + _FLYER_SAMPLE_PROMPT_PREFERENCE_PATTERN
        + r"|"
        r"add (authorized )?(number|auth)|add authorized number|"
        r"remove authorized number|remove number|"
        r"update business name|change business name|set business name|"
        r"update phone|update business phone|"
        r"update whatsapp|update business whatsapp|change plan|upgrade plan|upgrade to|downgrade to|"
        r"switch to|switch plan|move me to|select plan|choose plan|show flyer studio plans|confirm update|"
        + _FLYER_NATURAL_PLAN_CHANGE_PATTERN
        + r")\b",
        body or "",
        flags=re.IGNORECASE,
    ))


def is_flyer_regulated_account_intent(text: str) -> bool:
    """Return true for account/billing-shaped text that must not hit generic LLM.

    This is intentionally broader than `is_flyer_account_command`: command
    text routes to the deterministic account handler; regulated but unclear
    text gets a safe no-action clarification instead of an improvised success
    acknowledgement from generic Hermes chat.
    """
    body = flyer_visible_message_text(text)
    if is_flyer_account_command(body):
        return True
    if not body:
        return False
    return bool(_FLYER_REGULATED_ACCOUNT_PATTERN.search(body) or _FLYER_REGULATED_PAYMENT_PATTERN.search(body))


def is_flyer_starter_prompt_preference_command(text: str) -> bool:
    body = flyer_visible_message_text(text)
    return bool(re.search(
        _FLYER_OPTIONAL_POLITE_PREFIX + _FLYER_SAMPLE_PROMPT_PREFERENCE_PATTERN + r"\b",
        body or "",
        flags=re.IGNORECASE,
    ))


def trigger_flyer_account_command(
    *,
    chat_id: str,
    sender_phone: Optional[str],
    sender_role: str,
    text: str,
) -> tuple[bool, str, Optional[dict]]:
    try:
        cmd = [
            str(PYTHON_BIN),
            str(MANAGE_FLYER_ACCOUNT_BIN),
            "--command-text", text or "",
            "--sender-role", sender_role or "",
            "--chat-id", chat_id,
        ]
        if sender_phone:
            cmd.extend(["--sender-phone", sender_phone])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC)
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}", None
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"exit={result.returncode} {detail[:500]}", None
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"account_json_parse_failed: {detail[:500]}", None
    return True, detail[:500], doc


def _trigger_flyer_quota(
    mode: str,
    *,
    customer_phone: str,
    project_id: str,
    message_id: str,
) -> tuple[bool, str, Optional[dict]]:
    try:
        cmd = [
            str(PYTHON_BIN),
            str(MANAGE_FLYER_ACCOUNT_BIN),
            mode,
            "--customer-phone", customer_phone,
            "--project-id", project_id,
            "--message-id", message_id,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC)
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}", None
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"exit={result.returncode} {detail[:500]}", None
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"quota_json_parse_failed: {detail[:500]}", None
    return True, detail[:500], doc


def trigger_flyer_reserve_quota(*, customer_phone: str, project_id: str, message_id: str) -> tuple[bool, str, Optional[dict]]:
    return _trigger_flyer_quota("--reserve-quota", customer_phone=customer_phone, project_id=project_id, message_id=message_id)


def trigger_flyer_finalize_usage(*, customer_phone: str, project_id: str, message_id: str) -> tuple[bool, str, Optional[dict]]:
    return _trigger_flyer_quota("--finalize-usage", customer_phone=customer_phone, project_id=project_id, message_id=message_id)


def trigger_flyer_release_quota(*, customer_phone: str, project_id: str, message_id: str) -> tuple[bool, str, Optional[dict]]:
    return _trigger_flyer_quota("--release-quota", customer_phone=customer_phone, project_id=project_id, message_id=message_id)


def _trigger_flyer_guest_order(*args: str) -> tuple[bool, str, Optional[dict]]:
    try:
        result = subprocess.run(
            [str(PYTHON_BIN), str(MANAGE_FLYER_GUEST_ORDER_BIN), *args],
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}", None
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        try:
            return False, f"exit={result.returncode} {detail[:500]}", json.loads(result.stdout)
        except json.JSONDecodeError:
            return False, f"exit={result.returncode} {detail[:500]}", None
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"guest_order_json_parse_failed: {detail[:500]}", None
    return True, detail[:500], doc


def _guest_sender_phone_required(sender_phone: Optional[str]) -> Optional[tuple[bool, str, Optional[dict]]]:
    if sender_phone:
        return None
    return False, "sender_phone_required", {
        "ok": False,
        "handled": True,
        "detail": "sender_phone_required",
    }


def trigger_start_flyer_guest_order(*, sender_phone: Optional[str], chat_id: str, message_id: str) -> tuple[bool, str, Optional[dict]]:
    missing = _guest_sender_phone_required(sender_phone)
    if missing:
        return missing
    return _trigger_flyer_guest_order(
        "--start",
        "--sender-phone", sender_phone,
        "--chat-id", chat_id,
        "--message-id", message_id,
    )


def trigger_consume_flyer_guest_order(*, sender_phone: Optional[str], chat_id: str, project_id: str) -> tuple[bool, str, Optional[dict]]:
    missing = _guest_sender_phone_required(sender_phone)
    if missing:
        return missing
    return _trigger_flyer_guest_order(
        "--consume",
        "--sender-phone", sender_phone,
        "--chat-id", chat_id,
        "--project-id", project_id,
    )


def trigger_reserve_flyer_guest_order(*, sender_phone: Optional[str], chat_id: str, project_id: str) -> tuple[bool, str, Optional[dict]]:
    missing = _guest_sender_phone_required(sender_phone)
    if missing:
        return missing
    return _trigger_flyer_guest_order(
        "--reserve",
        "--sender-phone", sender_phone,
        "--chat-id", chat_id,
        "--project-id", project_id,
    )


def trigger_release_flyer_guest_order(*, sender_phone: Optional[str], chat_id: str, project_id: str) -> tuple[bool, str, Optional[dict]]:
    missing = _guest_sender_phone_required(sender_phone)
    if missing:
        return missing
    return _trigger_flyer_guest_order(
        "--release",
        "--sender-phone", sender_phone,
        "--chat-id", chat_id,
        "--project-id", project_id,
    )


def find_reserved_flyer_guest_order(sender_phone: Optional[str], chat_id: str, project_id: str) -> Optional[dict]:
    if not sender_phone:
        return None
    ok, _detail, doc = _trigger_flyer_guest_order(
        "--find-reserved",
        "--sender-phone", sender_phone,
        "--chat-id", chat_id,
        "--project-id", project_id,
    )
    if ok and doc and doc.get("reserved_order"):
        return doc
    return None


def find_paid_flyer_guest_order(sender_phone: Optional[str], chat_id: str) -> Optional[dict]:
    if not sender_phone:
        return None
    ok, _detail, doc = _trigger_flyer_guest_order(
        "--find-paid",
        "--sender-phone", sender_phone,
        "--chat-id", chat_id,
    )
    if ok and doc and doc.get("paid_order"):
        return doc
    return None


def trigger_flyer_onboarding(
    *,
    chat_id: str,
    sender_phone: Optional[str],
    message_id: str,
    text: str,
) -> tuple[bool, str, Optional[dict]]:
    """Invoke handle-flyer-onboarding and return (ok, detail, result)."""
    try:
        cmd = [
            str(PYTHON_BIN),
            str(HANDLE_FLYER_ONBOARDING_BIN),
            "--chat-id", chat_id,
            "--message-id", message_id,
            "--text", text,
            "--state-path", str(FLYER_CUSTOMERS_PATH),
            "--config-path", str(CONFIG_PATH),
            "--audit-log-path", str(LOG_PATH),
        ]
        if sender_phone:
            cmd.extend(["--sender-phone", sender_phone])
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}", None
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"exit={result.returncode} {detail[:500]}", None
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"onboarding_json_parse_failed: {detail[:500]}", None
    return True, detail[:500], doc


def trigger_flyer_intake(
    *,
    chat_id: str,
    sender_phone: Optional[str],
    message_id: str,
    text: str,
    media_path: str = "",
    start_source: str = "",
    original_text: str = "",
) -> tuple[bool, str, Optional[dict]]:
    """Invoke handle-flyer-intake and return (ok, detail, result)."""
    try:
        cmd = [
            str(PYTHON_BIN),
            str(HANDLE_FLYER_INTAKE_BIN),
            "--chat-id", chat_id,
            "--message-id", message_id,
            "--text", text or "",
            "--state-path", str(FLYER_CUSTOMERS_PATH),
        ]
        if sender_phone:
            cmd.extend(["--sender-phone", sender_phone])
        if media_path:
            cmd.extend(["--media-path", media_path])
        if start_source:
            cmd.extend(["--start-source", start_source])
        if original_text:
            cmd.extend(["--original-text", original_text])
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}", None
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"exit={result.returncode} {detail[:500]}", None
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"intake_json_parse_failed: {detail[:500]}", None
    return True, detail[:500], doc


def trigger_store_flyer_brand_asset(
    *,
    chat_id: str,
    sender_phone: Optional[str],
    message_id: str,
    media_path: str,
    text: str,
    sender_role: str = "",
) -> tuple[bool, str, Optional[dict]]:
    """Invoke store-flyer-brand-asset and return (ok, detail, result)."""
    try:
        cmd = [
            str(PYTHON_BIN),
            str(STORE_FLYER_BRAND_ASSET_BIN),
            "--chat-id", chat_id,
            "--message-id", message_id,
            "--media-path", media_path,
            "--text", text or "",
            "--sender-role", sender_role or "",
        ]
        if sender_phone:
            cmd.extend(["--sender-phone", sender_phone])
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}", None
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"exit={result.returncode} {detail[:500]}", None
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"brand_asset_json_parse_failed: {detail[:500]}", None
    return True, detail[:500], doc


def trigger_create_flyer_project(
    *,
    customer_phone: str,
    chat_id: str = "",
    raw_request: str,
    message_id: str,
    reference_media_path: str = "",
    manual_edit_required: bool = False,
) -> tuple[bool, str, Optional[dict]]:
    """Invoke create-flyer-project and return (ok, detail, project)."""
    try:
        cmd = [
            str(PYTHON_BIN),
            str(CREATE_FLYER_PROJECT_BIN),
            "--customer-phone", customer_phone,
            "--message-id", message_id,
            "--raw-request", raw_request,
        ]
        if chat_id:
            cmd.extend(["--chat-id", chat_id])
        if reference_media_path:
            cmd.extend(["--reference-media-path", reference_media_path])
            cmd.append("--defer-reference-extraction")
        if manual_edit_required:
            cmd.append("--manual-edit-required")
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}", None
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"exit={result.returncode} {detail[:500]}", None
    try:
        project = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"project_json_parse_failed: {detail[:500]}", None
    return True, detail[:500], project


def _resolve_flyer_source_edit_provider_for_preflight():
    try:
        _ensure_platform_path()
        from schemas import Config  # type: ignore
    except Exception:
        _ensure_local_src_path()
        platform = Path(__file__).resolve().parents[2] / "platform"
        p = str(platform)
        if p not in sys.path:
            sys.path.insert(0, p)
        from schemas import Config  # type: ignore
    import yaml  # type: ignore
    cfg = Config.model_validate(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {})
    return cfg.flyer.resolve_source_edit_render_provider()


def flyer_source_edit_preflight(project: dict) -> tuple[bool, str, str]:
    """Return ``(ok, detail, reason_code)`` for source-preserving edit readiness.

    On success: ``(True, "ready", "")``.

    On failure, ``reason_code`` is a `FlyerManualReviewReason` enum value that
    cockpit triage groups + tallies on, so callers MUST NOT hardcode a single
    code for every failure mode. Mapping:

      - ``source_edit_provider_unavailable`` — configured provider key
        absent/placeholder, manual-review sentinel selected, or the workflow
        helper failed to import (provider stack broken).
      - ``reference_unsupported`` — reference media is PDF / non-image type
        the source-edit endpoint cannot consume.
      - ``reference_provider_unavailable`` — no reference image attached to
        the project, OR the attached image is no longer on disk (retention,
        failover). Operator action is "re-upload the source flyer."
    """
    try:
        _ensure_platform_path()
        from flyer_workflow import source_edit_provider_ready  # type: ignore
    except Exception:
        try:
            _ensure_local_src_path()
            from agents.flyer.workflow import source_edit_provider_ready  # type: ignore
        except Exception as e:
            return (
                False,
                f"source edit readiness helper unavailable: {type(e).__name__}: {e}",
                "source_edit_provider_unavailable",
            )
    try:
        provider = _resolve_flyer_source_edit_provider_for_preflight()
    except Exception as e:
        return (
            False,
            f"source edit provider config unavailable: {type(e).__name__}: {e}",
            "source_edit_provider_unavailable",
        )
    env_path = CONFIG_PATH.parent / ".env"
    ok, detail = source_edit_provider_ready(project, provider=provider, env_path=env_path)
    if not ok:
        if "uploaded reference image" in detail:
            return ok, detail, "reference_provider_unavailable"
        if "must be an image" in detail:
            return ok, detail, "reference_unsupported"
        return ok, detail, "source_edit_provider_unavailable"
    assets = project.get("assets") or []
    reference = next((asset for asset in reversed(assets) if (asset or {}).get("kind") == "reference_image"), None)
    path = str((reference or {}).get("path") or "")
    if path.lower().endswith(".pdf"):
        return False, "source edit from PDF is not supported yet", "reference_unsupported"
    if path and not Path(path).exists():
        return False, "source edit reference image is not available on this server", "reference_provider_unavailable"
    return True, "ready", ""


def trigger_check_flyer_reference_scope(
    *,
    customer: dict,
    media_path: str,
    raw_request: str,
) -> tuple[bool, str, Optional[dict]]:
    """Ask vision whether an attached reference flyer belongs to the account."""
    business_name = str(customer.get("business_name") or "").strip()
    if not business_name or not media_path:
        return True, "scope_check_not_applicable", {"decision": "allow", "reason": "not_applicable"}
    if os.environ.get("FLYER_REFERENCE_SCOPE_ALLOW_SPEND") != "1":
        lower = " ".join((raw_request or "").lower().split())
        if _looks_like_exact_source_edit_request(lower):
            return True, "scope_check_deferred_no_spend", _reference_scope_clarification_payload(business_name)
        if re.search(r"\b(?:logo|menu|price\s*list|items?|prices?)\b", lower):
            return True, "scope_check_skipped_no_spend", {"decision": "allow", "reason": "no_spend_menu_or_logo"}
        return True, "scope_check_deferred_no_spend", _reference_scope_clarification_payload(business_name)
    cmd = [
        str(PYTHON_BIN),
        str(CHECK_FLYER_REFERENCE_SCOPE_BIN),
        "--media-path", media_path,
        "--business-name", business_name,
        "--business-address", str(customer.get("business_address") or ""),
        "--raw-request", raw_request,
    ]
    phones = set(customer.get("authorized_request_numbers") or [])
    for key in ("business_whatsapp_number", "onboarded_by_phone", "public_phone"):
        value = customer.get(key)
        if value:
            phones.add(value)
    for phone in sorted(str(p) for p in phones if str(p).strip()):
        cmd.extend(["--account-phone", phone])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}", None
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"exit={result.returncode} {detail[:500]}", None
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"scope_check_json_parse_failed: {detail[:500]}", None
    return True, detail[:500], doc


def _reference_scope_clarification_payload(business_name: str) -> dict:
    return {
        "decision": "clarify",
        "reason": "scope_check_requires_provider_after_quota",
        "reply_text": (
            "Flyer Studio\n"
            "------------\n"
            f"I need to confirm whether the attached flyer belongs to {business_name}.\n\n"
            f"If you own or are authorized to use this flyer, reply with how it is connected to {business_name}, "
            f"and send the {business_name} logo/details to use.\n"
            f"If this is only a reference, reply \"use as reference\" and Flyer Studio can create a new original "
            f"{business_name} flyer using it as inspiration without copying another business's branding/layout exactly."
        ),
    }


def _looks_like_exact_source_edit_request(text: str) -> bool:
    """Return true for edit-this-uploaded-flyer requests from a known account.

    The no-spend fallback cannot run vision/OCR, so exact edits ("remove the
    extra 16:00 from this flyer") defer to the source-vs-new clarification path
    instead of auto-copying attached artwork. Keep detection narrow: it must be
    an edit verb plus source-flyer language or a concrete text/date/time/extra
    correction.
    """
    body = " ".join((text or "").lower().split())
    has_edit_verb = bool(re.search(r"\b(?:remove|delete|change|replace|fix|correct|edit|update|add|modify|revise)\b", body))
    has_source_marker = bool(re.search(
        r"\b(?:this|attached|uploaded|source|existing).{0,30}\b(?:flyer|poster|image|artwork)\b",
        body,
    ))
    has_change_marker = bool(re.search(r"\b(?:change|changes?|chg|chng|chsng|chsnge|correction|revision)\b", body))
    has_text_correction_marker = bool(re.search(r"\b(?:date|time|extra|text|typo|spelling)\b", body))
    return (has_edit_verb or has_change_marker) and (has_source_marker or has_text_correction_marker)


def _reference_scope_choice(text: str) -> str:
    body = " ".join(flyer_visible_message_text(text).split()).lower().strip(" .!,:;-")
    if body in {"1", "option 1", "path 1", "choice 1"}:
        return "authorized"
    if body in {"2", "option 2", "path 2", "choice 2"}:
        return "use_reference"
    if "use as reference" in body or "only a reference" in body or "reference only" in body:
        return "use_reference"
    if "authorized" in body or "i own" in body or "we own" in body or "connected" in body:
        return "authorized"
    return ""


def _reference_scope_explicit_choice(text: str) -> str:
    body = " ".join(flyer_visible_message_text(text).split()).lower().strip(" .!,:;-")
    if body in {"1", "option 1", "path 1", "choice 1"}:
        return "authorized"
    if body in {"2", "option 2", "path 2", "choice 2"}:
        return "use_reference"
    if "use as reference" in body or "only a reference" in body or "reference only" in body:
        return "use_reference"
    return ""


def _read_reference_scope_state(now: Optional[float] = None) -> dict:
    now_ts = time.time() if now is None else now
    try:
        doc = json.loads(FLYER_REFERENCE_SCOPE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        doc = {"schema_version": 1, "pending": []}
    pending = [
        item for item in doc.get("pending", [])
        if isinstance(item, dict) and float(item.get("expires_at") or 0) >= now_ts
    ]
    return {"schema_version": 1, "pending": pending}


def _write_reference_scope_state(doc: dict) -> None:
    FLYER_REFERENCE_SCOPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, separators=(",", ":"), sort_keys=True)
    atomic_write_text = _reference_scope_atomic_writer()
    atomic_write_text(FLYER_REFERENCE_SCOPE_PATH, text)


def _reference_scope_atomic_writer() -> Callable[[Path, str], None]:
    _ensure_platform_path()
    try:
        from safe_io import atomic_write_text  # type: ignore
        return atomic_write_text
    except Exception:
        # Windows unit-test fallback; production imports safe_io and uses fsync+replace.
        def _fallback(path: Path, content: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return _fallback


@contextmanager
def _reference_scope_state_lock() -> Iterator[None]:
    _ensure_platform_path()
    try:
        from safe_io import FileLock  # type: ignore
    except Exception:
        yield
        return
    with FileLock(Path(str(FLYER_REFERENCE_SCOPE_PATH) + ".lock")):
        yield


def save_flyer_reference_scope_pending(
    *,
    chat_id: str,
    sender_phone: str,
    customer: dict,
    raw_request: str,
    media_path: str,
    scope: dict,
    ttl_sec: int = 1800,
    status: str = "awaiting_choice",
    authorization_note: str = "",
    original_intent: str = "unknown",
) -> None:
    """Remember that the last unrelated-reference reply is awaiting option 1/2.

    `original_intent` records whether the original raw request was an
    exact-source-edit ('exact_source_edit') or a generic reference use
    ('generic_reference'). Downstream intercepts branch on this to decide
    whether `use as reference` triggers the SOURCE/NEW clarification path
    instead of immediate generic generation. Defaults to 'unknown' for
    callers that have not been updated.
    """
    if not chat_id or not media_path:
        return
    now_ts = time.time()
    source_names = [
        str(name).strip()
        for name in (scope.get("visible_organization_names") or [])
        if str(name).strip()
    ]
    with _reference_scope_state_lock():
        state = _read_reference_scope_state(now_ts)
        pending = [
            item for item in state.get("pending", [])
            if item.get("chat_id") != chat_id and item.get("sender_phone") != sender_phone
        ]
        pending.append({
            "chat_id": chat_id,
            "sender_phone": sender_phone,
            "customer": {
                "business_name": str(customer.get("business_name") or ""),
                "customer_id": str(customer.get("customer_id") or ""),
            },
            "raw_request": raw_request,
            "media_path": media_path,
            "source_organization": source_names[0] if source_names else "",
            "status": status,
            "authorization_note": authorization_note,
            "original_intent": original_intent,
            "created_at": now_ts,
            "expires_at": now_ts + max(60, ttl_sec),
        })
        _write_reference_scope_state({"schema_version": 1, "pending": pending})


def save_flyer_reference_authorization_pending(pending: dict, authorization_note: str = "") -> None:
    """Keep a reference-scope block alive after the customer chooses path 1."""
    customer = pending.get("customer") or {}
    source = str(pending.get("source_organization") or "").strip()
    scope = {"visible_organization_names": [source] if source else []}
    save_flyer_reference_scope_pending(
        chat_id=str(pending.get("chat_id") or ""),
        sender_phone=str(pending.get("sender_phone") or ""),
        customer=customer if isinstance(customer, dict) else {},
        raw_request=str(pending.get("raw_request") or ""),
        media_path=str(pending.get("media_path") or ""),
        scope=scope,
        status="awaiting_authorization_details",
        authorization_note=authorization_note,
        original_intent=str(pending.get("original_intent") or "unknown"),
    )


def consume_flyer_reference_scope_choice(
    text: str,
    *,
    chat_id: str,
    sender_phone: str,
    transition_to_status: Optional[str] = None,
) -> Optional[dict]:
    """Return pending reference-scope choice for option 1/2 replies, consuming it.

    When `transition_to_status` is provided AND the matched row's choice is
    `use_reference` AND its `original_intent == 'exact_source_edit'`, the row
    is REWRITTEN in-place with the new status under the same lock instead of
    being removed. This eliminates the race window between the
    `_try_flyer_reference_scope_choice_intercept` consume step and the
    SOURCE/NEW intercept's lookup of the awaiting-source-vs-new-choice row.
    """
    choice = _reference_scope_choice(text)
    if not choice:
        return None
    with _reference_scope_state_lock():
        state = _read_reference_scope_state()
        pending = state.get("pending", [])
        matched: Optional[dict] = None
        matched_index: int = -1
        remaining: list[dict] = []
        for item in pending:
            if str(item.get("status") or "awaiting_choice") != "awaiting_choice":
                remaining.append(item)
                continue
            same_chat = chat_id and item.get("chat_id") == chat_id
            same_phone = sender_phone and item.get("sender_phone") == sender_phone
            if matched is None and (same_chat or same_phone):
                matched = dict(item)
                matched_index = len(remaining)
                continue
            remaining.append(item)
        if matched is None:
            if remaining != pending:
                _write_reference_scope_state({"schema_version": 1, "pending": remaining})
            return None
        if (
            transition_to_status
            and choice == "use_reference"
            and str(matched.get("original_intent") or "") == "exact_source_edit"
        ):
            transitioned = dict(matched)
            transitioned["status"] = transition_to_status
            remaining.insert(matched_index, transitioned)
            _write_reference_scope_state({"schema_version": 1, "pending": remaining})
        else:
            _write_reference_scope_state({"schema_version": 1, "pending": remaining})
    matched["choice"] = choice
    return matched


def consume_flyer_source_vs_new_choice(
    choice_token: str,
    trailing: str,
    *,
    chat_id: str,
    sender_phone: str,
) -> Optional[dict]:
    """Consume a pending awaiting_source_vs_new_choice row for SOURCE/NEW reply.

    Returns the row (with `choice` and `customer_followup_instruction`
    attached) if a matching row exists, else None. Removes the row from the
    state file inside the same lock that scopes the read.
    """
    if choice_token not in {"source", "new"}:
        return None
    with _reference_scope_state_lock():
        state = _read_reference_scope_state()
        pending = state.get("pending", [])
        matched: Optional[dict] = None
        remaining: list[dict] = []
        for item in pending:
            if str(item.get("status") or "") != "awaiting_source_vs_new_choice":
                remaining.append(item)
                continue
            same_chat = chat_id and item.get("chat_id") == chat_id
            same_phone = sender_phone and item.get("sender_phone") == sender_phone
            if matched is None and (same_chat or same_phone):
                matched = dict(item)
                continue
            remaining.append(item)
        if matched is None:
            return None
        _write_reference_scope_state({"schema_version": 1, "pending": remaining})
    matched["choice"] = choice_token
    matched["customer_followup_instruction"] = trailing or ""
    return matched


def peek_flyer_source_vs_new_pending(
    *,
    chat_id: str,
    sender_phone: str,
) -> Optional[dict]:
    """Read-only lookup for the awaiting_source_vs_new_choice row.

    Used by the status check-in branch so it can re-send the clarification
    without consuming the pending row.
    """
    with _reference_scope_state_lock():
        state = _read_reference_scope_state()
        for item in state.get("pending", []):
            if str(item.get("status") or "") != "awaiting_source_vs_new_choice":
                continue
            same_chat = chat_id and item.get("chat_id") == chat_id
            same_phone = sender_phone and item.get("sender_phone") == sender_phone
            if same_chat or same_phone:
                return dict(item)
    return None


_SOURCE_TOKEN_RE = re.compile(
    r"^\s*(?P<token>source|keep\s+source|same\s+flyer|exact\s+edit|option\s*1|1)\b[\s.,:;!\-—]*(?P<trailing>.*)$",
    flags=re.IGNORECASE | re.DOTALL,
)
_NEW_TOKEN_RE = re.compile(
    r"^\s*(?P<token>new|new\s+flyer|inspired(?:\s+by)?|option\s*2|2)\b[\s.,:;!\-—]*(?P<trailing>.*)$",
    flags=re.IGNORECASE | re.DOTALL,
)


def parse_source_vs_new_followup(text: str) -> tuple[str, str]:
    """Return (choice, trailing). choice is 'source'|'new'|''."""
    body = " ".join(flyer_visible_message_text(text).split())
    for choice, pattern in (("source", _SOURCE_TOKEN_RE), ("new", _NEW_TOKEN_RE)):
        match = pattern.match(body)
        if match:
            trailing = " ".join(match.group("trailing").strip(" .,:;-—").split())
            return choice, trailing[:500]
    return "", ""


def flyer_is_status_checkin(text: str) -> bool:
    # Keep SOURCE/NEW pending-status detection aligned with the main status
    # router so one path does not miss natural status wording variants.
    return is_flyer_project_status_request(text)


def find_recent_flyer_manual_edit_project(
    customer_phone: str,
    *,
    window_sec: int = 60,
) -> Optional[dict]:
    """Return the most recent manual_edit_required project for this customer
    created within `window_sec` seconds. Used by the idempotent-retry
    branch of the SOURCE/NEW intercept."""
    try:
        doc = json.loads(Path(str(FLYER_PROJECTS_PATH)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    now_ts = time.time()
    candidates = []
    for project in doc.get("projects", []) or []:
        if not isinstance(project, dict):
            continue
        if project.get("customer_phone") != customer_phone:
            continue
        if project.get("status") != "manual_edit_required":
            continue
        created_at = project.get("created_at") or ""
        try:
            ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            continue
        if now_ts - ts <= max(1, window_sec):
            candidates.append((ts, project))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _consume_flyer_reference_authorization_reply_locked(
    body: str,
    *,
    chat_id: str,
    sender_phone: str,
) -> Optional[dict]:
    state = _read_reference_scope_state()
    pending = state.get("pending", [])
    matched: Optional[dict] = None
    remaining: list[dict] = []
    # The bot's scope-check prompt invites a narrative reply ("reply with how
    # it is connected to <Business>"). A reply like "Co-owner" / "Family
    # business" / "Founder's sister" doesn't match any of the keyword tokens
    # in `_reference_scope_choice` (i_own / we_own / authorized / connected),
    # so it never consumes the `awaiting_choice` row at the choice intercept.
    # Pre-fix it then fell through to `_try_flyer_active_project_intercept`
    # and got routed as a revision against the source-edit project, returning
    # "I could not match that change to the queued edit" — the exact prod bug
    # observed on F0050.
    #
    # Fix: this consumer ALSO matches `awaiting_choice` rows when the body
    # looks like a substantive relationship answer rather than a trivial
    # acknowledgement. Definition of "substantive":
    #   - at least 4 alphabetic characters after stripping (rejects "ok",
    #     "yes", "yep", "k", "ya")
    #   - AND not in a small ack-only set (rejects "yeah", "okay", "sure",
    #     "fine", "thanks", "cool" — these are intent-ambiguous, route
    #     elsewhere)
    # The caller (`consume_flyer_reference_authorization_reply`) has already
    # filtered explicit "1" / "2" / "use as reference" replies via
    # `_reference_scope_explicit_choice`, so those still route through the
    # choice intercept rather than landing here. Conservative threshold:
    # false-negative (narrative reply misses the new path) keeps today's
    # behavior; false-positive (ack consumes a choice row) would silently
    # start a source-edit the customer didn't authorize.
    _ACK_ONLY = {"yeah", "okay", "sure", "fine", "thanks", "cool", "ok", "yes", "yep", "yup"}
    body_alpha = "".join(ch for ch in body if ch.isalpha())
    body_lower = body.lower().strip(" .!,:;-")
    body_is_substantive = len(body_alpha) >= 4 and body_lower not in _ACK_ONLY
    consumable_statuses = {"awaiting_authorization_details"}
    if body_is_substantive:
        consumable_statuses.add("awaiting_choice")

    for item in pending:
        same_chat = chat_id and item.get("chat_id") == chat_id
        same_phone = sender_phone and item.get("sender_phone") == sender_phone
        item_status = str(item.get("status") or "")
        is_consumable = item_status in consumable_statuses
        if matched is None and is_consumable and (same_chat or same_phone):
            matched = dict(item)
            continue
        remaining.append(item)
    if matched is None:
        return None

    lower = body.lower().strip(" .!,:;-")
    if lower in {"use account details", "use saved details", "use business details", "continue"}:
        _write_reference_scope_state({"schema_version": 1, "pending": remaining})
        matched["choice"] = "use_account_details"
        return matched

    note = str(matched.get("authorization_note") or "").strip()
    combined = "; ".join(part for part in [note, body] if part)
    matched["authorization_note"] = combined
    _write_reference_scope_state({"schema_version": 1, "pending": remaining})
    matched["choice"] = "use_account_details"
    matched["authorization_reply"] = body
    return matched


def consume_flyer_reference_authorization_reply(
    text: str,
    *,
    chat_id: str,
    sender_phone: str,
) -> Optional[dict]:
    """Handle the follow-up after option 1 without falling into revision parsing."""
    body = " ".join(flyer_visible_message_text(text).split()).strip()
    if not body:
        return None
    if _reference_scope_explicit_choice(body):
        return None

    with _reference_scope_state_lock():
        return _consume_flyer_reference_authorization_reply_locked(
            body,
            chat_id=chat_id,
            sender_phone=sender_phone,
        )


def send_flyer_manual_edit_ack(
    chat_id: str,
    project_id: str,
    request_text: str = "",
    reason: str = "",
    *,
    action_context: Optional[ActionExecutionContext],
) -> tuple[bool, str, str]:
    """Acknowledge a queued source-preserving flyer edit on WhatsApp.

    The WhatsApp body is deliberately outcome-only: it confirms receipt and
    promises delivery, nothing more. Workflow internals (source-preserving,
    edit queue, operator/provider language, raw customer request echo,
    project ID) live in the audit log and Cockpit, not the customer reply.
    Reason: echoing the request text was the F0063 drift surface; explaining
    the queue is workflow leakage. `request_text`, `project_id`, and `reason`
    remain on the signature for caller compatibility (7 sites in cf-router/
    hooks.py) but no longer reach the WhatsApp message body.

    PR-ζ.1b 2026-05-26 — `action_context` is keyword-only. Default None for
    callsite-migration ordering (commit 7 introduces optional kwarg, commit 8
    migrates callsites, commit 9 drops the default).
    """
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    message = (
        "Flyer Studio\n"
        "------------\n"
        "Got it. This needs a careful flyer edit. "
        "I'll send the updated flyer here once it's ready."
    )
    ok, message_id, err, status = bridge_post(
        chat_id, message, action_context=action_context,
    )
    if ok:
        return True, message_id, ""
    return False, message_id, f"{status}: {err}"


def send_flyer_manual_review_ack(
    chat_id: str,
    project_id: str,
    request_text: str = "",
    reason: str = "",
    *,
    action_context: Optional[ActionExecutionContext],
) -> tuple[bool, str, str]:
    """Acknowledge fail-closed manual review without exposing workflow internals.

    PR-ζ.1b 2026-05-26 — `action_context` is keyword-only; see
    `send_flyer_manual_edit_ack` for default-None rationale.
    """
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    del project_id, request_text, reason
    message = (
        "Flyer Studio\n"
        "------------\n"
        "I couldn't finish this automatically. I'll review it and send an update here."
    )
    ok, message_id, err, status = bridge_post(
        chat_id, message, action_context=action_context,
    )
    if ok:
        return True, message_id, ""
    return False, message_id, f"{status}: {err}"


def flyer_project_has_manual_review_queued(project: Optional[dict]) -> bool:
    if not project:
        return False
    manual = project.get("manual_review") or {}
    manual_status = str(manual.get("status") or "").strip().lower()
    return (
        project.get("status") == "manual_edit_required"
        and manual_status in {"queued", "in_progress"}
    )


_FALLBACK_MANUAL_REVIEW_REASON_CODES = {
    "reference_low_confidence",
    "reference_provider_unavailable",
    "reference_unsupported",
    "reference_not_run",
    "visual_qa_failed",
    "source_edit_provider_unavailable",
    "operator_request",
    "policy_block",
    "provider_timeout",
    "dependency_missing",
    "missing_required_facts",
}


def _manual_review_queued_reason_codes() -> set[str]:
    try:
        _ensure_platform_path()
        from schemas import FlyerManualReviewReason  # type: ignore
        reason_codes = {str(value) for value in get_args(FlyerManualReviewReason)}
    except Exception:
        reason_codes = set(_FALLBACK_MANUAL_REVIEW_REASON_CODES)
    return reason_codes - {"unclassified", "legacy_unknown"}


def _iter_generation_detail_json_objects(detail: str) -> Iterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    text = str(detail or "")
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[idx:])
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict):
            yield obj


def _generation_payload_has_manual_review(payload: dict[str, Any]) -> bool:
    queued_reason_codes = _manual_review_queued_reason_codes()
    manual = payload.get("manual_review")
    if isinstance(manual, dict):
        manual_status = str(manual.get("status") or "").strip().lower()
        if manual_status in {"queued", "in_progress"}:
            return True
        if manual_status:
            return False
        reason_code = str(manual.get("reason_code") or "").strip().lower()
        if reason_code in queued_reason_codes:
            return True
    reason_code = str(payload.get("manual_review_reason_code") or "").strip().lower()
    return reason_code in queued_reason_codes


def flyer_generation_queued_manual_review(detail: str) -> bool:
    detail_text = str(detail or "")
    payloads = list(_iter_generation_detail_json_objects(detail_text))
    saw_structured_manual_signal = False
    for payload in payloads:
        if "manual_review_reason_code" in payload or isinstance(payload.get("manual_review"), dict):
            saw_structured_manual_signal = True
        if _generation_payload_has_manual_review(payload):
            return True
    if saw_structured_manual_signal:
        return False

    detail_lower = detail_text.lower()
    if "reference_extraction_failed" in detail_lower:
        return True
    if "source_edit_failed" in detail_lower:
        return True
    if "visual_qa_failed" in detail_lower:
        return True
    if re.search(
        r"reason_code\s*=\s*(source_edit_provider_unavailable|visual_qa_failed|reference_unsupported|reference_provider_unavailable|source_edit_generation_failed)",
        detail_lower,
    ):
        return True
    if re.search(r"manual_review\.status\s*=\s*(queued|in_progress)", detail_lower):
        return True
    if re.search(r'"manual_review"\s*:\s*\{[^{}]*"status"\s*:\s*"(queued|in_progress)"', detail_lower):
        return True
    return False


def send_flyer_edit_processing_ack(
    chat_id: str,
    project_id: str,
    *,
    action_context: Optional[ActionExecutionContext],
) -> tuple[bool, str, str]:
    """Acknowledge source-edit generation before the model call.

    PR-ζ.1b 2026-05-26 — `action_context` is keyword-only; default-None
    rationale matches the other ack wrappers above.
    """
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    del project_id
    message = (
        "Flyer Studio\n"
        "------------\n"
        "Got it. I'm updating your flyer now and will send the revised version here when it's ready."
    )
    ok, message_id, err, status = bridge_post(
        chat_id, message, action_context=action_context,
    )
    if ok:
        return True, message_id, ""
    return False, message_id, f"{status}: {err}"


def send_flyer_intake_ack(
    chat_id: str,
    project_id: str,
    *,
    action_context: Optional[ActionExecutionContext],
) -> tuple[bool, str, str]:
    """Send the deterministic Flyer Studio intake acknowledgement.

    PR-ζ.1b 2026-05-26 — `action_context` keyword-only; same default-None
    rationale as the other ack wrappers.
    """
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    message = (
        "Flyer Studio\n"
        "------------\n"
        "Got it. I have your flyer request and will send an update here shortly."
    )
    ok, message_id, err, status = bridge_post(
        chat_id, message, action_context=action_context,
    )
    if ok:
        return True, message_id, ""
    return False, message_id, f"{status}: {err}"


def send_flyer_processing_ack(
    chat_id: str,
    project_id: str,
    *,
    action_context: Optional[ActionExecutionContext],
) -> tuple[bool, str, str]:
    """Immediately acknowledge a complete flyer request before image generation.

    PR-ζ.1b 2026-05-26 — `action_context` keyword-only.
    """
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    message = (
        "Flyer Studio\n"
        "------------\n"
        "Got it. I'm creating your flyer now and will send a preview here shortly. "
        "Flyer generation usually takes 5-6 minutes."
    )
    ok, message_id, err, status = bridge_post(
        chat_id, message, action_context=action_context,
    )
    if ok:
        return True, message_id, ""
    return False, message_id, f"{status}: {err}"


def trigger_generate_flyer_concepts(project_id: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [str(PYTHON_BIN), "/usr/local/bin/generate-flyer-concepts", "--project-id", project_id],
            capture_output=True, text=True, timeout=FLYER_RENDER_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"exit={result.returncode} {detail[:500]}"
    return True, detail[:500]


def _record_flyer_concept_preview_delivery(project_id: str, asset_id: str, outbound_message_id: str) -> None:
    """Persist concept-preview media delivery metadata after bridge success."""
    _ensure_platform_path()
    try:
        from safe_io import FileLock, atomic_write_text  # type: ignore
    except Exception as e:
        raise RuntimeError(f"safe_io_import_failed: {type(e).__name__}: {e}") from e

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with FileLock(Path(str(FLYER_PROJECTS_PATH) + ".lock")):
        store = json.loads(FLYER_PROJECTS_PATH.read_text(encoding="utf-8"))
        projects = store.get("projects") if isinstance(store, dict) else None
        if not isinstance(projects, list):
            raise RuntimeError("project_store_shape_invalid")
        for project in projects:
            if not isinstance(project, dict) or project.get("project_id") != project_id:
                continue
            for asset in project.get("assets", []):
                if not isinstance(asset, dict) or asset.get("asset_id") != asset_id:
                    continue
                try:
                    attempt_count = int(asset.get("delivery_attempt_count") or 0)
                except (TypeError, ValueError):
                    attempt_count = 0
                asset["delivery_status"] = "sent"
                asset["outbound_message_id"] = outbound_message_id
                asset["delivered_at"] = now
                asset["delivery_attempt_count"] = attempt_count + 1
                asset["delivery_error"] = ""
                project["updated_at"] = now
                atomic_write_text(FLYER_PROJECTS_PATH, json.dumps(store, indent=2, ensure_ascii=False))
                return
            raise RuntimeError(f"asset_not_found: {asset_id}")
        raise RuntimeError(f"project_not_found: {project_id}")


def _record_flyer_preview_message_ids(project_id: str, mids: list[str]) -> None:
    """Append preview-batch outbound mids to the project's quoted-binding index.

    The per-concept media mids are already delivery state on the asset rows
    (_record_flyer_concept_preview_delivery); this list additionally captures
    the trailing APPROVE-CTA text mid — the message customers most often
    swipe-reply APPROVE on. Best-effort index, NOT delivery state: callers
    swallow failures so a persist problem never fails a preview delivery that
    already reached the customer. Dedupes, keeps newest 10 (schema cap)."""
    cleaned: list[str] = []
    for mid in mids or []:
        if isinstance(mid, str) and mid.strip() and mid.strip() not in cleaned:
            cleaned.append(mid.strip())
    if not cleaned:
        return
    _ensure_platform_path()
    from safe_io import FileLock, atomic_write_text  # type: ignore
    with FileLock(Path(str(FLYER_PROJECTS_PATH) + ".lock")):
        store = json.loads(FLYER_PROJECTS_PATH.read_text(encoding="utf-8"))
        projects = store.get("projects") if isinstance(store, dict) else None
        if not isinstance(projects, list):
            raise RuntimeError("project_store_shape_invalid")
        for project in projects:
            if not isinstance(project, dict) or project.get("project_id") != project_id:
                continue
            existing = [
                mid for mid in (project.get("preview_message_ids") or [])
                if isinstance(mid, str) and mid.strip()
            ]
            merged = existing + [mid for mid in cleaned if mid not in existing]
            project["preview_message_ids"] = merged[-10:]
            atomic_write_text(FLYER_PROJECTS_PATH, json.dumps(store, indent=2, ensure_ascii=False))
            return
        raise RuntimeError(f"project_not_found: {project_id}")


def _load_flyer_project_dict(project_id: str) -> Optional[dict]:
    """Load a single flyer project from the projects.json store as a dict.
    Returns None if the store is unreadable or the project_id is absent.
    Shared by send_flyer_concept_previews / send_warn_tier_concept_previews /
    _dispatch_concept_preview_send (P0 #2 Commit 4)."""
    try:
        store = json.loads(FLYER_PROJECTS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    for p in store.get("projects", []):
        if p.get("project_id") == project_id:
            return p
    return None


def _send_concept_preview_media(
    chat_id: str,
    project: dict,
    qa_policy: str,
    customer_text: Optional[str] = None,
) -> tuple[bool, str, str]:
    """Canonical concept-preview send. Extracted from send_flyer_concept_previews
    so warn-tier delivery can reuse the same per-concept loop with a relaxed
    visual-QA-report gate (P0 #2 Commit 4 — X2 helper-extraction architecture).

    qa_policy semantics:
    - "strict" — text-manifest QA + visual-QA-report both gate the send.
      Pass-tier wrapper uses this; pre-PR behavior preserved bit-for-bit.
    - "warn_tolerant" — text-manifest QA still gates (substrate correctness;
      template-parse failures aren't warn-tier-recoverable), but visual-QA-
      report `status != "passed"` no longer hard-fails. Warn-tier wrapper
      uses this. The upstream classifier (classify_qa_severity) already
      determined warn-tier is acceptable; project.warning captures the
      visible blockers for audit.

    customer_text override (Pin C — design §5 Commit 4):
    - Replaces the trailing CTA at end of the previews send.
    - Does NOT replace per-concept captions — those stay stable semantic
      descriptors ("C1: Title\\n...").
    - None → existing pass-tier APPROVE CTA (6 callers preserved bit-for-bit).

    Hermes-as-brain: this is a worker. The strict vs warn_tolerant choice
    lives one level up in the wrappers; this helper just executes it."""
    _ensure_platform_path()
    try:
        from safe_io import bridge_post, bridge_send_media  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    try:
        from flyer_render import validate_text_manifest_file  # type: ignore
    except Exception as e:
        return False, "", f"flyer_render_import_failed: {type(e).__name__}: {e}"
    try:
        from flyer_visual_qa import validate_visual_qa_report  # type: ignore
    except Exception:
        try:
            _ensure_local_src_path()
            from agents.flyer.visual_qa import validate_visual_qa_report  # type: ignore
        except Exception as e:
            return False, "", f"flyer_visual_qa_import_failed: {type(e).__name__}: {e}"

    project_id = str(project.get("project_id") or "")
    assets = {asset.get("asset_id"): asset for asset in project.get("assets", [])}
    outbound_ids: list[str] = []
    for concept in project.get("concepts", []):
        asset = assets.get(concept.get("preview_asset_id"))
        if not asset:
            continue
        # Text-manifest QA — ALWAYS strict in both policies (substrate gate;
        # template-parse failures aren't warn-tier-recoverable)
        qa = validate_text_manifest_file(
            asset.get("path", ""),
            project_id=project_id,
            project_version=project.get("version"),
            output_format="concept_preview",
        )
        if not qa.ok:
            return False, "", "text_qa_failed: " + "; ".join(qa.blockers)
        # Visual-QA-report gate — strict in pass-tier; warn-tolerant skips it
        visual = validate_visual_qa_report(
            asset.get("path", ""),
            project_id=project_id,
            project_version=int(project.get("version") or 1),
            output_format="concept_preview",
            allow_sidecar=False,
        )
        if not visual.ok and qa_policy == "strict":
            return False, "", "visual_qa_failed: " + "; ".join(visual.blockers)
        # warn_tolerant: proceed; project.warning already captures blockers
        caption = (
            f"{concept.get('concept_id')}: {concept.get('title')}\n"
            f"{concept.get('style_summary')}\n\n"
            "Reply APPROVE or reply with changes."
        )
        # PR-ζ.1b §2.3 — concept-preview media + CTA contexts.
        try:
            from agents.flyer.action_registry import (  # type: ignore
                PROJECT_ACTIONS, build_action_context_for_command,
            )
        except ImportError:  # pragma: no cover - deployed flat-module fallback
            from flyer_action_registry import (  # type: ignore
                PROJECT_ACTIONS, build_action_context_for_command,
            )
        ok, mid, err, bridge_status = bridge_send_media(
            chat_id, asset.get("path", ""), caption=caption,
            action_context=build_action_context_for_command(
                PROJECT_ACTIONS, "concept_preview.media_send",
            ),
        )
        if not ok:
            if bridge_status == "send_uncertain":
                return False, ",".join(outbound_ids), f"partial_delivery_uncertain: {bridge_status}: {err}"
            return False, "", f"{bridge_status}: {err}"
        try:
            _record_flyer_concept_preview_delivery(project_id, str(asset.get("asset_id") or ""), mid)
        except Exception as e:
            return False, ",".join(outbound_ids + [mid]), f"delivery_persist_failed: {type(e).__name__}: {e}"
        outbound_ids.append(mid)
    if not outbound_ids:
        return False, "", "no concept previews to send"
    try:
        from agents.flyer.action_registry import (  # type: ignore
            PROJECT_ACTIONS as _PA_CTA, build_action_context_for_command as _bac_cta,
        )
    except ImportError:  # pragma: no cover - deployed flat-module fallback
        from flyer_action_registry import (  # type: ignore
            PROJECT_ACTIONS as _PA_CTA, build_action_context_for_command as _bac_cta,
        )
    # Pin C — customer_text override replaces ONLY the trailing CTA.
    approval_cta = "Reply APPROVE to receive final files, or reply with changes."
    if customer_text is None:
        try:
            from agents.flyer.customer_copy_policy import build_preview_approval_checklist  # type: ignore
        except ImportError:  # pragma: no cover - deployed flat-module fallback
            from flyer_customer_copy_policy import build_preview_approval_checklist  # type: ignore
        checklist = build_preview_approval_checklist(project)
        cta_text = f"{checklist}\n\n{approval_cta}" if checklist else approval_cta
    else:
        cta_text = customer_text
    ok, mid, err, bridge_status = bridge_post(
        chat_id, cta_text,
        action_context=_bac_cta(_PA_CTA, "concept_preview.cta_text"),
    )
    if ok:
        outbound_ids.append(mid)
    else:
        return False, ",".join(outbound_ids), f"partial_delivery: {bridge_status}: {err}"
    try:
        _record_flyer_preview_message_ids(project_id, outbound_ids)
    except Exception as e:  # noqa: BLE001 - binding index is best-effort; delivery already succeeded
        print(f"cf-router preview-mid index persist failed (non-fatal): {e}", file=sys.stderr)
    return True, ",".join(outbound_ids), ""


def send_flyer_concept_previews(chat_id: str, project_id: str) -> tuple[bool, str, str]:
    """Pass-tier concept-preview send. Signature unchanged from pre-PR
    (6 existing callers in hooks.py preserved bit-for-bit). Wraps
    _send_concept_preview_media with qa_policy='strict'."""
    project = _load_flyer_project_dict(project_id)
    if project is None:
        return False, "", f"project_not_found: {project_id}"
    return _send_concept_preview_media(chat_id, project, qa_policy="strict")


def send_warn_tier_concept_previews(
    chat_id: str,
    project_id: str,
    customer_text: str,
) -> tuple[bool, str, str]:
    """Warn-tier concept-preview send. Reachable only via
    _dispatch_concept_preview_send. Relaxes visual-QA-report status check;
    keeps text-manifest QA strict.

    customer_text is REQUIRED (no default) — warn-tier delivery always
    needs correction-prompt copy, never the pass-tier APPROVE CTA. The
    asymmetric signature (vs send_flyer_concept_previews) prevents
    accidental empty-customer-text on warn-tier."""
    project = _load_flyer_project_dict(project_id)
    if project is None:
        return False, "", f"project_not_found: {project_id}"
    return _send_concept_preview_media(
        chat_id, project, qa_policy="warn_tolerant", customer_text=customer_text,
    )


def _dispatch_concept_preview_send(chat_id: str, project_id: str) -> tuple[bool, str, str]:
    """Single point of change for the warn-tier branch. Reads project state,
    picks the right wrapper. Replaces the 6 direct callers of
    send_flyer_concept_previews at hooks.py:746, 1848, 2795, 3081, 3310, 3486.

    Hermes-as-brain compliance: this dispatcher only reads `project.status`
    and `project.warning`. No re-classification, no policy decisions beyond
    `status → wrapper`."""
    project = _load_flyer_project_dict(project_id)
    if (
        project is not None
        and project.get("status") == "delivered_with_warning"
        and project.get("warning") is not None
    ):
        warning = project["warning"]
        try:
            from agents.flyer.customer_copy_policy import build_warn_tier_customer_text  # type: ignore
        except ImportError:  # pragma: no cover - deployed flat-module fallback
            from flyer_customer_copy_policy import build_warn_tier_customer_text  # type: ignore
        warn_text = build_warn_tier_customer_text(
            list(warning.get("blockers") or []), project,
        )
        return send_warn_tier_concept_previews(chat_id, project_id, warn_text)
    return send_flyer_concept_previews(chat_id, project_id)


def _flyer_outbound_dedupe_key(chat_id: str, message: str) -> str:
    normalized_message = "\n".join((message or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()
    raw = f"{chat_id}\0{normalized_message}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


def _load_flyer_outbound_dedupe(now: float) -> dict[str, dict[str, object]]:
    try:
        if not FLYER_OUTBOUND_DEDUPE_PATH.exists():
            return {}
        data = json.loads(FLYER_OUTBOUND_DEDUPE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}
    cutoff = now - FLYER_OUTBOUND_DEDUPE_TTL_SEC
    pruned: dict[str, dict[str, object]] = {}
    for key, entry in entries.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        try:
            sent_at = float(entry.get("ts", 0))
        except (TypeError, ValueError):
            continue
        if sent_at >= cutoff:
            pruned[key] = entry
    return pruned


def _write_flyer_outbound_dedupe(entries: dict[str, dict[str, object]]) -> None:
    try:
        if len(entries) > FLYER_OUTBOUND_DEDUPE_MAX:
            entries = dict(
                sorted(
                    entries.items(),
                    key=lambda item: float(item[1].get("ts", 0)) if isinstance(item[1], dict) else 0,
                    reverse=True,
                )[:FLYER_OUTBOUND_DEDUPE_MAX]
            )
        _atomic_write_dedupe_json(FLYER_OUTBOUND_DEDUPE_PATH, {"version": 1, "entries": entries})
    except Exception:
        return


def _write_dedupe_file(path: Path, entries: dict[str, dict[str, object]], max_entries: int) -> None:
    if len(entries) > max_entries:
        entries = dict(
            sorted(
                entries.items(),
                key=lambda item: float(item[1].get("ts", 0)) if isinstance(item[1], dict) else 0,
                reverse=True,
            )[:max_entries]
        )
    _atomic_write_dedupe_json(path, {"version": 1, "entries": entries})


def _atomic_write_dedupe_json(path: Path, doc: dict) -> None:
    try:
        _ensure_platform_path()
        from safe_io import atomic_write_text  # type: ignore
    except Exception:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp_path.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, path)
        return
    atomic_write_text(path, json.dumps(doc, sort_keys=True))


@contextmanager
def _dedupe_file_lock(path: Path) -> Iterator[None]:
    try:
        _ensure_platform_path()
        from safe_io import FileLock  # type: ignore
    except Exception:
        yield
        return
    with FileLock(Path(str(path) + ".lock")):
        yield


def _cf_router_inbound_dedupe_key(chat_id: str, message_id: str, text: str) -> str:
    message_key = message_id.strip() if message_id else ""
    if not message_key:
        normalized = "\n".join((text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()
        message_key = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()
    raw = f"{chat_id}\0{message_key}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


def mark_cf_router_inbound_seen(chat_id: str, message_id: str, text: str, *, now: Optional[float] = None) -> bool:
    """Return True when this inbound event was already processed recently.

    The guard is intentionally stateful across gateway restarts. WhatsApp/Hermes
    can replay an already-handled upsert after a restart or resume; processing
    the same event again is exactly how a single inbound turns into repeated
    customer-visible replies.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and CF_ROUTER_INBOUND_DEDUPE_PATH == _DEFAULT_CF_ROUTER_INBOUND_DEDUPE_PATH:
        return False
    ts = time.time() if now is None else now
    key = _cf_router_inbound_dedupe_key(chat_id, message_id, text)
    try:
        with _dedupe_file_lock(CF_ROUTER_INBOUND_DEDUPE_PATH):
            if CF_ROUTER_INBOUND_DEDUPE_PATH.exists():
                data = json.loads(CF_ROUTER_INBOUND_DEDUPE_PATH.read_text(encoding="utf-8"))
                entries = data.get("entries") if isinstance(data, dict) else {}
            else:
                entries = {}
            if not isinstance(entries, dict):
                entries = {}
            cutoff = ts - CF_ROUTER_INBOUND_DEDUPE_TTL_SEC
            pruned: dict[str, dict[str, object]] = {}
            for existing_key, entry in entries.items():
                if not isinstance(existing_key, str) or not isinstance(entry, dict):
                    continue
                try:
                    seen_at = float(entry.get("ts", 0))
                except (TypeError, ValueError):
                    continue
                if seen_at >= cutoff:
                    pruned[existing_key] = entry
            if key in pruned:
                return True
            pruned[key] = {"ts": ts, "chat_id": chat_id, "message_id": message_id}
            _write_dedupe_file(CF_ROUTER_INBOUND_DEDUPE_PATH, pruned, CF_ROUTER_INBOUND_DEDUPE_MAX)
    except Exception:
        return False
    return False


def send_flyer_text(
    chat_id: str,
    message: str,
    *,
    action_context: ActionExecutionContext,
    allow_duplicate: bool = False,
) -> tuple[bool, str, str]:
    """Send a customer-facing Flyer Studio text reply via the bridge chokepoint.

    PR-ζ.1b 2026-05-26 (commit 9): `action_context` is REQUIRED keyword-only —
    the `= None` default landed in PR-ζ F8 has been removed now that every
    cf-router callsite passes an explicit ActionExecutionContext. The
    chokepoint runs PR-γ's lint_no_unverified_completion on regulated
    messages.
    """
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    now = time.time()
    dedupe_key = _flyer_outbound_dedupe_key(chat_id, message)
    with _dedupe_file_lock(FLYER_OUTBOUND_DEDUPE_PATH):
        dedupe_entries = _load_flyer_outbound_dedupe(now)
        if not allow_duplicate:
            existing = dedupe_entries.get(dedupe_key)
            if existing:
                mid = str(existing.get("mid") or "recent")
                return True, f"deduped:{mid}", ""
        ok, mid, err, status = bridge_post(chat_id, message, action_context=action_context)
        if ok:
            dedupe_entries[dedupe_key] = {"ts": now, "mid": mid}
            _write_flyer_outbound_dedupe(dedupe_entries)
            return True, mid, ""
    return False, mid, f"{status}: {err}"


def invoke_update_flyer_project(project_id: str, *args: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [str(PYTHON_BIN), "/usr/local/bin/update-flyer-project", "--project-id", project_id, *args],
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, f"exit={result.returncode} {detail[:500]}"
    return True, detail[:500]


def finalize_and_send_flyer(chat_id: str, project_id: str, message_id: str) -> tuple[bool, str]:
    steps = [
        [str(PYTHON_BIN), "/usr/local/bin/update-flyer-project", "--project-id", project_id, "--approve-message-id", message_id],
        [str(PYTHON_BIN), "/usr/local/bin/finalize-flyer-assets", "--project-id", project_id, "--approved-message-id", message_id],
        [str(PYTHON_BIN), "/usr/local/bin/send-flyer-package", "--jid", chat_id, "--project-id", project_id],
    ]
    details: list[str] = []
    for cmd in steps:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=FLYER_RENDER_TIMEOUT_SEC)
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"{cmd[0]}: {type(e).__name__}: {e}"
        detail = (result.stdout or result.stderr or "").strip()
        details.append(detail[:200])
        if result.returncode != 0:
            return False, f"{cmd[0]} exit={result.returncode}: {detail[:500]}"
    return True, " | ".join(details)


def retry_send_flyer_package(chat_id: str, project_id: str, message_id: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [str(PYTHON_BIN), "/usr/local/bin/send-flyer-package", "--jid", chat_id, "--project-id", project_id],
            capture_output=True, text=True, timeout=FLYER_RENDER_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"send-flyer-package: {type(e).__name__}: {e}"
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        if "project must be finalizing_assets before delivery, got delivered" in detail and _flyer_project_delivery_complete(project_id):
            return True, f"already_delivered=true; project_id={project_id}; message_id={message_id}"
        return False, f"send-flyer-package exit={result.returncode}: {detail[:500]}"
    return True, detail[:500] or f"delivery retried for {project_id}; message_id={message_id}"


def _flyer_project_delivery_complete(project_id: str) -> bool:
    for row in _load_flyer_projects():
        if not isinstance(row, dict) or row.get("project_id") != project_id:
            continue
        if row.get("status") != "delivered":
            return False
        final_ids = [str(asset_id) for asset_id in (row.get("final_asset_ids") or []) if str(asset_id)]
        if not final_ids:
            return False
        assets_by_id = {
            str(asset.get("asset_id") or ""): asset
            for asset in (row.get("assets") or [])
            if isinstance(asset, dict)
        }
        for asset_id in final_ids:
            asset = assets_by_id.get(asset_id)
            if not asset:
                return False
            if asset.get("delivery_status") != "sent":
                return False
            if not str(asset.get("outbound_message_id") or ""):
                return False
        return True
    return False


def find_dispatcher_routed_for(chat_id: str, since_ts: float) -> bool:
    """Check decisions.log for a `dispatcher_routed` entry matching chat_id
    within the rescue window [since_ts - LOOKBACK, since_ts + WATCHDOG + LOOKBACK].

    Mirrors the deployed F7 daemon's same-name function with three reviewer-
    requested hardenings:
      1. Upper-bound timestamp guard (M7) — rejects future-dated rows that
         would silently suppress real rescues. Without this, a manually
         crafted or clock-drifted row arbitrarily in the future would
         match.
      2. File-size snapshot (H2) — bounds the read to bytes that existed
         at scan-start. safe_io.ndjson_append doesn't acquire a read lock,
         so concurrent appends could otherwise produce torn-line reads
         that yield false negatives.
      3. Reverse-iterate from end-of-file (M10) — old daemon scanned from
         line 1 every call; on a multi-MB log this is O(N) per rescue.
         The relevant entries are always recent (within ~30s), so we read
         the last ~256 KiB in chronological order. Bounded by file size.

    Pure file read + JSON parse — safe to call from a Timer thread.
    """
    if not LOG_PATH.exists():
        return False
    chat_lid_only = chat_id.split("@", 1)[0] if "@" in chat_id else chat_id
    upper_bound_ts = since_ts + F7_WATCHDOG_TIMEOUT_SEC + F7_DISPATCHER_LOOKBACK_SEC
    lower_bound_ts = since_ts - F7_DISPATCHER_LOOKBACK_SEC
    # Read window: capped at 256 KiB from end-of-file, plenty for ~30s
    # of typical traffic and small enough to keep Timer-thread time bounded.
    READ_WINDOW_BYTES = 256 * 1024
    try:
        size = LOG_PATH.stat().st_size
        # H2 — pin the snapshot. Concurrent appends after this point are
        # ignored, so we never read a partial line at the tail.
        if size == 0:
            return False
        start = max(0, size - READ_WINDOW_BYTES)
        with LOG_PATH.open("rb") as f:
            f.seek(start)
            buf = f.read(size - start)
        # Drop the leading partial line if we did a mid-file seek (it may
        # have started inside a previous record).
        if start > 0:
            nl = buf.find(b"\n")
            if nl >= 0:
                buf = buf[nl + 1:]
        for raw_line in buf.split(b"\n"):
            line = raw_line.strip()
            if not line or b"dispatcher_routed" not in line:
                continue
            try:
                entry = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "dispatcher_routed":
                continue
            ts_str = entry.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except (ValueError, AttributeError):
                continue
            if ts < lower_bound_ts:
                continue
            if ts > upper_bound_ts:
                # M7 — silently-future row; reject as out of window
                continue
            sender_lid = entry.get("sender_lid") or ""
            sender_phone = entry.get("sender_phone") or ""
            if chat_lid_only in sender_lid or chat_lid_only in (sender_phone or "").lstrip("+"):
                return True
    except OSError:
        pass
    return False


def lid_to_phone_via_identify_sender(lid_or_jid: str) -> tuple[Optional[str], str]:
    """Resolve a LID/JID to (phone_E164, role) via identify-sender subprocess.

    Duplicates the deployed F7 daemon's helper rather than extending
    is_owner_chat / is_employee_chat (which return bool); changing those
    return types would risk PR-CF6's existing 31 tests.
    """
    try:
        result = subprocess.run(
            [str(IDENTIFY_SENDER_BIN), lid_or_jid],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None, "unknown"
        doc = json.loads(result.stdout)
        return doc.get("phone_normalized"), doc.get("role", "unknown")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None, "unknown"


BARE_FLYER_RECENT_DIR = Path("/opt/shift-agent/state/bare_flyer/recent")


def _sanitize_bare_chat(chat_id: str) -> str:
    # MUST match bare-flyer-render-and-send's _sanitize_chat so the marker is found.
    return re.sub(r"[^A-Za-z0-9_.@-]", "_", chat_id or "")[:80]


def recent_bare_flyer_for_chat(chat_id: str, *, within_hours: float = 6.0) -> bool:
    """True if a bare flyer was delivered to this chat within the window. The bare trunk is
    stateless (no projects.json row), so it drops a per-chat marker on send; this is the
    flyer-context gate that lets a follow-up revision route to the bare trunk WITHOUT hijacking
    catering/other messages (which have no such marker)."""
    try:
        marker = BARE_FLYER_RECENT_DIR / f"{_sanitize_bare_chat(chat_id)}.json"
        if not marker.exists():
            return False
        doc = json.loads(marker.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(doc.get("sent_at") or ""))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() <= within_hours * 3600.0
    except Exception:
        return False


def spawn_bare_flyer_render_and_send(chat_id: str, text: str, message_id: Optional[str] = None,
                                     is_revision: bool = False, is_revision_apply: bool = False) -> bool:
    """Detached async render+send for the bare-flyer path (Approach B).

    cf-router calls this and returns immediately so the gateway never blocks on
    the ~20-30s render. The spawned script sends an ack, renders one OpenRouter
    flyer image, and delivers it via bridge_send_media (or an honest error).
    - is_revision: a broad revision/feedback follow-up -> the script replies deterministically
      ("resend full details") instead of looping in the toolless LLM.
    - is_revision_apply: the supported uniform-price-header edit -> the script applies it to the
      persisted session and re-overlays (slice 2c). When the apply feature flag is off, the script
      degrades to the same "resend full details" reply, so this is safe to route before 2c ships."""
    cmd = [str(BARE_FLYER_SEND_BIN), "--chat-id", chat_id, "--brief", text[:2000]]
    if message_id:
        cmd += ["--message-id", message_id]
    if is_revision_apply:
        cmd += ["--revision-apply"]
    elif is_revision:
        cmd += ["--revision"]
    try:
        phone, _role = lid_to_phone_via_identify_sender(chat_id)
        if phone:
            cmd += ["--sender-phone", phone]
    except Exception:
        pass
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except (OSError, ValueError) as e:
        print("cf-router: bare_flyer spawn failed:", type(e).__name__, e, file=sys.stderr)
        return False


def trigger_create_catering_lead(
    customer_phone: str, customer_name: str, raw_inquiry: str, message_id: str,
    extracted_fields: Optional[dict] = None,
) -> tuple[bool, str]:
    """Invoke create-catering-lead.

    `extracted_fields` (PR-CF1d Commit 4 2026-05-12): optional dict to merge
    into the default all-null fields_json. Used by F7 primary-mode to forward
    classify_catering's headcount signal (and any future signal extraction)
    so the persisted lead carries structured data the owner can see in the
    approval card and daily brief, instead of every cf-router-created lead
    having headcount=null. Closes the UX-regression flagged at PR review:
    rule-following is preserved, but the regex IS already extracting
    headcount as a side-effect — passing it forward is plumbing, not new
    extraction logic.

    Defaults preserve the prior rescue-path behavior (all None / empty);
    callers from outside F7 primary-mode (e.g. legacy rescue path tests)
    don't need to pass extracted_fields.

    Returns (success, detail). Idempotency on (customer_phone, message_id)
    is enforced by create-catering-lead itself (existing behavior).
    """
    fields: dict = {
        "headcount": None,
        "event_date": None,
        "event_time": None,
        "menu_preferences": [],
        "off_menu_items": [],
        "dietary_restrictions": [],
        "delivery_or_pickup": "unknown",
        "budget_hint_usd": None,
        "notes": "(cf-router F7 rescue from missed-dispatch; LLM bypassed parse_catering_inquiry SKILL)",
    }
    if extracted_fields:
        fields.update(extracted_fields)
    fields_json = json.dumps(fields)
    try:
        result = subprocess.run(
            [
                str(CREATE_LEAD_BIN),
                "--customer-phone", customer_phone,
                "--customer-name", customer_name,
                "--raw-inquiry", raw_inquiry[:1000],
                "--message-id", message_id,
                "--fields-json", fields_json,
            ],
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, f"exit={result.returncode} stderr={result.stderr[:500]}"
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def audit_dispatcher_watchdog_fired(*, chat_id: str, message_id: str,
                                     customer_phone: str, signals: list[str],
                                     success: bool, detail: str = "") -> None:
    """Emit `catering_dispatcher_watchdog_fired` row via safe_io chokepoint.
    Same shape as the deployed F7 daemon — observability tooling unchanged.
    """
    try:
        _ensure_platform_path()
        from safe_io import ndjson_append  # type: ignore
        from schemas import CateringDispatcherWatchdogFired  # type: ignore
        entry = CateringDispatcherWatchdogFired(
            type="catering_dispatcher_watchdog_fired",
            ts=datetime.now(timezone.utc),
            chat_id=chat_id,
            message_id=message_id,
            customer_phone=customer_phone,
            signals=signals,
            success=success,
            detail=detail[:2000],
        )
        ndjson_append(LOG_PATH, entry.model_dump_json())
    except Exception as e:
        sys.stderr.write(f"cf-router F7: fired-audit emit failed (non-fatal): {e}\n")


def audit_dispatcher_watchdog_suppressed(*, chat_id: str, message_id: str,
                                          reason: str, detail: str = "") -> None:
    """Emit `catering_dispatcher_watchdog_suppressed` row.

    `reason` MUST be one of the schema's Literal values. After PR-CF7 only
    `non_customer_role` and `lid_no_phone_resolution` are emitted by the
    plugin (`text_unavailable` and `not_catering` are unreachable from
    the plugin code path — see plan §"Audit-row reachability").
    """
    try:
        _ensure_platform_path()
        from safe_io import ndjson_append  # type: ignore
        from schemas import CateringDispatcherWatchdogSuppressed  # type: ignore
        entry = CateringDispatcherWatchdogSuppressed(
            type="catering_dispatcher_watchdog_suppressed",
            ts=datetime.now(timezone.utc),
            chat_id=chat_id,
            message_id=message_id,
            reason=reason,  # type: ignore
            detail=detail[:2000],
        )
        ndjson_append(LOG_PATH, entry.model_dump_json())
    except Exception as e:
        sys.stderr.write(f"cf-router F7: suppressed-audit emit failed (non-fatal): {e}\n")


def f7_rescue_check(text: str, chat_id: str, message_id: str,
                     signals: list[str], ts_at_schedule: float) -> None:
    """Background-thread callback fired ~30s after pre_gateway_dispatch.

    Mirrors process_inbound() in the deployed F7 daemon, minus the
    text-availability check (plugin has text directly from the event).

    Decision tree:
      1. Did the LLM dispatch correctly? Audit-log scan for dispatcher_routed
         within ts_at_schedule + grace → no rescue needed
      2. Resolve sender role via identify-sender. Owner → suppressed
      3. Phone resolution required → suppressed if missing
      4. Fire rescue: invoke create-catering-lead, audit fired

    Best-effort: failures are logged to stderr, never raised (this runs
    in a daemon thread; an exception would kill the thread silently).
    """
    try:
        # 1. LLM handled it?
        if find_dispatcher_routed_for(chat_id, ts_at_schedule):
            return  # SKILL ran successfully — no rescue needed

        # 2. Sender role check
        phone, role = lid_to_phone_via_identify_sender(chat_id)
        if role == "owner":
            audit_dispatcher_watchdog_suppressed(
                chat_id=chat_id, message_id=message_id,
                reason="non_customer_role", detail=f"role={role}",
            )
            return

        # 3. Phone resolution required
        if not phone:
            audit_dispatcher_watchdog_suppressed(
                chat_id=chat_id, message_id=message_id,
                reason="lid_no_phone_resolution",
                detail=f"signals={','.join(signals)} text_preview={text[:60]!r}",
            )
            return

        # 4. Fire rescue
        success, detail = trigger_create_catering_lead(
            customer_phone=phone, customer_name="",
            raw_inquiry=text, message_id=f"watchdog:{message_id}",
        )
        audit_dispatcher_watchdog_fired(
            chat_id=chat_id, message_id=message_id, customer_phone=phone,
            signals=signals, success=success, detail=detail[:2000],
        )
    except Exception as e:
        sys.stderr.write(f"cf-router F7: rescue check crashed (non-fatal): {e}\n")
