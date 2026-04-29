"""PR-D1 commit 2: CateringQuoteSentLeadMissing variant + bridge_post_outcome
field on CateringQuoteAttempted.

Per design v2 §9.1 / R4-M-T1: schema test count grows to ~12 cases for
these two related changes (8 for the new variant + 4 for the field
extension).
"""
from __future__ import annotations
from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from schemas import (
    LogEntry,
    CateringQuoteAttempted,
    CateringQuoteSentLeadMissing,
    _KNOWN_LOG_ENTRY_TYPES,
)


_LE = TypeAdapter(LogEntry)
_QSL = TypeAdapter(CateringQuoteSentLeadMissing)
_CQA = TypeAdapter(CateringQuoteAttempted)


# ─────────────── CateringQuoteSentLeadMissing — 8 cases ───────────────

def test_quote_sent_lead_missing_round_trip_minimum():
    row = {
        "type": "catering_quote_sent_lead_missing",
        "ts": "2026-01-01T00:00:00Z",
        "lead_id": "L00042",
        "original_message_id": "m_orig",
        "customer_phone_at_approve": "+15555550100",
        "outbound_message_id": "mb_42",
    }
    parsed = _LE.validate_python(row)
    assert isinstance(parsed, CateringQuoteSentLeadMissing)
    assert parsed.lead_id == "L00042"
    assert parsed.detail == ""  # default


def test_quote_sent_lead_missing_with_detail():
    row = {
        "type": "catering_quote_sent_lead_missing",
        "ts": "2026-01-01T00:00:00Z",
        "lead_id": "L00042",
        "original_message_id": "m_orig",
        "customer_phone_at_approve": "+15555550100",
        "outbound_message_id": "mb_42",
        "detail": "post-bridge re-load lost lead (status='ok' but lead absent)",
    }
    parsed = _QSL.validate_python(row)
    assert "lost lead" in parsed.detail


def test_quote_sent_lead_missing_rejects_empty_lead_id():
    with pytest.raises(ValidationError):
        _QSL.validate_python({
            "type": "catering_quote_sent_lead_missing",
            "ts": "2026-01-01T00:00:00Z",
            "lead_id": "",  # min_length=1 violation
            "original_message_id": "m_orig",
            "customer_phone_at_approve": "+15555550100",
            "outbound_message_id": "mb_42",
        })


def test_quote_sent_lead_missing_rejects_invalid_phone():
    with pytest.raises(ValidationError):
        _QSL.validate_python({
            "type": "catering_quote_sent_lead_missing",
            "ts": "2026-01-01T00:00:00Z",
            "lead_id": "L00042",
            "original_message_id": "m_orig",
            "customer_phone_at_approve": "555-5555",  # not E.164
            "outbound_message_id": "mb_42",
        })


def test_quote_sent_lead_missing_rejects_empty_outbound_message_id():
    with pytest.raises(ValidationError):
        _QSL.validate_python({
            "type": "catering_quote_sent_lead_missing",
            "ts": "2026-01-01T00:00:00Z",
            "lead_id": "L00042",
            "original_message_id": "m_orig",
            "customer_phone_at_approve": "+15555550100",
            "outbound_message_id": "",
        })


def test_quote_sent_lead_missing_rejects_extra_field():
    """extra='forbid' inherited from _BaseEntry."""
    with pytest.raises(ValidationError):
        _QSL.validate_python({
            "type": "catering_quote_sent_lead_missing",
            "ts": "2026-01-01T00:00:00Z",
            "lead_id": "L00042",
            "original_message_id": "m_orig",
            "customer_phone_at_approve": "+15555550100",
            "outbound_message_id": "mb_42",
            "rogue_field": "x",
        })


def test_quote_sent_lead_missing_truncates_via_max_length_violation():
    """detail max_length=500."""
    with pytest.raises(ValidationError):
        _QSL.validate_python({
            "type": "catering_quote_sent_lead_missing",
            "ts": "2026-01-01T00:00:00Z",
            "lead_id": "L00042",
            "original_message_id": "m_orig",
            "customer_phone_at_approve": "+15555550100",
            "outbound_message_id": "mb_42",
            "detail": "x" * 501,
        })


def test_quote_sent_lead_missing_in_known_types():
    assert "catering_quote_sent_lead_missing" in _KNOWN_LOG_ENTRY_TYPES


# ─────────────── CateringQuoteAttempted bridge_post_outcome — 4 cases ───────────────

def test_quote_attempted_default_outcome_unknown():
    """Legacy rows pre-PR-D1 lacked the field; default fills cleanly."""
    row = {
        "type": "catering_quote_attempted",
        "ts": "2026-01-01T00:00:00Z",
        "lead_id": "L00042",
        "original_message_id": "m_orig",
        "code": "#A2B3C",
    }
    parsed = _CQA.validate_python(row)
    assert parsed.bridge_post_outcome == "unknown"


def test_quote_attempted_explicit_success():
    row = {
        "type": "catering_quote_attempted",
        "ts": "2026-01-01T00:00:00Z",
        "lead_id": "L00042",
        "original_message_id": "m_orig",
        "code": "#A2B3C",
        "bridge_post_outcome": "success",
    }
    parsed = _CQA.validate_python(row)
    assert parsed.bridge_post_outcome == "success"


def test_quote_attempted_explicit_failed():
    row = {
        "type": "catering_quote_attempted",
        "ts": "2026-01-01T00:00:00Z",
        "lead_id": "L00042",
        "original_message_id": "m_orig",
        "code": "#A2B3C",
        "bridge_post_outcome": "failed",
    }
    parsed = _CQA.validate_python(row)
    assert parsed.bridge_post_outcome == "failed"


def test_quote_attempted_rejects_invalid_outcome():
    """Literal["success","failed","unknown"] rejects everything else."""
    with pytest.raises(ValidationError):
        _CQA.validate_python({
            "type": "catering_quote_attempted",
            "ts": "2026-01-01T00:00:00Z",
            "lead_id": "L00042",
            "original_message_id": "m_orig",
            "code": "#A2B3C",
            "bridge_post_outcome": "pending",
        })
