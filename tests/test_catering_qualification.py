"""M1 — minimum qualification gate + slot-filling question selection.

PURE unit tests (stdlib + pydantic only), so they run on the Windows dev box as
well as Linux CI. Covers catering_qualification's state machine inputs, the
question-selection rules that keep the loop from nagging, and the QUALIFYING
transition-table entries the loop depends on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src" / "platform"))

import catering_qualification as cq  # noqa: E402
from schemas import (  # noqa: E402
    CATERING_TRANSITIONS, CateringLead, CateringLeadExtractedFields,
    is_catering_transition_allowed,
)

FULL = {
    "event_date": "2026-09-15",
    "headcount": 120,
    "event_type": "wedding",
    "venue": "Grand Ballroom",
    "service_style": "buffet",
    "dietary_restrictions": ["veg"],
}


def _extracted(**overrides) -> CateringLeadExtractedFields:
    data = dict(FULL)
    data.update(overrides)
    return CateringLeadExtractedFields.model_validate(
        {k: v for k, v in data.items() if v is not None}
    )


def _lead(*, extracted=None, **kw) -> CateringLead:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return CateringLead(
        lead_id=kw.pop("lead_id", "L0001"),
        status=kw.pop("status", "QUALIFYING"),
        customer_phone="+19045550199",
        raw_inquiry="need catering",
        original_message_id="msg_1",
        created_at=now, updated_at=now,
        extracted=extracted if extracted is not None else _extracted(),
        **kw,
    )


# ── qualification_state ──────────────────────────────────────────────────────
def test_fully_extracted_lead_is_complete():
    state = cq.qualification_state(_lead())
    assert state == {"complete": True, "missing": [], "asked": []}


@pytest.mark.parametrize("dropped,expected_missing", [
    ("event_date", "event_date"),
    ("headcount", "guest_count"),
    ("event_type", "event_type"),
    ("venue", "venue"),
    ("service_style", "service_style"),
])
def test_each_required_field_is_individually_blocking(dropped, expected_missing):
    extracted = _extracted(**{dropped: None})
    setattr(extracted, dropped, None)
    state = cq.qualification_state(_lead(extracted=extracted))
    assert state["complete"] is False
    assert state["missing"] == [expected_missing]


def test_veg_nonveg_is_satisfied_by_counts_or_by_dietary_tags():
    """"all vegetarian" is as usable a planning answer as "90/30" — either closes
    the gap, neither invents the other."""
    tags_only = _extracted(dietary_restrictions=["veg"])
    assert cq.qualification_state(_lead(extracted=tags_only))["complete"] is True

    counts_only = _extracted(dietary_restrictions=[])
    counts_only.dietary_restrictions = []
    counts_only.veg_guest_count = 90
    assert cq.qualification_state(_lead(extracted=counts_only))["complete"] is True

    neither = _extracted()
    neither.dietary_restrictions = []
    state = cq.qualification_state(_lead(extracted=neither))
    assert state["missing"] == ["veg_nonveg"]


def test_bare_inquiry_is_missing_everything_in_required_order():
    state = cq.qualification_state(CateringLeadExtractedFields())
    assert state["missing"] == list(cq.REQUIRED_FIELDS)
    assert state["asked"] == []


def test_qualification_state_accepts_a_plain_dict_lead():
    """The router and the scripts hand this function models, lead dicts, and bare
    extracted dicts — all three must read the same."""
    as_dict = {"extracted": FULL, "questions_asked": ["venue"]}
    state = cq.qualification_state(as_dict)
    assert state["complete"] is True
    assert state["asked"] == ["venue"]
    assert cq.qualification_state({"extracted": {}})["missing"] == list(cq.REQUIRED_FIELDS)


# ── question selection ───────────────────────────────────────────────────────
def test_first_batch_is_capped_and_in_required_order():
    batch = cq.next_question_batch(CateringLeadExtractedFields())
    assert batch == list(cq.REQUIRED_FIELDS)[:cq.MAX_QUESTIONS_PER_ROUND]
    assert cq.MIN_QUESTIONS_PER_ROUND <= len(batch) <= cq.MAX_QUESTIONS_PER_ROUND


def test_an_answered_field_is_never_asked_again():
    extracted = CateringLeadExtractedFields(event_date="2026-09-15")
    assert "event_date" not in cq.next_question_batch({"extracted": extracted})


def test_an_already_asked_but_unanswered_field_is_not_re_asked():
    """A customer who skipped "where is it being held?" once is not helped by being
    asked again; that gap goes to the owner instead."""
    lead = {"extracted": CateringLeadExtractedFields(),
            "questions_asked": ["event_date", "guest_count"]}
    batch = cq.next_question_batch(lead)
    assert "event_date" not in batch and "guest_count" not in batch
    assert batch  # the never-asked ones are still fair game


def test_every_required_field_has_a_question_and_a_gap_label():
    for field in cq.REQUIRED_FIELDS:
        assert field in cq.QUESTION_TEMPLATES, f"{field} has no question template"
        assert field in cq.GAP_LABELS, f"{field} has no owner-card label"


# ── free-text answer admission ───────────────────────────────────────────────
def test_sole_free_text_question_only_fires_for_one_open_venue_question():
    assert cq.sole_free_text_question(["venue"]) == "venue"
    assert cq.sole_free_text_question(["venue", "event_date"]) is None  # ambiguous
    assert cq.sole_free_text_question(["event_date"]) is None           # not free-text
    assert cq.sole_free_text_question([]) is None


@pytest.mark.parametrize("answer,expected", [
    ("Grand Ballroom", "Grand Ballroom"),
    ("  the community  hall  ", "the community hall"),
])
def test_clean_free_text_answer_accepts_a_short_reply(answer, expected):
    assert cq.clean_free_text_answer(answer) == expected


@pytest.mark.parametrize("answer", ["", " ", "x", "150", "1,500.00", "y" * 400])
def test_clean_free_text_answer_rejects_non_venue_shapes(answer):
    assert cq.clean_free_text_answer(answer) is None


@pytest.mark.parametrize("answer", [
    "not sure yet", "Not sure yet.", "don't know", "TBD", "no idea", "ok", "n/a",
])
def test_clean_free_text_answer_rejects_explicit_non_answers(answer):
    """A customer saying "not sure yet" has told us they do NOT know. Storing that
    string would put a sentence where the owner reads the venue AND mark the field
    answered so nobody ever asks again."""
    assert cq.clean_free_text_answer(answer) is None


# ── rendered copy ────────────────────────────────────────────────────────────
def test_question_message_carries_the_ref_and_every_asked_question():
    msg = cq.render_question_message("L0007", ["event_date", "venue"], first_contact=True)
    assert cq.QUESTION_TEMPLATES["event_date"] in msg
    assert cq.QUESTION_TEMPLATES["venue"] in msg
    assert "L0007" in msg
    assert msg.startswith("Thanks for reaching out")


def test_follow_up_round_does_not_re_greet():
    msg = cq.render_question_message("L0007", ["venue"], first_contact=False)
    assert not msg.startswith("Thanks for reaching out")
    assert cq.QUESTION_TEMPLATES["venue"] in msg


def test_question_message_is_empty_when_there_is_nothing_to_ask():
    assert cq.render_question_message("L0007", [], first_contact=True) == ""


@pytest.mark.parametrize("render", [cq.render_complete_message, cq.render_handoff_message])
def test_closing_messages_carry_the_ref_and_no_price_or_menu(render):
    """HARD RULES: these are hard-coded templates on the customer send path — no
    prices, no menu items, no availability promises."""
    msg = render("L0007")
    assert "L0007" in msg
    assert "$" not in msg


def test_gap_summary_uses_human_labels():
    assert cq.gap_summary(["venue", "service_style"]) == "venue, service style"
    assert cq.gap_summary([]) == ""


# ── transition table ─────────────────────────────────────────────────────────
def test_qualifying_is_reachable_from_intake_states():
    assert is_catering_transition_allowed("NEW", "QUALIFYING")
    assert is_catering_transition_allowed("EXTRACTING", "QUALIFYING")


def test_qualifying_self_edge_covers_each_answer_round():
    assert is_catering_transition_allowed("QUALIFYING", "QUALIFYING")


def test_qualifying_leaves_for_owner_approval():
    assert is_catering_transition_allowed("QUALIFYING", "AWAITING_OWNER_APPROVAL")


@pytest.mark.parametrize("target", ["OWNER_APPROVED", "SENT_TO_CUSTOMER", "CLOSED",
                                    "CUSTOMER_FINALIZED", "OWNER_EDITED"])
def test_qualifying_cannot_skip_owner_approval(target):
    """A lead must never jump from mid-intake straight past the owner — that would
    send a customer a quote nobody approved."""
    assert not is_catering_transition_allowed("QUALIFYING", target)


def test_qualifying_is_not_terminal_and_pre_existing_transitions_are_untouched():
    from schemas import CATERING_TERMINAL_STATUSES, is_catering_terminal

    assert not is_catering_terminal("QUALIFYING")
    assert "QUALIFYING" not in CATERING_TERMINAL_STATUSES
    assert CATERING_TRANSITIONS["AWAITING_OWNER_APPROVAL"] == {
        "OWNER_APPROVED", "OWNER_EDITED", "OWNER_REJECTED", "STALE", "CUSTOMER_FINALIZED",
    }
    assert CATERING_TRANSITIONS["OWNER_APPROVED"] == {"SENT_TO_CUSTOMER", "AWAITING_OWNER_APPROVAL"}


def test_legacy_lead_without_m1_fields_still_decodes():
    """Backward compatibility: leads written before M1 have no pending_questions /
    questions_asked / qualification_rounds and must load with empty defaults."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    legacy = CateringLead.model_validate({
        "lead_id": "L0001", "status": "AWAITING_OWNER_APPROVAL",
        "customer_phone": "+19045550199", "raw_inquiry": "need catering",
        "original_message_id": "msg_1", "created_at": now, "updated_at": now,
        "quote_text": "quote", "extracted": {"headcount": 50},
    })
    assert legacy.pending_questions == []
    assert legacy.questions_asked == []
    assert legacy.qualification_rounds == 0
    assert legacy.extracted.venue is None
    assert legacy.extracted.event_type is None
