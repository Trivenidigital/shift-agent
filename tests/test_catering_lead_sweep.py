"""Tests for the catering lead TTL expiry sweep (PR-A lifecycle guard).

Two layers:
  * `find_expired_awaiting_leads` — pure stdlib logic, runs cross-platform (mirrors
    tests/test_proposal_sweep.py). conftest puts src/platform on sys.path.
  * `catering-lead-ttl-sweep` — static-invariant scans (cross-platform) + a subprocess
    integration test (Linux-only, fcntl) that drives the real transition + audit chokepoint.
"""
from __future__ import annotations

import configparser
import json
import os
import platform
import py_compile
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from catering_lead_sweep import (
    find_expired_awaiting_leads, find_expired_qualifying_leads,
    CATERING_LEAD_TTL_DAYS, CATERING_QUALIFYING_STALE_HOURS,
)

NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
_REPO = Path(__file__).resolve().parent.parent
_SWEEP = _REPO / "src" / "agents" / "catering" / "scripts" / "catering-lead-ttl-sweep"


def _lead(status: str, updated_days_ago=None, lead_id="L1"):
    updated_at = NOW - timedelta(days=updated_days_ago) if updated_days_ago is not None else None
    return SimpleNamespace(lead_id=lead_id, status=status, updated_at=updated_at)


# ── find_expired_awaiting_leads ─────────────────────────────────────────────
def test_expired_awaiting_included():
    assert find_expired_awaiting_leads([_lead("AWAITING_OWNER_APPROVAL", 30)], NOW, 21) == ["L1"]


def test_fresh_awaiting_excluded():
    assert find_expired_awaiting_leads([_lead("AWAITING_OWNER_APPROVAL", 5)], NOW, 21) == []


def test_boundary_exactly_ttl_included():
    # updated_at exactly ttl days ago is expired (<= cutoff semantics).
    assert find_expired_awaiting_leads([_lead("AWAITING_OWNER_APPROVAL", 21)], NOW, 21) == ["L1"]


def test_non_awaiting_excluded_even_if_ancient():
    leads = [
        _lead("SENT_TO_CUSTOMER", 999, "L1"),
        _lead("CUSTOMER_FINALIZED", 999, "L2"),
        _lead("STALE", 999, "L3"),
        _lead("CLOSED", 999, "L4"),
        _lead("OWNER_APPROVED", 999, "L5"),
    ]
    assert find_expired_awaiting_leads(leads, NOW, 21) == []


def test_awaiting_without_updated_at_excluded():
    # Defensive: missing updated_at is skipped, not crashed on.
    assert find_expired_awaiting_leads([_lead("AWAITING_OWNER_APPROVAL", None)], NOW, 21) == []


def test_expired_returns_sorted_ids():
    leads = [
        _lead("AWAITING_OWNER_APPROVAL", 30, "L3"),
        _lead("AWAITING_OWNER_APPROVAL", 40, "L1"),
        _lead("AWAITING_OWNER_APPROVAL", 5, "L2"),   # fresh, excluded
    ]
    assert find_expired_awaiting_leads(leads, NOW, 21) == ["L1", "L3"]


def test_empty_store():
    assert find_expired_awaiting_leads([], NOW, 21) == []


def test_default_ttl_is_21_days():
    assert CATERING_LEAD_TTL_DAYS == 21


# ── find_expired_qualifying_leads ───────────────────────────────────────────
# A QUALIFYING lead waits on the CUSTOMER, not the owner: we asked an intake
# question and they never came back. Before this sweep such a lead was immortal
# AND invisible — a WhatsApp-only owner never sees the Studio's QUALIFYING
# counter, so nobody was ever told the enquiry existed.
def _qualifying(hours_ago=None, lead_id="L1"):
    updated_at = NOW - timedelta(hours=hours_ago) if hours_ago is not None else None
    return SimpleNamespace(lead_id=lead_id, status="QUALIFYING", updated_at=updated_at)


def test_stale_qualifying_included():
    assert find_expired_qualifying_leads([_qualifying(100)], NOW, 72) == ["L1"]


