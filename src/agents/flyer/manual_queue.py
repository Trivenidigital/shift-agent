"""Manual review queue helpers for Flyer Studio."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import mimetypes
import os
import shutil

from schemas import (
    FlyerAsset,
    FlyerConcept,
    FlyerManualReview,
    FlyerManualReviewReason,
    FlyerProject,
    FlyerProjectStore,
    is_flyer_transition_allowed,
)


def make_manual_review(
    *,
    reason_code: FlyerManualReviewReason,
    detail: str = "",
    reason: str = "",
    queued_at: datetime | None = None,
) -> FlyerManualReview:
    """Build a FlyerManualReview for a new queued manual-review event.

    `reason_code` is the structured code; `reason` is optional human-readable text
    (defaults to the code). `detail` is operator/user-facing context.
    """
    now = queued_at or datetime.now(timezone.utc)
    return FlyerManualReview(
        status="queued",
        reason=(reason or reason_code)[:120],
        reason_code=reason_code,
        detail=detail[:500],
        queued_at=now,
    )


def list_manual_queue(store: FlyerProjectStore, *, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    for project in store.projects:
        manual = project.manual_review
        # break-glass terminates the operator workflow for this row even if
        # project.status stays at manual_edit_required (operator signalled
        # out-of-band resolution). Excluding here prevents the queue and the
        # build_summary counters from accumulating ghost stuck rows.
        if manual.status == "break_glass_sent":
            continue
        has_failed_qa = any(report.status != "passed" for report in project.qa_reports)
        if project.status != "manual_edit_required" and manual.status not in {"queued", "in_progress"} and not has_failed_qa:
            continue
        queued_at = manual.queued_at or project.updated_at
        age_hours = int((now - queued_at).total_seconds() // 3600)
        rows.append({
            "project_id": project.project_id,
            "customer_phone": str(project.customer_phone),
            "status": project.status,
            "manual_status": manual.status,
            "manual_reason": manual.reason,
            "manual_reason_code": manual.reason_code,
            "manual_detail": manual.detail,
            "age_hours": max(age_hours, 0),
            "asset_ids": [asset.asset_id for asset in project.assets],
            "locked_facts": [fact.model_dump(mode="json") for fact in project.locked_facts],
            "qa_blockers": [blocker for report in project.qa_reports for blocker in report.blockers],
        })
    return rows


def triage_summary(store: FlyerProjectStore, *, now: datetime | None = None) -> dict:
    """Triage-oriented view: groups by customer_phone, sorts by oldest age, with a reason histogram."""
    rows = list_manual_queue(store, now=now)
    groups: dict[str, list[dict]] = defaultdict(list)
    reason_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        groups[row["customer_phone"]].append(row)
        reason_counts[row["manual_reason_code"]] += 1
    ordered_groups: list[dict] = []
    for phone, items in groups.items():
        items.sort(key=lambda r: r["age_hours"], reverse=True)
        ordered_groups.append({
            "customer_phone": phone,
            "count": len(items),
            "oldest_age_hours": items[0]["age_hours"] if items else 0,
            "projects": items,
        })
    ordered_groups.sort(key=lambda g: g["oldest_age_hours"], reverse=True)
    return {
        "total": len(rows),
        "reason_counts": dict(sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "groups": ordered_groups,
    }


def classify_legacy_reason(project: FlyerProject) -> tuple[FlyerManualReviewReason, str]:
    """Heuristic classifier for legacy manual-review projects without a reason_code.

    Used by the backfill CLI. Returns (reason_code, detail).
    """
    has_failed_qa = any(report.status != "passed" for report in project.qa_reports)
    if has_failed_qa:
        blockers = [b for report in project.qa_reports for b in report.blockers]
        detail = "; ".join(blockers)[:500] if blockers else "legacy QA-failed project (no blockers recorded)"
        return "visual_qa_failed", detail
    for extraction in project.reference_extractions:
        if extraction.status in {"low_confidence", "provider_unavailable", "unsupported", "not_run"}:
            code: FlyerManualReviewReason = f"reference_{extraction.status}"  # type: ignore[assignment]
            return code, (extraction.detail or f"legacy reference extraction status={extraction.status}")[:500]
    raw = (project.raw_request or "").lower()
    if "edit uploaded flyer/source artwork" in raw or "authorized flyer/source artwork update" in raw:
        return "source_edit_provider_unavailable", "legacy source-edit project queued before reason was tracked"
    return "legacy_unknown", (project.raw_request or "")[:500]


def backfill_manual_reasons(
    store: FlyerProjectStore,
    *,
    apply: bool,
    now: datetime | None = None,
) -> dict:
    """Backfill manual_review.reason_code for legacy projects.

    Eligible: status=='manual_edit_required' (or QA-failed) AND reason_code=='unclassified'.
    Idempotent — re-applying finds zero candidates.
    """
    now = now or datetime.now(timezone.utc)
    candidates: list[dict] = []
    for idx, project in enumerate(store.projects):
        if project.manual_review.reason_code != "unclassified":
            continue
        has_failed_qa = any(report.status != "passed" for report in project.qa_reports)
        if project.status != "manual_edit_required" and not has_failed_qa:
            continue
        reason_code, detail = classify_legacy_reason(project)
        queued_at = project.manual_review.queued_at or project.updated_at or now
        candidate = {
            "project_id": project.project_id,
            "customer_phone": str(project.customer_phone),
            "current_status": project.status,
            "current_reason_code": project.manual_review.reason_code,
            "proposed_reason_code": reason_code,
            "proposed_reason": reason_code,
            "proposed_detail": detail,
            "proposed_queued_at": queued_at.isoformat(),
        }
        candidates.append(candidate)
        if apply:
            current_manual_status = project.manual_review.status
            new_manual = project.manual_review.model_copy(update={
                "status": current_manual_status if current_manual_status in {"queued", "in_progress"} else "queued",
                "reason": reason_code,
                "reason_code": reason_code,
                "detail": detail,
                "queued_at": queued_at,
            })
            store.projects[idx] = project.model_copy(update={
                "manual_review": new_manual,
                "updated_at": now,
            })
    return {
        "applied": apply,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def _next_asset_id(project) -> str:
    max_id = 0
    for asset in project.assets:
        try:
            max_id = max(max_id, int(asset.asset_id[1:]))
        except Exception:
            continue
    return f"A{max_id + 1:04d}"


def complete_manual_project(
    store: FlyerProjectStore,
    project_id: str,
    approved_asset_path: Path | str,
    *,
    reason: str,
) -> FlyerProjectStore:
    source = Path(approved_asset_path)
    now = datetime.now(timezone.utc)
    for idx, project in enumerate(store.projects):
        if project.project_id != project_id:
            continue
        manual = project.manual_review
        if project.status != "manual_edit_required" or manual.status not in {"queued", "in_progress"}:
            raise ValueError(f"project not queued for manual completion: {project_id}")
        if not is_flyer_transition_allowed(project.status, "awaiting_final_approval"):
            raise ValueError(f"invalid transition {project.status}->awaiting_final_approval")
        if not source.exists() or not source.is_file():
            raise ValueError(f"approved asset not found: {source}")
        asset_id = _next_asset_id(project)
        root = Path(os.environ.get("FLYER_STATE_ROOT", "/opt/shift-agent/state/flyer")).resolve()
        dest_dir = root / "manual" / project_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{project_id}-{asset_id}{source.suffix or '.png'}"
        if source.resolve() != dest.resolve():
            shutil.copy2(source, dest)
        data = dest.read_bytes()
        asset = FlyerAsset(
            asset_id=asset_id,
            kind="concept_preview",
            source="uploaded",
            path=str(dest),
            mime_type=mimetypes.guess_type(str(dest))[0] or "image/png",
            sha256=hashlib.sha256(data).hexdigest(),
            original_message_id=project.original_message_id,
            received_at=now,
        )
        concept = FlyerConcept(
            concept_id="C1",
            title="Designer Approved",
            style_summary="Operator-approved manual review asset",
            preview_asset_id=asset.asset_id,
            prompt=project.raw_request,
            created_at=now,
        )
        manual = project.manual_review.model_copy(update={
            "status": "completed",
            "detail": reason,
            "completed_at": now,
            "operator_asset_ids": [asset.asset_id],
        })
        store.projects[idx] = project.model_copy(update={
            "status": "awaiting_final_approval",
            "assets": [*project.assets, asset],
            "concepts": [concept],
            "selected_concept_id": "C1",
            "manual_review": manual,
            "updated_at": now,
        })
        return FlyerProjectStore.model_validate(store.model_dump())
    raise ValueError(f"project not found: {project_id}")
