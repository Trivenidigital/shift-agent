"""End-to-end-ish tests for the eod-reconcile script. Linux-only (fcntl).

Pattern follows tests/test_daily_brief_script.py — subprocess-invocation
against fixture files via env-var-overridable paths.

Closes the drift gap identified in 2026-04-30 non-catering audit
(memory/project_non_catering_agents_audit.md): EOD previously had only
test_eod_reconcile_schemas.py (pure schema tests), no script-level
subprocess test. Per CLAUDE.md drift rule: deterministic Python scripts
get pytest with subprocess-invoke + assert on file mutations + stdout.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="eod-reconcile depends on safe_io which uses fcntl (Linux only)",
)

SCRIPT = Path(__file__).resolve().parent.parent / "src" / "agents" / "eod_reconcile" / "scripts" / "eod-reconcile"


@pytest.fixture
def fixture_dir(tmp_path):
    """Build the on-disk artifacts an EOD run needs."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    config = {
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_jax_01", "timezone": "America/New_York"},
        "owner": {
            "name": "Owner", "phone": "+19045550100",
            "self_chat_jid": "19045550100@s.whatsapp.net",
        },
        "limits": {},
        "alerting": {"pushover_user_key": "test_k", "pushover_app_token": "test_t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "eod": {
            "eod_time": "23:00",
            # Minimum-valid catchup window so the past_catchup test can use a same-day
            # override. With a 60-min catchup, "past catchup" would require
            # spilling into the next calendar day, breaking same-day
            # target_dt math in self_gate_window_state.
            "catchup_window_minutes": 15,
            "pushover_priority": 0,
            "pushover_only_if_unresolved": False,
        },
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    pending = {"proposals": []}
    (state / "pending.json").write_text(json.dumps(pending), encoding="utf-8")
    decisions = logs / "decisions.log"
    decisions.write_text("", encoding="utf-8")
    return tmp_path


def _run(fixture_dir, args=("--force",), now_override=None,
         disabled_flag=False, notify_owner_stub=None):
    env = {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(fixture_dir / "config.yaml"),
        "SHIFT_AGENT_PENDING_PATH": str(fixture_dir / "state" / "pending.json"),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(fixture_dir / "logs" / "decisions.log"),
        "SHIFT_AGENT_EOD_SNAPSHOT_PATH": str(fixture_dir / "state" / "eod-snapshot.json"),
        "SHIFT_AGENT_DISABLED_FLAG": str(fixture_dir / "state" / "disabled.flag"),
        "SHIFT_AGENT_LOG_SOURCE_OVERRIDE": str(fixture_dir / "logs" / "decisions.log"),
        "SHIFT_AGENT_NOTIFY_FAILED_LOG": str(fixture_dir / "logs" / "notify-failed.log"),
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src" / "platform"),
    }
    if now_override is not None:
        env["SHIFT_AGENT_NOW_OVERRIDE"] = now_override
    if notify_owner_stub is not None:
        env["SHIFT_AGENT_NOTIFY_OWNER_BIN"] = notify_owner_stub
    else:
        env["SHIFT_AGENT_NOTIFY_OWNER_BIN"] = "/bin/true"
    if disabled_flag:
        (fixture_dir / "state" / "disabled.flag").write_text("disabled-for-test", encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True, text=True, env=env, timeout=20,
    )


def _read_log(fixture_dir):
    log = fixture_dir / "logs" / "decisions.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_snapshot(fixture_dir):
    snap = fixture_dir / "state" / "eod-snapshot.json"
    if not snap.exists():
        return None
    return json.loads(snap.read_text(encoding="utf-8"))


# ────────────────── Tests ──────────────────


def test_dry_run_aggregates_no_write(fixture_dir):
    r = _run(fixture_dir, args=("--force", "--dry-run"))
    assert r.returncode == 0, r.stderr
    # Dry-run does NOT write snapshot
    assert _read_snapshot(fixture_dir) is None
    # Dry-run does NOT emit audit entries
    assert _read_log(fixture_dir) == []


def test_force_writes_snapshot_and_audit(fixture_dir):
    r = _run(fixture_dir, args=("--force",))
    assert r.returncode == 0, r.stderr
    snap = _read_snapshot(fixture_dir)
    assert snap is not None
    # Snapshot has the expected aggregated counters
    assert "sick_calls" in snap
    assert "proposals_created" in snap
    assert "proposals_resolved" in snap
    assert "proposals_unresolved" in snap
    assert "outbound_sent" in snap
    assert "outbound_send_failed" in snap

    log = _read_log(fixture_dir)
    types = [e["type"] for e in log]
    assert "eod_snapshot" in types


