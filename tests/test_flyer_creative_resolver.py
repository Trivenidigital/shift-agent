"""Tests for the deterministic creative resolver (`flyer_creative_resolver.py`).

CD v2 Slice A Task A2: the per-field firewall for the Creative-Director brief's
creative fields. ``resolve_creative_direction`` resolves the Hermes-proposed
brief's creative refs against the LOCKED FACTS with PER-FIELD DETERMINISTIC
FALLBACK. It is PURE deterministic, NEVER raises, and NEVER invents a value
(it only SELECTS values that already exist in ``locked_facts``).

This is SEPARATE from the strict anti-fabrication ``validate`` in
``flyer_brief_validator.py`` (which is NOT touched by this slice).
"""
from __future__ import annotations

from agents.flyer.flyer_brief import (
    FactRef,
    FlyerBrief,
    MarketingHook,
    VisualDirection,
)
from agents.flyer.flyer_creative_resolver import (
    ResolvedCreativeDirection,
    resolve_creative_direction,
)
from schemas import FlyerLockedFact


# --- helpers ----------------------------------------------------------------


def _fact(fact_id: str, value: str, *, label: str = "lbl") -> FlyerLockedFact:
    """Build a minimal grounded locked fact (source customer_text => groundable)."""
    return FlyerLockedFact(
        fact_id=fact_id,
        label=label,
        value=value,
        source="customer_text",
    )


def _two_item_facts() -> list[FlyerLockedFact]:
    return [
        _fact("item:0:name", "Masala Dosa"),
        _fact("item:0:price", "$8.99"),
        _fact("item:1:name", "Idli Sambar"),
        _fact("item:1:price", "$6.99"),
    ]


def _brief(**kwargs) -> FlyerBrief:
    """A minimal valid FlyerBrief with overridable creative fields."""
    base = dict(
        request_intent="menu",
        visual_direction=VisualDirection(),
    )
    base.update(kwargs)
    return FlyerBrief(**base)


def _all_fact_values(locked_facts) -> set[str]:
    return {f.value for f in locked_facts}


# --- hero_name --------------------------------------------------------------


def test_hero_ref_resolves_to_real_item_name():
    facts = _two_item_facts()
    brief = _brief(hero_ref=FactRef(fact_id="item:1:name"))
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Idli Sambar"


def test_hero_ref_unknown_fact_id_falls_back_to_first_item_name():
    facts = _two_item_facts()
    brief = _brief(hero_ref=FactRef(fact_id="item:9:name"))
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Masala Dosa"


def test_hero_ref_pointing_at_non_item_fact_falls_back_to_first_item_name():
    facts = _two_item_facts() + [_fact("contact_phone", "+1 732 555 0100")]
    brief = _brief(hero_ref=FactRef(fact_id="contact_phone"))
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Masala Dosa"


def test_hero_ref_raw_span_does_not_resolve_and_falls_back():
    facts = _two_item_facts()
    # A raw_span (customer_text provenance) is NOT a locked fact id => no selection.
    brief = _brief(hero_ref=FactRef(raw_span="Masala Dosa"))
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Masala Dosa"  # but only via first-item fallback


def test_hero_first_item_is_by_lowest_index():
    # Provide facts out of index order; "first" must be the lowest item index.
    facts = [
        _fact("item:2:name", "Third"),
        _fact("item:0:name", "First"),
        _fact("item:1:name", "Second"),
    ]
    brief = _brief()
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "First"


# --- supporting_names -------------------------------------------------------


def test_supporting_refs_drop_fabricated_keep_real_exclude_hero_dedup():
    facts = _two_item_facts() + [_fact("item:2:name", "Vada")]
    brief = _brief(
        hero_ref=FactRef(fact_id="item:0:name"),  # hero = Masala Dosa
        supporting_refs=[
            FactRef(fact_id="item:1:name"),  # real -> Idli Sambar
            FactRef(fact_id="item:2:name"),  # real -> Vada
            FactRef(fact_id="item:2:name"),  # duplicate -> de-duped
            FactRef(fact_id="item:0:name"),  # == hero -> excluded
            FactRef(fact_id="item:99:name"),  # fabricated -> dropped
            FactRef(raw_span="Some Invented Dish"),  # raw_span -> dropped
        ],
    )
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Masala Dosa"
    assert out.supporting_names == ["Idli Sambar", "Vada"]


def test_supporting_refs_empty_when_none_provided():
    facts = _two_item_facts()
    brief = _brief(hero_ref=FactRef(fact_id="item:0:name"))
    out = resolve_creative_direction(brief, facts)
    assert out.supporting_names == []


# --- hook_text / hook_prominence --------------------------------------------


