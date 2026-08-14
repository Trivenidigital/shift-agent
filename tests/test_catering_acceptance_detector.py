"""M3 — the deterministic quote acceptance / decline detector.

This grammar books events and closes leads, so its false-positive cost is real
money. These cells are the contract: what MUST book, what MUST close, what MUST
only ask, and — the largest section — what must do NOTHING.

Pure + cross-platform (no fcntl, no subprocess): conftest puts src/platform on
sys.path.
"""
from __future__ import annotations

import time

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
    # still negotiating the number — no digit has to appear
    ("let's go ahead with option 2 but we need to talk price", None),
    ("we accept, lets discuss the cost first", None),
    ("please proceed, we should go over the budget again", None),
    ("lets proceed to the next step of discussing the menu", None),
    # conditional acceptance is a counter-offer
    ("we accept only if you include dessert", None),
    ("we accept if you include dessert", None),
    ("we accept but only if you can do it for $2000", None),
    ("we accept provided you include delivery", None),
    ("we'd like to proceed as long as the price stays the same", None),
    ("please proceed subject to the manager approving", None),
    # bare `if` with any subject, and the wait-for-something-else forms
    ("we accept if the total stays under $2000", None),
    ("we accept pending my husband's approval", None),
    ("we accept once we hear back from the venue", None),
    ("we accept as soon as you confirm the date", None),
    ("please go ahead once you have the deposit", None),
    ("we accept while we wait for approval", None),
    # the marker attaches to the headcount NOUN and the number arrives later
    ("we want to proceed with fewer guests, say 90", None),
    ("confirmed, proceed but reduce it to 150 people", None),
    ("go ahead, but we may need to change the date later", None),
    # a QUESTION cannot be a commitment, but it can withdraw one
    ("we accept. can you cancel the earlier order?", None),
    ("we accept. can we talk price first?", None),
    ("We accept! But first, can you do it cheaper?", None),
    ("we accept the quote. can you knock off a discount?", None),
    # haggling with no digit and no negotiating verb
    ("we accept, any flexibility on the price?", None),
    ("please go ahead, can you come down a bit", None),
    # the decision is still outstanding
    ("we accept, hold on - let me check with my wife first", None),
    ("we accept, need to confirm with my partner", None),
    ("we accept, lets revisit the per-head price", None),
    ("we need to renegotiate the cost, but go ahead for now", None),
    # adding covers locks a quote priced for the OLD headcount
    ("yes we accept, and please add 20 more guests", None),
    ("we accept the quote, plus 15 extra plates", None),
    ("we accept, add another 2 mains", None),
    # haggling idioms that name no number at all
    ("we accept, can you do a better number", None),
    ("we accept, sharpen the pencil a bit", None),
    ("we accept, any wiggle room on the price", None),
    ("we accept, can you knock something off", None),
    ("we accept, can you shave 200 off", None),
    ("we accept, lets move on the price", None),
    ("we accept the quote. can you knock off a discount?", None),
    ("we accept, any flexibility on the price?", None),
    # a deferral WITH a decision object is still a deferral
    ("we accept, need to confirm with my partner", None),
    ("we accept, let me check with my wife first", None),
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
# A demoted acceptance is SILENT — the detector writes no audit row for a
# message it declines to classify, so a guard that over-reaches loses real
# bookings invisibly. These cells bound that: every one is a plain yes that
# happens to sit next to a connective, a restated term, or an aside.
#
# THIS TABLE IS THE FULL GENUINE SET AND EVERY ROW IS PINNED. A row that is
# only ever checked by hand is a row a later narrowing can break in silence:
# the cancellation-policy cell below was regressed for a whole review round
# because it lived in a hand-run A/B list instead of here, and the suite
# passed over it. Anything claimed as "still books" belongs in this table.
@pytest.mark.parametrize("text", [
    "we accept the quote",
    "please go ahead",
    "confirmed, proceed with the quote",
    "we would like to proceed",
    "confirmed, please go ahead",
    "we accept, the date of June 5 works for us",
    "please proceed with the booking",
    "we accept and will pay the deposit today",
    "we'd like to proceed",
    # "but"/"only" are the connectives of a RESTATEMENT, not change markers
    "we accept the quote for 250 guests only",
    "we accept the quote, but can you confirm the June 5 date",
    "please go ahead, but note there will be 200 guests as agreed",
    "we accept, but please send the invoice to accounts",
    # a material term restated or agreed-to is not a change to it
    "we accept the quote for 250 guests",
    "we'd like to proceed with the quote for 120 people",
    "we accept the quote, delivery for 300 guests as quoted",
    "please go ahead with the $4500 quote",
    "we accept, see you on June 20",
    "confirmed, proceed - the June 5 date works",
    "let's book it for the 17th",
    # "tasting menu" is a menu, not an appointment
    "please proceed with the tasting menu",
    # scene-setting, not a condition the acceptance hangs on
    "we accept, the venue is ours until 10pm",
    "we accept, waiting on your invoice",
    # asking ABOUT a cancellation term is the opposite of cancelling, and it is
    # a normal thing to ask in the same breath as saying yes
    "we accept the quote. what is your cancellation policy?",
    "we accept the quote. what is your cancellation window?",
    # gratitude for a price already granted is not a request to lower it
    "thanks for the discount you gave us, we accept",
    "we accept, and thanks for the discounted rate",
    # confirming LOGISTICS is not deferring the decision
    "we accept, let me confirm the delivery address",
    "we accept, let me confirm the spelling of the business name",
    "we accept. please confirm",
    # adding a person or an unquantified item is not a headcount change
    "add my husband to the invite list, we accept",
    "we accept, add extra naan",
])
def test_genuine_acceptance_survives_the_guards(text):
    assert _outcome(text) == "accepted"


