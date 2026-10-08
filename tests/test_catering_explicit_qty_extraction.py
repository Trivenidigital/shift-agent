"""Explicit per-item quantity extraction — `catering_extraction.extract_explicit_line_items`.

The menu names are COPIED from the production-menu fixture the deterministic
lifecycle E2E uses (tests/e2e/fixtures/catering-menu-e2e.json), never hand-typed:
the matching rules are only worth testing against the names customers actually
see ("Idly (3 PCS)", four "... Biryani" rows, fifteen "... Dosa" rows).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catering_extraction import ExplicitLineItems, extract_explicit_line_items

MENU_FIXTURE = Path(__file__).resolve().parent / "e2e" / "fixtures" / "catering-menu-e2e.json"
MENU_NAMES = [item["name"] for item in json.loads(MENU_FIXTURE.read_text(encoding="utf-8"))["items"]]


def _pairs(result: ExplicitLineItems) -> list[tuple[str, int]]:
    return [(row["name"], row["qty"]) for row in result.matched]


def test_fixture_carries_the_live_style_names_these_cases_rely_on():
    for name in ("Idly (3 PCS)", "Chicken Biryani", "Masala Dosa", "Plain Dosa", "Veg Biryani"):
        assert name in MENU_NAMES, name


def test_trays_of_and_bare_of_resolve_to_canonical_menu_names():
    result = extract_explicit_line_items("10 trays of Idly, 5 of Chicken Biryani", MENU_NAMES)
    assert _pairs(result) == [("Idly (3 PCS)", 10), ("Chicken Biryani", 5)]
    assert result.unmatched == []
    assert result.has_quantity_signal is True


def test_parenthetical_is_stripped_on_both_sides():
    result = extract_explicit_line_items("4 orders of idly (3 pcs)", MENU_NAMES)
    assert _pairs(result) == [("Idly (3 PCS)", 4)]


@pytest.mark.parametrize("text", ["Masala Dosa x 4", "Masala Dosa x4", "Masala Dosa: 4",
                                  "Masala Dosa - 4", "4 x Masala Dosa", "4× Masala Dosa"])
def test_name_then_quantity_and_multiplier_forms(text):
    assert _pairs(extract_explicit_line_items(text, MENU_NAMES)) == [("Masala Dosa", 4)]


def test_bare_dosa_is_ambiguous_and_suggests_exact_menu_names():
    result = extract_explicit_line_items("Dosa x 4", MENU_NAMES)
    assert result.matched == []
    assert result.unmatched == ["Dosa x 4"]
    assert result.has_quantity_signal is True
    suggestions = result.suggestions["Dosa x 4"]
    assert 1 <= len(suggestions) <= 3
    assert all(s in MENU_NAMES and "Dosa" in s for s in suggestions)


def test_ambiguous_biryani_is_unmatched_with_suggestions_while_the_rest_matches():
    result = extract_explicit_line_items("10 trays of Idly and 5 biryani", MENU_NAMES)
    assert _pairs(result) == [("Idly (3 PCS)", 10)]
    assert result.unmatched == ["5 biryani"]
    suggestions = result.suggestions["5 biryani"]
    assert len(suggestions) == 3
    assert all(s.endswith("Biryani") for s in suggestions)


@pytest.mark.parametrize("qty", [0, 201, 500])
def test_quantity_outside_bounds_is_unmatched_never_clamped(qty):
    result = extract_explicit_line_items(f"{qty} trays of Chicken Biryani", MENU_NAMES)
    assert result.matched == []
    assert result.unmatched == [f"{qty} trays of Chicken Biryani"]
    assert result.has_quantity_signal is True


@pytest.mark.parametrize("text", ["Option 2", "option 2 please", "I'll take option 2",
                                  "We are 50 people", "for 60 guests at 7 pm", "2"])
def test_no_quantity_signal_without_a_named_item(text):
    result = extract_explicit_line_items(text, MENU_NAMES)
    assert result.matched == [] and result.unmatched == []
    assert result.has_quantity_signal is False


def test_selection_verb_with_item_quantity_is_an_item_not_an_option():
    result = extract_explicit_line_items("I'll take 2 trays of Idly", MENU_NAMES)
    assert _pairs(result) == [("Idly (3 PCS)", 2)]


def test_numbers_inside_menu_names_survive():
    result = extract_explicit_line_items("3 Chicken 65 Dosa", MENU_NAMES)
    assert _pairs(result) == [("Chicken 65 Dosa", 3)]


def test_unknown_item_with_unit_word_is_reported_without_suggestions():
    result = extract_explicit_line_items("3 trays of lasagna, 2 trays of Upma", MENU_NAMES)
    assert _pairs(result) == [("Upma", 2)]
    assert result.unmatched == ["3 trays of lasagna"]
    assert result.suggestions["3 trays of lasagna"] == []


def test_repeated_item_quantities_are_summed():
    result = extract_explicit_line_items("5 Upma, 3 Upma", MENU_NAMES)
    assert _pairs(result) == [("Upma", 8)]


def test_empty_menu_or_text_is_inert():
    assert extract_explicit_line_items("", MENU_NAMES).has_quantity_signal is False
    assert extract_explicit_line_items("10 trays of Idly", []).matched == []
