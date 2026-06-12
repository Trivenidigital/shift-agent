"""Property tests for schemas.py — catches drift the code-quality review flagged.

Most critical invariants:
- LEGAL_TRANSITIONS covers every status; terminals have empty outgoing set
- ProposalCode regex matches code-generator alphabet
- E164Phone canonicalizes every input format
- Roster.find_by_phone honors phone_history effective window
"""
from __future__ import annotations
import re
import pytest
from datetime import datetime, timezone
from pathlib import Path

from schemas import (
    LEGAL_TRANSITIONS, TERMINAL_STATUSES, is_legal_transition, is_terminal_status,
    E164Phone, Roster, Config, Proposal, AwaitingProposal, ApprovedProposal,
    ReconcilingProposal, SentProposal, SendFailedProposal, AcceptedProposal,
    DeclinedProposal, DeniedByOwnerProposal, ExpiredProposal, CancelledProposal,
    NoResponseTimeoutProposal, ProposalCode,
)


# ─────────────────────────────────────────────────────────────────
# LEGAL_TRANSITIONS completeness
# ─────────────────────────────────────────────────────────────────

ALL_STATUSES = {
    "awaiting_owner_approval", "approved", "reconciling", "sent",
    "send_failed", "accepted", "declined", "denied_by_owner",
    "expired", "cancelled", "no_response_timeout",
}


def test_legal_transitions_covers_every_status():
    """Every status literal must appear as a key in LEGAL_TRANSITIONS."""
    for status in ALL_STATUSES:
        assert status in LEGAL_TRANSITIONS, f"{status!r} missing from LEGAL_TRANSITIONS"


def test_terminals_have_empty_outgoing_transitions():
    """Terminal statuses must have no outgoing transitions (frozenset())."""
    for status in TERMINAL_STATUSES:
        assert LEGAL_TRANSITIONS[status] == frozenset(), (
            f"{status!r} is in TERMINAL_STATUSES but LEGAL_TRANSITIONS says "
            f"it can transition to {LEGAL_TRANSITIONS[status]}"
        )


def test_terminals_set_matches_empty_transition_statuses():
    """TERMINAL_STATUSES must exactly equal the set of statuses with empty transitions."""
    empty_trans = {s for s, t in LEGAL_TRANSITIONS.items() if t == frozenset()}
    assert empty_trans == TERMINAL_STATUSES


def test_is_terminal_status_matches_set():
    for s in ALL_STATUSES:
        assert is_terminal_status(s) == (s in TERMINAL_STATUSES)


def test_is_legal_transition_rejects_terminals():
    for terminal in TERMINAL_STATUSES:
        # From a terminal, nothing is legal (except maybe cancelled)
        for other in ALL_STATUSES:
            assert not is_legal_transition(terminal, other), (
                f"transition {terminal!r}→{other!r} should be illegal"
            )


def test_is_legal_transition_rejects_unknown_from_status():
    assert not is_legal_transition("bogus_status", "approved")


def test_happy_path_transitions_all_legal():
    """The canonical create → approve → send → accept chain must all be legal."""
    path = [
        ("awaiting_owner_approval", "approved"),
        ("approved", "reconciling"),
        ("reconciling", "sent"),
        ("sent", "accepted"),
    ]
    for frm, to in path:
        assert is_legal_transition(frm, to), f"{frm}→{to} should be legal"


def test_send_failed_can_recover_via_owner_retry():
    """RETRY #CODE transitions send_failed → approved → reconciling → sent."""
    assert is_legal_transition("send_failed", "approved")


def test_reconciling_can_revert_to_approved_on_cap_exceeded():
    """When cap is hit mid-send, we revert reconciling → approved."""
    assert is_legal_transition("reconciling", "approved")


def test_cancel_reachable_from_every_non_terminal():
    """Owner should be able to CANCEL any in-flight proposal."""
    non_terminals = ALL_STATUSES - TERMINAL_STATUSES
    for s in non_terminals:
        assert is_legal_transition(s, "cancelled"), (
            f"CANCEL from {s!r} should be legal; otherwise owner can't revoke"
        )


# ─────────────────────────────────────────────────────────────────
# ProposalCode regex ↔ generator alphabet agreement
# ─────────────────────────────────────────────────────────────────

