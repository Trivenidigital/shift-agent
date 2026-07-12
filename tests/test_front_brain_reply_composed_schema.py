"""P0-5 — conversation-review surface LogEntry variant `front_brain_reply_composed`.

The Phase-1 human-review gate reads these rows: one per PASSED free-form send
through the front-brain outbound enforcement tier (P0-3a). `verdict="passed"`
records that the SENT text is safe (either the composed free-form reply, or the
fallback template when the composed reply was refused — distinguished by
`template_fallback`).

Schema-only coverage (mirrors test_catering_deposit_schema.py):
- round-trip through the LogEntry discriminated union
- field defaults + constraints (reply_text ≤2000, verdict Literal["passed"])
- extra="forbid" rejects unmodelled fields
- the tag joins _KNOWN_LOG_ENTRY_TYPES automatically (no picker drift)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from schemas import (
    FrontBrainReplyComposed,
    LogEntry,
    _KNOWN_LOG_ENTRY_TYPES,
)

TS = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
ADAPTER = TypeAdapter(LogEntry)


def _minimal_kwargs() -> dict:
    return dict(
        type="front_brain_reply_composed",
        ts=TS,
        reply_text="Happy to help — what should the flyer promote?",
    )


def test_front_brain_reply_composed_defaults():
    entry = FrontBrainReplyComposed(**_minimal_kwargs())
    assert entry.type == "front_brain_reply_composed"
    assert entry.chat_key_hash == ""
    assert entry.verdict == "passed"
    assert entry.lint_classes_checked == []
    assert entry.template_fallback is False


def test_front_brain_reply_composed_round_trip_through_union():
    payload = {
        **_minimal_kwargs(),
        "ts": TS.isoformat(),
        "chat_key_hash": "a" * 32,
        "lint_classes_checked": ["promise_ban", "invented_operational_claim", "length_spam_cap"],
        "template_fallback": True,
    }
    decoded = ADAPTER.validate_python(payload)
    assert isinstance(decoded, FrontBrainReplyComposed)
    assert decoded.template_fallback is True
    assert decoded.lint_classes_checked[0] == "promise_ban"
    # JSON serialization round-trips.
    reserialized = ADAPTER.validate_json(ADAPTER.dump_json(decoded))
    assert isinstance(reserialized, FrontBrainReplyComposed)


def test_verdict_is_fixed_passed_literal():
    with pytest.raises(ValidationError):
        FrontBrainReplyComposed(**{**_minimal_kwargs(), "verdict": "failed"})


def test_reply_text_capped_at_2000():
    # Exactly 2000 is allowed; 2001 is rejected (the review surface is bounded).
    ok = FrontBrainReplyComposed(**{**_minimal_kwargs(), "reply_text": "x" * 2000})
    assert len(ok.reply_text) == 2000
    with pytest.raises(ValidationError):
        FrontBrainReplyComposed(**{**_minimal_kwargs(), "reply_text": "x" * 2001})


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        FrontBrainReplyComposed(**{**_minimal_kwargs(), "unexpected": "nope"})


def test_tag_registered_in_known_types():
    assert "front_brain_reply_composed" in _KNOWN_LOG_ENTRY_TYPES