def test_an_acceptance_with_no_sentence_break_before_its_question_is_lost():
    """PRE-EXISTING limitation, pinned so it is not rediscovered as a new bug.

    `_assertion_text` drops per SENTENCE, and this message is a single
    interrogative sentence — the dash is not a sentence break — so nothing is
    left to match. The same words with a full stop instead of the dash book
    normally, and that pair is the whole difference.

    Verified None on the pre-v2 module too, so no guard added across v2-v4
    caused it. Widening the sentence splitter would change which text every
    other rule reads, which is a bigger change than this row is worth.
    """
    assert _outcome("we accept - what are the cancellation terms?") is None
    assert _outcome("we accept. what are the cancellation terms?") == "accepted"


# ── `if` does two opposite jobs ─────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "we accept, let us know if you need a deposit",
    "we accept the quote, if you need anything let us know",
    "please go ahead, if that works for you",
    "we accept - call us if there's anything else",
    # a polite request and a resignation idiom, neither of them a condition
    "we accept, if you could email the invoice that'd be great",
    "we accept, if that's what it takes",
])
def test_a_courtesy_if_clause_still_books(text):
    """An offer of help is not a condition on the acceptance.

    The counterpart cells live in the disqualifier table above: "we accept if
    the total stays under $2000" and "we accept if you include dessert" are
    counter-offers and must never book. The discriminator is the clause the
    `if` sits in, so both directions have to be pinned or the next narrowing
    of one silently breaks the other.
    """
    assert _outcome(text) == "accepted"


# ── shape contract ──────────────────────────────────────────────────────────
def test_result_shape():
    result = detect_quote_acceptance("we accept")
    assert set(result) == {"outcome", "matched_phrase"}
    assert result["outcome"] in ("accepted", "declined", "ambiguous")


def test_matched_phrase_is_bounded():
    result = detect_quote_acceptance("we accept " + "x" * 500)
    assert len(result["matched_phrase"]) <= 120


def test_a_long_adversarial_inbound_stays_linear():
    """The detector runs synchronously inside the router's pre-dispatch hook,
    so a message it is slow on stalls the whole router.

    This input is the worst case for the change-marker adjacency scan: many
    markers, many material terms, and NO pair ever close enough to short-
    circuit. The first version compared every marker against every term and
    re-counted the tokens between each pair, which took 31s on this 18KB
    string — and WhatsApp permits 65K characters. The bound is deliberately
    loose (the linear version runs in ~5ms); it is here to fail loudly if a
    future edit reintroduces the quadratic, not to police milliseconds.
    """
    unit = "instead alpha bravo charlie delta echo 250 guests foxtrot golf hotel india juliet "
    text = "we accept the quote " + (unit * 230)[:18 * 1024]

    start = time.perf_counter()
    detect_quote_acceptance(text)
    assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize("unit,label", [
    ("instead alpha bravo charlie delta echo 250 guests foxtrot golf hotel india juliet ",
     "change-marker adjacency, no pair ever close enough to short-circuit"),
    ("if the total alpha bravo charlie delta echo foxtrot golf hotel india ",
     "ONE giant clause with no boundaries, packed with bare ifs"),
    ("if you could email alpha bravo charlie delta echo foxtrot golf hotel ",
     "ONE giant clause packed with COURTESY ifs — every if resolves, none exits"),
])
def test_the_quadratic_shapes_all_stay_linear(unit, label):
    """Three different ways to make this detector quadratic, all pinned.

    The clause-scan one is the subtle member: `_has_binding_condition` looks up
    the clause each `if` sits in, and a message that is ONE clause containing
    hundreds of `if`s re-scanned that whole clause once per `if` until the
    result was cached per clause.

    Asserted as a per-KB RATE rather than a wall-clock total, because a rate is
    what distinguishes "slow machine" from "wrong complexity" — a quadratic
    reintroduction makes the rate climb with size even on a fast host.
    """
    rates = []
    for kb in (4.6, 18, 73):
        text = "we accept the quote " + (unit * (int(kb * 1024 // len(unit)) + 1))[:int(kb * 1024)]
        start = time.perf_counter()
        detect_quote_acceptance(text)
        rates.append((time.perf_counter() - start) * 1000 / kb)

    assert max(rates) < 20.0, f"{label}: {rates} ms/KB — measured ~1 ms/KB"
    assert max(rates) < 8 * min(rates), (
        f"{label}: cost per KB grew {max(rates) / max(min(rates), 1e-9):.1f}x "
        f"with size ({rates} ms/KB) — that is superlinear, not a slow host")


def test_detector_is_deterministic():
    """Same input, same answer — this is what makes the audit row replayable."""
    for _ in range(3):
        assert detect_quote_acceptance("we accept")["outcome"] == "accepted"
