"""M3 — the deterministic quote acceptance / decline detector.

This grammar books events and closes leads, so its false-positive cost is real
money. These cells are the contract: what MUST book, what MUST close, what MUST
only ask, and — the largest section — what must do NOTHING.

Pure + cross-platform (no fcntl, no subprocess): conftest puts src/platform on
sys.path.
"""
from __future__ import annotations

import pytest

from catering_extraction import detect_quote_acceptance


def _outcome(text):
    result = detect_quote_acceptance(text)
    return result["outcome"] if result else None


# ── ACCEPT ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "we accept",
    "We accept.",
    "we accept the quote",
    "We've accepted the quote, thank you!",
    "I accept",
    "accepted",
    "Accepted!",
    "quote accepted",
    "the proposal is accepted",
    "confirmed, go ahead",
    "Confirmed. Please proceed.",
    "confirmed, let's book",
    "yes let's book it",
    "Yes, let's go ahead",
    "yep, please proceed",
    "we'd like to proceed",
    "We would like to book",
    "we'd like to go ahead with this",
    "we want to proceed",
    "let's book it",
    "Lets go ahead",
    "please proceed",
    "Please book it for us",
])
def test_explicit_acceptance(text):
    assert _outcome(text) == "accepted"


def test_acceptance_survives_a_trailing_question():
    """A real acceptance often comes with a next-step question attached. The
    question must not swallow the commitment sentence before it."""
    assert _outcome("We accept. When do you need the deposit?") == "accepted"


def test_matched_phrase_is_reported_for_the_audit_row():
    result = detect_quote_acceptance("Thanks — we accept the quote.")
    assert result["outcome"] == "accepted"
    assert "accept" in result["matched_phrase"].lower()


# ── DECLINE ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "we decline",
    "We declined, sorry.",
    "declined",
    "not going ahead",
    "we are not moving forward",
    "went with someone else",
    "We went with another caterer.",
    "we decided to go with someone else",
    "cancel the quote",
    "Please cancel our booking.",
    "cancel my order",
    "no longer need catering",
    "we no longer need it",
    "not interested",
    "we'll pass",
])
def test_explicit_decline(text):
    assert _outcome(text) == "declined"


def test_decline_wins_over_the_accept_words_it_contains():
    """"not going ahead" contains "going ahead"; decline is evaluated first."""
    assert _outcome("we are not going ahead") == "declined"


# ── AMBIGUOUS — one clarification, never a booking ──────────────────────────
@pytest.mark.parametrize("text", [
    "ok",
    "OK",
    "okay",
    "k",
    "sure",
    "great",
    "Great!",
    "sounds good",
    "Sounds good, thanks",
    "looks good",
    "perfect",
    "nice",
    "cool",
    "alright",
    "thanks",
    "thank you",
    "yes",
    "yeah",
    "yep",
    "noted",
    "got it",
    "ok thanks",
    "great, thank you",
])
def test_ambiguous_filler_never_accepts(text):
    assert _outcome(text) == "ambiguous", f"{text!r} must not book an event"


def test_ambiguous_words_with_real_content_fall_through_untouched():
    """"sounds good, add 20 plates" is an AMENDMENT. Asking "shall we book?" there
    would talk past the customer and hide the change they just made."""
    assert _outcome("sounds good, can you add 20 more plates?") is None
    assert _outcome("ok but move the date to June 20") is None


# ── NEGATION + questions: the false-positive guards ─────────────────────────
@pytest.mark.parametrize("text", [
    "we don't accept credit cards",
    "we do not accept cheques",
    "sorry, we can't accept that",
    "we won't be able to confirm today",
    "we cannot proceed until the manager returns",
    "we didn't accept the previous one either",
])
def test_negated_commitment_never_accepts(text):
    assert _outcome(text) != "accepted"


@pytest.mark.parametrize("text", [
    "do you accept venmo?",
    "do you accept credit cards?",
    "should we accept?",
    "can we book it?",
    "how do we proceed?",
    "when can we confirm?",
    "is the quote accepted on your end?",
    "can you cancel the quote if we change our mind?",
])
def test_questions_never_book_or_close(text):
    assert _outcome(text) not in ("accepted", "declined"), (
        "a question is never a commitment"
    )