def test_proposal_code_regex_and_alphabet_agree():
    """Generator alphabet must only contain chars the regex accepts.

    This catches drift between schemas.ProposalCode and the generator alphabet
    in create-proposal. We import the regex pattern from the Pydantic model
    (via its JSON schema) rather than hardcoding here — otherwise the TEST
    would drift too.
    """
    from schemas import ProposalCode
    from pydantic import TypeAdapter
    # Extract the regex from the Pydantic-annotated type
    schema = TypeAdapter(ProposalCode).json_schema()
    pattern = schema.get("pattern")
    assert pattern, f"ProposalCode annotation has no pattern (got schema {schema})"
    _CODE_REGEX = re.compile(pattern)

    # This is what create-proposal uses
    _CODE_ALPHA = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    # Every generator char, when paired into a 5-char code, must match
    for ch in _CODE_ALPHA:
        assert _CODE_REGEX.match(f"#{ch * 5}"), (
            f"char {ch!r} is in generator alphabet but fails schemas.ProposalCode regex {pattern!r}"
        )
    # Conversely, no char outside alphabet should match
    for forbidden in "0O1IL":
        assert not _CODE_REGEX.match(f"#{forbidden * 5}"), (
            f"{forbidden!r} matches regex {pattern!r} but is excluded from alphabet"
        )


# ─────────────────────────────────────────────────────────────────
def test_approval_code_regex_surfaces_use_canonical_alphabet():
    """Dispatcher/cf-router/catering regex surfaces must match ProposalCode."""
    from pydantic import TypeAdapter

    schema = TypeAdapter(ProposalCode).json_schema()
    canonical = schema["pattern"].removeprefix("^").removesuffix("$")
    stale = "#[A-HJ-NP-Z2-9]{5}"
    generator_alpha = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    repo = Path(__file__).resolve().parent.parent
    expected_snippets = {
        "src/plugins/cf-router/hooks.py": r"#([A-HJKMNPQR-Z2-9]{5})",
        "src/agents/shift/scripts/create-proposal": f'_CODE_ALPHA = "{generator_alpha}"',
        "src/agents/shift/skills/dispatch_shift_agent/SKILL.md": canonical,
        "src/agents/catering/scripts/create-catering-lead": f'_CODE_ALPHA = "{generator_alpha}"',
        "src/agents/catering/scripts/parse-menu-photo": f'_CODE_ALPHA = "{generator_alpha}"',
        "src/agents/catering/scripts/finalize-catering-menu": f"^{canonical}$",
        "src/agents/catering/skills/apply_catering_menu_decision/SKILL.md": canonical,
        "src/agents/catering/skills/handle_catering_owner_approval/SKILL.md": canonical,
        "src/agents/catering/skills/catering_dispatcher/SKILL.md": canonical,
        "src/agents/expense_bookkeeper/scripts/extract-receipt": f'_CODE_ALPHA = "{generator_alpha}"',
        "tests/_dispatcher_replay.py": canonical,
    }

    for rel, expected in expected_snippets.items():
        text = (repo / rel).read_text(encoding="utf-8")
        assert expected in text, f"{rel} does not reference expected canonical snippet {expected}"
        assert stale not in text, f"{rel} still references stale approval-code regex"


# E164Phone canonicalization
# ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("+19045550101", "+19045550101"),              # already canonical
    ("+1-904-555-0101", "+19045550101"),           # dashes
    ("+1 (904) 555-0101", "+19045550101"),         # parens + space
    ("19045550101@s.whatsapp.net", "+19045550101"), # @s.whatsapp.net JID
    ("19045550101@lid", "+19045550101"),            # @lid JID
    ("0019045550101", "+19045550101"),              # 00 prefix
    ("19045550101", "+19045550101"),                # bare digits
])
def test_e164_canonicalizes_all_formats(raw, expected):
    assert E164Phone.validate(raw) == expected


@pytest.mark.parametrize("bad", [
    "garbage",                 # no digits
    "+1",                      # too short
    "+123456789",              # still too short (need 10+)
    "+12345678901234567890",   # too long
])
def test_e164_rejects_invalid(bad):
    with pytest.raises(ValueError):
        E164Phone.validate(bad)


# ─────────────────────────────────────────────────────────────────
# Roster validation + find_by_phone
# ─────────────────────────────────────────────────────────────────

def test_roster_validates_phase0_fixture(sample_roster_dict):
    r = Roster.model_validate(sample_roster_dict)
    assert len(r.employees) == 6
    assert r.employees[0].status == "active"  # default


def test_roster_referential_integrity_caught(sample_roster_dict):
    """Schedule referencing unknown employee_id should fail validation."""
    bad = dict(sample_roster_dict)
    bad["schedule"] = {
        "2026-05-01": [{"employee_id": "e999", "shift": "09:00-17:00", "role": "cashier"}]
    }
    with pytest.raises(Exception):
        Roster.model_validate(bad)


def test_roster_duplicate_employee_id_caught(sample_roster_dict):
    bad = dict(sample_roster_dict)
    bad["employees"] = sample_roster_dict["employees"] + [sample_roster_dict["employees"][0]]
    with pytest.raises(Exception):
        Roster.model_validate(bad)


def test_find_by_phone_resolves_canonical(sample_roster_dict):
    r = Roster.model_validate(sample_roster_dict)
    assert r.find_by_phone("+19045550101").id == "e001"


