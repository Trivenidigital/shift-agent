"""Unit tests for proposal_sweep — Shift no-response escalation stale-detection.

Pure stdlib logic → runs cross-platform (unlike the fcntl-gated subprocess suites).
conftest puts src/platform on sys.path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from proposal_sweep import find_stale_sent_proposals, find_expired_awaiting_proposals
from schemas import LimitsConfig

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


def _p(status: str, sent_minutes_ago=None, created_hours_ago=None):
    sent_ts = NOW - timedelta(minutes=sent_minutes_ago) if sent_minutes_ago is not None else None
    created_ts = NOW - timedelta(hours=created_hours_ago) if created_hours_ago is not None else None
    return SimpleNamespace(status=status, sent_ts=sent_ts, created_ts=created_ts)


# ── find_stale_sent_proposals ─────────────────────────────────────────────────

def test_stale_sent_included():
    assert find_stale_sent_proposals({"P1": _p("sent", 40)}, NOW, 30) == ["P1"]


def test_fresh_sent_excluded():
    assert find_stale_sent_proposals({"P1": _p("sent", 10)}, NOW, 30) == []


def test_boundary_exactly_ttl_included():
    # sent_ts exactly ttl minutes ago is stale (>= cutoff semantics).
    assert find_stale_sent_proposals({"P1": _p("sent", 30)}, NOW, 30) == ["P1"]


def test_non_sent_excluded_even_if_ancient():
    props = {
        "P1": _p("approved", 999),
        "P2": _p("accepted", 999),
        "P3": _p("awaiting_owner_approval", 999),
        "P4": _p("no_response_timeout", 999),
    }
    assert find_stale_sent_proposals(props, NOW, 30) == []


def test_sent_without_sent_ts_excluded():
    # Defensive: a 'sent' proposal missing sent_ts is skipped, not crashed on.
    assert find_stale_sent_proposals({"P1": _p("sent", None)}, NOW, 30) == []


def test_multiple_returns_sorted_ids():
    props = {"P3": _p("sent", 40), "P1": _p("sent", 50), "P2": _p("sent", 10)}
    assert find_stale_sent_proposals(props, NOW, 30) == ["P1", "P3"]


def test_empty_store():
    assert find_stale_sent_proposals({}, NOW, 30) == []


# ── LimitsConfig new fields (config gate + TTL) ───────────────────────────────

def test_limits_config_defaults_ship_off():
    c = LimitsConfig()
    assert c.no_response_sweep_enabled is False, "sweep MUST ship OFF by default"
    assert c.candidate_response_ttl_minutes == 30


def test_limits_config_backward_compat_without_new_fields():
    # A config that predates these fields must still validate (defaults apply) despite extra=forbid.
    c = LimitsConfig.model_validate({"max_outbound_per_day": 6})
    assert c.no_response_sweep_enabled is False
    assert c.candidate_response_ttl_minutes == 30


def test_limits_config_accepts_new_fields():
    c = LimitsConfig.model_validate(
        {"no_response_sweep_enabled": True, "candidate_response_ttl_minutes": 45}
    )
    assert c.no_response_sweep_enabled is True
    assert c.candidate_response_ttl_minutes == 45


# ── safety invariants of the sweep script + unit (must never regress) ─────────

import py_compile  # noqa: E402
from pathlib import Path  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_SWEEP = _REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-proposal-sweep"
_SVC = _REPO / "src" / "agents" / "shift" / "systemd" / "shift-agent-proposal-sweep.service"


def test_sweep_script_compiles():
    # The script isn't imported by the suite (subprocess CLI); compile it to catch syntax errors.
    py_compile.compile(str(_SWEEP), doraise=True)


def test_sweep_gates_off_when_both_flags_off():
    t = _SWEEP.read_text(encoding="utf-8")
    assert "no_response_sweep_enabled" in t
    assert "owner_approval_expiry_enabled" in t
    # sweep must no-op only when BOTH escalations are disabled
    assert "if not (no_resp_on or expiry_on)" in t, "sweep must no-op when both flags are off"


def test_sweep_uses_existing_transition_chokepoint_as_timer():
    t = _SWEEP.read_text(encoding="utf-8")
    assert "update-proposal-status" in t
    assert "no_response_timeout" in t
    assert "--actor" in t and '"timer"' in t


def test_sweep_never_messages_staff_or_moves_money():
    # Alerts the OWNER only. Must not call the staff-send path or any money path.
    t = _SWEEP.read_text(encoding="utf-8")
    assert "shift-agent-notify-owner" in t
    assert "send-coverage-message" not in t
    low = t.lower()
    assert "deposit" not in low and "stripe" not in low and "payment" not in low


def test_sweep_service_runs_as_shift_agent():
    assert "User=shift-agent" in _SVC.read_text(encoding="utf-8")


# ── find_expired_awaiting_proposals (BL-SHIFT-04) ─────────────────────────────

def test_expired_awaiting_included():
    # awaiting_owner_approval created 5h ago, ttl 4h → expired.
    assert find_expired_awaiting_proposals({"P1": _p("awaiting_owner_approval", created_hours_ago=5)}, NOW, 4) == ["P1"]


def test_fresh_awaiting_excluded():
    assert find_expired_awaiting_proposals({"P1": _p("awaiting_owner_approval", created_hours_ago=1)}, NOW, 4) == []


def test_awaiting_boundary_exactly_ttl_included():
    # created exactly ttl hours ago is expired (<= cutoff semantics).
    assert find_expired_awaiting_proposals({"P1": _p("awaiting_owner_approval", created_hours_ago=4)}, NOW, 4) == ["P1"]


def test_non_awaiting_excluded_even_if_ancient():
    props = {
        "P1": _p("sent", created_hours_ago=99),
        "P2": _p("approved", created_hours_ago=99),
        "P3": _p("expired", created_hours_ago=99),
        "P4": _p("send_failed", created_hours_ago=99),
    }
    assert find_expired_awaiting_proposals(props, NOW, 4) == []


def test_awaiting_without_created_ts_excluded():
    # Defensive: missing created_ts is skipped, not crashed on.
    assert find_expired_awaiting_proposals({"P1": _p("awaiting_owner_approval", created_hours_ago=None)}, NOW, 4) == []


def test_expired_awaiting_returns_sorted_ids():
    props = {
        "P3": _p("awaiting_owner_approval", created_hours_ago=5),
        "P1": _p("awaiting_owner_approval", created_hours_ago=6),
        "P2": _p("awaiting_owner_approval", created_hours_ago=1),  # fresh, excluded
    }
    assert find_expired_awaiting_proposals(props, NOW, 4) == ["P1", "P3"]


def test_expired_awaiting_empty_store():
    assert find_expired_awaiting_proposals({}, NOW, 4) == []


# ── LimitsConfig owner_approval_expiry flag ───────────────────────────────────

def test_owner_approval_expiry_ships_off():
    assert LimitsConfig().owner_approval_expiry_enabled is False, "expiry sweep MUST ship OFF"


def test_owner_approval_expiry_backward_compat():
    # A config predating the field still validates (default applies) under extra=forbid.
    c = LimitsConfig.model_validate({"max_outbound_per_day": 6})
    assert c.owner_approval_expiry_enabled is False


def test_owner_approval_expiry_accepts_true():
    c = LimitsConfig.model_validate({"owner_approval_expiry_enabled": True})
    assert c.owner_approval_expiry_enabled is True


# ── expire path invariants (must never regress) ───────────────────────────────

def test_sweep_expire_uses_legal_terminal_transition():
    t = _SWEEP.read_text(encoding="utf-8")
    # awaiting_owner_approval -> expired is a LEGAL, terminal transition; the sweep must
    # go through the chokepoint as the timer actor (never a raw state write).
    assert '"expired"' in t
    assert "owner_approval_ttl" in t
    assert "find_expired_awaiting_proposals" in t


def test_sweep_expire_still_owner_alert_only():
    t = _SWEEP.read_text(encoding="utf-8")
    # The expiry path alerts the owner and touches no staff/money surface.
    assert "owner_approval_expiry_alert_dispatched" in t
    low = t.lower()
    assert "send-coverage-message" not in t
    assert "deposit" not in low and "stripe" not in low and "payment" not in low
