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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Deployed-system paths (mutable for tests)
CONFIG_PATH = Path("/opt/shift-agent/config.yaml")
LEADS_PATH = Path("/opt/shift-agent/state/catering-leads.json")
MENU_PENDING_PATH = Path("/opt/shift-agent/state/catering-menu-pending.json")
ROSTER_PATH = Path("/opt/shift-agent/roster.json")
LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")
THROTTLE_PATH = Path("/opt/shift-agent/state/cf-router-throttle.json")

APPLY_OWNER_DECISION_BIN = Path("/usr/local/bin/apply-catering-owner-decision")
APPLY_MENU_UPDATE_BIN = Path("/usr/local/bin/apply-menu-update")
NOTIFY_OWNER_BIN = Path("/usr/local/bin/shift-agent-notify-owner")
CREATE_LEAD_BIN = Path("/usr/local/bin/create-catering-lead")  # F7 path

PYTHON_BIN = Path("/usr/local/lib/hermes-agent/venv/bin/python")
PLATFORM_DIR = Path("/opt/shift-agent")  # Where schemas.py lives
IDENTIFY_SENDER_BIN = Path("/usr/local/bin/identify-sender")
SEND_CATERING_ACK_BIN = Path("/usr/local/bin/send-catering-ack")

SUBPROCESS_TIMEOUT_SEC = 30
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
      2. Resolve sender role via identify-sender. Owner/employee → suppressed
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
        if role in {"owner", "employee"}:
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
