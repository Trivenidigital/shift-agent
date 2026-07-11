"""Hard-fact-claim classifier (truth-guard core) — `is_hard_fact_claim`.

Originally the slice-3 creative-planner firewall; the planner + the
`CreativeFirewall` materialization gate were removed 2026-07-04 (#548).
`is_hard_fact_claim` survives, reused by `flyer_brief_validator`: it must let
legitimate item names through and FLAG any text that smuggles a hard-fact-class
claim (§6b: the #1 risk is a claim disguised as an item name). Fail-closed.
"""
from __future__ import annotations

import time

import pytest

from agents.flyer.creative_firewall import is_hard_fact_claim


def test_legitimate_item_names_pass():
    names = ["Idli", "Masala Dosa", "Veg Manchurian", "Plain Dosa", "Medu Vada",
             "Uttapam", "Pongal", "Filter Coffee", "7 Up", "Item 65",
             "Gluten Free Dosa", "Sugar Free Sweet",  # compound "free" must PASS (not lone)
             "Chicken 65", "Mysore 65", "Idli 8"]  # bare trailing number is NOT a claim (Codex r6)
    for name in names:  # all pass; incidental digits OK
        assert is_hard_fact_claim(name) is False, name


@pytest.mark.parametrize("claim", [
    "Free Delivery",          # service claim
    "Open Daily 8-11",        # schedule
    "20% Off",                # discount
    "$8.99 Special",          # price
    "Lowest Prices in Town",  # superlative price claim
    "Best Price Guaranteed",  # guarantee
    "Sat & Sun Brunch",       # day-of-week
    "Call 555-123-4567",      # phone
    "Visit www.shop.com",     # url
    "Weekend Combo Deal",     # weekend + deal
    "From $5",                # from-price
    "Certified Organic",      # legal/cert claim
    "Cash Only",              # payment claim (Codex r1)
    "Card Accepted",          # payment claim (Codex r1)
    "UPI Accepted",           # payment claim (Codex r1)
    "Order at shop.com",      # bare domain (Codex r1)
    "+1 732 555 1212",        # international phone (Codex r1)
    "₹8.99 Thali",            # non-$ currency (Codex r1)
    "We Accept Venmo",        # payment claim
    "Thali 8.99",             # plain decimal price-shape (Codex r2)
    "Combo 12/99",            # slash price-shape (Codex r2)
    "Brunch 8 to 11",         # "to" time range (Codex r2)
    "Best Prices Here",       # plural superlative (Codex r2)
    "Free",                   # lone "Free" claim (Codex r2)
    "Call 555-1212",          # 7-digit local phone (Codex r3)
    "(555) 1212",             # paren phone (Codex r3)
    "Tel 5551212",            # bare 7-digit run (Codex r3)
    "Special 2026-06-01",     # ISO numeric date (Codex r4)
    "Sale 06/01/2026",        # slash numeric date (Codex r4)
    "Brunch 6/1",             # short slash date (Codex r4)
    "Order Online",           # service-assertion claim (Codex r5)
    "Pickup Available",       # service-assertion claim (Codex r5)
    "Dine-In Only",           # service-assertion claim (Codex r5)
    "Takeout Combo",          # service-assertion claim (Codex r5)
    "Catering Available",     # service-assertion claim (Codex r5)
    "Reservations Welcome",   # service-assertion claim (Codex r5)
    "Price 8",                # context-token price claim (Codex r6)
    "Only 10",                # context-token price claim (Codex r6)
    "Just $5",                # context-token price claim (Codex r6)
    "Starting at 8",          # context-token price claim (Codex r6)
    "2 for 1",                # BOGO/offer (Codex r6)
    "8 Dollars",              # currency-word price (Codex r6)
    "",                       # empty
    "   ",                    # whitespace
])
def test_hard_fact_class_claims_are_rejected(claim):
    assert is_hard_fact_claim(claim) is True


# ── "open" precision (false positive 2026-06-05) ────────────────────────────
# "open" has both an operational sense ("now open", "open daily", "grand opening")
# and a benign compositional sense ("open central area left clear for text"). The
# detector is ANCHORED-PHRASE with a default-BENIGN posture: "open" is operational
# ONLY via a SPECIFIC anchored open-phrase (incl. reopen variants + meal-service /
# hours / launch tails) or a co-occurring unambiguous clock-time signal; everything
# else (incl. bare "open", "open layout for Memorial Day", "open for seating",
# "24 inch margin") is benign. Only the "open" handling changed — every other
# operational/claim token (incl. "hours", "closes", "closing") is untouched.


