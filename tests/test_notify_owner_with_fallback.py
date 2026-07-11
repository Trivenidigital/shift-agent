"""Tests for safe_io.notify_owner_with_fallback — platform-helpers consolidation.

Validates the deduplicated helper that replaces inline implementations in:
- send-coverage-message._notify_owner + _append_notify_failed
- send-daily-brief._pushover_alert
- eod-reconcile._pushover_summary (subprocess-call portion)

Linux-only: safe_io transitively imports fcntl.
"""
from __future__ import annotations

import os
import json
from pathlib import Path

import pytest

pytest.importorskip("fcntl")
pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="notify-owner subprocess tests build POSIX executable scripts",
)

from safe_io import notify_owner_with_fallback, _notify_dedup_key


def _make_fake_notify_bin(tmp_path: Path, *, exit_code: int = 0,
                           stderr: str = "") -> Path:
    """Build a fake notify-owner script the helper can subprocess.run."""
    bin_path = tmp_path / "fake-notify-owner"
    bin_path.write_text(
        f"#!/usr/bin/env bash\n"
        f"echo {stderr!r} >&2\n"
        f"exit {exit_code}\n"
    )
    bin_path.chmod(0o755)
    return bin_path


def test_returns_true_on_pushover_success(tmp_path):
    bin_ok = _make_fake_notify_bin(tmp_path, exit_code=0)
    log = tmp_path / "notify-failed.log"
    result = notify_owner_with_fallback(
        "title", "msg", priority=1, source="test",
        notify_owner_bin=str(bin_ok), notify_failed_log=log,
    )
    assert result is True
    # No fallback log entry when subprocess returns 0
    assert not log.exists()


def test_returns_false_and_writes_fallback_on_nonzero_exit(tmp_path):
    bin_fail = _make_fake_notify_bin(tmp_path, exit_code=2, stderr="boom")
    log = tmp_path / "notify-failed.log"
    result = notify_owner_with_fallback(
        "title", "msg", priority=2, source="test-caller",
        notify_owner_bin=str(bin_fail), notify_failed_log=log,
    )
    assert result is False
    assert log.exists()
    line = log.read_text(encoding="utf-8").strip().splitlines()[-1]
    entry = json.loads(line)
    assert entry["source"] == "test-caller"
    assert entry["title"] == "title"
    assert entry["message"] == "msg"
    assert "exit=2" in entry["pushover_error"]
    assert "boom" in entry["pushover_error"]


def test_returns_false_and_writes_fallback_on_missing_binary(tmp_path):
    log = tmp_path / "notify-failed.log"
    nonexistent = tmp_path / "no-such-binary"
    result = notify_owner_with_fallback(
        "title", "msg", source="test",
        notify_owner_bin=str(nonexistent), notify_failed_log=log,
    )
    assert result is False
    assert log.exists()
    entry = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "FileNotFoundError" in entry["pushover_error"] or \
           "No such file" in entry["pushover_error"]


def test_truncates_long_fields(tmp_path):
    """Title 200ch / message 500ch / pushover_error 300ch caps."""
    bin_fail = _make_fake_notify_bin(tmp_path, exit_code=1, stderr="x" * 5000)
    log = tmp_path / "notify-failed.log"
    notify_owner_with_fallback(
        "T" * 1000, "M" * 5000, source="test",
        notify_owner_bin=str(bin_fail), notify_failed_log=log,
    )
    entry = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert len(entry["title"]) == 200
    assert len(entry["message"]) == 500
    assert len(entry["pushover_error"]) <= 300


def test_creates_log_parent_dir(tmp_path):
    """notify-failed.log lives at /opt/shift-agent/state/...; helper must
    mkdir parents=True so first-call from a clean install works."""
    bin_fail = _make_fake_notify_bin(tmp_path, exit_code=1)
    log = tmp_path / "deep" / "nested" / "subdir" / "notify-failed.log"
    notify_owner_with_fallback(
        "title", "msg", source="test",
        notify_owner_bin=str(bin_fail), notify_failed_log=log,
    )
    assert log.exists()


def test_appends_not_overwrites(tmp_path):
    """Multiple failures should append to the same log."""
    bin_fail = _make_fake_notify_bin(tmp_path, exit_code=1)
    log = tmp_path / "notify-failed.log"
    notify_owner_with_fallback("t1", "m1", source="a",
                                notify_owner_bin=str(bin_fail), notify_failed_log=log)
    notify_owner_with_fallback("t2", "m2", source="b",
                                notify_owner_bin=str(bin_fail), notify_failed_log=log)
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["source"] == "a"
    assert json.loads(lines[1])["source"] == "b"


