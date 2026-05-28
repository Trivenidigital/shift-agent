"""Commerce cart primitive — deterministic cart state per (sender, chat).

Slice 1 scope:
- Cart create / item add / item remove / item update_qty / clear
- 4h idle TTL (auto-expire on read or on prune)
- Integer quantities only (Reviewer A HIGH-2; Decimal fractional units → slice 2)
- Per-VPS state file: state/commerce/carts.json (atomic via safe_io)
- Audit via commerce.audit.emit (no direct decisions.log writes)

Caller responsibility: identify the sender via Hermes identify-sender + pass
sender_phone/sender_lid + chat_id. Cart accepts either-or per RawInbound
shape (see schemas.CommerceCart model_validator).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from schemas import (
    CommerceCart,
    CommerceCartItem,
    CommerceCartStore,
    E164Phone,
)
import os
try:
    if os.name == "nt":
        raise ModuleNotFoundError("use simple atomic write fallback on Windows (Flyer guest_order.py precedent)")
    from safe_io import atomic_write_json as _safe_atomic_write_json  # type: ignore
except ModuleNotFoundError:
    import json as _json
    def _safe_atomic_write_json(path, obj, mode=0o640):  # type: ignore[no-redef]
        path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(obj, "model_dump_json"):
            content = obj.model_dump_json(indent=2)
        else:
            content = _json.dumps(obj, indent=2, default=str)
        path.write_text(content, encoding="utf-8")

def atomic_write_json(path, obj, mode=0o640):
    _safe_atomic_write_json(path, obj, mode=mode)

from .audit import emit
from .exceptions import CommerceError

CART_IDLE_TTL = timedelta(hours=4)


@dataclass(frozen=True)
class CartOpResult:
    ok: bool
    cart: Optional[CommerceCart]
    detail: str = ""


def load_cart_store(path: Path) -> CommerceCartStore:
    if not path.exists():
        return CommerceCartStore()
    import json
    return CommerceCartStore.model_validate(json.loads(path.read_text(encoding="utf-8")))


def write_cart_store(path: Path, store: CommerceCartStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, store)


def _normalize_phone(sender_phone: Optional[str]) -> Optional[str]:
    if sender_phone is None or sender_phone == "":
        return None
    try:
        return str(E164Phone.from_any(sender_phone, country_code="US"))
    except ValueError:
        return None


def _find_open(
    store: CommerceCartStore,
    sender_phone: Optional[str],
    sender_lid: Optional[str],
    chat_id: str,
) -> Optional[CommerceCart]:
    canonical_phone = _normalize_phone(sender_phone)
    for cart in store.carts:
        if cart.status != "open":
            continue
        if cart.chat_id != chat_id:
            continue
        phone_match = (
            canonical_phone is not None
            and cart.sender_phone is not None
            and str(cart.sender_phone) == canonical_phone
        )
        lid_match = (
            sender_lid is not None
            and cart.sender_lid is not None
            and cart.sender_lid == sender_lid
        )
        if phone_match or lid_match:
            return cart
    return None


def _next_cart_id(store: CommerceCartStore) -> str:
    n = 1
    used = {c.cart_id for c in store.carts}
    while True:
        candidate = f"CC{n:05d}"
        if candidate not in used:
            return candidate
        n += 1


def _refresh_expiry(cart: CommerceCart, now: datetime) -> CommerceCart:
    return cart.model_copy(
        update={
            "updated_at": now,
            "expires_at": now + CART_IDLE_TTL,
            "subtotal_cents": sum(item.line_total_cents for item in cart.items),
        }
    )


def add_item(
    *,
    state_path: Path,
    decisions_log_path: Path,
    sender_phone: Optional[str],
    sender_lid: Optional[str],
    chat_id: str,
    sku: str,
    display_name: str,
    quantity: int,
    unit: str,
    unit_price_cents: int,
    currency: str = "USD",
    now: Optional[datetime] = None,
) -> CartOpResult:
    if quantity <= 0:
        return CartOpResult(False, None, "quantity_must_be_positive")
    if unit_price_cents <= 0:
        return CartOpResult(False, None, "unit_price_must_be_positive")
    now = now or datetime.now(timezone.utc)
    store = load_cart_store(state_path)
    cart = _find_open(store, sender_phone, sender_lid, chat_id)

    canonical_phone = _normalize_phone(sender_phone)
    if cart is None:
        cart_id = _next_cart_id(store)
        cart = CommerceCart(
            cart_id=cart_id,
            sender_phone=canonical_phone,
            sender_lid=sender_lid,
            chat_id=chat_id,
            items=[],
            subtotal_cents=0,
            currency=currency,
            status="open",
            created_at=now,
            updated_at=now,
            expires_at=now + CART_IDLE_TTL,
        )
        store.carts.append(cart)
        op = "add"
        emit(
            decisions_log_path,
            {
                "type": "commerce_cart_started",
                "ts": now.isoformat(),
                "cart_id": cart_id,
                "sender_phone": canonical_phone,
                "sender_lid": sender_lid,
                "chat_id": chat_id,
            },
        )
    else:
        op = "add"

    if cart.currency != currency:
        return CartOpResult(False, cart, "currency_mismatch")

    qty_before = 0
    new_items = []
    found = False
    for item in cart.items:
        if item.sku == sku:
            qty_before = item.quantity
            new_qty = item.quantity + quantity
            new_items.append(
                item.model_copy(
                    update={
                        "quantity": new_qty,
                        "line_total_cents": new_qty * item.unit_price_cents,
                    }
                )
            )
            found = True
        else:
            new_items.append(item)
    if not found:
        new_items.append(
            CommerceCartItem(
                sku=sku,
                display_name=display_name,
                quantity=quantity,
                unit=unit,
                unit_price_cents=unit_price_cents,
                line_total_cents=quantity * unit_price_cents,
                added_at=now,
            )
        )

    updated = cart.model_copy(update={"items": new_items})
    updated = _refresh_expiry(updated, now)
    _replace(store, updated)
    write_cart_store(state_path, store)

    emit(
        decisions_log_path,
        {
            "type": "commerce_cart_updated",
            "ts": now.isoformat(),
            "cart_id": updated.cart_id,
            "op": op,
            "sku": sku,
            "qty_before": qty_before,
            "qty_after": qty_before + quantity,
            "subtotal_cents": updated.subtotal_cents,
        },
    )
    return CartOpResult(True, updated)


def remove_item(
    *,
    state_path: Path,
    decisions_log_path: Path,
    cart_id: str,
    sku: str,
    now: Optional[datetime] = None,
) -> CartOpResult:
    now = now or datetime.now(timezone.utc)
    store = load_cart_store(state_path)
    cart = next((c for c in store.carts if c.cart_id == cart_id), None)
    if cart is None or cart.status != "open":
        return CartOpResult(False, cart, "cart_not_open")
    qty_before = next((i.quantity for i in cart.items if i.sku == sku), 0)
    if qty_before == 0:
        return CartOpResult(False, cart, "sku_not_in_cart")
    new_items = [i for i in cart.items if i.sku != sku]
    updated = _refresh_expiry(cart.model_copy(update={"items": new_items}), now)
    _replace(store, updated)
    write_cart_store(state_path, store)
    emit(
        decisions_log_path,
        {
            "type": "commerce_cart_updated",
            "ts": now.isoformat(),
            "cart_id": updated.cart_id,
            "op": "remove",
            "sku": sku,
            "qty_before": qty_before,
            "qty_after": 0,
            "subtotal_cents": updated.subtotal_cents,
        },
    )
    return CartOpResult(True, updated)


def update_qty(
    *,
    state_path: Path,
    decisions_log_path: Path,
    cart_id: str,
    sku: str,
    quantity: int,
    now: Optional[datetime] = None,
) -> CartOpResult:
    if quantity < 0:
        return CartOpResult(False, None, "quantity_must_be_non_negative")
    if quantity == 0:
        return remove_item(
            state_path=state_path,
            decisions_log_path=decisions_log_path,
            cart_id=cart_id,
            sku=sku,
            now=now,
        )
    now = now or datetime.now(timezone.utc)
    store = load_cart_store(state_path)
    cart = next((c for c in store.carts if c.cart_id == cart_id), None)
    if cart is None or cart.status != "open":
        return CartOpResult(False, cart, "cart_not_open")
    qty_before = 0
    new_items = []
    found = False
    for item in cart.items:
        if item.sku == sku:
            qty_before = item.quantity
            new_items.append(
                item.model_copy(
                    update={
                        "quantity": quantity,
                        "line_total_cents": quantity * item.unit_price_cents,
                    }
                )
            )
            found = True
        else:
            new_items.append(item)
    if not found:
        return CartOpResult(False, cart, "sku_not_in_cart")
    updated = _refresh_expiry(cart.model_copy(update={"items": new_items}), now)
    _replace(store, updated)
    write_cart_store(state_path, store)
    emit(
        decisions_log_path,
        {
            "type": "commerce_cart_updated",
            "ts": now.isoformat(),
            "cart_id": updated.cart_id,
            "op": "update_qty",
            "sku": sku,
            "qty_before": qty_before,
            "qty_after": quantity,
            "subtotal_cents": updated.subtotal_cents,
        },
    )
    return CartOpResult(True, updated)


def clear(
    *,
    state_path: Path,
    decisions_log_path: Path,
    cart_id: str,
    reason: str = "operator_clear",
    now: Optional[datetime] = None,
) -> CartOpResult:
    now = now or datetime.now(timezone.utc)
    store = load_cart_store(state_path)
    cart = next((c for c in store.carts if c.cart_id == cart_id), None)
    if cart is None:
        return CartOpResult(False, None, "cart_not_found")
    if cart.status != "open":
        return CartOpResult(False, cart, "cart_not_open")
    updated = cart.model_copy(update={"status": "cleared", "updated_at": now})
    _replace(store, updated)
    write_cart_store(state_path, store)
    emit(
        decisions_log_path,
        {
            "type": "commerce_cart_cleared",
            "ts": now.isoformat(),
            "cart_id": cart_id,
            "reason": reason,
        },
    )
    return CartOpResult(True, updated)


def expire_idle(
    *,
    state_path: Path,
    decisions_log_path: Path,
    now: Optional[datetime] = None,
) -> int:
    """Cron entry point: mark `open` carts past expires_at as `expired`."""
    now = now or datetime.now(timezone.utc)
    store = load_cart_store(state_path)
    n = 0
    updated_carts = []
    for cart in store.carts:
        if cart.status == "open" and cart.expires_at <= now:
            expired = cart.model_copy(update={"status": "expired", "updated_at": now})
            updated_carts.append(expired)
            emit(
                decisions_log_path,
                {
                    "type": "commerce_cart_expired",
                    "ts": now.isoformat(),
                    "cart_id": cart.cart_id,
                    "expired_at": cart.expires_at.isoformat(),
                },
            )
            n += 1
        else:
            updated_carts.append(cart)
    if n:
        store.carts = updated_carts
        write_cart_store(state_path, store)
    return n


def checkout(
    *,
    state_path: Path,
    decisions_log_path: Path,
    cart_id: str,
    order_id: str,
    now: Optional[datetime] = None,
) -> CartOpResult:
    """Mark cart as checked_out; called by order_state.create after order is built."""
    now = now or datetime.now(timezone.utc)
    store = load_cart_store(state_path)
    cart = next((c for c in store.carts if c.cart_id == cart_id), None)
    if cart is None:
        return CartOpResult(False, None, "cart_not_found")
    if cart.status != "open":
        return CartOpResult(False, cart, "cart_not_open")
    if not cart.items:
        return CartOpResult(False, cart, "cart_empty")
    updated = cart.model_copy(update={"status": "checked_out", "updated_at": now})
    _replace(store, updated)
    write_cart_store(state_path, store)
    emit(
        decisions_log_path,
        {
            "type": "commerce_cart_checked_out",
            "ts": now.isoformat(),
            "cart_id": cart_id,
            "order_id": order_id,
            "subtotal_cents": cart.subtotal_cents,
        },
    )
    return CartOpResult(True, updated)


def _replace(store: CommerceCartStore, cart: CommerceCart) -> None:
    store.carts = [cart if c.cart_id == cart.cart_id else c for c in store.carts]