@pytest.mark.parametrize("operational", [
    # anchored open-phrase (A) — launch / hours / meal-service word adjacent.
    "now open",
    "open daily",
    "open until 10",
    "open 9am-9pm",
    "grand opening",
    "open weekends",
    "open weekdays",
    "open all week",
    "open seven days a week",
    "open for lunch",
    "open for dinner",
    "open 24hrs",
    "open 24/7",
    "open until midnight",
    "opens at noon",
    "open at 9",
    "open from noon",
    "opening soon",
    "opening day",
    # reopen variants (round-4 MAJOR) — token matches re/re- + open.
    "grand reopening",
    "newly reopened",
    "reopened for business",
    "reopens monday",
    # bare reopen* (round-5 final) — operational on its own (no benign sense).
    "reopen",
    "reopens",
    "reopened",
    "reopening",
    "re-open",
    "re open",
    # round-5 day-tail recall — optional preposition + full/plural weekday forms.
    "open on weekends",
    "open on weekdays",
    "opens on Saturday",
    "open Saturdays",
    "open on monday",
    "open during the weekend",
    # co-occurrence: a benign "open" must not mask an operational one.
    "an open central area, open until 10",
    "store open with clean seating, open 9am-9pm",
    # also still caught from earlier rounds + via the existing "hours" token.
    "open today",
    "open late",
    "open for business",
    "newly opened location",
    "open monday",
    "we are now open daily 9am-9pm",
    "opening hours",
])
def test_open_operational_claims_still_rejected(operational):
    # genuine business-hours / launch / meal-service claims must STILL be caught.
    assert is_hard_fact_claim(operational) is True


@pytest.mark.parametrize("compositional", [
    # layout / negative-space uses of "open" are NOT operational claims.
    "an open central area left clear for text",
    "open space",
    "leave open",
    "open layout with a wide open background",
    # MAJOR over-block regression guards: "Memorial Day" must NOT trip an "open"
    # claim (live combo brief); bare "soft"/"24"/"to N"/incidental numbers must not.
    "open layout for Memorial Day",
    "soft background",
    "24 inch margin",
    "to 3 inch margin",
    "3 inch margin",
    # round-4 BENIGN guards: open + a NON-operational tail (a layout word, not a
    # meal / clock / launch word) stays benign — the anchored tails are specific.
    "open area at the center",
    "open at the top edge",
    "open for seating",
    "open space for plating",
    "leave 2 inches open until the margin",
    "a clear 24 inch wide open background",
    "opening of the composition",
    # round-5 day-recall guard: a NON-ADJACENT day after "open" stays benign — the
    # day tail requires open [on|during]? <day> adjacency ("open layout for the
    # Saturday market" is open+layout, the day is not anchored to the open token).
    "open layout for the Saturday market",
    # round-5 final \b guard: bare reopen* flags, but "re" INSIDE another word
    # (store/more/are/here) must NOT trigger the reopen branch — these are bare
    # non-re "open" with no operational signal ⇒ benign.
    "store open",
    "more open space",
    "are open",
    "the gallery is more open near the top",
    # earlier-round compositional set (still benign).
    "open central area",
    "central area left open",
    "open space for the overlay",
    "kept open in the middle",
    "open composition",
    "wide open background",
    "leave the center open",
    "an open central area with a clear 3 inch margin",
])
def test_open_compositional_uses_pass(compositional):
    assert is_hard_fact_claim(compositional) is False


def test_open_is_operational_classifier_directly():
    from agents.flyer.creative_firewall import _open_is_operational
    # the exact live false-positive string classifies as compositional (False).
    assert _open_is_operational(
        "A festive Memorial Day cookout background with an open central area "
        "left clear for text. No words anywhere."
    ) is False
    # an anchored operational phrase anywhere flags the text…
    assert _open_is_operational("open central area, now open daily") is True
    assert _open_is_operational("grand opening") is True
    assert _open_is_operational("grand reopening") is True       # reopen variant
    # …but a bare "open" with no operational anchor is BENIGN (default benign).
    assert _open_is_operational("Open") is False
    # the Memorial-Day over-block is gone; benign open-tails stay benign.
    assert _open_is_operational("open layout for Memorial Day") is False
    assert _open_is_operational("open for seating") is False


