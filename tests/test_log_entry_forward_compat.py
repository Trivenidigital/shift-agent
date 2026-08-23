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
import json
from typing import get_args
from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError, Tag

# ONE import statement, evaluated in the same module body as `_ADAPTER` below.
# That is load-bearing, not tidiness. Several suites (see
# tests/test_gateway_send_throttle.py's fixture docstring) pop `schemas` out of
# sys.modules and reload it FRESH while the session runs, so by the time a test
# here executes, `sys.modules['schemas']` can be a DIFFERENT module object than
# the one whose classes `_ADAPTER` validates into — two classes, same qualified
# name. Re-resolving the class inside a test (`from schemas import ...`, or
# `sys.modules[type(parsed).__module__]`, which is the same lookup) then compares
# across module identities and fails on a property that is actually fine.
# Binding here, beside the adapter, is the only form that cannot drift.
from schemas import (
    LogEntry, _UnknownLogEntry, _KNOWN_LOG_ENTRY_TYPES, RawInbound, _BaseEntry,
    CfRouterIntercepted, _UnknownReasonCfRouterIntercepted,
)


_ADAPTER = TypeAdapter(LogEntry)


# Rollback-compat case: ExpenseOwnerApprovalRequested.routed_to was narrowed
# from Literal["whatsapp", "cockpit_v01_paper"] to Literal["whatsapp"] in
# PR #42 (the writer was always hardcoded to "whatsapp" post-cleanup). PR #42
# post-merge review caught that this was a backwards-incompatible read-side
# break — historical decisions.log rows could contain "cockpit_v01_paper".
# This test pins the read-side widening: legacy rows must still validate
# through the LogEntry adapter. Removal of the legacy value follows the
# rollback-window-lapse + live-VPS grep-zero confirmation per the comment
# on the schema field itself.
def test_expense_owner_approval_requested_legacy_cockpit_v01_paper_compat():
    row = {
        "type": "expense_owner_approval_requested",
        "ts": "2026-04-29T12:00:00Z",
        "expense_id": "E0001",
        "owner_approval_code": "#A47C2",
        "extracted_total_cents": 23450,
        "routed_to": "cockpit_v01_paper",  # legacy value pre-PR #42
    }
    parsed = _ADAPTER.validate_python(row)
    # Routes to typed variant (NOT _UnknownLogEntry — `type` is known)
    assert parsed.type == "expense_owner_approval_requested"
    assert parsed.routed_to == "cockpit_v01_paper"
    assert parsed.expense_id == "E0001"


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


def _union_member_models(annotated_union) -> list[type]:
    """Every concrete model class reachable in a Tag-discriminated union.

    Recurses into NESTED discriminated unions: the `cf_router_intercepted`
    member is itself `Annotated[Union[strict, shim], Discriminator(...)]`
    (the reason-level forward-compat shim, Case 14 below), so a flat
    `get_args(m)[0]` would hand back a `Union` object instead of a class and
    `issubclass` would raise. Recursing also makes the check STRONGER — the
    nested members are inspected too, not skipped.
    """
    out: list[type] = []
    for member in get_args(get_args(annotated_union)[0]):
        inner = get_args(member)[0]
        if isinstance(inner, type):
            out.append(inner)
        else:  # nested Annotated[Union[...], Discriminator(...)]
            out.extend(_union_member_models(member))
    return out


# Case 12 — isinstance discrimination: only _UnknownLogEntry IS _UnknownLogEntry.
def test_unknown_log_entry_is_only_subclass_of_self():
    """No other LogEntry variant subclasses _UnknownLogEntry. extra='allow'
    must not propagate to typed variants via inheritance."""
    members = _union_member_models(LogEntry)
    leaks = [c for c in members
             if c is not _UnknownLogEntry and issubclass(c, _UnknownLogEntry)]
    assert leaks == [], (
        f"{leaks} should not inherit from _UnknownLogEntry — extra='allow' "
        f"would silently leak via inheritance"
    )


