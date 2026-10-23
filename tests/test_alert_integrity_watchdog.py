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


# ── PR3 alert hygiene: stable STALE body + 6h re-page + second dead-letter ────

def _stale_kw(module, tmp_path, now_iso):
    d = tmp_path / "decisions.log"
    if not d.exists():
        d.write_text("{}\n", encoding="utf-8")
        t = module.parse_utc(NOW).timestamp() - 200 * 60  # stale at NOW
        os.utime(d, (t, t))
    return dict(
        decisions_log_path=d,
        notify_failed_log_path=tmp_path / "notify-failed.log",
        state_notify_failed_log_path=tmp_path / "state-notify-failed.log",
        state_path=tmp_path / "state.json",
        now=module.parse_utc(now_iso),
        freshness_threshold_minutes=120,
    )


def _stale_pages(calls):
    return [c for c in calls if c["title"] == "Audit log STALE"]


def test_stale_pages_once_then_not_within_6h(tmp_path):
    module = load_module()
    n1, c1 = _recording_notify()
    r1 = module.run_watchdog(notify_func=n1, **_stale_kw(module, tmp_path, NOW))
    assert len(_stale_pages(c1)) == 1
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["last_stale_page_at"].startswith("2026-06-05T12:00:00")
    # 15 min and 5h59m later: still stale, no re-page, suppression is traceable.
    for later in ("2026-06-05T12:15:00Z", "2026-06-05T17:59:00Z"):
        n, c = _recording_notify()
        r = module.run_watchdog(notify_func=n, **_stale_kw(module, tmp_path, later))
        assert r["decisions_log_status"] == "stale"
        assert _stale_pages(c) == []
        evs = [e["event"] for e in r["events"]]
        assert "alert_integrity_decisions_log_stale_repage_suppressed" in evs
        assert "alert_integrity_decisions_log_stale" in evs
    assert r1["exit_code"] == 0


def test_stale_repages_after_6h(tmp_path):
    module = load_module()
    n1, _ = _recording_notify()
    module.run_watchdog(notify_func=n1, **_stale_kw(module, tmp_path, NOW))
    n2, c2 = _recording_notify()
    module.run_watchdog(notify_func=n2, **_stale_kw(module, tmp_path, "2026-06-05T18:00:00Z"))
    assert len(_stale_pages(c2)) == 1
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["last_stale_page_at"].startswith("2026-06-05T18:00:00")


def test_stale_page_body_is_stable_and_has_no_minutes_count(tmp_path):
    module = load_module()
    n1, c1 = _recording_notify()
    module.run_watchdog(notify_func=n1, **_stale_kw(module, tmp_path, NOW))
    n2, c2 = _recording_notify()
    module.run_watchdog(notify_func=n2, **_stale_kw(module, tmp_path, "2026-06-06T02:00:00Z"))
    body1, body2 = _stale_pages(c1)[0]["message"], _stale_pages(c2)[0]["message"]
    assert body1 == body2  # one dedupe key across repeats
    assert "200" not in body1 and "1040" not in body2  # the age never reaches the body
    # ...but the age IS in the structured event.
    r = module.run_watchdog(notify_func=_recording_notify()[0],
                            **_stale_kw(module, tmp_path, "2026-06-06T02:05:00Z"))
    ev = [e for e in r["events"] if e["event"] == "alert_integrity_decisions_log_stale"][0]
    assert ev["decisions_log_age_minutes"] == 1045.0


def test_failed_stale_page_does_not_arm_repage_window(tmp_path):
    module = load_module()
    nf, _ = _failing_notify()
    r = module.run_watchdog(notify_func=nf, **_stale_kw(module, tmp_path, NOW))
    assert r["exit_code"] == 6
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "last_stale_page_at" not in saved
    n2, c2 = _recording_notify()
    module.run_watchdog(notify_func=n2, **_stale_kw(module, tmp_path, "2026-06-05T12:15:00Z"))
    assert len(_stale_pages(c2)) == 1


