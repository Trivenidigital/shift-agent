"""Flyer Studio operator dashboard APIs."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..audit import log as audit_log
from ..auth import require_auth, require_fresh_otp
from ..config import get_settings
from ..deps import client_ip, client_ua
from ..shell import run_cli

_AGENT_ROOT = Path("/opt/shift-agent")
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import safe_io  # noqa: E402
from schemas import (  # noqa: E402
    E164Phone,
    FlyerConfig,
    FlyerCustomerProfile,
    FlyerCustomerStore,
    FlyerGuestOrderStore,
    FlyerProjectStore,
    FlyerUsageEvent,
)
try:
    from flyer_manual_queue import (  # type: ignore  # noqa: E402
        _verification_modes as flyer_verification_modes,
        build_closure_customer_text,
        close_manual_project,
        complete_manual_project,
        enforce_close_freshness_guard,
        list_manual_queue,
        notify_customer_of_closure,
        resolve_proactive_chat_id_for_project,
        triage_summary,
    )
except ImportError:
    from agents.flyer.manual_queue import (  # noqa: E402
        _verification_modes as flyer_verification_modes,
        build_closure_customer_text,
        close_manual_project,
        complete_manual_project,
        enforce_close_freshness_guard,
        list_manual_queue,
        notify_customer_of_closure,
        resolve_proactive_chat_id_for_project,
        triage_summary,
    )

router = APIRouter(prefix="/flyer", tags=["flyer"])

_FORMULA_PREFIXES: frozenset[str] = frozenset({"=", "+", "-", "@", "\t"})
_CAMPAIGN_CSV_MAX_BYTES = 512_000
_CAMPAIGN_SEND_BIN = Path("/usr/local/bin/send-flyer-campaign")


class ReasonBody(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


class ExtendTrialBody(ReasonBody):
    extra_flyers: int = Field(ge=1, le=100)


class CampaignSendBody(ReasonBody):
    targets_text: str = Field(min_length=3, max_length=20_000)
    dry_run: bool = True
    include_paid: bool = False


class ManualQueueCompleteBody(ReasonBody):
    operator_asset_path: str = Field(min_length=1, max_length=500)


class ManualQueueBreakGlassBody(ReasonBody):
    pass


class ManualQueueCloseNoSendBody(ReasonBody):
    """Operator-close request body. `force=True` bypasses the freshness
    guard; the agent helper `enforce_close_freshness_guard` accepts force
    OR a documented bypass token in the reason string."""
    force: bool = False


def _flyer_dir() -> Path:
    return get_settings().state_dir / "flyer"


def _customers_path() -> Path:
    return _flyer_dir() / "customers.json"


def _projects_path() -> Path:
    return _flyer_dir() / "projects.json"


def _guest_orders_path() -> Path:
    return _flyer_dir() / "guest_orders.json"


def _marketing_flyer_path() -> Path:
    return _flyer_dir() / "marketing" / "Flyer.png"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_load(path: Path, model_cls, default):
    model, _status = safe_io.load_model(path, model_cls, default=default)
    return model


def load_customer_store() -> FlyerCustomerStore:
    return _safe_load(_customers_path(), FlyerCustomerStore, FlyerCustomerStore())


def load_project_store() -> FlyerProjectStore:
    return _safe_load(_projects_path(), FlyerProjectStore, FlyerProjectStore())


def load_guest_order_store() -> FlyerGuestOrderStore:
    return _safe_load(_guest_orders_path(), FlyerGuestOrderStore, FlyerGuestOrderStore())


def _backup_path(path: Path, reason: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return ""
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    clean = "".join(ch if ch.isalnum() else "-" for ch in reason.lower())[:40].strip("-") or "operator"
    backup = path.with_name(f"{path.name}.pre-admin-{stamp}-{clean}")
    shutil.copy2(path, backup)
    return str(backup)


def _dump_store(path: Path, store: BaseModel) -> None:
    if os.name == "nt":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
        return
    safe_io.dump_model(path, store)


def _tier_for(customer: FlyerCustomerProfile) -> dict[str, Any]:
    cfg = FlyerConfig()
    for tier in cfg.plan_tiers:
        if tier.plan_id == customer.plan_id:
            return tier.model_dump(mode="json")
    return {"plan_id": customer.plan_id, "label": customer.plan_id, "included_flyers": None}


def _customer_category(customer: FlyerCustomerProfile) -> Literal["free_trial", "paid", "payment_pending", "inactive"]:
    if customer.status == "payment_pending":
        return "payment_pending"
    if customer.status in {"suspended", "cancelled"}:
        return "inactive"
    if customer.plan_id == "trial" or customer.status == "trial":
        return "free_trial"
    return "paid"


def _customer_row(customer: FlyerCustomerProfile, project_count: int = 0) -> dict[str, Any]:
    cfg = FlyerConfig()
    used = customer.usage_count_for_current_period()
    remaining = customer.quota_remaining(cfg.plan_tiers)
    return {
        "customer_id": customer.customer_id,
        "business_name": customer.business_name,
        "business_address": customer.business_address,
        "category": _customer_category(customer),
        "status": customer.status,
        "plan_id": customer.plan_id,
        "plan": _tier_for(customer),
        "preferred_language": customer.preferred_language,
        "public_phone": str(customer.public_phone),
        "business_whatsapp_number": str(customer.business_whatsapp_number),
        "authorized_request_numbers": [str(phone) for phone in customer.authorized_request_numbers],
        "usage_used": used,
        "usage_remaining": remaining,
        "trial_bonus_flyers": customer.trial_bonus_flyers,
        "project_count": project_count,
        "updated_at": customer.updated_at.isoformat(),
    }


def build_summary() -> dict[str, Any]:
    customers = load_customer_store()
    projects = load_project_store()
    guests = load_guest_order_store()
    segments = {
        "free_trial": 0,
        "paid": 0,
        "payment_pending": 0,
        "inactive": 0,
        "one_time": len(guests.orders),
    }
    for customer in customers.customers:
        segments[_customer_category(customer)] += 1
    now = _now()
    active_statuses = {
        "intake_started",
        "collecting_required_info",
        "awaiting_assets",
        "manual_edit_required",
        "generating_concepts",
        "awaiting_final_approval",
        "revising_design",
        "finalizing_assets",
    }
    stuck_statuses = {"intake_started", "collecting_required_info", "awaiting_assets"}
    # break-glass rows stay at status=manual_edit_required by design (audit
    # signal for "operator resolved out-of-band"), but for the operator
    # dashboard counters they're terminal — don't ghost in manual_edit_count
    # or stuck_edit_count forever.
    manual_edit_count = sum(
        1
        for p in projects.projects
        if p.status == "manual_edit_required" and p.manual_review.status != "break_glass_sent"
    )
    stuck_edit_count = 0
    for project in projects.projects:
        if project.manual_review.status == "break_glass_sent":
            continue
        age_minutes = max(0, int((now - project.updated_at).total_seconds() // 60))
        if project.status == "manual_edit_required" and age_minutes >= 30:
            stuck_edit_count += 1
        elif project.status == "revising_design" and not project.concepts and age_minutes >= 10:
            stuck_edit_count += 1
    return {
        "segments": segments,
        "total_customers": len(customers.customers),
        "active_projects": sum(1 for p in projects.projects if p.status in active_statuses),
        "stuck_projects": sum(1 for p in projects.projects if p.status in stuck_statuses),
        "manual_edit_count": manual_edit_count,
        "stuck_edit_count": stuck_edit_count,
        "guest_orders": len(guests.orders),
        "campaign_asset": {
            "path": str(_marketing_flyer_path()),
            "exists": _marketing_flyer_path().exists(),
        },
    }


def _find_customer_or_404(store: FlyerCustomerStore, customer_id: str) -> FlyerCustomerProfile:
    customer = store.find_customer_by_id(customer_id)
    if customer is None:
        raise HTTPException(404, f"customer {customer_id} not found")
    return customer


def extend_trial_quota(customer_id: str, *, extra_flyers: int, reason: str) -> dict[str, Any]:
    path = _customers_path()
    with safe_io.flock(path):
        store = load_customer_store()
        customer = _find_customer_or_404(store, customer_id)
        backup = _backup_path(path, reason)
        customer.trial_bonus_flyers += extra_flyers
        customer.updated_at = _now()
        FlyerCustomerStore.model_validate(store.model_dump())
        _dump_store(path, store)
    return {
        "ok": True,
        "customer_id": customer_id,
        "trial_bonus_flyers": customer.trial_bonus_flyers,
        "backup": backup,
    }


def reset_trial_quota(customer_id: str, *, reason: str) -> dict[str, Any]:
    path = _customers_path()
    released = 0
    with safe_io.flock(path):
        store = load_customer_store()
        customer = _find_customer_or_404(store, customer_id)
        backup = _backup_path(path, reason)
        latest: dict[str, FlyerUsageEvent] = {}
        for event in customer.usage_events:
            previous = latest.get(event.reservation_id)
            if previous is None or event.recorded_at >= previous.recorded_at:
                latest[event.reservation_id] = event
        now = _now()
        for event in latest.values():
            if event.kind not in {"reserved", "used"}:
                continue
            customer.usage_events.append(
                FlyerUsageEvent(
                    reservation_id=event.reservation_id,
                    project_id=event.project_id,
                    customer_id=customer.customer_id,
                    kind="released",
                    count=1,
                    recorded_at=now,
                    message_id=f"cockpit-reset-{now.strftime('%Y%m%dT%H%M%S')}-{released + 1}",
                )
            )
            released += 1
        customer.updated_at = now
        FlyerCustomerStore.model_validate(store.model_dump())
        _dump_store(path, store)
    return {"ok": True, "customer_id": customer_id, "released": released, "backup": backup}


def deactivate_customer(customer_id: str, *, reason: str) -> dict[str, Any]:
    """Soft-remove a Flyer customer from active use without deleting history.

    Historical projects, audit rows, media assets, and usage records remain
    untouched. `cancelled` is the existing inactive lifecycle state used by
    the Flyer creation paths to block future work.
    """
    path = _customers_path()
    with safe_io.flock(path):
        store = load_customer_store()
        customer = _find_customer_or_404(store, customer_id)
        backup = _backup_path(path, reason)
        previous_status = customer.status
        already_inactive = previous_status == "cancelled"
        now = _now()
        if not already_inactive:
            existing_notes = customer.notes.strip()
            note = f"Deactivated by Cockpit: {reason}"
            notes = f"{existing_notes}\n{note}".strip() if existing_notes else note
            customer.status = "cancelled"
            customer.notes = notes[-1000:]
            customer.updated_at = now
        FlyerCustomerStore.model_validate(store.model_dump())
        _dump_store(path, store)
    return {
        "ok": True,
        "customer_id": customer_id,
        "previous_status": previous_status,
        "status": "cancelled",
        "already_inactive": already_inactive,
        "backup": backup,
    }


def _formula_guard(value: str, *, row: int = 1, col: str = "phone") -> None:
    stripped = value.lstrip()
    if not stripped:
        return
    if stripped[:1] in _FORMULA_PREFIXES:
        if stripped.startswith("+") and all(c.isdigit() or c in "- ()" for c in stripped[1:]):
            return
        raise ValueError(f"row {row} col {col!r}: formula-injection prefix rejected")
    if "\r" in value or "\n" in value:
        raise ValueError(f"row {row} col {col!r}: CR/LF in cell rejected")


def _normalize_phone(value: str) -> str:
    return E164Phone.from_any(value.strip(), country_code="US")


def parse_campaign_targets(text: str) -> dict[str, Any]:
    valid: list[str] = []
    invalid: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicate_count = 0
    for row_num, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        value = line.split(",", 1)[0].strip()
        try:
            _formula_guard(value, row=row_num)
            phone = _normalize_phone(value)
        except Exception as exc:
            invalid.append({"row": row_num, "value": value, "error": str(exc)})
            continue
        if phone in seen:
            duplicate_count += 1
            continue
        seen.add(phone)
        valid.append(phone)
    if invalid and not valid:
        raise ValueError(invalid[0]["error"])
    return {"valid_targets": valid, "invalid": invalid, "duplicate_count": duplicate_count}


async def parse_campaign_csv(file: UploadFile) -> dict[str, Any]:
    raw_bytes = await file.read()
    if len(raw_bytes) > _CAMPAIGN_CSV_MAX_BYTES:
        raise HTTPException(413, f"CSV exceeds {_CAMPAIGN_CSV_MAX_BYTES // 1024} KB limit")
    try:
        raw = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "File must be UTF-8 CSV")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or "phone" not in reader.fieldnames:
        raise HTTPException(422, "CSV missing required header 'phone'")
    lines = []
    for row_num, row in enumerate(reader, start=2):
        for col, val in row.items():
            if val is not None:
                try:
                    _formula_guard(str(val), row=row_num, col=col or "")
                except ValueError as exc:
                    raise HTTPException(422, str(exc))
        lines.append(str(row.get("phone") or ""))
    try:
        return parse_campaign_targets("\n".join(lines))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _send_campaign_target(target: str, *, dry_run: bool) -> dict[str, Any]:
    jid = target.removeprefix("+") + "@s.whatsapp.net"
    args = ["--jid", jid]
    if dry_run:
        args.append("--dry-run")
    result = run_cli(str(_CAMPAIGN_SEND_BIN).replace("\\", "/"), args, timeout=60)
    output = result.stdout.strip() or result.stderr.strip()
    try:
        payload = json.loads(output) if output else {}
    except json.JSONDecodeError:
        payload = {"output": output[:500]}
    return {"ok": result.returncode == 0, "target": target, "returncode": result.returncode, **payload}


def send_campaign_to_targets(targets: list[str], *, dry_run: bool, reason: str) -> dict[str, Any]:
    del reason
    normalized = []
    seen: set[str] = set()
    for target in targets:
        phone = _normalize_phone(target)
        if phone not in seen:
            seen.add(phone)
            normalized.append(phone)
    if dry_run:
        return {"ok": True, "dry_run": True, "sent": 0, "failed": 0, "targets": normalized, "results": []}
    results = [_send_campaign_target(target, dry_run=False) for target in normalized]
    return {
        "ok": all(result.get("ok") for result in results),
        "dry_run": False,
        "sent": sum(1 for result in results if result.get("ok")),
        "failed": sum(1 for result in results if not result.get("ok")),
        "targets": normalized,
        "results": results,
    }


@router.get("/summary")
async def summary(_=Depends(require_auth)):
    return build_summary()


@router.get("/customers")
async def customers(
    query: str = "",
    segment: str = "",
    offset: int = 0,
    limit: int = 300,
    _=Depends(require_auth),
):
    # BUG-FLYER-QA-002 (review follow-up): pagination via offset+limit so
    # rows beyond the first page are reachable. limit is capped at 300 to
    # match /projects and /guest-orders; offset/limit are clamped to
    # non-negative to avoid Python list-slice weirdness.
    offset = max(0, offset)
    limit = max(1, min(300, limit))
    store = load_customer_store()
    projects = load_project_store()
    project_counts: dict[str, int] = {}
    by_phone = {phone: c.customer_id for c in store.customers for phone in c.routable_phones()}
    for project in projects.projects:
        cid = by_phone.get(str(project.customer_phone), "")
        if cid:
            project_counts[cid] = project_counts.get(cid, 0) + 1
    q = query.strip().lower()
    rows = []
    for customer in store.customers:
        row = _customer_row(customer, project_counts.get(customer.customer_id, 0))
        haystack = " ".join(
            [
                row["customer_id"],
                row["business_name"],
                row["public_phone"],
                row["business_whatsapp_number"],
                " ".join(row["authorized_request_numbers"]),
            ]
        ).lower()
        if q and q not in haystack:
            continue
        if segment and row["category"] != segment:
            continue
        rows.append(row)
    # BUG-FLYER-QA-002: cap + sort to match /projects and /guest-orders,
    # plus offset/limit pagination so rows beyond the first page stay
    # reachable. Surface `total` + `truncated` + `offset` + `limit` so the
    # dashboard can show "showing X-Y of N" and navigate forward.
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    total = len(rows)
    page = rows[offset:offset + limit]
    return {
        "customers": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": total > len(page) + offset,
    }


@router.get("/customers/{customer_id}")
async def customer_detail(customer_id: str, _=Depends(require_auth)):
    store = load_customer_store()
    customer = _find_customer_or_404(store, customer_id)
    projects = [
        p.model_dump(mode="json")
        for p in load_project_store().projects
        if str(p.customer_phone) in customer.routable_phones()
    ]
    return {"customer": _customer_row(customer, len(projects)), "projects": projects, "raw": customer.model_dump(mode="json")}


@router.post("/customers/{customer_id}/extend-trial")
async def extend_trial(customer_id: str, body: ExtendTrialBody, request: Request, _=Depends(require_fresh_otp)):
    result = extend_trial_quota(customer_id, extra_flyers=body.extra_flyers, reason=body.reason)
    audit_log("flyer.customer.extend_trial", ip=client_ip(request), ua=client_ua(request), details=result | {"reason": body.reason})
    return result


@router.post("/customers/{customer_id}/reset-trial")
async def reset_trial(customer_id: str, body: ReasonBody, request: Request, _=Depends(require_fresh_otp)):
    result = reset_trial_quota(customer_id, reason=body.reason)
    audit_log("flyer.customer.reset_trial", ip=client_ip(request), ua=client_ua(request), details=result | {"reason": body.reason})
    return result


@router.post("/customers/{customer_id}/deactivate")
async def customer_deactivate(customer_id: str, body: ReasonBody, request: Request, _=Depends(require_fresh_otp)):
    result = deactivate_customer(customer_id, reason=body.reason)
    audit_log("flyer.customer.deactivate", ip=client_ip(request), ua=client_ua(request), details=result | {"reason": body.reason})
    return result


@router.get("/projects")
async def projects(status: str = "", query: str = "", _=Depends(require_auth)):
    q = query.strip().lower()
    rows = []
    now = _now()
    for project in load_project_store().projects:
        if status and project.status != status:
            continue
        haystack = f"{project.project_id} {project.customer_phone} {project.raw_request}".lower()
        if q and q not in haystack:
            continue
        row = project.model_dump(mode="json")
        age_minutes = max(0, int((now - project.updated_at).total_seconds() // 60))
        attention: list[str] = []
        if project.status == "manual_edit_required":
            attention.append("manual_edit_queue")
            if age_minutes >= 30:
                attention.append("manual_edit_stale")
        if project.status == "revising_design" and not project.concepts and age_minutes >= 10:
            attention.append("regeneration_stale")
        row["age_minutes"] = age_minutes
        row["attention"] = attention
        rows.append(row)
    rows.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return {"projects": rows[:300]}


@router.get("/guest-orders")
async def guest_orders(_=Depends(require_auth)):
    orders = [order.model_dump(mode="json") for order in load_guest_order_store().orders]
    orders.sort(key=lambda o: o.get("updated_at", ""), reverse=True)
    return {"orders": orders[:300]}


@router.post("/campaigns/preview")
async def campaign_preview(body: CampaignSendBody, _=Depends(require_auth)):
    try:
        parsed = parse_campaign_targets(body.targets_text)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return parsed


@router.post("/campaigns/preview-csv")
async def campaign_preview_csv(file: UploadFile = File(...), _=Depends(require_auth)):
    return await parse_campaign_csv(file)


@router.post("/campaigns/send")
async def campaign_send(body: CampaignSendBody, request: Request, _=Depends(require_fresh_otp)):
    try:
        parsed = parse_campaign_targets(body.targets_text)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    result = send_campaign_to_targets(parsed["valid_targets"], dry_run=body.dry_run, reason=body.reason)
    audit_log(
        "flyer.campaign.send",
        ip=client_ip(request),
        ua=client_ua(request),
        details={
            "reason": body.reason,
            "dry_run": body.dry_run,
            "target_count": len(parsed["valid_targets"]),
            "sent": result["sent"],
            "failed": result["failed"],
        },
    )
    return result | {"invalid": parsed["invalid"], "duplicate_count": parsed["duplicate_count"]}


def manual_queue_triage_action() -> dict[str, Any]:
    """Read-only triage view for the Flyer manual-review queue."""
    return triage_summary(load_project_store())


def _operator_upload_root() -> Path:
    return _flyer_dir() / "operator-uploads"


_OPERATOR_ASSET_MIME_ALLOWLIST: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/pdf",
})


def manual_queue_complete_action(project_id: str, *, asset_path: str, reason: str) -> dict[str, Any]:
    """Operator-completes a queued manual-review project by attaching an approved asset.

    Wraps `complete_manual_project` from the agent helper with the cockpit
    backup + atomic-write contract that the rest of this router uses for
    operator mutations.
    """
    source = Path(asset_path)
    if not source.is_absolute():
        raise HTTPException(422, "operator_asset_path must be absolute")
    if not source.exists() or not source.is_file():
        raise HTTPException(404, f"operator asset not found: {asset_path}")
    # Constrain to the operator-uploads root so a fresh-OTP'd operator can't
    # accidentally publish /opt/shift-agent/.env, /etc/passwd, or other
    # process-readable files as flyer artwork. Operator places the file
    # under state/flyer/operator-uploads/ via SCP/SFTP first.
    upload_root = _operator_upload_root().resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    try:
        source.resolve().relative_to(upload_root)
    except ValueError:
        raise HTTPException(
            422,
            f"operator_asset_path must be under {upload_root} (got {source})",
        )
    import mimetypes as _mimetypes
    mime, _ = _mimetypes.guess_type(str(source))
    if mime not in _OPERATOR_ASSET_MIME_ALLOWLIST:
        raise HTTPException(
            415,
            f"operator_asset_path mime {mime!r} not in allowlist {sorted(_OPERATOR_ASSET_MIME_ALLOWLIST)}",
        )
    path = _projects_path()
    with safe_io.flock(path):
        backup = _backup_path(path, reason)
        store = load_project_store()
        try:
            updated = complete_manual_project(store, project_id, source, reason=reason)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        _dump_store(path, updated)
        completed = next(p for p in updated.projects if p.project_id == project_id)
    return {
        "ok": True,
        "project_id": project_id,
        "status": completed.status,
        "manual_status": completed.manual_review.status,
        "operator_asset_ids": list(completed.manual_review.operator_asset_ids),
        "backup": backup,
    }


def manual_queue_break_glass_action(project_id: str, *, reason: str) -> dict[str, Any]:
    """Mark a queued project as operator-handled out-of-band (no QA).

    Does NOT change project.status — the operator is signalling "I have
    resolved this customer's request outside automation; record the audit
    trail." Subsequent triage will surface this row as
    `manual_status: break_glass_sent` so it's distinct from completed.
    """
    path = _projects_path()
    with safe_io.flock(path):
        backup = _backup_path(path, reason)
        store = load_project_store()
        target = next((p for p in store.projects if p.project_id == project_id), None)
        if target is None:
            raise HTTPException(404, f"project {project_id} not found")
        if target.status != "manual_edit_required" or target.manual_review.status not in {"queued", "in_progress"}:
            raise HTTPException(409, f"project not queued for break-glass: {project_id}")
        now = _now()
        new_manual = target.manual_review.model_copy(update={
            "status": "break_glass_sent",
            "break_glass_reason": reason[:500],
            "completed_at": now,
        })
        for idx, project in enumerate(store.projects):
            if project.project_id == project_id:
                store.projects[idx] = project.model_copy(update={
                    "manual_review": new_manual,
                    "updated_at": now,
                })
                break
        store = FlyerProjectStore.model_validate(store.model_dump())
        _dump_store(path, store)
    return {
        "ok": True,
        "project_id": project_id,
        "manual_status": "break_glass_sent",
        "backup": backup,
    }


@router.get("/manual-queue")
async def manual_queue(_=Depends(require_auth)):
    return manual_queue_triage_action()


@router.post("/manual-queue/{project_id}/complete")
async def manual_queue_complete(
    project_id: str,
    body: ManualQueueCompleteBody,
    request: Request,
    _=Depends(require_fresh_otp),
):
    result = manual_queue_complete_action(project_id, asset_path=body.operator_asset_path, reason=body.reason)
    audit_log(
        "flyer.manual_queue.complete",
        ip=client_ip(request),
        ua=client_ua(request),
        details=result | {"reason": body.reason},
    )
    return result


@router.post("/manual-queue/{project_id}/break-glass")
async def manual_queue_break_glass(
    project_id: str,
    body: ManualQueueBreakGlassBody,
    request: Request,
    _=Depends(require_fresh_otp),
):
    result = manual_queue_break_glass_action(project_id, reason=body.reason)
    audit_log(
        "flyer.manual_queue.break_glass",
        ip=client_ip(request),
        ua=client_ua(request),
        details=result | {"reason": body.reason},
    )
    return result


# ─────────────────────────────────────────────────────────────────
# P0-6 close/no-send cockpit action + P0-5 customer-message preview.
#
# Close path mirrors `flyer-manual-queue --close` CLI semantics:
#   under flock: enforce_close_freshness_guard → close_manual_project
#                → backup + atomic state write
#   then OUTSIDE flock: notify_customer_of_closure (bridge + audit).
# Notify runs after the lock is released so a slow bridge call cannot
# block unrelated project writers — closure state is already
# persisted, and the reactive "any update?" path is the safety net
# when the proactive push misses. Reuses the agent helpers from
# PRs #127/#129/#130 so the cockpit binding is the only net-new
# surface.
# ─────────────────────────────────────────────────────────────────


def _decisions_log_path() -> Path:
    """Resolve the agent decisions log path.

    `notify_customer_of_closure` writes a `flyer_closure_customer_notified`
    row to the agent decisions log. Use the same `Settings.decisions_path`
    other cockpit routes rely on so test mode (Settings.state_dir under
    tmp_path) and production (/opt/shift-agent/logs/decisions.log) both
    resolve correctly.
    """
    return get_settings().decisions_path


def manual_queue_close_no_send_action(
    project_id: str, *, reason: str, force: bool,
) -> dict[str, Any]:
    """Close a queued manual-review row without sending customer assets.

    Symmetric with `manual_queue_complete_action` / `manual_queue_break_glass_action`:
    cockpit backup + atomic write + agent helpers. Best-effort proactive
    notification runs AFTER state is persisted; its result is returned in
    the response and audited but does NOT roll back the closure (reactive
    "any update?" path is the safety net).
    """
    path = _projects_path()
    backup = ""
    closed_status = ""
    closed_manual_status = ""
    notification: dict[str, Any] = {}
    # Hold the flock ONLY for the state mutation. The proactive bridge call
    # below (notify_customer_of_closure → WhatsApp send + audit append) can
    # take seconds; holding flock through it would block unrelated project
    # writes. Closure state is already persisted before the bridge call, so
    # a failure or slow send doesn't roll anything back — the reactive
    # "any update?" safety net carries when the proactive push misses.
    with safe_io.flock(path):
        backup = _backup_path(path, reason)
        store = load_project_store()
        # Freshness guard. Raises ValueError on a fresh row without --force
        # AND without a documented bypass token in the reason string.
        try:
            enforce_close_freshness_guard(
                store, project_id,
                reason=reason,
                force=force,
                now=_now(),
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        # Transition manual_edit_required → closed_no_send. Raises ValueError
        # if the row isn't currently in a closable state.
        try:
            updated = close_manual_project(store, project_id, reason=reason)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        _dump_store(path, updated)
        closed = next(p for p in updated.projects if p.project_id == project_id)
        closed_status = closed.status
        closed_manual_status = closed.manual_review.status
    # Notify the customer proactively. Best-effort; never raises.
    # OUTSIDE the flock — bridge calls must not block other state writers.
    notification = notify_customer_of_closure(
        updated, project_id,
        customers_path=_customers_path(),
        decisions_log_path=_decisions_log_path(),
    )
    return {
        "ok": True,
        "project_id": project_id,
        "status": closed_status,
        "manual_status": closed_manual_status,
        "backup": backup,
        "notification": {
            "send_ok": bool(notification.get("send_ok", False)),
            "chat_id": notification.get("chat_id", ""),
            "outbound_message_id": notification.get("outbound_message_id", ""),
            "error": notification.get("error", ""),
        },
    }


@router.post("/manual-queue/{project_id}/close-no-send")
async def manual_queue_close_no_send(
    project_id: str,
    body: ManualQueueCloseNoSendBody,
    request: Request,
    _=Depends(require_fresh_otp),
):
    result = manual_queue_close_no_send_action(
        project_id, reason=body.reason, force=body.force,
    )
    audit_log(
        "flyer.manual_queue.close_no_send",
        ip=client_ip(request),
        ua=client_ua(request),
        details=result | {"reason": body.reason, "force": body.force},
    )
    return result


# ── P0-5: action preview ──────────────────────────────────────────


_ALLOWED_PREVIEW_ACTIONS = frozenset({"close_no_send", "complete", "break_glass"})


def manual_queue_action_preview(project_id: str, *, action: str) -> dict[str, Any]:
    """Customer-visible preview for a mutating cockpit action.

    Reuses the agent's deterministic copy sources so the cockpit preview
    is the SAME text the customer will actually receive:
      - close_no_send → `build_closure_customer_text` on a simulated
        post-close project (so it picks up `CLOSED_NO_SEND_REASON_LINES`).
      - complete → the next concept-preview caption pattern emitted by
        `send_flyer_concept_previews`. Operator-completed assets land as
        concept C1 with title `Designer Approved` / style summary
        `Operator-approved manual review asset`.
      - break_glass → no customer push. Explicitly flagged so the cockpit
        can render "No customer message will be sent" instead of a copy
        preview the operator might confuse for one.
    """
    if action not in _ALLOWED_PREVIEW_ACTIONS:
        raise HTTPException(422, f"unknown action {action!r}; expected one of {sorted(_ALLOWED_PREVIEW_ACTIONS)}")
    store = load_project_store()
    project = next((p for p in store.projects if p.project_id == project_id), None)
    if project is None:
        raise HTTPException(404, f"project {project_id} not found")

    # Use the same audit-derived resolver `notify_customer_of_closure` will
    # use at send time so the cockpit preview shows the chat_id the
    # customer will actually receive on (P0-5 single-source-of-truth +
    # PR #133 review fix for the LID/authorized-requester misroute).
    chat_id, chat_id_source = resolve_proactive_chat_id_for_project(
        project,
        customers_path=_customers_path(),
        decisions_log_path=_decisions_log_path(),
    )

    if action == "close_no_send":
        # Simulate the post-close state so the canonical copy table fires.
        # We don't write — just project the state forward to read out the
        # reason-aware closure copy.
        simulated_manual = project.manual_review.model_copy(update={"status": "closed_no_send"})
        simulated = project.model_copy(update={
            "status": "closed_no_send",
            "manual_review": simulated_manual,
        })
        customer_text = build_closure_customer_text(simulated)
        return {
            "action": "close_no_send",
            "project_id": project_id,
            "will_notify": bool(chat_id),
            "customer_text": customer_text,
            "customer_messages": [customer_text],
            "would_notify_chat_id": chat_id,
            "chat_id_source": chat_id_source,
            "note": (
                "Customer will receive this message on close. "
                "If no chat_id is on file, the safety net 'any update?' "
                "reactive reply still surfaces the closure."
            ),
            "reason_code": str(project.manual_review.reason_code or ""),
        }

    if action == "complete":
        # `send_flyer_concept_previews` emits TWO customer-visible messages
        # on the next preview send: (1) media caption attached to the
        # concept image, (2) a follow-up text. Both are previewed so the
        # operator sees the full customer-visible sequence (P0-5 + PR #133
        # review HIGH-3 fix — caption alone was an incomplete preview).
        # `complete_manual_project` attaches the uploaded asset as concept
        # C1 with these exact literals; the caption helper in
        # `cf-router/actions.py::send_flyer_concept_previews` concatenates
        # concept_id, title, style_summary, then a separate bridge_post
        # ships the follow-up.
        caption = (
            "C1: Designer Approved\n"
            "Operator-approved manual review asset\n"
            "\n"
            "Reply APPROVE or reply with changes."
        )
        followup = "Reply APPROVE to receive final files, or reply with changes."
        messages = [caption, followup]
        return {
            "action": "complete",
            "project_id": project_id,
            "will_notify": False,
            # `customer_text` retained for back-compat with any earlier
            # consumer; joined with an explicit separator so a client
            # reading only the legacy field still sees both messages.
            "customer_text": "\n\n--- followed by ---\n\n".join(messages),
            "customer_messages": messages,
            "would_notify_chat_id": chat_id,
            "chat_id_source": chat_id_source,
            "note": (
                "Complete transitions the project to awaiting_final_approval. "
                "No immediate proactive push; the customer sees these two "
                "messages on the next send_flyer_concept_previews fire — "
                "the caption ships on the concept image, the follow-up "
                "ships as a separate text."
            ),
            "reason_code": str(project.manual_review.reason_code or ""),
        }

    # break_glass
    return {
        "action": "break_glass",
        "project_id": project_id,
        "will_notify": False,
        "customer_text": None,
        "customer_messages": [],
        "would_notify_chat_id": chat_id,
        "chat_id_source": chat_id_source,
        "note": (
            "Break-glass records `manual_review.status=break_glass_sent` "
            "for audit. No customer message is sent by the agent — operator "
            "is asserting out-of-band delivery."
        ),
        "reason_code": str(project.manual_review.reason_code or ""),
    }


@router.get("/manual-queue/{project_id}/action-preview")
async def manual_queue_action_preview_endpoint(
    project_id: str,
    action: str,
    _=Depends(require_auth),
):
    return manual_queue_action_preview(project_id, action=action)


# ─────────────────────────────────────────────────────────────────
# P0-1/P0-2/P0-3 cockpit-ops surface (manual-queue detail drawer,
# operator-uploads endpoint, asset media-serve for visual preview).
# ─────────────────────────────────────────────────────────────────

_OPERATOR_UPLOAD_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB cap; well above any real flyer
_OPERATOR_MIME_TO_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
# Cockpit-uploaded filenames are server-generated to prevent path-injection
# (../../, NUL bytes, etc) and to keep operator-uploads listings sortable by
# arrival time. The pattern matches what we generate so the GET media-serve
# endpoint can reject anything else without filesystem stat.
_OPERATOR_UPLOAD_NAME_RE = re.compile(
    r"^\d{8}T\d{6}Z-[0-9a-f]{16}\.(?:png|jpg|webp|pdf)$"
)


def _generate_operator_upload_filename(mime: str) -> str:
    """Timestamped + random server-controlled filename. Operator cannot
    influence the path inside operator-uploads/."""
    ext = _OPERATOR_MIME_TO_EXT[mime]
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(8)}{ext}"


def _safe_operator_upload_target(filename: str) -> Path:
    """Resolve filename under operator-uploads root, refusing traversal."""
    if not _OPERATOR_UPLOAD_NAME_RE.match(filename):
        raise HTTPException(422, "operator upload filename must match the cockpit-generated pattern")
    upload_root = _operator_upload_root().resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    target = (upload_root / filename).resolve()
    try:
        target.relative_to(upload_root)
    except ValueError:
        raise HTTPException(422, "operator upload filename escapes upload root")
    return target


async def _validate_and_persist_operator_upload(
    file: UploadFile,
) -> tuple[Path, str, int]:
    """Read the upload stream, enforce MIME + size cap, write to a fresh
    server-generated path under operator-uploads/. Returns (path, mime, size)."""
    declared_mime = (file.content_type or "").lower()
    # Trust the declared MIME only as a hint — re-derive from extension on the
    # generated filename (we control the extension based on this MIME).
    if declared_mime not in _OPERATOR_ASSET_MIME_ALLOWLIST:
        raise HTTPException(
            415,
            f"operator upload content_type {declared_mime!r} not in allowlist "
            f"{sorted(_OPERATOR_ASSET_MIME_ALLOWLIST)}",
        )
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _OPERATOR_UPLOAD_MAX_BYTES:
            raise HTTPException(
                413,
                f"operator upload exceeds {_OPERATOR_UPLOAD_MAX_BYTES} bytes",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(422, "operator upload is empty")
    filename = _generate_operator_upload_filename(declared_mime)
    target = _safe_operator_upload_target(filename)
    # Write atomically: tmp file in same dir, then rename.
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(b"".join(chunks))
    try:
        os.replace(tmp, target)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
        raise
    return target, declared_mime, total


@router.post("/operator-uploads")
async def operator_upload(
    request: Request,
    file: UploadFile = File(...),
    reason: str = Form(..., min_length=5, max_length=300),
    _=Depends(require_fresh_otp),
):
    """Upload an approved operator/designer asset to operator-uploads/.

    P0-2: replaces the SCP-then-type-absolute-path UX with a cockpit
    multipart upload. The returned `asset_path` is what the complete
    endpoint expects; the operator does not type or see the file system
    layout.
    """
    target, mime, size = await _validate_and_persist_operator_upload(file)
    payload = {
        "ok": True,
        "asset_path": str(target),
        "filename": target.name,
        "mime_type": mime,
        "size_bytes": size,
    }
    audit_log(
        "flyer.operator_upload",
        ip=client_ip(request),
        ua=client_ua(request),
        details={
            "filename": target.name,
            "mime_type": mime,
            "size_bytes": size,
            "reason": reason,
            "original_filename": file.filename or "",
        },
    )
    return payload


@router.get("/operator-uploads/{filename}")
async def operator_upload_media(
    filename: str,
    _=Depends(require_auth),
):
    """Serve a cockpit-uploaded file back to the operator for preview before
    they commit Complete. Read-only; auth (not OTP) gated. Filename must
    match the server-generated pattern so the operator cannot probe
    arbitrary files under operator-uploads/."""
    target = _safe_operator_upload_target(filename)
    if not target.is_file():
        raise HTTPException(404, "operator upload not found")
    mime, _enc = mimetypes.guess_type(str(target))
    return FileResponse(
        path=str(target),
        media_type=mime or "application/octet-stream",
        filename=target.name,
    )

_FINAL_ASSET_KINDS = {
    "final_whatsapp_image",
    "final_instagram_post",
    "final_instagram_story",
    "final_printable_pdf",
}

_ASSET_OUTPUT_FORMATS = {
    "concept_preview": "concept_preview",
    "final_whatsapp_image": "whatsapp_image",
    "final_instagram_post": "instagram_post",
    "final_instagram_story": "instagram_story",
    "final_printable_pdf": "printable_pdf",
}


def _asset_output_format(kind: str) -> str:
    return _ASSET_OUTPUT_FORMATS.get(kind, kind)


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _asset_dimensions(path: Path, mime_type: str) -> tuple[int | None, int | None]:
    """Best-effort image dimensions. PDFs and unreadable images return None."""
    if not path.is_file():
        return None, None
    if mime_type == "image/png":
        try:
            data = path.read_bytes()[:24]
        except OSError:
            return None, None
        if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
    if mime_type.startswith("image/"):
        try:
            from PIL import Image  # type: ignore
            with Image.open(path) as img:
                return int(img.width), int(img.height)
        except Exception:
            return None, None
    return None, None


def _asset_summary(asset: Any, *, project_id: str) -> dict[str, Any]:
    path = Path(asset.path)
    width, height = _asset_dimensions(path, asset.mime_type)
    size_bytes = path.stat().st_size if path.is_file() else None
    return {
        "asset_id": asset.asset_id,
        "kind": asset.kind,
        "output_format": _asset_output_format(asset.kind),
        "source": asset.source,
        "mime_type": asset.mime_type,
        "sha256": asset.sha256,
        "sha256_short": asset.sha256[:16],
        "file_sha256": _file_sha256(path),
        "size_bytes": size_bytes,
        "width": width,
        "height": height,
        "delivery_status": asset.delivery_status,
        "outbound_message_id": asset.outbound_message_id,
        "received_at": asset.received_at.isoformat() if asset.received_at else None,
        "delivered_at": asset.delivered_at.isoformat() if asset.delivered_at else None,
        "media_url": f"/api/flyer/projects/{project_id}/assets/{asset.asset_id}",
    }


def _parse_ts(value: Any) -> datetime:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timeline_ts(value: Any) -> str:
    parsed = _parse_ts(value)
    if parsed == datetime.min.replace(tzinfo=timezone.utc):
        return ""
    return parsed.isoformat()


def _safe_json_rows(path: Path, *, max_lines: int = 5000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            rows.append(doc)
    return rows


def _entry_project_id(entry: dict[str, Any]) -> str:
    direct = str(entry.get("project_id") or "").strip()
    if direct:
        return direct
    details = entry.get("details")
    if isinstance(details, dict):
        direct = str(details.get("project_id") or "").strip()
        if direct:
            return direct
    text = json.dumps(details if details is not None else entry, default=str, separators=(",", ":"))
    match = re.search(r"\b(F\d{4,})\b", text)
    return match.group(1) if match else ""


def _entry_detail(entry: dict[str, Any]) -> str:
    if entry.get("type") == "flyer_status_change":
        return (
            f"{entry.get('from_status', '')}->{entry.get('to_status', '')}; "
            f"actor={entry.get('actor', '')}; reason={entry.get('reason', '')}"
        ).strip("; ")
    if entry.get("type") == "flyer_assets_delivered":
        return f"asset_ids={','.join(str(x) for x in entry.get('asset_ids', []) or [])}"
    details = entry.get("details")
    if isinstance(details, dict):
        parts = []
        for key in ("reason", "status", "manual_status", "asset_path", "operator_asset_ids", "backup"):
            if key in details and details.get(key) not in ("", None, []):
                parts.append(f"{key}={details.get(key)}")
        if parts:
            return "; ".join(parts)[:500]
        return json.dumps(details, default=str, separators=(",", ":"))[:500]
    if isinstance(details, str):
        return details[:500]
    if "detail" in entry:
        return str(entry.get("detail") or "")[:500]
    return ""


def _audit_timeline(project_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    settings = get_settings()
    for source, path, event_key in (
        ("decisions", settings.decisions_path, "type"),
        ("cockpit_audit", settings.cockpit_audit_log, "event"),
    ):
        for entry in _safe_json_rows(path):
            if _entry_project_id(entry) != project_id:
                continue
            ts = _timeline_ts(entry.get("ts"))
            if not ts:
                continue
            rows.append({
                "ts": ts,
                "event": str(entry.get(event_key) or entry.get("type") or entry.get("event") or "audit_event"),
                "detail": _entry_detail(entry),
                "source": source,
            })
    return rows


def manual_queue_detail_action(project_id: str) -> dict[str, Any]:
    """Rich per-project context for the cockpit drawer (P0-1).

    Pulls together project state, locked facts, QA blockers, asset
    summaries, manual review timeline, and a recommended-action playbook
    key. Strictly read-only; no auth state mutation."""
    store = load_project_store()
    project = next((p for p in store.projects if p.project_id == project_id), None)
    if project is None:
        raise HTTPException(404, f"project {project_id} not found")
    manual = project.manual_review
    qa_blockers: list[str] = []
    for report in project.qa_reports:
        qa_blockers.extend(report.blockers)
    timeline = [
        {"ts": project.created_at.isoformat(), "event": "project_created", "detail": f"status={project.status}", "source": "project_state"},
        {"ts": project.updated_at.isoformat(), "event": "project_updated", "detail": f"status={project.status}", "source": "project_state"},
    ]
    if manual.queued_at:
        timeline.append({
            "ts": manual.queued_at.isoformat(),
            "event": "manual_review_queued",
            "detail": f"reason_code={manual.reason_code}",
            "source": "project_state",
        })
    if manual.completed_at:
        timeline.append({
            "ts": manual.completed_at.isoformat(),
            "event": f"manual_review_{manual.status}",
            "detail": manual.detail[:200] if manual.detail else "",
            "source": "project_state",
        })
    timeline.extend(_audit_timeline(project.project_id))
    timeline.sort(key=lambda row: _parse_ts(row["ts"]))
    asset_summaries = [_asset_summary(asset, project_id=project.project_id) for asset in project.assets]
    final_asset_ids = set(project.final_asset_ids)
    final_assets = [
        asset
        for asset in asset_summaries
        if asset["kind"] in _FINAL_ASSET_KINDS or asset["asset_id"] in final_asset_ids
    ]
    return {
        "project_id": project.project_id,
        "customer_phone": str(project.customer_phone),
        "status": project.status,
        "raw_request": project.raw_request,
        "original_message_id": project.original_message_id,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
        "version": project.version,
        "manual_review": {
            "status": manual.status,
            "reason": manual.reason,
            "reason_code": manual.reason_code,
            "detail": manual.detail,
            "queued_at": manual.queued_at.isoformat() if manual.queued_at else None,
            "completed_at": manual.completed_at.isoformat() if manual.completed_at else None,
            "break_glass_reason": getattr(manual, "break_glass_reason", "") or "",
            "operator_asset_ids": list(manual.operator_asset_ids),
        },
        "locked_facts": [fact.model_dump(mode="json") for fact in project.locked_facts],
        "qa_blockers": qa_blockers,
        "verification_modes": flyer_verification_modes(project),
        "assets": asset_summaries,
        "final_assets": final_assets,
        "final_asset_ids": list(project.final_asset_ids),
        "selected_concept_id": project.selected_concept_id,
        "fields": project.fields.model_dump(mode="json"),
        "timeline": timeline,
    }


@router.get("/manual-queue/{project_id}/detail")
async def manual_queue_detail(
    project_id: str,
    _=Depends(require_auth),
):
    return manual_queue_detail_action(project_id)


@router.get("/projects/{project_id}/assets/{asset_id}")
async def project_asset_media(
    project_id: str,
    asset_id: str,
    _=Depends(require_auth),
):
    """Serve a project asset's bytes for thumbnail/preview rendering (P0-3).

    Authorization: project + asset_id pair must exist in the live store.
    Path is validated by the FlyerAsset schema to be under
    /opt/shift-agent/state/flyer/, so we additionally ensure the resolved
    file is under the flyer state root before serving.
    """
    if not re.match(r"^F\d{4,}$", project_id) or not re.match(r"^A\d{4,}$", asset_id):
        raise HTTPException(422, "project_id or asset_id has wrong shape")
    store = load_project_store()
    project = next((p for p in store.projects if p.project_id == project_id), None)
    if project is None:
        raise HTTPException(404, f"project {project_id} not found")
    asset = next((a for a in project.assets if a.asset_id == asset_id), None)
    if asset is None:
        raise HTTPException(404, f"asset {asset_id} not found in project {project_id}")
    state_root = _flyer_dir().resolve()
    asset_file = Path(asset.path).resolve()
    try:
        asset_file.relative_to(state_root)
    except ValueError:
        raise HTTPException(404, "asset path is outside flyer state root")
    if not asset_file.is_file():
        raise HTTPException(404, "asset file is missing on disk")
    return FileResponse(
        path=str(asset_file),
        media_type=asset.mime_type or "application/octet-stream",
        filename=asset_file.name,
    )


@router.post("/campaigns/send-csv")
async def campaign_send_csv(
    request: Request,
    file: UploadFile = File(...),
    reason: str = Form(..., min_length=5, max_length=300),
    dry_run: bool = Form(True),
    _=Depends(require_fresh_otp),
):
    parsed = await parse_campaign_csv(file)
    result = send_campaign_to_targets(parsed["valid_targets"], dry_run=dry_run, reason=reason)
    audit_log(
        "flyer.campaign.send_csv",
        ip=client_ip(request),
        ua=client_ua(request),
        details={
            "reason": reason,
            "dry_run": dry_run,
            "filename": file.filename or "unknown",
            "target_count": len(parsed["valid_targets"]),
            "sent": result["sent"],
            "failed": result["failed"],
        },
    )
    return result | {"invalid": parsed["invalid"], "duplicate_count": parsed["duplicate_count"]}


# ─── P0-7: provider + runtime health ────────────────────────────────────
#
# Read-only health surface for the Flyer cockpit. Never returns secret values
# or prefixes; only key_present + key_source so the operator knows WHICH env
# file to inspect without revealing anything sensitive. Mirrors the layered
# env reader convention from src/agents/flyer/workflow.py::_read_env_value
# (process env -> /root/.hermes/.env -> /opt/shift-agent/.env) but exposes the
# matching source. Placeholder detection matches workflow.source_edit_provider_ready
# (any value containing "PLACEHOLDER" counts as missing).


def _is_placeholder(value: str) -> bool:
    return "PLACEHOLDER" in value.upper()


def _read_env_layered(name: str) -> tuple[str, str | None]:
    """Return (value, source) where source is process_env|hermes_env|agent_env or None.

    Honors HERMES_ENV_PATH / SHIFT_AGENT_ENV_PATH overrides so tests can isolate.
    Process env wins over both files (matches workflow._read_env_value order).
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value, "process_env"
    candidates = (
        ("hermes_env", Path(os.environ.get("HERMES_ENV_PATH", "/root/.hermes/.env"))),
        ("agent_env", Path(os.environ.get("SHIFT_AGENT_ENV_PATH", "/opt/shift-agent/.env"))),
    )
    for source, env_path in candidates:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                if key.strip() == name:
                    raw = raw.strip().strip('"').strip("'")
                    if raw:
                        return raw, source
        except OSError:
            continue
    return "", None


