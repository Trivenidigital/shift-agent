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
import platform
import sys
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


def test_load_roster_names_collects_nicknames(tmp_path: Path) -> None:
    """v0.2 strategic review finding: identify-sender can resolve to nickname-
    shaped names; carve-out must include them or false-positives recur."""
    roster = {
        "employees": [
            {"id": "e001", "name": "Ravi Kumar", "nickname": "Ravi"},
            {"id": "e002", "name": "Priya Reddy", "nickname": ""},  # blank skipped
            {"id": "e003", "name": "Anjali Iyer"},                  # missing nickname OK
        ],
    }
    rp = tmp_path / "roster.json"
    rp.write_text(json.dumps(roster), encoding="utf-8")
    names = mod._load_roster_names(rp)
    assert names == {"ravi kumar", "ravi", "priya reddy", "anjali iyer"}


def test_load_roster_names_missing_file_returns_empty(tmp_path: Path) -> None:
    names = mod._load_roster_names(tmp_path / "nope.json")
    assert names == set()


def test_load_roster_names_malformed_returns_empty(tmp_path: Path) -> None:
    rp = tmp_path / "roster.json"
    rp.write_text("not json", encoding="utf-8")
    assert mod._load_roster_names(rp) == set()


def test_load_roster_names_non_list_employees_returns_empty(tmp_path: Path) -> None:
    """Structural review finding: `employees: 42` (non-list, non-falsy) must
    not raise TypeError. Degradation contract is empty-set, never crash."""
    rp = tmp_path / "roster.json"
    rp.write_text(json.dumps({"employees": 42}), encoding="utf-8")
    assert mod._load_roster_names(rp) == set()
    rp.write_text(json.dumps({"employees": "not-a-list"}), encoding="utf-8")
    assert mod._load_roster_names(rp) == set()
    # Also guard against non-dict employee entries
    rp.write_text(json.dumps({"employees": ["not-a-dict", {"name": "Real Name"}]}),
                  encoding="utf-8")
    assert mod._load_roster_names(rp) == {"real name"}


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
    """Full _scan: lead with roster-resolved name → 0 findings, suppressed=1."""
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

    findings, suppressed = mod._scan(log_path, leads_path, now - timedelta(days=1), roster_path)
    assert findings == []
    assert suppressed == 1


def test_scan_still_flags_genuine_hallucination(tmp_path: Path, now: datetime) -> None:
    """Full _scan: lead with name absent from roster AND inquiry → flagged, suppressed=0."""
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

    findings, suppressed = mod._scan(log_path, leads_path, now - timedelta(days=1), roster_path)
    assert len(findings) == 1
    assert findings[0]["lead_id"] == "L9002"
    assert findings[0]["persisted_name"] == "Fabricated Person"
    assert suppressed == 0


def test_scan_counts_multiple_suppressions(tmp_path: Path, now: datetime) -> None:
    """Suppression counter is per-lead, not boolean. 3 roster-matched leads → suppressed=3."""
    log_path = tmp_path / "decisions.log"
    leads_path = tmp_path / "leads.json"
    roster_path = tmp_path / "roster.json"

    log_path.write_text(
        "\n".join(json.dumps({
            "type": "catering_lead_created",
            "ts": _ts(now, -3600 + i),
            "lead_id": f"L900{i}",
        }) for i in (3, 4, 5)) + "\n",
        encoding="utf-8",
    )
    leads_path.write_text(json.dumps({"leads": [
        {"lead_id": "L9003", "customer_name": "Anjali Iyer", "raw_inquiry": "catering for 50"},
        {"lead_id": "L9004", "customer_name": "Anjali Iyer", "raw_inquiry": "catering for 60"},
        {"lead_id": "L9005", "customer_name": "Anjali Iyer", "raw_inquiry": "catering for 70"},
    ]}), encoding="utf-8")
    roster_path.write_text(json.dumps({
        "employees": [{"id": "e004", "name": "Anjali Iyer"}],
    }), encoding="utf-8")

    findings, suppressed = mod._scan(log_path, leads_path, now - timedelta(days=1), roster_path)
    assert findings == []
    assert suppressed == 3


