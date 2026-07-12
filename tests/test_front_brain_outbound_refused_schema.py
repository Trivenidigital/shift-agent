"""P0-3a — refusal-audit LogEntry variant `front_brain_outbound_refused`.

Emitted when the front-brain enforcement tier refuses a composed free-form reply;
the composed text is NOT sent (a fallback template / safe generic ack is sent
instead). Schema-only coverage mirrors the review-surface variant test.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from schemas import (
    FrontBrainOutboundRefused,
    LogEntry,
    _KNOWN_LOG_ENTRY_TYPES,
)

TS = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
ADAPTER = TypeAdapter(LogEntry)


def _minimal_kwargs() -> dict:
    return dict(
        type="front_brain_outbound_refused",
        ts=TS,
        hit_classes=["promise_ban"],
        message_preview="We guarantee a full refund.",
    )


def test_defaults():
    entry = FrontBrainOutboundRefused(**_minimal_kwargs())
    assert entry.type == "front_brain_outbound_refused"
    assert entry.chat_key_hash == ""
    assert entry.hit_values == []
    assert entry.template_fallback_used is True


def test_round_trip_through_union():
    payload = {
        **_minimal_kwargs(),
        "ts": TS.isoformat(),
        "chat_key_hash": "b" * 32,
        "hit_classes": ["promise_ban", "invented_operational_claim"],
        "hit_values": ["guarantee", "refund", "processed"],
    }
    decoded = ADAPTER.validate_python(payload)
    assert isinstance(decoded, FrontBrainOutboundRefused)
    reserialized = ADAPTER.validate_json(ADAPTER.dump_json(decoded))
    assert isinstance(reserialized, FrontBrainOutboundRefused)


def test_hit_values_capped_at_20():
    ok = FrontBrainOutboundRefused(**{**_minimal_kwargs(), "hit_values": [f"v{i}" for i in range(20)]})
    assert len(ok.hit_values) == 20
    with pytest.raises(ValidationError):
        FrontBrainOutboundRefused(**{**_minimal_kwargs(), "hit_values": [f"v{i}" for i in range(21)]})


def test_message_preview_capped_at_120():
    with pytest.raises(ValidationError):
        FrontBrainOutboundRefused(**{**_minimal_kwargs(), "message_preview": "x" * 121})


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        FrontBrainOutboundRefused(**{**_minimal_kwargs(), "unexpected": "nope"})


def test_tag_registered_in_known_types():
    assert "front_brain_outbound_refused" in _KNOWN_LOG_ENTRY_TYPES