def _shift_agent_deploy_tag() -> tuple[str | None, str | None]:
    """Resolve the SHIFT-AGENT deploy_tag + commit_hash from on-disk markers.

    /opt/shift-agent/.commit-hash is written by tools/build-deploy-tarball.sh.
    The newest /opt/shift-agent/deploys/deploy-*.tgz is the active agent deploy.
    Both env-overrideable for test isolation. Either or both may be None.

    IMPORTANT: this is the agent tarball deploy, NOT the cockpit deploy. The
    cockpit (FastAPI + React) deploys separately and there is no cockpit-side
    marker today; surfacing this value labeled as "cockpit" would lie when
    cockpit code is fresh but the agent tarball is stale or vice versa.
    See tasks/flyer-source-edit-provider-posture-2026-05-20.md follow-ups.
    """
    commit_hash: str | None = None
    deploy_tag: str | None = None
    hash_path = Path(os.environ.get("SHIFT_AGENT_DEPLOY_HASH_PATH", "/opt/shift-agent/.commit-hash"))
    deploys_dir = Path(os.environ.get("SHIFT_AGENT_DEPLOYS_DIR", "/opt/shift-agent/deploys"))
    if hash_path.exists():
        try:
            commit_hash = (hash_path.read_text(encoding="utf-8").strip() or None)
            if commit_hash:
                commit_hash = commit_hash[:40]
        except OSError:
            commit_hash = None
    if deploys_dir.exists():
        try:
            # Deploy tag names embed a UTC timestamp (deploy-YYYYMMDD-HHMMSS-<sha>),
            # so name-desc is the authoritative ordering. mtime is a secondary
            # tiebreaker for tarballs that share an embedded timestamp.
            tarballs = sorted(
                (p for p in deploys_dir.iterdir() if p.name.startswith("deploy-") and p.suffix == ".tgz"),
                key=lambda p: (p.name, p.stat().st_mtime),
                reverse=True,
            )
            if tarballs:
                deploy_tag = tarballs[0].name[: -len(".tgz")]
        except OSError:
            deploy_tag = None
    return deploy_tag, commit_hash


