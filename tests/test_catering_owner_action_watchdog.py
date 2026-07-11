"""F8 catering-owner-action-watchdog tests (restored 2026-07-11, census C2).

Covers the deterministic no-LLM absorbing shim for missed owner
`#XXXXX approve|reject` commands:

  --text parse mode (subprocess): approve / reject / edit / wait / no-code
  process_inbound decision logic (in-process, stubbed apply-decision):
    - approve w/ truth-guard pass  -> invokes apply-catering-owner-decision, FIRED success
    - approve w/ null headcount    -> truth-guard SUPPRESS (apply NOT invoked, FIRED success=False)
    - reject passthrough           -> invokes apply-decision with --reason, FIRED success
    - edit                         -> SUPPRESSED (action_unsupported_by_watchdog)
    - code not found / terminal    -> SUPPRESSED
  audit rows validate through the LogEntry discriminated union.

Linux-only: emit_audit -> safe_io.ndjson_append uses O_APPEND fd semantics and
the module chains through fcntl-based safe_io on import.
"""
from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
import sys
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="watchdog audit path depends on safe_io (fcntl / O_APPEND, Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "catering" / "scripts" / "catering-owner-action-watchdog"
PLATFORM_DIR = REPO / "src" / "platform"


# ---------------------------------------------------------------------------
# --text parse mode (subprocess) — the script's built-in test hook
# ---------------------------------------------------------------------------

