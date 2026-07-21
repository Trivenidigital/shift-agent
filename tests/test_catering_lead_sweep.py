"""Tests for the catering lead TTL expiry sweep (PR-A lifecycle guard).

Two layers:
  * `find_expired_awaiting_leads` — pure stdlib logic, runs cross-platform (mirrors
    tests/test_proposal_sweep.py). conftest puts src/platform on sys.path.
  * `catering-lead-ttl-sweep` — static-invariant scans (cross-platform) + a subprocess
    integration test (Linux-only, fcntl) that drives the real transition + audit chokepoint.
"""
from __future__ import annotations

import json
import os
import platform
import py_compile
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from catering_lead_sweep import find_expired_awaiting_leads, CATERING_LEAD_TTL_DAYS

NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
_REPO = Path(__file__).resolve().parent.parent
_SWEEP = _REPO / "src" / "agents" / "catering" / "scripts" / "catering-lead-ttl-sweep"


def _lead(status: str, updated_days_ago=None, lead_id="L1"):
    updated_at = NOW - timedelta(days=updated_days_ago) if updated_days_ago is not None else None
    return SimpleNamespace(lead_id=lead_id, status=status, updated_at=updated_at)


# ── find_expired_awaiting_leads ─────────────────────────────────────────────
def test_expired_awaiting_included():
    assert find_expired_awaiting_leads([_lead("AWAITING_OWNER_APPROVAL", 30)], NOW, 21) == ["L1"]


def test_fresh_awaiting_excluded():
    assert find_expired_awaiting_leads([_lead("AWAITING_OWNER_APPROVAL", 5)], NOW, 21) == []


def test_boundary_exactly_ttl_included():
    # updated_at exactly ttl days ago is expired (<= cutoff semantics).
    assert find_expired_awaiting_leads([_lead("AWAITING_OWNER_APPROVAL", 21)], NOW, 21) == ["L1"]


def test_non_awaiting_excluded_even_if_ancient():
    leads = [
        _lead("SENT_TO_CUSTOMER", 999, "L1"),
        _lead("CUSTOMER_FINALIZED", 999, "L2"),
        _lead("STALE", 999, "L3"),
        _lead("CLOSED", 999, "L4"),
        _lead("OWNER_APPROVED", 999, "L5"),
    ]
    assert find_expired_awaiting_leads(leads, NOW, 21) == []


def test_awaiting_without_updated_at_excluded():
    # Defensive: missing updated_at is skipped, not crashed on.
    assert find_expired_awaiting_leads([_lead("AWAITING_OWNER_APPROVAL", None)], NOW, 21) == []


def test_expired_returns_sorted_ids():
    leads = [
        _lead("AWAITING_OWNER_APPROVAL", 30, "L3"),
        _lead("AWAITING_OWNER_APPROVAL", 40, "L1"),
        _lead("AWAITING_OWNER_APPROVAL", 5, "L2"),   # fresh, excluded
    ]
    assert find_expired_awaiting_leads(leads, NOW, 21) == ["L1", "L3"]


def test_empty_store():
    assert find_expired_awaiting_leads([], NOW, 21) == []


def test_default_ttl_is_21_days():
    assert CATERING_LEAD_TTL_DAYS == 21


# ── static-invariant scans of the sweep script (cross-platform) ─────────────
def test_sweep_script_compiles():
    py_compile.compile(str(_SWEEP), doraise=True)


def test_sweep_gated_off_by_default_env_flag():
    t = _SWEEP.read_text(encoding="utf-8")
    assert "CATERING_LEAD_TTL_SWEEP_ENABLED" in t
    assert "def _enabled" in t
    # dormant unless armed
    assert "if not _enabled()" in t


def test_sweep_uses_legal_terminal_transition_via_chokepoint():
    t = _SWEEP.read_text(encoding="utf-8")
    assert 'TERMINAL_STATUS = "STALE"' in t
    assert "is_catering_transition_allowed" in t
    assert "CateringLeadStatusChange" in t
    assert "find_expired_awaiting_leads" in t
    assert "atomic_write_json" in t


def test_sweep_owner_alert_only_plain_text_and_no_money():
    t = _SWEEP.read_text(encoding="utf-8")
    assert "shift-agent-notify-owner" in t
    # §12b dispatched/delivered structured logs around the alert.
    assert "catering_lead_ttl_alert_dispatched" in t
    assert "catering_lead_ttl_alert_delivered" in t
    low = t.lower()
    assert "deposit" not in low and "stripe" not in low and "payment" not in low
    assert "send-catering-ack" not in t, "the sweep alerts the OWNER, never the customer"


def test_sweep_never_fails_its_timer():
    t = _SWEEP.read_text(encoding="utf-8")
    # Watchdog discipline: the top-level exception handler swallows + returns 0.
    assert "a watchdog must never fail its timer" in t
    assert "except Exception" in t


