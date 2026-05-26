from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from schemas import (
    FlyerAsset,
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
    assert rows[0]["age_minutes"] == 24 * 60
    assert rows[0]["age_hours"] == 24
    assert rows[0]["is_stale"] is True


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


def test_close_manual_project_marks_no_send_terminal_and_excludes_from_queue():
    from agents.flyer.manual_queue import close_manual_project, list_manual_queue

    store = FlyerProjectStore(projects=[_manual_project()])

    updated = close_manual_project(store, "F9100", reason="stale/superseded operator cleanup")
    project = updated.projects[0]

    assert project.status == "closed_no_send"
    assert project.manual_review.status == "closed_no_send"
    assert project.manual_review.detail == "stale/superseded operator cleanup"
    assert project.manual_review.completed_at is not None
    assert list_manual_queue(updated) == []


def test_close_manual_project_rejects_nonqueued_project():
    from agents.flyer.manual_queue import close_manual_project

    project = _manual_project().model_copy(update={
        "status": "delivered",
        "manual_review": FlyerManualReview(status="none"),
    })
    store = FlyerProjectStore(projects=[project])

    with pytest.raises(ValueError, match="not queued for manual close"):
        close_manual_project(store, "F9100", reason="stale/superseded operator cleanup")


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


def test_triage_reason_family_normalizes_reason_code_case():
    from agents.flyer.manual_queue import _reason_family
    assert _reason_family("Visual_QA_Failed") == "visual_quality"
    assert _reason_family(" source_edit_provider_unavailable ") == "provider_readiness"


def test_triage_operator_hint_normalizes_reason_code_case():
    from agents.flyer.manual_queue import _operator_action_hint
    hint = _operator_action_hint(" Source_Edit_Provider_Unavailable ")
    assert "provider credentials" in hint.lower()


def test_list_manual_queue_excludes_nonqueued_projects_with_old_failed_qa():
    from agents.flyer.manual_queue import list_manual_queue

    now = datetime(2026, 5, 20, tzinfo=timezone.utc)
    delivered = _manual_project().model_copy(update={
        "project_id": "F9200",
        "status": "delivered",
        "manual_review": FlyerManualReview(status="none"),
        "qa_reports": [
            FlyerVisualQAReport(
                project_id="F9200",
                asset_id="A0001",
                artifact_path="/tmp/f9200.png",
                artifact_sha256="0" * 64,
                project_version=1,
                output_format="concept_preview",
                provider="sidecar",
                qa_source="ocr_vision",
                status="failed",
                blockers=["old blocker"],
                checked_at=now,
            )
        ],
    })
    awaiting = delivered.model_copy(update={
        "project_id": "F9201",
        "status": "awaiting_final_approval",
    })

    rows = list_manual_queue(FlyerProjectStore(projects=[delivered, awaiting]), now=now)

    assert rows == []


def test_list_manual_queue_includes_reason_code():
    from agents.flyer.manual_queue import list_manual_queue

    rows = list_manual_queue(FlyerProjectStore(projects=[_manual_project()]), now=datetime(2026, 5, 20, tzinfo=timezone.utc))
    assert rows[0]["manual_reason_code"] == "source_edit_provider_unavailable"


def test_list_manual_queue_adds_reason_family_action_and_priority_fields():
    from agents.flyer.manual_queue import list_manual_queue

    rows = list_manual_queue(
        FlyerProjectStore(projects=[_manual_project()]),
        now=datetime(2026, 5, 19, 0, 30, tzinfo=timezone.utc),
    )

    assert rows[0]["reason_family"] == "provider_readiness"
    assert rows[0]["operator_action_hint"] == "Configure provider credentials or keep designer-assisted path."
    assert rows[0]["age_priority"] == "fresh"
    assert rows[0]["customer_update_due"] is False


def test_list_manual_queue_marks_customer_update_due_for_stale_visual_qa_rows():
    from agents.flyer.manual_queue import list_manual_queue

    now = datetime(2026, 5, 20, tzinfo=timezone.utc)
    stale_visual = _manual_project().model_copy(
        update={
            "manual_review": FlyerManualReview(
                status="queued",
                reason="visual_qa_failed",
                reason_code="visual_qa_failed",
                detail="text overlap",
                queued_at=now - timedelta(minutes=200),
            )
        }
    )

    rows = list_manual_queue(
        FlyerProjectStore(projects=[stale_visual]),
        now=now,
        stale_minutes_threshold=30,
    )

    assert rows[0]["reason_family"] == "visual_quality"
    assert rows[0]["operator_action_hint"] == "Review QA blockers and correct layout/text in manual edit."
    assert rows[0]["age_priority"] == "critical"
    assert rows[0]["customer_update_due"] is True


def test_list_manual_queue_surfaces_source_edit_integrity_mode(tmp_path, monkeypatch):
    """Cockpit triage needs to distinguish source-edit previews that passed
    text-manifest integrity only from fully OCR/visual-QA'd generated flyers.
    """
    from agents.flyer.manual_queue import list_manual_queue

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    preview = tmp_path / "manual" / "F9100" / "F9100-C1-preview.png"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"png")
    Path(f"{preview}.text.json").write_text(
        '{"verification_mode":"source_edit_integrity_only"}',
        encoding="utf-8",
    )
    project = _manual_project().model_copy(update={
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="concept_preview",
                source="rendered",
                path=str(preview),
                mime_type="image/png",
                sha256="0" * 64,
                original_message_id="m-manual",
                received_at=now,
            )
        ],
    })

    rows = list_manual_queue(FlyerProjectStore(projects=[project]), now=datetime(2026, 5, 20, tzinfo=timezone.utc))

    assert rows[0]["verification_modes"] == ["source_edit_integrity_only"]


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
    assert summary["stale_total"] == 5
    assert summary["stale_minutes_threshold"] == 30
    assert summary["reason_counts"] == {
        "visual_qa_failed": 2,
        "source_edit_provider_unavailable": 2,
        "legacy_unknown": 1,
    }
    assert [g["customer_phone"] for g in summary["groups"]] == ["+19803826497", "+19045550104", "+17329837841"]
    chloe = summary["groups"][0]
    assert chloe["count"] == 3
    assert chloe["stale_count"] == 3
    assert chloe["oldest_age_hours"] == 19
    assert chloe["oldest_age_minutes"] == 19 * 60
    assert [p["project_id"] for p in chloe["projects"]] == ["F0036", "F0043", "F0045"]
    assert summary["manual_status_counts"] == {"queued": 5}
    assert chloe["oldest_queued_at"] == "2026-05-18T17:00:00+00:00"