def test_hook_text_ref_resolves_to_pricing_structure_with_model_prominence():
    facts = _two_item_facts() + [_fact("pricing_structure", "All items $5 today")]
    brief = _brief(
        marketing_hook=MarketingHook(
            text_ref=FactRef(fact_id="pricing_structure"),
            prominence="medium",
        )
    )
    out = resolve_creative_direction(brief, facts)
    assert out.hook_text == "All items $5 today"
    assert out.hook_prominence == "medium"


def test_hook_text_ref_resolves_to_offer():
    facts = _two_item_facts() + [_fact("offer:0", "Buy one get one free")]
    brief = _brief(
        marketing_hook=MarketingHook(
            text_ref=FactRef(fact_id="offer:0"),
            prominence="low",
        )
    )
    out = resolve_creative_direction(brief, facts)
    assert out.hook_text == "Buy one get one free"
    assert out.hook_prominence == "low"


def test_hook_text_ref_resolves_to_offer_price():
    facts = _two_item_facts() + [_fact("offer_price", "$19.99 combo")]
    brief = _brief(
        marketing_hook=MarketingHook(
            text_ref=FactRef(fact_id="offer_price"),
            prominence="high",
        )
    )
    out = resolve_creative_direction(brief, facts)
    assert out.hook_text == "$19.99 combo"
    assert out.hook_prominence == "high"


def test_hook_text_ref_fabricated_falls_back_to_pricing_structure_high():
    facts = _two_item_facts() + [_fact("pricing_structure", "Everything $9")]
    brief = _brief(
        marketing_hook=MarketingHook(
            text_ref=FactRef(fact_id="offer:404"),  # fabricated
            prominence="low",
        )
    )
    out = resolve_creative_direction(brief, facts)
    assert out.hook_text == "Everything $9"
    assert out.hook_prominence == "high"  # fallback prominence is high


def test_hook_text_ref_pointing_at_item_name_does_not_resolve_falls_back():
    # item:*:name is NOT an allowed hook source kind; only pricing/offer/offer_price.
    facts = _two_item_facts() + [_fact("pricing_structure", "Half price")]
    brief = _brief(
        marketing_hook=MarketingHook(
            text_ref=FactRef(fact_id="item:0:name"),
            prominence="medium",
        )
    )
    out = resolve_creative_direction(brief, facts)
    assert out.hook_text == "Half price"
    assert out.hook_prominence == "high"


def test_hook_no_pricing_and_bad_ref_gives_empty_low():
    facts = _two_item_facts()  # NO pricing_structure / offer / offer_price
    brief = _brief(
        marketing_hook=MarketingHook(
            text_ref=FactRef(fact_id="offer:0"),  # fabricated, nothing to fall to
            prominence="high",
        )
    )
    out = resolve_creative_direction(brief, facts)
    assert out.hook_text == ""
    assert out.hook_prominence == "low"


def test_hook_no_marketing_hook_and_pricing_exists_uses_pricing_high():
    facts = _two_item_facts() + [_fact("pricing_structure", "Flat $5")]
    brief = _brief(marketing_hook=None)
    out = resolve_creative_direction(brief, facts)
    assert out.hook_text == "Flat $5"
    assert out.hook_prominence == "high"


def test_hook_no_marketing_hook_no_pricing_gives_empty_low():
    facts = _two_item_facts()
    brief = _brief(marketing_hook=None)
    out = resolve_creative_direction(brief, facts)
    assert out.hook_text == ""
    assert out.hook_prominence == "low"


# --- offer_priority ---------------------------------------------------------


def test_offer_priority_valid_value_passthrough():
    facts = _two_item_facts() + [_fact("pricing_structure", "Flat $5")]
    brief = _brief(offer_priority="low")
    out = resolve_creative_direction(brief, facts)
    assert out.offer_priority == "low"


def test_offer_priority_default_medium_passes_through():
    # The schema default "medium" IS a valid value (∈ {high,medium,low}), so it
    # passes through unchanged — even when a pricing_structure fact exists. The
    # coercion branch fires ONLY for an out-of-set value (see the bogus test below).
    facts = _two_item_facts() + [_fact("pricing_structure", "Flat $5")]
    brief = _brief()  # offer_priority defaults to "medium"
    out = resolve_creative_direction(brief, facts)
    assert out.offer_priority == "medium"


def test_offer_priority_bogus_coerced_to_high_when_pricing_structure_exists():
    # An out-of-set value (only reachable by bypassing schema validation) is
    # coerced: "high" when a pricing_structure fact exists.
    facts = _two_item_facts() + [_fact("pricing_structure", "Flat $5")]
    brief = _brief()
    object.__setattr__(brief, "offer_priority", "bogus")
    out = resolve_creative_direction(brief, facts)
    assert out.offer_priority == "high"


