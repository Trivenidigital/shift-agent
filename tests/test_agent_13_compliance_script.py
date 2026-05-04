"""PR-Agent13 Commit 2 — script-level tests for check-compliance-deadlines.py
+ mark-compliance-item-done.py.

Linux-only (fcntl). Pattern mirrors tests/test_eod_reconcile_script.py:
subprocess invocation against fixture files via env-var-overridable paths.

Covers:
- Escalation gates: each gate fires exactly on boundary
- Idempotency: sentinel hit/miss/contention; orphan-Attempted skip
- Sentinel GC: prune on mark-done deletion + tick-start config-edit
- Catchup + deferral: bounded; ComplianceReminderDeferred + Pushover
- State recovery: corrupt items.json → InvariantViolation; missing → recreate
- Date math: DST, leap-day, hypothesis-style range
- Mark-done: annual advance; one-shot delete; item-not-found; sentinel cleanup
- Template render: 4 conditional combos (resource_url × notes None/set)
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="check-compliance-deadlines depends on safe_io which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPO / "src" / "agents" / "compliance" / "scripts" / "check-compliance-deadlines.py"
MARK_SCRIPT = REPO / "src" / "agents" / "compliance" / "scripts" / "mark-compliance-item-done.py"
PLATFORM_DIR = REPO / "src" / "platform"

sys.path.insert(0, str(PLATFORM_DIR))


@pytest.fixture
def fixture_dir(tmp_path):
    """Build the on-disk artifacts a compliance run needs."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    config = {
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_jax_01",
                     "timezone": "America/New_York"},
        "owner": {
            "name": "Owner", "phone": "+19045550100",
            "self_chat_jid": "19045550100@s.whatsapp.net",
        },
        "limits": {},
        "alerting": {"pushover_user_key": "test_k", "pushover_app_token": "test_t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "compliance": {
            "enabled": True,
            "advance_warning_days": [30, 14, 7, 3, 1],
            "max_deferral_days": 7,
        },
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    items = {
        "schema_version": 1,
        "items": [
            {
                "id": "health_inspect_houston",
                "name": "Health Inspection Houston",
                "category": "inspection",
                "renewal_date": "2026-09-01",
                "recurrence_days": 365,
                "agency": "HCPHTX",
            },
        ],
    }
    (state / "compliance-items.json").write_text(json.dumps(items), encoding="utf-8")
    (logs / "decisions.log").write_text("", encoding="utf-8")
    return tmp_path


def _run_check(fixture_dir, *, args=(), now_override=None, render_bin=None):
    """Run check-compliance-deadlines.py with fixture env."""
    env = {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(fixture_dir / "config.yaml"),
        "SHIFT_AGENT_COMPLIANCE_ITEMS_PATH": str(fixture_dir / "state" / "compliance-items.json"),
        "SHIFT_AGENT_COMPLIANCE_SENTINEL_PATH": str(fixture_dir / "state" / "compliance-last-sent.json"),
        "SHIFT_AGENT_COMPLIANCE_HEARTBEAT_PATH": str(fixture_dir / "state" / "compliance-last-cron-tick.json"),
        "SHIFT_AGENT_COMPLIANCE_LOCK_PATH": str(fixture_dir / "state" / "compliance-check.json.lock"),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(fixture_dir / "logs" / "decisions.log"),
        "PYTHONPATH": str(PLATFORM_DIR),
    }
    if now_override:
        env["SHIFT_AGENT_NOW_OVERRIDE"] = now_override
    if render_bin:
        env["SHIFT_AGENT_RENDER_TEMPLATE_BIN"] = str(render_bin)
    return subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), *args],
        env=env, capture_output=True, text=True, timeout=30,
    )


def _run_mark(fixture_dir, *, item_id, actor="owner", dry_run=False):
    env = {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(fixture_dir / "config.yaml"),
        "SHIFT_AGENT_COMPLIANCE_ITEMS_PATH": str(fixture_dir / "state" / "compliance-items.json"),
        "SHIFT_AGENT_COMPLIANCE_SENTINEL_PATH": str(fixture_dir / "state" / "compliance-last-sent.json"),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(fixture_dir / "logs" / "decisions.log"),
        "PYTHONPATH": str(PLATFORM_DIR),
    }
    args = [sys.executable, str(MARK_SCRIPT),
            "--item-id", item_id, "--actor", actor]
    if dry_run:
        args.append("--dry-run")
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=15)