def test_triage_summary_surfaces_manual_status_split():
    from agents.flyer.manual_queue import triage_summary

    queued = _project("F0060", "+19045550104", age_hours=3, reason_code="visual_qa_failed")
    in_progress = queued.model_copy(update={
        "project_id": "F0061",
        "manual_review": queued.manual_review.model_copy(update={"status": "in_progress"}),
    })
    summary = triage_summary(
        FlyerProjectStore(projects=[queued, in_progress]),
        now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
    )
    assert summary["manual_status_counts"] == {"in_progress": 1, "queued": 1}


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


# --------------------------------------------------------------------------
# flyer-manual-queue --close freshness guard
# --------------------------------------------------------------------------


def _fresh_store():
    now = datetime(2026, 5, 19, 21, 4, 4, tzinfo=timezone.utc)
    return FlyerProjectStore(projects=[FlyerProject(
        project_id="F0058",
        status="manual_edit_required",
        customer_phone="+19045550104",
        created_at=now,
        updated_at=now,
        original_message_id="m-58",
        raw_request="Authorized exact edit",
        manual_review=FlyerManualReview(
            status="queued",
            reason="source_edit_provider_unavailable",
            reason_code="source_edit_provider_unavailable",
            queued_at=now,
        ),
    )])


@pytest.mark.parametrize("token", ["duplicate", "test", "superseded", "provider_unavailable_after_retry"])
def test_close_freshness_guard_allows_fresh_row_with_documented_reason_token(token):
    from agents.flyer.manual_queue import enforce_close_freshness_guard
    now = datetime(2026, 5, 19, 21, 10, 0, tzinfo=timezone.utc)  # +6 min: fresh
    # No exception raised
    enforce_close_freshness_guard(
        _fresh_store(), "F0058",
        reason=f"operator_burndown_{token}",
        force=False,
        now=now,
    )


def test_close_freshness_guard_allows_fresh_row_with_force():
    from agents.flyer.manual_queue import enforce_close_freshness_guard
    now = datetime(2026, 5, 19, 21, 10, 0, tzinfo=timezone.utc)
    enforce_close_freshness_guard(
        _fresh_store(), "F0058",
        reason="provider_unavailable_no_customer_asset_sent",
        force=True,
        now=now,
    )


