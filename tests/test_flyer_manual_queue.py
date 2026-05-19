from __future__ import annotations

from datetime import datetime, timezone

from schemas import FlyerManualReview, FlyerProject, FlyerProjectStore


def _manual_project():
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F9100",
        status="manual_edit_required",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-manual",
        raw_request="Remove extra 08:00 from this uploaded flyer",
        manual_review=FlyerManualReview(
            status="queued",
            reason="source_edit_provider_unavailable",
            detail="OPENAI_API_KEY missing",
            queued_at=now,
        ),
    )


def test_manual_queue_lists_queued_projects_with_reason():
    from agents.flyer.manual_queue import list_manual_queue

    rows = list_manual_queue(FlyerProjectStore(projects=[_manual_project()]), now=datetime(2026, 5, 20, tzinfo=timezone.utc))

    assert rows[0]["project_id"] == "F9100"
    assert rows[0]["manual_reason"] == "source_edit_provider_unavailable"
    assert rows[0]["age_hours"] == 24


def test_complete_manual_project_attaches_operator_asset(tmp_path, monkeypatch):
    from agents.flyer.manual_queue import complete_manual_project

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = tmp_path / "approved.png"
    asset.write_bytes(b"approved")
    store = FlyerProjectStore(projects=[_manual_project()])

    updated = complete_manual_project(store, "F9100", asset, reason="designer approved")
    project = updated.projects[0]

    assert project.status == "awaiting_final_approval"
    assert project.manual_review.status == "completed"
    assert project.concepts[0].preview_asset_id == "A0001"
