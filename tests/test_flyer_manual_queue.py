from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from schemas import (
    FlyerManualReview,
    FlyerProject,
    FlyerProjectStore,
    FlyerReferenceExtraction,
    FlyerVisualQAReport,
)


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
            reason_code="source_edit_provider_unavailable",
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
    assert str(tmp_path / "manual" / "F9100") in project.assets[-1].path
    assert Path(project.assets[-1].path).exists()


def test_complete_manual_project_rejects_nonqueued_project(tmp_path, monkeypatch):
    from agents.flyer.manual_queue import complete_manual_project

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = tmp_path / "approved.png"
    asset.write_bytes(b"approved")
    project = _manual_project().model_copy(update={
        "status": "delivered",
        "manual_review": FlyerManualReview(status="none"),
    })
    store = FlyerProjectStore(projects=[project])

    with pytest.raises(ValueError, match="not queued for manual completion"):
        complete_manual_project(store, "F9100", asset, reason="designer approved")


def test_make_manual_review_sets_status_reason_and_code():
    from agents.flyer.manual_queue import make_manual_review

    review = make_manual_review(reason_code="visual_qa_failed", detail="missing headline")

    assert review.status == "queued"
    assert review.reason == "visual_qa_failed"
    assert review.reason_code == "visual_qa_failed"
    assert review.detail == "missing headline"
    assert review.queued_at is not None


def test_make_manual_review_human_reason_overrides_code_text():
    from agents.flyer.manual_queue import make_manual_review

    review = make_manual_review(reason_code="operator_request", reason="ops cleared by Daisy")
    assert review.reason == "ops cleared by Daisy"
    assert review.reason_code == "operator_request"


def test_make_manual_review_rejects_invalid_reason_code():
    from agents.flyer.manual_queue import make_manual_review
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_manual_review(reason_code="not_a_real_code")  # type: ignore[arg-type]


def test_list_manual_queue_includes_reason_code():
    from agents.flyer.manual_queue import list_manual_queue

    rows = list_manual_queue(FlyerProjectStore(projects=[_manual_project()]), now=datetime(2026, 5, 20, tzinfo=timezone.utc))
    assert rows[0]["manual_reason_code"] == "source_edit_provider_unavailable"


def _project(project_id: str, phone: str, age_hours: int, *, reason_code: str = "operator_request", status: str = "manual_edit_required") -> FlyerProject:
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    queued_at = now - timedelta(hours=age_hours)
    return FlyerProject(
        project_id=project_id,
        status=status,
        customer_phone=phone,
        created_at=queued_at,
        updated_at=queued_at,
        original_message_id=f"m-{project_id}",
        raw_request=f"raw {project_id}",
        manual_review=FlyerManualReview(
            status="queued",
            reason=reason_code,
            reason_code=reason_code,
            detail=f"detail {project_id}",
            queued_at=queued_at,
        ),
    )


def test_triage_summary_groups_by_customer_and_aggregates_reasons():
    from agents.flyer.manual_queue import triage_summary

    store = FlyerProjectStore(projects=[
        _project("F0036", "+19803826497", age_hours=19, reason_code="visual_qa_failed"),
        _project("F0043", "+19803826497", age_hours=18, reason_code="visual_qa_failed"),
        _project("F0045", "+19803826497", age_hours=17, reason_code="legacy_unknown"),
        _project("F0052", "+19045550104", age_hours=13, reason_code="source_edit_provider_unavailable"),
        _project("F0056", "+17329837841", age_hours=12, reason_code="source_edit_provider_unavailable"),
    ])

    summary = triage_summary(store, now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc))

    assert summary["total"] == 5
    assert summary["reason_counts"] == {
        "visual_qa_failed": 2,
        "source_edit_provider_unavailable": 2,
        "legacy_unknown": 1,
    }
    assert [g["customer_phone"] for g in summary["groups"]] == ["+19803826497", "+19045550104", "+17329837841"]
    chloe = summary["groups"][0]
    assert chloe["count"] == 3
    assert chloe["oldest_age_hours"] == 19
    assert [p["project_id"] for p in chloe["projects"]] == ["F0036", "F0043", "F0045"]


