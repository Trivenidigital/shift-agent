from __future__ import annotations

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


def test_list_manual_queue_includes_reason_code():
    from agents.flyer.manual_queue import list_manual_queue

    rows = list_manual_queue(FlyerProjectStore(projects=[_manual_project()]), now=datetime(2026, 5, 20, tzinfo=timezone.utc))
    assert rows[0]["manual_reason_code"] == "source_edit_provider_unavailable"


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
    assert "F0058" in text
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
