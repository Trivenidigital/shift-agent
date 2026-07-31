"""M1 — minimum lead qualification + deterministic slot-filling questions (PURE).

THE single source of truth for "is this lead complete enough to quote". Both the
write path (``create-catering-lead`` deciding QUALIFYING vs AWAITING_OWNER_APPROVAL)
and the update path (``amend-catering-lead`` deciding whether an answer closed the
loop) call the same functions here, so the gate can never disagree with itself.

WHY A GATE AT ALL: before M1 every classified inquiry went straight to
AWAITING_OWNER_APPROVAL and fired an owner approval card plus an F14 sample menu.
A message like "do you cater?" produced an owner card with "(no structured fields
extracted)" — the owner is asked to approve a quote for an event whose date, size,
venue, and style are all unknown. The gate parks such a lead in QUALIFYING and asks
for the missing fields instead.

DETERMINISTIC ONLY — fixed question templates, no LLM. The question TEXT is
rendered from a field NAME at send time; leads persist field names
(``pending_questions`` / ``questions_asked``), never rendered prose, so editing a
template can never strand a lead mid-loop.

Deployed FLAT to /opt/shift-agent/ so scripts + the cf-router plugin can import it.
"""
from __future__ import annotations

from typing import Any, Optional

# Minimum qualification. Names are the QUALIFICATION vocabulary (what the customer
# is asked for), mapped below to the CateringLeadExtractedFields that satisfy them —
# "guest_count" is the question, `headcount` is the storage field.
REQUIRED_FIELDS: tuple[str, ...] = (
    "event_date",
    "guest_count",
    "event_type",
    "venue",
    "service_style",
    "veg_nonveg",
)

# Questions per round. 2 keeps a first contact from reading as an interrogation;
# 4 is the ceiling a single WhatsApp message can carry without scrolling.
MIN_QUESTIONS_PER_ROUND = 2
MAX_QUESTIONS_PER_ROUND = 4

# After this many rounds the lead goes to the owner WITH its gaps flagged rather
# than asking again. A customer who has answered twice and still left fields blank
# is telling us something the third question will not fix, and a bot that keeps
# asking is worse than an owner who calls.
MAX_QUALIFICATION_ROUNDS = 3

# Free-text fields — no vocabulary or number to match on, so a short bare reply
# ("Grand Ballroom") is only interpretable as an answer when it is the ONLY thing
# outstanding. See `sole_free_text_question`.
FREE_TEXT_FIELDS: frozenset[str] = frozenset({"venue"})

QUESTION_TEMPLATES: dict[str, str] = {
    "event_date": "What date is your event?",
    "guest_count": "Roughly how many guests?",
    "event_type": "What kind of event is it (wedding, birthday, corporate, religious)?",
    "venue": "Where is it being held?",
    "service_style": "How would you like it served — buffet, plated, boxed, or family style?",
    "veg_nonveg": "Any veg / non-veg split or dietary needs we should plan around?",
}

# Owner-card labels for gaps that are still open when a lead reaches owner review.
GAP_LABELS: dict[str, str] = {
    "event_date": "event date",
    "guest_count": "guest count",
    "event_type": "event type",
    "venue": "venue",
    "service_style": "service style",
    "veg_nonveg": "veg/non-veg split",
}

VENUE_MAX_LEN = 200

# Explicit NON-answers. A customer who replies "not sure yet" to "where is it being
# held?" has told us they do not know — writing that string into `venue` would put
# a sentence on the owner card in the place the owner reads as the venue, and would
# mark the field satisfied so nobody ever asks again. Narrow and literal on purpose:
# this is a reject list for one structured slot, not a whitelist standing in for
# intent classification. Anything not listed is accepted as a venue.
NON_ANSWER_PHRASES: frozenset[str] = frozenset({
    "not sure", "not sure yet", "unsure", "no idea", "dont know", "don't know",
    "do not know", "i dont know", "i don't know", "idk", "tbd", "to be decided",
    "to be confirmed", "not yet", "not decided", "undecided", "unknown", "maybe",
    "n/a", "na", "none", "nothing", "no", "nope", "yes", "yeah", "ok", "okay",
    "sure", "thanks", "thank you", "hi", "hello",
})


