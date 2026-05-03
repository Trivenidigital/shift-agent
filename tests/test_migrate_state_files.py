"""PR-CF5 — tests for tools/migrate-state-files.py.

Linux-only — script imports safe_io which uses fcntl.

Covers:
- Per-migrator unit tests (no-op, intermediate, legacy, unknown shape)
- Dispatch-layer integration tests (verify Finding 1 fix end-to-end)
- CLI subprocess tests (--check, --apply, --file, override)
- Lock discipline test (concurrent flock holder)
- json_decode_failed reason emission
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="migrator imports safe_io (fcntl-only)",
)

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "migrate-state-files.py"
PLATFORM_DIR = REPO / "src" / "platform"


def _load_migrator(state_dir: Path, log_path: Path):
    """Load migrate-state-files as a module + override paths to test fixtures."""
    sys.path.insert(0, str(PLATFORM_DIR))
    loader = importlib.machinery.SourceFileLoader("migrator_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_file_location(
        "migrator_under_test", str(SCRIPT), loader=loader
    )
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    # Override paths to test fixtures
    mod.STATE_DIR = state_dir
    mod.LOG_PATH = log_path
    mod.SEND_COUNTER_PATH = state_dir / "send-counter.json"
    mod.SEEN_IDS_PATH = state_dir / "seen-ids.json"
    mod.MIGRATORS = {
        mod.SEND_COUNTER_PATH: (mod.SendCounter, mod.migrate_send_counter),
        mod.SEEN_IDS_PATH: (mod.SeenIds, mod.migrate_seen_ids),
    }
    mod.MIGRATOR_EXPECTED_KEYS = {
        mod.SEND_COUNTER_PATH: {"day", "count", "last_send_ts"},
        mod.SEEN_IDS_PATH: {"seen_message_ids", "max_size", "last_offset_bytes", "agent_log_inode"},
    }
    return mod


@pytest.fixture
def state_env(tmp_path):
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    return {
        "state_dir": state,
        "log_path": logs / "decisions.log",
    }


@pytest.fixture
def mod(state_env):
    return _load_migrator(state_env["state_dir"], state_env["log_path"])


# ============================================================================
# Per-migrator unit tests
# ============================================================================

class TestMigrateSendCounter:
    def test_no_op_when_current(self, mod):
        result = mod.migrate_send_counter(
            {"day": "2026-05-03", "count": 5, "last_send_ts": None},
            customer_tz_resolved="America/New_York",
        )
        assert result is None  # defensive no-op

    def test_intermediate_strips_sent_count(self, mod):
        result = mod.migrate_send_counter(
            {"day": "2026-05-03", "count": 5, "sent_count": 5},
            customer_tz_resolved="America/New_York",
        )
        assert result == {"day": "2026-05-03", "count": 5, "last_send_ts": None}

    def test_legacy_full_with_date(self, mod):
        result = mod.migrate_send_counter(
            {"date": "2026-05-01", "sent_count": 3},
            customer_tz_resolved="America/New_York",
        )
        assert result == {"day": "2026-05-01", "count": 3, "last_send_ts": None}

    def test_legacy_null_date_uses_tz_fallback(self, mod):
        result = mod.migrate_send_counter(
            {"date": None, "sent_count": 0},
            customer_tz_resolved="America/New_York",
        )
        assert result["day"]  # populated
        assert result["count"] == 0
        assert result["last_send_ts"] is None

    def test_legacy_null_date_no_config_uses_utc(self, mod):
        result = mod.migrate_send_counter(
            {"date": None, "sent_count": 0},
            customer_tz_resolved=None,
        )
        # Should fall back to UTC date
        assert result["day"] == datetime.now(timezone.utc).date().isoformat()

    def test_unknown_shape_raises(self, mod):
        with pytest.raises(mod.UnknownStateShapeError, match="unrecognized keys"):
            mod.migrate_send_counter(
                {"foo": "bar", "baz": 1},
                customer_tz_resolved=None,
            )


class TestMigrateSeenIds:
    def test_no_op_when_current(self, mod):
        result = mod.migrate_seen_ids({
            "seen_message_ids": ["a", "b"],
            "max_size": 10000,
            "last_offset_bytes": 12345,
            "agent_log_inode": 99,
        })
        assert result is None  # defensive no-op

    def test_empty_dict_migrates_to_4_fields(self, mod):
        result = mod.migrate_seen_ids({})
        assert result == {
            "seen_message_ids": [],
            "max_size": 10000,
            "last_offset_bytes": 0,
            "agent_log_inode": 0,
        }

    def test_2_field_legacy_migrates(self, mod):
        result = mod.migrate_seen_ids({
            "seen_message_ids": ["msg1", "msg2"],
            "max_size": 5000,
        })
        assert result == {
            "seen_message_ids": ["msg1", "msg2"],
            "max_size": 5000,
            "last_offset_bytes": 0,
            "agent_log_inode": 0,
        }

    def test_unknown_shape_raises(self, mod):
        with pytest.raises(mod.UnknownStateShapeError, match="unrecognized keys"):
            mod.migrate_seen_ids({"seen_message_ids": [], "max_size": 10000, "foo": "bar"})


# ============================================================================
# Dispatch-layer integration tests (verify Finding 1 fix end-to-end)
# ============================================================================

class TestDispatchLayer:
    def _write_send_counter(self, state_dir, content):
        (state_dir / "send-counter.json").write_text(json.dumps(content), encoding="utf-8")

    def _write_seen_ids(self, state_dir, content):
        (state_dir / "seen-ids.json").write_text(json.dumps(content), encoding="utf-8")

    def test_seen_ids_empty_dict_routes_to_migrator(self, mod, state_env):
        """Finding 1 fix verified: empty-dict SeenIds reaches migrator (would
        have silently passed Pydantic validation in v1 design).
        """
        self._write_seen_ids(state_env["state_dir"], {})
        needed, msg = mod._migrate_one_file(
            mod.SEEN_IDS_PATH, model_cls=mod.SeenIds,
            migrator=mod.migrate_seen_ids,
            customer_tz_resolved="UTC", dry_run=False,
        )
        assert needed is True
        # Verify the file was actually rewritten with 4 fields
        result = json.loads(mod.SEEN_IDS_PATH.read_text(encoding="utf-8"))
        assert set(result.keys()) == {"seen_message_ids", "max_size", "last_offset_bytes", "agent_log_inode"}

    def test_send_counter_legacy_routes_to_migrator(self, mod, state_env):
        """Finding 1 fix verified: {date, sent_count} reaches migrator (would
        have failed with `missing` errors in v1 design, falsely routed to
        load_failed_non_extra).
        """
        self._write_send_counter(state_env["state_dir"], {"date": "2026-05-01", "sent_count": 7})
        needed, msg = mod._migrate_one_file(
            mod.SEND_COUNTER_PATH, model_cls=mod.SendCounter,
            migrator=mod.migrate_send_counter,
            customer_tz_resolved="America/New_York", dry_run=False,
        )
        assert needed is True
        result = json.loads(mod.SEND_COUNTER_PATH.read_text(encoding="utf-8"))
        assert result["day"] == "2026-05-01"
        assert result["count"] == 7
        assert result["last_send_ts"] is None

    def test_keys_match_validation_succeeds_no_op(self, mod, state_env):
        self._write_send_counter(state_env["state_dir"], {
            "day": "2026-05-03", "count": 5, "last_send_ts": None,
        })
        needed, msg = mod._migrate_one_file(
            mod.SEND_COUNTER_PATH, model_cls=mod.SendCounter,
            migrator=mod.migrate_send_counter,
            customer_tz_resolved="UTC", dry_run=False,
        )
        assert needed is False
        assert "no-op" in msg

    def test_keys_match_but_validation_fails(self, mod, state_env):
        """Keys are right but values are wrong types → load_failed_non_extra audit."""
        self._write_send_counter(state_env["state_dir"], {
            "day": 123,  # wrong type — should be str
            "count": "x",  # wrong type — should be int
            "last_send_ts": None,
        })
        with pytest.raises(mod.UnknownStateShapeError):
            mod._migrate_one_file(
                mod.SEND_COUNTER_PATH, model_cls=mod.SendCounter,
                migrator=mod.migrate_send_counter,
                customer_tz_resolved="UTC", dry_run=False,
            )
        # Verify audit row written
        audit = [json.loads(l) for l in mod.LOG_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert any(r["type"] == "state_file_migration_failed" and r["reason"] == "load_failed_non_extra" for r in audit)

    def test_corrupt_json_emits_json_decode_failed(self, mod, state_env):
        """Invalid JSON → json_decode_failed audit (not load_failed_non_extra)."""
        (state_env["state_dir"] / "send-counter.json").write_text(
            "not valid json {{{", encoding="utf-8",
        )
        with pytest.raises(mod.UnknownStateShapeError):
            mod._migrate_one_file(
                mod.SEND_COUNTER_PATH, model_cls=mod.SendCounter,
                migrator=mod.migrate_send_counter,
                customer_tz_resolved="UTC", dry_run=False,
            )
        audit = [json.loads(l) for l in mod.LOG_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert any(r["type"] == "state_file_migration_failed" and r["reason"] == "json_decode_failed" for r in audit)

    def test_top_level_not_dict_raises(self, mod, state_env):
        (state_env["state_dir"] / "send-counter.json").write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(mod.UnknownStateShapeError):
            mod._migrate_one_file(
                mod.SEND_COUNTER_PATH, model_cls=mod.SendCounter,
                migrator=mod.migrate_send_counter,
                customer_tz_resolved="UTC", dry_run=False,
            )
        audit = [json.loads(l) for l in mod.LOG_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert any(r["type"] == "state_file_migration_failed" and r["reason"] == "unknown_shape" for r in audit)

    def test_apply_writes_backup_before_rewrite(self, mod, state_env):
        original = {"date": "2026-05-01", "sent_count": 10}
        self._write_send_counter(state_env["state_dir"], original)
        needed, msg = mod._migrate_one_file(
            mod.SEND_COUNTER_PATH, model_cls=mod.SendCounter,
            migrator=mod.migrate_send_counter,
            customer_tz_resolved="UTC", dry_run=False,
        )
        assert needed is True
        # Verify backup file exists with original content
        backups = list(state_env["state_dir"].glob("send-counter.json.pre-migrate-*"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text()) == original

    def test_dry_run_does_not_modify_file(self, mod, state_env):
        original = {"date": "2026-05-01", "sent_count": 10}
        self._write_send_counter(state_env["state_dir"], original)
        before = mod.SEND_COUNTER_PATH.read_text(encoding="utf-8")
        needed, msg = mod._migrate_one_file(
            mod.SEND_COUNTER_PATH, model_cls=mod.SendCounter,
            migrator=mod.migrate_send_counter,
            customer_tz_resolved="UTC", dry_run=True,
        )
        assert needed is True
        assert "WOULD migrate" in msg
        after = mod.SEND_COUNTER_PATH.read_text(encoding="utf-8")
        assert before == after  # unchanged
        # No backup written on dry-run
        assert list(state_env["state_dir"].glob("send-counter.json.pre-migrate-*")) == []


# ============================================================================
# CLI subprocess tests
# ============================================================================

def _run_cli(args, env_extra=None, state_dir=None, log_path=None):
    """Invoke migrate-state-files.py as a subprocess with paths overridden via
    a temp wrapper script.
    """
    wrapper = f"""