def test_close_freshness_guard_blocks_fresh_row_without_force_or_token():
    from agents.flyer.manual_queue import enforce_close_freshness_guard
    now = datetime(2026, 5, 19, 21, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError) as exc:
        enforce_close_freshness_guard(
            _fresh_store(), "F0058",
            reason="cleanup",
            force=False,
            now=now,
        )
    msg = str(exc.value)
    assert "F0058" in msg
    assert "--force" in msg
    assert "duplicate" in msg  # accepted-token list surfaced to operator


def test_close_freshness_guard_does_not_accept_provider_unavailable_substring():
    """SUBSTRING SLIPPAGE GUARD: the operator-burndown reason as seen in prod
    (`operator_burndown_20260519_duplicate_source_edit_provider_unavailable_no_customer_asset_sent`)
    contains `provider_unavailable` but NOT the documented exact token
    `provider_unavailable_after_retry`. Without word-boundary anchoring on the
    matcher, a bare `provider_unavailable` substring would silently bypass
    the guard. It must NOT — only the exact token (or --force) bypasses."""
    from agents.flyer.manual_queue import enforce_close_freshness_guard
    now = datetime(2026, 5, 19, 21, 10, 0, tzinfo=timezone.utc)
    reason = "operator_burndown_provider_unavailable_no_customer_asset_sent"
    with pytest.raises(ValueError):
        enforce_close_freshness_guard(
            _fresh_store(), "F0058",
            reason=reason,
            force=False,
            now=now,
        )
    # Same reason BUT containing duplicate — bypassed via duplicate token.
    enforce_close_freshness_guard(
        _fresh_store(), "F0058",
        reason="operator_burndown_20260519_duplicate_" + reason,
        force=False,
        now=now,
    )


def test_close_freshness_guard_passes_aged_rows():
    """A row older than the freshness threshold can be closed with any
    non-empty reason — the guard is targeted at fresh-row protection only."""
    from agents.flyer.manual_queue import enforce_close_freshness_guard
    now = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)  # +56 min
    enforce_close_freshness_guard(
        _fresh_store(), "F0058",
        reason="cleanup",
        force=False,
        now=now,
    )


def test_close_freshness_guard_uses_queue_row_age_not_project_age():
    """REGRESSION: An old project that JUST transitioned to manual_edit_required
    (e.g., after a generation failure or break-glass round-trip) has a fresh
    `manual_review.queued_at` but an old `created_at`. The guard must use the
    queue-row age, not the project age — otherwise a row queued seconds ago
    can be closed silently because the underlying project is days old."""
    from agents.flyer.manual_queue import enforce_close_freshness_guard
    project_created = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)  # 4+ days ago
    queued = datetime(2026, 5, 19, 21, 5, 0, tzinfo=timezone.utc)  # 5 min before "now"
    now = datetime(2026, 5, 19, 21, 10, 0, tzinfo=timezone.utc)
    store = FlyerProjectStore(projects=[FlyerProject(
        project_id="F0058",
        status="manual_edit_required",
        customer_phone="+19045550104",
        created_at=project_created,
        updated_at=queued,
        original_message_id="m-58",
        raw_request="Authorized exact edit",
        manual_review=FlyerManualReview(
            status="queued",
            reason="source_edit_provider_unavailable",
            reason_code="source_edit_provider_unavailable",
            queued_at=queued,
        ),
    )])
    with pytest.raises(ValueError) as exc:
        enforce_close_freshness_guard(
            store, "F0058",
            reason="cleanup",
            force=False,
            now=now,
        )
    assert "F0058" in str(exc.value)
    assert "queue row" in str(exc.value).lower()  # error names the right age source


def test_close_freshness_guard_reason_match_is_case_insensitive():
    """Reason matching normalizes to lowercase so uppercase token typed by
    the operator still passes."""
    from agents.flyer.manual_queue import enforce_close_freshness_guard
    now = datetime(2026, 5, 19, 21, 10, 0, tzinfo=timezone.utc)
    enforce_close_freshness_guard(
        _fresh_store(), "F0058",
        reason="DUPLICATE",
        force=False,
        now=now,
    )


def test_close_freshness_guard_only_invoked_inside_close_branch():
    """The guard is bound to `--close` only — `--complete` and other
    dispositions must not be subject to it. Verified by source inspection:
    one call site, gated by the `if args.close:` argparse branch."""
    source = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "flyer-manual-queue"
    text = source.read_text(encoding="utf-8")
    assert text.count("enforce_close_freshness_guard(") == 1, (
        "guard must be invoked exactly once (inside --close)"
    )
    close_idx = text.find("if args.close:")
    complete_idx = text.find("if args.complete:")
    guard_idx = text.find("enforce_close_freshness_guard(")
    assert close_idx >= 0 and complete_idx >= 0 and guard_idx >= 0
    assert complete_idx < close_idx < guard_idx, (
        "guard must sit inside the --close branch, after --complete"
    )


