"""Read-only Commerce Order Cockpit (Slice B, 2026-05-30).

Surfaces existing Commerce orders (`state/commerce/orders.json`) + their audit
trail (`decisions.log`) in the operator dashboard.

READ-ONLY by construction: no state transitions, no file writes, no provider
activation, no dispatcher/customer messaging. Single-tenant per VPS — reads
ONLY this deployment's state under `settings.state_dir`. Fails gracefully
(empty list / `degraded` flag) when the commerce state file is absent (the
common case — Commerce is config-inactive) or unreadable; never raises out of
a missing/malformed state file, never mutates anything.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..audit import log as audit_log
from ..auth import require_auth, require_fresh_otp
from ..config import get_settings
from ..deps import client_ip, client_ua
from ..log_tail import reverse_json_entries

# Same path-injection as flyer.py so the agent's commerce primitives + schemas
# import in production (/opt/shift-agent) and on a fresh clone (conftest /
# dump-openapi prepend src + src/platform).
_AGENT_ROOT = Path("/opt/shift-agent")
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from commerce import order_state  # noqa: E402
from commerce.audit import emit as commerce_emit  # noqa: E402
from commerce.exceptions import IllegalCommerceTransition  # noqa: E402
from schemas import CommerceOrderStatus  # noqa: E402

router = APIRouter(prefix="/commerce", tags=["commerce"])

# Slice-C scope gate: a STRICT SUBSET of the primitive's LEGAL_TRANSITIONS.
# Owner-initiated fulfillment progress + pre-payment cancel only. Money/provider
# transitions (->paid, ->refunded), post-payment cancel (preparing->cancelled),
# and POS edits are intentionally withheld — see the Slice-C design doc.
SLICE_C_ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("paid", "preparing"),
    ("preparing", "ready"),
    ("ready", "out_for_delivery"),
    ("out_for_delivery", "completed"),
    ("ready", "completed"),
    ("pending_payment", "cancelled"),
    ("awaiting_approval", "cancelled"),
})
_CANCEL_TARGET = "cancelled"

_COMMERCE_AUDIT_PREFIX = "commerce_"
_OPEN_STATUSES = {
    "pending_payment", "awaiting_approval", "paid",
    "preparing", "ready", "out_for_delivery",
}


def _orders_path() -> Path:
    # state_dir is mutated in tests; resolve per-call so tests + prod agree.
    return get_settings().state_dir / "commerce" / "orders.json"


def _safe_int(v: Any) -> int:
    """Coerce a possibly-malformed cents value to int; bad data -> 0 (never raise)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _item_count(v: Any) -> int:
    """Count line items defensively; a non-list value -> 0 (never raise)."""
    return len(v) if isinstance(v, list) else 0


def _load_orders() -> tuple[list[dict[str, Any]], Optional[str]]:
    """Return (orders, degraded_reason). Missing file -> ([], None): a useful
    empty state (Commerce is dormant). Unreadable/malformed -> ([], reason):
    the UI shows a degraded banner instead of crashing. Never raises, never
    writes."""
    path = _orders_path()
    if not path.exists():
        return [], None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        return [], f"orders state unreadable ({type(e).__name__})"
    orders = raw.get("orders") if isinstance(raw, dict) else None
    if not isinstance(orders, list):
        return [], "orders state malformed (missing 'orders' list)"
    return [o for o in orders if isinstance(o, dict)], None


def _payment_status(o: dict[str, Any]) -> str:
    """Display-only payment status inferred from order status + payment fields.
    No money logic, no computation — pure read."""
    status = o.get("status") or ""
    if status in {"paid", "preparing", "ready", "out_for_delivery", "completed"}:
        return "paid"
    if status == "refunded":
        return "refunded"
    if status in {"cancelled", "voided"}:
        return "none"
    return "pending" if o.get("payment_intent_id") else "unpaid"


def _row(o: dict[str, Any]) -> dict[str, Any]:
    """Compact, scan-friendly inbox row."""
    return {
        "order_id": o.get("order_id"),
        "customer_name": o.get("customer_name"),
        "sender_phone": o.get("sender_phone"),
        "sender_lid": o.get("sender_lid"),
        "status": o.get("status"),
        "fulfillment_type": o.get("fulfillment_type"),
        "requested_time": o.get("requested_time"),
        "payment_status": _payment_status(o),
        "pos_sync_status": o.get("pos_sync_status", "not_synced"),
        "total_cents": o.get("total_cents"),
        "currency": o.get("currency"),
        "item_count": _item_count(o.get("line_items")),
        "created_at": o.get("created_at"),
        "updated_at": o.get("updated_at"),
    }


def _totals(orders: list[dict[str, Any]]) -> dict[str, Any]:
    by_currency: dict[str, int] = {}
    for o in orders:
        cur = o.get("currency") or "USD"
        by_currency[cur] = by_currency.get(cur, 0) + _safe_int(o.get("total_cents"))
    open_count = sum(1 for o in orders if (o.get("status") or "") in _OPEN_STATUSES)
    return {
        "order_count": len(orders),
        "open_count": open_count,
        "gross_cents_by_currency": by_currency,
    }


