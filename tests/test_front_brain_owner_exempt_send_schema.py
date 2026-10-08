"""2026-10-08 — LogEntry variant `front_brain_owner_exempt_send`.

Positive audit record of a scripted send to a PRIMARY owner identity that skipped
the front-brain screen (bridge_post seam, exempt_owner=True). Without it nothing
records what the owner was sent. Schema-only coverage mirrors the refusal variant.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from schemas import (
    FrontBrainOwnerExemptSend,
    LogEntry,
    _KNOWN_LOG_ENTRY_TYPES,
)

TS = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
ADAPTER = TypeAdapter(LogEntry)


def _minimal_kwargs() -> dict:
    return dict(
        type="front_brain_owner_exempt_send",
        ts=TS,
        message_text="Good morning! 2 shifts scheduled today. Quotes sent: 3.",
    )


def test_defaults():
    entry = FrontBrainOwnerExemptSend(**_minimal_kwargs())
    assert entry.type == "front_brain_owner_exempt_send"
    assert entry.chat_key_hash == ""
    assert entry.seam == "bridge_post"
    assert entry.exempt_reason == "primary_owner"
    assert entry.send_attempt_id == ""


def test_round_trip_through_union():
    payload = {
        **_minimal_kwargs(),
        "ts": TS.isoformat(),
        "chat_key_hash": "c" * 32,
        "seam": "bridge_post",
        "exempt_reason": "primary_owner",
        "send_attempt_id": "d" * 32,
    }
    decoded = ADAPTER.validate_python(payload)
    assert isinstance(decoded, FrontBrainOwnerExemptSend)
    reserialized = ADAPTER.validate_json(ADAPTER.dump_json(decoded))
    assert isinstance(reserialized, FrontBrainOwnerExemptSend)
    assert reserialized.message_text == payload["message_text"]


def test_message_text_capped_at_2000():
    ok = FrontBrainOwnerExemptSend(**{**_minimal_kwargs(), "message_text": "x" * 2000})
    assert len(ok.message_text) == 2000
    with pytest.raises(ValidationError):
        FrontBrainOwnerExemptSend(**{**_minimal_kwargs(), "message_text": "x" * 2001})


@pytest.mark.parametrize("field,value", [
    ("seam", "gateway_send"),
    ("exempt_reason", "authorized_identity"),
])
def test_closed_literals(field, value):
    with pytest.raises(ValidationError):
        FrontBrainOwnerExemptSend(**{**_minimal_kwargs(), field: value})


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        FrontBrainOwnerExemptSend(**{**_minimal_kwargs(), "unexpected": "nope"})


def test_tag_registered_in_known_types():
    assert "front_brain_owner_exempt_send" in _KNOWN_LOG_ENTRY_TYPES
