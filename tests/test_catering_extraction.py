"""M1 — deterministic catering field extraction (src/platform/catering_extraction.py).

PURE unit tests: the module is stdlib-only regex + dict shaping, so these run
everywhere including the Windows dev box (no fcntl, no subprocess, no bridge).

Two things are being pinned:
  1. every widened field extracts what it should AND, just as importantly, stays
     silent when it should — a wrong headcount or date is worse than a null,
     because the owner can see a blank but cannot see a plausible wrong number;
  2. the fields the deployed F7 extractor already produced are unchanged, and the
     NARROW month-name date parser the ruled fresh-vs-stale discriminator depends
     on has not been widened underneath it.
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src" / "platform"))

import catering_extraction as ce  # noqa: E402


def _future(month: int, day: int) -> str:
    """The next occurrence of month/day, matching the module's roll-forward rule."""
    today = datetime.now(timezone.utc).date()
    candidate = date(today.year, month, day)
    if candidate < today:
        candidate = date(today.year + 1, month, day)
    return candidate.isoformat()


# ── Dates ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "we need catering on June 15",
    "the event is june 15th",
    "party Jun 15 for the family",
])
def test_month_name_dates(text):
    assert ce.parse_event_date(text) == _future(6, 15)


def test_month_name_date_with_explicit_year_is_not_rolled_forward():
    assert ce.parse_event_date("wedding on March 3, 2026") == "2026-03-03"


@pytest.mark.parametrize("text,expected_md", [
    ("event on 6/15", (6, 15)),
    ("we are looking at 06/15", (6, 15)),
    ("date is 6-15", (6, 15)),
])
def test_numeric_dates(text, expected_md):
    assert ce.parse_event_date(text) == _future(*expected_md)


@pytest.mark.parametrize("text,expected", [
    ("6/15/2026", "2026-06-15"),
    ("06/15/26", "2026-06-15"),
])
def test_numeric_dates_with_year(text, expected):
    assert ce.parse_event_date(text) == expected


@pytest.mark.parametrize("text", [
    "we want 1/2 tray of biryani",     # fraction + unit, not January 2
    "3/4 pan of curry please",
    "13/45 is not a date",             # month + day both out of range
    "no date mentioned at all here",
])
def test_numeric_date_negatives(text):
    assert ce.parse_numeric_event_date(text) is None


def test_month_day_parser_stays_narrow_for_the_ruled_discriminator():
    """_inbound_event_identity feeds the ruled fresh-vs-stale discriminator through
    parse_month_day_event_date. If THAT parser learned numeric dates, which
    follow-ups count as contradicting a different event would change silently."""
    assert ce.parse_month_day_event_date("event on 6/15") is None
    assert ce.parse_month_day_event_date("event on June 15") == _future(6, 15)


# ── Headcount ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("catering for 120 people", 120),
    ("we need 45 plates", 45),
    ("serving 200", 200),
    ("90 vegetarian meals", 90),
])
def test_headcount(text, expected):
    assert ce.parse_headcount(text) == expected


@pytest.mark.parametrize("text", [
    "just 2 of us",                     # below the 5-guest acceptance band
    "we might have 50000 guests",       # above the 10000 ceiling
    "how much do you charge",
])
def test_headcount_negatives(text):
    assert ce.parse_headcount(text) is None


def test_headcount_prefers_the_classifier_signal_when_one_is_supplied():
    fields = ce.extract_catering_fields("catering for 120 people", ["headcount:80"])
    assert fields["headcount"] == 80


