"""Forward-compat for the Proposal discriminated union (BL-HERMES-06).

`PendingStore.proposals` is `dict[str, Proposal]`; before the `_UnknownProposal` shim a single
row with a `status` written by a NEWER binary raised `ValidationError` and bricked the WHOLE
store load — every proposal was lost from the reader's view. These tests pin: unknown status →
`_UnknownProposal` passthrough; known status → typed variant; known-but-malformed → still raises.

Pure schema introspection → runs cross-platform (incl. the Windows dev box), like
test_log_entry_forward_compat.py. Mirrors that suite's structure.
"""
from __future__ import annotations
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError, Tag

from schemas import (
    Proposal, PendingStore, _UnknownProposal, _KNOWN_PROPOSAL_STATUSES, _BaseProp,
    SentProposal, LEGAL_TRANSITIONS,
)

_ADAPTER = TypeAdapter(Proposal)

_BASE = {
    "proposal_id": "P0001", "code": "#AB3X2",
    "created_ts": "2026-07-10T08:00:00-04:00", "last_updated_ts": "2026-07-10T08:00:00-04:00",
    "absent_employee_id": "e001", "absent_date": "2026-07-10", "absent_shift": "09:00-17:00",
    "absent_role": "cashier", "absent_reason": "sick", "input_message": "x", "message_id": "m1",
}


def _prop(**over):
    return {**_BASE, **over}


# Case 1 — known status routes to its typed variant.
def test_known_status_routes_to_typed_class():
    p = _ADAPTER.validate_python(_prop(status="sent", sent_ts="2026-07-10T08:00:00-04:00"))
    assert isinstance(p, SentProposal)
    assert p.status == "sent"


# Case 2 — unknown status routes to _UnknownProposal, preserving status + extras.
def test_unknown_status_routes_to_passthrough():
    p = _ADAPTER.validate_python(_prop(status="future_holo_status", new_field=42))
    assert isinstance(p, _UnknownProposal)
    assert p.status == "future_holo_status"
    assert p.model_extra == {"new_field": 42}


# Case 3 — known status with malformed fields still raises (no silent fallback).
def test_known_status_bad_fields_raises():
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"status": "sent", "proposal_id": "P0"})


# Case 4 — the core case: a store mixing a known + an unknown proposal loads FULLY; the unknown
# row does not brick the known one.
def test_pending_store_survives_unknown_proposal():
    store = PendingStore.model_validate({"proposals": {
        "P0001": _prop(status="sent", sent_ts="2026-07-10T08:00:00-04:00"),
        "P0002": _prop(proposal_id="P0002", status="future_v2_status"),
    }})
    assert isinstance(store.proposals["P0001"], SentProposal)
    assert isinstance(store.proposals["P0002"], _UnknownProposal)
    assert store.proposals["P0002"].status == "future_v2_status"


# Case 5 — round-trip preserves arbitrary new fields via model_extra.
def test_unknown_proposal_round_trip_preserves_fields():
    p = _ADAPTER.validate_python(_prop(status="future_v2", extra_a="z", amount_cents=99))
    dumped = p.model_dump()
    assert dumped["status"] == "future_v2"
    assert dumped["extra_a"] == "z"
    assert dumped["amount_cents"] == 99


# Case 6 — drift guard: _KNOWN_PROPOSAL_STATUSES == the union's Tag set.
def test_known_statuses_matches_union_tags():
    union_arg = get_args(Proposal)[0]
    tags: set[str] = set()
    for member in get_args(union_arg):
        for meta in get_args(member):
            if isinstance(meta, Tag):
                tags.add(meta.tag)
    assert _KNOWN_PROPOSAL_STATUSES == tags - {"_unknown_"}, (
        f"_KNOWN_PROPOSAL_STATUSES drifted from the union Tag set: "
        f"set={_KNOWN_PROPOSAL_STATUSES} tags={tags - {'_unknown_'}}"
    )
    assert "_unknown_" not in _KNOWN_PROPOSAL_STATUSES


# Case 7 — the known-status set must exactly match the state-machine's transition keys.
def test_known_statuses_matches_legal_transitions_keys():
    assert _KNOWN_PROPOSAL_STATUSES == set(LEGAL_TRANSITIONS.keys()), (
        "known proposal statuses and LEGAL_TRANSITIONS keys drifted"
    )


# Case 8 — reachability (mirror of batch 7's LogEntry guard): every _BaseProp subclass with a
# single-value status Literal must be registered, else its rows silently pass through unknown.
def _all_base_prop_subclasses() -> set[type]:
    out: set[type] = set()
    stack = list(_BaseProp.__subclasses__())
    while stack:
        c = stack.pop()
        if c in out:
            continue
        out.add(c)
        stack.extend(c.__subclasses__())
    out.discard(_UnknownProposal)
    return out


def test_every_base_prop_variant_is_registered():
    unregistered = []
    for cls in _all_base_prop_subclasses():
        f = cls.model_fields.get("status")
        if f is None:
            continue
        vals = get_args(f.annotation)  # Literal["x"] -> ("x",); str -> ()
        if len(vals) == 1 and isinstance(vals[0], str) and vals[0] not in _KNOWN_PROPOSAL_STATUSES:
            unregistered.append((cls.__name__, vals[0]))
    assert not unregistered, (
        f"unregistered proposal variant(s) {sorted(unregistered)} — defined as _BaseProp "
        f"subclasses with a status Literal but absent from _KNOWN_PROPOSAL_STATUSES / the union, "
        f"so their rows silently route to _UnknownProposal."
    )


# Case 9 — production write path: dump_model uses model_dump_json; an _UnknownProposal must
# survive a whole-store round-trip (dump → re-load) so the NEXT read still works. This is the
# one behavior a future serializer refactor could silently break.
def test_pending_store_round_trip_survives_unknown():
    store = PendingStore.model_validate({"proposals": {
        "P0001": _prop(status="sent", sent_ts="2026-07-10T08:00:00-04:00"),
        "P0002": _prop(proposal_id="P0002", status="future_v2_status", new_field=7),
    }})
    reloaded = PendingStore.model_validate_json(store.model_dump_json())  # matches dump_model
    assert isinstance(reloaded.proposals["P0001"], SentProposal)
    u = reloaded.proposals["P0002"]
    assert isinstance(u, _UnknownProposal)
    assert u.status == "future_v2_status"
    assert (u.model_extra or {}).get("new_field") == 7


# Case 10 — None / non-string status route to "_unknown_" then _UnknownProposal.status: str
# raises — a subset of, and strictly stricter than, "everything unknown raises" pre-change.
def test_none_and_non_str_status_raise():
    for bad in (None, 42):
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(_prop(status=bad))
