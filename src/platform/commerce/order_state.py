"""Commerce order state machine — slice 1.

LEGAL_TRANSITIONS is the single source of truth (Reviewer A MEDIUM-2).
Every transition is validated against this constant; illegal transitions
raise IllegalCommerceTransition.

`refunded` / `chargeback` end states are reserved in the enum but slice 1
emits no transitions into them — operator-only paths land in slice 2+.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, get_args

from schemas import (
    CommerceCart,
    CommerceOrder,
    CommerceOrderStatus,
    CommerceOrderStatusEvent,
    CommerceOrderStore,
)
from .cart import atomic_write_json  # reuse cart.py's Windows-safe shim
from .audit import emit
from .exceptions import IllegalCommerceTransition


LEGAL_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    # pending_payment → ...
    ("pending_payment", "awaiting_approval"),
    ("pending_payment", "paid"),
    ("pending_payment", "cancelled"),
    ("pending_payment", "voided"),
    # awaiting_approval → ...
    ("awaiting_approval", "pending_payment"),
    ("awaiting_approval", "cancelled"),
    # paid → ...
    ("paid", "preparing"),
    ("paid", "refunded"),
    # preparing → ...
    ("preparing", "ready"),
    ("preparing", "cancelled"),
    # ready → ...
    ("ready", "out_for_delivery"),
    ("ready", "completed"),
    # out_for_delivery → ...
    ("out_for_delivery", "completed"),
})

TERMINAL_STATUSES: frozenset[str] = frozenset({
    "completed", "cancelled", "voided", "refunded",
})

REFUSED_CATEGORY_REASONS = {
    "restricted_category",
    "per_vps_exclusion",
    "permanently_blocked",
}


@dataclass(frozen=True)
class OrderOpResult:
    ok: bool
    order: Optional[CommerceOrder]
    detail: str = ""


def load_order_store(path: Path) -> CommerceOrderStore:
    if not path.exists():
        return CommerceOrderStore()
    import json
    return CommerceOrderStore.model_validate(json.loads(path.read_text(encoding="utf-8")))


def write_order_store(path: Path, store: CommerceOrderStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, store)


def _next_order_id(store: CommerceOrderStore) -> str:
    n = 1
    used = {o.order_id for o in store.orders}
    while True:
        candidate = f"CO{n:05d}"
        if candidate not in used:
            return candidate
        n += 1


def create(
    *,
    state_path: Path,
    decisions_log_path: Path,
    cart: CommerceCart,
    restricted_skus: list[str] = (),
    refusal_reason: str = "restricted_category",
    now: Optional[datetime] = None,
) -> OrderOpResult:
    """Create an order from a cart.

    If `restricted_skus` is non-empty, refuses the order and emits
    `commerce_order_create_refused_category`. Caller (catalog filter)
    determines which SKUs are restricted under current cfg.
    """
    now = now or datetime.now(timezone.utc)
    if restricted_skus:
        emit(
            decisions_log_path,
            {
                "type": "commerce_order_create_refused_category",
                "ts": now.isoformat(),
                "sender_phone": str(cart.sender_phone) if cart.sender_phone else None,
                "sender_lid": cart.sender_lid,
                "refused_skus": list(restricted_skus),
                "reason": refusal_reason,
            },
        )
        return OrderOpResult(False, None, f"refused_category:{refusal_reason}")

    if not cart.items:
        return OrderOpResult(False, None, "cart_empty")

    store = load_order_store(state_path)
    existing = next(
        (o for o in store.orders if o.cart_id == cart.cart_id), None
    )
    if existing is not None:
        # Idempotent: same cart -> same order (do not double-create).
        return OrderOpResult(True, existing, "already_created_idempotent")

    order_id = _next_order_id(store)
    subtotal = sum(item.line_total_cents for item in cart.items)
    initial_event = CommerceOrderStatusEvent(
        from_status=None,
        to_status="pending_payment",
        ts=now,
        cause="customer_checkout",
        actor="caller",
        event_ref=cart.cart_id,
    )
    order = CommerceOrder(
        order_id=order_id,
        sender_phone=cart.sender_phone,
        sender_lid=cart.sender_lid,
        chat_id=cart.chat_id,
        cart_id=cart.cart_id,
        line_items=list(cart.items),
        subtotal_cents=subtotal,
        tax_cents=0,
        fee_cents=0,
        total_cents=subtotal,
        currency=cart.currency,
        status="pending_payment",
        payment_intent_id="",
        payment_reference="",
        status_history=[initial_event],
        created_at=now,
        updated_at=now,
    )
    store.orders.append(order)
    write_order_store(state_path, store)
    emit(
        decisions_log_path,
        {
            "type": "commerce_order_created",
            "ts": now.isoformat(),
            "order_id": order_id,
            "cart_id": cart.cart_id,
            "sender_phone": str(cart.sender_phone) if cart.sender_phone else None,
            "sender_lid": cart.sender_lid,
            "total_cents": subtotal,
            "currency": cart.currency,
        },
    )
    return OrderOpResult(True, order)


def transition(
    *,
    state_path: Path,
    decisions_log_path: Path,
    order_id: str,
    to_status: CommerceOrderStatus,
    actor: Literal["customer", "caller", "operator", "cron", "webhook"],
    cause: str,
    event_ref: str = "",
    now: Optional[datetime] = None,
) -> OrderOpResult:
    """Apply a state transition. Raises IllegalCommerceTransition if not in LEGAL_TRANSITIONS."""
    now = now or datetime.now(timezone.utc)
    store = load_order_store(state_path)
    order = next((o for o in store.orders if o.order_id == order_id), None)
    if order is None:
        return OrderOpResult(False, None, "order_not_found")

    if order.status == to_status:
        # Idempotent: re-applying the same status is a no-op success.
        return OrderOpResult(True, order, "noop_already_in_status")

    if (order.status, to_status) not in LEGAL_TRANSITIONS:
        raise IllegalCommerceTransition(order.status, to_status)

    new_event = CommerceOrderStatusEvent(
        from_status=order.status,
        to_status=to_status,
        ts=now,
        cause=cause,
        actor=actor,
        event_ref=event_ref,
    )
    updated = order.model_copy(
        update={
            "status": to_status,
            "status_history": list(order.status_history) + [new_event],
            "updated_at": now,
        }
    )
    _replace(store, updated)
    write_order_store(state_path, store)
    emit(
        decisions_log_path,
        {
            "type": "commerce_order_status_change",
            "ts": now.isoformat(),
            "order_id": order_id,
            "prev_status": order.status,
            "next_status": to_status,
            "actor": actor,
            "cause": cause,
        },
    )
    return OrderOpResult(True, updated)


def cancel(
    *,
    state_path: Path,
    decisions_log_path: Path,
    order_id: str,
    reason: str,
    actor: Literal["customer", "operator", "cron"] = "operator",
    now: Optional[datetime] = None,
) -> OrderOpResult:
    """Convenience wrapper. Cancel is only valid pre-payment.

    Post-payment refunds go via the (slice 2) refund path which transitions
    to `refunded`, not `cancelled`.
    """
    result = transition(
        state_path=state_path,
        decisions_log_path=decisions_log_path,
        order_id=order_id,
        to_status="cancelled",
        actor=actor,
        cause=reason,
        now=now,
    )
    if not result.ok or result.order is None:
        return result
    now = now or datetime.now(timezone.utc)
    emit(
        decisions_log_path,
        {
            "type": "commerce_order_cancelled",
            "ts": now.isoformat(),
            "order_id": order_id,
            "reason": reason,
            "actor": actor,
        },
    )
    return result


def get(state_path: Path, order_id: str) -> Optional[CommerceOrder]:
    store = load_order_store(state_path)
    return next((o for o in store.orders if o.order_id == order_id), None)


def _replace(store: CommerceOrderStore, order: CommerceOrder) -> None:
    store.orders = [order if o.order_id == order.order_id else o for o in store.orders]
