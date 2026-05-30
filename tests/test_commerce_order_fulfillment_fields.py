"""Slice A — additive pickup/delivery fulfillment fields on CommerceOrder.

Verifies the new fields (fulfillment_type, customer_name, delivery_address,
requested_time, order_notes, pos_sync_status) are additive, default-safe, and
backward-compatible with orders stored BEFORE Slice A, and that extra="forbid"
+ the existing sender-identity validator still hold. No runtime behavior change
(the order substrate stays config-inactive; these fields are populated by later
slices).
"""
from __future__ import annotations

import platform
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="commerce order_state uses safe_io fcntl (Linux only)",
)

from pydantic import ValidationError
from schemas import CommerceCart, CommerceCartItem, CommerceOrder, CommerceOrderStore

from commerce import order_state as commerce_order

_TS = datetime(2026, 5, 30, tzinfo=timezone.utc)


def _order_kwargs(**overrides):
    base = dict(
        order_id="CO00001",
        sender_phone="+19045550123",
        chat_id="19045550123@s.whatsapp.net",
        cart_id="CC00001",
        line_items=[],
        subtotal_cents=0,
        total_cents=0,
        currency="USD",
        status="pending_payment",
        created_at=_TS,
        updated_at=_TS,
    )
    base.update(overrides)
    return base


# ── defaults / dormancy ─────────────────────────────────────────────────────

def test_new_fields_default_safe_when_omitted():
    o = CommerceOrder(**_order_kwargs())
    assert o.fulfillment_type is None
    assert o.customer_name is None
    assert o.delivery_address is None
    assert o.requested_time is None
    assert o.order_notes is None
    assert o.pos_sync_status == "not_synced"


# ── back-compat: an order stored BEFORE Slice A (no fulfillment keys) loads ──

def test_pre_sliceA_stored_order_loads_with_defaults():
    legacy = {
        "order_id": "CO00009", "sender_phone": "+19045550199",
        "chat_id": "19045550199@s.whatsapp.net", "cart_id": "CC00009",
        "line_items": [], "subtotal_cents": 1200, "tax_cents": 0, "fee_cents": 0,
        "total_cents": 1200, "currency": "USD", "status": "paid",
        "payment_intent_id": "", "payment_reference": "", "status_history": [],
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-05-01T00:00:00+00:00",
    }
    store = CommerceOrderStore.model_validate({"orders": [legacy]})
    o = store.orders[0]
    assert o.pos_sync_status == "not_synced"
    assert o.fulfillment_type is None
    assert o.delivery_address is None
    assert o.requested_time is None


# ── set values round-trip through the on-disk store ─────────────────────────

def test_fulfillment_fields_round_trip(tmp_state_dir: Path):
    path = tmp_state_dir / "commerce" / "orders.json"
    o = CommerceOrder(**_order_kwargs(
        order_id="CO00002", cart_id="CC00002",
        fulfillment_type="delivery",
        customer_name="Asha",
        delivery_address="123 Main St, Apt 4, Jacksonville FL 32256",
        requested_time=datetime(2026, 5, 30, 18, 30, tzinfo=timezone.utc),
        order_notes="leave at door; no onions",
        pos_sync_status="pending",
    ))
    commerce_order.write_order_store(path, CommerceOrderStore(orders=[o]))
    g = commerce_order.load_order_store(path).orders[0]
    assert g.fulfillment_type == "delivery"
    assert g.customer_name == "Asha"
    assert g.delivery_address.startswith("123 Main St")
    assert g.requested_time == datetime(2026, 5, 30, 18, 30, tzinfo=timezone.utc)
    assert g.order_notes == "leave at door; no onions"
    assert g.pos_sync_status == "pending"


def test_pickup_fulfillment_value_accepted():
    o = CommerceOrder(**_order_kwargs(fulfillment_type="pickup"))
    assert o.fulfillment_type == "pickup"


# ── Literal validation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["dinein", "shipping", "Pickup", ""])
def test_invalid_fulfillment_type_rejected(bad):
    with pytest.raises(ValidationError):
        CommerceOrder(**_order_kwargs(fulfillment_type=bad))


@pytest.mark.parametrize("bad", ["synced_ok", "SYNCED", "done", ""])
def test_invalid_pos_sync_status_rejected(bad):
    with pytest.raises(ValidationError):
        CommerceOrder(**_order_kwargs(pos_sync_status=bad))


def test_pos_sync_status_all_legal_values():
    for v in ("not_synced", "pending", "synced", "failed", "n/a"):
        assert CommerceOrder(**_order_kwargs(pos_sync_status=v)).pos_sync_status == v


# ── existing invariants still hold ──────────────────────────────────────────

def test_extra_forbid_still_rejects_unknown_field():
    with pytest.raises(ValidationError):
        CommerceOrder(**_order_kwargs(some_unknown_field="x"))


def test_sender_identity_validator_still_enforced():
    kw = _order_kwargs()
    kw.pop("sender_phone", None)
    with pytest.raises(ValidationError):
        CommerceOrder(**kw)  # neither sender_phone nor sender_lid


def test_overlong_delivery_address_rejected():
    with pytest.raises(ValidationError):
        CommerceOrder(**_order_kwargs(delivery_address="x" * 501))


# ── create() (config-inactive substrate) still builds orders, new fields default ──

def test_create_defaults_new_fields(tmp_state_dir: Path):
    order_path = tmp_state_dir / "commerce" / "orders.json"
    log_path = tmp_state_dir / "logs" / "decisions.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    item = CommerceCartItem(
        sku="samosa", display_name="Samosa", quantity=2, unit="each",
        unit_price_cents=300, line_total_cents=600, added_at=_TS,
    )
    cart = CommerceCart(
        cart_id="CC00050", sender_phone="+19045550150",
        chat_id="19045550150@s.whatsapp.net", items=[item],
        subtotal_cents=600, currency="USD", status="open",
        created_at=_TS, updated_at=_TS,
        expires_at=datetime(2026, 5, 30, 4, tzinfo=timezone.utc),
    )
    res = commerce_order.create(
        state_path=order_path, decisions_log_path=log_path, cart=cart,
    )
    assert res.ok, res.detail
    assert res.order is not None
    assert res.order.fulfillment_type is None
    assert res.order.pos_sync_status == "not_synced"
    assert res.order.delivery_address is None
