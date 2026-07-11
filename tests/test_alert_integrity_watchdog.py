"""alert-integrity-watchdog — audit-log freshness (§12a) + dropped-alert growth
(§12b dead-letter). In-process tests over run_watchdog with an injected notify
func (the script uses an fcntl-less import fallback so this runs cross-platform).
"""
from __future__ import annotations

import importlib.machinery
import json
import os
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "src" / "platform" / "scripts" / "alert-integrity-watchdog"

NOW = "2026-06-05T12:00:00Z"


def load_module():
    loader = importlib.machinery.SourceFileLoader("alert_integrity_watchdog", str(SCRIPT))
    return loader.load_module()


def _recording_notify():
    calls: list[dict] = []

    def fn(**kw):
        calls.append(kw)
        return True

    return fn, calls


def _failing_notify():
    calls: list[dict] = []

    def fn(**kw):
        calls.append(kw)
        return False

    return fn, calls


def _fresh_decisions(module, tmp_path, minutes_old=5):
    d = tmp_path / "decisions.log"
    d.write_text("{}\n", encoding="utf-8")
    now = module.parse_utc(NOW)
    t = now.timestamp() - minutes_old * 60
    os.utime(d, (t, t))
    return d


# ── Check 1: decisions.log freshness (§12a) ───────────────────────────────────

def test_stale_decisions_log_pages_owner(tmp_path):
    module = load_module()
    d = _fresh_decisions(module, tmp_path, minutes_old=200)  # > 120 threshold
    notify, calls = _recording_notify()
    r = module.run_watchdog(
        decisions_log_path=d,
        notify_failed_log_path=tmp_path / "notify-failed.log",
        state_path=tmp_path / "state.json",
        now=module.parse_utc(NOW),
        freshness_threshold_minutes=120,
        notify_func=notify,
    )
    assert r["decisions_log_status"] == "stale"
    assert r["exit_code"] == 0
    assert any(c["title"] == "Audit log STALE" for c in calls)
    evs = [e["event"] for e in r["events"]]
    assert "alert_integrity_decisions_log_stale_alert_dispatched" in evs
    assert "alert_integrity_decisions_log_stale_alert_delivered" in evs


def test_fresh_decisions_log_is_silent(tmp_path):
    module = load_module()
    d = _fresh_decisions(module, tmp_path, minutes_old=5)
    notify, calls = _recording_notify()
    r = module.run_watchdog(
        decisions_log_path=d,
        notify_failed_log_path=tmp_path / "notify-failed.log",
        state_path=tmp_path / "state.json",
        now=module.parse_utc(NOW),
        freshness_threshold_minutes=120,
        notify_func=notify,
    )
    assert r["decisions_log_status"] == "fresh"
    assert calls == []
    assert any(e["event"] == "alert_integrity_ok" for e in r["events"])


def test_missing_decisions_log_pages_owner(tmp_path):
    module = load_module()
    notify, calls = _recording_notify()
    r = module.run_watchdog(
        decisions_log_path=tmp_path / "nope.log",
        notify_failed_log_path=tmp_path / "notify-failed.log",
        state_path=tmp_path / "state.json",
        now=module.parse_utc(NOW),
        notify_func=notify,
    )
    assert r["decisions_log_status"] == "missing"
    assert any(c["title"] == "Audit log MISSING" for c in calls)


# ── Check 2: notify-failed.log growth (§12b dead-letter) ──────────────────────