def test_headcount_patterns_match_the_classifier_source_verbatim():
    """INVARIANT: catering_extraction.HEADCOUNT_PATTERNS is a verbatim copy of
    cf-router actions._HEADCOUNT_PATTERNS. The classifier keeps its own copy (26
    pinned cases run inside the Hermes plugin loader), so this compares SOURCE
    TEXT — it fails loudly the day either side is edited alone. Static read only:
    actions.py is not importable on Windows."""
    def _list_literal(text: str, name: str) -> str:
        start = text.index(f"{name} = [")
        depth, i = 0, text.index("[", start)
        for j in range(i, len(text)):
            if text[j] == "[":
                depth += 1
            elif text[j] == "]":
                depth -= 1
                if depth == 0:
                    return re.sub(r"\s+", " ", text[i:j + 1])
        raise AssertionError(f"unterminated list literal for {name}")

    actions_src = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8")
    module_src = (REPO / "src" / "platform" / "catering_extraction.py").read_text(encoding="utf-8")
    assert _list_literal(module_src, "HEADCOUNT_PATTERNS") == \
        _list_literal(actions_src, "_HEADCOUNT_PATTERNS"), (
        "headcount grammar drifted between catering_extraction and cf-router "
        "actions.classify_catering — the extractor and the classifier would then "
        "disagree about the same message"
    )


# ── Event type ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("my daughter's wedding reception", "wedding"),
    ("a 50th birthday party", "birthday"),
    ("our office holiday lunch", "corporate"),
    ("a small puja at home", "religious"),
    ("graduation party for my son", "other"),
])
def test_event_type(text, expected):
    assert ce.parse_event_type(text) == expected


def test_event_type_negative():
    assert ce.parse_event_type("do you cater? what are your prices") is None


# ── Service style ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("we want a buffet setup", "buffet"),
    ("plated dinner service", "plated"),
    ("individual boxed lunches", "boxed"),
    ("family style please", "family_style"),
    ("can you do live counters", "other"),
])
def test_service_style(text, expected):
    assert ce.parse_service_style(text) == expected


def test_service_style_negative():
    assert ce.parse_service_style("catering for 80 people in June") is None


# ── Delivery vs pickup ───────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("can you deliver to the hall", "delivery"),
    ("we will pick up the trays", "pickup"),
    ("drop off at 5pm", "delivery"),
    ("takeaway is fine", "pickup"),
])
def test_delivery_or_pickup(text, expected):
    assert ce.parse_delivery_or_pickup(text) == expected


@pytest.mark.parametrize("text", [
    "can you deliver or should we pick up",   # BOTH — genuinely ambiguous
    "catering for 60 guests",                 # neither
])
def test_delivery_or_pickup_stays_silent_when_unclear(text):
    assert ce.parse_delivery_or_pickup(text) is None


# ── Event time ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("starts at 6pm", "18:00"),
    ("6:30 pm start", "18:30"),
    ("11 am setup", "11:00"),
    ("12am is midnight", "00:00"),
    ("12pm is noon", "12:00"),
    ("the event runs 18:00 onwards", "18:00"),
])
def test_event_time_clock(text, expected):
    assert ce.parse_event_time(text)[0] == expected


def test_event_time_from_prose_is_flagged_approximate():
    hhmm, source = ce.parse_event_time("sometime in the evening")
    assert hhmm == "18:00"
    assert source == "evening"
    fields = ce.extract_catering_fields("catering for 60 guests in the evening")
    assert fields["event_time"] == "18:00"
    assert "approximate" in fields["notes"] and "evening" in fields["notes"]


def test_event_time_negative():
    assert ce.parse_event_time("catering for 60 guests on June 15")[0] is None


# ── Budget ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected_total", [
    ("budget is $3000", 3000),
    ("we want to stay under $2,500", 2500),
    ("up to 4000 dollars", 4000),
])
def test_budget_total(text, expected_total):
    assert ce.parse_budget(text)[0] == expected_total


def test_per_person_budget_is_a_note_not_a_total():
    """Storing $20/head in budget_hint_usd (a TOTAL field) would misread as a $20
    event; multiplying by headcount would compose a price nobody approved."""
    total, per_person = ce.parse_budget("we are thinking $20 per person")
    assert per_person == 20
    assert total is None
    fields = ce.extract_catering_fields("catering for 60 guests at $20 per person")
    assert "budget_hint_usd" not in fields
    assert "$20 per person" in fields["notes"]