def test_complete_writes_qa_sidecars_so_send_path_clears(tmp_path, monkeypatch):
    """REGRESSION (PR #131 review finding 1): a cockpit "Complete with
    uploaded asset" action must produce a project state that the
    downstream send path (`send_flyer_concept_previews`) can ACTUALLY
    deliver. Before this fix, `complete_manual_project` attached the
    uploaded file as a concept_preview but wrote no `.text.json` or
    `.visual_qa.json` sidecars, so `validate_text_manifest_file` returned
    `text manifest missing` and the customer never received the preview.

    Pin both halves of the fix:
      1. After complete, the artifact's text manifest sidecar exists,
         carries `source_edit_integrity_only` verification mode, and
         validates.
      2. The visual QA sidecar exists, is attributed to `operator_review`
         (NOT `sidecar_test` — that's dev-only), and passes validation
         without the `FLYER_QA_ALLOW_SIDECAR` env gate.
    """
    from agents.flyer.manual_queue import complete_manual_project
    from agents.flyer.render import validate_text_manifest_file
    from agents.flyer.visual_qa import validate_visual_qa_report

    # FlyerAsset.path validator constrains the asset to live under this
    # root; both the upload source and the post-copy dest must satisfy it.
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    # The whole point of qa_source=="operator_review" is to clear without
    # this env flag. Make sure it's off.
    monkeypatch.delenv("FLYER_QA_ALLOW_SIDECAR", raising=False)

    queued_at = datetime(2026, 5, 19, 21, 0, 0, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0058",
        status="manual_edit_required",
        customer_phone="+19045550104",
        created_at=queued_at,
        updated_at=queued_at,
        original_message_id="m-58",
        raw_request="Authorized exact edit. Replace phone number.",
        manual_review=FlyerManualReview(
            status="queued",
            reason="source_edit_provider_unavailable",
            reason_code="source_edit_provider_unavailable",
            queued_at=queued_at,
        ),
    )
    store = FlyerProjectStore(projects=[project])

    asset_src = tmp_path / "operator-uploads" / "approved.png"
    asset_src.parent.mkdir(parents=True, exist_ok=True)
    asset_src.write_bytes(b"\x89PNG\r\n\x1a\noperator-approved-bytes")

    updated = complete_manual_project(store, "F0058", asset_src, reason="operator-approved designer asset")
    completed = next(p for p in updated.projects if p.project_id == "F0058")
    assert completed.status == "awaiting_final_approval"
    assert completed.selected_concept_id == "C1"
    assert len(completed.concepts) == 1

    asset = next(a for a in completed.assets if a.kind == "concept_preview")
    artifact = Path(asset.path)
    assert artifact.exists(), f"asset must be copied under FLYER_STATE_ROOT: {artifact}"

    text_qa = validate_text_manifest_file(
        artifact,
        project_id="F0058",
        project_version=completed.version,
        output_format="concept_preview",
    )
    assert text_qa.ok, (
        f"text QA gate must clear after operator complete; blockers={text_qa.blockers}"
    )

    visual_qa = validate_visual_qa_report(
        artifact,
        project_id="F0058",
        project_version=completed.version,
        output_format="concept_preview",
        allow_sidecar=False,
    )
    assert visual_qa.ok, (
        f"visual QA gate must clear after operator complete; blockers={visual_qa.blockers}"
    )

    qa_doc = json.loads(Path(str(artifact) + ".qa.json").read_text(encoding="utf-8"))
    assert qa_doc["qa_source"] == "operator_review", qa_doc.get("qa_source")
    assert qa_doc["status"] == "passed"
    assert qa_doc["provider"] == "operator-cockpit"


def test_operator_review_qa_source_passes_without_sidecar_env_gate():
    """The `operator_review` qa_source is a distinct semantic from
    `sidecar_test`: the latter is dev-only (gated by FLYER_QA_ALLOW_SIDECAR),
    the former carries operator authority by construction. Pin that the
    validator does NOT add the sidecar-disabled blocker for operator_review."""
    from agents.flyer.visual_qa import validate_visual_qa_report, write_visual_qa_report
    from schemas import FlyerVisualQAReport
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        artifact = td_path / "img.png"
        artifact.write_bytes(b"\x89PNG\r\n\x1a\nbytes")
        sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
        report = FlyerVisualQAReport(
            project_id="F0058",
            asset_id="A0001",
            artifact_path=str(artifact),
            artifact_sha256=sha,
            project_version=1,
            output_format="concept_preview",
            provider="operator-cockpit",
            qa_source="operator_review",
            status="passed",
            blockers=[],
            warnings=[],
            extracted_text="",
            checked_at=datetime.now(timezone.utc),
        )
        write_visual_qa_report(report, artifact)
        result = validate_visual_qa_report(
            artifact,
            project_id="F0058",
            project_version=1,
            output_format="concept_preview",
            allow_sidecar=False,
        )
        assert result.ok, (
            f"operator_review must validate without the sidecar env flag; "
            f"blockers={result.blockers}"
        )