def test_fresh_qualifying_excluded():
    assert find_expired_qualifying_leads([_qualifying(4)], NOW, 72) == []


def test_qualifying_boundary_exactly_at_window_included():
    assert find_expired_qualifying_leads([_qualifying(72)], NOW, 72) == ["L1"]


def test_qualifying_without_updated_at_excluded():
    assert find_expired_qualifying_leads([_qualifying(None)], NOW, 72) == []


def test_qualifying_sweep_ignores_every_other_status():
    leads = [
        _lead("AWAITING_OWNER_APPROVAL", 999, "L1"),
        _lead("SENT_TO_CUSTOMER", 999, "L2"),
        _lead("STALE", 999, "L3"),
    ]
    assert find_expired_qualifying_leads(leads, NOW, 72) == []


def test_awaiting_sweep_ignores_qualifying():
    """The two windows are disjoint by status, so a QUALIFYING lead can never be
    expired on the (much longer) owner-side TTL by accident."""
    assert find_expired_awaiting_leads([_qualifying(24 * 999)], NOW, 21) == []


def test_qualifying_expired_returns_sorted_ids():
    leads = [_qualifying(100, "L3"), _qualifying(200, "L1"), _qualifying(1, "L2")]
    assert find_expired_qualifying_leads(leads, NOW, 72) == ["L1", "L3"]


def test_default_qualifying_window_is_72_hours():
    assert CATERING_QUALIFYING_STALE_HOURS == 72


def test_the_qualifying_window_is_much_shorter_than_the_owner_side_ttl():
    """Different waits, different windows: three weeks is right for an owner who
    has not got to a lead, and absurd for a customer who stopped replying."""
    assert CATERING_QUALIFYING_STALE_HOURS < CATERING_LEAD_TTL_DAYS * 24


# ── static-invariant scans of the sweep script (cross-platform) ─────────────
def test_sweep_script_compiles():
    py_compile.compile(str(_SWEEP), doraise=True)


def test_sweep_gated_off_by_default_env_flag():
    t = _SWEEP.read_text(encoding="utf-8")
    assert "CATERING_LEAD_TTL_SWEEP_ENABLED" in t
    assert "def _enabled" in t
    # dormant unless armed
    assert "if not _enabled()" in t


def test_sweep_uses_legal_terminal_transition_via_chokepoint():
    t = _SWEEP.read_text(encoding="utf-8")
    assert 'TERMINAL_STATUS = "STALE"' in t
    assert "is_catering_transition_allowed" in t
    assert "CateringLeadStatusChange" in t
    assert "find_expired_awaiting_leads" in t
    assert "find_expired_qualifying_leads" in t
    assert "atomic_write_json" in t


def test_sweep_owner_alert_only_plain_text_and_no_money():
    t = _SWEEP.read_text(encoding="utf-8")
    assert "shift-agent-notify-owner" in t
    # §12b dispatched/delivered structured logs around the alert.
    assert "catering_lead_ttl_alert_dispatched" in t
    assert "catering_lead_ttl_alert_delivered" in t
    low = t.lower()
    assert "deposit" not in low and "stripe" not in low and "payment" not in low
    assert "send-catering-ack" not in t, "the sweep alerts the OWNER, never the customer"


def test_sweep_never_fails_its_timer():
    """Watchdog discipline: every exit path returns 0 so a bad cycle cannot put
    the timer unit into failed state. Asserted against the code, not against the
    docstring that describes the code."""
    t = _SWEEP.read_text(encoding="utf-8")
    assert "except Exception" in t
    assert "sys.exit(main())" in t
    # No non-zero return anywhere in the script.
    assert not re.search(r"^\s*return [1-9]", t, re.M)
    assert not re.search(r"sys\.exit\([1-9]", t)


# ── the timer that runs it (P1: the sweep was unschedulable in prod) ─────────
#
# The unit files did not exist, so shift-agent-deploy.sh installed the sweep
# binary with nothing to run it. The old version of this section asserted a
# sentence from the sweep's own docstring, which could not fail on a missing
# unit. These assertions are anchored on the real filenames instead.