# ── subprocess integration (Linux-only, fcntl) ──────────────────────────────
_LINUX_ONLY = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="sweep depends on safe_io which uses fcntl (Linux only)",
)


def _lead_row(lead_id, status, updated_at, code="#ABCDE"):
    return {
        "lead_id": lead_id, "status": status,
        "customer_phone": "+19045550104", "customer_name": "",
        "raw_inquiry": "x", "original_message_id": f"m-{lead_id}",
        "owner_approval_code": code,
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": updated_at,
    }


def _run_sweep(tmp_path, *, enabled, extra_env=None):
    leads_path = tmp_path / "catering-leads.json"
    log_path = tmp_path / "decisions.log"
    env = {
        **os.environ,
        "PYTHONPATH": str(_REPO / "src" / "platform"),
        "SHIFT_AGENT_CATERING_LEADS_PATH": str(leads_path),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(log_path),
        "SHIFT_AGENT_CONFIG_PATH": str(tmp_path / "no-such-config.yaml"),  # UTC fallback
    }
    if enabled:
        env["CATERING_LEAD_TTL_SWEEP_ENABLED"] = "1"
    else:
        env.pop("CATERING_LEAD_TTL_SWEEP_ENABLED", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(_SWEEP)], capture_output=True, text=True,
                          env=env, timeout=30)


def _seed(tmp_path):
    leads_path = tmp_path / "catering-leads.json"
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    leads_path.write_text(json.dumps({
        "schema_version": 1,
        "leads": [
            _lead_row("L0001", "AWAITING_OWNER_APPROVAL", "2020-01-01T00:00:00+00:00"),  # stale
            _lead_row("L0002", "AWAITING_OWNER_APPROVAL", recent),                       # fresh
            _lead_row("L0003", "SENT_TO_CUSTOMER", "2020-01-01T00:00:00+00:00"),         # wrong status
        ],
    }), encoding="utf-8")
    return leads_path


def _statuses(leads_path):
    doc = json.loads(leads_path.read_text(encoding="utf-8"))
    return {l["lead_id"]: l["status"] for l in doc["leads"]}


@_LINUX_ONLY
def test_cli_expires_only_stale_awaiting_lead(tmp_path):
    leads_path = _seed(tmp_path)
    r = _run_sweep(tmp_path, enabled=True)
    assert r.returncode == 0, r.stderr
    statuses = _statuses(leads_path)
    assert statuses["L0001"] == "STALE", "old AWAITING_OWNER_APPROVAL lead expires"
    assert statuses["L0002"] == "AWAITING_OWNER_APPROVAL", "fresh lead untouched"
    assert statuses["L0003"] == "SENT_TO_CUSTOMER", "non-awaiting lead untouched"
    # §12b: a catering_lead_status_change audit row (actor=system) for the expiry.
    rows = [json.loads(l) for l in (tmp_path / "decisions.log").read_text(encoding="utf-8").splitlines() if l.strip()]
    changes = [x for x in rows if x.get("type") == "catering_lead_status_change" and x.get("lead_id") == "L0001"]
    assert len(changes) == 1
    assert changes[0]["from_status"] == "AWAITING_OWNER_APPROVAL"
    assert changes[0]["to_status"] == "STALE"
    assert changes[0]["actor"] == "system"


@_LINUX_ONLY
def test_cli_idempotent_second_run_is_noop(tmp_path):
    leads_path = _seed(tmp_path)
    _run_sweep(tmp_path, enabled=True)
    _run_sweep(tmp_path, enabled=True)
    # Exactly one status-change row for L0001 across two runs (STALE is terminal).
    rows = [json.loads(l) for l in (tmp_path / "decisions.log").read_text(encoding="utf-8").splitlines() if l.strip()]
    changes = [x for x in rows if x.get("type") == "catering_lead_status_change" and x.get("lead_id") == "L0001"]
    assert len(changes) == 1, "terminal transition fires at most once — no re-expiry"


@_LINUX_ONLY
def test_cli_flag_off_is_a_noop(tmp_path):
    leads_path = _seed(tmp_path)
    r = _run_sweep(tmp_path, enabled=False)
    assert r.returncode == 0
    assert _statuses(leads_path)["L0001"] == "AWAITING_OWNER_APPROVAL", "dormant unless armed"
    assert not (tmp_path / "decisions.log").exists() or \
        (tmp_path / "decisions.log").read_text(encoding="utf-8").strip() == ""


@_LINUX_ONLY
def test_cli_within_ttl_lead_untouched(tmp_path):
    leads_path = _seed(tmp_path)
    # A huge TTL keeps even the 2020 lead within window → nothing expires.
    r = _run_sweep(tmp_path, enabled=True, extra_env={"CATERING_LEAD_TTL_DAYS": "100000"})
    assert r.returncode == 0
    assert _statuses(leads_path)["L0001"] == "AWAITING_OWNER_APPROVAL"