# ─────────────────────────────────────────────────────────────────────────────
# Counts-only catering learning summary
# ─────────────────────────────────────────────────────────────────────────────


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _learning_lead(
    lead_id: str,
    *,
    created_at: datetime,
    status: str = "NEW",
    headcount: int | None = 50,
    event_date: str | None = "2026-07-12",
    off_menu_items: list[str] | None = None,
) -> dict:
    return {
        "lead_id": lead_id,
        "status": status,
        "customer_phone": "+15555550100",
        "customer_name": "Priya Private",
        "raw_inquiry": "Need catering at 123 Main Street for $2500",
        "original_message_id": f"msg_{lead_id}",
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "extracted": {
            "headcount": headcount,
            "event_date": event_date,
            "event_time": None,
            "menu_preferences": [],
            "off_menu_items": off_menu_items or [],
            "dietary_restrictions": [],
            "delivery_or_pickup": "unknown",
            "budget_hint_usd": None,
            "notes": "",
        },
        "quote_text": "",
        "quote_version": 0,
        "owner_approval_code": None,
        "customer_replied": False,
    }


def _proposal_set(
    proposal_set_id: str,
    *,
    status: str,
    created_at: datetime,
    sent_at: datetime | None = None,
    outbound_message_id: str = "",
) -> dict:
    return {
        "proposal_set_id": proposal_set_id,
        "lead_id": "L9001",
        "status": status,
        "created_at": created_at.isoformat(),
        "sent_at": sent_at.isoformat() if sent_at else None,
        "outbound_message_id": outbound_message_id,
        "source_message_id": f"src_{proposal_set_id}",
        "request_text": "Priya wants option 2 for 2500 bucks",
        "options": [
            {"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
             "item_names": ["Idly"]},
            {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
             "item_names": ["Dosa"]},
        ],
    }


