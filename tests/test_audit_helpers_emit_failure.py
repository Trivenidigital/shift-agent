"""§12a: record_audit_emit_failure + the cf-router swallow sites that call it.

A best-effort audit emitter that swallows its own failure to stderr leaves no
durable trace — the row is simply missing. These tests prove the trace row is
written, is a valid LogEntry, carries metadata only, never raises, and that the
cf-router emitters actually reach it (helper monkeypatched AND real end-to-end).
"""
from __future__ import annotations

import importlib.util
import json
import platform
import re
from pathlib import Path
import sys

import pytest

# audit_helpers + cf-router actions transitively import safe_io → fcntl.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="audit_helpers transitively depends on fcntl via safe_io; runs on Linux CI",
)

REPO = Path(__file__).resolve().parent.parent

if platform.system() != "Windows":
    from pydantic import TypeAdapter, ValidationError

    import audit_helpers
    from audit_helpers import record_audit_emit_failure
    from schemas import AuditEmitFailed, CfRouterIntercepted, LogEntry

    sys.path.insert(0, str(REPO / "src" / "platform"))
    _spec = importlib.util.spec_from_file_location(
        "cf_actions_emit_failure", REPO / "src" / "plugins" / "cf-router" / "actions.py")
    cf_actions = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(cf_actions)


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _validation_error(reason: str) -> "ValidationError":
    from datetime import datetime, timezone
    try:
        CfRouterIntercepted(type="cf_router_intercepted", ts=datetime.now(timezone.utc),
                            reason=reason, chat_id="c")
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


# ─────────────── helper ───────────────

def test_writes_one_valid_log_entry_row(tmp_path):
    log = tmp_path / "decisions.log"
    ok = record_audit_emit_failure(
        source="cf-router.audit_intercepted", intended_type="cf_router_intercepted",
        intended_reason="f8_owner_approve", error=RuntimeError("disk"), detail="lock busy",
        log_path=log,
    )
    assert ok is True
    rows = _rows(log)
    assert len(rows) == 1
    entry = TypeAdapter(LogEntry).validate_python(rows[0])
    assert isinstance(entry, AuditEmitFailed)
    assert entry.source == "cf-router.audit_intercepted"
    assert entry.intended_type == "cf_router_intercepted"
    assert entry.intended_reason == "f8_owner_approve"
    assert entry.error_class == "RuntimeError"
    assert entry.detail == "lock busy"


def test_validation_error_detail_is_metadata_only(tmp_path):
    """str(ValidationError) echoes input_value — message text / phones. The
    default detail must be loc:type pairs only."""
    log = tmp_path / "decisions.log"
    exc = _validation_error("call +15551234567 secret body text")
    assert record_audit_emit_failure(source="t", intended_type="cf_router_intercepted",
                                     error=exc, log_path=log)
    row = _rows(log)[0]
    assert row["error_class"] == "ValidationError"
    assert "reason" in row["detail"] and "literal_error" in row["detail"]
    assert "secret" not in json.dumps(row)


def test_phone_like_runs_scrubbed_and_fields_clamped(tmp_path):
    log = tmp_path / "decisions.log"
    assert record_audit_emit_failure(
        source="s" * 200, intended_type="t" * 200, intended_reason="r+15551234567" * 20,
        error=ValueError(), detail="lead +15551234567 / 9876543210 " + "x" * 2000,
        log_path=log,
    )
    row = _rows(log)[0]
    blob = json.dumps(row)
    assert not re.search(r"\d{7,}", blob), blob
    assert "<num>" in row["detail"]
    assert len(row["detail"]) == 500
    assert len(row["source"]) == 64 and len(row["intended_type"]) == 64
    assert len(row["intended_reason"]) <= 64
    TypeAdapter(LogEntry).validate_python(row)


def test_unwritable_path_returns_false_never_raises(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    ok = record_audit_emit_failure(source="s", intended_type="t", error=OSError("x"),
                                   log_path=blocker / "decisions.log")
    assert ok is False


def test_contended_lock_does_not_block_and_reports_to_stderr(tmp_path, capsys):
    """One NON-blocking attempt: a held `<log>.lock` must not stall the inbound."""
    import time
    from safe_io import FileLock

    log = tmp_path / "decisions.log"
    with FileLock(Path(str(log) + ".lock")):
        t0 = time.monotonic()
        ok = record_audit_emit_failure(source="cf-router.audit_intercepted",
                                       intended_type="cf_router_intercepted",
                                       error=RuntimeError("x"), log_path=log)
        elapsed = time.monotonic() - t0
    assert ok is False
    assert elapsed < 0.5
    assert not log.exists()
    assert "lock busy" in capsys.readouterr().err


def test_hostile_error_object_never_raises(tmp_path):
    class Hostile(Exception):
        def errors(self):
            raise RuntimeError("boom")
    assert record_audit_emit_failure(source="s", intended_type="t", error=Hostile(),
                                     log_path=tmp_path / "d.log") is True


def test_default_path_honours_decisions_log_env(tmp_path, monkeypatch):
    log = tmp_path / "env" / "decisions.log"
    log.parent.mkdir()
    monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(log))
    assert record_audit_emit_failure(source="s", intended_type="t", error=KeyError("k"))
    assert [r["type"] for r in _rows(log)] == ["audit_emit_failed"]