def _order_audit_trail(order_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Best-effort join of `decisions.log` commerce entries referencing this
    order. The authoritative per-order trail is the order's embedded
    `status_history`; this supplements it. Read-only, bounded scan, oldest-first."""
    out: list[dict[str, Any]] = []
    try:
        for e in reverse_json_entries(get_settings().decisions_path, max_lines=20_000):
            t = e.get("type") or ""
            if not t.startswith(_COMMERCE_AUDIT_PREFIX):
                continue
            if e.get("order_id") == order_id or e.get("event_ref") == order_id:
                out.append(e)
                if len(out) >= limit:
                    break
    except OSError:
        # Unreadable decisions.log (permission/IO/IsADirectory). The audit join
        # is supplementary (the order's status_history is the authoritative
        # trail) — degrade to an empty trail rather than 500 the detail view.
        return []
    out.reverse()
    return out


@router.get("/orders")
async def list_orders(_=Depends(require_auth)) -> dict[str, Any]:
    orders, degraded = _load_orders()
    rows = sorted(
        (_row(o) for o in orders),
        key=lambda r: r.get("created_at") or "",
        reverse=True,
    )
    return {"orders": rows, "totals": _totals(orders), "degraded": degraded}


@router.get("/orders/{order_id}")
async def get_order(order_id: str, _=Depends(require_auth)) -> dict[str, Any]:
    orders, degraded = _load_orders()
    match = next((o for o in orders if o.get("order_id") == order_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="order not found")
    return {
        "order": match,
        "payment_status": _payment_status(match),
        "audit": _order_audit_trail(order_id),
        "degraded": degraded,
    }


# ── Slice C: owner-initiated status transitions (the first write surface) ────


class OrderTransitionBody(BaseModel):
    """Cockpit staff-action request. `expected_from_status` is the status the
    operator saw when they clicked (optimistic-concurrency token); `cause` is
    required for a cancel (the operator's reason) and optional otherwise."""
    model_config = ConfigDict(extra="forbid")
    to_status: CommerceOrderStatus
    expected_from_status: CommerceOrderStatus
    cause: str = Field(default="", max_length=200)


def _emit_action_refused(
    *, order_id: str, body: "OrderTransitionBody", reason: str, request: Request
) -> None:
    """Audit a declined cockpit action through the commerce chokepoint AND the
    cockpit audit log (owner sub + IP + UA)."""
    commerce_emit(
        get_settings().decisions_path,
        {
            "type": "commerce_order_action_refused",
            "order_id": order_id,
            "attempted_to_status": body.to_status,
            "from_status": body.expected_from_status,
            "reason": reason,
            "actor": "operator",
            "cause": body.cause[:200],
        },
    )
    audit_log(
        "commerce.order.transition.refused",
        ip=client_ip(request),
        ua=client_ua(request),
        details={
            "order_id": order_id,
            "to_status": body.to_status,
            "expected_from_status": body.expected_from_status,
            "reason": reason,
        },
    )


@router.post("/orders/{order_id}/transition")
async def transition_order(
    order_id: str,
    body: OrderTransitionBody,
    request: Request,
    _=Depends(require_fresh_otp),
) -> dict[str, Any]:
    """Apply an owner-initiated Slice-C status transition.

    Owner-only (require_fresh_otp step-up). No provider/POS/customer-send calls.
    Scope-gated to SLICE_C_ALLOWED_TRANSITIONS; the optimistic-concurrency
    guard (`expected_from_status`) and the shared FileLock live in the
    `order_state` primitive. Every refusal is audited; on the happy path the
    updated order is returned."""
    settings = get_settings()
    state_path = _orders_path()
    pair = (body.expected_from_status, body.to_status)

    # 1. Slice-C scope gate (route-level allowlist; narrower than LEGAL_TRANSITIONS).
    if pair not in SLICE_C_ALLOWED_TRANSITIONS:
        _emit_action_refused(order_id=order_id, body=body,
                             reason="not_allowed_in_slice_c", request=request)
        raise HTTPException(status_code=409, detail="transition not allowed in this slice")

    # 2. Cancel requires an operator reason (O3); progress gets a default cause.
    cause = body.cause.strip()
    if body.to_status == _CANCEL_TARGET and not cause:
        raise HTTPException(status_code=422, detail="cancel requires a reason")
    if not cause:
        cause = f"cockpit: {body.expected_from_status}->{body.to_status}"

    # 3. Apply via the primitive (lock + authoritative stale-check are inside it).
    try:
        if body.to_status == _CANCEL_TARGET:
            result = order_state.cancel(
                state_path=state_path,
                decisions_log_path=settings.decisions_path,
                order_id=order_id,
                reason=cause,
                actor="operator",
                expected_from_status=body.expected_from_status,
            )
        else:
            result = order_state.transition(
                state_path=state_path,
                decisions_log_path=settings.decisions_path,
                order_id=order_id,
                to_status=body.to_status,
                actor="operator",
                cause=cause,
                expected_from_status=body.expected_from_status,
            )
    except IllegalCommerceTransition:
        _emit_action_refused(order_id=order_id, body=body,
                             reason="illegal_transition", request=request)
        raise HTTPException(status_code=409, detail="illegal transition")

    if not result.ok:
        reason = result.detail if result.detail in (
            "order_not_found", "stale_expected_status") else "illegal_transition"
        _emit_action_refused(order_id=order_id, body=body, reason=reason, request=request)
        raise HTTPException(
            status_code=404 if reason == "order_not_found" else 409, detail=reason)

    audit_log(
        "commerce.order.transition",
        ip=client_ip(request),
        ua=client_ua(request),
        details={
            "order_id": order_id,
            "to_status": body.to_status,
            "expected_from_status": body.expected_from_status,
            "cause": cause,
            "detail": result.detail,
        },
    )
    return {"order": result.order.model_dump(mode="json"), "detail": result.detail}
