"""Tests for shift-agent-fsck.py nightly invariant checks.

The script is a silent-failure-prevention safety net, so these tests keep it
send-safe: hardcoded paths are redirected to tmp files and the owner alert
subprocess is replaced with a recorder. The assertions focus on the checks that
would otherwise only run on a deployed box.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

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

# Pin repo modules before loading the script; the deployed script prepends
# /opt/shift-agent, which may exist on VPS test hosts.
import exit_codes  # noqa: E402,F401
import safe_io  # noqa: E402,F401
import schemas  # noqa: E402,F401

_SCRIPT = _REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-fsck.py"


def _load_fsck():
    spec = importlib.util.spec_from_file_location("shift_agent_fsck_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fsck = _load_fsck()

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
        "proposal_id": pid,
        "code": code,
        "created_ts": ts.isoformat(),
        "last_updated_ts": ts.isoformat(),
        "absent_employee_id": "e001",
        "absent_date": "2026-04-25",
        "absent_shift": "09:00-17:00",
        "absent_role": "cashier",
        "absent_reason": "fever",
        "input_message": "out sick",
        "message_id": f"m-{pid}",
    }


def _proposal(pid: str, code: str, status: str, ts: datetime, **extra) -> dict:
    data = _base_prop(pid, code, ts)
    data["status"] = status
    data.update(extra)
    return data


def _write_pending(path: Path, proposals: list[dict]) -> None:
    path.write_text(json.dumps({"proposals": {p["proposal_id"]: p for p in proposals}}), encoding="utf-8")


def _read_violations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") == "invariant_violation":
            rows.append(row)
    return rows


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()

    cfg_path = tmp_path / "config.yaml"
    roster_path = tmp_path / "roster.json"
    pending_path = state / "pending.json"
    counter_path = state / "send-counter.json"
    seen_path = state / "seen-ids.json"
    log_path = logs / "decisions.log"
    log_lock = logs / "decisions.log.lock"
    agent_log = logs / "agent.log"

    cfg_path.write_text(yaml.safe_dump(_VALID_CONFIG), encoding="utf-8")
    roster_path.write_text(json.dumps({"employees": []}), encoding="utf-8")
    _write_pending(pending_path, [])
    log_path.write_text("", encoding="utf-8")
    agent_log.write_text("agent log\n", encoding="utf-8")

    monkeypatch.setattr(fsck, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(fsck, "ROSTER_PATH", roster_path)
    monkeypatch.setattr(fsck, "PENDING_PATH", pending_path)
    monkeypatch.setattr(fsck, "COUNTER_PATH", counter_path)
    monkeypatch.setattr(fsck, "SEEN_PATH", seen_path)
    monkeypatch.setattr(fsck, "LOG_PATH", log_path)
    monkeypatch.setattr(fsck, "LOG_LOCK", log_lock)
    monkeypatch.setattr(fsck, "AGENT_LOG", agent_log)
    monkeypatch.setattr(fsck, "customer_now", lambda _tz: FIXED_NOW)
    monkeypatch.setattr(fsck, "customer_today_str", lambda _tz: "2026-05-31")

    alerts: list[str] = []
    monkeypatch.setattr(fsck, "_alert", alerts.append)

    return types.SimpleNamespace(
        cfg_path=cfg_path,
        pending_path=pending_path,
        counter_path=counter_path,
        seen_path=seen_path,
        log_path=log_path,
        agent_log=agent_log,
        alerts=alerts,
    )


def test_clean_state_prints_ok_and_does_not_alert(env, capsys):
    assert fsck.main() == fsck.EXIT_OK

    captured = capsys.readouterr()
    assert "fsck: all invariants OK" in captured.out
    assert env.alerts == []
    assert _read_violations(env.log_path) == []


def test_duplicate_code_among_nonterminal_proposals_logs_violation(env, capsys):
    _write_pending(env.pending_path, [
        _proposal("P0001", "#A3F2X", "awaiting_owner_approval", FIXED_NOW),
        _proposal("P0002", "#A3F2X", "approved", FIXED_NOW, approved_ts=FIXED_NOW.isoformat(), owner_input="ok"),
        _proposal("P0003", "#A3F2X", "accepted", FIXED_NOW, response_ts=FIXED_NOW.isoformat(), response_message="yes"),
    ])

    assert fsck.main() == fsck.EXIT_OK

    captured = capsys.readouterr()
    rows = _read_violations(env.log_path)
    assert "VIOLATION code_uniqueness" in captured.out
    assert [r["check"] for r in rows] == ["code_uniqueness"]
    assert env.alerts and "1 invariant violations" in env.alerts[0]


def test_unknown_proposal_status_logs_violation(env):
    # A status this binary doesn't recognize downgrades to _UnknownProposal (BL-HERMES-06);
    # fsck must raise the loud §12a signal the pre-shim brick used to give.
    _write_pending(env.pending_path, [
        _proposal("P0007", "#E7K6B", "future_holo_status", FIXED_NOW),
    ])

    assert fsck.main() == fsck.EXIT_OK

    rows = _read_violations(env.log_path)
    assert [r["check"] for r in rows] == ["unknown_proposal_status"]
    assert "P0007" in rows[0]["detail"]
    assert "future_holo_status" in rows[0]["detail"]
    assert env.alerts and "1 invariant violations" in env.alerts[0]


def test_known_status_does_not_trigger_unknown_check(env):
    # Sanity: a fully-known proposal must NOT be flagged as unknown.
    _write_pending(env.pending_path, [
        _proposal("P0008", "#F8M7C", "sent", FIXED_NOW, sent_ts=FIXED_NOW.isoformat()),
    ])

    assert fsck.main() == fsck.EXIT_OK

    checks = [r["check"] for r in _read_violations(env.log_path)]
    assert "unknown_proposal_status" not in checks


def test_reconciling_older_than_ten_minutes_logs_violation(env):
    _write_pending(env.pending_path, [
        _proposal(
            "P0004", "#B4G3Y", "reconciling", FIXED_NOW - timedelta(minutes=11),
            reconciling_started_ts=(FIXED_NOW - timedelta(minutes=11)).isoformat(),
            reconciling_pid=4321,
        )
    ])

    assert fsck.main() == fsck.EXIT_OK

    rows = _read_violations(env.log_path)
    assert [r["check"] for r in rows] == ["reconciling_stuck"]
    assert "P0004 stuck in reconciling" in rows[0]["detail"]


def test_malformed_decisions_log_line_logs_violation(env):
    env.log_path.write_text("{not json}\n", encoding="utf-8")

    assert fsck.main() == fsck.EXIT_OK

    rows = _read_violations(env.log_path)
    assert [r["check"] for r in rows] == ["decisions_log_malformed"]
    assert "line 1" in rows[0]["detail"]


def test_log_proposal_missing_from_pending_logs_orphan(env):
    env.log_path.write_text(json.dumps({"type": "proposal_created", "proposal_id": "P0005", "code": "#C5H4Z"}) + "\n", encoding="utf-8")

    assert fsck.main() == fsck.EXIT_OK

    checks = [r["check"] for r in _read_violations(env.log_path)]
    assert checks == ["orphan_log_entry", "orphan_proposal"]


def test_counter_mismatch_for_today_logs_violation(env):
    _write_pending(env.pending_path, [
        _proposal("P0006", "#D6J5A", "sent", FIXED_NOW, sent_ts=FIXED_NOW.isoformat())
    ])
    env.log_path.write_text(
        json.dumps({
            "type": "outbound_sent",
            "ts": "2026-05-31T10:00:00-04:00",
            "proposal_id": "P0006",
            "recipient_employee_id": "e001",
            "outbound_message_id": "wamid.1",
            "rendered": "Can you cover?",
        }) + "\n",
        encoding="utf-8",
    )
    env.counter_path.write_text(json.dumps({"day": "2026-05-31", "count": 0}), encoding="utf-8")

    assert fsck.main() == fsck.EXIT_OK

    rows = _read_violations(env.log_path)
    assert [r["check"] for r in rows] == ["counter_mismatch"]
    assert "send-counter.count=0" in rows[0]["detail"]


def test_seen_offset_past_agent_log_eof_logs_violation(env):
    env.agent_log.write_text("short\n", encoding="utf-8")
    env.seen_path.write_text(json.dumps({"seen_message_ids": [], "last_offset_bytes": 999}), encoding="utf-8")

    assert fsck.main() == fsck.EXIT_OK

    rows = _read_violations(env.log_path)
    assert [r["check"] for r in rows] == ["seen_offset_past_eof"]
    assert "offset 999" in rows[0]["detail"]


def test_config_load_failure_returns_schema_violation(env, capsys):
    env.cfg_path.write_text("customer: {}\n", encoding="utf-8")

    assert fsck.main() == fsck.EXIT_SCHEMA_VIOLATION

    captured = capsys.readouterr()
    assert "fsck: config load failed" in captured.err
    assert env.alerts == []