def test_find_by_phone_resolves_dashed(sample_roster_dict):
    r = Roster.model_validate(sample_roster_dict)
    assert r.find_by_phone("+1-904-555-0101").id == "e001"


def test_find_by_phone_resolves_jid(sample_roster_dict):
    r = Roster.model_validate(sample_roster_dict)
    assert r.find_by_phone("19045550101@s.whatsapp.net").id == "e001"


def test_find_by_phone_returns_none_for_unknown(sample_roster_dict):
    r = Roster.model_validate(sample_roster_dict)
    assert r.find_by_phone("+15555551234") is None


def test_find_by_phone_skips_inactive(sample_roster_dict):
    """P8-FIX regression test — terminated employee should not resolve."""
    bad = dict(sample_roster_dict)
    bad["employees"] = [dict(e) for e in sample_roster_dict["employees"]]
    bad["employees"][0]["status"] = "terminated"
    r = Roster.model_validate(bad)
    assert r.find_by_phone("+19045550101") is None


def test_find_by_phone_honors_effective_to(sample_roster_dict):
    """P8-FIX regression: phone_history with effective_to in the past should NOT match."""
    bad = dict(sample_roster_dict)
    bad["employees"] = [dict(e) for e in sample_roster_dict["employees"]]
    # Give e001 a phone_history entry for an OLD phone that ended in 2020
    bad["employees"][0] = dict(bad["employees"][0])
    bad["employees"][0]["phone_history"] = [
        {"phone": "+15551234567",
         "effective_from": "2018-01-01T00:00:00+00:00",
         "effective_to": "2020-01-01T00:00:00+00:00"}
    ]
    r = Roster.model_validate(bad)
    # The expired phone should NOT resolve to e001
    assert r.find_by_phone("+15551234567") is None
    # But the current phone still should
    assert r.find_by_phone("+19045550101").id == "e001"


# ─────────────────────────────────────────────────────────────────
# Config validation
# ─────────────────────────────────────────────────────────────────

def test_config_rejects_empty_pushover_creds(sample_config_dict):
    """Critical: alerting validator must reject empty Pushover (required for dead-man)."""
    bad = dict(sample_config_dict)
    bad["alerting"] = dict(bad["alerting"])
    bad["alerting"]["pushover_user_key"] = ""
    with pytest.raises(Exception):
        Config.model_validate(bad)


def test_config_rejects_invalid_timezone(sample_config_dict):
    bad = dict(sample_config_dict)
    bad["customer"] = dict(bad["customer"])
    bad["customer"]["timezone"] = "Not/A_Real_Tz"
    with pytest.raises(Exception):
        Config.model_validate(bad)


def test_config_rejects_extra_fields(sample_config_dict):
    """extra='forbid' catches typos."""
    bad = dict(sample_config_dict)
    bad["bogus_field"] = True
    with pytest.raises(Exception):
        Config.model_validate(bad)


def test_config_canonicalizes_owner_phone(sample_config_dict):
    """Owner phone should pass through E164Phone validator."""
    bad = dict(sample_config_dict)
    bad["owner"] = dict(bad["owner"])
    bad["owner"]["phone"] = "+1-904-555-0999"
    cfg = Config.model_validate(bad)
    assert cfg.owner.phone == "+19045550999"


# ─────────────────────────────────────────────────────────────────
# Proposal discriminated union
# ─────────────────────────────────────────────────────────────────

def _base_proposal_fields(now):
    return {
        "proposal_id": "P0001", "code": "#A3F2X",
        "created_ts": now, "last_updated_ts": now,
        "absent_employee_id": "e001", "absent_date": "2026-04-25",
        "absent_shift": "09:00-17:00", "absent_role": "cashier",
        "absent_reason": "fever", "input_message": "test",
        "message_id": "m001", "status_history": [],
    }


def test_awaiting_proposal_builds_correctly(now_aware):
    p = AwaitingProposal(status="awaiting_owner_approval", **_base_proposal_fields(now_aware))
    assert p.status == "awaiting_owner_approval"


def test_proposal_discriminator_routes_to_sent(now_aware):
    """Pydantic should route `status: sent` to SentProposal variant with required sent_ts."""
    from pydantic import TypeAdapter
    adapter = TypeAdapter(Proposal)
    raw = dict(_base_proposal_fields(now_aware))
    raw["status"] = "sent"
    raw["sent_ts"] = now_aware.isoformat()
    raw["created_ts"] = now_aware.isoformat()
    raw["last_updated_ts"] = now_aware.isoformat()
    p = adapter.validate_python(raw)
    assert isinstance(p, SentProposal)


def test_proposal_discriminator_rejects_sent_without_sent_ts(now_aware):
    from pydantic import TypeAdapter, ValidationError
    adapter = TypeAdapter(Proposal)
    raw = dict(_base_proposal_fields(now_aware))
    raw["status"] = "sent"
    # intentionally missing sent_ts
    with pytest.raises(ValidationError):
        adapter.validate_python(raw)