_UNIT_DIR = _REPO / "src" / "agents" / "catering" / "systemd"
_SERVICE = _UNIT_DIR / "catering-lead-ttl-sweep.service"
_TIMER = _UNIT_DIR / "catering-lead-ttl-sweep.timer"
_DEPLOY = _REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
_SMOKE = _REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh"


def _unit(path: Path) -> configparser.ConfigParser:
    """systemd units are INI-ish: directives repeat and values contain '%', so
    disable interpolation and keep duplicates out of our way by reading the
    keys we assert on individually."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


def test_sweep_systemd_units_exist():
    assert _SERVICE.exists(), "sweep binary ships with no unit to run it"
    assert _TIMER.exists(), "service with no timer is still unschedulable"


def test_service_runs_the_sweep_binary_as_the_agent_user():
    unit = _unit(_SERVICE)
    exec_start = unit["Service"]["ExecStart"]
    assert exec_start.endswith("/usr/local/bin/catering-lead-ttl-sweep")
    assert unit["Service"]["Type"] == "oneshot"
    assert unit["Service"]["User"] == "shift-agent"
    assert unit["Service"]["Group"] == "shift-agent"
    assert unit["Service"]["EnvironmentFile"] == "/opt/shift-agent/.env"
    assert unit["Service"]["NoNewPrivileges"] == "true"
    assert unit["Service"]["ProtectSystem"] == "strict"
    assert unit["Service"]["ReadWritePaths"] == "/opt/shift-agent"


def test_service_does_not_arm_the_sweep():
    """The unit must not set the enable flag: scheduling the sweep and arming it
    are separate operator decisions, and the store is live."""
    body = _SERVICE.read_text(encoding="utf-8")
    assert "CATERING_LEAD_TTL_SWEEP_ENABLED" not in body.split("[Service]", 1)[1]


def test_timer_has_a_schedule_and_installs():
    unit = _unit(_TIMER)
    assert unit["Timer"]["OnCalendar"] == "*-*-* 03:20:00"
    assert unit["Timer"]["Unit"] == "catering-lead-ttl-sweep.service"
    assert unit["Timer"]["Persistent"] == "true"
    assert unit["Install"]["WantedBy"] == "timers.target"
    assert unit["Unit"]["Requires"] == "catering-lead-ttl-sweep.service"


def test_deploy_installs_and_enables_the_timer():
    deploy = _DEPLOY.read_text(encoding="utf-8")
    assert "install -m 644 src/agents/catering/systemd/*.service" in deploy
    assert "install -m 644 src/agents/catering/systemd/*.timer" in deploy
    assert "systemctl enable --now catering-lead-ttl-sweep.timer" in deploy


def test_smoke_test_checks_the_timer_is_enabled_and_the_units_parse():
    smoke = _SMOKE.read_text(encoding="utf-8")
    assert "catering-lead-ttl-sweep.timer" in smoke
    assert "/etc/systemd/system/catering-lead-ttl-sweep.service" in smoke
    assert "/etc/systemd/system/catering-lead-ttl-sweep.timer" in smoke


# ── subprocess integration (Linux-only, fcntl) ──────────────────────────────
_LINUX_ONLY = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="sweep depends on safe_io which uses fcntl (Linux only)",
)


def _lead_row(lead_id, status, updated_at, code="#ABCDE"):
    return {
        "lead_id": lead_id, "status": status,
        "customer_phone": "+19045550104", "customer_name": "",
        "raw_inquiry": "x", "original_message_id": f"m-{lead_id}",
        "owner_approval_code": code,
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": updated_at,
    }


def _run_sweep(tmp_path, *, enabled, extra_env=None):
    leads_path = tmp_path / "catering-leads.json"
    log_path = tmp_path / "decisions.log"
    env = {
        **os.environ,
        "PYTHONPATH": str(_REPO / "src" / "platform"),
        "SHIFT_AGENT_CATERING_LEADS_PATH": str(leads_path),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(log_path),
        "SHIFT_AGENT_CONFIG_PATH": str(tmp_path / "no-such-config.yaml"),  # UTC fallback
    }
    if enabled:
        env["CATERING_LEAD_TTL_SWEEP_ENABLED"] = "1"
    else:
        env.pop("CATERING_LEAD_TTL_SWEEP_ENABLED", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(_SWEEP)], capture_output=True, text=True,
                          env=env, timeout=30)


def _seed(tmp_path):
    leads_path = tmp_path / "catering-leads.json"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=1)).isoformat()
    leads_path.write_text(json.dumps({
        "schema_version": 1,
        "leads": [
            _lead_row("L0001", "AWAITING_OWNER_APPROVAL", "2020-01-01T00:00:00+00:00"),  # stale
            _lead_row("L0002", "AWAITING_OWNER_APPROVAL", recent),                       # fresh
            _lead_row("L0003", "SENT_TO_CUSTOMER", "2020-01-01T00:00:00+00:00"),         # wrong status
            # QUALIFYING: one abandoned mid-intake, one that answered an hour ago.
            _lead_row("L0004", "QUALIFYING", (now - timedelta(hours=200)).isoformat()),
            _lead_row("L0005", "QUALIFYING", (now - timedelta(hours=1)).isoformat()),
        ],
    }), encoding="utf-8")
    return leads_path


def _statuses(leads_path):
    doc = json.loads(leads_path.read_text(encoding="utf-8"))
    return {l["lead_id"]: l["status"] for l in doc["leads"]}


@_LINUX_ONLY
def test_cli_expires_only_stale_awaiting_lead(tmp_path):
    leads_path = _seed(tmp_path)
    r = _run_sweep(tmp_path, enabled=True)
    assert r.returncode == 0, r.stderr
    statuses = _statuses(leads_path)
    assert statuses["L0001"] == "STALE", "old AWAITING_OWNER_APPROVAL lead expires"
    assert statuses["L0002"] == "AWAITING_OWNER_APPROVAL", "fresh lead untouched"
    assert statuses["L0003"] == "SENT_TO_CUSTOMER", "non-awaiting lead untouched"
    # §12b: a catering_lead_status_change audit row (actor=system) for the expiry.
    rows = [json.loads(l) for l in (tmp_path / "decisions.log").read_text(encoding="utf-8").splitlines() if l.strip()]
    changes = [x for x in rows if x.get("type") == "catering_lead_status_change" and x.get("lead_id") == "L0001"]
    assert len(changes) == 1
    assert changes[0]["from_status"] == "AWAITING_OWNER_APPROVAL"
    assert changes[0]["to_status"] == "STALE"
    assert changes[0]["actor"] == "system"


@_LINUX_ONLY
def test_cli_idempotent_second_run_is_noop(tmp_path):
    leads_path = _seed(tmp_path)
    _run_sweep(tmp_path, enabled=True)
    _run_sweep(tmp_path, enabled=True)
    # Exactly one status-change row for L0001 across two runs (STALE is terminal).
    rows = [json.loads(l) for l in (tmp_path / "decisions.log").read_text(encoding="utf-8").splitlines() if l.strip()]
    changes = [x for x in rows if x.get("type") == "catering_lead_status_change" and x.get("lead_id") == "L0001"]
    assert len(changes) == 1, "terminal transition fires at most once — no re-expiry"


@_LINUX_ONLY
def test_cli_flag_off_is_a_noop(tmp_path):
    leads_path = _seed(tmp_path)
    r = _run_sweep(tmp_path, enabled=False)
    assert r.returncode == 0
    assert _statuses(leads_path)["L0001"] == "AWAITING_OWNER_APPROVAL", "dormant unless armed"
    assert not (tmp_path / "decisions.log").exists() or \
        (tmp_path / "decisions.log").read_text(encoding="utf-8").strip() == ""


@_LINUX_ONLY
def test_cli_within_ttl_lead_untouched(tmp_path):
    leads_path = _seed(tmp_path)
    # A huge TTL keeps even the 2020 lead within window → nothing expires.
    r = _run_sweep(tmp_path, enabled=True, extra_env={"CATERING_LEAD_TTL_DAYS": "100000"})
    assert r.returncode == 0
    assert _statuses(leads_path)["L0001"] == "AWAITING_OWNER_APPROVAL"


# ── in-process drive of the sweep's main() (runs everywhere) ────────────────
# The subprocess cells above are Linux-gated because a fresh interpreter cannot
# import safe_io on Windows. Loading main() in-process behind the fcntl stub —
# the pattern tests/test_amend_catering_lead.py uses — pins the QUALIFYING arm on
# the dev box too, which matters because that arm is the new behavior here.
def _drive_sweep(tmp_path, monkeypatch, *, config_text=None):
    from fixtures_fleet import ensure_fcntl_stub, load_script

    ensure_fcntl_stub()
    leads_path = _seed(tmp_path)
    log_path = tmp_path / "decisions.log"
    config_path = tmp_path / "config.yaml"
    if config_text is not None:
        config_path.write_text(config_text, encoding="utf-8")

    monkeypatch.setenv("CATERING_LEAD_TTL_SWEEP_ENABLED", "1")
    monkeypatch.setenv("SHIFT_AGENT_CATERING_LEADS_PATH", str(leads_path))
    monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(log_path))
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(config_path))
    mod = load_script("catering_lead_ttl_sweep_under_test", _SWEEP)
    mod.LEADS_PATH = leads_path
    mod.LEADS_LOCK = Path(str(leads_path) + ".lock")
    mod.LOG_PATH = log_path
    mod.CONFIG_PATH = config_path

    alerts: list[tuple] = []
    monkeypatch.setattr(mod, "_alert_owner",
                        lambda lead_id, window, **kw: alerts.append((lead_id, window, kw)))
    old_argv = sys.argv
    sys.argv = ["catering-lead-ttl-sweep"]
    try:
        rc = mod.main()
    finally:
        sys.argv = old_argv
    return rc, leads_path, log_path, alerts


def test_qualifying_lead_expires_with_an_owner_alert(tmp_path, monkeypatch):
    """The lead nobody could see: QUALIFYING, customer gone, owner never told."""
    rc, leads_path, log_path, alerts = _drive_sweep(tmp_path, monkeypatch)

    assert rc == 0
    statuses = _statuses(leads_path)
    assert statuses["L0004"] == "STALE", "a QUALIFYING lead past its window expires"
    assert statuses["L0005"] == "QUALIFYING", "one that answered an hour ago is untouched"
    assert statuses["L0001"] == "STALE", "the owner-side TTL still behaves as before"
    assert statuses["L0002"] == "AWAITING_OWNER_APPROVAL"
    assert statuses["L0003"] == "SENT_TO_CUSTOMER"

    alerted = {lead_id: kw for lead_id, _window, kw in alerts}
    assert set(alerted) == {"L0001", "L0004"}, "§12b: one owner alert per expiry"
    assert alerted["L0004"]["qualifying"] is True
    assert alerted["L0001"].get("qualifying", False) is False

    rows = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    changes = {x["lead_id"]: x for x in rows
               if x.get("type") == "catering_lead_status_change"}
    assert changes["L0004"]["from_status"] == "QUALIFYING"
    assert changes["L0004"]["to_status"] == "STALE"
    assert changes["L0004"]["actor"] == "system"
    assert "ttl_expired_qualifying" in changes["L0004"]["reason"]
    assert "ttl_expired_awaiting_owner_approval" in changes["L0001"]["reason"]


def test_qualifying_window_is_read_from_config(tmp_path, monkeypatch):
    """`catering.qualifying_stale_after_hours` is the per-customer knob; a wide
    value keeps a 200h-old intake alive while the owner-side TTL is unaffected."""
    import yaml

    config_text = yaml.safe_dump({
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc", "timezone": "UTC"},
        "owner": {"name": "O", "phone": "+19045550100"},
        "limits": {}, "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True, "qualifying_stale_after_hours": 10000},
    })
    rc, leads_path, _log, alerts = _drive_sweep(tmp_path, monkeypatch, config_text=config_text)

    assert rc == 0
    assert _statuses(leads_path)["L0004"] == "QUALIFYING"
    assert [lead_id for lead_id, _w, _k in alerts] == ["L0001"]


def test_qualifying_sweep_is_dormant_unless_armed(tmp_path, monkeypatch):
    monkeypatch.delenv("CATERING_LEAD_TTL_SWEEP_ENABLED", raising=False)
    from fixtures_fleet import ensure_fcntl_stub, load_script

    ensure_fcntl_stub()
    leads_path = _seed(tmp_path)
    mod = load_script("catering_lead_ttl_sweep_disarmed", _SWEEP)
    mod.LEADS_PATH = leads_path
    old_argv = sys.argv
    sys.argv = ["catering-lead-ttl-sweep"]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = old_argv
    assert _statuses(leads_path)["L0004"] == "QUALIFYING", "same flag gates both arms"


@_LINUX_ONLY
def test_cli_expires_the_abandoned_qualifying_lead_and_alerts(tmp_path):
    """The lead nobody could see: QUALIFYING, customer gone, owner never told."""
    leads_path = _seed(tmp_path)
    r = _run_sweep(tmp_path, enabled=True)
    assert r.returncode == 0, r.stderr

    statuses = _statuses(leads_path)
    assert statuses["L0004"] == "STALE", "a QUALIFYING lead past its window expires"
    assert statuses["L0005"] == "QUALIFYING", "one that answered an hour ago is untouched"

    rows = [json.loads(l) for l in (tmp_path / "decisions.log").read_text(encoding="utf-8").splitlines() if l.strip()]
    changes = [x for x in rows if x.get("type") == "catering_lead_status_change"
               and x.get("lead_id") == "L0004"]
    assert len(changes) == 1
    assert changes[0]["from_status"] == "QUALIFYING"
    assert changes[0]["to_status"] == "STALE"
    assert changes[0]["actor"] == "system"
    assert "ttl_expired_qualifying" in changes[0]["reason"]
    # §12b: the owner is told at the write site, and the reason distinguishes the
    # two windows so the alert text can too.
    assert "catering_lead_ttl_alert_dispatched lead=L0004" in r.stderr


@_LINUX_ONLY
def test_cli_qualifying_expiry_is_idempotent(tmp_path):
    _seed(tmp_path)
    _run_sweep(tmp_path, enabled=True)
    _run_sweep(tmp_path, enabled=True)
    rows = [json.loads(l) for l in (tmp_path / "decisions.log").read_text(encoding="utf-8").splitlines() if l.strip()]
    changes = [x for x in rows if x.get("type") == "catering_lead_status_change"
               and x.get("lead_id") == "L0004"]
    assert len(changes) == 1, "STALE is terminal — no re-expiry, no repeat alert"


@_LINUX_ONLY
def test_cli_qualifying_sweep_obeys_the_same_arming_flag(tmp_path):
    leads_path = _seed(tmp_path)
    r = _run_sweep(tmp_path, enabled=False)
    assert r.returncode == 0
    assert _statuses(leads_path)["L0004"] == "QUALIFYING", "dormant unless armed"


@_LINUX_ONLY
def test_cli_qualifying_window_comes_from_config(tmp_path):
    """`catering.qualifying_stale_after_hours` is the per-customer knob; a wide
    value keeps a 200h-old intake alive."""
    import yaml

    leads_path = _seed(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc", "timezone": "UTC"},
        "owner": {"name": "O", "phone": "+19045550100"},
        "limits": {}, "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True, "qualifying_stale_after_hours": 10000},
    }), encoding="utf-8")

    r = _run_sweep(tmp_path, enabled=True,
                   extra_env={"SHIFT_AGENT_CONFIG_PATH": str(config)})
    assert r.returncode == 0, r.stderr
    assert _statuses(leads_path)["L0004"] == "QUALIFYING"