def _attr(obj: Any, name: str) -> Any:
    """Read an attribute from a model OR a key from a dict (None if absent)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _extracted_of(lead: Any) -> Any:
    """The extracted block of a lead, whether `lead` is a CateringLead, a plain
    lead dict, or already the extracted block itself."""
    extracted = _attr(lead, "extracted")
    return lead if extracted is None else extracted


def _satisfied(extracted: Any, field: str) -> bool:
    """Is one REQUIRED_FIELDS entry answered on this extracted block?"""
    if field == "event_date":
        return bool(_attr(extracted, "event_date"))
    if field == "guest_count":
        return _attr(extracted, "headcount") is not None
    if field == "event_type":
        return bool(_attr(extracted, "event_type"))
    if field == "venue":
        return bool(_attr(extracted, "venue"))
    if field == "service_style":
        return bool(_attr(extracted, "service_style"))
    if field == "veg_nonveg":
        # Either explicit counts OR any dietary tag counts as an indication —
        # "all vegetarian" is as usable a planning answer as "90/30".
        return (
            _attr(extracted, "veg_guest_count") is not None
            or _attr(extracted, "nonveg_guest_count") is not None
            or bool(_attr(extracted, "dietary_restrictions"))
        )
    return True  # unknown field name is never a blocker


def missing_fields(lead: Any) -> list[str]:
    """REQUIRED_FIELDS with no answer yet, in REQUIRED_FIELDS order."""
    extracted = _extracted_of(lead)
    return [f for f in REQUIRED_FIELDS if not _satisfied(extracted, f)]


def qualification_state(lead: Any) -> dict:
    """{"complete": bool, "missing": [field], "asked": [field]}.

    `lead` may be a CateringLead model, a plain lead dict, or a bare extracted-fields
    dict (in which case `asked` is empty — nothing has been asked of a lead that does
    not exist yet). This is the ONE function that decides whether a lead may proceed
    to a full proposal.
    """
    missing = missing_fields(lead)
    asked = _attr(lead, "questions_asked") or []
    return {
        "complete": not missing,
        "missing": missing,
        "asked": [a for a in asked if isinstance(a, str)],
    }


def next_question_batch(lead: Any, *, limit: int = MAX_QUESTIONS_PER_ROUND) -> list[str]:
    """The next batch of field names to ask: still missing AND never asked before.

    Never re-asks an answered field (it is no longer missing) and never re-asks an
    unanswered one either — a customer who skipped "where is it being held?" once
    is not helped by being asked again; that gap goes to the owner instead.
    """
    asked = set(_attr(lead, "questions_asked") or [])
    fresh = [f for f in missing_fields(lead) if f not in asked]
    return fresh[:max(1, limit)]


def sole_free_text_question(pending: Any) -> Optional[str]:
    """The field name when EXACTLY ONE question is outstanding and it is free-text.

    This is the only situation in which a short bare reply can be safely read as an
    answer: with "Grand Ballroom" and two open questions there is no deterministic
    way to know which one it answers, and a wrong assignment writes customer content
    into the wrong field. One open free-text question makes it unambiguous.
    """
    pending = [p for p in (pending or []) if isinstance(p, str)]
    if len(pending) != 1:
        return None
    return pending[0] if pending[0] in FREE_TEXT_FIELDS else None


def clean_free_text_answer(text: str) -> Optional[str]:
    """A short bare reply normalised into a venue value, or None if it does not look
    like one. Rejects empty / over-long / pure-number replies and the explicit
    NON_ANSWER_PHRASES, so "150", "ok" or "not sure yet" never lands in the venue
    field — and, just as importantly, never marks the venue answered."""
    candidate = " ".join((text or "").split())
    if not (2 <= len(candidate) <= VENUE_MAX_LEN):
        return None
    if candidate.replace(",", "").replace(".", "").isdigit():
        return None
    if candidate.lower().strip(" .!?").rstrip(",") in NON_ANSWER_PHRASES:
        return None
    return candidate


def gap_summary(missing: Any) -> str:
    """Human labels for the gaps still open, for the owner card."""
    labels = [GAP_LABELS.get(f, f) for f in (missing or []) if isinstance(f, str)]
    return ", ".join(labels)


def render_question_message(lead_id: str, fields: Any, *, first_contact: bool) -> str:
    """ONE warm, concise customer message asking the given fields.

    HARD RULES compliance: fixed templates — no LLM composition, no prices, no menu
    items, no promises about availability. Just an acknowledgement and the questions.
    """
    questions = [QUESTION_TEMPLATES[f] for f in (fields or []) if f in QUESTION_TEMPLATES]
    if not questions:
        return ""
    opener = (
        "Thanks for reaching out about catering! So we can put together the right "
        "plan, could you share:"
        if first_contact else
        "Thank you — that helps. Just a couple more things:"
    )
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
    return f"{opener}\n{numbered}\n\nJust reply here and we'll take it from there. (Ref: {lead_id})"


def render_complete_message(lead_id: str) -> str:
    """Customer message once minimum qualification is met."""
    return (
        f"Perfect — we have everything we need for now. The owner will review the "
        f"details and follow up with a quote shortly. (Ref: {lead_id})"
    )


def render_handoff_message(lead_id: str) -> str:
    """Customer message when the round cap is reached with gaps still open."""
    return (
        f"Thanks — I've passed your enquiry to the owner, who'll follow up directly "
        f"to fill in the remaining details. (Ref: {lead_id})"
    )


__all__ = [
    "REQUIRED_FIELDS", "QUESTION_TEMPLATES", "GAP_LABELS", "FREE_TEXT_FIELDS",
    "NON_ANSWER_PHRASES",
    "MIN_QUESTIONS_PER_ROUND", "MAX_QUESTIONS_PER_ROUND", "MAX_QUALIFICATION_ROUNDS",
    "VENUE_MAX_LEN",
    "qualification_state", "missing_fields", "next_question_batch",
    "sole_free_text_question", "clean_free_text_answer", "gap_summary",
    "render_question_message", "render_complete_message", "render_handoff_message",
]
