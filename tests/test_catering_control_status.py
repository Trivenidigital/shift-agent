"""M4/deliverable 5 — catering-control-status renders every control, read-only.

The controls live in four different stores; this script is the one place that
answers "what is currently allowed to talk to customers". Two properties matter
most and are pinned here: it MUTATES NOTHING, and a broken store degrades to a
"UNREADABLE" line instead of hiding the other three.

Windows-runnable via the fcntl stub + in-process load_script.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub, load_script

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import automation_control as ac  # noqa: E402

STATUS_SCRIPT = REPO / "src" / "agents" / "catering" / "scripts" / "catering-control-status"
CUSTOMER = "15550100777@c.us"


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    leads = state / "catering-leads.json"
    kernel = state / "catering-automation-control.json"
    flag = state / "disabled.flag"
    leads.write_text(json.dumps({"schema_version": 1, "leads": []}), encoding="utf-8")
    monkeypatch.setenv("SHIFT_AGENT_LEADS_PATH", str(leads))
    monkeypatch.setenv("SHIFT_AGENT_DISABLED_FLAG", str(flag))
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_STATE_PATH", str(kernel))
    ac._LAST_KNOWN_MODE.clear()
    yield {"leads": leads, "kernel": kernel, "flag": flag, "state": state}
    ac._LAST_KNOWN_MODE.clear()


def _report(env):
    """Build the report through the script's own entry point."""
    mod = load_script("catering_control_status", STATUS_SCRIPT)
    return mod, mod.build_report()


def _run(env, *argv):
    mod = load_script("catering_control_status", STATUS_SCRIPT)
    old = sys.argv
    sys.argv = ["catering-control-status", *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old


def _seed_lead(env, lead_id="L0001", **over):
    store = json.loads(env["leads"].read_text(encoding="utf-8"))
    lead = {
        "lead_id": lead_id, "status": "NEW",
        "customer_phone": "+15550100777", "raw_inquiry": "x",
        "original_message_id": "m", "created_at": "2026-07-30T10:00:00+00:00",
        "updated_at": "2026-07-30T10:00:00+00:00",
    }
    lead.update(over)
    store["leads"].append(lead)
    env["leads"].write_text(json.dumps(store), encoding="utf-8")


class TestKillSwitchSection:
    def test_reports_clear_when_absent(self, env):
        _, rep = _report(env)
        assert rep["kill_switch"]["engaged"] is False
        assert rep["kill_switch"]["readable"] is True

    def test_reports_engaged_with_set_at(self, env):
        env["flag"].write_text("x", encoding="utf-8")
        _, rep = _report(env)
        assert rep["kill_switch"]["engaged"] is True
        assert rep["kill_switch"]["set_at"]


class TestKernelSection:
    def test_flags_and_allowlist_semantics(self, env, monkeypatch):
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
        monkeypatch.setenv("CATERING_STOP_ENABLED", "1")
        _, rep = _report(env)
        assert rep["flags"]["kernel"]["CATERING_AUTOMATION_CONTROL_ENABLED"] == "1"
        assert rep["flags"]["kernel"]["CATERING_TAKEOVER_ENABLED"] == ""
        allow = rep["flags"]["kernel_allowlists"]["CATERING_AUTOMATION_CONTROL_ALLOWLIST"]
        assert allow["mode"] == "wildcard"

    def test_empty_allowlist_is_reported_as_disabled(self, env, monkeypatch):
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "")
        _, rep = _report(env)
        allow = rep["flags"]["kernel_allowlists"]["CATERING_AUTOMATION_CONTROL_ALLOWLIST"]
        assert allow["mode"] == "empty"
        assert "DISABLED" in allow["effect"]

    def test_scoped_allowlist_counts_without_printing_numbers(self, env, monkeypatch):
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "+17329837841,+15550100777")
        _, rep = _report(env)
        allow = rep["flags"]["kernel_allowlists"]["CATERING_AUTOMATION_CONTROL_ALLOWLIST"]
        assert allow["mode"] == "scoped" and allow["entry_count"] == 2
        assert "17329837841" not in json.dumps(rep)

    def test_conversation_modes_with_expiry(self, env, monkeypatch):
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
        ac.set_mode(CUSTOMER, "takeover", actor="owner", reason="owner_takeover")
        _, rep = _report(env)
        convs = rep["automation_control"]["conversations"]
        assert len(convs) == 1
        assert convs[0]["effective_mode"] == "takeover"
        assert convs[0]["takeover_expires_ts"]
        assert convs[0]["expired"] is False
        assert rep["automation_control"]["suppressed_count"] == 1
        # never prints a raw identifier
        assert "15550100777" not in json.dumps(rep)

    def test_expired_record_reports_stored_and_effective(self, env, monkeypatch):
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
        ac.set_mode(CUSTOMER, "paused", actor="customer", reason="p", pause_seconds=3600)
        doc = json.loads(env["kernel"].read_text(encoding="utf-8"))
        key = next(iter(doc["conversations"]))
        doc["conversations"][key]["pause_expires_ts"] = "2000-01-01T00:00:00+00:00"
        env["kernel"].write_text(json.dumps(doc), encoding="utf-8")

        _, rep = _report(env)
        conv = rep["automation_control"]["conversations"][0]
        assert conv["stored_mode"] == "paused"
        assert conv["effective_mode"] == "active"
        assert conv["expired"] is True
        assert rep["automation_control"]["suppressed_count"] == 0


