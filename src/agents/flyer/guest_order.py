"""Payment-first guest orders for one-off Flyer Studio buyers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
from typing import Optional

from schemas import E164Phone, FlyerGuestOrder, FlyerGuestOrderStore

try:
    if os.name == "nt":
        raise ModuleNotFoundError("use simple atomic write fallback on Windows")
    from safe_io import atomic_write_text  # type: ignore
except ModuleNotFoundError:
    def atomic_write_text(path: Path, text: str) -> None:  # type: ignore[no-redef]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class GuestOrderResult:
    ok: bool
    handled: bool
    reply_text: str
    order_id: str = ""
    status: str = ""
    payment_checkout_url: str = ""
    detail: str = ""


def load_guest_order_store(path: Path) -> FlyerGuestOrderStore:
    if not path.exists():
        return FlyerGuestOrderStore()
    return FlyerGuestOrderStore.model_validate(json.loads(path.read_text(encoding="utf-8")))


def write_guest_order_store(path: Path, store: FlyerGuestOrderStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, store.model_dump_json(indent=2))


def start_guest_order(
    *,
    state_path: Path,
    sender_phone: str,
    chat_id: str,
    message_id: str,
    checkout_url_template: str = "",
    fallback_checkout_url_template: str = "",
    unit_price_cents: int = 400,
    currency: str = "USD",
    payment_provider: str = "manual",
    now: Optional[datetime] = None,
) -> GuestOrderResult:
    now = now or datetime.now(timezone.utc)
    store = load_guest_order_store(state_path)
    existing = store.find_open_order_by_sender(sender_phone, chat_id)
    if existing:
        return GuestOrderResult(
            True,
            True,
            _reply_for_order(existing),
            existing.order_id,
            existing.status,
            existing.payment_checkout_url,
        )

    order_id = store.next_order_id()
    checkout_url = _checkout_url(
        checkout_url_template or fallback_checkout_url_template,
        order_id=order_id,
        chat_id=chat_id,
        amount_cents=unit_price_cents,
        currency=currency,
    )
    order = store.new_order(
        sender_phone=sender_phone,
        chat_id=chat_id,
        message_id=message_id,
        now=now,
        unit_price_cents=unit_price_cents,
        currency=currency,
        payment_provider=payment_provider,
        checkout_url=checkout_url,
    )
    store.orders.append(order)
    write_guest_order_store(state_path, store)
    return GuestOrderResult(True, True, _reply_for_order(order), order.order_id, order.status, order.payment_checkout_url)


def activate_guest_order(
    *,
    state_path: Path,
    order_id: str = "",
    sender_phone: str = "",
    provider: str = "manual",
    payment_reference: str,
    amount_cents: Optional[int] = None,
    currency: str = "",
    now: Optional[datetime] = None,
) -> GuestOrderResult:
    now = now or datetime.now(timezone.utc)
    if provider not in {"manual", "stripe", "razorpay", "other"}:
        return GuestOrderResult(False, True, "", detail="invalid_provider")
    store = load_guest_order_store(state_path)
    order = store.find_order_by_id(order_id) if order_id else store.find_open_order_by_sender(sender_phone)
    if order is None:
        return GuestOrderResult(False, True, "", detail="guest_order_not_found")
    expected_amount = order.unit_price_cents * max(1, order.flyer_count_purchased)
    expected_currency = (order.currency or "USD").upper()
    check_currency = (currency or expected_currency).upper()
    if check_currency != expected_currency:
        return GuestOrderResult(False, True, "", order.order_id, order.status, detail="currency_mismatch")
    if amount_cents is not None and amount_cents != expected_amount:
        return GuestOrderResult(False, True, "", order.order_id, order.status, detail="amount_mismatch")
    if provider != "manual" and amount_cents is None:
        return GuestOrderResult(False, True, "", order.order_id, order.status, detail="amount_cents_required")
    payment_reference = " ".join((payment_reference or "").split())
    if not payment_reference:
        return GuestOrderResult(False, True, "", order.order_id, order.status, detail="payment_reference_required")
    if any(
        other.payment_reference == payment_reference
        and other.payment_provider == provider
        and other.order_id != order.order_id
        for other in store.orders
        if other.payment_reference
    ):
        return GuestOrderResult(False, True, "", order.order_id, order.status, detail="payment_reference_already_used")
    if order.status == "paid":
        replay_amount = amount_cents if amount_cents is not None else order.payment_amount_cents
        same = (
            order.payment_provider == provider
            and order.payment_reference == payment_reference
            and (order.payment_amount_cents == replay_amount or (order.payment_amount_cents is None and replay_amount == expected_amount))
            and (order.currency or "USD").upper() == check_currency
        )
        if not same:
            return GuestOrderResult(False, True, "", order.order_id, order.status, detail="payment_reference_replay_mismatch")
        return GuestOrderResult(True, True, _reply_for_order(order), order.order_id, order.status, order.payment_checkout_url)
    if order.status not in {"pending_payment"}:
        return GuestOrderResult(False, True, "", order.order_id, order.status, detail=f"cannot_activate_{order.status}")
    updated = order.model_copy(update={
        "status": "paid",
        "payment_provider": provider,
        "payment_reference": payment_reference,
        "payment_amount_cents": amount_cents if amount_cents is not None else expected_amount,
        "paid_at": now,
        "updated_at": now,
    })
    _replace_order(store, updated)
    write_guest_order_store(state_path, store)
    return GuestOrderResult(True, True, _reply_for_order(updated), updated.order_id, updated.status, updated.payment_checkout_url)


def reserve_guest_order(
    *,
    state_path: Path,
    sender_phone: str,
    chat_id: str,
    project_id: str,
    now: Optional[datetime] = None,
) -> GuestOrderResult:
    now = now or datetime.now(timezone.utc)
    store = load_guest_order_store(state_path)
    order = store.find_paid_order_by_sender(sender_phone, chat_id)
    if order is None:
        return GuestOrderResult(False, True, "", detail="paid_guest_order_not_found")
    updated = order.model_copy(update={
        "status": "reserved",
        "reserved_project_id": project_id,
        "updated_at": now,
    })
    _replace_order(store, updated)
    write_guest_order_store(state_path, store)
    return GuestOrderResult(True, True, "", updated.order_id, updated.status, updated.payment_checkout_url)


def release_guest_order(
    *,
    state_path: Path,
    sender_phone: str,
    chat_id: str,
    project_id: str,
    now: Optional[datetime] = None,
) -> GuestOrderResult:
    now = now or datetime.now(timezone.utc)
    store = load_guest_order_store(state_path)
    order = _find_reserved_guest_order(store, sender_phone=sender_phone, chat_id=chat_id, project_id=project_id)
    if order is None:
        return GuestOrderResult(False, True, "", detail="reserved_guest_order_not_found")
    updated = order.model_copy(update={
        "status": "paid",
        "reserved_project_id": "",
        "updated_at": now,
    })
    _replace_order(store, updated)
    write_guest_order_store(state_path, store)
    return GuestOrderResult(True, True, "", updated.order_id, updated.status, updated.payment_checkout_url)


def consume_guest_order(
    *,
    state_path: Path,
    sender_phone: str,
    chat_id: str,
    project_id: str,
    now: Optional[datetime] = None,
) -> GuestOrderResult:
    now = now or datetime.now(timezone.utc)
    store = load_guest_order_store(state_path)
    # BUG-FLYER-QA-001: idempotent replay. After a successful first consume
    # the order's status flips to 'used' or 'paid' and reserved_project_id is
    # cleared, so _find_reserved_guest_order can no longer locate it. Match
    # on used_project_ids first so a replay returns the same success tuple.
    replayed = _find_consumed_guest_order(
        store, sender_phone=sender_phone, chat_id=chat_id, project_id=project_id,
    )
    if replayed is not None:
        return GuestOrderResult(
            True, True, "",
            replayed.order_id, replayed.status, replayed.payment_checkout_url,
        )
    order = _find_reserved_guest_order(store, sender_phone=sender_phone, chat_id=chat_id, project_id=project_id)
    if order is None:
        return GuestOrderResult(False, True, "", detail="reserved_guest_order_not_found")
    used_project_ids = list(order.used_project_ids)
    if project_id in used_project_ids:
        return GuestOrderResult(True, True, "", order.order_id, order.status, order.payment_checkout_url)
    used_project_ids.append(project_id)
    used = min(order.flyer_count_purchased, max(order.flyer_count_used + 1, len(used_project_ids)))
    status = "used" if used >= order.flyer_count_purchased else "paid"
    updated = order.model_copy(update={
        "status": status,
        "flyer_count_used": used,
        "used_project_ids": used_project_ids,
        "reserved_project_id": "",
        "updated_at": now,
    })
    _replace_order(store, updated)
    write_guest_order_store(state_path, store)
    return GuestOrderResult(True, True, "", updated.order_id, updated.status, updated.payment_checkout_url)


def find_paid_guest_order(*, state_path: Path, sender_phone: str, chat_id: str) -> Optional[FlyerGuestOrder]:
    return load_guest_order_store(state_path).find_paid_order_by_sender(sender_phone, chat_id)


def find_reserved_guest_order(*, state_path: Path, sender_phone: str, chat_id: str, project_id: str) -> Optional[FlyerGuestOrder]:
    store = load_guest_order_store(state_path)
    return _find_reserved_guest_order(store, sender_phone=sender_phone, chat_id=chat_id, project_id=project_id)


def _reply_for_order(order: FlyerGuestOrder) -> str:
    price = f"${order.unit_price_cents / 100:.2f}".rstrip("0").rstrip(".")
    if order.status == "paid":
        return (
            "Flyer Studio\n------------\n"
            f"Payment received for {order.order_id}. Send the flyer details now.\n\n"
            "You can send text, logo, photos, menu, or a sample flyer."
        )
    if order.status == "pending_payment":
        payment_line = f"Pay here: {order.payment_checkout_url}" if order.payment_checkout_url else "Payment link is not configured yet. I will send it here when it is ready."
        return (
            "Flyer Studio\n------------\n"
            f"Create one professional flyer for {price}.\n"
            "No monthly plan. No setup required.\n\n"
            f"{payment_line}\n\n"
            "After payment, send your flyer details, logo, photos, menu, or sample flyer."
        )
    return "Flyer Studio\n------------\nThis quick flyer order is already complete."


def _replace_order(store: FlyerGuestOrderStore, order: FlyerGuestOrder) -> None:
    store.orders = [order if existing.order_id == order.order_id else existing for existing in store.orders]


def _find_consumed_guest_order(
    store: FlyerGuestOrderStore,
    *,
    sender_phone: str,
    chat_id: str,
    project_id: str,
) -> Optional[FlyerGuestOrder]:
    """Return an order that already consumed `project_id` for this sender.

    Used to make `consume_guest_order` idempotent on replay (BUG-FLYER-QA-001).
    After a successful first consume the order's status becomes 'used' or 'paid'
    and reserved_project_id is cleared, so `_find_reserved_guest_order` cannot
    locate it on a retry. This helper matches on `project_id in used_project_ids`
    instead, scoped to the same sender + chat (with a chat_id fallback that
    mirrors `_find_reserved_guest_order` so cross-chat replays still resolve).

    Excludes orders where `reserved_project_id == project_id AND
    status == "reserved"` — those represent in-flight first consumes, not
    replays. A freshly reserved order has empty `used_project_ids` so this
    guard is redundant in practice; it stays as a defensive belt-and-braces.
    """
    try:
        canonical = E164Phone.from_any(sender_phone, country_code="US")
    except ValueError:
        return None

    def is_replay(order: FlyerGuestOrder) -> bool:
        if project_id not in order.used_project_ids:
            return False
        if order.status == "reserved" and order.reserved_project_id == project_id:
            return False
        return order.sender_phone == canonical

    matches = [
        order for order in store.orders
        if is_replay(order) and (not chat_id or order.chat_id == chat_id)
    ]
    if not matches and chat_id:
        matches = [order for order in store.orders if is_replay(order)]
    if not matches:
        return None
    return max(matches, key=lambda order: order.updated_at)


def _find_reserved_guest_order(
    store: FlyerGuestOrderStore,
    *,
    sender_phone: str,
    chat_id: str,
    project_id: str,
) -> Optional[FlyerGuestOrder]:
    try:
        canonical = E164Phone.from_any(sender_phone, country_code="US")
    except ValueError:
        return None
    matches = [
        order for order in store.orders
        if order.status == "reserved"
        and order.sender_phone == canonical
        and order.reserved_project_id == project_id
        and (not chat_id or order.chat_id == chat_id)
    ]
    if not matches and chat_id:
        matches = [
            order for order in store.orders
            if order.status == "reserved"
            and order.sender_phone == canonical
            and order.reserved_project_id == project_id
        ]
    if not matches:
        return None
    return max(matches, key=lambda order: order.updated_at)


def _checkout_url(template: str, *, order_id: str, chat_id: str, amount_cents: int, currency: str) -> str:
    if not template:
        return ""
    try:
        return template.format(
            order_id=order_id,
            customer_id=order_id,
            plan_id="quick_flyer",
            chat_id=chat_id,
            amount_cents=amount_cents,
            price_usd=f"{amount_cents / 100:.2f}",
            currency=currency,
        )
    except (KeyError, IndexError, ValueError):
        return ""