def _source_edit_manual_queue_impact() -> dict[str, Any]:
    """Count active manual-queue rows with reason_code=source_edit_provider_unavailable.

    Active = manual_status in {queued, in_progress}. Returns
    {queued_count: int, oldest_age_hours: int | None}. Best-effort: any
    exception loading the project store returns zero impact (the health
    endpoint must never raise).
    """
    try:
        rows = list_manual_queue(load_project_store())
    except Exception:
        return {"queued_count": 0, "oldest_age_hours": None}
    matched = [
        r for r in rows
        if r.get("manual_reason_code") == "source_edit_provider_unavailable"
        and r.get("manual_status") in {"queued", "in_progress"}
    ]
    if not matched:
        return {"queued_count": 0, "oldest_age_hours": None}
    oldest = max(int(r.get("age_hours", 0) or 0) for r in matched)
    return {"queued_count": len(matched), "oldest_age_hours": oldest}


def _flyer_image_model_config() -> dict[str, dict[str, str]]:
    """Read deployed image-gen model config from config.yaml (not FlyerConfig defaults).

    Falls back to FlyerConfig defaults if config.yaml cannot be loaded -- keeps
    the health endpoint best-effort and non-raising.
    """
    try:
        from ..state import load_config
        flyer_cfg = load_config().flyer
        draft = flyer_cfg.draft_image_model
        final = flyer_cfg.final_image_model
        edit = flyer_cfg.edit_image_model
        draft_provider = flyer_cfg.resolve_draft_render_provider()
        final_provider = flyer_cfg.resolve_final_render_provider()
        source_edit_provider = flyer_cfg.resolve_source_edit_render_provider()
    except Exception:
        defaults = FlyerConfig()
        draft = defaults.draft_image_model
        final = defaults.final_image_model
        edit = defaults.edit_image_model
        draft_provider = defaults.resolve_draft_render_provider()
        final_provider = defaults.resolve_final_render_provider()
        source_edit_provider = defaults.resolve_source_edit_render_provider()
    return {
        "openrouter_generation_vision": {
            "draft_image_model": draft,
            "final_image_model": final,
            "draft_provider": draft_provider.provider,
            "draft_provider_model": draft_provider.model,
            "draft_provider_quality": draft_provider.quality,
            "final_provider": final_provider.provider,
            "final_provider_model": final_provider.model,
            "final_provider_quality": final_provider.quality,
        },
        "source_edit_provider": {
            "edit_image_model": edit,
            "source_edit_provider": source_edit_provider.provider,
            "source_edit_provider_model": source_edit_provider.model,
            "source_edit_provider_quality": source_edit_provider.quality,
        },
    }


