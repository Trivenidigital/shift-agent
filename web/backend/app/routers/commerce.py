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
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..config import get_settings
from ..log_tail import reverse_json_entries

router = APIRouter(prefix="/commerce", tags=["commerce"])

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