# --------------------------------------------------------------------------
# Proactive closure customer-notification
# --------------------------------------------------------------------------


def _two_customer_store(tmp_path, *, alpha_chat=True, beta_chat=True):
    """Write a flyer customers.json fixture with two customers so tests can
    pin ownership/isolation behavior in resolve_customer_chat_id_by_phone."""
    customers = [
        {
            "customer_id": "CUST0001",
            "business_name": "Lakshmis Kitchen",
            "primary_chat_id": "201975216009469@lid" if alpha_chat else "",
            "business_whatsapp_number": "+19045550104",
            "authorized_request_numbers": ["+19045550104"],
        },
        {
            "customer_id": "CUST0002",
            "business_name": "Fresh Meats",
            "primary_chat_id": "201234567890123@lid" if beta_chat else "",
            "business_whatsapp_number": "+19048626362",
            "authorized_request_numbers": ["+19048626362"],
        },
    ]
    path = tmp_path / "customers.json"
    path.write_text(json.dumps({"customers": customers}), encoding="utf-8")
    return path


def test_resolve_customer_chat_id_by_phone_finds_owning_customer(tmp_path):
    from agents.flyer.manual_queue import resolve_customer_chat_id_by_phone
    path = _two_customer_store(tmp_path)
    assert resolve_customer_chat_id_by_phone(path, "+19045550104") == "201975216009469@lid"
    assert resolve_customer_chat_id_by_phone(path, "+19048626362") == "201234567890123@lid"


def test_resolve_customer_chat_id_by_phone_returns_none_for_unknown_phone(tmp_path):
    from agents.flyer.manual_queue import resolve_customer_chat_id_by_phone
    path = _two_customer_store(tmp_path)
    assert resolve_customer_chat_id_by_phone(path, "+10000000000") is None


def test_resolve_customer_chat_id_by_phone_returns_none_when_chat_id_blank(tmp_path):
    from agents.flyer.manual_queue import resolve_customer_chat_id_by_phone
    path = _two_customer_store(tmp_path, alpha_chat=False)
    assert resolve_customer_chat_id_by_phone(path, "+19045550104") is None
    # Other customer's chat_id is unaffected
    assert resolve_customer_chat_id_by_phone(path, "+19048626362") == "201234567890123@lid"


def test_resolve_customer_chat_id_by_phone_handles_missing_store(tmp_path):
    from agents.flyer.manual_queue import resolve_customer_chat_id_by_phone
    assert resolve_customer_chat_id_by_phone(tmp_path / "nope.json", "+19045550104") is None


def test_recent_inbound_chat_id_ignores_synthetic_status_jids(tmp_path):
    from agents.flyer.manual_queue import find_recent_inbound_chat_id_for_project

    decisions = tmp_path / "decisions.log"
    decisions.write_text(
        "\n".join([
            json.dumps({
                "type": "cf_router_intercepted",
                "reason": "flyer_primary_project_created",
                "chat_id": "201975216009469@lid",
                "detail": "project_id=F0102; sender_role=employee",
            }),
            json.dumps({
                "type": "cf_router_intercepted",
                "reason": "flyer_customer_not_active",
                "chat_id": "cancelled@s.whatsapp.net",
                "detail": "project_id=F0102; customer_id=CUST0001; status=cancelled",
            }),
        ]) + "\n",
        encoding="utf-8",
    )

    assert find_recent_inbound_chat_id_for_project(decisions, "F0102") == "201975216009469@lid"


def _closed_store(*, phone="+19045550104", reason_code="source_edit_provider_unavailable"):
    """Single-project store shaped as if close_manual_project just ran."""
    queued = datetime(2026, 5, 19, 21, 4, 4, tzinfo=timezone.utc)
    closed = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)
    return FlyerProjectStore(projects=[FlyerProject(
        project_id="F0058",
        status="closed_no_send",
        customer_phone=phone,
        created_at=queued,
        updated_at=closed,
        original_message_id="m-58",
        raw_request="Authorized exact edit",
        manual_review=FlyerManualReview(
            status="closed_no_send",
            reason=reason_code,
            reason_code=reason_code,
            queued_at=queued,
            completed_at=closed,
        ),
    )])


