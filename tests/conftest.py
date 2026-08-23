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


# ── copied-state rehearsal sandbox (P1-A 2026-08-23) ────────────────────────
# A copied-state rehearsal of a menu mutation copied the REAL production
# config.yaml — live Pushover user key and app token included — into a
# `--network host` docker container running real production scripts. Nothing
# fired, but only by luck: those scripts contain a notify_owner_with_fallback
# call that DID page the owner for real minutes later.
#
# This is the ergonomic wrapper around the platform primitives in safe_io
# (sanitize_config_for_rehearsal / assert_rehearsal_config_sterile /
# assert_rehearsal_env_sterile / sterile_env_file_body). It deliberately does
# NOT reimplement them: the primitives live in src/platform so an operator's
# on-box rehearsal script gets the same guarantee as pytest does, and so the
# sanitiser and its verifier stay separable (a verifier that shares code with
# the sanitiser cannot catch a sanitiser that never ran).
#
# It extends the grain already in this file — _force_fake_bridge_sink pointing
# the bridge at a closed loopback sink, _isolate_audit_log / _isolate_notify_*
# routing prod paths into tmp — rather than inventing a parallel mechanism.
# Where those fixtures default ONE door per fixture for every test, this builds
# a whole sandbox for the specific case of a config COPIED from production.
#
# The subprocess env is built from a small PASSTHROUGH ALLOWLIST, not from
# os.environ: an operator shell that has sourced the box's .env carries
# credentials under names nobody registered, and inheriting-then-scrubbing can
# only remove the names we thought of.
REHEARSAL_SINK_URL = "http://127.0.0.1:1/__rehearsal_sink__"

_REHEARSAL_ENV_PASSTHROUGH = (
    # POSIX
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME", "SHELL",
    # Windows (the repo's non-POSIX test runner)
    "USERPROFILE", "TMP", "TEMP", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC",
    "WINDIR", "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "APPDATA", "LOCALAPPDATA",
    # Python / pytest
    "PYTHONPATH", "PYTHONHASHSEED", "PYTHONIOENCODING", "PYTHONUTF8",
    "VIRTUAL_ENV", "PYTEST_CURRENT_TEST",
)


def sterilize_subprocess_env(env: dict, *, env_file: Path, notify_owner_bin: Path) -> dict:
    """Neutralise every outbound credential in a subprocess environment.

    Layers 1-3 of the rehearsal contract (strip credentials / close the .env
    file fallback / repoint endpoints at sinks) WITHOUT setting the rehearsal
    marker. That split is deliberate: the marker also makes the runtime
    chokepoints refuse, which would change what the existing copied-state
    suites assert. Those suites need to stay behaviourally identical while
    becoming credential-sterile — so they get layers 1-3 and purpose-built
    rehearsals additionally get layer 4.

    Mutates and returns ``env`` so it can wrap an existing builder in one line.

    ``env_file`` is written with the sterile body if it does not already exist;
    ``notify_owner_bin`` should be a path that does NOT exist, so a sandbox that
    installed the real shift-agent-notify-owner into /usr/local/bin cannot have
    safe_io's NOTIFY_OWNER_BIN default resolve to it.
    """
    import safe_io as _safe_io

    for name in _safe_io.REHEARSAL_FORBIDDEN_ENV_VARS:
        env[name] = ""
    env_file = Path(env_file)
    if not env_file.exists():
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(_safe_io.sterile_env_file_body(), encoding="utf-8")
    env["HERMES_ENV_PATH"] = str(env_file)
    env["SHIFT_AGENT_ENV_PATH"] = str(env_file)
    env["PUSHOVER_API_URL"] = REHEARSAL_SINK_URL
    env["STRIPE_ACCOUNT_URL"] = REHEARSAL_SINK_URL
    env["OPENAI_IMAGE_EDIT_URL"] = REHEARSAL_SINK_URL
    env["OPENROUTER_URL"] = REHEARSAL_SINK_URL
    env["SHIFT_AGENT_NOTIFY_OWNER_BIN"] = str(notify_owner_bin)
    return env


class RehearsalSandbox:
    """A credential-sterile copy of production state.

    Attributes:
        root:        sandbox directory (contains config.yaml, state/, logs/)
        config_path: the SANITISED config.yaml the rehearsal reads
        env_file:    the sterile .env every credential resolver is pointed at
        env:         a COMPLETE environment for subprocess.run(env=...) — pass
                     it directly; merging os.environ back in re-opens the door
                     the allowlist exists to close.
    """

    __slots__ = ("root", "config_path", "env_file", "env")

    def __init__(self, root: Path, config_path: Path, env_file: Path, env: dict):
        self.root = root
        self.config_path = config_path
        self.env_file = env_file
        self.env = env