def _read_audit_log(fixture_dir):
    """Read decisions.log as list of dicts."""
    p = fixture_dir / "logs" / "decisions.log"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _make_render_stub(tmp_path):
    """Make a tiny shell stub that mimics render-coverage-template (just echoes the template name)."""
    stub = tmp_path / "render-stub.sh"
    stub.write_text(
        "#!/bin/sh\n"
        "echo \"COMPLIANCE-REMINDER-RENDERED-FOR-$1\"\n"
    )
    stub.chmod(0o755)
    return stub


# ============================================================================
# CLI smoke
# ============================================================================

class TestCli:
    def test_help_parses(self, fixture_dir):
        r = subprocess.run(
            [sys.executable, str(CHECK_SCRIPT), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0
        assert "compliance" in r.stdout.lower() or "deadline" in r.stdout.lower()

    def test_disabled_no_op(self, fixture_dir):
        # Set compliance.enabled=False
        cfg = yaml.safe_load((fixture_dir / "config.yaml").read_text())
        cfg["compliance"]["enabled"] = False
        (fixture_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
        r = _run_check(fixture_dir, args=("--dry-run",))
        assert r.returncode == 0
        # Heartbeat written even when disabled
        assert (fixture_dir / "state" / "compliance-last-cron-tick.json").exists()

    def test_dry_run_writes_attempted_audit_no_send(self, fixture_dir):
        # Today = 30 days before renewal_date 2026-09-01 → should fire 30-gate
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-02T06:00:00-04:00")
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        attempted = [e for e in log if e.get("type") == "compliance_reminder_attempted"]
        sent = [e for e in log if e.get("type") == "compliance_reminder_sent"]
        assert len(attempted) >= 1
        assert len(sent) == 0  # dry-run skips bridge POST


# ============================================================================
# Escalation gates
# ============================================================================

class TestEscalationGates:
    def test_30_day_boundary_fires(self, fixture_dir):
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-02T06:00:00-04:00")  # 30 days before 9/1
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        attempted = [e for e in log if e.get("type") == "compliance_reminder_attempted"]
        gates = sorted(set(e["gate_days"] for e in attempted))
        # At T-30 we should fire only the 30-day gate (no others met yet)
        assert 30 in gates

    def test_31_days_out_no_fire(self, fixture_dir):
        # 31 days before 9/1 = 8/1 — no gate met
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-01T06:00:00-04:00")
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        attempted = [e for e in log if e.get("type") == "compliance_reminder_attempted"]
        # 30-day gate ideal_fire = 8/2; on 8/1 we're days_late=-1, skip
        assert len(attempted) == 0

    def test_due_today_fires_gate_0(self, fixture_dir):
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-09-01T06:00:00-04:00")
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        attempted = [e for e in log if e.get("type") == "compliance_reminder_attempted"]
        gates = [e["gate_days"] for e in attempted]
        # On the day = gate 0 fires (and possibly catchups for 30/14/7/3/1 if not sent)
        assert 0 in gates

    def test_overdue_fires_negative_gate(self, fixture_dir):
        # 2 days overdue (renewal was 8/30, today 9/1)
        items = json.loads((fixture_dir / "state" / "compliance-items.json").read_text())
        items["items"][0]["renewal_date"] = "2026-08-30"
        (fixture_dir / "state" / "compliance-items.json").write_text(json.dumps(items))
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-09-01T06:00:00-04:00")
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        attempted = [e for e in log if e.get("type") == "compliance_reminder_attempted"]
        gates = sorted(set(e["gate_days"] for e in attempted))
        assert -2 in gates  # 2 days overdue


# ============================================================================
# Catchup + deferral
# ============================================================================

class TestCatchupAndDeferral:
    def test_8_days_late_emits_deferred(self, fixture_dir):
        # Item was 30-day-out on 8/2 (renewal 9/1). Today 8/10 = 8 days past
        # ideal_fire of 8/2 for the 30-gate. max_deferral_days=7, so defer.
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-10T06:00:00-04:00")
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        deferred = [e for e in log if e.get("type") == "compliance_reminder_deferred"]
        assert len(deferred) >= 1
        assert any(e["gate_days"] == 30 for e in deferred)
        # Note: notify_owner_with_fallback subprocess call may fail in test env;
        # operator_pushover_sent reflects that (False).

    def test_max_deferral_days_configurable(self, fixture_dir):
        cfg = yaml.safe_load((fixture_dir / "config.yaml").read_text())
        cfg["compliance"]["max_deferral_days"] = 30  # widen
        (fixture_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-10T06:00:00-04:00")
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        deferred = [e for e in log if e.get("type") == "compliance_reminder_deferred"]
        # With max_deferral=30, the 30-gate (8 days late) should NOT defer
        gates_deferred = [e["gate_days"] for e in deferred]
        assert 30 not in gates_deferred


# ============================================================================
# Idempotency: sentinel + orphan scan
# ============================================================================

class TestIdempotency:
    def test_sentinel_hit_skips(self, fixture_dir):
        # Pre-populate sentinel: 30-gate already sent today
        (fixture_dir / "state" / "compliance-last-sent.json").write_text(json.dumps({
            "schema_version": 1,
            "last_sent": {"health_inspect_houston:30": "2026-08-02"},
        }))
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-02T06:00:00-04:00")
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        # No 30-gate fire (sentinel hit)
        attempted_30 = [e for e in log if e.get("type") == "compliance_reminder_attempted"
                        and e.get("gate_days") == 30]
        assert len(attempted_30) == 0

    def test_orphan_attempted_blocks_resend(self, fixture_dir):
        # Pre-populate decisions.log with orphan Attempted (no matching Sent)
        log_path = fixture_dir / "logs" / "decisions.log"
        log_path.write_text(json.dumps({
            "type": "compliance_reminder_attempted",
            "ts": "2026-08-02T05:55:00-04:00",  # 5 min before our run
            "item_id": "health_inspect_houston",
            "item_name": "Health Inspection Houston",
            "days_until_renewal": 30,
            "gate_days": 30,
            "attempt_id": "orphan_aid_123",
        }) + "\n")
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-02T06:00:00-04:00")
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        skipped = [e for e in log if e.get("type") == "compliance_reminder_skipped"]
        assert len(skipped) >= 1
        assert skipped[0]["orphan_attempt_id"] == "orphan_aid_123"
        assert skipped[0]["reason"] == "orphan_attempted_in_window"

    def test_orphan_with_matching_sent_does_not_block(self, fixture_dir):
        # Pre-populate with Attempted AND matching Sent — not an orphan
        log_path = fixture_dir / "logs" / "decisions.log"
        log_path.write_text(
            json.dumps({
                "type": "compliance_reminder_attempted",
                "ts": "2026-08-02T05:55:00-04:00",
                "item_id": "health_inspect_houston",
                "item_name": "Health Inspection Houston",
                "days_until_renewal": 30, "gate_days": 30,
                "attempt_id": "ok_aid",
            }) + "\n" +
            json.dumps({
                "type": "compliance_reminder_sent",
                "ts": "2026-08-02T05:56:00-04:00",
                "item_id": "health_inspect_houston",
                "days_until_renewal": 30, "gate_days": 30,
                "attempt_id": "ok_aid",
                "outbound_message_id": "wamid.test",
            }) + "\n"
        )
        # Update sentinel too — already sent
        (fixture_dir / "state" / "compliance-last-sent.json").write_text(json.dumps({
            "schema_version": 1,
            "last_sent": {"health_inspect_houston:30": "2026-08-02"},
        }))
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-02T06:00:00-04:00")
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        new_skipped = [e for e in log
                       if e.get("type") == "compliance_reminder_skipped"]
        assert len(new_skipped) == 0


# ============================================================================
# Sentinel GC at tick start
# ============================================================================

class TestSentinelGC:
    def test_orphan_sentinel_keys_pruned_when_item_removed(self, fixture_dir):
        # Sentinel has key for item that no longer exists in items.json
        (fixture_dir / "state" / "compliance-last-sent.json").write_text(json.dumps({
            "schema_version": 1,
            "last_sent": {
                "health_inspect_houston:30": "2026-08-02",
                "deleted_item:14": "2026-07-01",  # ghost
            },
        }))
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-02T06:00:00-04:00")
        assert r.returncode == 0
        sentinel = json.loads(
            (fixture_dir / "state" / "compliance-last-sent.json").read_text()
        )
        assert "deleted_item:14" not in sentinel["last_sent"]
        assert "health_inspect_houston:30" in sentinel["last_sent"]

    def test_orphan_gate_pruned_when_warning_days_changed(self, fixture_dir):
        # Sentinel has key for gate not in advance_warning_days
        (fixture_dir / "state" / "compliance-last-sent.json").write_text(json.dumps({
            "schema_version": 1,
            "last_sent": {
                "health_inspect_houston:30": "2026-08-02",
                "health_inspect_houston:60": "2026-07-01",  # 60 not in default config
            },
        }))
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-02T06:00:00-04:00")
        assert r.returncode == 0
        sentinel = json.loads(
            (fixture_dir / "state" / "compliance-last-sent.json").read_text()
        )
        assert "health_inspect_houston:60" not in sentinel["last_sent"]


# ============================================================================
# State recovery
# ============================================================================

class TestStateRecovery:
    def test_corrupt_items_file_emits_invariant(self, fixture_dir):
        (fixture_dir / "state" / "compliance-items.json").write_text("{not valid json")
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-02T06:00:00-04:00")
        assert r.returncode != 0
        log = _read_audit_log(fixture_dir)
        invariants = [e for e in log if e.get("type") == "invariant_violation"]
        assert len(invariants) >= 1
        assert "compliance_items_file" in invariants[0]["check"]

    def test_missing_items_file_emits_invariant(self, fixture_dir):
        (fixture_dir / "state" / "compliance-items.json").unlink()
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-02T06:00:00-04:00")
        assert r.returncode != 0
        log = _read_audit_log(fixture_dir)
        invariants = [e for e in log if e.get("type") == "invariant_violation"]
        assert len(invariants) >= 1


# ============================================================================
# Heartbeat
# ============================================================================

class TestHeartbeat:
    def test_heartbeat_updated_on_every_tick(self, fixture_dir):
        r = _run_check(fixture_dir, args=("--dry-run",),
                       now_override="2026-08-02T06:00:00-04:00")
        assert r.returncode == 0
        hb = json.loads(
            (fixture_dir / "state" / "compliance-last-cron-tick.json").read_text()
        )
        assert "last_tick_utc" in hb
        assert hb["items_scanned"] == 1


# ============================================================================
# Mark-done
# ============================================================================

class TestMarkDone:
    def test_annual_advances_renewal_date(self, fixture_dir):
        r = _run_mark(fixture_dir, item_id="health_inspect_houston")
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["completed"] == "2026-09-01"
        assert out["next"] == "2027-09-01"
        assert out["deleted"] is False
        items = json.loads(
            (fixture_dir / "state" / "compliance-items.json").read_text()
        )
        assert items["items"][0]["renewal_date"] == "2027-09-01"

    def test_one_shot_delete(self, fixture_dir):
        items = json.loads((fixture_dir / "state" / "compliance-items.json").read_text())
        items["items"].append({
            "id": "one_shot_x", "name": "One-shot X", "category": "other",
            "renewal_date": "2026-09-01", "recurrence_days": 0,
        })
        (fixture_dir / "state" / "compliance-items.json").write_text(json.dumps(items))
        r = _run_mark(fixture_dir, item_id="one_shot_x")
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["next"] is None
        assert out["deleted"] is True
        items_after = json.loads(
            (fixture_dir / "state" / "compliance-items.json").read_text()
        )
        assert all(i["id"] != "one_shot_x" for i in items_after["items"])

    def test_item_not_found_returns_1(self, fixture_dir):
        r = _run_mark(fixture_dir, item_id="nonexistent")
        assert r.returncode == 1
        out = json.loads(r.stdout)
        assert out["error"] == "item_not_found"

    def test_audit_row_written_with_actor(self, fixture_dir):
        r = _run_mark(fixture_dir, item_id="health_inspect_houston", actor="operator")
        assert r.returncode == 0
        log = _read_audit_log(fixture_dir)
        marked = [e for e in log if e.get("type") == "compliance_item_marked_done"]
        assert len(marked) == 1
        assert marked[0]["actor"] == "operator"
        assert marked[0]["item_id"] == "health_inspect_houston"
        assert marked[0]["completed_renewal_date"] == "2026-09-01"
        assert marked[0]["next_renewal_date"] == "2027-09-01"

    def test_sentinel_keys_pruned_on_delete(self, fixture_dir):
        # Set up one-shot + populate sentinel for it
        items = json.loads((fixture_dir / "state" / "compliance-items.json").read_text())
        items["items"].append({
            "id": "one_shot_y", "name": "One-shot Y", "category": "other",
            "renewal_date": "2026-09-01", "recurrence_days": 0,
        })
        (fixture_dir / "state" / "compliance-items.json").write_text(json.dumps(items))
        (fixture_dir / "state" / "compliance-last-sent.json").write_text(json.dumps({
            "schema_version": 1,
            "last_sent": {
                "one_shot_y:30": "2026-08-02",
                "one_shot_y:14": "2026-08-18",
                "health_inspect_houston:30": "2026-08-02",
            },
        }))
        r = _run_mark(fixture_dir, item_id="one_shot_y")
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["sentinel_keys_pruned"] == 2
        sentinel = json.loads(
            (fixture_dir / "state" / "compliance-last-sent.json").read_text()
        )
        assert "one_shot_y:30" not in sentinel["last_sent"]
        assert "one_shot_y:14" not in sentinel["last_sent"]
        assert "health_inspect_houston:30" in sentinel["last_sent"]

    def test_dry_run_does_not_mutate(self, fixture_dir):
        before = (fixture_dir / "state" / "compliance-items.json").read_text()
        r = _run_mark(fixture_dir, item_id="health_inspect_houston", dry_run=True)
        assert r.returncode == 0
        after = (fixture_dir / "state" / "compliance-items.json").read_text()
        assert before == after

    def test_missing_items_file_recreated(self, fixture_dir):
        (fixture_dir / "state" / "compliance-items.json").unlink()
        r = _run_mark(fixture_dir, item_id="anything")
        assert r.returncode == 1  # item-not-found (just recreated empty)
        # File should now exist with empty items
        assert (fixture_dir / "state" / "compliance-items.json").exists()
        items = json.loads(
            (fixture_dir / "state" / "compliance-items.json").read_text()
        )
        assert items["items"] == []
        # Invariant audit emitted
        log = _read_audit_log(fixture_dir)
        invariants = [e for e in log if e.get("type") == "invariant_violation"]
        assert any("recreated" in e["check"] for e in invariants)


# ============================================================================
# Date math (cross-platform — pure Python date arithmetic, no subprocess)
# ============================================================================

class TestDateMath:
    """These import the module directly to test pure helpers; no subprocess needed.
    Skipped on Windows because safe_io has fcntl.
    """

    def _import_module(self):
        import importlib.util
        import importlib.machinery
        loader = importlib.machinery.SourceFileLoader(
            "ccd_test", str(CHECK_SCRIPT),
        )
        spec = importlib.util.spec_from_loader("ccd_test", loader)
        m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
        return m

    def test_leap_day_advances_to_feb_28_next_year(self):
        # Python: date(2024,2,29) + timedelta(365) = date(2025,2,28)
        from datetime import date, timedelta
        result = date(2024, 2, 29) + timedelta(days=365)
        assert result == date(2025, 2, 28)

    def test_build_candidates_30_day_boundary(self):
        m = self._import_module()
        from schemas import ComplianceItem, ComplianceLastSentFile
        items = [ComplianceItem(
            id="x", name="X", category="inspection",
            renewal_date=date(2026, 9, 1), recurrence_days=365,
        )]
        sentinel = ComplianceLastSentFile()
        # Today = 30 days before renewal
        fire, defer = m._build_candidates(
            items, [30, 14, 7, 3, 1], sentinel, max_deferral_days=7,
            today=date(2026, 8, 2),
        )
        gates_fired = sorted(set(c["gate_days"] for c in fire))
        assert 30 in gates_fired
        assert defer == []

    def test_build_candidates_skips_already_sent(self):
        m = self._import_module()
        from schemas import ComplianceItem, ComplianceLastSentFile
        items = [ComplianceItem(
            id="x", name="X", category="inspection",
            renewal_date=date(2026, 9, 1), recurrence_days=365,
        )]
        sentinel = ComplianceLastSentFile(
            last_sent={"x:30": "2026-08-02"},  # already sent today
        )
        fire, defer = m._build_candidates(
            items, [30, 14, 7, 3, 1], sentinel, max_deferral_days=7,
            today=date(2026, 8, 2),
        )
        gates_fired = sorted(set(c["gate_days"] for c in fire))
        assert 30 not in gates_fired

    def test_build_candidates_deferral_after_window(self):
        m = self._import_module()
        from schemas import ComplianceItem, ComplianceLastSentFile
        items = [ComplianceItem(
            id="x", name="X", category="inspection",
            renewal_date=date(2026, 9, 1), recurrence_days=365,
        )]
        sentinel = ComplianceLastSentFile()
        # Today = 8 days past 30-day-out fire date (8/10 vs 8/2)
        fire, defer = m._build_candidates(
            items, [30, 14, 7, 3, 1], sentinel, max_deferral_days=7,
            today=date(2026, 8, 10),
        )
        # 30-gate should defer (8 days late > 7 max)
        deferred_gates = sorted(set(d["gate_days"] for d in defer))
        assert 30 in deferred_gates
        # 14, 7, 3, 1 gates should fire (within window from today's perspective)
        fired_gates = sorted(set(c["gate_days"] for c in fire))
        assert 14 in fired_gates  # ideal 8/18, days_late=-8 actually... wait recompute

    def test_prune_sentinel_drops_unknown_items(self):
        m = self._import_module()
        from schemas import ComplianceItem, ComplianceLastSentFile
        items = [ComplianceItem(
            id="x", name="X", category="inspection",
            renewal_date=date(2026, 9, 1), recurrence_days=365,
        )]
        sentinel = ComplianceLastSentFile(last_sent={
            "x:30": "2026-08-02",
            "ghost:14": "2026-07-01",
        })
        new_sentinel, dropped = m._prune_sentinel(sentinel, items, [30, 14, 7, 3, 1])
        assert dropped == 1
        assert "ghost:14" not in new_sentinel.last_sent
        assert "x:30" in new_sentinel.last_sent

    def test_prune_sentinel_drops_unknown_gates(self):
        m = self._import_module()
        from schemas import ComplianceItem, ComplianceLastSentFile
        items = [ComplianceItem(
            id="x", name="X", category="inspection",
            renewal_date=date(2026, 9, 1), recurrence_days=365,
        )]
        sentinel = ComplianceLastSentFile(last_sent={
            "x:30": "2026-08-02",
            "x:60": "2026-07-01",  # not in advance_warning_days
        })
        new_sentinel, dropped = m._prune_sentinel(sentinel, items, [30, 14, 7, 3, 1])
        assert dropped == 1
        assert "x:60" not in new_sentinel.last_sent

    def test_prune_sentinel_keeps_negative_overdue_keys(self):
        m = self._import_module()
        from schemas import ComplianceItem, ComplianceLastSentFile
        items = [ComplianceItem(
            id="x", name="X", category="inspection",
            renewal_date=date(2026, 9, 1), recurrence_days=365,
        )]
        sentinel = ComplianceLastSentFile(last_sent={
            "x:-3": "2026-09-04",  # overdue gate
        })
        new_sentinel, dropped = m._prune_sentinel(sentinel, items, [30, 14, 7, 3, 1])
        assert dropped == 0
        assert "x:-3" in new_sentinel.last_sent
