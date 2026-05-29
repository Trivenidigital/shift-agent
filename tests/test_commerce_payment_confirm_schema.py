"""Slice-3 PR-2 schema-only tests for the new LogEntry variants.

Covers:
- CateringDepositPaid round-trip + pattern validation
- CommercePaymentConfirmationFailed round-trip + reason Literal coverage
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from schemas import (
    CateringDepositPaid,
    CommercePaymentConfirmationFailed,
    LogEntry,
)


TS = datetime(2026, 5, 29, 19, 0, tzinfo=timezone.utc)
ADAPTER = TypeAdapter(LogEntry)


# ─────────────────────────────────────────────────────────────────
# CateringDepositPaid
# ─────────────────────────────────────────────────────────────────

def test_catering_deposit_paid_round_trip():
    obj = CateringDepositPaid(
        type="catering_deposit_paid", ts=TS,
        lead_id="L0007",
        commerce_order_id="CO00042",
        commerce_payment_intent_id="CPI00042",
        payment_reference="pi_test_abc123",
        amount_cents=15000,
    )
    raw = obj.model_dump_json()
    parsed = ADAPTER.validate_python(json.loads(raw))
    assert parsed.type == "catering_deposit_paid"
    assert parsed.commerce_order_id == "CO00042"
    assert parsed.payment_reference == "pi_test_abc123"


def test_catering_deposit_paid_rejects_malformed_order_id():
    with pytest.raises(ValidationError):
        CateringDepositPaid(
            type="catering_deposit_paid", ts=TS,
            lead_id="L0007",
            commerce_order_id="not-an-order-id",
            commerce_payment_intent_id="CPI00042",
            payment_reference="pi_test",
            amount_cents=15000,
        )


def test_catering_deposit_paid_rejects_empty_payment_reference():
    """2026-05-25 lesson: must fail closed without nonblank payment_reference."""
    with pytest.raises(ValidationError):
        CateringDepositPaid(
            type="catering_deposit_paid", ts=TS,
            lead_id="L0007",
            commerce_order_id="CO00042",
            commerce_payment_intent_id="CPI00042",
            payment_reference="",  # min_length=1
            amount_cents=15000,
        )


def test_catering_deposit_paid_extras_forbidden():
    with pytest.raises(ValidationError):
        CateringDepositPaid(
            type="catering_deposit_paid", ts=TS,
            lead_id="L0007",
            commerce_order_id="CO00042",
            commerce_payment_intent_id="CPI00042",
            payment_reference="pi_test",
            amount_cents=15000,
            extra_field="boom",
        )


# ─────────────────────────────────────────────────────────────────
# CommercePaymentConfirmationFailed
# ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("reason", [
    "signature_invalid",
    "empty_payment_reference",
    "missing_metadata",
    "intent_not_found",
    "currency_mismatch",
    "amount_mismatch",
    "reference_reused",
    "mark_confirmed_failed",
    "config_load_failed",
])
def test_confirmation_failed_round_trip_all_reasons(reason):
    obj = CommercePaymentConfirmationFailed(
        type="commerce_payment_confirmation_failed", ts=TS,
        reason=reason,
        detail=f"reason={reason}",
    )
    raw = obj.model_dump_json()
    parsed = ADAPTER.validate_python(json.loads(raw))
    assert parsed.reason == reason


def test_confirmation_failed_rejects_invalid_reason():
    with pytest.raises(ValidationError):
        CommercePaymentConfirmationFailed(
            type="commerce_payment_confirmation_failed", ts=TS,
            reason="this_reason_is_not_in_the_literal",
        )


def test_confirmation_failed_all_cross_ref_fields_optional():
    """For signature_invalid / missing_metadata, the intent + order IDs
    may not yet be parseable from the payload. All cross-ref fields default
    to empty string."""
    obj = CommercePaymentConfirmationFailed(
        type="commerce_payment_confirmation_failed", ts=TS,
        reason="signature_invalid",
    )
    assert obj.commerce_intent_id == ""
    assert obj.commerce_order_id == ""
    assert obj.lead_id == ""
