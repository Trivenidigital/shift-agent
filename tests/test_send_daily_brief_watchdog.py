"""Tests for the routing-summary-staleness watchdog injected into send-daily-brief.

Linux-only (depends on safe_io which uses fcntl).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import platform
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="depends on safe_io which uses fcntl (Linux only)",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "src" / "agents" / "daily_brief" / "scripts" / "send-daily-brief"


def _import_brief_module():
    loader = importlib.machinery.SourceFileLoader("sdb", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_file_location("sdb", str(SCRIPT_PATH), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cfg_stub():
    cfg = MagicMock()
    cfg.customer.timezone = "America/New_York"
    return cfg


@pytest.fixture
def fresh_timestamp():
    return (datetime.now(tz=timezone.utc) - timedelta(days=2)).isoformat()


@pytest.fixture
def stale_timestamp():
    return (datetime.now(tz=timezone.utc) - timedelta(days=12)).isoformat()


def _patch_watchdog_paths(monkeypatch, sdb, watchdog_path, timer_unit_path):
    monkeypatch.setattr(sdb, "WATCHDOG_PATH", watchdog_path)
    monkeypatch.setattr(sdb, "WATCHDOG_TIMER_UNIT", timer_unit_path)


def _make_present_timer_unit(tmp_path):
    p = tmp_path / "timer.unit"
    p.touch()
    return p


# ---------- silent-when-OK paths ----------

def test_watchdog_silent_when_no_file(cfg_stub, tmp_path, monkeypatch):
    sdb = _import_brief_module()
    timer_unit = _make_present_timer_unit(tmp_path)
    _patch_watchdog_paths(monkeypatch, sdb, tmp_path / "missing.json", timer_unit)
    with patch.object(sdb, "_pushover_alert") as alert:
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)
        alert.assert_not_called()


def test_watchdog_silent_when_fresh(cfg_stub, tmp_path, monkeypatch, fresh_timestamp):
    sdb = _import_brief_module()
    timer_unit = _make_present_timer_unit(tmp_path)
    wd = tmp_path / "wd.json"
    wd.write_text(json.dumps({"ts": fresh_timestamp}))
    _patch_watchdog_paths(monkeypatch, sdb, wd, timer_unit)
    with patch.object(sdb, "_pushover_alert") as alert:
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)
        alert.assert_not_called()


def test_watchdog_silent_in_dry_run(cfg_stub, tmp_path, monkeypatch, stale_timestamp):
    """dry_run=True must short-circuit even with a stale file."""
    sdb = _import_brief_module()
    timer_unit = _make_present_timer_unit(tmp_path)
    wd = tmp_path / "wd.json"
    wd.write_text(json.dumps({"ts": stale_timestamp}))
    _patch_watchdog_paths(monkeypatch, sdb, wd, timer_unit)
    with patch.object(sdb, "_pushover_alert") as alert:
        sdb._check_routing_watchdog(cfg_stub, dry_run=True)
        alert.assert_not_called()


def test_watchdog_silent_when_timer_unit_missing(cfg_stub, tmp_path, monkeypatch, stale_timestamp):
    """Partial-deploy guard: code shipped without timer enabled -> no false alarm."""
    sdb = _import_brief_module()
    wd = tmp_path / "wd.json"
    wd.write_text(json.dumps({"ts": stale_timestamp}))
    missing_timer = tmp_path / "no-such-timer.unit"  # never created
    _patch_watchdog_paths(monkeypatch, sdb, wd, missing_timer)
    with patch.object(sdb, "_pushover_alert") as alert:
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)
        alert.assert_not_called()


# ---------- fires-when-stale ----------

def test_watchdog_fires_when_stale(cfg_stub, tmp_path, monkeypatch, stale_timestamp):
    sdb = _import_brief_module()
    timer_unit = _make_present_timer_unit(tmp_path)
    wd = tmp_path / "wd.json"
    wd.write_text(json.dumps({"ts": stale_timestamp}))
    _patch_watchdog_paths(monkeypatch, sdb, wd, timer_unit)
    with patch.object(sdb, "_pushover_alert") as alert:
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)
        alert.assert_called_once()
        args, kwargs = alert.call_args
        assert "stale" in args[0].lower()
        assert kwargs.get("priority") == 1


def test_watchdog_no_state_persisted_between_calls(cfg_stub, tmp_path, monkeypatch, stale_timestamp):
    """Codifies accepted behavior: two consecutive calls both fire (no dedup)."""
    sdb = _import_brief_module()
    timer_unit = _make_present_timer_unit(tmp_path)
    wd = tmp_path / "wd.json"
    wd.write_text(json.dumps({"ts": stale_timestamp}))
    _patch_watchdog_paths(monkeypatch, sdb, wd, timer_unit)
    with patch.object(sdb, "_pushover_alert") as alert:
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)
        assert alert.call_count == 2


# ---------- error tolerance ----------

def test_watchdog_handles_missing_timestamp_key(cfg_stub, tmp_path, monkeypatch, capsys):
    sdb = _import_brief_module()
    timer_unit = _make_present_timer_unit(tmp_path)
    wd = tmp_path / "wd.json"
    wd.write_text(json.dumps({"wrong_key": "x"}))
    _patch_watchdog_paths(monkeypatch, sdb, wd, timer_unit)
    with patch.object(sdb, "_pushover_alert") as alert:
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)
        alert.assert_not_called()
    err = capsys.readouterr().err
    assert "watchdog check failed" in err


def test_watchdog_handles_corrupt_json(cfg_stub, tmp_path, monkeypatch, capsys):
    sdb = _import_brief_module()
    timer_unit = _make_present_timer_unit(tmp_path)
    wd = tmp_path / "wd.json"
    wd.write_text("not json {{")
    _patch_watchdog_paths(monkeypatch, sdb, wd, timer_unit)
    with patch.object(sdb, "_pushover_alert") as alert:
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)
        alert.assert_not_called()
    err = capsys.readouterr().err
    assert "watchdog check failed" in err


def test_watchdog_tz_safe_against_naive_timestamp(cfg_stub, tmp_path, monkeypatch, capsys):
    """If a buggy writer ever emits a naive ISO string, the aware-vs-naive
    subtraction TypeError must be caught — daily brief MUST NOT crash."""
    sdb = _import_brief_module()
    timer_unit = _make_present_timer_unit(tmp_path)
    wd = tmp_path / "wd.json"
    wd.write_text(json.dumps({"ts": datetime(2026, 4, 1, 12, 0).isoformat()}))  # naive
    _patch_watchdog_paths(monkeypatch, sdb, wd, timer_unit)
    with patch.object(sdb, "_pushover_alert"):
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)  # must not raise
    err = capsys.readouterr().err
    assert "watchdog check failed" in err


def test_pushover_failure_does_not_crash_watchdog(cfg_stub, tmp_path, monkeypatch, stale_timestamp, capsys):
    """If _pushover_alert raises, watchdog must catch it (the brief MUST proceed)."""
    sdb = _import_brief_module()
    timer_unit = _make_present_timer_unit(tmp_path)
    wd = tmp_path / "wd.json"
    wd.write_text(json.dumps({"ts": stale_timestamp}))
    _patch_watchdog_paths(monkeypatch, sdb, wd, timer_unit)

    def boom(*a, **kw):
        raise BrokenPipeError("pushover died")

    with patch.object(sdb, "_pushover_alert", side_effect=boom):
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)  # must not raise
    err = capsys.readouterr().err
    assert "watchdog pushover failed" in err


# ---------- alias correctness ----------

def test_watchdog_uses_customer_now_alias(cfg_stub, tmp_path, monkeypatch, fresh_timestamp):
    """SHIFT_AGENT_NOW_OVERRIDE must reach the watchdog's stale-calc.
    If watchdog used safe_io.customer_now directly, override would be ignored."""
    sdb = _import_brief_module()
    timer_unit = _make_present_timer_unit(tmp_path)
    wd = tmp_path / "wd.json"
    # File ts is "fresh" (2 days ago) by real wall clock.
    wd.write_text(json.dumps({"ts": fresh_timestamp}))
    _patch_watchdog_paths(monkeypatch, sdb, wd, timer_unit)

    # Override "now" to 30 days in the future -> file becomes >10 days stale
    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", future)

    with patch.object(sdb, "_pushover_alert") as alert:
        sdb._check_routing_watchdog(cfg_stub, dry_run=False)
        # If override flowed through _customer_now, this fires; if watchdog
        # bypassed the alias, alert would not be called.
        alert.assert_called_once()