def test_budget_negative_bare_amount_without_a_cue_word():
    """A bare "$3000" in a pasted price list is not a budget."""
    assert ce.parse_budget("Mango custard tray - $3000")[0] is None


# ── Veg / non-veg ────────────────────────────────────────────────────────────
def test_veg_nonveg_counts_and_tags():
    fields = ce.extract_catering_fields("90 veg 30 non-veg for the reception")
    assert fields["veg_guest_count"] == 90
    assert fields["nonveg_guest_count"] == 30
    assert fields["dietary_restrictions"] == ["veg", "non-veg"]
    assert "30 non-veg" in fields["notes"] and "90 veg" in fields["notes"]


def test_veg_majority_prose_records_a_note_and_no_invented_counts():
    fields = ce.extract_catering_fields(
        "catering for 80 guests, mostly vegetarian but some chicken",
    )
    assert "veg_guest_count" not in fields and "nonveg_guest_count" not in fields
    assert "veg-majority" in fields["notes"]
    assert fields["dietary_restrictions"] == ["veg"]


def test_dietary_tags_unchanged_from_the_deployed_extractor():
    assert ce.extract_catering_fields("all vegetarian please")["dietary_restrictions"] == ["veg"]
    # DEPLOYED QUIRK, pinned deliberately: the veg word-boundary pattern also fires
    # inside "non-veg" (the hyphen is a word boundary), so a non-veg-only message
    # tags BOTH. Preserved verbatim — this test exists so the behavior is a recorded
    # decision rather than an accident, and so a future tightening is a conscious
    # change with a failing test to discuss.
    assert ce.extract_catering_fields("non-veg options?")["dietary_restrictions"] == ["veg", "non-veg"]


def test_sample_menu_request_note_unchanged():
    fields = ce.extract_catering_fields("can you send two sample menus for 80 people")
    assert "requested 2 sample menu combinations" in fields["notes"]


# ── Venue + whole-message behaviour ──────────────────────────────────────────
def test_venue_is_never_guessed_from_free_text():
    """Venue is filled ONLY from a direct answer to the venue question — there is no
    deployed venue grammar and a fragile one would write customer prose into a field
    the owner reads as fact."""
    fields = ce.extract_catering_fields(
        "catering for 80 at the Grand Ballroom on June 15",
    )
    assert "venue" not in fields


def test_full_inquiry_extracts_the_whole_qualification_block():
    fields = ce.extract_catering_fields(
        "Need catering for 120 people on June 15 for my daughter's wedding "
        "reception, buffet style, 90 veg 30 non-veg, please deliver, 6pm start, "
        "budget under $3000",
    )
    assert fields["headcount"] == 120
    assert fields["event_date"] == _future(6, 15)
    assert fields["event_type"] == "wedding"
    assert fields["service_style"] == "buffet"
    assert fields["delivery_or_pickup"] == "delivery"
    assert fields["event_time"] == "18:00"
    assert fields["budget_hint_usd"] == 3000
    assert fields["veg_guest_count"] == 90
    assert fields["nonveg_guest_count"] == 30


def test_nothing_extractable_returns_none():
    assert ce.extract_catering_fields("do you cater?") is None
    assert ce.extract_catering_fields("") is None


def test_extracted_dict_validates_against_the_lead_schema():
    """TRAP guard: CateringLeadExtractedFields is extra="ignore", so an extractor key
    with no schema field is dropped SILENTLY. Round-tripping the extractor's full
    output through the model and re-reading every key proves none is being lost."""
    from schemas import CateringLeadExtractedFields  # noqa: PLC0415 — path set above

    fields = ce.extract_catering_fields(
        "Need catering for 120 people on June 15 for a wedding reception, buffet, "
        "90 veg 30 non-veg, deliver at 6pm, budget under $3000",
    )
    model = CateringLeadExtractedFields.model_validate(fields)
    for key, value in fields.items():
        assert getattr(model, key) == value, f"{key} was dropped by extra='ignore'"
