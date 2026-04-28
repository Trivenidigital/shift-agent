"""Tests for src/agents/shift/scripts/send-routing-accuracy-summary.

Linux-only (depends on safe_io.atomic_write_json which uses fcntl).
"""
from __future__ import annotations

import importlib.util
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="depends on safe_io which uses fcntl (Linux only)",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "send-routing-accuracy-summary"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"


def _load_script():
    """Load the script as a module. Conftest.py has src/platform on sys.path so
    the script's `from safe_io import atomic_write_json` resolves."""
    spec = importlib.util.spec_from_file_location("sras", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def normal_report():
    return json.loads((FIXTURE_DIR / "dispatcher_accuracy_report_normal.json").read_text())


@pytest.fixture
def empty_report():
    return json.loads((FIXTURE_DIR / "dispatcher_accuracy_report_empty.json").read_text())


@pytest.fixture
def boundary_report():
    return json.loads((FIXTURE_DIR / "dispatcher_accuracy_report_boundary.json").read_text())


@pytest.fixture
def healthy_log(tmp_path):
    """A non-empty, recently-touched decisions.log."""
    p = tmp_path / "decisions.log"
    p.write_text('{"type":"raw_inbound","ts":"2026-04-28T12:00:00+00:00"}\n')
    return p


def _patch_paths(monkeypatch, sras, watchdog_path, decisions_log):
    monkeypatch.setattr(sras, "WATCHDOG_PATH", watchdog_path)
    monkeypatch.setattr(sras, "DECISIONS_LOG", decisions_log)


# ---------- empty-window paths ----------

def test_empty_window_skips_pushover_writes_watchdog(empty_report, tmp_path, healthy_log, monkeypatch):
    sras = _load_script()
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", healthy_log)
    notify_calls = []

    def fake_run(args, **kwargs):
        if args[0] == sras.REPORT_BIN:
            return MagicMock(returncode=0, stdout=json.dumps(empty_report), stderr="")
        notify_calls.append(args)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 0
    assert notify_calls == []
    assert (tmp_path / "wd.json").exists()


def test_empty_window_with_missing_log_exits_1(empty_report, tmp_path, monkeypatch):
    sras = _load_script()
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", tmp_path / "missing.log")

    def fake_run(args, **kwargs):
        if args[0] == sras.REPORT_BIN:
            return MagicMock(returncode=0, stdout=json.dumps(empty_report), stderr="")
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 1
    assert not (tmp_path / "wd.json").exists()


def test_empty_window_with_zero_byte_log_exits_1(empty_report, tmp_path, monkeypatch):
    sras = _load_script()
    log = tmp_path / "decisions.log"
    log.touch()  # 0 bytes
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", log)

    def fake_run(args, **kwargs):
        if args[0] == sras.REPORT_BIN:
            return MagicMock(returncode=0, stdout=json.dumps(empty_report), stderr="")
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 1
    assert not (tmp_path / "wd.json").exists()


def test_empty_window_with_stale_log_exits_1(empty_report, tmp_path, monkeypatch):
    import os
    sras = _load_script()
    log = tmp_path / "decisions.log"
    log.write_text('{"x":1}\n')
    # Force mtime 60 days ago
    sixty_days_ago = datetime.now(tz=timezone.utc).timestamp() - 60 * 86400
    os.utime(log, (sixty_days_ago, sixty_days_ago))
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", log)

    def fake_run(args, **kwargs):
        if args[0] == sras.REPORT_BIN:
            return MagicMock(returncode=0, stdout=json.dumps(empty_report), stderr="")
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 1
    assert not (tmp_path / "wd.json").exists()


# ---------- normal-week paths ----------

def test_normal_week_sends_summary_writes_watchdog(normal_report, tmp_path, healthy_log, monkeypatch):
    sras = _load_script()
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", healthy_log)
    notify_calls = []

    def fake_run(args, **kwargs):
        if args[0] == sras.REPORT_BIN:
            return MagicMock(returncode=0, stdout=json.dumps(normal_report), stderr="")
        notify_calls.append(tuple(args))
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 0
    assert (tmp_path / "wd.json").exists()
    assert len(notify_calls) == 1
    args = notify_calls[0]
    assert args[0] == sras.NOTIFY_BIN
    assert "--title" in args
    assert "Weekly routing accuracy" in args
    # Last positional arg is the message body
    assert "44/47" in args[-1]
    assert "93.6" in args[-1]
    assert "5 declined" in args[-1]


def test_boundary_one_inbound_renders_correctly(boundary_report, tmp_path, healthy_log, monkeypatch):
    """1/1 100% case must not divide-by-zero or pluralize incorrectly."""
    sras = _load_script()
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", healthy_log)
    captured = []

    def fake_run(args, **kwargs):
        if args[0] == sras.REPORT_BIN:
            return MagicMock(returncode=0, stdout=json.dumps(boundary_report), stderr="")
        captured.append(tuple(args))
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 0
    assert "1/1" in captured[0][-1]
    assert "100" in captured[0][-1]


# ---------- failure paths ----------

def test_invalid_json_exits_1(tmp_path, healthy_log, monkeypatch):
    sras = _load_script()
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", healthy_log)

    def fake_run(args, **kwargs):
        if args[0] == sras.REPORT_BIN:
            return MagicMock(returncode=0, stdout="not json {{", stderr="")
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 1
    assert not (tmp_path / "wd.json").exists()


def test_report_failure_exits_1(tmp_path, healthy_log, monkeypatch):
    sras = _load_script()
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", healthy_log)

    def fake_run(args, **kwargs):
        if args[0] == sras.REPORT_BIN:
            return MagicMock(returncode=2, stdout="", stderr="db gone")
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 1
    assert not (tmp_path / "wd.json").exists()


def test_pushover_failure_watchdog_still_written(normal_report, tmp_path, healthy_log, monkeypatch):
    """v2 semantic: watchdog written BEFORE format/notify so notify-failure
    doesn't trigger cron-stale alarm next day."""
    sras = _load_script()
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", healthy_log)

    def fake_run(args, **kwargs):
        if args[0] == sras.REPORT_BIN:
            return MagicMock(returncode=0, stdout=json.dumps(normal_report), stderr="")
        return MagicMock(returncode=3, stdout="", stderr="pushover api 503")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 1
    assert (tmp_path / "wd.json").exists()  # v2 contract: written before notify


def test_subprocess_timeout_exits_1(tmp_path, healthy_log, monkeypatch):
    sras = _load_script()
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", healthy_log)

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 60))

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 1
    assert not (tmp_path / "wd.json").exists()