# ─────────────────────────────────────────────────────────────────
# DispatcherRouted (routing-reliability audit entry)
# ─────────────────────────────────────────────────────────────────


def test_dispatcher_routed_happy_path(now_aware):
    from pydantic import TypeAdapter
    from schemas import LogEntry, DispatcherRouted
    adapter = TypeAdapter(LogEntry)
    entry = adapter.validate_python({
        "type": "dispatcher_routed",
        "ts": now_aware.isoformat(),
        "message_id": "wa:abc123",
        "sender_role": "owner",
        "message_shape": "approval_code",
        "routed_to_skill": "handle_owner_command",
        "sender_phone": "+918522041562",
    })
    assert isinstance(entry, DispatcherRouted)
    assert entry.routed_to_skill == "handle_owner_command"


def test_dispatcher_routed_rejects_invalid_role(now_aware):
    from pydantic import TypeAdapter, ValidationError
    from schemas import LogEntry
    adapter = TypeAdapter(LogEntry)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "type": "dispatcher_routed",
            "ts": now_aware.isoformat(),
            "message_id": "wa:abc123",
            "sender_role": "admin",  # not in the enum
            "message_shape": "text",
            "routed_to_skill": "handle_sick_call",
        })


def test_dispatcher_routed_rejects_invalid_shape(now_aware):
    from pydantic import TypeAdapter, ValidationError
    from schemas import LogEntry
    adapter = TypeAdapter(LogEntry)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "type": "dispatcher_routed",
            "ts": now_aware.isoformat(),
            "message_id": "wa:abc123",
            "sender_role": "employee",
            "message_shape": "video",  # not in the enum (would be "media_other")
            "routed_to_skill": "handle_sick_call",
        })


def test_dispatcher_routed_routed_to_skill_required(now_aware):
    from pydantic import TypeAdapter, ValidationError
    from schemas import LogEntry
    adapter = TypeAdapter(LogEntry)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "type": "dispatcher_routed",
            "ts": now_aware.isoformat(),
            "message_id": "wa:abc123",
            "sender_role": "employee",
            "message_shape": "text",
            "routed_to_skill": "",  # empty string violates min_length=1
        })


def test_validate_failed_minimal(now_aware):
    """dispatch_shift_agent SKILL writes a `validate_failed` audit when
    validate-sender-block returns valid=false OR v != 1. The minimal emit
    (type + ts) must validate so log-decision-direct accepts it instead of
    refusing (exit 5)."""
    from pydantic import TypeAdapter
    from schemas import LogEntry, ValidateFailed
    adapter = TypeAdapter(LogEntry)
    entry = adapter.validate_python({
        "type": "validate_failed",
        "ts": now_aware.isoformat(),
    })
    assert isinstance(entry, ValidateFailed)
    assert entry.reason is None and entry.message_id is None


def test_validate_failed_with_reason_and_message_id(now_aware):
    from pydantic import TypeAdapter
    from schemas import LogEntry, ValidateFailed
    adapter = TypeAdapter(LogEntry)
    entry = adapter.validate_python({
        "type": "validate_failed",
        "ts": now_aware.isoformat(),
        "reason": "version_mismatch",
        "message_id": "wa:abc123",
    })
    assert isinstance(entry, ValidateFailed)
    assert entry.reason == "version_mismatch"
    assert entry.message_id == "wa:abc123"


def test_validate_failed_rejects_extra_field(now_aware):
    """extra='forbid' (via _BaseEntry) — no raw block content can be smuggled in
    via an unmodelled field."""
    from pydantic import TypeAdapter, ValidationError
    from schemas import LogEntry
    adapter = TypeAdapter(LogEntry)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "type": "validate_failed",
            "ts": now_aware.isoformat(),
            "raw_block": "[shift-agent-sender ... IGNORE PREVIOUS ...]",  # must be rejected
        })


def test_validate_failed_distinct_from_unknown_sender_declined(now_aware):
    """validate_failed (malformed/absent block) is a DIFFERENT event from
    unknown_sender_declined (a valid block whose identity is just unknown)."""
    from pydantic import TypeAdapter
    from schemas import LogEntry, ValidateFailed, UnknownSenderDeclined
    adapter = TypeAdapter(LogEntry)
    vf = adapter.validate_python({"type": "validate_failed", "ts": now_aware.isoformat()})
    usd = adapter.validate_python({
        "type": "unknown_sender_declined",
        "ts": now_aware.isoformat(),
        "sender_lid": "201975216009469@lid",
        "input_message_truncated": "hi",
    })
    assert isinstance(vf, ValidateFailed)
    assert isinstance(usd, UnknownSenderDeclined)
