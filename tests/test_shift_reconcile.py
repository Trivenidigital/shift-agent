"""Tests for shift-agent-reconcile.py — boot-time crash recovery for sick-call
proposals (a §12 silent-failure-prevention tool, previously untested).

reconcile.main() classifies pending proposals at boot and:
  - `reconciling` > 5 min  → alert owner, do NOT auto-retry (avoid dup send)
  - `approved` + an outbound_attempted log entry → uncertain, alert, NO retry
  - `approved` + NO attempt logged → legitimate missed send → invoke sender
  - decisions.log unreadable → refuse (alert), never misclassify as "no attempt"
  - send timeout → mark proposal send_failed (avoid boot-loop)

Tested in-process: the script's hardcoded path globals + `_alert` + `subprocess.run`
are monkeypatched, so NOTHING is actually sent and no real subprocess runs (send-
safe). Fixtures are built via the Pydantic models (valid by construction).

reconcile.py imports safe_io (fcntl, Unix-only). A Windows-only fcntl stub is
installed BEFORE the import so the test runs locally on Windows and against real
fcntl on the Linux CI runner — mirrors web/backend/tests/conftest.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ── Windows-only fcntl stub, BEFORE loading the script (which imports safe_io) ──
if os.name == "nt" and "fcntl" not in sys.modules:
    _stub = types.ModuleType("fcntl")
    _stub.LOCK_EX = 2
    _stub.LOCK_UN = 8
    _stub.LOCK_NB = 4
    _stub.flock = lambda *a, **k: None
    sys.modules["fcntl"] = _stub

_REPO = Path(__file__).resolve().parent.parent
_PLATFORM = _REPO / "src" / "platform"
for _p in (_PLATFORM, _REPO / "src" / "agents" / "shift"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_SCRIPT = _REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-reconcile.py"


def _load_reconcile():
    spec = importlib.util.spec_from_file_location("shift_agent_reconcile_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rec = _load_reconcile()

FIXED_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)

_VALID_CONFIG = {
    "schema_version": 1,
    "customer": {"name": "Triveni", "location_id": "loc_jax_01", "timezone": "America/New_York"},
    "owner": {"name": "Owner", "phone": "+19045550100", "self_chat_jid": "19045550100@s.whatsapp.net"},
    "limits": {},
    "alerting": {"pushover_user_key": "test_k", "pushover_app_token": "test_t"},
    "backup": {"gpg_recipient_email": "x@y"},
}


def _base_prop(pid: str, code: str, ts: datetime) -> dict:
    return {
        "proposal_id": pid, "code": code,
        "created_ts": ts, "last_updated_ts": ts,
        "absent_employee_id": "e001", "absent_date": "2026-04-25",
        "absent_shift": "09:00-17:00", "absent_role": "cashier",
        "absent_reason": "fever", "input_message": "out sick", "message_id": "m001",
    }


def _write_pending(path: Path, proposals: list) -> None:
    from schemas import PendingStore
    store = PendingStore(proposals={p.proposal_id: p for p in proposals})
    path.write_text(store.model_dump_json(), encoding="utf-8")


class _FakeRun:
    """Records subprocess invocations; configurable per-binary behavior. Never
    runs a real process (send-safe)."""
    def __init__(self):
        self.calls: list[list[str]] = []
        self.behaviors: dict[str, tuple] = {}  # binname -> ("ok"|"fail"|"timeout", rc, out, err)

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        name = Path(cmd[0]).name
        beh = self.behaviors.get(name, ("ok",))
        if beh[0] == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        rc = beh[1] if len(beh) > 1 else 0
        out = beh[2] if len(beh) > 2 else ""
        err = beh[3] if len(beh) > 3 else ""
        return subprocess.CompletedProcess(cmd, rc, out, err)

    def names(self) -> list[str]:
        return [Path(c[0]).name for c in self.calls]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Wire reconcile's hardcoded globals to tmp paths + send-safe stubs."""
    import yaml
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    cfg_path = tmp_path / "config.yaml"
    pending_path = state / "pending.json"
    log_path = logs / "decisions.log"
    lock_path = state / "pending.json.lock"
    cfg_path.write_text(yaml.safe_dump(_VALID_CONFIG), encoding="utf-8")
    log_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(rec, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(rec, "PENDING_PATH", pending_path)
    monkeypatch.setattr(rec, "PENDING_LOCK", lock_path)
    monkeypatch.setattr(rec, "LOG_PATH", log_path)
    monkeypatch.setattr(rec, "customer_now", lambda _tz: FIXED_NOW)

    alerts: list[dict] = []
    monkeypatch.setattr(rec, "_alert", lambda msg, priority=1, title="Reconciler":
                        alerts.append({"msg": msg, "priority": priority}))
    fake_run = _FakeRun()
    monkeypatch.setattr(rec.subprocess, "run", fake_run)

    return types.SimpleNamespace(
        cfg_path=cfg_path, pending_path=pending_path, log_path=log_path,
        alerts=alerts, run=fake_run,
    )


def _approved(pid, code, ts):
    from schemas import ApprovedProposal
    return ApprovedProposal(status="approved", approved_ts=ts, owner_input="ok",
                            **_base_prop(pid, code, ts))


def _reconciling(pid, code, ts):
    from schemas import ReconcilingProposal
    return ReconcilingProposal(status="reconciling", reconciling_started_ts=ts,
                               reconciling_pid=4321, **_base_prop(pid, code, ts))


def _sent(pid, code, ts):
    from schemas import SentProposal
    return SentProposal(status="sent", sent_ts=ts, **_base_prop(pid, code, ts))


# ── nothing to do ───────────────────────────────────────────────────────────

def test_nothing_to_do_returns_ok_no_alert_no_send(env):
    _write_pending(env.pending_path, [_sent("P0001", "#A3F2X", FIXED_NOW)])
    assert rec.main() == rec.EXIT_OK
    assert env.alerts == []
    assert env.run.calls == []


def test_missing_pending_file_is_ok(env):
    # no pending.json written → load_model default empty store → nothing to do
    assert rec.main() == rec.EXIT_OK
    assert env.run.calls == []


# ── stuck reconciling: alert, NEVER auto-retry ──────────────────────────────

def test_stuck_reconciling_alerts_and_does_not_send(env):
    _write_pending(env.pending_path, [_reconciling("P0002", "#B4G3Y", FIXED_NOW - timedelta(minutes=10))])
    assert rec.main() == rec.EXIT_OK
    assert any("reconciling" in a["msg"] and a["priority"] == 2 for a in env.alerts)
    assert "send-coverage-message" not in env.run.names()  # NO auto-retry


def test_reconciling_within_window_is_not_flagged(env):
    _write_pending(env.pending_path, [_reconciling("P0003", "#C5H4Z", FIXED_NOW - timedelta(minutes=1))])
    assert rec.main() == rec.EXIT_OK
    assert env.alerts == []
    assert env.run.calls == []


# ── approved + prior attempt logged: uncertain, alert, NO retry ─────────────

def test_approved_with_attempt_in_log_is_uncertain_no_send(env):
    _write_pending(env.pending_path, [_approved("P0004", "#D6J5A", FIXED_NOW)])
    env.log_path.write_text(
        json.dumps({"type": "outbound_attempted", "proposal_id": "P0004"}) + "\n",
        encoding="utf-8")
    assert rec.main() == rec.EXIT_OK
    assert any("outbound_attempted" in a["msg"] for a in env.alerts)
    assert "send-coverage-message" not in env.run.names()


# ── approved + NO attempt: legitimate missed send → invoke sender ───────────

def test_approved_no_attempt_triggers_send(env):
    _write_pending(env.pending_path, [_approved("P0005", "#E7K6B", FIXED_NOW)])
    assert rec.main() == rec.EXIT_OK
    sends = [c for c in env.run.calls if Path(c[0]).name == "send-coverage-message"]
    assert len(sends) == 1 and "P0005" in sends[0]


def test_approved_send_failure_alerts_priority_1(env):
    _write_pending(env.pending_path, [_approved("P0006", "#F8P7C", FIXED_NOW)])
    env.run.behaviors["send-coverage-message"] = ("fail", 3, "", "boom")
    assert rec.main() == rec.EXIT_OK
    assert any("failed" in a["msg"].lower() and a["priority"] == 1 for a in env.alerts)


# ── send timeout → mark send_failed (avoid boot-loop) ───────────────────────

def test_send_timeout_marks_send_failed(env):
    _write_pending(env.pending_path, [_approved("P0007", "#G9M8D", FIXED_NOW)])
    env.run.behaviors["send-coverage-message"] = ("timeout",)
    assert rec.main() == rec.EXIT_OK
    names = env.run.names()
    assert "send-coverage-message" in names and "update-proposal-status" in names
    upd = next(c for c in env.run.calls if Path(c[0]).name == "update-proposal-status")
    assert "P0007" in upd and "send_failed" in upd
    assert any("timed out" in a["msg"] for a in env.alerts)


# ── decisions.log unreadable: refuse + alert, never misclassify as no-attempt ─

def test_unreadable_log_refuses_and_does_not_send(env):
    _write_pending(env.pending_path, [_approved("P0008", "#H2N9E", FIXED_NOW)])
    # make LOG_PATH a directory so .open() raises OSError → AttemptLogUnreadable
    env.log_path.unlink()
    env.log_path.mkdir()
    assert rec.main() == rec.EXIT_OK
    assert any("unreadable" in a["msg"].lower() for a in env.alerts)
    assert "send-coverage-message" not in env.run.names()  # MUST NOT risk a dup send


# ── config load failure: schema-violation exit + alert ──────────────────────

def test_config_load_failure_returns_schema_violation(env):
    env.cfg_path.write_text("customer: {}\n", encoding="utf-8")  # missing required sections
    assert rec.main() == rec.EXIT_SCHEMA_VIOLATION
    assert any(a["priority"] == 2 for a in env.alerts)