def build_rehearsal_sandbox(
    root: Path,
    *,
    source_config,
    bridge_url: str | None = None,
    _sanitizer=None,
    _on_ready=None,
) -> RehearsalSandbox:
    """Derive a credential-sterile rehearsal sandbox from a production config.

    Args:
        root: directory to build the sandbox in (created if absent).
        source_config: a config dict, or a Path to a real ``config.yaml``.
        bridge_url: local stub to send to. Defaults to a CLOSED loopback sink,
            so a rehearsal that was not given a stub cannot deliver anything.
        _sanitizer / _on_ready: test seams. ``_sanitizer`` substitutes the
            sanitisation step so a test can prove the VERIFIER catches a
            sanitiser that skipped a key; ``_on_ready`` is the "business code
            would start here" hook, used to prove the refusal happens BEFORE it.

    Raises:
        safe_io.RehearsalCredentialLeak: before writing anything the rehearsal
            would run against, if any credential survived. Fail-closed by
            construction — the sandbox does not exist unless it is sterile.
    """
    import yaml as _yaml
    import safe_io as _safe_io

    root = Path(root)
    if isinstance(source_config, (str, Path)):
        raw = _yaml.safe_load(Path(source_config).read_text(encoding="utf-8")) or {}
    else:
        raw = source_config

    sanitize = _sanitizer or _safe_io.sanitize_config_for_rehearsal
    sanitized = sanitize(raw)

    # FAIL-CLOSED GATE #1 — before the config is written anywhere a script
    # could read it.
    _safe_io.assert_rehearsal_config_sterile(sanitized, source="rehearsal config.yaml")

    root.mkdir(parents=True, exist_ok=True)
    (root / "state").mkdir(exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    config_path = root / "config.yaml"
    config_path.write_text(_yaml.safe_dump(sanitized, sort_keys=False), encoding="utf-8")

    env_file = root / ".env"
    env_file.write_text(_safe_io.sterile_env_file_body(), encoding="utf-8")

    # ALLOWLIST base, not os.environ: an operator shell that has sourced the
    # box's .env carries credentials under names nobody registered, and
    # inherit-then-scrub can only remove the names we thought of.
    env = {k: os.environ[k] for k in _REHEARSAL_ENV_PASSTHROUGH if k in os.environ}
    # Layers 1-3, shared with the existing copied-state suites so there is ONE
    # credential-neutralisation implementation rather than two that drift.
    sterilize_subprocess_env(
        env,
        env_file=env_file,
        notify_owner_bin=root / "bin" / "no-rehearsal-pushover",
    )
    env["HERMES_BRIDGE_URL"] = bridge_url or REHEARSAL_SINK_URL
    # Prod-path isolation, same three doors the autouse fixtures above cover.
    env["SHIFT_AGENT_DECISIONS_LOG_PATH"] = str(root / "logs" / "decisions.log")
    env["SHIFT_AGENT_NOTIFY_DEDUP_STATE"] = str(root / "state" / "notify-dedup.json")
    env["SHIFT_AGENT_NOTIFY_FAILED_LOG"] = str(root / "logs" / "notify-failed.log")
    # Unmistakably a rehearsal — the marker the runtime chokepoints refuse on.
    env[_safe_io.REHEARSAL_MODE_ENV] = "1"

    # FAIL-CLOSED GATE #2 — the environment the subprocess would inherit.
    _safe_io.assert_rehearsal_env_sterile(env, source="rehearsal subprocess env")

    sandbox = RehearsalSandbox(root, config_path, env_file, env)
    if _on_ready is not None:
        _on_ready(sandbox)
    return sandbox


@pytest.fixture
def rehearsal_sandbox(tmp_path):
    """Factory fixture: ``rehearsal_sandbox(source_config=..., ...)``."""
    def _build(**kwargs):
        kwargs.setdefault("root", tmp_path / "rehearsal")
        root = kwargs.pop("root")
        return build_rehearsal_sandbox(root, **kwargs)
    return _build


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


# ── shared: substring privacy assertions must not scan machine timestamps ────
#
# A "this value must never appear in the record" assertion that runs
# `json.dumps(row)` also scans `ts`, which carries microsecond precision. A
# short numeric needle then collides with the clock and the test fails with no
# leak present:
#
#   "280"   fired 2026-08-23 on ...T02:57:27.280053+00:00
#   "900"   fired 2026-08-23 on a synced-report timestamp
#
# Both were real CI failures on unrelated PRs, and the first was "fixed" only at
# the single call site that happened to fire — which is why the second one was
# still there to find. Use this for any numeric needle short enough to appear in
# a timestamp. A long needle (a phone number, an 11-digit id) cannot collide and
# does not need it; keeping those unfiltered preserves the stronger assertion.
_MACHINE_TIME_FIELDS = ("ts", "timestamp", "created_at", "updated_at",
                        "generated_at", "synced_at", "completed_at")


def privacy_blob(record, extra_exclude=()):
    """json.dumps(record) with machine-generated time fields removed.

    Every other field is still scanned, so the privacy contract is unchanged —
    only the clock is excluded, and only because it cannot carry the value the
    assertion is protecting.
    """
    if not isinstance(record, dict):
        return json.dumps(record)
    drop = set(_MACHINE_TIME_FIELDS) | set(extra_exclude)
    return json.dumps({k: v for k, v in record.items() if k not in drop})