def test_notify_customer_of_closure_success_path(tmp_path):
    from agents.flyer.manual_queue import notify_customer_of_closure
    customers_path = _two_customer_store(tmp_path)
    decisions_log = tmp_path / "decisions.log"
    sent = []
    audited = []
    def bridge(chat_id, text):
        sent.append((chat_id, text))
        return (True, "mid-123", "", "sent")
    def audit(path, line):
        audited.append((path, line))
    entry = notify_customer_of_closure(
        _closed_store(),
        "F0058",
        customers_path=customers_path,
        decisions_log_path=decisions_log,
        bridge_send=bridge,
        audit_append=audit,
        now_fn=lambda: datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc),
    )
    assert len(sent) == 1, "bridge_post must be called exactly once for success path"
    chat_id, text = sent[0]
    assert chat_id == "201975216009469@lid"  # owning customer's chat_id
    assert "F0058" not in text
    assert "apply that source-flyer edit" in text  # CLOSED_NO_SEND_REASON_LINES copy
    assert len(audited) == 1
    audit_path, audit_line = audited[0]
    assert audit_path == decisions_log
    parsed = json.loads(audit_line)
    assert parsed["type"] == "flyer_closure_customer_notified"
    assert parsed["project_id"] == "F0058"
    assert parsed["customer_phone"] == "+19045550104"
    assert parsed["reason_code"] == "source_edit_provider_unavailable"
    assert parsed["chat_id"] == "201975216009469@lid"
    assert parsed["send_ok"] is True
    assert parsed["outbound_message_id"] == "mid-123"
    assert parsed["error"] == ""


def test_notify_customer_of_closure_missing_chat_id(tmp_path):
    """Customer record exists but has no primary_chat_id. Bridge must NOT be
    called; audit row MUST be written with send_ok=false and the documented
    error string. Closure flow always commits regardless."""
    from agents.flyer.manual_queue import notify_customer_of_closure
    customers_path = _two_customer_store(tmp_path, alpha_chat=False)
    decisions_log = tmp_path / "decisions.log"
    bridge_called = []
    audited = []
    def bridge(chat_id, text):
        bridge_called.append((chat_id, text))
        return (True, "x", "", "sent")
    def audit(path, line):
        audited.append(line)
    notify_customer_of_closure(
        _closed_store(),
        "F0058",
        customers_path=customers_path,
        decisions_log_path=decisions_log,
        bridge_send=bridge,
        audit_append=audit,
    )
    assert bridge_called == [], "bridge_post must not be called when chat_id is missing"
    assert len(audited) == 1
    parsed = json.loads(audited[0])
    assert parsed["send_ok"] is False
    assert parsed["chat_id"] == ""
    assert parsed["outbound_message_id"] == ""
    assert parsed["error"] == "no_chat_id_for_customer"


def test_notify_customer_of_closure_bridge_failure(tmp_path):
    """When bridge_post returns ok=False, audit captures the status+error
    string. Closure flow does NOT raise into the caller."""
    from agents.flyer.manual_queue import notify_customer_of_closure
    customers_path = _two_customer_store(tmp_path)
    decisions_log = tmp_path / "decisions.log"
    audited = []
    def bridge(chat_id, text):
        return (False, "", "connection refused", "connect_failed")
    def audit(path, line):
        audited.append(line)
    notify_customer_of_closure(
        _closed_store(),
        "F0058",
        customers_path=customers_path,
        decisions_log_path=decisions_log,
        bridge_send=bridge,
        audit_append=audit,
    )
    assert len(audited) == 1
    parsed = json.loads(audited[0])
    assert parsed["send_ok"] is False
    assert parsed["chat_id"] == "201975216009469@lid"
    assert "connect_failed" in parsed["error"]
    assert "connection refused" in parsed["error"]


def test_notify_customer_of_closure_sends_to_owning_customer_only(tmp_path):
    """Multi-customer fixture: closure for F0058 (owned by CUST0001) must
    reach CUST0001's chat_id only, NEVER CUST0002's. Ownership boundary
    test against the cross-customer leak failure mode."""
    from agents.flyer.manual_queue import notify_customer_of_closure
    customers_path = _two_customer_store(tmp_path)
    decisions_log = tmp_path / "decisions.log"
    sent = []
    def bridge(chat_id, text):
        sent.append(chat_id)
        return (True, "mid", "", "sent")
    notify_customer_of_closure(
        _closed_store(phone="+19045550104"),
        "F0058",
        customers_path=customers_path,
        decisions_log_path=decisions_log,
        bridge_send=bridge,
        audit_append=lambda *_a: None,
    )
    assert sent == ["201975216009469@lid"]
    assert "201234567890123@lid" not in sent  # CUST0002 unaffected