def test_fresh_log_clears_last_stale_page_at(tmp_path):
    module = load_module()
    n1, _ = _recording_notify()
    module.run_watchdog(notify_func=n1, **_stale_kw(module, tmp_path, NOW))
    d = tmp_path / "decisions.log"
    t = module.parse_utc("2026-06-05T12:30:00Z").timestamp()
    os.utime(d, (t, t))  # writer recovered
    n2, c2 = _recording_notify()
    r2 = module.run_watchdog(notify_func=n2, **_stale_kw(module, tmp_path, "2026-06-05T12:35:00Z"))
    assert r2["decisions_log_status"] == "fresh" and c2 == []
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "last_stale_page_at" not in saved
    # Stale again 1h later -> pages immediately (window was cleared).
    t_old = module.parse_utc("2026-06-05T12:30:00Z").timestamp()
    os.utime(d, (t_old, t_old))
    n3, c3 = _recording_notify()
    module.run_watchdog(notify_func=n3, **_stale_kw(module, tmp_path, "2026-06-05T14:45:00Z"))
    assert len(_stale_pages(c3)) == 1


def test_state_dead_letter_growth_pages_with_own_offset(tmp_path):
    module = load_module()
    d = _fresh_decisions(module, tmp_path)
    logs_nf = tmp_path / "notify-failed.log"
    state_nf = tmp_path / "state-notify-failed.log"
    state_nf.write_text(
        json.dumps({"ts": "t1", "message": "NOTIFY-OWNER-DROP", "results": []}) + "\n",
        encoding="utf-8",
    )
    state = tmp_path / "state.json"
    kw = dict(decisions_log_path=d, notify_failed_log_path=logs_nf,
              state_notify_failed_log_path=state_nf, state_path=state,
              now=module.parse_utc(NOW), freshness_threshold_minutes=120)
    n1, c1 = _recording_notify()
    r1 = module.run_watchdog(notify_func=n1, **kw)
    assert r1["notify_failed_status"] == "growth"
    assert r1["state_notify_failed_new_count"] == 1
    assert r1["notify_failed_new_count"] == 0
    drops = [c for c in c1 if c["title"] == "Owner alerts DROPPED"]
    assert len(drops) == 1
    assert "NOTIFY-OWNER-DROP" in drops[0]["message"]
    assert f"1 owner alert(s) in {state_nf}" in drops[0]["message"]
    ev = [e for e in r1["events"] if e["event"] == "alert_integrity_notify_failed_growth"][0]
    assert ev["new_lines_per_file"] == {str(logs_nf): 0, str(state_nf): 1}
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["state_notify_failed_offset"] == state_nf.stat().st_size
    assert saved["notify_failed_offset"] == 0
    # No growth -> silent.
    n2, c2 = _recording_notify()
    assert module.run_watchdog(notify_func=n2, **kw)["notify_failed_status"] == "ok"
    assert c2 == []


def test_both_dead_letters_growing_is_one_page_with_per_file_counts(tmp_path):
    """One failed alert can land in BOTH files (safe_io helper + notify-owner
    binary): counts are reported per file, never summed, and the run pages once."""
    module = load_module()
    d = _fresh_decisions(module, tmp_path)
    logs_nf = tmp_path / "notify-failed.log"
    state_nf = tmp_path / "state-notify-failed.log"
    logs_nf.write_text(
        json.dumps({"ts": "t1", "source": "a", "title": "LOGS-DROP"}) + "\n"
        + json.dumps({"ts": "t2", "source": "b", "title": "LOGS-DROP-2"}) + "\n",
        encoding="utf-8",
    )
    state_nf.write_text(json.dumps({"ts": "t1", "message": "BIN-DROP", "results": []}) + "\n",
                        encoding="utf-8")
    notify, calls = _recording_notify()
    r = module.run_watchdog(
        decisions_log_path=d, notify_failed_log_path=logs_nf,
        state_notify_failed_log_path=state_nf, state_path=tmp_path / "state.json",
        now=module.parse_utc(NOW), freshness_threshold_minutes=120, notify_func=notify,
    )
    assert len(calls) == 1
    assert r["notify_failed_new_count"] == 2
    assert r["state_notify_failed_new_count"] == 1
    ev = [e for e in r["events"] if e["event"] == "alert_integrity_notify_failed_growth"][0]
    assert ev["new_lines_per_file"] == {str(logs_nf): 2, str(state_nf): 1}
    msg = calls[0]["message"]
    assert f"2 owner alert(s) in {logs_nf}" in msg
    assert f"1 owner alert(s) in {state_nf}" in msg
    assert "3 owner alert" not in msg


def test_cli_default_watches_state_dead_letter():
    module = load_module()
    assert str(module.STATE_NOTIFY_FAILED_LOG_PATH) == "/opt/shift-agent/state/notify-failed.log"
    assert module.DEFAULT_STALE_REPAGE_MIN == 360