# Case 13 — reachability (BL-CI-03): every _BaseEntry subclass that declares a concrete
# `type` Literal must be REGISTERED. Case 4 pins _KNOWN_LOG_ENTRY_TYPES == union Tags, but
# neither checks that a newly-defined variant CLASS is actually in that set — an unregistered
# subclass is silently read back as _UnknownLogEntry (its typed fields never validate), with
# no failing test. This closes that gap.
def _all_base_entry_subclasses() -> set[type]:
    """All _BaseEntry subclasses (recursive), excluding the _UnknownLogEntry sentinel."""
    seen: set[type] = set()
    stack = list(_BaseEntry.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
    seen.discard(_UnknownLogEntry)
    return seen


def test_every_typed_log_entry_variant_is_registered():
    """A _BaseEntry subclass with a single-value `type` Literal that is missing from
    _KNOWN_LOG_ENTRY_TYPES is an unregistered variant: rows with that `type` route to
    _UnknownLogEntry instead of validating against the typed class. Fails on the drop."""
    unregistered = []
    for cls in _all_base_entry_subclasses():
        type_field = cls.model_fields.get("type")
        if type_field is None:
            continue
        literal_values = get_args(type_field.annotation)  # Literal["x"] -> ("x",); str -> ()
        if len(literal_values) == 1 and isinstance(literal_values[0], str):
            if literal_values[0] not in _KNOWN_LOG_ENTRY_TYPES:
                unregistered.append((cls.__name__, literal_values[0]))
    assert not unregistered, (
        f"unregistered LogEntry variant(s) {sorted(unregistered)} — defined as _BaseEntry "
        f"subclasses with a `type` Literal but absent from _KNOWN_LOG_ENTRY_TYPES, so their "
        f"rows silently route to _UnknownLogEntry. Add each to the LogEntry union "
        f"(Annotated[<Class>, Tag(\"<type>\")]) + _KNOWN_LOG_ENTRY_TYPES."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Case 14 — reader-side forward-compat for an unknown `reason` VALUE inside the
# KNOWN `cf_router_intercepted` tag (phase 1 of the reader-first migration,
# 2026-08-23).
#
# Cases 2/3 above pin the TAG-level contract: an unknown tag degrades through
# `_UnknownLogEntry`, a known tag with bad fields still raises. That leaves the
# VALUE-level gap: `cf_router_intercepted` is a known tag, so a row whose
# `reason` is not (yet) a Literal member routes to the typed variant and RAISES
# — the one rollback shape where an old reader rejects a row instead of
# degrading past it. `_UnknownReasonCfRouterIntercepted` closes that for this
# field the same way `_UnknownLogEntry` closes it for the tag.
#
# The contract these cases pin is deliberately asymmetric:
#   READ  (through the LogEntry union) absorbs an unknown reason.
#   WRITE (constructing CfRouterIntercepted directly, which is what
#          cf-router/actions.py:audit_intercepted does) still raises.
# That asymmetry IS phase 1: deploying it emits no new row, so reverting it can
# strand none.
# ═══════════════════════════════════════════════════════════════════════════
_CF_ROW_BASE = {
    "type": "cf_router_intercepted",
    "ts": "2026-08-23T00:00:00Z",
    "chat_id": "19045550199@s.whatsapp.net",
    "detail": "followup status was awaiting_owner_approval",
}

# The two reasons cf-router emits TODAY that are not Literal members, so their
# rows are silently dropped at write time. Kept in sync with
# tests/test_catering_amendment_capture.py::_KNOWN_DROPPED_REASONS.
# PHASE 2 LANDED 2026-08-23. The two M5 follow-up reasons are Literal members
# now, so they route to the strict class; what remains under test below is the
# property phase 1 exists for — a value nobody has added yet must still be
# absorbed rather than rejected.
_PHASE_2_LANDED = ("f8_followup_approve", "f8_followup_cancel")


@pytest.mark.parametrize("reason", _PHASE_2_LANDED)
def test_phase_2_reasons_now_validate_and_are_no_longer_dropped(reason):
    """The point of the whole two-phase exercise.

    Before phase 2 these failed validation inside audit_intercepted's
    best-effort try/except, so cf-router emitted them and the row silently never
    landed in decisions.log. They must now construct DIRECTLY — the writer path,
    not just the reader union, because the writer is what was losing them.
    """
    row = CfRouterIntercepted(**{**_CF_ROW_BASE, "reason": reason})
    assert row.reason == reason
    assert type(row).__name__ == "CfRouterIntercepted", (
        "a phase-2 reason must route to the strict class, not the absorbing shim")


def test_the_shim_still_absorbs_something_nobody_has_added_yet():
    """Phase 2 must not have removed the forward-compat property it depended on."""
    parsed = _ADAPTER.validate_python(
        {**_CF_ROW_BASE, "reason": "a_reason_from_a_release_after_this_one"})
    assert type(parsed).__name__ == "_UnknownReasonCfRouterIntercepted"
    assert parsed.reason == "a_reason_from_a_release_after_this_one"


@pytest.mark.parametrize("reason", ("some_reason_invented_in_2027",
                                   "a_reason_from_a_future_release"))
def test_cf_router_intercepted_unknown_reason_absorbed_on_read(reason):
    """A reader must ingest a reason value it does not know, not reject the row."""
    parsed = _ADAPTER.validate_python({**_CF_ROW_BASE, "reason": reason})
    assert parsed.reason == reason
    assert parsed.type == "cf_router_intercepted"
    assert parsed.chat_id == _CF_ROW_BASE["chat_id"]
    assert type(parsed) is _UnknownReasonCfRouterIntercepted
    # isinstance still holds — readers that branch on the typed class keep working.
    assert isinstance(parsed, CfRouterIntercepted)


def test_cf_router_intercepted_known_reason_still_routes_to_the_strict_variant():
    """The shim must not swallow rows the Literal already covers."""
    parsed = _ADAPTER.validate_python({**_CF_ROW_BASE, "reason": "f8_owner_approve"})
    assert type(parsed) is CfRouterIntercepted, (
        "a known reason must keep validating against the Literal")
    # Belt-and-braces, identity-free: even if the two classes above were ever the
    # same object for the wrong reason, absorption by the shim still fails here.
    assert type(parsed).__name__ == "CfRouterIntercepted", (
        "a known reason must NOT be absorbed by the phase-1 shim")




def test_cf_router_intercepted_unknown_reason_still_validates_every_other_field():
    """Only the reason VALUE is absorbed. A row that is genuinely malformed in
    any other way still raises — the shim inherits extra='forbid' and every
    other field's constraint from the strict variant."""
    # required field missing
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"type": "cf_router_intercepted",
                                  "ts": "2026-08-23T00:00:00Z",
                                  "reason": "f8_followup_approve"})  # no chat_id
    # unmodelled extra key
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({**_CF_ROW_BASE, "reason": "f8_followup_approve",
                                  "not_a_field": 1})
    # inherited constraint on a sibling field (code max_length=10)
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({**_CF_ROW_BASE, "reason": "f8_followup_approve",
                                  "code": "#" + "X" * 32})


@pytest.mark.parametrize("reason", [None, 42, ["f8_followup_approve"]])
def test_cf_router_intercepted_non_string_reason_still_raises(reason):
    """Forward-compat is for a future STRING value, not for a broken row."""
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({**_CF_ROW_BASE, "reason": reason})


def test_cf_router_intercepted_unknown_reason_round_trips():
    """Audit-replay tooling must get the row back byte-for-byte on the fields it
    carries — an absorbed row that loses its reason is no better than a dropped one."""
    row = {**_CF_ROW_BASE, "reason": "f8_followup_cancel", "code": "#AB2CD",
           "subprocess_rc": 0}
    parsed = _ADAPTER.validate_python(row)
    dumped = json.loads(parsed.model_dump_json())
    for key, value in row.items():
        if key == "ts":
            continue  # normalized to a tz-aware datetime by _BaseEntry
        assert dumped[key] == value, f"{key} did not round-trip"


