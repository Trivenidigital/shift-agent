"""Pytest fixtures for Shift Agent tests.

Run: cd /opt/shift-agent/working && /opt/shift-agent/venv/bin/python -m pytest tests/

On deployed VPS the venv has pydantic + pyyaml; tests don't mock them.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

# Make schemas/safe_io/sender_context importable from any cwd.
# Platform-extraction layout: src/, src/platform/, src/agents/shift/ all on path
# so flat imports (`from safe_io import ...`) keep working as modules migrate.
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
for _p in (_SRC_DIR, _SRC_DIR / "platform", _SRC_DIR / "agents" / "shift"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ── env hygiene (census env-pop fix 2026-07-06) ─────────────────────────────
# Belt-and-suspenders for the monkeypatch discipline: monkeypatch.setenv/delenv
# already auto-restore, but a test that mutates os.environ DIRECTLY (raw
# os.environ[...] = / os.environ.pop) would leak that mutation into later tests
# in the same process (the exact order-interference the premium_poster suites'
# unrestored os.environ.pop produced). Snapshotting the ambient environment and
# restoring it after teardown makes single-process runs order-independent
# regardless of HOW a test touches the environment. Defined FIRST so it is the
# outermost autouse fixture: it snapshots before every other fixture sets up and
# restores after every other fixture (incl. monkeypatch) has torn down.
@pytest.fixture(autouse=True)
def _restore_os_environ():
    snapshot = dict(os.environ)
    yield
    if dict(os.environ) != snapshot:
        for key in [k for k in os.environ if k not in snapshot]:
            del os.environ[key]
        for key, value in snapshot.items():
            if os.environ.get(key) != value:
                os.environ[key] = value


# ── send-path test safety (send-path-test-harness 2026-05-30) ───────────────
# Default the bridge URL to a CLOSED loopback sink for EVERY test so no test can
# reach the live WhatsApp bridge (port 3000). Subprocess tests inherit
# HERMES_BRIDGE_URL; in-process callers get safe_io.BRIDGE_URL patched. Tests
# that capture sends override safe_io.BRIDGE_URL to their own stub AFTER this
# autouse fixture runs (function-scoped monkeypatch in the test body wins).
# Paired with safe_io's LiveBridgeSendInTestError tripwire (defense in depth):
# a stray send to :3000 RAISES (loud test failure) rather than leaking.
FAKE_BRIDGE_SINK = "http://127.0.0.1:1/__fake_test_sink__"


@pytest.fixture(autouse=True)
def _force_fake_bridge_sink(monkeypatch):
    """Autouse: no test may default to the live bridge. See FAKE_BRIDGE_SINK."""
    monkeypatch.setenv("HERMES_BRIDGE_URL", FAKE_BRIDGE_SINK)
    _mod = sys.modules.get("safe_io")
    if _mod is not None and hasattr(_mod, "BRIDGE_URL"):
        monkeypatch.setattr(_mod, "BRIDGE_URL", FAKE_BRIDGE_SINK, raising=False)
    yield


# ── audit-log test isolation (census C1 2026-07-11) ─────────────────────────
# Route EVERY test's audit writes to a per-test tmp decisions.log so no test can
# pollute the production audit chokepoint (/opt/shift-agent/logs/decisions.log).
# census C1 found pytest had written 41 regulated_send_*, 87 config_load_failed,
# and 209 dry-run proposal rows into the prod log because the default paths
# point at prod and the tests forgot to override. This mirrors
# _force_fake_bridge_sink: a belt-and-suspenders default that the safe_io
# ndjson_append guard backs up (a stray prod-path write from pytest RAISES).
# In-process writers read the env at call time; subprocess tests that build
# env={**os.environ, ...} inherit it; sudo/on-box tests pass it through
# explicitly. A test that pins the constant-default path (test_audit_helpers'
# default-kwarg case) delenv's this var in its own body.
@pytest.fixture(autouse=True)
def _isolate_audit_log(tmp_path, monkeypatch):
    """Autouse: default the audit chokepoint to a per-test tmp path."""
    monkeypatch.setenv(
        "SHIFT_AGENT_DECISIONS_LOG_PATH", str(tmp_path / "audit" / "decisions.log")
    )
    yield


# ── notify-owner dedup isolation (census C-7 2026-07-11) ────────────────────
# safe_io.notify_owner_with_fallback dedups identical (title+message) owner
# alerts within a 30-min window via a state file. Its default lives at
# /opt/shift-agent/state/notify-dedup.json — a SHARED, real path on the VPS/CI.
# Without isolation one test's DELIVERED alert arms that window and suppresses a
# later test's identical message (breaking delivery-contract + paging tests),
# and pytest pollutes the production dedup file (same failure class as the
# audit-log and bridge isolations above). Route it to a per-test tmp path; the
# function resolves SHIFT_AGENT_NOTIFY_DEDUP_STATE at CALL time so this reaches
# both in-process and subprocess callers.
@pytest.fixture(autouse=True)
def _isolate_notify_dedup(tmp_path, monkeypatch):
    """Autouse: default the notify-owner dedup state to a per-test tmp path."""
    monkeypatch.setenv(
        "SHIFT_AGENT_NOTIFY_DEDUP_STATE", str(tmp_path / "notify-dedup.json")
    )
    yield


# ── notify-owner dead-letter isolation (fix/test-prod-path-bleed-class) ──────
# safe_io.notify_owner_with_fallback appends to a fallback "dead-letter" log when
# the Pushover bin fails — which it always does under test (no bin on the runner).
# Its default lives at /opt/shift-agent/logs/notify-failed.log, a real path on the
# VPS/CI. Without isolation a test whose send fails appends real rows there, and
# pytest pollutes the production dead-letter file (same class as the audit-log and
# notify-dedup isolations above; the generalized safe_io write-guard now RAISES on
# such a stray write, which is how the flyer-recovery-watchdog subprocess tests
# surfaced it). notify_owner_with_fallback resolves SHIFT_AGENT_NOTIFY_FAILED_LOG
# at CALL time, so routing it to a per-test tmp path reaches both in-process and
# subprocess callers (the latter inherit os.environ).
@pytest.fixture(autouse=True)
def _isolate_notify_failed_log(tmp_path, monkeypatch):
    """Autouse: default the notify-owner dead-letter log to a per-test tmp path."""
    monkeypatch.setenv(
        "SHIFT_AGENT_NOTIFY_FAILED_LOG", str(tmp_path / "notify-failed.log")
    )
    yield


@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    """Isolated state directory per test."""
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture
def sample_roster_dict() -> dict:
    """Phase 0 roster dict (6 employees, Triveni Jacksonville)."""
    return {
        "location": {"id": "loc_jax_01", "name": "Triveni", "timezone": "America/New_York"},
        "employees": [
            {"id": "e001", "name": "Ravi Kumar", "nickname": "Ravi",
             "role": "cashier", "phone": "+19045550101",
             "languages": ["en", "te", "hi"], "can_cover_roles": ["cashier", "floor"]},
            {"id": "e002", "name": "Priya Reddy", "role": "bakery",
             "phone": "+19045550102", "languages": ["en", "te"],
             "can_cover_roles": ["bakery", "sweets"]},
            {"id": "e003", "name": "Suresh Patel", "role": "meat_counter",
             "phone": "+19045550103", "languages": ["en", "hi", "gu"],
             "can_cover_roles": ["meat_counter", "floor"]},
            {"id": "e004", "name": "Anjali Iyer", "role": "cashier",
             "phone": "+19045550104", "languages": ["en", "ta"],
             "can_cover_roles": ["cashier", "bakery", "sweets"]},
            {"id": "e005", "name": "Vikram Sharma", "role": "floor",
             "phone": "+19045550105", "languages": ["en", "hi"],
             "can_cover_roles": ["floor", "cashier", "meat_counter"]},
            {"id": "e006", "name": "Lakshmi Rao", "role": "sweets",
             "phone": "+19045550106", "languages": ["en", "te"],
             "can_cover_roles": ["sweets", "bakery"]},
        ],
        "schedule": {
            "2026-04-25": [
                {"employee_id": "e001", "shift": "09:00-17:00", "role": "cashier"},
                {"employee_id": "e002", "shift": "06:00-14:00", "role": "bakery"},
                {"employee_id": "e003", "shift": "10:00-18:00", "role": "meat_counter"},
                {"employee_id": "e005", "shift": "12:00-20:00", "role": "floor"},
                {"employee_id": "e006", "shift": "08:00-16:00", "role": "sweets"},
            ]
        },
    }


@pytest.fixture
def sample_config_dict() -> dict:
    """Minimum-valid config for tests."""
    return {
        "schema_version": 1,
        "customer": {
            "name": "Test Customer", "location_id": "loc_test_01",
            "timezone": "America/New_York", "languages": ["en"],
        },
        "owner": {
            "name": "Test Owner", "phone": "+19045550999", "self_chat_jid": "",
        },
        "limits": {
            "max_outbound_per_day": 2, "max_outbound_per_minute": 30,
            "pending_proposal_ttl_hours": 4, "per_message_timeout_sec": 120,
            "send_failure_retry_count": 1,
        },
        "alerting": {
            # Non-empty to pass validator; tests don't actually call Pushover
            "pushover_user_key": "test-user-key",
            "pushover_app_token": "test-app-token",
            "healthchecks_io_url": "", "email": "",
        },
        "backup": {
            "gpg_recipient_email": "test@example.com",
            "s3_bucket": "", "retention_days": 30,
        },
        "operations": {"business_hours_local": "08:00-22:00"},
    }


@pytest.fixture
def now_aware() -> datetime:
    return datetime.now(tz=timezone.utc)