def test_learning_summary_counts_off_menu_without_persisting_text(tmp_path: Path, now: datetime) -> None:
    leads = [
        _learning_lead(
            "L9001",
            created_at=now - timedelta(days=2),
            off_menu_items=[
                "Priya special",
                "Srini family menu",
                "+1 987 654 3210",
                "123 Main Street",
                "Pineville NC",
                "$2500 deposit",
                "35 per head",
                "Butter_Chicken *premium*\u200b",
                "Butter Chicken",
            ],
        ),
        _learning_lead(
            "L9002",
            created_at=now - timedelta(days=40),
            off_menu_items=["Old item"],
        ),
    ]
    leads_path = tmp_path / "leads.json"
    _write_json(leads_path, {"leads": leads})
    proposals_path = tmp_path / "proposals.json"
    _write_json(proposals_path, {"sets": []})
    menu_path = tmp_path / "menu.json"
    _write_json(menu_path, {"updated_at": (now - timedelta(days=5)).isoformat(), "items": []})

    summary = mod._build_learning_summary(
        leads_path, proposals_path, menu_path, now, 30,
    )

    assert summary.off_menu_request_count == 9
    assert summary.leads_with_off_menu_count == 1
    dumped = summary.model_dump_json()
    forbidden_fragments = [
        "Priya", "Srini", "987", "Main Street", "Pineville", "2500",
        "35 per head", "deposit", "Butter_Chicken", "premium",
        "Butter Chicken", "Need catering", "request_text",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in dumped
    assert summary.menu_freshness_days == 5


def test_learning_summary_counts_proposal_buckets_and_ignores_old(tmp_path: Path, now: datetime) -> None:
    _write_json(tmp_path / "leads.json", {"leads": []})
    sent_at = now - timedelta(days=1)
    _write_json(tmp_path / "proposals.json", {
        "sets": [
            _proposal_set(
                "CPS-L9001-000001",
                status="SENT",
                created_at=sent_at,
                sent_at=sent_at,
                outbound_message_id="wamid.sent",
            ),
            _proposal_set("CPS-L9001-000002", status="SUPERSEDED", created_at=sent_at),
            _proposal_set("CPS-L9001-000003", status="SELECTED", created_at=sent_at),
            _proposal_set("CPS-L9001-000004", status="SEND_FAILED", created_at=sent_at),
            _proposal_set("CPS-L9001-000005", status="SELECT_FAILED", created_at=sent_at),
            _proposal_set(
                "CPS-L9001-000006",
                status="SENT",
                created_at=now - timedelta(days=60),
                sent_at=now - timedelta(days=60),
                outbound_message_id="wamid.old",
            ),
        ],
    })
    _write_json(tmp_path / "menu.json", {"updated_at": now.isoformat(), "items": []})

    summary = mod._build_learning_summary(
        tmp_path / "leads.json", tmp_path / "proposals.json", tmp_path / "menu.json",
        now, 30,
    )

    assert summary.proposal_health.sent == 2
    assert summary.proposal_health.selected == 1
    assert summary.proposal_health.send_failed == 1
    assert summary.proposal_health.select_failed == 1


def test_learning_summary_degrades_on_bad_sources(tmp_path: Path, now: datetime) -> None:
    (tmp_path / "leads.json").write_text("not json", encoding="utf-8")
    (tmp_path / "proposals.json").write_text("not json", encoding="utf-8")
    (tmp_path / "menu.json").write_text("not json", encoding="utf-8")

    summary = mod._build_learning_summary(
        tmp_path / "leads.json", tmp_path / "proposals.json", tmp_path / "menu.json",
        now, 30,
    )

    assert set(summary.degraded_sources) == {"leads", "proposals", "menu"}
    assert summary.off_menu_request_count == 0
    assert summary.proposal_health.sent == 0


def test_learning_summary_lock_derives_from_actual_output_path(tmp_path: Path) -> None:
    out = tmp_path / "custom-summary.json"
    assert mod._learning_summary_lock_path(out) == Path(str(out) + ".lock")
    explicit = tmp_path / "explicit.lock"
    assert mod._learning_summary_lock_path(out, explicit) == explicit


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="main() write path imports safe_io/FileLock (fcntl)",
)
def test_main_writes_learning_sidecar_even_with_no_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "decisions.log"
    log_path.write_text("", encoding="utf-8")
    leads_path = tmp_path / "leads.json"
    _write_json(leads_path, {"leads": []})
    proposals_path = tmp_path / "proposals.json"
    _write_json(proposals_path, {"sets": []})
    menu_path = tmp_path / "menu.json"
    _write_json(menu_path, {"updated_at": datetime.now(tz=timezone.utc).isoformat(), "items": []})
    summary_path = tmp_path / "state" / "learning.json"
    lessons_path = tmp_path / "lessons.md"

    monkeypatch.setattr(sys, "argv", [
        "catering-pattern-report",
        "--log", str(log_path),
        "--leads", str(leads_path),
        "--proposals", str(proposals_path),
        "--menu", str(menu_path),
        "--learning-summary", str(summary_path),
        "--lessons", str(lessons_path),
    ])

    assert mod.main() == 0
    written = json.loads(summary_path.read_text(encoding="utf-8"))
    assert written["source"] == "catering-pattern-report"
    assert Path(str(summary_path) + ".lock").exists()
    assert not lessons_path.exists()


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="main() write path imports safe_io/FileLock (fcntl)",
)
def test_main_writes_degraded_learning_sidecar_when_log_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leads_path = tmp_path / "leads.json"
    _write_json(leads_path, {"leads": []})
    proposals_path = tmp_path / "proposals.json"
    _write_json(proposals_path, {"sets": []})
    menu_path = tmp_path / "menu.json"
    _write_json(menu_path, {"updated_at": datetime.now(tz=timezone.utc).isoformat(), "items": []})
    summary_path = tmp_path / "state" / "learning.json"
    lessons_path = tmp_path / "lessons.md"

    monkeypatch.setattr(sys, "argv", [
        "catering-pattern-report",
        "--log", str(tmp_path / "missing-decisions.log"),
        "--leads", str(leads_path),
        "--proposals", str(proposals_path),
        "--menu", str(menu_path),
        "--learning-summary", str(summary_path),
        "--lessons", str(lessons_path),
    ])

    assert mod.main() == 0
    written = json.loads(summary_path.read_text(encoding="utf-8"))
    assert "log" in written["degraded_sources"]
