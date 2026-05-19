"""Manual review queue helpers for Flyer Studio."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import mimetypes
import shutil

from schemas import FlyerAsset, FlyerConcept, FlyerManualReview, FlyerProjectStore


def list_manual_queue(store: FlyerProjectStore, *, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    for project in store.projects:
        manual = project.manual_review
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
            "manual_detail": manual.detail,
            "age_hours": max(age_hours, 0),
            "asset_ids": [asset.asset_id for asset in project.assets],
            "locked_facts": [fact.model_dump(mode="json") for fact in project.locked_facts],
            "qa_blockers": [blocker for report in project.qa_reports for blocker in report.blockers],
        })
    return rows


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
        asset_id = _next_asset_id(project)
        dest = source
        if source.exists():
            dest = source.with_name(f"{project_id}-{asset_id}{source.suffix or '.png'}")
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