class TestHoldSection:
    def test_lists_only_held_leads(self, env):
        _seed_lead(env, "L0001", on_hold=True, hold_reason="wait",
                   hold_set_at="2026-07-31T00:00:00+00:00")
        _seed_lead(env, "L0002")
        _, rep = _report(env)
        assert rep["lead_holds"]["held_count"] == 1
        assert rep["lead_holds"]["total_leads"] == 2
        assert rep["lead_holds"]["leads"][0]["lead_id"] == "L0001"
        assert rep["lead_holds"]["leads"][0]["hold_reason"] == "wait"

    def test_no_holds_is_zero_not_an_error(self, env):
        _seed_lead(env, "L0001")
        _, rep = _report(env)
        assert rep["lead_holds"]["readable"] is True
        assert rep["lead_holds"]["held_count"] == 0


class TestDegradation:
    def test_corrupt_leads_store_does_not_hide_the_other_sections(self, env):
        env["leads"].write_text("{ not json", encoding="utf-8")
        env["flag"].write_text("x", encoding="utf-8")
        mod, rep = _report(env)
        assert rep["lead_holds"]["readable"] is False
        assert rep["lead_holds"]["error"]
        # the other three sections still report
        assert rep["kill_switch"]["engaged"] is True
        assert rep["automation_control"]["readable"] is True
        assert "flags" in rep
        human = mod._human(rep)
        assert "UNREADABLE" in human
        assert "kill switch: ENGAGED" in human


class TestReadOnly:
    def test_creates_no_files_and_writes_no_audit_rows(self, env, monkeypatch, tmp_path):
        """Running the status surface must never perturb what it reports."""
        audit = tmp_path / "audit" / "decisions.log"
        monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(audit))
        before = sorted(p.name for p in env["state"].iterdir())

        assert _run(env, "--json") == 0

        assert sorted(p.name for p in env["state"].iterdir()) == before
        assert not audit.exists()
        assert not env["kernel"].exists()
        assert not env["flag"].exists()

    def test_json_flag_emits_parseable_json_only(self, env, capsys):
        assert _run(env, "--json") == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert set(parsed) == {"generated_at", "kill_switch", "flags",
                               "automation_control", "lead_holds"}

    def test_default_mode_prints_human_lines_then_json(self, env, capsys):
        assert _run(env) == 0
        out = capsys.readouterr().out
        assert out.startswith("kill switch:")
        assert "automation-control kernel:" in out
        assert "lead holds:" in out
        # the JSON block still parses off the tail
        json.loads(out[out.index("{"):])