def test_notify_customer_of_closure_returns_silently_when_project_missing(tmp_path):
    """Catastrophic post-close inconsistency (project not in store). Helper
    returns a skipped marker — no audit append, no bridge call, no
    exception."""
    from agents.flyer.manual_queue import notify_customer_of_closure
    customers_path = _two_customer_store(tmp_path)
    decisions_log = tmp_path / "decisions.log"
    bridge_called = []
    audited = []
    result = notify_customer_of_closure(
        FlyerProjectStore(projects=[]),  # empty store: project absent
        "F0058",
        customers_path=customers_path,
        decisions_log_path=decisions_log,
        bridge_send=lambda *_a: bridge_called.append(_a),
        audit_append=lambda *_a: audited.append(_a),
    )
    assert bridge_called == []
    assert audited == []
    assert result.get("skipped") is True
    assert "not_found" in result.get("error", "")


def test_notify_customer_of_closure_swallows_bridge_exception(tmp_path):
    """If bridge_post raises (e.g., URL error, JSON parse error), the
    closure flow MUST NOT see the exception. The error is captured as an
    audited send failure with an 'unexpected: ...' prefix."""
    from agents.flyer.manual_queue import notify_customer_of_closure
    customers_path = _two_customer_store(tmp_path)
    decisions_log = tmp_path / "decisions.log"
    audited = []
    def angry_bridge(chat_id, text):
        raise RuntimeError("simulated bridge crash")
    def audit(path, line):
        audited.append(line)
    notify_customer_of_closure(
        _closed_store(),
        "F0058",
        customers_path=customers_path,
        decisions_log_path=decisions_log,
        bridge_send=angry_bridge,
        audit_append=audit,
    )
    assert len(audited) == 1
    parsed = json.loads(audited[0])
    assert parsed["send_ok"] is False
    assert parsed["error"].startswith("unexpected: RuntimeError"), parsed["error"]


def test_notify_customer_of_closure_audit_validates_as_logentry(tmp_path):
    """The audit row must satisfy the FlyerClosureCustomerNotified discriminated-
    union variant — operator dashboards parsing decisions.log via Pydantic
    will reject malformed rows. Pin schema-level conformance."""
    from agents.flyer.manual_queue import notify_customer_of_closure
    from schemas import LogEntry
    from pydantic import TypeAdapter
    customers_path = _two_customer_store(tmp_path)
    decisions_log = tmp_path / "decisions.log"
    audited = []
    def bridge(chat_id, text):
        return (True, "mid-xyz", "", "sent")
    def audit(path, line):
        audited.append(line)
    notify_customer_of_closure(
        _closed_store(),
        "F0058",
        customers_path=customers_path,
        decisions_log_path=decisions_log,
        bridge_send=bridge,
        audit_append=audit,
    )
    parsed = json.loads(audited[0])
    validated = TypeAdapter(LogEntry).validate_python(parsed)
    assert validated.type == "flyer_closure_customer_notified"
    assert validated.project_id == "F0058"
    assert validated.send_ok is True