def test_offer_priority_bogus_coerced_to_medium_when_no_pricing_structure():
    facts = _two_item_facts()  # no pricing_structure
    brief = _brief()
    object.__setattr__(brief, "offer_priority", "bogus")
    out = resolve_creative_direction(brief, facts)
    assert out.offer_priority == "medium"


def test_offer_priority_default_medium_when_no_pricing_structure():
    facts = _two_item_facts()  # no pricing_structure
    brief = _brief()  # offer_priority defaults to "medium"
    out = resolve_creative_direction(brief, facts)
    assert out.offer_priority == "medium"


# --- theme_family / mood (pure passthrough) ---------------------------------


def test_theme_family_and_mood_passthrough():
    facts = _two_item_facts()
    brief = _brief(
        visual_direction=VisualDirection(
            theme_family="Festive Diwali",
            mood="Warm Restaurant Promo",
        )
    )
    out = resolve_creative_direction(brief, facts)
    assert out.theme_family == "Festive Diwali"
    assert out.mood == "Warm Restaurant Promo"


# --- theme_family / mood: ungrounded-commercial firewall (FIX 1) ------------


def test_mood_with_ungrounded_price_is_stripped_to_empty():
    # A model could smuggle a fabricated commercial value via mood ("$5 off") — it
    # is NOT a locked fact value, so the resolver must default mood to "".
    facts = _two_item_facts()  # locked prices are $8.99 / $6.99, NOT $5
    brief = _brief(
        visual_direction=VisualDirection(mood="$5 off")
    )
    out = resolve_creative_direction(brief, facts)
    assert out.mood == ""


def test_theme_family_with_ungrounded_discount_is_stripped_to_empty():
    facts = _two_item_facts()
    brief = _brief(
        visual_direction=VisualDirection(theme_family="Buy one get $3 off")
    )
    out = resolve_creative_direction(brief, facts)
    assert out.theme_family == ""


def test_mood_with_no_commercial_value_is_kept_verbatim():
    facts = _two_item_facts()
    brief = _brief(
        visual_direction=VisualDirection(mood="Warm Restaurant Promo")
    )
    out = resolve_creative_direction(brief, facts)
    assert out.mood == "Warm Restaurant Promo"


def test_theme_mood_with_grounded_number_is_kept_not_overstripped():
    # The ONLY commercial value in the theme/mood IS a locked fact value ($8.99),
    # so it is grounded and must be kept (not over-stripped).
    facts = _two_item_facts()  # item:0:price == "$8.99"
    brief = _brief(
        visual_direction=VisualDirection(
            theme_family="$8.99 hero spotlight",
            mood="$8.99 promo mood",
        )
    )
    out = resolve_creative_direction(brief, facts)
    assert out.theme_family == "$8.99 hero spotlight"
    assert out.mood == "$8.99 promo mood"


def test_only_offending_field_is_stripped():
    # An ungrounded mood is stripped while a clean theme_family is kept (per-field).
    facts = _two_item_facts()
    brief = _brief(
        visual_direction=VisualDirection(
            theme_family="Festive Diwali",
            mood="$5 off",
        )
    )
    out = resolve_creative_direction(brief, facts)
    assert out.theme_family == "Festive Diwali"
    assert out.mood == ""


# --- shared helper: scrub_ungrounded_commercial_taste -----------------------
# The resolver's theme/mood firewall AND the advisory scene path both reuse this
# single shared helper (no parallel commercial regex). Direct unit test for it.


def test_scrub_ungrounded_commercial_taste_strips_ungrounded_keeps_clean():
    from agents.flyer.flyer_brief_validator import scrub_ungrounded_commercial_taste

    # empty allowed list (advisory path): any commercial value is ungrounded.
    theme, mood = scrub_ungrounded_commercial_taste("$5 off", "festive and warm", [])
    assert theme == ""                      # ungrounded commercial -> stripped
    assert mood == "festive and warm"      # clean -> kept verbatim


def test_scrub_ungrounded_commercial_taste_respects_grounded_values():
    from agents.flyer.flyer_brief_validator import scrub_ungrounded_commercial_taste

    # the commercial value IS in the allowed list -> grounded -> kept.
    theme, mood = scrub_ungrounded_commercial_taste(
        "$8.99 hero spotlight", "$3 off promo", ["$8.99"]
    )
    assert theme == "$8.99 hero spotlight"  # grounded -> kept
    assert mood == ""                       # $3 not allowed -> stripped