@pytest.mark.parametrize("co_occurring", [
    # a compositional "open" must NOT mask a co-occurring operational "open" — the
    # anchored-phrase / time-signal scan is whole-text, no window.
    "an open central area, open until 10",
    "open layout, opened for business",
    "open layout, store open until 10pm",
    "store open with clean bright seating, open 9am-9pm",
])
def test_open_co_occurrence_operational_is_not_masked(co_occurring):
    assert is_hard_fact_claim(co_occurring) is True


def test_open_token_matches_reopen():
    # round-4 MAJOR: the open TOKEN must match reopen variants (with/without hyphen).
    from agents.flyer.creative_firewall import _OPEN_TOKEN_RE
    for t in ("reopen", "reopening", "reopened", "reopens", "re-open", "re-opening"):
        assert _OPEN_TOKEN_RE.search(t), t


@pytest.mark.parametrize("reopen_text", [
    "reopen", "reopens", "reopened", "reopening", "re-open", "re open",
    "we reopen this weekend",
])
def test_bare_reopen_is_always_operational(reopen_text):
    # round-5 final MAJOR: bare reopen* has NO benign sense ⇒ always flagged, no
    # anchor / time signal required (unlike bare "open").
    assert is_hard_fact_claim(reopen_text) is True
    from agents.flyer.creative_firewall import _open_is_operational
    assert _open_is_operational(reopen_text) is True


@pytest.mark.parametrize("benign_text", [
    # bare non-re "open" with no operational signal stays BENIGN…
    "open", "open central area", "open space", "leave open",
    # …and the \b before "re" keeps "re" INSIDE another word from triggering the
    # reopen branch: store/more/are/here + open are bare non-re "open" ⇒ benign.
    "store open", "more open space", "are open", "the gallery is more open here",
])
def test_bare_open_and_embedded_re_stay_benign(benign_text):
    assert is_hard_fact_claim(benign_text) is False
    from agents.flyer.creative_firewall import _open_is_operational
    assert _open_is_operational(benign_text) is False


@pytest.mark.parametrize("op_text", [
    "open on weekends",
    "opens on Saturday",
    "open during the weekend",
])
def test_open_day_tail_adjacent_flags(op_text):
    # round-5: an ADJACENT day (open [on|during]? <day>) flags.
    assert is_hard_fact_claim(op_text) is True


@pytest.mark.parametrize("benign_text", [
    # round-5: a NON-ADJACENT day after "open" stays benign — the day tail requires
    # open [on|during]? <day> adjacency. NB: we avoid "weekend"/"daily"/"today" here
    # because those are independent claim tokens in the existing _CLAIM_PATTERNS
    # date class (out of scope); "Saturday market" is the reviewer's exact case and
    # "saturday" is NOT a bare token there (the sat(?:day|s)? pattern needs a word
    # boundary after, which "satURday" lacks), so the only gate is the open day-tail.
    "open layout for the Saturday market",
    "an open central area beside the Saturday market stall, left clear for text",
])
def test_open_day_tail_non_adjacent_is_benign(benign_text):
    assert is_hard_fact_claim(benign_text) is False


def test_open_anchored_re_is_redos_safe():
    """Round-5 MUST-FIX: the prefix-marker separator must be LINEAR — a pathological
    input ("grand" + thousands of spaces + a non-open word) must classify quickly,
    not backtrack quadratically. The old `\\s*[- ]?\\s*` separator took ~0.5s on this
    5000-space input; the linear `\\s*(?:-\\s*)?` form is sub-millisecond."""
    patho = "grand " + " " * 5000 + "x"
    start = time.perf_counter()
    result = is_hard_fact_claim(patho)
    elapsed = time.perf_counter() - start
    assert result is False  # "grand <spaces> x" is not an open claim
    assert elapsed < 0.5, f"ReDoS: classification took {elapsed:.3f}s"

    # also a long-benign string that DOES contain a real open token after the marker.
    patho2 = "grand" + " " * 8000 + "mosaic, open central area left clear for text"
    start = time.perf_counter()
    result2 = is_hard_fact_claim(patho2)
    elapsed2 = time.perf_counter() - start
    assert result2 is False
    assert elapsed2 < 0.5, f"ReDoS: classification took {elapsed2:.3f}s"


def test_open_compositional_item_does_not_regress_legit_names():
    # the open logic must flag a smuggled "Open Daily" claim while leaving a
    # compositional "open central area" and legitimate dishes clean.
    assert is_hard_fact_claim("Open Daily") is True
    assert is_hard_fact_claim("open central area") is False
    assert is_hard_fact_claim("Masala Dosa") is False
    assert is_hard_fact_claim("Idli") is False