# ─────────────── cf-router swallow sites ───────────────

@pytest.fixture
def recorded(monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setattr(cf_actions, "LOG_PATH", tmp_path / "decisions.log")
    monkeypatch.setattr(audit_helpers, "record_audit_emit_failure",
                        lambda **kw: calls.append(kw) or True)
    return calls


def test_audit_intercepted_invalid_reason_calls_helper(recorded, tmp_path):
    cf_actions.audit_intercepted(reason="bogus_fleet_reason", chat_id="c")  # must not raise
    assert len(recorded) == 1
    kw = recorded[0]
    assert kw["source"] == "cf-router.audit_intercepted"
    assert kw["intended_type"] == "cf_router_intercepted"
    assert kw["intended_reason"] == "bogus_fleet_reason"
    assert isinstance(kw["error"], ValidationError)
    assert kw["log_path"] == tmp_path / "decisions.log"


def test_audit_dispatcher_routed_failure_calls_helper(recorded, monkeypatch):
    monkeypatch.setattr(cf_actions, "identify_sender_metadata", lambda _c: {"role": "owner"})
    cf_actions.audit_dispatcher_routed(message_id="m", chat_id="c",
                                       routed_to_skill="s", message_shape="bogus_shape")
    assert len(recorded) == 1
    assert recorded[0]["source"] == "cf-router.audit_dispatcher_routed"
    assert recorded[0]["intended_type"] == "dispatcher_routed"
    assert isinstance(recorded[0]["error"], ValidationError)


def test_watchdog_suppressed_failure_calls_helper(recorded):
    cf_actions.audit_dispatcher_watchdog_suppressed(chat_id="c", message_id="m",
                                                    reason="bogus_fleet_reason")
    assert [(k["intended_type"], k["intended_reason"]) for k in recorded] == [
        ("catering_dispatcher_watchdog_suppressed", "bogus_fleet_reason")]


def test_helper_failure_inside_emitter_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(cf_actions, "LOG_PATH", tmp_path / "decisions.log")

    def boom(**_kw):
        raise RuntimeError("helper exploded")
    monkeypatch.setattr(audit_helpers, "record_audit_emit_failure", boom)
    cf_actions.audit_intercepted(reason="bogus_fleet_reason", chat_id="c")


def test_helper_import_failure_inside_emitter_never_raises(monkeypatch, tmp_path):
    """The helper import sits in its OWN try: if audit_helpers cannot import
    (the same import-class failure that broke the original emit), the
    emitter's except must still not raise."""
    monkeypatch.setattr(cf_actions, "LOG_PATH", tmp_path / "decisions.log")
    monkeypatch.setattr(cf_actions, "identify_sender_metadata", lambda _c: {"role": "owner"})
    monkeypatch.setitem(sys.modules, "audit_helpers", None)  # `import` -> ImportError
    cf_actions.audit_intercepted(reason="bogus_fleet_reason", chat_id="c")
    cf_actions.audit_dispatcher_routed(message_id="m", chat_id="c",
                                       routed_to_skill="s", message_shape="bogus_shape")


def test_audit_intercepted_end_to_end_writes_trace_row(monkeypatch, tmp_path):
    """WITNESS: no monkeypatched helper — the real row lands on cf-router's LOG_PATH."""
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(cf_actions, "LOG_PATH", log)
    cf_actions.audit_intercepted(reason="bogus_fleet_reason", chat_id="+15551234567")
    rows = _rows(log)
    assert [r["type"] for r in rows] == ["audit_emit_failed"]
    entry = TypeAdapter(LogEntry).validate_python(rows[0])
    assert entry.intended_type == "cf_router_intercepted"
    assert entry.intended_reason == "bogus_fleet_reason"
    assert "15551234567" not in log.read_text(encoding="utf-8")


# ─────────────── fleet harness smoke ───────────────

def test_fleet_harness_routes_audit_rows_to_state_dir(tmp_path):
    """run_agent_script's env contract reaches the audit chokepoint: a script
    that calls the helper with NO log_path lands its row under state_dir."""
    from fleet_harness import decisions_log_path, read_audit_rows, run_agent_script

    script = tmp_path / "emit.py"
    script.write_text(
        "from audit_helpers import record_audit_emit_failure\n"
        "import sys\n"
        "sys.exit(0 if record_audit_emit_failure(source='harness', "
        "intended_type='raw_inbound', error=ValueError('x')) else 3)\n",
        encoding="utf-8",
    )
    state = tmp_path / "state"
    proc = run_agent_script(script, [], state_dir=state)
    assert proc.returncode == 0, proc.stderr
    rows = read_audit_rows(decisions_log_path(state), type_filter="audit_emit_failed")
    assert [r["source"] for r in rows] == ["harness"]
    assert read_audit_rows(decisions_log_path(state), type_filter={"raw_inbound"}) == []