def test_scrub_ungrounded_commercial_taste_never_raises():
    from agents.flyer.flyer_brief_validator import scrub_ungrounded_commercial_taste

    # non-str inputs / odd allowed must not raise; result is safe.
    theme, mood = scrub_ungrounded_commercial_taste(None, None, None)  # type: ignore[arg-type]
    assert isinstance(theme, str) and isinstance(mood, str)


# --- defaults / empties / never-raises --------------------------------------


def test_empty_brief_with_facts_gives_sensible_defaults():
    facts = _two_item_facts()
    brief = _brief()  # all creative fields defaulted
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Masala Dosa"  # first item
    assert out.supporting_names == []
    assert out.hook_text == ""
    assert out.hook_prominence == "low"
    assert out.offer_priority == "medium"
    assert out.theme_family == ""
    assert out.mood == ""


def test_empty_locked_facts_all_empty_defaults_never_raises():
    brief = _brief(
        hero_ref=FactRef(fact_id="item:0:name"),
        supporting_refs=[FactRef(fact_id="item:1:name")],
        marketing_hook=MarketingHook(
            text_ref=FactRef(fact_id="pricing_structure"), prominence="high"
        ),
        offer_priority="high",
    )
    out = resolve_creative_direction(brief, [])  # NO facts -> never raises
    assert isinstance(out, ResolvedCreativeDirection)
    assert out.hero_name == ""
    assert out.supporting_names == []
    assert out.hook_text == ""
    assert out.hook_prominence == "low"
    # offer_priority "high" is a valid value => passthrough even with no facts.
    assert out.offer_priority == "high"


def test_result_is_frozen_dataclass():
    facts = _two_item_facts()
    out = resolve_creative_direction(_brief(), facts)
    import dataclasses

    assert dataclasses.is_dataclass(out)
    # frozen => assignment raises
    try:
        out.hero_name = "mutated"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover - frozen invariant
        raise AssertionError("ResolvedCreativeDirection must be frozen")


# --- the load-bearing invariant: NEVER invents a value ----------------------


def test_invariant_never_invents_only_selects_locked_values():
    """Every returned name/hook_text is either "" or a real locked fact value."""
    facts = _two_item_facts() + [
        _fact("item:2:name", "Vada"),
        _fact("pricing_structure", "All $5"),
        _fact("offer:0", "BOGO"),
        _fact("offer_price", "$19.99"),
        _fact("contact_phone", "+1 732 555 0100"),
    ]
    allowed = _all_fact_values(facts)

    # Exercise many ref combinations, including fabricated ones, to ensure no
    # branch ever fabricates a value not present in locked_facts.
    briefs = [
        _brief(),
        _brief(hero_ref=FactRef(fact_id="item:2:name")),
        _brief(hero_ref=FactRef(fact_id="item:999:name")),
        _brief(hero_ref=FactRef(raw_span="Totally Invented")),
        _brief(
            hero_ref=FactRef(fact_id="item:0:name"),
            supporting_refs=[
                FactRef(fact_id="item:1:name"),
                FactRef(fact_id="item:404:name"),
                FactRef(raw_span="Invented Side"),
            ],
        ),
        _brief(
            marketing_hook=MarketingHook(
                text_ref=FactRef(fact_id="offer:0"), prominence="medium"
            )
        ),
        _brief(
            marketing_hook=MarketingHook(
                text_ref=FactRef(fact_id="bogus:ref"), prominence="medium"
            )
        ),
    ]
    for brief in briefs:
        out = resolve_creative_direction(brief, facts)
        assert out.hero_name == "" or out.hero_name in allowed
        assert out.hook_text == "" or out.hook_text in allowed
        for name in out.supporting_names:
            assert name in allowed


def test_invariant_theme_mood_never_return_ungrounded_commercial():
    """theme_family / mood are visual-taste strings — they must NEVER return an
    ungrounded commercial value. A model trying to smuggle a fabricated commercial
    value through either field has that field defaulted to ""."""
    facts = _two_item_facts() + [
        _fact("pricing_structure", "All $5"),
        _fact("offer:0", "BOGO"),
    ]
    allowed = _all_fact_values(facts)
    ungrounded_briefs = [
        _brief(visual_direction=VisualDirection(mood="$9.99 off")),
        _brief(visual_direction=VisualDirection(theme_family="50% off blowout")),
        _brief(visual_direction=VisualDirection(mood="free dessert vibes")),
        _brief(
            visual_direction=VisualDirection(
                theme_family="$3 off cashback", mood="buy one get $7 free"
            )
        ),
    ]
    for brief in ungrounded_briefs:
        out = resolve_creative_direction(brief, facts)
        # Each field is either "" or carries NO ungrounded commercial value.
        for value in (out.theme_family, out.mood):
            assert value == "" or value in allowed
