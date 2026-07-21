"""Unit tests for catering_recompose — the deterministic mix-and-match planner
(PR-D turn-3 assist). Pure functions; no I/O, Windows-runnable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from catering_recompose import parse_section_refs, recompose_plan  # noqa: E402


# Menu: name -> category. A SENT set option 1 has appetizer+main; option 2 has main+dessert.
MENU = {
    "Idly (3 PCS)": "appetizer", "Chicken Tikka Dosa": "appetizer",
    "Paneer Butter Masala": "main", "Chicken Biryani": "main",
    "Goat Curry": "main", "2 Scoop": "dessert", "Masala Dosa": "appetizer",
}

SENT = {
    "options": [
        {"option_id": "1", "item_names": ["Idly (3 PCS)", "Chicken Tikka Dosa",
                                          "Paneer Butter Masala", "Chicken Biryani"]},
        # Option 2 deliberately has NO appetizer (main + dessert only) so the
        # missing-section clarify path is exercised.
        {"option_id": "2", "item_names": ["Goat Curry", "2 Scoop"]},
    ]
}


# ── parse_section_refs grammar ───────────────────────────────────────────────
def test_parse_basic_two_refs():
    assert parse_section_refs("Can we do option 1 starters with the option 2 mains?") == [
        (1, "appetizer"), (2, "main")]


def test_parse_apostrophe_s():
    assert parse_section_refs("option 1's starters and option 2's mains") == [
        (1, "appetizer"), (2, "main")]


def test_parse_no_option_reference():
    assert parse_section_refs("the biryani one please") == []


def test_parse_synonyms():
    assert parse_section_refs("option 2 entrees with option 1 appetizers") == [
        (2, "main"), (1, "appetizer")]


def test_parse_single_ref():
    assert parse_section_refs("can we mix in option 3's desserts?") == [(3, "dessert")]


# ── recompose_plan — clean merge ─────────────────────────────────────────────
def test_merge_starters_from_1_mains_from_2():
    plan = recompose_plan("option 1 starters with the option 2 mains", SENT, MENU)
    assert plan["kind"] == "merge"
    # appetizers come from option 1; mains from option 2 (Goat Curry), CATEGORY_ORDER.
    assert plan["sections"] == ["appetizer", "main"]
    assert plan["item_names"] == ["Idly (3 PCS)", "Chicken Tikka Dosa", "Goat Curry"]
    assert plan["combination"] == "option 1 starters + option 2 mains"


def test_merge_orders_by_category_not_request_order():
    plan = recompose_plan("option 2 mains with option 1 starters", SENT, MENU)
    assert plan["kind"] == "merge"
    assert plan["sections"] == ["appetizer", "main"]  # appetizer before main regardless of phrasing


# ── recompose_plan — clarify fallbacks ───────────────────────────────────────
def test_clarify_unknown_option():
    plan = recompose_plan("option 3 desserts with option 1 starters", SENT, MENU)
    assert plan["kind"] == "clarify" and plan["reason"] == "unknown_option"
    assert "options 1 and 2" in plan["message"]


def test_clarify_missing_section():
    # option 2 has no appetizer in SENT; asking for option 2 starters -> clarify.
    plan = recompose_plan("option 2 starters with option 1 mains", SENT, MENU)
    assert plan["kind"] == "clarify" and plan["reason"] == "missing_section"
    assert "starters" in plan["message"]


def test_clarify_underspecified_single_section():
    plan = recompose_plan("can we mix in option 1's desserts?", SENT, MENU)
    assert plan["kind"] == "clarify" and plan["reason"] == "underspecified"


def test_clarify_no_parse():
    plan = recompose_plan("the biryani one", SENT, MENU)
    assert plan["kind"] == "clarify" and plan["reason"] == "underspecified"


def test_clarify_ambiguous_same_section_two_options():
    plan = recompose_plan("option 1 mains and option 2 mains", SENT, MENU)
    assert plan["kind"] == "clarify" and plan["reason"] == "ambiguous_section"


def test_clarify_no_sent_set():
    plan = recompose_plan("option 1 starters with option 2 mains", None, MENU)
    assert plan["kind"] == "clarify" and plan["reason"] == "no_sent_set"


def test_merge_never_contains_off_menu():
    plan = recompose_plan("option 1 starters with the option 2 mains", SENT, MENU)
    assert all(n in MENU for n in plan["item_names"])


# ── message hygiene: clarify lines carry no price / internal ids ─────────────
def test_clarify_messages_price_and_id_free():
    import re
    for req in ("option 3 desserts with option 1 starters",
                "option 2 starters with option 1 mains",
                "the biryani one"):
        msg = recompose_plan(req, SENT, MENU)["message"]
        assert not re.search(r"\$|\bL0\d{3}\b|CPS-|#[A-Z0-9]{5}\b", msg), msg
