"""Tests for select_poster_archetype() — CD v2 Composition Phase 1, Task 1.

A pure, never-raises deterministic router from request_intent (+ unused-this-phase
offer_priority) to a poster archetype. Phase-1 mapping only:

    menu / new / source_edit  -> message_first
    combo_offer               -> offer_first
    event                     -> event_first
    anything else / "" / None -> message_first (safe default)
"""
import pytest

from agents.flyer.flyer_poster_archetype import (
    EVENT_FIRST,
    MESSAGE_FIRST,
    OFFER_FIRST,
    select_poster_archetype,
)


def test_constants_have_expected_values():
    assert MESSAGE_FIRST == "message_first"
    assert OFFER_FIRST == "offer_first"
    assert EVENT_FIRST == "event_first"


@pytest.mark.parametrize(
    "request_intent,expected",
    [
        ("menu", MESSAGE_FIRST),
        ("new", MESSAGE_FIRST),
        ("source_edit", MESSAGE_FIRST),
        ("combo_offer", OFFER_FIRST),
        ("event", EVENT_FIRST),
    ],
)
def test_known_intents_map_to_expected_archetype(request_intent, expected):
    assert select_poster_archetype(request_intent) == expected


@pytest.mark.parametrize(
    "request_intent",
    ["", "unknown", "MENU", "Menu", "garbage", "combo", "events", None],
)
def test_unknown_empty_none_default_to_message_first(request_intent):
    assert select_poster_archetype(request_intent) == MESSAGE_FIRST


def test_offer_priority_does_not_change_selection_phase_1():
    """offer_priority is accepted but NOT used for selection in Phase 1."""
    for priority in ("high", "medium", "low", "", None, "garbage"):
        assert select_poster_archetype("menu", priority) == MESSAGE_FIRST
        assert select_poster_archetype("combo_offer", priority) == OFFER_FIRST
        assert select_poster_archetype("event", priority) == EVENT_FIRST


def test_offer_priority_defaults_to_medium():
    """The signature defaults offer_priority to "medium"; calling without it works."""
    assert select_poster_archetype("combo_offer") == OFFER_FIRST


def test_pure_never_raises_on_weird_inputs():
    """Never raises, even on non-string / unexpected inputs."""
    assert select_poster_archetype(None) == MESSAGE_FIRST
    assert select_poster_archetype(123) == MESSAGE_FIRST  # type: ignore[arg-type]
    assert select_poster_archetype(["menu"]) == MESSAGE_FIRST  # type: ignore[arg-type]
    assert select_poster_archetype({"intent": "menu"}) == MESSAGE_FIRST  # type: ignore[arg-type]
    assert select_poster_archetype(object()) == MESSAGE_FIRST  # type: ignore[arg-type]