def test_notify_failed_growth_pages_and_persists_offset(tmp_path):
    module = load_module()
    d = _fresh_decisions(module, tmp_path)
    nf = tmp_path / "notify-failed.log"
    nf.write_text(
        json.dumps({"ts": "t1", "source": "send-daily-brief", "title": "x"}) + "\n"
        + json.dumps({"ts": "t2", "source": "eod-reconcile", "title": "y"}) + "\n",
        encoding="utf-8",
    )
    state = tmp_path / "state.json"
    notify, calls = _recording_notify()
    r = module.run_watchdog(
        decisions_log_path=d, notify_failed_log_path=nf, state_path=state,
        now=module.parse_utc(NOW), freshness_threshold_minutes=120, notify_func=notify,
    )
    assert r["notify_failed_status"] == "growth"
    assert r["notify_failed_new_count"] == 2
    assert any(c["title"] == "Owner alerts DROPPED" for c in calls)
    assert "2 owner alert" in calls[0]["message"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["notify_failed_offset"] == nf.stat().st_size


def test_notify_failed_no_growth_is_silent_on_second_run(tmp_path):
    module = load_module()
    d = _fresh_decisions(module, tmp_path)
    nf = tmp_path / "notify-failed.log"
    nf.write_text(json.dumps({"ts": "t1", "source": "x", "title": "y"}) + "\n", encoding="utf-8")
    state = tmp_path / "state.json"
    kw = dict(decisions_log_path=d, notify_failed_log_path=nf, state_path=state,
              now=module.parse_utc(NOW), freshness_threshold_minutes=120)
    n1, c1 = _recording_notify()
    r1 = module.run_watchdog(notify_func=n1, **kw)
    assert r1["notify_failed_status"] == "growth"  # first run surfaces the backlog
    n2, c2 = _recording_notify()
    r2 = module.run_watchdog(notify_func=n2, **kw)
    assert r2["notify_failed_status"] == "ok"
    assert c2 == []


def test_notify_failed_growth_reports_only_new_lines(tmp_path):
    module = load_module()
    d = _fresh_decisions(module, tmp_path)
    nf = tmp_path / "notify-failed.log"
    nf.write_text(json.dumps({"ts": "t1", "source": "a", "title": "1"}) + "\n", encoding="utf-8")
    state = tmp_path / "state.json"
    kw = dict(decisions_log_path=d, notify_failed_log_path=nf, state_path=state,
              now=module.parse_utc(NOW), freshness_threshold_minutes=120)
    n1, _ = _recording_notify()
    module.run_watchdog(notify_func=n1, **kw)  # baseline over line 1
    with nf.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "t2", "source": "b", "title": "SECOND"}) + "\n")
    n2, c2 = _recording_notify()
    r2 = module.run_watchdog(notify_func=n2, **kw)
    assert r2["notify_failed_status"] == "growth"
    assert r2["notify_failed_new_count"] == 1
    assert "SECOND" in c2[0]["message"]


def test_notify_failed_rotation_to_empty_is_silent_and_resets_offset(tmp_path):
    module = load_module()
    d = _fresh_decisions(module, tmp_path)
    nf = tmp_path / "notify-failed.log"
    nf.write_text("", encoding="utf-8")  # rotated -> empty
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"notify_failed_offset": 5000}), encoding="utf-8")
    notify, calls = _recording_notify()
    r = module.run_watchdog(
        decisions_log_path=d, notify_failed_log_path=nf, state_path=state,
        now=module.parse_utc(NOW), freshness_threshold_minutes=120, notify_func=notify,
    )
    assert r["notify_failed_status"] == "ok"
    assert calls == []
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["notify_failed_offset"] == 0


# ── Delivery-failure contract (§12b) ──────────────────────────────────────────

def test_delivery_failure_surfaces_dependency_down_exit(tmp_path):
    module = load_module()
    notify, calls = _failing_notify()
    r = module.run_watchdog(
        decisions_log_path=tmp_path / "nope.log",   # missing -> dispatches a page
        notify_failed_log_path=tmp_path / "nf.log",
        state_path=tmp_path / "state.json",
        now=module.parse_utc(NOW),
        notify_func=notify,
    )
    assert r["delivery_failed"] is True
    assert r["exit_code"] == 6
    evs = [e["event"] for e in r["events"]]
    assert "alert_integrity_decisions_log_stale_alert_delivery_failed" in evs


# ── deploy wiring: units exist + installed + enabled like siblings ────────────

def test_units_exist_in_platform_systemd():
    svc = REPO / "src" / "platform" / "systemd" / "alert-integrity-watchdog.service"
    tmr = REPO / "src" / "platform" / "systemd" / "alert-integrity-watchdog.timer"
    assert "ExecStart=/usr/local/bin/alert-integrity-watchdog" in svc.read_text(encoding="utf-8")
    timer = tmr.read_text(encoding="utf-8")
    assert "OnUnitActiveSec=15min" in timer
    assert "Unit=alert-integrity-watchdog.service" in timer
    assert "WantedBy=timers.target" in timer


def test_deploy_installs_platform_timers_and_enables_watchdog():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")
    # The platform *.timer install line must exist (previously only *.service shipped).
    assert "install -m 644 src/platform/systemd/*.timer" in deploy
    assert "systemctl enable --now alert-integrity-watchdog.timer" in deploy
    assert "alert-integrity-watchdog.timer" in smoke
