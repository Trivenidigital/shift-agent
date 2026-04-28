"""Tests for Catering Lead schemas (Agent #2). Windows-runnable."""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from datetime import datetime, timezone

from schemas import (
    Config, CateringConfig, CateringLeadStatus, CateringLeadExtractedFields,
    CateringLead, CateringLeadStore, is_catering_terminal, CATERING_TERMINAL_STATUSES,
    CateringLeadCreated, CateringLeadStatusChange, CateringQuoteDrafted,
    CateringOwnerApprovalRequested, CateringOwnerDecision, CateringQuoteSent,
)


def test_catering_config_defaults():
    c = CateringConfig()
    assert c.enabled is False  # opt-in
    assert c.deposit_threshold_guests == 50
    assert c.deposit_pct == 0.25
    assert c.stale_after_hours == 14 * 24


def test_catering_config_validators():
    with pytest.raises(ValidationError):
        CateringConfig(deposit_pct=1.5)  # > 1.0
    with pytest.raises(ValidationError):
        CateringConfig(deposit_threshold_guests=0)
    with pytest.raises(ValidationError):
        CateringConfig(stale_after_hours=0)


def test_catering_extracted_fields_optional_all():
    e = CateringLeadExtractedFields()  # all defaults
    assert e.headcount is None
    assert e.menu_preferences == []


def test_catering_extracted_validators():
    e = CateringLeadExtractedFields(headcount=50, event_date="2026-12-25")
    assert e.headcount == 50
    with pytest.raises(ValidationError):
        CateringLeadExtractedFields(headcount=-1)
    with pytest.raises(ValidationError):
        CateringLeadExtractedFields(event_date="12-25-2026")  # bad pattern


def test_catering_extracted_extras_ignored():
    """LLM may emit extras; extra='ignore' is intentional here vs forbid elsewhere."""
    e = CateringLeadExtractedFields.model_validate({
        "headcount": 100, "noise_field": "ignore_me", "extra": "stuff",
    })
    assert e.headcount == 100


def test_is_catering_terminal():
    for status in ["NOT_CATERING", "OWNER_REJECTED", "CLOSED", "STALE"]:
        assert is_catering_terminal(status)
    for status in ["NEW", "EXTRACTING", "AWAITING_OWNER_APPROVAL", "SENT_TO_CUSTOMER"]:
        assert not is_catering_terminal(status)


def test_catering_lead_basic():
    now = datetime.now(tz=timezone.utc)
    l = CateringLead(
        lead_id="L0001", status="NEW",
        customer_phone="+19045550199", raw_inquiry="Need catering for 50 people",
        original_message_id="msg_meta_1", created_at=now, updated_at=now,
    )
    assert l.lead_id == "L0001"
    assert l.quote_version == 0


def test_catering_lead_extra_forbid():
    now = datetime.now(tz=timezone.utc)
    with pytest.raises(ValidationError):
        CateringLead(
            lead_id="L1", status="NEW", customer_phone="+19045550199",
            raw_inquiry="x", original_message_id="m1",
            created_at=now, updated_at=now,
            typo_field="bad",  # type: ignore[call-arg]
        )


def test_catering_lead_store_default_empty():
    s = CateringLeadStore()
    assert s.leads == []


def test_catering_log_entries():
    now = datetime.now(tz=timezone.utc)
    CateringLeadCreated(
        type="catering_lead_created", ts=now, lead_id="L1",
        customer_phone="+19045550199", original_message_id="m1",
    )
    CateringLeadStatusChange(
        type="catering_lead_status_change", ts=now, lead_id="L1",
        from_status="NEW", to_status="EXTRACTING", actor="system",
    )
    CateringQuoteDrafted(
        type="catering_quote_drafted", ts=now, lead_id="L1",
        quote_version=1, word_count=42,
    )
    CateringOwnerApprovalRequested(
        type="catering_owner_approval_requested", ts=now,
        lead_id="L1", approval_code="#A1B2C",
    )
    CateringOwnerDecision(
        type="catering_owner_decision", ts=now, lead_id="L1",
        decision="approve",
    )
    CateringQuoteSent(
        type="catering_quote_sent", ts=now, lead_id="L1",
        customer_phone="+19045550199", outbound_message_id="meta_out_1",
    )


def test_config_backward_compat_no_catering():
    old = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "l", "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }
    c = Config.model_validate(old)
    assert c.catering.enabled is False  # default opt-in
