"""P5 team-ops: atomic 'claim next' throughput action.

Covers `POST /flyer/manual-queue/claim-next` — hands an idle admin the OLDEST
unclaimed manual-queue case and claims it for them in one atomic step
(selection + claim under a single flock so two admins racing claim-next can't
grab the same case). Reuses the existing claim machinery; no customer-facing
surface.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _queued_project(
    project_id: str,
    *,
    phone: str = "+19045550104",
    status: str = "manual_edit_required",
    manual_status: str = "queued",
    claimed_by: str = "",
    queued_at: str = "2026-05-20T00:00:00Z",
    reason_code: str = "source_edit_provider_unavailable",
) -> dict:
    return {
        "project_id": project_id,
        "status": status,
        "customer_phone": phone,
        "created_at": queued_at,
        "updated_at": queued_at,
        "original_message_id": f"msg-{project_id}",
        "raw_request": "Authorized exact edit.",
        "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": phone},
        "manual_review": {
            "status": manual_status,
            "reason": reason_code,
            "reason_code": reason_code,
            "detail": "",
            "queued_at": queued_at,
            "claimed_by": claimed_by,
            "claimed_at": "2026-05-20T00:00:00Z" if claimed_by else None,
        },
        "assets": [],
        "concepts": [],
        "selected_concept_id": None,
        "revisions": [],
        "version": 1,
        "final_asset_ids": [],
        "approved_message_id": "",
    }


def _seed_store(tmp_path: Path, projects: list[dict]) -> None:
    from app.routers import flyer
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    settings.decisions_path = tmp_path / "logs" / "decisions.log"
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_sequence": len(projects) + 1, "projects": projects},
    )


def _reload_owner(project_id: str) -> str:
    from app.routers import flyer
    store = flyer.load_project_store()
    project = next(p for p in store.projects if p.project_id == project_id)
    return project.manual_review.claimed_by


def test_claim_next_claims_oldest_unclaimed(tmp_path):
    from app.routers import flyer
    _seed_store(tmp_path, [
        _queued_project("F0071", queued_at="2026-05-25T00:00:00Z"),  # newer
        _queued_project("F0070", queued_at="2026-05-20T00:00:00Z"),  # OLDEST
    ])
    result = flyer.manual_queue_claim_next_action(admin_id="alice")
    assert result["claimed"] is True
    assert result["project_id"] == "F0070"  # oldest queue-entry first
    assert result["claimed_by"] == "alice"
    assert result["claimed_at"]
    # Persisted, and the newer one is untouched.
    assert _reload_owner("F0070") == "alice"
    assert _reload_owner("F0071") == ""


def test_claim_next_skips_already_claimed_rows(tmp_path):
    from app.routers import flyer
    _seed_store(tmp_path, [
        _queued_project("F0070", queued_at="2026-05-20T00:00:00Z", claimed_by="bob"),  # older but taken
        _queued_project("F0071", queued_at="2026-05-25T00:00:00Z"),  # only free one
    ])
    result = flyer.manual_queue_claim_next_action(admin_id="alice")
    assert result["claimed"] is True
    assert result["project_id"] == "F0071"
    assert _reload_owner("F0070") == "bob"  # untouched


def test_claim_next_all_claimed_returns_not_claimed(tmp_path):
    from app.routers import flyer
    _seed_store(tmp_path, [
        _queued_project("F0070", claimed_by="bob"),
        _queued_project("F0071", claimed_by="carol"),
    ])
    result = flyer.manual_queue_claim_next_action(admin_id="alice")
    assert result["claimed"] is False
    assert result["reason"] == "no_unclaimed_cases"
    assert result["project_id"] == ""


def test_claim_next_empty_queue_returns_not_claimed(tmp_path):
    from app.routers import flyer
    _seed_store(tmp_path, [])
    result = flyer.manual_queue_claim_next_action(admin_id="alice")
    assert result["claimed"] is False
    assert result["reason"] == "no_unclaimed_cases"


def test_claim_next_ignores_non_queue_status(tmp_path):
    from app.routers import flyer
    _seed_store(tmp_path, [
        # closed/delivered rows must never be claimed even if unclaimed + older.
        _queued_project("F0070", status="closed_no_send", manual_status="closed_no_send",
                        queued_at="2026-05-10T00:00:00Z"),
        _queued_project("F0071", queued_at="2026-05-25T00:00:00Z"),  # only valid queue row
    ])
    result = flyer.manual_queue_claim_next_action(admin_id="alice")
    assert result["claimed"] is True
    assert result["project_id"] == "F0071"
    assert _reload_owner("F0070") == ""  # closed row untouched


def test_claim_next_claims_unclaimed_in_progress(tmp_path):
    from app.routers import flyer
    _seed_store(tmp_path, [
        _queued_project("F0070", manual_status="in_progress", queued_at="2026-05-20T00:00:00Z"),
    ])
    result = flyer.manual_queue_claim_next_action(admin_id="alice")
    assert result["claimed"] is True
    assert result["project_id"] == "F0070"
    assert result["manual_status"] == "in_progress"


def test_claim_next_whitespace_admin_422(tmp_path):
    from app.routers import flyer
    from fastapi import HTTPException
    _seed_store(tmp_path, [_queued_project("F0070")])
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_claim_next_action(admin_id="   ")
    assert exc.value.status_code == 422
    assert _reload_owner("F0070") == ""  # nothing claimed on a bad handle
