"""Priority 1: multi-admin queue ownership (claim / unclaim / assign).

Self-reported admin-handle coordination on the manual-review queue so two
admins don't silently work the same case. All mutations are audited via the
cockpit audit pattern; these tests exercise the action functions against a
seeded isolated store.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _queued_project(pid: str, *, claimed_by: str = "", claimed_at: str | None = None) -> dict:
    mr: dict = {
        "status": "queued",
        "reason": "source_edit_provider_unavailable",
        "reason_code": "source_edit_provider_unavailable",
        "detail": "",
        "queued_at": "2026-05-20T00:00:00Z",
        "claimed_by": claimed_by,
    }
    if claimed_at:
        mr["claimed_at"] = claimed_at
    return {
        "project_id": pid,
        "status": "manual_edit_required",
        "customer_phone": "+19045550104",
        "created_at": "2026-05-20T00:00:00Z",
        "updated_at": "2026-05-20T00:00:00Z",
        "original_message_id": f"msg-{pid}",
        "raw_request": "Authorized exact edit. Replace phone number.",
        "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+19045550104"},
        "manual_review": mr,
        "assets": [], "concepts": [], "selected_concept_id": None,
        "revisions": [], "version": 1, "final_asset_ids": [], "approved_message_id": "",
    }


def _seed(tmp_path: Path, projects: list[dict]):
    from app.routers import flyer
    s = flyer.get_settings()
    s.state_dir = tmp_path / "state"
    s.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    s.decisions_path = tmp_path / "logs" / "decisions.log"
    p = s.state_dir / "flyer" / "projects.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"schema_version": 1, "next_sequence": len(projects) + 1, "projects": projects}),
        encoding="utf-8",
    )
    return flyer


def test_claim_sets_owner_and_time_and_persists(tmp_path):
    flyer = _seed(tmp_path, [_queued_project("F0001")])
    r = flyer.manual_queue_claim_action("F0001", admin_id="priya", force=False)
    assert r["claimed_by"] == "priya"
    assert r["claimed_at"]
    assert r["previous_owner"] == ""
    detail = flyer.manual_queue_detail_action("F0001")
    assert detail["manual_review"]["claimed_by"] == "priya"
    assert detail["manual_review"]["claimed_at"]


def test_claim_conflict_409_when_owned_by_another_then_force_overrides(tmp_path):
    from fastapi import HTTPException
    flyer = _seed(tmp_path, [_queued_project("F0001", claimed_by="sam", claimed_at="2026-05-20T01:00:00Z")])
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_claim_action("F0001", admin_id="priya", force=False)
    assert exc.value.status_code == 409
    r = flyer.manual_queue_claim_action("F0001", admin_id="priya", force=True)
    assert r["claimed_by"] == "priya"
    assert r["previous_owner"] == "sam"


def test_reclaim_by_same_admin_is_idempotent(tmp_path):
    flyer = _seed(tmp_path, [_queued_project("F0001", claimed_by="priya", claimed_at="2026-05-20T01:00:00Z")])
    r = flyer.manual_queue_claim_action("F0001", admin_id="priya", force=False)
    assert r["claimed_by"] == "priya"


def test_unclaim_clears_owner(tmp_path):
    flyer = _seed(tmp_path, [_queued_project("F0001", claimed_by="priya", claimed_at="2026-05-20T01:00:00Z")])
    r = flyer.manual_queue_unclaim_action("F0001", admin_id="priya", force=False)
    assert r["claimed_by"] == ""
    assert r["claimed_at"] is None
    detail = flyer.manual_queue_detail_action("F0001")
    assert detail["manual_review"]["claimed_by"] == ""
    assert detail["manual_review"]["claimed_at"] is None


def test_unclaim_by_non_owner_409_without_force(tmp_path):
    from fastapi import HTTPException
    flyer = _seed(tmp_path, [_queued_project("F0001", claimed_by="sam", claimed_at="2026-05-20T01:00:00Z")])
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_unclaim_action("F0001", admin_id="priya", force=False)
    assert exc.value.status_code == 409


def test_assign_reassigns_and_records_previous_owner(tmp_path):
    flyer = _seed(tmp_path, [_queued_project("F0001", claimed_by="sam", claimed_at="2026-05-20T01:00:00Z")])
    r = flyer.manual_queue_assign_action("F0001", admin_id="priya", by="sam")
    assert r["claimed_by"] == "priya"
    assert r["previous_owner"] == "sam"


def test_claim_404_unknown_project(tmp_path):
    from fastapi import HTTPException
    flyer = _seed(tmp_path, [_queued_project("F0001")])
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_claim_action("F9999", admin_id="priya", force=False)
    assert exc.value.status_code == 404
