"""P0-4 — reusable per-key/day budget counter + latency wrapper.

Generalizes the B1 global shadow-LLM budget (cf-router actions.py) into a
per-KEY/day counter: key = canonical chat key (a PARAMETER — the identity helper
is on a sibling branch, so this stays decoupled; the caller passes chat_id
today). Caps are env-tunable (default 30 turns/chat/day) and the counter fails
toward SKIP (never unbounded). The latency wrapper bounds a composed call
(4s default) and returns a fallback on timeout/failure.

Pure module (lazy, guarded flock import) — runs on Windows and Docker alike.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "platform"))

import front_brain_budget as budget  # noqa: E402


# ── per-key/day budget ──────────────────────────────────────────────────────

def test_reserve_within_cap_then_skip(tmp_path):
    state = tmp_path / "budget.json"
    for i in range(3):
        assert budget.reserve_chat_day_budget("chatA", cap=3, state_path=state) is True, i
    # 4th turn exceeds the cap -> SKIP.
    assert budget.reserve_chat_day_budget("chatA", cap=3, state_path=state) is False


def test_keys_are_independent(tmp_path):
    state = tmp_path / "budget.json"
    assert budget.reserve_chat_day_budget("chatA", cap=1, state_path=state) is True
    assert budget.reserve_chat_day_budget("chatA", cap=1, state_path=state) is False
    # A different chat still has its full budget.
    assert budget.reserve_chat_day_budget("chatB", cap=1, state_path=state) is True


def test_day_rollover_resets(tmp_path):
    state = tmp_path / "budget.json"
    d1 = datetime(2026, 7, 12, 23, 59, tzinfo=timezone.utc)
    d2 = datetime(2026, 7, 13, 0, 1, tzinfo=timezone.utc)
    assert budget.reserve_chat_day_budget("chatA", cap=1, state_path=state, now=d1) is True
    assert budget.reserve_chat_day_budget("chatA", cap=1, state_path=state, now=d1) is False
    # New UTC day -> counter resets.
    assert budget.reserve_chat_day_budget("chatA", cap=1, state_path=state, now=d2) is True


def test_zero_cap_always_skips(tmp_path):
    state = tmp_path / "budget.json"
    assert budget.reserve_chat_day_budget("chatA", cap=0, state_path=state) is False


def test_empty_key_fails_closed(tmp_path):
    state = tmp_path / "budget.json"
    assert budget.reserve_chat_day_budget("", cap=5, state_path=state) is False


def test_write_error_fails_closed(tmp_path):
    # state_path is a directory -> the atomic replace fails -> reserve returns
    # False (fail toward SKIP; the counter never lets turns through unbounded).
    state_dir = tmp_path / "adir"
    state_dir.mkdir()
    assert budget.reserve_chat_day_budget("chatA", cap=5, state_path=state_dir) is False


def test_cap_defaults_to_env(tmp_path, monkeypatch):
    state = tmp_path / "budget.json"
    monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "2")
    assert budget.chat_daily_cap() == 2
    assert budget.reserve_chat_day_budget("chatA", state_path=state) is True
    assert budget.reserve_chat_day_budget("chatA", state_path=state) is True
    assert budget.reserve_chat_day_budget("chatA", state_path=state) is False


def test_default_cap_is_30(monkeypatch):
    monkeypatch.delenv("FRONT_BRAIN_CHAT_DAILY_CAP", raising=False)
    assert budget.chat_daily_cap() == budget.DEFAULT_CHAT_DAILY_CAP == 30


# ── latency wrapper ─────────────────────────────────────────────────────────

def test_fast_call_returns_value():
    value, used_fallback = budget.run_with_timeout(lambda: "composed reply", timeout=1.0, fallback="TEMPLATE")
    assert value == "composed reply"
    assert used_fallback is False


def test_slow_call_times_out_to_fallback():
    def slow():
        time.sleep(0.5)
        return "too late"
    value, used_fallback = budget.run_with_timeout(slow, timeout=0.05, fallback="TEMPLATE")
    assert value == "TEMPLATE"
    assert used_fallback is True


def test_raising_call_falls_back():
    def boom():
        raise RuntimeError("provider exploded")
    value, used_fallback = budget.run_with_timeout(boom, timeout=1.0, fallback="TEMPLATE")
    assert value == "TEMPLATE"
    assert used_fallback is True


def test_timeout_defaults_to_env(monkeypatch):
    monkeypatch.setenv("FRONT_BRAIN_COMPOSE_TIMEOUT_SEC", "0.05")
    assert budget.compose_timeout_sec() == pytest.approx(0.05)

    def slow():
        time.sleep(0.4)
        return "late"
    value, used_fallback = budget.run_with_timeout(slow, fallback="TEMPLATE")
    assert used_fallback is True and value == "TEMPLATE"


def test_default_timeout_is_4s(monkeypatch):
    monkeypatch.delenv("FRONT_BRAIN_COMPOSE_TIMEOUT_SEC", raising=False)
    assert budget.compose_timeout_sec() == budget.DEFAULT_COMPOSE_TIMEOUT_SEC == 4.0
