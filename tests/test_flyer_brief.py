"""Tests for the Creative-Director output contract (`flyer_brief.py`).

Covers the Creative Director v2 (CD v2, Slice A) additive fields layered onto
the dormant slice-1 schema: ``VisualDirection.mood``, the new ``MarketingHook``
model, and the ``FlyerBrief`` creative fields (``hero_ref`` / ``supporting_refs``
/ ``marketing_hook`` / ``offer_priority``). Every new field is optional/defaulted
so existing ``FlyerBrief`` construction stays byte-identical (backward compatible).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.flyer.flyer_brief import (
    FactRef,
    FlyerBrief,
    MarketingHook,
    VisualDirection,
)


# --- VisualDirection.mood ---------------------------------------------------


def test_visual_direction_mood_round_trips():
    vd = VisualDirection(mood="Warm Restaurant Promo")
    assert vd.mood == "Warm Restaurant Promo"
    assert vd.model_dump()["mood"] == "Warm Restaurant Promo"


def test_visual_direction_mood_defaults_empty():
    vd = VisualDirection()
    assert vd.mood == ""


# --- MarketingHook ----------------------------------------------------------


def test_marketing_hook_validates():
    hook = MarketingHook(
        text_ref=FactRef(fact_id="pricing_structure"), prominence="high"
    )
    assert hook.text_ref.fact_id == "pricing_structure"
    assert hook.text_ref.provenance == "locked"
    assert hook.prominence == "high"


def test_marketing_hook_prominence_defaults_high():
    hook = MarketingHook(text_ref=FactRef(fact_id="pricing_structure"))
    assert hook.prominence == "high"


def test_marketing_hook_bad_prominence_raises():
    with pytest.raises(ValidationError):
        MarketingHook(
            text_ref=FactRef(fact_id="pricing_structure"), prominence="huge"
        )


def test_marketing_hook_extra_forbidden():
    with pytest.raises(ValidationError):
        MarketingHook(
            text_ref=FactRef(fact_id="pricing_structure"), unexpected="x"
        )


# --- FlyerBrief CD v2 fields ------------------------------------------------


def test_flyer_brief_accepts_new_fields():
    brief = FlyerBrief(
        request_intent="menu",
        visual_direction=VisualDirection(mood="Festive"),
        hero_ref=FactRef(fact_id="hero_dish"),
        supporting_refs=[FactRef(fact_id="side_1"), FactRef(fact_id="side_2")],
        marketing_hook=MarketingHook(
            text_ref=FactRef(fact_id="pricing_structure"), prominence="medium"
        ),
        offer_priority="high",
    )
    assert brief.hero_ref.fact_id == "hero_dish"
    assert [r.fact_id for r in brief.supporting_refs] == ["side_1", "side_2"]
    assert brief.marketing_hook.prominence == "medium"
    assert brief.offer_priority == "high"


def test_flyer_brief_offer_priority_defaults_medium():
    brief = FlyerBrief(request_intent="menu", visual_direction=VisualDirection())
    assert brief.offer_priority == "medium"


def test_flyer_brief_bad_offer_priority_raises():
    with pytest.raises(ValidationError):
        FlyerBrief(
            request_intent="menu",
            visual_direction=VisualDirection(),
            offer_priority="urgent",
        )


# --- Backward compatibility -------------------------------------------------


def test_flyer_brief_backward_compatible_defaults():
    """A FlyerBrief built WITHOUT any CD v2 field still validates and the new
    fields take their defaults."""
    brief = FlyerBrief(
        request_intent="menu", visual_direction=VisualDirection()
    )
    assert brief.hero_ref is None
    assert brief.supporting_refs == []
    assert brief.marketing_hook is None
    assert brief.offer_priority == "medium"


# --- FlyerBrief.campaign_narrative (CD v2, Slice B B0.1) ---------------------


def test_flyer_brief_accepts_campaign_narrative():
    brief = FlyerBrief(
        request_intent="menu",
        visual_direction=VisualDirection(),
        campaign_narrative="South Indian Favorites at One Price",
    )
    assert brief.campaign_narrative == "South Indian Favorites at One Price"
    assert (
        brief.model_dump()["campaign_narrative"]
        == "South Indian Favorites at One Price"
    )


def test_flyer_brief_campaign_narrative_defaults_empty():
    brief = FlyerBrief(request_intent="menu", visual_direction=VisualDirection())
    assert brief.campaign_narrative == ""


def test_flyer_brief_campaign_narrative_max_length_raises():
    with pytest.raises(ValidationError):
        FlyerBrief(
            request_intent="menu",
            visual_direction=VisualDirection(),
            campaign_narrative="x" * 201,
        )


def test_flyer_brief_campaign_narrative_extra_forbid_preserved():
    """Adding campaign_narrative must not loosen ``extra="forbid"``."""
    with pytest.raises(ValidationError):
        FlyerBrief(
            request_intent="menu",
            visual_direction=VisualDirection(),
            campaign_narrative="ok",
            unexpected_field="x",
        )