async def _platform_runtime_components() -> list[dict[str, Any]]:
    """Probe gateway / bridge / paired / cockpit deploy. Reuses health.py helpers."""
    from .health import _bridge_health, _gateway_active, _wa_paired

    components: list[dict[str, Any]] = []
    now_iso = _now().isoformat()

    gw = _gateway_active()
    components.append({
        "name": "gateway",
        "severity": "green" if gw else "red",
        "detail": "active" if gw else "inactive",
        "checked_at": now_iso,
    })

    bridge_ok, bridge_detail = await _bridge_health()
    components.append({
        "name": "whatsapp_bridge",
        "severity": "green" if bridge_ok else "red",
        "detail": bridge_detail,
        "checked_at": now_iso,
    })

    paired, me_id = _wa_paired()
    components.append({
        "name": "whatsapp_paired",
        "severity": "green" if paired else "red",
        "detail": me_id or "not paired",
        "checked_at": now_iso,
    })

    deploy_tag, commit_hash = _shift_agent_deploy_tag()
    if deploy_tag:
        deploy_detail = deploy_tag
        deploy_severity = "green"
    elif commit_hash:
        deploy_detail = f"commit {commit_hash}"
        deploy_severity = "green"
    else:
        deploy_detail = "deploy marker missing"
        deploy_severity = "yellow"
    components.append({
        # Truthful label: this is the SHIFT-AGENT tarball marker, not the
        # cockpit's. Cockpit deploys separately and has no own marker today
        # (deferred — see plan/posture follow-up).
        "name": "shift_agent_deploy",
        "severity": deploy_severity,
        "detail": deploy_detail,
        "checked_at": now_iso,
    })
    return components


