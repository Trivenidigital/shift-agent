"""Slice-2 commit 1: schema-only tests for catering deposit caller.

Covers:
- CateringLead deposit_* field defaults preserve legacy-lead decode
- CateringDepositLinkSent / CateringDepositLinkFailed round-trip via LogEntry
- CommerceConfig.minimum_deposit_cents default + validation
- _emit_payment_link_failed helper writes the commerce_payment_link_failed
  audit row (Reviewer A BLOCKER-2 fix)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError
from schemas import (
    CateringLead,
    CateringLeadExtractedFields,
    CommerceConfig,
    CateringDepositLinkSent,
    CateringDepositLinkFailed,
    LogEntry,
)

from commerce import payment_link as commerce_payment_link


TS = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
ADAPTER = TypeAdapter(LogEntry)


# ─────────────────────────────────────────────────────────────────
# CateringLead deposit-field defaults — legacy-lead decode
# ─────────────────────────────────────────────────────────────────

def _minimal_lead_kwargs() -> dict:
    """Minimum-valid kwargs that decode under the existing CateringLead validators."""
    return dict(
        lead_id="L0001",
        status="NEW",
        customer_phone="+15551234567",
        raw_inquiry="catering for 50",
        original_message_id="msg_abc",
        created_at=TS,
        updated_at=TS,
    )


def test_catering_lead_deposit_fields_default_none_status():
    lead = CateringLead(**_minimal_lead_kwargs())
    assert lead.deposit_required is False
    assert lead.deposit_amount_cents == 0
    assert lead.deposit_commerce_order_id == ""
    assert lead.deposit_payment_intent_id == ""
    assert lead.deposit_payment_reference == ""
    assert lead.deposit_status == "none"
    assert lead.deposit_minted_at is None


def test_catering_lead_deposit_fields_round_trip():
    lead = CateringLead(
        **_minimal_lead_kwargs(),
        deposit_required=True,
        deposit_amount_cents=15000,
        deposit_commerce_order_id="CO00042",
        deposit_payment_intent_id="CPI00042",
        deposit_status="awaiting_payment",
        deposit_minted_at=TS,
    )
    raw = lead.model_dump_json()
    lead2 = CateringLead.model_validate_json(raw)
    assert lead2.deposit_amount_cents == 15000
    assert lead2.deposit_status == "awaiting_payment"
    assert lead2.deposit_minted_at == TS


def test_catering_lead_legacy_decode_omits_deposit_fields():
    """A pre-slice-2 lead JSON (no deposit_* keys) MUST decode cleanly under
    the new schema with defaults populated."""
    legacy_json = json.dumps({
        **_minimal_lead_kwargs(),
        "created_at": TS.isoformat(),
        "updated_at": TS.isoformat(),
    })
    lead = CateringLead.model_validate_json(legacy_json)
    assert lead.deposit_status == "none"
    assert lead.deposit_amount_cents == 0


def test_catering_lead_deposit_status_rejects_invalid_literal():
    with pytest.raises(ValidationError):
        CateringLead(
            **_minimal_lead_kwargs(),
            deposit_status="invalid_status_value",
        )


# ─────────────────────────────────────────────────────────────────
# CommerceConfig.minimum_deposit_cents — Reviewer B MEDIUM-2
# ─────────────────────────────────────────────────────────────────

def test_commerce_config_minimum_deposit_cents_default():
    cfg = CommerceConfig()
    assert cfg.minimum_deposit_cents == 500


def test_commerce_config_minimum_deposit_cents_override():
    cfg = CommerceConfig(minimum_deposit_cents=1000)
    assert cfg.minimum_deposit_cents == 1000


def test_commerce_config_minimum_deposit_cents_negative_refused():
    with pytest.raises(ValidationError):
        CommerceConfig(minimum_deposit_cents=-1)


def test_commerce_config_minimum_deposit_cents_zero_allowed():
    """Operator may set 0 to effectively disable the floor."""
    cfg = CommerceConfig(minimum_deposit_cents=0)
    assert cfg.minimum_deposit_cents == 0


# ─────────────────────────────────────────────────────────────────
# LogEntry variant round-trip
# ─────────────────────────────────────────────────────────────────

def test_catering_deposit_link_sent_round_trip():
    obj = CateringDepositLinkSent(
        type="catering_deposit_link_sent",
        ts=TS,
        lead_id="L0007",
        commerce_order_id="CO00042",
        commerce_payment_intent_id="CPI00042",
        amount_cents=15000,
        url_status="configured",
        outbound_message_id="wamid_xyz",
    )
    raw = obj.model_dump_json()
    parsed = ADAPTER.validate_python(json.loads(raw))
    assert parsed.type == "catering_deposit_link_sent"
    assert parsed.commerce_order_id == "CO00042"


def test_catering_deposit_link_sent_pattern_validation():
    """commerce_order_id MUST match ^CO\\d{5,}$ — write-side guard so callers
    can't pollute the audit log with malformed IDs."""
    with pytest.raises(ValidationError):
        CateringDepositLinkSent(
            type="catering_deposit_link_sent",
            ts=TS,
            lead_id="L0007",
            commerce_order_id="not-an-order-id",
            commerce_payment_intent_id="CPI00042",
            amount_cents=15000,
            url_status="configured",
            outbound_message_id="wamid_xyz",
        )


