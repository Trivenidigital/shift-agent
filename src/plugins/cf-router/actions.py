"""cf-router subprocess + state helpers.

All file paths and command paths are deployed-system constants — the plugin
runs on the VPS, so /opt/shift-agent and /usr/local/bin are stable.

Test override: set the module-level path constants before invoking hooks
(see tests/test_cf_router_plugin.py for the pattern).
"""
from __future__ import annotations

import json
import os
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

PYTHON_BIN = Path("/usr/local/lib/hermes-agent/venv/bin/python")
PLATFORM_DIR = Path("/opt/shift-agent")  # Where schemas.py lives
IDENTIFY_SENDER_BIN = Path("/usr/local/bin/identify-sender")

SUBPROCESS_TIMEOUT_SEC = 30
ALERT_THROTTLE_SEC = 300  # Suppress duplicate Pushover alerts within 5 min


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
    find_catering_lead_by_code). The lead's quote_text (F14-drafted
    proposal) is piped via --quote-text-stdin. Callers must NOT
    re-read LEADS_PATH — TOCTOU mitigation.
    For `reject`: passes --reason "owner_reject_via_cf_router".
    Lead dict is ignored on reject.
    """
    try:
        env = {**os.environ, "PYTHONPATH": str(PLATFORM_DIR)}
        cmd = [str(PYTHON_BIN), str(APPLY_OWNER_DECISION_BIN),
               "--code", code, "--decision", decision]
        stdin_text: Optional[str] = None
        if decision == "approve":
            if lead is None:
                return 4  # EXIT_NOT_FOUND — caller forgot to pass lead
            stdin_text = lead.get("quote_text", "")
            if not stdin_text or stdin_text.startswith("<legacy"):
                # No drafted quote available — let LLM handle
                return 2  # EXIT_INVALID_INPUT
            cmd.append("--quote-text-stdin")
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