def _flyer_provider_components() -> list[dict[str, Any]]:
    """Per-provider posture for OpenRouter generation/vision and source edits.

    NEVER returns secret values or prefixes -- only key_present + key_source.
    Severity policy:
      - OpenRouter missing/placeholder => red (hard block on normal generation).
      - Source-edit missing/placeholder/manual-review => yellow (degraded;
        routes to manual review). Stays yellow even when queued_count > 0 --
        the detail string surfaces the manual-queue impact prominently instead.
    """
    now_iso = _now().isoformat()
    models = _flyer_image_model_config()

    # OpenRouter -- generation + vision (normal path).
    or_value, or_source = _read_env_layered("OPENROUTER_API_KEY")
    or_placeholder = bool(or_value) and _is_placeholder(or_value)
    or_present = bool(or_value) and not or_placeholder
    if or_present:
        or_severity = "green"
        or_detail = f"OPENROUTER_API_KEY present ({or_source})"
    elif or_placeholder:
        or_severity = "red"
        or_detail = "OPENROUTER_API_KEY is a placeholder - normal Flyer Studio generation is blocked"
    else:
        or_severity = "red"
        or_detail = "OPENROUTER_API_KEY missing - normal Flyer Studio generation is blocked"

    source_model = models["source_edit_provider"]
    source_provider = str(source_model.get("source_edit_provider") or "manual_review")
    source_model_id = str(source_model.get("source_edit_provider_model") or "manual_review")
    queue_impact = _source_edit_manual_queue_impact()
    key_name = {
        "openrouter": "OPENROUTER_API_KEY",
        "openai": "OPENAI_API_KEY",
    }.get(source_provider)
    if key_name:
        source_value, source = _read_env_layered(key_name)
        source_placeholder = bool(source_value) and _is_placeholder(source_value)
        source_present = bool(source_value) and not source_placeholder
    else:
        source, source_placeholder, source_present = None, False, False
    if source_provider == "manual_review":
        source_severity = "yellow"
        source_detail = "Exact source edits are configured for manual review"
    elif source_present:
        source_severity = "green"
        source_detail = f"{key_name} present ({source}) for {source_provider}/{source_model_id}"
    else:
        source_severity = "yellow"
        queued = queue_impact["queued_count"]
        oldest = queue_impact["oldest_age_hours"]
        if queued > 0:
            source_detail = (
                "Exact flyer edits are falling back to manual review because source-edit "
                f"provider is unavailable ({queued} queued; oldest "
                f"{oldest if oldest is not None else 0}h)"
            )
        elif source_placeholder:
            source_detail = f"{key_name} is a placeholder - exact edits will route to manual review"
        else:
            source_detail = f"{key_name or 'source edit provider key'} missing - exact edits will route to manual review"

    operator_note_source = (
        "Source-edit uses the configured Flyer source-edit provider. "
        "Customer-grade reliance still requires spend-gated source-preservation smoke."
    )

    return [
        {
            "name": "openrouter_generation_vision",
            "purpose": "Image generation + vision extraction (normal Flyer Studio path)",
            "severity": or_severity,
            "detail": or_detail,
            "key_present": or_present,
            "key_source": or_source if or_present else None,
            "model_config": models["openrouter_generation_vision"],
            "checked_at": now_iso,
        },
        {
            "name": "source_edit_provider",
            "purpose": "Exact source-preserving flyer edits (configured provider)",
            "severity": source_severity,
            "detail": source_detail,
            "key_present": source_present,
            "key_source": source if source_present else None,
            "model_config": source_model,
            "manual_queue_impact": queue_impact,
            "operator_note": operator_note_source,
            "checked_at": now_iso,
        },
    ]


@router.get("/health")
async def flyer_health(_=Depends(require_auth)):
    """Read-only flyer provider + platform-runtime health.

    Surfaces gateway / bridge / paired / cockpit deploy plus OpenRouter
    (generation + vision) and configured source-edit posture. Never returns
    secret values or prefixes - only key_present + key_source.
    OpenRouter missing = red (blocks generation); source-edit missing = yellow
    (degraded; routes to manual review).
    """
    components = await _platform_runtime_components()
    providers = _flyer_provider_components()
    # Top-level deploy fields name the SHIFT-AGENT tarball explicitly so a
    # consumer cannot mis-attribute them to the cockpit's own deploy state.
    shift_agent_deploy_tag, shift_agent_commit_hash = _shift_agent_deploy_tag()
    return {
        "checked_at": _now().isoformat(),
        "shift_agent_deploy_tag": shift_agent_deploy_tag,
        "shift_agent_commit_hash": shift_agent_commit_hash,
        "components": components,
        "providers": providers,
    }