def test_classify_legacy_reason_picks_visual_qa_when_qa_failed():
    from agents.flyer.manual_queue import classify_legacy_reason

    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    project = _manual_project().model_copy(update={
        "qa_reports": [FlyerVisualQAReport(
            project_id="F9100",
            asset_id="A0001",
            artifact_path="/tmp/F9100-C1.png",
            artifact_sha256="0" * 64,
            project_version=1,
            output_format="whatsapp_image",
            provider="sidecar",
            qa_source="ocr_vision",
            status="failed",
            blockers=["missing headline", "placeholder [price]"],
            checked_at=now,
        )],
    })

    code, detail = classify_legacy_reason(project)
    assert code == "visual_qa_failed"
    assert "missing headline" in detail


def test_classify_legacy_reason_picks_reference_status_when_extraction_failed():
    from agents.flyer.manual_queue import classify_legacy_reason

    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    project = _manual_project().model_copy(update={
        "qa_reports": [],
        "reference_extractions": [FlyerReferenceExtraction(
            asset_id="A0001",
            role="menu_reference",
            provider="openai",
            status="low_confidence",
            detail="OCR could not read prices clearly",
            extracted_at=now,
        )],
    })

    code, detail = classify_legacy_reason(project)
    assert code == "reference_low_confidence"
    assert "OCR could not read" in detail


def test_classify_legacy_reason_detects_source_edit_in_raw_request():
    from agents.flyer.manual_queue import classify_legacy_reason

    project = _manual_project().model_copy(update={
        "qa_reports": [],
        "reference_extractions": [],
        "raw_request": "Authorized flyer/source artwork update. Replace phone number.",
    })

    code, _ = classify_legacy_reason(project)
    assert code == "source_edit_provider_unavailable"


def test_classify_legacy_reason_falls_back_to_legacy_unknown():
    from agents.flyer.manual_queue import classify_legacy_reason

    project = _manual_project().model_copy(update={
        "qa_reports": [],
        "reference_extractions": [],
        "raw_request": "Create a flyer for our salon special",
    })

    code, detail = classify_legacy_reason(project)
    assert code == "legacy_unknown"
    assert "salon" in detail


def test_backfill_dry_run_returns_candidates_without_mutating():
    from agents.flyer.manual_queue import backfill_manual_reasons

    legacy = _manual_project().model_copy(update={
        "raw_request": "Authorized flyer/source artwork update. Replace phone number.",
        "manual_review": FlyerManualReview(status="none"),  # legacy: never populated, defaults to unclassified
    })
    store = FlyerProjectStore(projects=[legacy])

    result = backfill_manual_reasons(store, apply=False)

    assert result["applied"] is False
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["proposed_reason_code"] == "source_edit_provider_unavailable"
    assert store.projects[0].manual_review.reason_code == "unclassified"


def test_backfill_apply_mutates_store_and_is_idempotent():
    from agents.flyer.manual_queue import backfill_manual_reasons

    legacy = _manual_project().model_copy(update={
        "raw_request": "Authorized flyer/source artwork update. Replace phone number.",
        "manual_review": FlyerManualReview(status="none"),
    })
    store = FlyerProjectStore(projects=[legacy])

    first = backfill_manual_reasons(store, apply=True)
    assert first["applied"] is True
    assert first["candidate_count"] == 1
    assert store.projects[0].manual_review.reason_code == "source_edit_provider_unavailable"
    assert store.projects[0].manual_review.status == "queued"

    second = backfill_manual_reasons(store, apply=True)
    assert second["candidate_count"] == 0


def test_backfill_skips_already_classified_projects():
    from agents.flyer.manual_queue import backfill_manual_reasons

    keep = _manual_project()  # has reason_code="source_edit_provider_unavailable"
    store = FlyerProjectStore(projects=[keep])

    result = backfill_manual_reasons(store, apply=False)

    assert result["candidate_count"] == 0
