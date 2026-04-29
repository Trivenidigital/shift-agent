"""PR-D1 commit 1 acceptance test: callable Discriminator + _UnknownLogEntry shim.

Validates that the LogEntry discriminated union routes:
  - Known `type` values to their typed variants (round-trip preserved).
  - Unknown `type` values to _UnknownLogEntry passthrough.
  - Known-but-malformed rows still raise ValidationError (no silent fallback).

Pinned to Pydantic 2.10+ behavior. The test set was validated on 2.12.5.
A future Pydantic version that changes Annotated/Discriminator semantics
should surface here, not in production audit-log replay.
"""
from __future__ import annotations
from typing import get_args
from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError, Tag

from schemas import LogEntry, _UnknownLogEntry, _KNOWN_LOG_ENTRY_TYPES, RawInbound


_ADAPTER = TypeAdapter(LogEntry)


# Case 1 — known variants round-trip to their typed class.
def test_known_variant_routes_to_typed_class():
    row = {
        "type": "raw_inbound",
        "ts": "2026-01-01T00:00:00Z",
        "message_id": "m1",
        "sender_phone": "+15555550100",
        "input_message": "hello",
    }
    parsed = _ADAPTER.validate_python(row)
    assert isinstance(parsed, RawInbound)
    assert parsed.message_id == "m1"


# Case 2 — unknown `type` routes to _UnknownLogEntry, preserving the tag value.
def test_unknown_type_routes_to_passthrough():
    row = {
        "type": "future_xyz",
        "ts": "2026-01-01T00:00:00Z",
        "extra_field": 42,
        "nested": {"a": 1},
    }
    parsed = _ADAPTER.validate_python(row)
    assert isinstance(parsed, _UnknownLogEntry)
    assert parsed.type == "future_xyz"
    # extra="allow" captures unknown fields into model_extra
    assert parsed.model_extra == {"extra_field": 42, "nested": {"a": 1}}


# Case 3 — known type with bad fields raises ValidationError (NOT silent fallback).
def test_known_type_bad_fields_raises():
    """Critical: the picker only routes UNKNOWN tags to _unknown_. A
    recognized type with malformed fields must still raise so contributors
    don't accidentally bypass field validation."""
    row = {"type": "raw_inbound", "ts": "2026-01-01T00:00:00Z"}  # missing required fields
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(row)


# Case 4 — drift introspection guard: every Tag in the union is in
# _KNOWN_LOG_ENTRY_TYPES (or is the `_unknown_` sentinel which is excluded).
def test_known_log_entry_types_matches_union_tags():
    union_arg = get_args(LogEntry)[0]
    tags_in_union: set[str] = set()
    for member in get_args(union_arg):
        for meta in get_args(member):
            if isinstance(meta, Tag):
                tags_in_union.add(meta.tag)
    expected = tags_in_union - {"_unknown_"}
    assert _KNOWN_LOG_ENTRY_TYPES == expected, (
        f"_KNOWN_LOG_ENTRY_TYPES drifted from union Tag set. "
        f"Missing from set: {expected - _KNOWN_LOG_ENTRY_TYPES}. "
        f"Extra in set: {_KNOWN_LOG_ENTRY_TYPES - expected}."
    )
    # And the sentinel is NOT in the known set
    assert "_unknown_" not in _KNOWN_LOG_ENTRY_TYPES


# Case 5 — `type` key entirely absent routes to _UnknownLogEntry.
def test_missing_type_key_routes_to_passthrough():
    row = {"ts": "2026-01-01T00:00:00Z"}
    # _UnknownLogEntry.type: str is required; with no `type`, validation
    # of _UnknownLogEntry itself fails (we route there, but the model
    # still requires a `type` field). Behavior: ValidationError.
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(row)


# Case 6 — type=None routes to _UnknownLogEntry; _UnknownLogEntry rejects None for type: str.
def test_type_null_raises():
    row = {"type": None, "ts": "2026-01-01T00:00:00Z"}
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(row)


# Case 7 — type=int routes to _UnknownLogEntry; _UnknownLogEntry rejects int for type: str.
def test_type_non_string_raises():
    row = {"type": 42, "ts": "2026-01-01T00:00:00Z"}
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(row)


# Case 8 — empty string `type=""` is captured (intentional).
def test_empty_type_string_captured():
    """Capture-and-preserve is the design intent for forward-compat: a
    malformed row with empty `type` lands in _UnknownLogEntry rather than
    raising, so audit replay tooling can still inspect the row."""
    row = {"type": "", "ts": "2026-01-01T00:00:00Z"}
    parsed = _ADAPTER.validate_python(row)
    assert isinstance(parsed, _UnknownLogEntry)
    assert parsed.type == ""


# Case 9 — literal `type="_unknown_"` passes through (sentinel-typo case).
def test_literal_unknown_sentinel_passes_through():
    """If a future emitter accidentally writes `type: "_unknown_"`, route
    it through the passthrough rather than raising. Distinguishes
    sentinel-typo from missing-field bugs."""
    row = {"type": "_unknown_", "ts": "2026-01-01T00:00:00Z"}
    parsed = _ADAPTER.validate_python(row)
    assert isinstance(parsed, _UnknownLogEntry)
    assert parsed.type == "_unknown_"


# Case 10 — round-trip preserves arbitrary extra fields via model_extra (Pydantic v2).
def test_unknown_round_trip_preserves_fields():
    row = {
        "type": "future_v2_event",
        "ts": "2026-01-01T00:00:00Z",
        "lead_id": "L00042",
        "amount_cents": 12500,
        "tags": ["a", "b"],
    }
    parsed = _ADAPTER.validate_python(row)
    dumped = parsed.model_dump()
    # Every input field appears in the dump
    assert dumped["type"] == "future_v2_event"
    assert dumped["lead_id"] == "L00042"
    assert dumped["amount_cents"] == 12500
    assert dumped["tags"] == ["a", "b"]


# Case 11 — ts validator (mode="before" tz coercion) still applies through _UnknownLogEntry.
def test_ts_validator_runs_for_unknown_entry():
    row = {"type": "future_xyz", "ts": "2026-01-01T00:00:00"}  # naive
    parsed = _ADAPTER.validate_python(row)
    assert isinstance(parsed, _UnknownLogEntry)
    assert parsed.ts.tzinfo is not None
    assert parsed.ts == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


# Case 12 — isinstance discrimination: only _UnknownLogEntry IS _UnknownLogEntry.
def test_unknown_log_entry_is_only_subclass_of_self():
    """No other LogEntry variant subclasses _UnknownLogEntry. extra='allow'
    must not propagate to typed variants via inheritance."""
    union_arg = get_args(LogEntry)[0]
    members = []
    for m in get_args(union_arg):
        members.append(get_args(m)[0])
    leaks = [c for c in members
             if c is not _UnknownLogEntry and issubclass(c, _UnknownLogEntry)]
    assert leaks == [], (
        f"{leaks} should not inherit from _UnknownLogEntry — extra='allow' "
        f"would silently leak via inheritance"
    )
