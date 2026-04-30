"""Tests for safe_io.self_gate_window_state — platform-helpers consolidation
commit 2 of 4.

Validates the deduplicated helper that replaces inline implementations in:
- send-daily-brief._self_gate_state
- eod-reconcile._self_gate_state

Linux-only: safe_io transitively imports fcntl. (The function itself is
pure, but the module import requires fcntl.)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fcntl")

from safe_io import self_gate_window_state


# Anchor: 2026-04-30 07:00 UTC (the typical brief time in tests)
def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 4, 30, hour, minute, 0, tzinfo=timezone.utc)


def test_before_target_returns_before():
    state, late = self_gate_window_state(
        _at(6, 45), "07:00", window_min=15, catchup_min=180,
    )
    assert state == "before"
    assert late == 0


def test_at_target_returns_in_window():
    state, late = self_gate_window_state(
        _at(7, 0), "07:00", window_min=15, catchup_min=180,
    )
    assert state == "in_window"
    assert late == 0


def test_just_inside_window_returns_in_window():
    state, late = self_gate_window_state(
        _at(7, 14), "07:00", window_min=15, catchup_min=180,
    )
    assert state == "in_window"
    assert late == 0


def test_at_window_boundary_returns_in_catchup():
    """t == target + window_min is past the (target, target+window_min) range."""
    state, late = self_gate_window_state(
        _at(7, 15), "07:00", window_min=15, catchup_min=180,
    )
    assert state == "in_catchup"
    assert late == 15


def test_inside_catchup_returns_in_catchup():
    state, late = self_gate_window_state(
        _at(8, 30), "07:00", window_min=15, catchup_min=180,
    )
    assert state == "in_catchup"
    assert late == 90


def test_at_catchup_boundary_returns_past_catchup():
    state, late = self_gate_window_state(
        _at(10, 0), "07:00", window_min=15, catchup_min=180,
    )
    assert state == "past_catchup"
    assert late == 180


def test_past_catchup_returns_past_catchup():
    state, late = self_gate_window_state(
        _at(15, 0), "07:00", window_min=15, catchup_min=180,
    )
    assert state == "past_catchup"
    assert late == 480


def test_window_min_default_is_15():
    """Match the cron OnUnitActiveSec=15min default."""
    state, _ = self_gate_window_state(
        _at(7, 14), "07:00", catchup_min=180,
    )
    assert state == "in_window"
    state, _ = self_gate_window_state(
        _at(7, 15), "07:00", catchup_min=180,
    )
    assert state == "in_catchup"


def test_zero_minute_target():
    """Edge: midnight target."""
    state, late = self_gate_window_state(
        _at(0, 5), "00:00", window_min=15, catchup_min=180,
    )
    assert state == "in_window"


def test_24_hour_format_supports_double_digit_hour():
    state, _ = self_gate_window_state(
        _at(23, 30), "23:30", window_min=15, catchup_min=60,
    )
    assert state == "in_window"