def test_unicode_decode_error_exits_1(tmp_path, healthy_log, monkeypatch):
    """Non-UTF-8 bytes from subprocess(text=True) -> UnicodeDecodeError must
    be caught (silent-failure-hunter HIGH-1 from PR review)."""
    sras = _load_script()
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", healthy_log)

    def fake_run(args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sras.main() == 1
    assert not (tmp_path / "wd.json").exists()


def test_reporter_stderr_forwarded(normal_report, tmp_path, healthy_log, monkeypatch, capsys):
    sras = _load_script()
    _patch_paths(monkeypatch, sras, tmp_path / "wd.json", healthy_log)

    def fake_run(args, **kwargs):
        if args[0] == sras.REPORT_BIN:
            return MagicMock(
                returncode=0, stdout=json.dumps(normal_report),
                stderr="WARN: fuzzy match fallback fired\n",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sras.main()
    err = capsys.readouterr().err
    assert "fuzzy match fallback" in err


# ---------- watchdog round-trip ----------

def test_watchdog_timestamp_round_trip(tmp_path, monkeypatch):
    """Pin the cross-file contract: writer produces tz-aware UTC ISO that
    fromisoformat parses + subtracts cleanly from any tz-aware now()."""
    sras = _load_script()
    monkeypatch.setattr(sras, "WATCHDOG_PATH", tmp_path / "wd.json")
    sras._write_watchdog()

    data = json.loads((tmp_path / "wd.json").read_text())
    parsed = datetime.fromisoformat(data["ts"])
    assert parsed.tzinfo is not None  # tz-aware

    from zoneinfo import ZoneInfo
    customer_tz_now = datetime.now(tz=ZoneInfo("America/New_York"))
    delta = customer_tz_now - parsed
    assert delta.total_seconds() >= 0  # didn't raise


# ---------- _format_summary unit tests ----------

def test_format_summary_normal(normal_report):
    sras = _load_script()
    out = sras._format_summary(normal_report)
    assert "44/47" in out
    assert "93.6" in out
    assert "5 declined" in out
    assert "shift-coverage-coordinator" in out


def test_format_summary_missing_required_key_raises():
    sras = _load_script()
    bad = {"paired_count": 1, "coverage_pct": 100.0}  # missing total_raw_inbound
    with pytest.raises(KeyError):
        sras._format_summary(bad)


def test_format_summary_optional_keys_default():
    """declined_count and by_routed_to_skill are .get()-soft; missing is fine."""
    sras = _load_script()
    minimal = {"total_raw_inbound": 5, "paired_count": 5, "coverage_pct": 100.0}
    out = sras._format_summary(minimal)
    assert "5/5" in out
    assert "0 declined" in out
    assert "Top: none" in out
