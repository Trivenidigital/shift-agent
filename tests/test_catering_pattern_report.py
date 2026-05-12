"""Tests for src/agents/catering/scripts/catering-pattern-report.

PR-CF1d follow-up (2026-05-12 PM): verifies the roster-lookup carve-out in
`_is_name_hallucinated` that suppresses false-positives when a lead's
`customer_name` was populated via legitimate `identify-sender` roster
resolution (e.g. Bangaru's LID → e004 Anjali Iyer).

Mirrors the load-as-module pattern used by tests/test_dispatcher_accuracy_report.py.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "src" / "agents" / "catering" / "scripts" / "catering-pattern-report"
)
loader = importlib.machinery.SourceFileLoader("catering_pattern_report", str(SCRIPT))
spec = importlib.util.spec_from_loader("catering_pattern_report", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)


# ─────────────────────────────────────────────────────────────────
# _load_roster_names
# ─────────────────────────────────────────────────────────────────


def test_load_roster_names_collects_employee_and_owner(tmp_path: Path) -> None:
    roster = {
        "employees": [
            {"id": "e001", "name": "Anjali Iyer"},
            {"id": "e002", "name": "Srini Bangaru"},
            {"id": "e003", "name": ""},  # blank name skipped
            {"id": "e004"},               # missing name skipped
        ],
        "owner": {"name": "Operator Owner"},
    }
    rp = tmp_path / "roster.json"
    rp.write_text(json.dumps(roster), encoding="utf-8")
    names = mod._load_roster_names(rp)
    assert names == {"anjali iyer", "srini bangaru", "operator owner"}


def test_load_roster_names_missing_file_returns_empty(tmp_path: Path) -> None:
    names = mod._load_roster_names(tmp_path / "nope.json")
    assert names == set()


def test_load_roster_names_malformed_returns_empty(tmp_path: Path) -> None:
    rp = tmp_path / "roster.json"
    rp.write_text("not json", encoding="utf-8")
    assert mod._load_roster_names(rp) == set()


# ─────────────────────────────────────────────────────────────────
# _is_name_hallucinated
# ─────────────────────────────────────────────────────────────────


def test_roster_resolved_name_not_flagged() -> None:
    """The Anjali Iyer false-positive class: LID-only sender, name from
    roster, inquiry text does not mention the name → NOT a hallucination."""
    roster = {"anjali iyer", "srini bangaru"}
    inquiry = "catering for 50 people wedding next month food delivered"
    assert mod._is_name_hallucinated("Anjali Iyer", inquiry, roster) is False


def test_roster_resolved_name_case_insensitive() -> None:
    roster = {"anjali iyer"}
    assert mod._is_name_hallucinated("anjali iyer", "wedding catering", roster) is False
    assert mod._is_name_hallucinated("ANJALI IYER", "wedding catering", roster) is False
    assert mod._is_name_hallucinated("  Anjali Iyer  ", "wedding catering", roster) is False


def test_actual_hallucination_still_flagged() -> None:
    """Name not in roster AND not in inquiry → genuine LLM invention."""
    roster = {"anjali iyer", "srini bangaru"}
    inquiry = "catering for 50 people wedding next month"
    assert mod._is_name_hallucinated("John Smith", inquiry, roster) is True


def test_name_present_in_inquiry_not_flagged_regardless_of_roster() -> None:
    """Pre-existing behavior preserved: if any token appears in inquiry, not flagged."""
    roster: set[str] = set()
    inquiry = "Hi this is John booking catering for 50 people"
    assert mod._is_name_hallucinated("John Smith", inquiry, roster) is False


def test_no_roster_falls_back_to_text_match() -> None:
    """Missing roster (empty set) → degrades to v0.1 text-only heuristic.
    The Anjali case WOULD flag here — that's the pre-fix behavior we lost
    when roster is unavailable, which is acceptable degradation."""
    inquiry = "catering for 50 people wedding next month"
    assert mod._is_name_hallucinated("Anjali Iyer", inquiry, None) is True
    assert mod._is_name_hallucinated("Anjali Iyer", inquiry, set()) is True


def test_empty_customer_name_not_flagged() -> None:
    """Pre-existing guard: blank name → not flagged (no LLM contribution)."""
    assert mod._is_name_hallucinated("", "any inquiry text", {"anjali iyer"}) is False


def test_empty_inquiry_not_flagged() -> None:
    """Pre-existing guard: blank inquiry → not flagged (insufficient data)."""
    assert mod._is_name_hallucinated("Anjali Iyer", "", set()) is False


# ─────────────────────────────────────────────────────────────────
# End-to-end _scan with roster
# ─────────────────────────────────────────────────────────────────


def _ts(base: datetime, delta_seconds: int = 0) -> str:
    return (base + timedelta(seconds=delta_seconds)).isoformat()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 12, 18, 0, 0, tzinfo=timezone.utc)


def test_scan_suppresses_roster_resolved_finding(tmp_path: Path, now: datetime) -> None:
    """Full _scan: lead with roster-resolved name → 0 findings (was 1 in v0.1)."""
    log_path = tmp_path / "decisions.log"
    leads_path = tmp_path / "leads.json"
    roster_path = tmp_path / "roster.json"

    log_path.write_text(json.dumps({
        "type": "catering_lead_created",
        "ts": _ts(now, -3600),
        "lead_id": "L9001",
    }) + "\n", encoding="utf-8")
    leads_path.write_text(json.dumps({"leads": [{
        "lead_id": "L9001",
        "customer_name": "Anjali Iyer",
        "raw_inquiry": "catering for 50 people wedding next month",
    }]}), encoding="utf-8")
    roster_path.write_text(json.dumps({
        "employees": [{"id": "e004", "name": "Anjali Iyer"}],
    }), encoding="utf-8")

    findings = mod._scan(log_path, leads_path, now - timedelta(days=1), roster_path)
    assert findings == []


def test_scan_still_flags_genuine_hallucination(tmp_path: Path, now: datetime) -> None:
    """Full _scan: lead with name absent from roster AND inquiry → flagged."""
    log_path = tmp_path / "decisions.log"
    leads_path = tmp_path / "leads.json"
    roster_path = tmp_path / "roster.json"

    log_path.write_text(json.dumps({
        "type": "catering_lead_created",
        "ts": _ts(now, -3600),
        "lead_id": "L9002",
    }) + "\n", encoding="utf-8")
    leads_path.write_text(json.dumps({"leads": [{
        "lead_id": "L9002",
        "customer_name": "Fabricated Person",
        "raw_inquiry": "catering for 50 people next month",
    }]}), encoding="utf-8")
    roster_path.write_text(json.dumps({
        "employees": [{"id": "e004", "name": "Anjali Iyer"}],
    }), encoding="utf-8")

    findings = mod._scan(log_path, leads_path, now - timedelta(days=1), roster_path)
    assert len(findings) == 1
    assert findings[0]["lead_id"] == "L9002"
    assert findings[0]["persisted_name"] == "Fabricated Person"
