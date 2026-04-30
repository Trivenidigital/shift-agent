"""PR-B v0.4 commit 1 — CateringQuoteSkillFailed LogEntry variant tests.

Validates the discriminated-union add: variant round-trips, all 5 reason
literals accepted, invalid reason rejected, extra fields forbidden, the
variant resolves through the LogEntry Tag-union dispatcher.

Windows-runnable.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from schemas import (
    CateringQuoteSkillFailed,
    LogEntry,
    _KNOWN_LOG_ENTRY_TYPES,
)


def _ts() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def test_variant_round_trip_minimum_fields():
    e = CateringQuoteSkillFailed(
        type="catering_quote_skill_failed",
        ts=_ts(),
        lead_id="L0001",
        code="#ABCDE",
        reason="truth_guard_failed",
    )
    assert e.lead_id == "L0001"
    assert e.code == "#ABCDE"
    assert e.reason == "truth_guard_failed"
    assert e.detail == ""


def test_variant_round_trip_full_fields():
    e = CateringQuoteSkillFailed(
        type="catering_quote_skill_failed",
        ts=_ts(),
        lead_id="L0099",
        code="#XYZAB",
        reason="apply_decision_nonzero",
        detail="exit=3 stderr='bridge POST timed out'",
    )
    assert e.detail.startswith("exit=3")


@pytest.mark.parametrize("reason", [
    "missing_quote_text",
    "truth_guard_failed",
    "apply_decision_nonzero",
    "llm_unreachable",
    "llm_malformed_response",
])
def test_all_documented_reasons_accepted(reason):
    e = CateringQuoteSkillFailed(
        type="catering_quote_skill_failed",
        ts=_ts(), lead_id="L1", code="#ABCDE", reason=reason,
    )
    assert e.reason == reason


def test_invalid_reason_rejected():
    with pytest.raises(ValidationError):
        CateringQuoteSkillFailed(
            type="catering_quote_skill_failed",
            ts=_ts(), lead_id="L1", code="#ABCDE",
            reason="some_made_up_reason",  # type: ignore[arg-type]
        )


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        CateringQuoteSkillFailed(
            type="catering_quote_skill_failed",
            ts=_ts(), lead_id="L1", code="#ABCDE",
            reason="truth_guard_failed",
            rogue_field="x",  # type: ignore[call-arg]
        )


def test_code_format_constraints():
    """Code must be 6 chars (the # plus 5)."""
    # Too short
    with pytest.raises(ValidationError):
        CateringQuoteSkillFailed(
            type="catering_quote_skill_failed",
            ts=_ts(), lead_id="L1", code="#ABC",
            reason="truth_guard_failed",
        )
    # Too long
    with pytest.raises(ValidationError):
        CateringQuoteSkillFailed(
            type="catering_quote_skill_failed",
            ts=_ts(), lead_id="L1", code="#ABCDEF",
            reason="truth_guard_failed",
        )


def test_variant_known_to_log_entry_dispatcher():
    """The variant tag must be in _KNOWN_LOG_ENTRY_TYPES so the dispatcher
    routes it correctly (not to _UnknownLogEntry forward-compat passthrough)."""
    assert "catering_quote_skill_failed" in _KNOWN_LOG_ENTRY_TYPES


def test_variant_resolves_through_log_entry_union():
    """End-to-end: NDJSON-shaped dict resolves through TypeAdapter[LogEntry]
    to CateringQuoteSkillFailed instance, not forward-compat passthrough."""
    raw = {
        "type": "catering_quote_skill_failed",
        "ts": _ts(),
        "lead_id": "L0042",
        "code": "#QQQQQ",
        "reason": "missing_quote_text",
        "detail": "",
    }
    adapter = TypeAdapter(LogEntry)
    parsed = adapter.validate_python(raw)
    assert isinstance(parsed, CateringQuoteSkillFailed)
    assert parsed.reason == "missing_quote_text"
