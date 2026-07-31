"""M4/G4 — per-lead hold: set/clear script + the two customer send call sites.

The automation-control kernel mutes a whole CONVERSATION. A hold mutes ONE lead,
so a customer with two leads can have one held and the other running, and the
owner keeps getting cards for the held one.

Windows-runnable: the scripts are loaded in-process via fixtures_fleet.load_script
with the fcntl stub installed. The owner-approved-quote leg lives in
tests/test_catering_apply_lead_hold.py.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub, load_script

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "src" / "agents" / "catering" / "scripts"
for _p in (REPO / "src" / "platform",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

HOLD_SCRIPT = SCRIPTS / "set-catering-lead-hold"
ACK_SCRIPT = SCRIPTS / "send-catering-ack"

CUSTOMER_JID = "15550100777@s.whatsapp.net"


def _lead(lead_id="L0001", **over):
    lead = {
        "lead_id": lead_id, "status": "NEW",
        "customer_phone": "+15550100777", "customer_name": "Test Customer",
        "raw_inquiry": "catering for 40 people", "original_message_id": "msg_orig",
        "created_at": "2026-07-30T10:00:00+00:00",
        "updated_at": "2026-07-30T10:00:00+00:00",
    }
    lead.update(over)
    return lead


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated leads store + audit log; the hold/ack scripts read both paths
    from the env at import time, so load_script must run AFTER this."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    leads = state / "catering-leads.json"
    log = logs / "decisions.log"
    leads.write_text(json.dumps({"schema_version": 1, "leads": [_lead()]}), encoding="utf-8")
    monkeypatch.setenv("SHIFT_AGENT_LEADS_PATH", str(leads))
    monkeypatch.setenv("SHIFT_AGENT_LEADS_LOCK", str(leads) + ".lock")
    monkeypatch.setenv("SHIFT_AGENT_LOG_PATH", str(log))
    monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(log))
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path / "missing-config.yaml"))
    return {"leads": leads, "log": log, "tmp": tmp_path}


def _run_hold(env, *argv):
    mod = load_script("set_lead_hold", HOLD_SCRIPT)
    old = sys.argv
    sys.argv = ["set-catering-lead-hold", *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old


def _run_ack(env, monkeypatch, *argv, bridge=None):
    mod = load_script("send_catering_ack", ACK_SCRIPT)
    sent: list = []
    monkeypatch.setattr(
        mod, "_bridge_post",
        bridge or (lambda jid, msg: (sent.append((jid, msg)), (True, "wamid.OK"))[1]),
    )
    old = sys.argv
    sys.argv = ["send-catering-ack", *argv]
    try:
        return mod.main(), sent
    finally:
        sys.argv = old


def _leads(env):
    return json.loads(env["leads"].read_text(encoding="utf-8"))["leads"]


def _rows(env):
    if not env["log"].exists():
        return []
    return [json.loads(l) for l in env["log"].read_text(encoding="utf-8").splitlines() if l.strip()]


class TestSetAndClear:
    def test_on_sets_the_fields_and_audits(self, env):
        rc = _run_hold(env, "--lead-id", "L0001", "--on", "--reason", "customer asked us to wait")
        assert rc == 0
        lead = _leads(env)[0]
        assert lead["on_hold"] is True
        assert lead["hold_reason"] == "customer asked us to wait"
        assert lead["hold_set_at"]
        row = [r for r in _rows(env) if r["type"] == "catering_lead_hold_changed"][-1]
        assert row["lead_id"] == "L0001"
        assert row["from_hold"] is False and row["to_hold"] is True
        assert row["reason"] == "customer asked us to wait"

    def test_off_clears_the_fields(self, env):
        _run_hold(env, "--lead-id", "L0001", "--on", "--reason", "wait")
        assert _run_hold(env, "--lead-id", "L0001", "--off") == 0
        lead = _leads(env)[0]
        assert lead["on_hold"] is False
        assert lead["hold_reason"] is None
        assert lead["hold_set_at"] is None

    def test_idempotent_reassert(self, env):
        """Re-running with the same flag leaves the same final state and still
        audits the no-op, mirroring catering_automation_control_changed."""
        _run_hold(env, "--lead-id", "L0001", "--on", "--reason", "wait")
        assert _run_hold(env, "--lead-id", "L0001", "--on", "--reason", "still waiting") == 0
        lead = _leads(env)[0]
        assert lead["on_hold"] is True
        assert lead["hold_reason"] == "still waiting"
        rows = [r for r in _rows(env) if r["type"] == "catering_lead_hold_changed"]
        assert rows[-1]["from_hold"] is True and rows[-1]["to_hold"] is True

    def test_idempotent_double_off(self, env):
        assert _run_hold(env, "--lead-id", "L0001", "--off") == 0
        assert _run_hold(env, "--lead-id", "L0001", "--off") == 0
        assert _leads(env)[0]["on_hold"] is False

    def test_hold_does_not_touch_status_or_updated_at(self, env):
        """A hold is not a lifecycle event, and the TTL sweep reads updated_at.
        Compared as instants, not strings: the pydantic round-trip renormalizes
        +00:00 to Z, which every script rewriting this store already does."""
        before = _leads(env)[0]
        _run_hold(env, "--lead-id", "L0001", "--on", "--reason", "wait")
        after = _leads(env)[0]
        assert after["status"] == before["status"]
        assert (datetime.fromisoformat(after["updated_at"].replace("Z", "+00:00"))
                == datetime.fromisoformat(before["updated_at"].replace("Z", "+00:00")))

    def test_unknown_lead_is_not_found(self, env):
        assert _run_hold(env, "--lead-id", "L9999", "--on") == 4

    def test_on_and_off_are_mutually_exclusive(self, env):
        with pytest.raises(SystemExit):
            _run_hold(env, "--lead-id", "L0001", "--on", "--off")

    def test_reason_is_bounded(self, env):
        _run_hold(env, "--lead-id", "L0001", "--on", "--reason", "x" * 500)
        assert len(_leads(env)[0]["hold_reason"]) == 200

    def test_only_the_named_lead_is_held(self, env):
        store = json.loads(env["leads"].read_text(encoding="utf-8"))
        store["leads"].append(_lead("L0002"))
        env["leads"].write_text(json.dumps(store), encoding="utf-8")
        _run_hold(env, "--lead-id", "L0001", "--on", "--reason", "wait")
        by_id = {l["lead_id"]: l for l in _leads(env)}
        assert by_id["L0001"]["on_hold"] is True
        assert by_id["L0002"].get("on_hold", False) is False


class TestAckBlockedByHold:
    def test_held_lead_blocks_the_customer_ack(self, env, monkeypatch):
        _run_hold(env, "--lead-id", "L0001", "--on", "--reason", "wait")
        rc, sent = _run_ack(
            env, monkeypatch,
            "--customer-jid", CUSTOMER_JID, "--message-text", "Thanks!",
            "--lead-id", "L0001",
        )
        assert rc == 14  # EXIT_LEAD_ON_HOLD
        assert sent == []  # nothing reached the bridge
        row = [r for r in _rows(env) if r["type"] == "catering_lead_hold_blocked_send"][-1]
        assert row["lead_id"] == "L0001"
        assert row["blocked_send_kind"] == "customer_ack"
        assert row["hold_reason"] == "wait"

    def test_unheld_lead_sends_normally(self, env, monkeypatch):
        rc, sent = _run_ack(
            env, monkeypatch,
            "--customer-jid", CUSTOMER_JID, "--message-text", "Thanks!",
            "--lead-id", "L0001",
        )
        assert rc == 0
        assert len(sent) == 1
        assert not any(r["type"] == "catering_lead_hold_blocked_send" for r in _rows(env))

    def test_ack_without_lead_id_is_never_blocked(self, env, monkeypatch):
        """The hold is lead-scoped; an ack with no lead has no hold to consult."""
        _run_hold(env, "--lead-id", "L0001", "--on", "--reason", "wait")
        rc, sent = _run_ack(
            env, monkeypatch,
            "--customer-jid", CUSTOMER_JID, "--message-text", "Thanks!",
        )
        assert rc == 0 and len(sent) == 1

    def test_hold_read_fails_open(self, env, monkeypatch):
        """An unreadable store must not swallow a courtesy ack and leave a
        customer who just wrote in with silence."""
        env["leads"].write_text("{ this is not json", encoding="utf-8")
        rc, sent = _run_ack(
            env, monkeypatch,
            "--customer-jid", CUSTOMER_JID, "--message-text", "Thanks!",
            "--lead-id", "L0001",
        )
        assert rc == 0 and len(sent) == 1

    def test_clearing_the_hold_restores_sending(self, env, monkeypatch):
        _run_hold(env, "--lead-id", "L0001", "--on", "--reason", "wait")
        assert _run_ack(env, monkeypatch, "--customer-jid", CUSTOMER_JID,
                        "--message-text", "Thanks!", "--lead-id", "L0001")[0] == 14
        _run_hold(env, "--lead-id", "L0001", "--off")
        rc, sent = _run_ack(env, monkeypatch, "--customer-jid", CUSTOMER_JID,
                            "--message-text", "Thanks!", "--lead-id", "L0001")
        assert rc == 0 and len(sent) == 1