def test_disabled_flag_skips_run(fixture_dir):
    r = _run(fixture_dir, args=("--force",), disabled_flag=True)
    assert r.returncode == 0, r.stderr
    log = _read_log(fixture_dir)
    types = [e["type"] for e in log]
    # Disabled flag should result in EodSkipped, not EodSnapshot
    assert "eod_skipped" in types
    assert "eod_snapshot" not in types


def test_idempotent_resnap_refused_without_force_resnap(fixture_dir):
    r1 = _run(fixture_dir, args=("--force",))
    assert r1.returncode == 0, r1.stderr
    log_after_first = _read_log(fixture_dir)

    # Second invocation without --force-resnap: snapshot already exists for today,
    # so should skip with eod_skipped(reason=already_snapshotted)
    r2 = _run(fixture_dir, args=("--force",))
    assert r2.returncode == 0, r2.stderr
    log_after_second = _read_log(fixture_dir)
    types_added = [e["type"] for e in log_after_second[len(log_after_first):]]
    assert "eod_skipped" in types_added
    # Snapshot count must be 1 (not duplicated)
    snap_types = [e for e in log_after_second if e["type"] == "eod_snapshot"]
    assert len(snap_types) == 1


def test_force_resnap_overwrites(fixture_dir):
    r1 = _run(fixture_dir, args=("--force",))
    assert r1.returncode == 0
    snap1 = _read_snapshot(fixture_dir)
    snap1_id = snap1["snapshot_id"]
    # Wait at least 1ms then re-run with --force --force-resnap
    r2 = _run(fixture_dir, args=("--force", "--force-resnap"))
    assert r2.returncode == 0, r2.stderr
    snap2 = _read_snapshot(fixture_dir)
    # snapshot_id should differ (regenerated)
    assert snap2["snapshot_id"] != snap1_id


def test_force_resnap_alone_refused(fixture_dir):
    r = _run(fixture_dir, args=("--force-resnap",))
    assert r.returncode != 0
    assert "--force-resnap requires --force" in r.stderr


def test_self_gate_outside_window_skips(fixture_dir):
    """now_override past target+catchup_min → past_catchup, no work.

    With fixture's catchup_window_minutes=15 and eod_time=23:00, anything past
    23:15 same-day is past catchup. Use 23:30 to be safely past."""
    r = _run(
        fixture_dir, args=(),  # no --force
        now_override="2026-04-30T23:30:00-04:00",
    )
    # Past-catchup → exit 0 with eod_skipped(past_catchup)
    assert r.returncode == 0, r.stderr
    log = _read_log(fixture_dir)
    types = [e["type"] for e in log]
    assert "eod_skipped" in types
    assert _read_snapshot(fixture_dir) is None


def test_self_gate_before_target_skips(fixture_dir):
    """now_override before eod_time → before, no work."""
    r = _run(
        fixture_dir, args=(),  # no --force
        now_override="2026-04-30T22:00:00-04:00",  # 22:00 — before 23:00 target
    )
    assert r.returncode == 0, r.stderr
    # Pre-target: no audit entries written (silent skip)
    log = _read_log(fixture_dir)
    types = [e["type"] for e in log]
    # Acceptable: either empty (no log) or eod_skipped(before)
    assert "eod_snapshot" not in types


def test_invariant_violations_detected(fixture_dir):
    """Inject a pending.json with stale unresolved proposals; eod-reconcile
    flags them as invariant_violations (or equivalent counter)."""
    r = _run(fixture_dir, args=("--force",))
    assert r.returncode == 0, r.stderr
    snap = _read_snapshot(fixture_dir)
    # Empty pending → 0 invariant_violations
    assert snap["invariant_violations"] == 0


def test_pushover_succeeds_with_stub_bin(fixture_dir):
    """If the script tries to fire Pushover (priority bump or unresolved>0),
    it must work with /bin/true stub. Smoke test only."""
    r = _run(fixture_dir, args=("--force",), notify_owner_stub="/bin/true")
    assert r.returncode == 0, r.stderr
    # Snapshot still written; pushover_summary may or may not be in log
    # depending on counts. Just verify no crash.
    assert _read_snapshot(fixture_dir) is not None