def test_catering_deposit_link_sent_extras_forbidden():
    with pytest.raises(ValidationError):
        CateringDepositLinkSent(
            type="catering_deposit_link_sent",
            ts=TS,
            lead_id="L0007",
            commerce_order_id="CO00042",
            commerce_payment_intent_id="CPI00042",
            amount_cents=15000,
            url_status="configured",
            outbound_message_id="wamid_xyz",
            extra_field="boom",
        )


def test_catering_deposit_link_sent_url_status_unconfigured():
    """Unconfigured-template still emits the SENT row (the bridge POST succeeded;
    only the URL itself was unactionable)."""
    obj = CateringDepositLinkSent(
        type="catering_deposit_link_sent",
        ts=TS,
        lead_id="L0007",
        commerce_order_id="CO00042",
        commerce_payment_intent_id="CPI00042",
        amount_cents=15000,
        url_status="unconfigured",
        outbound_message_id="wamid_xyz",
    )
    assert obj.url_status == "unconfigured"


def test_catering_deposit_link_failed_round_trip_all_reasons():
    """Every reason Literal must round-trip cleanly."""
    reasons = (
        "zero_amount",
        "below_minimum",
        "cart_build_failed",
        "order_create_failed",
        "intent_mint_failed",
        "bridge_send_failed",
        "subprocess_timeout",
    )
    for reason in reasons:
        obj = CateringDepositLinkFailed(
            type="catering_deposit_link_failed",
            ts=TS,
            lead_id="L0007",
            reason=reason,
            detail=f"reason={reason}",
        )
        raw = obj.model_dump_json()
        parsed = ADAPTER.validate_python(json.loads(raw))
        assert parsed.reason == reason


def test_catering_deposit_link_failed_invalid_reason_refused():
    with pytest.raises(ValidationError):
        CateringDepositLinkFailed(
            type="catering_deposit_link_failed",
            ts=TS,
            lead_id="L0007",
            reason="this_reason_is_not_in_the_literal",
        )


def test_catering_deposit_link_failed_commerce_ids_optional():
    """For early failures (zero_amount, cart_build_failed) the commerce_*
    fields may be unset because no slice-1 primitive returned an id yet."""
    obj = CateringDepositLinkFailed(
        type="catering_deposit_link_failed",
        ts=TS,
        lead_id="L0007",
        reason="zero_amount",
    )
    assert obj.commerce_order_id == ""
    assert obj.commerce_payment_intent_id == ""


# ─────────────────────────────────────────────────────────────────
# commerce.payment_link.emit_payment_link_failed helper
# ─────────────────────────────────────────────────────────────────

def test_emit_payment_link_failed_writes_audit_row(tmp_path: Path):
    log_path = tmp_path / "decisions.log"
    commerce_payment_link.emit_payment_link_failed(
        decisions_log_path=log_path,
        intent_id="CPI00042",
        order_id="CO00042",
        reason="bridge_send_failed: timeout after 10s",
    )
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["type"] == "commerce_payment_link_failed"
    assert rows[0]["intent_id"] == "CPI00042"
    assert rows[0]["order_id"] == "CO00042"
    assert rows[0]["reason"].startswith("bridge_send_failed")


def test_emit_payment_link_failed_truncates_long_reason(tmp_path: Path):
    log_path = tmp_path / "decisions.log"
    commerce_payment_link.emit_payment_link_failed(
        decisions_log_path=log_path,
        intent_id="CPI00042",
        order_id="CO00042",
        reason="x" * 500,  # well over 200
    )
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows[0]["reason"]) == 200
