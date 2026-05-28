"""Tests for the new commerce_* LogEntry variants in schemas.py.

Each variant accepts its canonical field set and rejects extras
(extra="forbid" discipline). Mirrors the existing test_log_entry_*
patterns.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError, TypeAdapter

from schemas import (
    LogEntry,
    CommerceCartStarted,
    CommerceCartUpdated,
    CommerceCartCleared,
    CommerceCartExpired,
    CommerceCartCheckedOut,
    CommerceOrderCreated,
    CommerceOrderStatusChange,
    CommerceOrderCancelled,
    CommerceOrderCreateRefusedCategory,
    CommercePaymentIntentMinted,
    CommercePaymentLinkAttempted,
    CommercePaymentLinkSent,
    CommercePaymentLinkFailed,
    CommercePaymentIntentVoided,
    CommercePaymentConfirmed,
    CommercePaymentDedupBlocked,
    CommercePaymentRefunded,
    CommercePaymentChargebackReceived,
    CommerceOrderOwnerApprovalRequired,
    CommerceOrderOwnerApprovalThresholdUnconfigured,
    CommerceBlockedCategoryOverride,
)


TS = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
ADAPTER = TypeAdapter(LogEntry)


def _round_trip(payload: dict) -> dict:
    """Validate via the discriminated union and return the parsed dict."""
    parsed = ADAPTER.validate_python(payload)
    return parsed.model_dump(mode="json")


def test_commerce_cart_started_round_trip():
    obj = CommerceCartStarted(
        type="commerce_cart_started", ts=TS,
        cart_id="CC00001", sender_phone="+15551234567",
        sender_lid=None, chat_id="chat",
    )
    assert obj.cart_id == "CC00001"
    _round_trip({
        "type": "commerce_cart_started", "ts": TS.isoformat(),
        "cart_id": "CC00001", "sender_phone": "+15551234567",
        "sender_lid": None, "chat_id": "chat",
    })


def test_commerce_cart_updated_extra_forbidden():
    with pytest.raises(ValidationError):
        CommerceCartUpdated(
            type="commerce_cart_updated", ts=TS,
            cart_id="CC00001", op="add", sku="A",
            qty_before=0, qty_after=1, subtotal_cents=100,
            extra_field="boom",  # extra="forbid"
        )


def test_commerce_order_status_change_ts_required():
    with pytest.raises(ValidationError):
        CommerceOrderStatusChange(
            type="commerce_order_status_change",
            order_id="CO00001",
            prev_status="pending_payment",
            next_status="paid",
            actor="webhook",
            cause="x",
        )  # missing ts


def test_commerce_payment_dedup_blocked_required_fields():
    obj = CommercePaymentDedupBlocked(
        type="commerce_payment_dedup_blocked", ts=TS,
        reference="stripe_pi_abc",
        attempted_order_id="CO00002",
        original_order_id="CO00001",
    )
    assert obj.original_order_id == "CO00001"


def test_commerce_payment_refunded_default_is_partial_false():
    obj = CommercePaymentRefunded(
        type="commerce_payment_refunded", ts=TS,
        intent_id="CPI00001", order_id="CO00001",
        refund_reference="rf_xyz", amount_cents=2599,
    )
    assert obj.is_partial is False


def test_commerce_payment_chargeback_arrived_after_refund_flag():
    obj = CommercePaymentChargebackReceived(
        type="commerce_payment_chargeback_received", ts=TS,
        intent_id="CPI00001", order_id="CO00001",
        provider_reference="cb_xyz", amount_cents=2599,
        arrived_after_refund=True,
    )
    assert obj.arrived_after_refund is True


def test_all_variants_discriminated_union_round_trips():
    variants = [
        {"type": "commerce_cart_started", "ts": TS.isoformat(),
         "cart_id": "CC00001", "chat_id": "c", "sender_lid": "l@lid"},
        {"type": "commerce_cart_updated", "ts": TS.isoformat(),
         "cart_id": "CC00001", "op": "add", "sku": "A",
         "qty_before": 0, "qty_after": 1, "subtotal_cents": 100},
        {"type": "commerce_cart_cleared", "ts": TS.isoformat(),
         "cart_id": "CC00001", "reason": "operator"},
        {"type": "commerce_cart_expired", "ts": TS.isoformat(),
         "cart_id": "CC00001", "expired_at": TS.isoformat()},
        {"type": "commerce_cart_checked_out", "ts": TS.isoformat(),
         "cart_id": "CC00001", "order_id": "CO00001", "subtotal_cents": 100},
        {"type": "commerce_order_created", "ts": TS.isoformat(),
         "order_id": "CO00001", "cart_id": "CC00001",
         "sender_lid": "l@lid", "total_cents": 100, "currency": "USD"},
        {"type": "commerce_order_status_change", "ts": TS.isoformat(),
         "order_id": "CO00001", "prev_status": "pending_payment",
         "next_status": "paid", "actor": "webhook", "cause": "paid"},
        {"type": "commerce_order_cancelled", "ts": TS.isoformat(),
         "order_id": "CO00001", "reason": "x", "actor": "operator"},
        {"type": "commerce_order_create_refused_category", "ts": TS.isoformat(),
         "sender_lid": "l@lid", "refused_skus": ["A"], "reason": "restricted_category"},
        {"type": "commerce_payment_intent_minted", "ts": TS.isoformat(),
         "intent_id": "CPI00001", "order_id": "CO00001",
         "amount_cents": 100, "currency": "USD", "provider": "placeholder"},
        {"type": "commerce_payment_link_attempted", "ts": TS.isoformat(),
         "intent_id": "CPI00001", "order_id": "CO00001"},
        {"type": "commerce_payment_link_sent", "ts": TS.isoformat(),
         "intent_id": "CPI00001", "order_id": "CO00001"},
        {"type": "commerce_payment_intent_voided", "ts": TS.isoformat(),
         "intent_id": "CPI00001", "order_id": "CO00001",
         "reason": "x", "actor": "operator"},
        {"type": "commerce_payment_dedup_blocked", "ts": TS.isoformat(),
         "reference": "r", "attempted_order_id": "CO00002", "original_order_id": "CO00001"},
    ]
    for v in variants:
        parsed = ADAPTER.validate_python(v)
        assert parsed.type == v["type"]