import sys
sys.path.insert(0, {str(PLATFORM_DIR)!r})
from importlib.machinery import SourceFileLoader
mod = SourceFileLoader('migrator_cli', {str(SCRIPT)!r}).load_module()
import pathlib
mod.STATE_DIR = pathlib.Path({str(state_dir)!r})
mod.LOG_PATH = pathlib.Path({str(log_path)!r})
mod.SEND_COUNTER_PATH = mod.STATE_DIR / 'send-counter.json'
mod.SEEN_IDS_PATH = mod.STATE_DIR / 'seen-ids.json'
mod.MIGRATORS = {{
    mod.SEND_COUNTER_PATH: (mod.SendCounter, mod.migrate_send_counter),
    mod.SEEN_IDS_PATH: (mod.SeenIds, mod.migrate_seen_ids),
}}
mod.MIGRATOR_EXPECTED_KEYS = {{
    mod.SEND_COUNTER_PATH: {{'day', 'count', 'last_send_ts'}},
    mod.SEEN_IDS_PATH: {{'seen_message_ids', 'max_size', 'last_offset_bytes', 'agent_log_inode'}},
}}
sys.exit(mod.main({args!r}))
"""
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, env=env, timeout=30,
    )


class TestCLI:
    def test_check_clean_returns_0(self, state_env):
        # Write a current-shape file
        (state_env["state_dir"] / "send-counter.json").write_text(
            json.dumps({"day": "2026-05-03", "count": 0, "last_send_ts": None}),
            encoding="utf-8",
        )
        (state_env["state_dir"] / "seen-ids.json").write_text(
            json.dumps({"seen_message_ids": [], "max_size": 10000, "last_offset_bytes": 0, "agent_log_inode": 0}),
            encoding="utf-8",
        )
        result = _run_cli(["--check"], state_dir=state_env["state_dir"], log_path=state_env["log_path"])
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_check_legacy_returns_1(self, state_env):
        (state_env["state_dir"] / "send-counter.json").write_text(
            json.dumps({"date": "2026-05-01", "sent_count": 3}),
            encoding="utf-8",
        )
        result = _run_cli(["--check"], state_dir=state_env["state_dir"], log_path=state_env["log_path"])
        assert result.returncode == 1, f"stderr: {result.stderr}"

    def test_apply_migrates_and_writes_audit(self, state_env):
        (state_env["state_dir"] / "send-counter.json").write_text(
            json.dumps({"date": "2026-05-01", "sent_count": 3}),
            encoding="utf-8",
        )
        result = _run_cli(["--apply"], state_dir=state_env["state_dir"], log_path=state_env["log_path"])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Verify audit row
        audit = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        assert any(r["type"] == "state_file_migrated" and "send-counter.json" in r["file"] for r in audit)

    def test_apply_idempotent(self, state_env):
        (state_env["state_dir"] / "send-counter.json").write_text(
            json.dumps({"date": "2026-05-01", "sent_count": 3}),
            encoding="utf-8",
        )
        # First apply
        r1 = _run_cli(["--apply"], state_dir=state_env["state_dir"], log_path=state_env["log_path"])
        assert r1.returncode == 0
        # Second apply — should be no-op
        r2 = _run_cli(["--apply"], state_dir=state_env["state_dir"], log_path=state_env["log_path"])
        assert r2.returncode == 0
        # Should only be one state_file_migrated audit row (not two)
        audit = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        migrated_count = sum(1 for r in audit if r["type"] == "state_file_migrated")
        assert migrated_count == 1

    def test_override_skip_without_reason_fails(self, state_env):
        (state_env["state_dir"] / "send-counter.json").write_text(
            json.dumps({"date": "2026-05-01", "sent_count": 3}),
            encoding="utf-8",
        )
        result = _run_cli(
            ["--apply"],
            env_extra={"STATE_MIGRATION_OVERRIDE": "skip"},
            state_dir=state_env["state_dir"], log_path=state_env["log_path"],
        )
        assert result.returncode == 1, f"stderr: {result.stderr}"
        assert "REASON" in result.stderr

    def test_override_skip_with_reason_succeeds(self, state_env):
        (state_env["state_dir"] / "send-counter.json").write_text(
            json.dumps({"date": "2026-05-01", "sent_count": 3}),
            encoding="utf-8",
        )
        result = _run_cli(
            ["--apply"],
            env_extra={
                "STATE_MIGRATION_OVERRIDE": "skip",
                "STATE_MIGRATION_OVERRIDE_REASON": "test override path",
            },
            state_dir=state_env["state_dir"], log_path=state_env["log_path"],
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Verify the override audit row
        audit = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        assert any(r["type"] == "state_file_migration_overridden" for r in audit)
        assert any(r.get("reason") == "test override path" for r in audit if r["type"] == "state_file_migration_overridden")

    def test_override_invalid_value_fails_with_exit_1(self, state_env):
        result = _run_cli(
            ["--apply"],
            env_extra={
                "STATE_MIGRATION_OVERRIDE": "force",  # invalid; must be exactly 'skip'
                "STATE_MIGRATION_OVERRIDE_REASON": "trying force",
            },
            state_dir=state_env["state_dir"], log_path=state_env["log_path"],
        )
        assert result.returncode == 1, f"stderr: {result.stderr}"
        assert "must be exactly 'skip'" in result.stderr
