"""Flyer Studio operator dashboard APIs."""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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
    manual_edit_count = sum(1 for p in projects.projects if p.status == "manual_edit_required")
    stuck_edit_count = 0
    for project in projects.projects:
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
async def customers(query: str = "", segment: str = "", _=Depends(require_auth)):
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
    return {"customers": rows}


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