# ── the vast majority of messages: no signal at all ────────────────────────
@pytest.mark.parametrize("text", [
    "",
    "   ",
    "hi",
    "what's the price per person?",
    "can you send the menu again",
    "actually make it 280 guests not 235",
    "we need vegetarian only",
    "move the date to July 19th",
    "option 2 please",
    "do you deliver to Plano?",
    "the address is 123 Main St",
    "50 people, June 15, wedding",
])
def test_no_acceptance_signal(text):
    assert _outcome(text) is None


def test_none_and_non_string_inputs_are_safe():
    assert detect_quote_acceptance(None) is None
    assert detect_quote_acceptance("") is None


# ── commitment verbs that are NOT a yes to this quote ───────────────────────
# The patterns match a commitment VERB; these are the shapes where the sentence
# around that verb means something else. Every cell here booked a real event
# before the disqualifier guard landed. Table-driven on purpose: a new phrase is
# one line.
#
# They resolve to None, not "ambiguous": each needs a real answer about the
# cancellation / meeting / condition / change, and one generic "shall we book?"
# would talk past it.
@pytest.mark.parametrize("text,expected", [
    # cancel wins over every commitment verb it shares a message with
    ("please go ahead and cancel", None),
    ("go ahead and cancel it", None),
    ("please proceed to cancel", None),
    ("confirm the cancellation please", None),
    ("we'd like to proceed — actually no, cancelling", None),
    ("lets book, sorry ignore that, we are backing out", None),
    # …but a cancellation with an object is still a real decline
    ("please go ahead and cancel the booking", "declined"),
    ("go ahead and cancel our order", "declined"),
    # the object is a conversation, not the quote
    ("lets book a call to discuss the price", None),
    ("please book us in for a tasting first", None),
    ("we'd like to book a site visit", None),
    ("please proceed with the discussion tomorrow", None),
    ("lets proceed with a call next week", None),
    ("we want to confirm a meeting for Monday", None),
    ("please book a zoom", None),
    ("we'd like to go ahead with a walkthrough", None),
    # conditional acceptance is a counter-offer
    ("we accept only if you include dessert", None),
    ("we accept if you include dessert", None),
    ("we accept but only if you can do it for $2000", None),
    ("we accept provided you include delivery", None),
    ("we'd like to proceed as long as the price stays the same", None),
    ("please proceed subject to the manager approving", None),
    # a yes that moves a material term is an amendment, not an acceptance
    ("we want to proceed with a smaller headcount of 80", None),
    ("go ahead but for 120 people", None),
    ("lets book it but make it 300 guests", None),
    ("please proceed, actually change the date to June 20", None),
    ("we'd like to proceed but only if it drops to $1800", None),
])
def test_commitment_verb_without_a_real_commitment(text, expected):
    assert _outcome(text) == expected, f"{text!r} must not book an event"


# ── the same guards must not eat real acceptances ───────────────────────────
@pytest.mark.parametrize("text", [
    "we accept the quote",
    "please go ahead",
    "confirmed, proceed with the quote",
    "we'd like to proceed",
    # a material term RESTATED is not a change — no change marker, so it books
    "we accept the quote for 250 guests",
    "we accept, see you on June 20",
    "let's book it for the 17th",
    # "tasting menu" is a menu, not an appointment
    "please proceed with the tasting menu",
])
def test_genuine_acceptance_survives_the_guards(text):
    assert _outcome(text) == "accepted"


# ── shape contract ──────────────────────────────────────────────────────────
def test_result_shape():
    result = detect_quote_acceptance("we accept")
    assert set(result) == {"outcome", "matched_phrase"}
    assert result["outcome"] in ("accepted", "declined", "ambiguous")


def test_matched_phrase_is_bounded():
    result = detect_quote_acceptance("we accept " + "x" * 500)
    assert len(result["matched_phrase"]) <= 120


def test_detector_is_deterministic():
    """Same input, same answer — this is what makes the audit row replayable."""
    for _ in range(3):
        assert detect_quote_acceptance("we accept")["outcome"] == "accepted"
