"""cf-router subprocess + state helpers.

All file paths and command paths are deployed-system constants — the plugin
runs on the VPS, so /opt/shift-agent and /usr/local/bin are stable.

Test override: set the module-level path constants before invoking hooks
(see tests/test_cf_router_plugin.py for the pattern).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

# Deployed-system paths (mutable for tests)
CONFIG_PATH = Path("/opt/shift-agent/config.yaml")
LEADS_PATH = Path("/opt/shift-agent/state/catering-leads.json")
PROPOSALS_PATH = Path("/opt/shift-agent/state/catering-proposals.json")
MENU_PENDING_PATH = Path("/opt/shift-agent/state/catering-menu-pending.json")
FLYER_PROJECTS_PATH = Path("/opt/shift-agent/state/flyer/projects.json")
FLYER_CUSTOMERS_PATH = Path("/opt/shift-agent/state/flyer/customers.json")
FLYER_GUEST_ORDERS_PATH = Path("/opt/shift-agent/state/flyer/guest_orders.json")
FLYER_REFERENCE_SCOPE_PATH = Path("/opt/shift-agent/state/flyer/reference_scope_pending.json")
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
HANDLE_FLYER_ONBOARDING_BIN = Path("/usr/local/bin/handle-flyer-onboarding")
HANDLE_FLYER_INTAKE_BIN = Path("/usr/local/bin/handle-flyer-intake")
STORE_FLYER_BRAND_ASSET_BIN = Path("/usr/local/bin/store-flyer-brand-asset")
MANAGE_FLYER_ACCOUNT_BIN = Path("/usr/local/bin/manage-flyer-account")
MANAGE_FLYER_GUEST_ORDER_BIN = Path("/usr/local/bin/manage-flyer-guest-order")
CHECK_FLYER_REFERENCE_SCOPE_BIN = Path("/usr/local/bin/check-flyer-reference-scope")

PYTHON_BIN = Path("/usr/local/lib/hermes-agent/venv/bin/python")
PLATFORM_DIR = Path("/opt/shift-agent")  # Where schemas.py lives
IDENTIFY_SENDER_BIN = Path("/usr/local/bin/identify-sender")
SEND_CATERING_ACK_BIN = Path("/usr/local/bin/send-catering-ack")

SUBPROCESS_TIMEOUT_SEC = 30
FLYER_RENDER_TIMEOUT_SEC = 900
ALERT_THROTTLE_SEC = 300  # Suppress duplicate Pushover alerts within 5 min
F7_DISPATCHER_LOOKBACK_SEC = 5  # Grace window when scanning audit log
                                 # for dispatcher_routed (matches deployed F7
                                 # daemon's `since_ts - 5` clock-skew tolerance)


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


# === Audit ===

def audit_intercepted(reason: str, chat_id: str, code: Optional[str] = None,
                      subprocess_rc: Optional[int] = None, detail: str = "") -> None:
    """Emit a `cf_router_intercepted` audit row via the deployed
    safe_io.ndjson_append chokepoint.

    Best-effort: failures are logged to stderr; the plugin still returns
    its action so the gateway flow continues. The wrapping try/except is
    critical — if this raises, the outer plugin try/except converts a
    successful skip into a `None` (LLM re-runs after apply already fired).
    """
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
        )
        ndjson_append(LOG_PATH, entry.model_dump_json())
    except Exception as e:
        sys.stderr.write(f"cf-router: audit emit failed (non-fatal): {e}\n")


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
    r"menu options?|options?)\b",
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


def is_flyer_approval_text(text: str) -> bool:
    """Return True for the exact Flyer Studio final-approval reply."""
    body = " ".join(flyer_visible_message_text(text).split())
    return body.lower().strip(" .!,:;") == "approve"


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
    has_detail = (
        "$" in lower
        or ":" in body
        or bool(re.search(r"\b\d{1,2}\s*(?:am|pm)\b", lower))
        or bool(re.search(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|weekend|special|menu|sale|offer|discount|grand opening|class|event|seo|aeo|geo|paid ads|content creation)\b", lower))
    )
    if has_detail:
        return False
    return bool(re.search(r"\b(?:create|make|need|help|start|try|get)\b.*\b(?:flyer|flier|poster|marketing|flyer studio)\b", lower))


def extract_flyer_request_after_confirm(text: str) -> str:
    """Return a flyer brief trailing a compound onboarding CONFIRM reply."""
    body = flyer_visible_message_text(text)
    match = re.match(r"^\s*CONFIRM\b(?:\s*[\.:,;!\-]\s*|\s+)(.+?)\s*$", body, flags=re.IGNORECASE | re.DOTALL)
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
            return (
                "Flyer Studio\n"
                "------------\n"
                "Here is a starter flyer request.\n"
                "Edit anything below and send it back.\n\n"
                "Create a professional flyer for my business.\n\n"
                "Main heading:\nSpecial Offer\n\n"
                "Details:\nAdd what I am promoting, products or services, prices, dates, and contact details here.\n\n"
                "Use my saved business name, address, phone, and logo.\n\n"
                "Reply with your edited version, or replace it with your own flyer request."
            )
    return starter_brief_message(
        str(customer.get("business_category") or ""),
        business_name=str(customer.get("business_name") or ""),
    )


def flyer_customer_not_active_reply(customer: dict) -> str:
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
            "This Flyer Studio account is cancelled. Contact Support or restart setup before creating a new flyer."
        )
    return (
        "Flyer Studio\n"
        "------------\n"
        f"This Flyer Studio account is {status}. Contact Support before creating a new flyer."
    )


def flyer_project_missing_info_reply(project: dict) -> str:
    """Customer-facing prompt for an incomplete Flyer project."""
    project_id = str(project.get("project_id") or "this project")
    if flyer_project_needs_missing_reference(project):
        return (
            "Flyer Studio\n"
            "------------\n"
            f"I have {project_id}, but I need the sample/reference flyer before I can create the design.\n\n"
            "Please attach the flyer image/PDF, or type the items, offers, prices, date/time if needed, and contact details."
        )
    return (
        "Flyer Studio\n"
        "------------\n"
        f"I have {project_id}, but I need a few more details before creating the design.\n\n"
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


def find_active_flyer_project_by_sender(phone: Optional[str], chat_id: str) -> Optional[dict]:
    """Look up a non-terminal flyer project by sender phone.

    Flyer projects currently store canonical customer_phone. LID senders are
    mapped to phone by the caller via identify-sender before invoking this.
    """
    if not phone or not FLYER_PROJECTS_PATH.exists():
        return None
    terminal = {"completed"}
    try:
        account_phones = {_canonical_phone(phone) or phone}
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
        with FLYER_PROJECTS_PATH.open(encoding="utf-8") as f:
            store = json.load(f)
        projects = store.get("projects", [])
        if not isinstance(projects, list):
            return None
        matches = [
            row for row in projects
            if isinstance(row, dict)
            and row.get("customer_phone") in account_phones
            and row.get("status") not in terminal
        ]
        if not matches:
            return None
        return max(matches, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""))
    except Exception:
        return None


def has_non_delivered_flyer_project_by_sender(phone: Optional[str], chat_id: str) -> bool:
    project = find_active_flyer_project_by_sender(phone, chat_id)
    return bool(project and project.get("status") != "delivered")


def is_flyer_revision_intent(text: str) -> bool:
    body = flyer_visible_message_text(text).lower()
    return bool(re.search(
        r"\b(change|replace|swap|remove|exclude|add|fix|correct|problem|wrong|still|instead|not\s+in|looks?\s+great|design)\b",
        body,
        flags=re.IGNORECASE,
    ))


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


def find_flyer_customer_by_sender(phone: Optional[str], chat_id: str) -> Optional[dict]:
    """Return registered Flyer customer for this sender, if any."""
    canonical = _canonical_phone(phone)
    if not canonical and chat_id.endswith("@s.whatsapp.net"):
        canonical = _canonical_phone(chat_id.split("@", 1)[0])
    if not FLYER_CUSTOMERS_PATH.exists():
        return None
    try:
        store = json.loads(FLYER_CUSTOMERS_PATH.read_text(encoding="utf-8"))
        if not canonical and chat_id:
            matches = [
                customer for customer in store.get("customers", [])
                if isinstance(customer, dict) and customer.get("primary_chat_id") == chat_id
            ]
            return matches[0] if len(matches) == 1 else None
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
        return matches[0] if len(matches) == 1 else None
    except Exception:
        return None


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


_US_STATE_WORDS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "illinois": "IL", "indiana": "IN",
    "maryland": "MD", "michigan": "MI", "new jersey": "NJ", "new york": "NY",
    "north carolina": "NC", "ohio": "OH", "pennsylvania": "PA", "south carolina": "SC",
    "texas": "TX", "virginia": "VA", "washington": "WA",
}


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
        r"\b(?:for|at|in|location|branch|store)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})\b",
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


def is_flyer_account_command(text: str) -> bool:
    return bool(re.search(
        r"^\s*(status|plan status|help|add (authorized )?(number|auth)|add authorized number|"
        r"remove authorized number|remove number|update phone|update business phone|"
        r"update whatsapp|update business whatsapp|change plan|confirm update)\b",
        text or "",
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
        if reference_media_path:
            cmd.extend(["--reference-media-path", reference_media_path])
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
) -> None:
    """Remember that the last unrelated-reference reply is awaiting option 1/2."""
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
    )


def consume_flyer_reference_scope_choice(
    text: str,
    *,
    chat_id: str,
    sender_phone: str,
) -> Optional[dict]:
    """Return pending reference-scope choice for option 1/2 replies, consuming it."""
    choice = _reference_scope_choice(text)
    if not choice:
        return None
    with _reference_scope_state_lock():
        state = _read_reference_scope_state()
        pending = state.get("pending", [])
        matched: Optional[dict] = None
        remaining: list[dict] = []
        for item in pending:
            if str(item.get("status") or "awaiting_choice") != "awaiting_choice":
                remaining.append(item)
                continue
            same_chat = chat_id and item.get("chat_id") == chat_id
            same_phone = sender_phone and item.get("sender_phone") == sender_phone
            if matched is None and (same_chat or same_phone):
                matched = dict(item)
                continue
            remaining.append(item)
        if matched is None:
            if remaining != pending:
                _write_reference_scope_state({"schema_version": 1, "pending": remaining})
            return None
        _write_reference_scope_state({"schema_version": 1, "pending": remaining})
    matched["choice"] = choice
    return matched


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
    for item in pending:
        same_chat = chat_id and item.get("chat_id") == chat_id
        same_phone = sender_phone and item.get("sender_phone") == sender_phone
        is_auth_pending = str(item.get("status") or "") == "awaiting_authorization_details"
        if matched is None and is_auth_pending and (same_chat or same_phone):
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
    remaining.append(matched)
    _write_reference_scope_state({"schema_version": 1, "pending": remaining})
    matched["choice"] = "authorization_note_recorded"
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
    if _reference_scope_choice(body):
        return None

    with _reference_scope_state_lock():
        return _consume_flyer_reference_authorization_reply_locked(
            body,
            chat_id=chat_id,
            sender_phone=sender_phone,
        )


def send_flyer_manual_edit_ack(chat_id: str, project_id: str, request_text: str = "") -> tuple[bool, str, str]:
    """Acknowledge source-preserving flyer edits without auto-generating."""
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    body = flyer_visible_message_text(request_text).strip()
    requested = f"\n\nRequested edit: {body}" if body else ""
    message = (
        "Flyer Studio\n"
        "------------\n"
        f"I received your uploaded flyer and queued project {project_id} for a source-preserving edit."
        f"{requested}\n\n"
        "I will not auto-generate a new flyer from scratch for this kind of request. "
        "The correction should preserve the existing design and change only the requested text/artwork. "
        "I will send the updated flyer here once it is ready."
    )
    ok, message_id, err, status = bridge_post(chat_id, message)
    if ok:
        return True, message_id, ""
    return False, message_id, f"{status}: {err}"


def send_flyer_edit_processing_ack(chat_id: str, project_id: str) -> tuple[bool, str, str]:
    """Acknowledge source-preserving edit generation before the model call."""
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    message = (
        "Flyer Studio\n"
        "------------\n"
        f"I received your uploaded flyer as project {project_id} and am editing the source artwork now.\n\n"
        "This is a source-preserving edit, so I will keep the existing design and change only the requested text/artwork. "
        "It usually takes 5-6 minutes. I will send the edited preview here when it is ready.\n\n"
        "Reply here if you need to add another correction while I work on it."
    )
    ok, message_id, err, status = bridge_post(chat_id, message)
    if ok:
        return True, message_id, ""
    return False, message_id, f"{status}: {err}"


def send_flyer_intake_ack(chat_id: str, project_id: str) -> tuple[bool, str, str]:
    """Send the deterministic Flyer Studio intake acknowledgement."""
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    message = (
        "Flyer Studio\n"
        "------------\n"
        f"Got it. I created flyer project {project_id}. "
        "I have the request and will prepare design concepts. "
        "Reply here with a logo or photos if you want them included."
    )
    ok, message_id, err, status = bridge_post(chat_id, message)
    if ok:
        return True, message_id, ""
    return False, message_id, f"{status}: {err}"


def send_flyer_processing_ack(chat_id: str, project_id: str) -> tuple[bool, str, str]:
    """Immediately acknowledge a complete flyer request before image generation."""
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    message = (
        "Flyer Studio\n"
        "------------\n"
        f"Request processing. I created flyer project {project_id} and am creating the design now.\n\n"
        "Flyer generation is in progress and usually takes 5-6 minutes. "
        "Please check back here shortly; I will send the preview as soon as it is ready.\n\n"
        "Reply here if you need to add a logo, photos, or changes while I work on it."
    )
    ok, message_id, err, status = bridge_post(chat_id, message)
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


def send_flyer_concept_previews(chat_id: str, project_id: str) -> tuple[bool, str, str]:
    """Send the generated concept preview and approval instructions."""
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
        store = json.loads(FLYER_PROJECTS_PATH.read_text(encoding="utf-8"))
        project = next((p for p in store.get("projects", []) if p.get("project_id") == project_id), None)
    except Exception as e:
        return False, "", f"project_load_failed: {type(e).__name__}: {e}"
    if not project:
        return False, "", f"project_not_found: {project_id}"
    assets = {asset.get("asset_id"): asset for asset in project.get("assets", [])}
    outbound_ids: list[str] = []
    for concept in project.get("concepts", []):
        asset = assets.get(concept.get("preview_asset_id"))
        if not asset:
            continue
        qa = validate_text_manifest_file(
            asset.get("path", ""),
            project_id=project_id,
            project_version=project.get("version"),
            output_format="concept_preview",
        )
        if not qa.ok:
            return False, "", "text_qa_failed: " + "; ".join(qa.blockers)
        caption = (
            f"{concept.get('concept_id')}: {concept.get('title')}\n"
            f"{concept.get('style_summary')}\n\n"
            "Reply APPROVE or reply with changes."
        )
        ok, mid, err, status = bridge_send_media(chat_id, asset.get("path", ""), caption=caption)
        if not ok:
            if status == "send_uncertain":
                return False, ",".join(outbound_ids), f"partial_delivery_uncertain: {status}: {err}"
            return False, "", f"{status}: {err}"
        outbound_ids.append(mid)
    if not outbound_ids:
        return False, "", "no concept previews to send"
    ok, mid, err, status = bridge_post(
        chat_id,
        "Reply APPROVE to receive final files, or reply with changes.",
    )
    if ok:
        outbound_ids.append(mid)
    else:
        return False, ",".join(outbound_ids), f"partial_delivery: {status}: {err}"
    return True, ",".join(outbound_ids), ""


def send_flyer_text(chat_id: str, message: str) -> tuple[bool, str, str]:
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    ok, mid, err, status = bridge_post(chat_id, message)
    if ok:
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