def test_priority_passed_to_subprocess(tmp_path):
    """Verify priority kwarg flows through to the subprocess argv."""
    capture_path = tmp_path / "captured-args.txt"
    bin_capture = tmp_path / "fake-notify"
    bin_capture.write_text(
        f"#!/usr/bin/env bash\n"
        f"echo \"$@\" > {capture_path}\n"
        f"exit 0\n"
    )
    bin_capture.chmod(0o755)
    notify_owner_with_fallback(
        "t", "m", priority=2, source="test",
        notify_owner_bin=str(bin_capture), notify_failed_log=tmp_path / "log",
    )
    captured = capture_path.read_text(encoding="utf-8").strip()
    assert "--priority 2" in captured


def test_default_source_is_unknown(tmp_path):
    bin_fail = _make_fake_notify_bin(tmp_path, exit_code=1)
    log = tmp_path / "notify-failed.log"
    notify_owner_with_fallback(
        "t", "m",
        notify_owner_bin=str(bin_fail), notify_failed_log=log,
    )
    entry = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["source"] == "unknown"


# ── C-7: same-message dedup (default ON, env kill-switch) ─────────────────────

def _counting_bin(tmp_path: Path):
    """A notify-owner stub that records each invocation, so a test can count how
    many times the subprocess actually ran."""
    count_file = tmp_path / "invocations.txt"
    b = tmp_path / "counting-notify"
    b.write_text(f"#!/usr/bin/env bash\necho x >> {count_file}\nexit 0\n")
    b.chmod(0o755)
    return b, count_file


def _invocations(count_file: Path) -> int:
    return len(count_file.read_text(encoding="utf-8").splitlines()) if count_file.exists() else 0


def test_dedup_suppresses_identical_alert_within_window(tmp_path):
    b, count_file = _counting_bin(tmp_path)
    kw = dict(notify_owner_bin=str(b), notify_failed_log=tmp_path / "log",
              dedup_state_path=tmp_path / "notify-dedup.json", dedup_window_min=30)
    assert notify_owner_with_fallback("t", "m", source="s", **kw) is True
    assert notify_owner_with_fallback("t", "m", source="s", **kw) is True  # suppressed
    assert _invocations(count_file) == 1  # subprocess ran once; 2nd identical deduped


def test_dedup_lets_distinct_message_through(tmp_path):
    b, count_file = _counting_bin(tmp_path)
    kw = dict(notify_owner_bin=str(b), notify_failed_log=tmp_path / "log",
              dedup_state_path=tmp_path / "notify-dedup.json", dedup_window_min=30)
    notify_owner_with_fallback("t", "m1", source="s", **kw)
    notify_owner_with_fallback("t", "m2", source="s", **kw)  # different body
    assert _invocations(count_file) == 2


def test_dedup_disabled_lets_identical_through(tmp_path):
    b, count_file = _counting_bin(tmp_path)
    kw = dict(notify_owner_bin=str(b), notify_failed_log=tmp_path / "log",
              dedup_state_path=tmp_path / "notify-dedup.json", dedup_enabled=False)
    notify_owner_with_fallback("t", "m", source="s", **kw)
    notify_owner_with_fallback("t", "m", source="s", **kw)
    assert _invocations(count_file) == 2


def test_dedup_expired_entry_does_not_suppress(tmp_path):
    from datetime import datetime, timezone, timedelta
    b, count_file = _counting_bin(tmp_path)
    state = tmp_path / "notify-dedup.json"
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    state.write_text(json.dumps({"sent": {_notify_dedup_key("t", "m"): old}}), encoding="utf-8")
    notify_owner_with_fallback("t", "m", source="s", notify_owner_bin=str(b),
                                notify_failed_log=tmp_path / "log",
                                dedup_state_path=state, dedup_window_min=30)
    assert _invocations(count_file) == 1  # 45-min-old entry is outside the 30-min window


def test_dedup_dormant_when_state_dir_missing(tmp_path):
    """The state-dir guard keeps dedup a no-op off a deployed box, so callers on
    the default (non-existent) path see byte-compatible pre-dedup behavior."""
    b, count_file = _counting_bin(tmp_path)
    missing = tmp_path / "no-such-dir" / "notify-dedup.json"  # parent absent
    kw = dict(notify_owner_bin=str(b), notify_failed_log=tmp_path / "log",
              dedup_state_path=missing, dedup_window_min=30)
    notify_owner_with_fallback("t", "m", source="s", **kw)
    notify_owner_with_fallback("t", "m", source="s", **kw)
    assert _invocations(count_file) == 2  # nothing recorded -> nothing suppressed