def test_build_closure_customer_text_resolves_in_flat_installed_layout(tmp_path):
    """REGRESSION (PR #130 first review): the deployed VPS layout flattens
    Flyer modules to `/opt/shift-agent/flyer_*.py` — there is no
    `agents.flyer` package on the import path. `build_closure_customer_text`
    must therefore prefer the flat alias (`from flyer_workflow import ...`)
    with the packaged path as a dev/test fallback. Without that pattern the
    function raises `ModuleNotFoundError` in production, gets swallowed by
    `notify_customer_of_closure`'s broad exception handler, and audits as an
    `unexpected: ...` send failure — exactly the silent-failure mode the
    PR #129 reactive safety net is supposed to backstop, NOT cover for.

    Strategy: copy the three files the helper touches into a tmp dir using
    the flat install names (`flyer_manual_queue.py`, `flyer_workflow.py`,
    `schemas.py` + `safe_io.py`), import `flyer_manual_queue` from that
    isolated path, and call the helper. If the deployed import shape breaks,
    this test fails before deploy.
    """
    import importlib
    import importlib.util
    import shutil
    import sys

    repo = Path(__file__).resolve().parent.parent
    src = repo / "src"
    # Mirror the deployed flat layout in a tmp install dir.
    install_dir = tmp_path / "shift-agent-install"
    install_dir.mkdir()
    shutil.copy(src / "agents" / "flyer" / "manual_queue.py", install_dir / "flyer_manual_queue.py")
    shutil.copy(src / "agents" / "flyer" / "workflow.py", install_dir / "flyer_workflow.py")
    # workflow.py imports LANGUAGE_NAMES, FIELD_LABELS — copy supporting modules
    # if they exist alongside, else trust schemas to be reachable via platform.
    for support in ("facts.py", "starter_briefs.py"):
        src_path = src / "agents" / "flyer" / support
        if src_path.exists():
            shutil.copy(src_path, install_dir / f"flyer_{support}")
    # platform schemas are deployed flat into /opt/shift-agent/schemas.py
    shutil.copy(src / "platform" / "schemas.py", install_dir / "schemas.py")

    # Isolate sys.path/sys.modules so the flat-layout import doesn't piggyback
    # on the already-loaded packaged versions.
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules[name] for name in list(sys.modules)
                     if name in {"flyer_manual_queue", "flyer_workflow",
                                 "agents", "agents.flyer", "agents.flyer.workflow",
                                 "agents.flyer.manual_queue", "schemas"}}
    for name in list(saved_modules):
        del sys.modules[name]
    sys.path.insert(0, str(install_dir))
    try:
        # Now import flat — this is what the VPS Python process sees.
        flat_mq = importlib.import_module("flyer_manual_queue")
        # Build a closed project in this isolated module's schema namespace.
        from schemas import FlyerProject as FP, FlyerManualReview as FMR, FlyerProjectStore as FPS  # type: ignore
        now = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)
        project = FP(
            project_id="F0058",
            status="closed_no_send",
            customer_phone="+19045550104",
            created_at=now,
            updated_at=now,
            original_message_id="m-58",
            raw_request="Authorized exact edit",
            manual_review=FMR(
                status="closed_no_send",
                reason="source_edit_provider_unavailable",
                reason_code="source_edit_provider_unavailable",
                queued_at=now,
                completed_at=now,
            ),
        )
        text = flat_mq.build_closure_customer_text(project)
        # Must resolve to the closure-aware reason line, not raise.
        assert "F0058" not in text
        assert "source-flyer edit" in text or "closed" in text.lower()
        # And the source must contain the flat-first import pattern so we
        # don't regress to a single packaged-only import.
        manual_queue_src = (install_dir / "flyer_manual_queue.py").read_text(encoding="utf-8")
        assert "from flyer_workflow import build_project_status_reply" in manual_queue_src, (
            "manual_queue.py must prefer the flat `flyer_workflow` alias to "
            "match the deployed /opt/shift-agent/ layout"
        )
        assert "from agents.flyer.workflow import build_project_status_reply" in manual_queue_src, (
            "manual_queue.py must keep the packaged fallback for dev/test"
        )
    finally:
        # Restore the packaged-layout sys.path/modules for subsequent tests.
        sys.path[:] = saved_path
        for name in list(sys.modules):
            if name in {"flyer_manual_queue", "flyer_workflow", "schemas"}:
                del sys.modules[name]
        sys.modules.update(saved_modules)


def test_close_script_invokes_notify_after_state_write_and_only_once():
    """The script's --close branch MUST call notify_customer_of_closure
    AFTER close_manual_project + atomic_write_text, and exactly once. This
    pins the no-double-send guarantee (a re-close on a closed row would
    raise inside close_manual_project, exiting the script BEFORE the
    notify call is reached) and the post-write-only ordering (we don't
    notify customers about closures we haven't actually persisted)."""
    source = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "flyer-manual-queue"
    text = source.read_text(encoding="utf-8")
    close_idx = text.find("if args.close:")
    close_call_idx = text.find("close_manual_project(", close_idx)
    atomic_idx = text.find("atomic_write_text(", close_idx)
    notify_idx = text.find("notify_customer_of_closure(", close_idx)
    assert close_idx >= 0 and close_call_idx >= 0 and atomic_idx >= 0 and notify_idx >= 0
    assert close_call_idx < atomic_idx < notify_idx, (
        "notify_customer_of_closure must follow close_manual_project + "
        "atomic_write_text — otherwise a ValueError from re-closing a "
        "closed row could still trigger a duplicate send, or we could "
        "notify the customer before the closure is persisted."
    )
    assert text.count("notify_customer_of_closure(") == 1, (
        "notify_customer_of_closure must be called exactly once in the script"
    )