def _run_text(text: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--text", text],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


class TestParseTextMode:
    def test_approve(self):
        assert _run_text("#993HY approve")["parsed"] == ["#993HY", "approve", ""]

    def test_reject_with_reason(self):
        assert _run_text("#993HY reject too far out")["parsed"] == [
            "#993HY", "reject", "too far out"]

    def test_edit(self):
        assert _run_text("#ABCDE edit add naan")["parsed"] == [
            "#ABCDE", "edit", "add naan"]

    def test_wait(self):
        assert _run_text("#ABCDE waiting on client")["parsed"] == [
            "#ABCDE", "wait", "on client"]

    def test_case_insensitive_and_normalized(self):
        assert _run_text("#993hy APPROVED")["parsed"] == ["#993HY", "approve", ""]

    def test_no_code_returns_null(self):
        assert _run_text("hello there no code")["parsed"] is None


# ---------------------------------------------------------------------------
# process_inbound decision logic (in-process, stubbed apply-decision binary)
# ---------------------------------------------------------------------------

@pytest.fixture
def watchdog(tmp_path):
    """Freshly-loaded watchdog module wired to tmp paths + a stub apply-decision
    binary. Returns (module, decisions_log_path, marker_path).

    The stub records its argv + stdin to `marker` and exits 0 so tests can assert
    both whether apply-catering-owner-decision was invoked and with what args.
    """
    if str(PLATFORM_DIR) not in sys.path:
        sys.path.insert(0, str(PLATFORM_DIR))

    marker = tmp_path / "apply_invoked.json"
    stub = tmp_path / "apply-catering-owner-decision-stub"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "data = '' if sys.stdin.isatty() else sys.stdin.read()\n"
        f"open({str(marker)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], 'stdin': data}}))\n"
        "print('stub-applied')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    decisions_log = tmp_path / "decisions.log"

    mod = SourceFileLoader("coaw_test_loaded", str(SCRIPT)).load_module()
    mod.DECISIONS_LOG = decisions_log
    mod.APPLY_DECISION_BIN = str(stub)
    mod.WATCHDOG_TIMEOUT_SECS = 0  # skip the dispatcher-wait loop immediately
    mod.find_dispatcher_routed_for = lambda *a, **k: False
    return mod, decisions_log, marker


def _lead(status="AWAITING_OWNER_APPROVAL", headcount=50, event_date="2026-06-15",
          lead_id="L0015"):
    return {
        "lead_id": lead_id,
        "status": status,
        "extracted": {"headcount": headcount, "event_date": event_date},
    }


def _read_audit(decisions_log: Path) -> list[dict]:
    if not decisions_log.exists():
        return []
    return [json.loads(l) for l in decisions_log.read_text(encoding="utf-8").splitlines() if l.strip()]


def _drive(mod, inbound_text, lead):
    """Run process_inbound with monkeypatched inbound-text + lead lookup, owner
    fast-path (chat_id == owner_jid)."""
    mod.find_inbound_text_for = lambda chat_id: inbound_text
    mod.lookup_lead_by_code = lambda code: lead
    chat_id = "19045550100@s.whatsapp.net"
    mod.process_inbound(chat_id, "msg_owner_1", chat_id, time.time() - 1)


class TestProcessInbound:
    def test_approve_truth_guard_pass_invokes_apply_decision(self, watchdog):
        mod, dlog, marker = watchdog
        _drive(mod, "#993HY approve", _lead())
        # apply-catering-owner-decision was invoked with approve + stdin quote
        assert marker.exists(), "apply-decision stub was not invoked"
        rec = json.loads(marker.read_text(encoding="utf-8"))
        assert "--decision" in rec["argv"] and "approve" in rec["argv"]
        assert "--quote-text-stdin" in rec["argv"]
        assert "50" in rec["stdin"] and "2026-06-15" in rec["stdin"]
        rows = _read_audit(dlog)
        assert len(rows) == 1
        assert rows[0]["type"] == "catering_owner_action_watchdog_fired"
        assert rows[0]["success"] is True
        assert rows[0]["action"] == "approve"
        assert rows[0]["lead_id"] == "L0015"

    def test_approve_null_headcount_suppresses(self, watchdog):
        mod, dlog, marker = watchdog
        _drive(mod, "#993HY approve", _lead(headcount=None))
        # truth-guard suppression: apply-decision NOT invoked
        assert not marker.exists(), "apply-decision should NOT run with null headcount"
        rows = _read_audit(dlog)
        assert len(rows) == 1
        assert rows[0]["type"] == "catering_owner_action_watchdog_fired"
        assert rows[0]["success"] is False
        assert "null_extracted_fields" in rows[0]["detail"]

    def test_reject_passthrough_invokes_apply_decision(self, watchdog):
        mod, dlog, marker = watchdog
        _drive(mod, "#993HY reject too far out", _lead())
        assert marker.exists()
        rec = json.loads(marker.read_text(encoding="utf-8"))
        assert "reject" in rec["argv"]
        assert "--reason" in rec["argv"]
        assert "too far out" in rec["argv"]
        rows = _read_audit(dlog)
        assert rows[0]["type"] == "catering_owner_action_watchdog_fired"
        assert rows[0]["success"] is True
        assert rows[0]["action"] == "reject"

    def test_edit_suppresses(self, watchdog):
        mod, dlog, marker = watchdog
        _drive(mod, "#993HY edit add naan", _lead())
        assert not marker.exists(), "edit has no safe fallback — apply must not run"
        rows = _read_audit(dlog)
        assert len(rows) == 1
        assert rows[0]["type"] == "catering_owner_action_watchdog_suppressed"
        assert rows[0]["reason"] == "action_unsupported_by_watchdog"

    def test_code_not_found_suppresses(self, watchdog):
        mod, dlog, marker = watchdog
        _drive(mod, "#993HY approve", None)  # lookup returns no lead
        assert not marker.exists()
        rows = _read_audit(dlog)
        assert rows[0]["type"] == "catering_owner_action_watchdog_suppressed"
        assert rows[0]["reason"] == "code_not_found"

    def test_terminal_state_suppresses(self, watchdog):
        mod, dlog, marker = watchdog
        _drive(mod, "#993HY approve", _lead(status="CUSTOMER_CONFIRMED"))
        assert not marker.exists()
        rows = _read_audit(dlog)
        assert rows[0]["type"] == "catering_owner_action_watchdog_suppressed"
        assert rows[0]["reason"] == "lead_terminal_state"

    def test_audit_rows_validate_through_logentry(self, watchdog):
        mod, dlog, _ = watchdog
        # Drive one FIRED + one SUPPRESSED row, then validate both through the
        # LogEntry discriminated union (schema round-trip).
        _drive(mod, "#993HY approve", _lead())
        _drive(mod, "#993HY edit add naan", _lead())
        from pydantic import TypeAdapter
        from schemas import LogEntry
        ta = TypeAdapter(LogEntry)
        rows = dlog.read_text(encoding="utf-8").splitlines()
        assert len(rows) == 2
        parsed = [ta.validate_json(r) for r in rows if r.strip()]
        typenames = {type(p).__name__ for p in parsed}
        assert typenames == {
            "CateringOwnerActionWatchdogFired",
            "CateringOwnerActionWatchdogSuppressed",
        }


class TestBuildFallbackQuoteText:
    def test_includes_headcount_and_date(self, watchdog):
        mod, _, _ = watchdog
        text = mod.build_fallback_quote_text(_lead())
        assert text is not None
        assert "50" in text and "2026-06-15" in text

    def test_null_headcount_returns_none(self, watchdog):
        mod, _, _ = watchdog
        assert mod.build_fallback_quote_text(_lead(headcount=None)) is None

    def test_null_event_date_returns_none(self, watchdog):
        mod, _, _ = watchdog
        assert mod.build_fallback_quote_text(_lead(event_date=None)) is None
