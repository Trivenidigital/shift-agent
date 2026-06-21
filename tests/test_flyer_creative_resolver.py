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


# --- B0.3: scrub_campaign_narrative (scoped-scrub narrative firewall) --------
#
# campaign_narrative is a model-authored marketing message that RENDERS prominently
# (the only new fabrication surface in CD v2 Slice B). The scoped scrub (operator-
# approved Option B) keeps evocative-but-grounded marketing language and rejects
# fabrication; on reject it defaults to campaign_title.

from agents.flyer.flyer_creative_resolver import scrub_campaign_narrative


# The operator's ALLOW list — must SURVIVE unchanged when the facts are grounded.
_ALLOW_NARRATIVES = [
    "feast",
    "favorites",
    "celebration",
    "weekend treats",
    "family favorites",
    "classic flavors",
    "one-price specials",
    "authentic flavors",
    "festive desserts",
    "South Indian Favorites at One Price",
    "Weekend Feast of Family Favorites",
    "Festive Dessert Celebration",
    "Authentic Classic Flavors",
    "One-Price Weekend Treats",
]

# The operator's REJECT list — each must default to campaign_title.
_REJECT_NARRATIVES = [
    "$5 off",
    "50% off the menu",
    "best biryani in town",
    "#1 South Indian spot",
    "award-winning dosa",
    "voted top-rated",
    "order today only",
    "limited time offer",
    "free delivery on all orders",
    # an ungrounded scheduling claim
    "tables available this weekend",
]

_TITLE = "Weekend Combo Special"


def test_scrub_narrative_allow_phrases_survive_with_grounded_facts():
    """EVERY operator ALLOW phrase survives unchanged when the price ($7.99) and
    items are grounded locked-fact values. The scoped scrub keeps evocative-but-
    grounded marketing language."""
    # Grounded facts: price $7.99 + item names the marketing language can evoke.
    allowed_values = ["$7.99", "masala dosa", "idli sambar", "gulab jamun"]
    for phrase in _ALLOW_NARRATIVES:
        out = scrub_campaign_narrative(
            phrase, allowed_values=allowed_values, campaign_title=_TITLE
        )
        assert out == phrase, f"ALLOW phrase wrongly rejected: {phrase!r} -> {out!r}"


def test_scrub_narrative_reject_classes_default_to_campaign_title():
    """EVERY operator REJECT class (prices/discounts/percentages not in facts,
    time-pressure, delivery/operational/scheduling claims, awards/rankings/
    superlatives) defaults to campaign_title."""
    allowed_values = ["$7.99", "masala dosa", "idli sambar"]
    for phrase in _REJECT_NARRATIVES:
        out = scrub_campaign_narrative(
            phrase, allowed_values=allowed_values, campaign_title=_TITLE
        )
        assert out == _TITLE, f"REJECT phrase not caught: {phrase!r} -> {out!r}"


def test_scrub_narrative_grounded_price_survives():
    """A grounded price IS OK — "Everything at $7.99" survives when $7.99 is a
    locked fact value."""
    out = scrub_campaign_narrative(
        "Everything at $7.99",
        allowed_values=["$7.99"],
        campaign_title=_TITLE,
    )
    assert out == "Everything at $7.99"


def test_scrub_narrative_ungrounded_price_rejected():
    """An ungrounded price ($9.99 not in facts) defaults to campaign_title even
    though $7.99 IS grounded (token-anchored, not loose substring)."""
    out = scrub_campaign_narrative(
        "Everything at $9.99",
        allowed_values=["$7.99"],
        campaign_title=_TITLE,
    )
    assert out == _TITLE


def test_scrub_narrative_empty_returns_empty():
    """Empty / blank narrative returns "" (NOT the campaign_title)."""
    assert scrub_campaign_narrative("", allowed_values=[], campaign_title=_TITLE) == ""
    assert scrub_campaign_narrative("   ", allowed_values=[], campaign_title=_TITLE) == ""


def test_scrub_narrative_reject_with_no_title_yields_empty():
    """A fabricated narrative with NO campaign_title present defaults to "" (the
    fallback is the title; an absent title means nothing safe to show)."""
    out = scrub_campaign_narrative(
        "best biryani in town", allowed_values=[], campaign_title=""
    )
    assert out == ""


def test_scrub_narrative_clean_marketing_survives_no_facts():
    """A clean evocative narrative with NO commercial content survives even with
    NO grounded facts (it carries nothing to ground)."""
    out = scrub_campaign_narrative(
        "Festive Dessert Celebration",
        allowed_values=[],
        campaign_title=_TITLE,
    )
    assert out == "Festive Dessert Celebration"


def test_scrub_narrative_superlative_phrase_set_specific_tokens():
    """The explicit superlative / award / ranking tokens that the broad scanners
    miss are each caught (best / finest / greatest / voted / top-rated / #1 /
    number one / award-winning)."""
    allowed_values = ["$7.99"]
    for phrase in (
        "the best in the city",
        "the finest flavors anywhere",
        "the greatest dosa ever",
        "voted #1 by locals",
        "number one rated spot",
        "award-winning recipes",
        "top-rated by everyone",
    ):
        out = scrub_campaign_narrative(
            phrase, allowed_values=allowed_values, campaign_title=_TITLE
        )
        assert out == _TITLE, f"superlative not caught: {phrase!r} -> {out!r}"


def test_scrub_narrative_time_pressure_phrase_set():
    """The explicit time-pressure tokens are each caught (today only / limited
    time / act now / hurry / while supplies last)."""
    allowed_values = ["$7.99"]
    for phrase in (
        "order today only",
        "limited time offer",
        "act now and save",
        "hurry in this weekend",
        "while supplies last",
    ):
        out = scrub_campaign_narrative(
            phrase, allowed_values=allowed_values, campaign_title=_TITLE
        )
        assert out == _TITLE, f"time-pressure not caught: {phrase!r} -> {out!r}"


def test_scrub_narrative_grounded_delivery_phrase_survives():
    """A delivery PHRASE that IS grounded in a locked fact survives (the scrub is
    grounded-aware, not a blanket delivery ban)."""
    out = scrub_campaign_narrative(
        "Free delivery on all orders",
        allowed_values=["free delivery on all orders"],
        campaign_title=_TITLE,
    )
    assert out == "Free delivery on all orders"


def test_scrub_narrative_never_raises_on_bad_input():
    """Pure + never raises: a non-str narrative / non-str allowed_values entry /
    non-str title never raise; a bad narrative defaults safely."""
    # Non-str narrative coerces to "" (empty => "").
    assert scrub_campaign_narrative(None, allowed_values=[], campaign_title=_TITLE) == ""  # type: ignore[arg-type]
    assert scrub_campaign_narrative(123, allowed_values=[], campaign_title=_TITLE) == ""  # type: ignore[arg-type]
    # Non-str allowed_values entries are tolerated.
    out = scrub_campaign_narrative(
        "Festive Dessert Celebration",
        allowed_values=[None, 5, "$7.99"],  # type: ignore[list-item]
        campaign_title=_TITLE,
    )
    assert out in ("Festive Dessert Celebration", _TITLE)
    # Non-str title is tolerated.
    out2 = scrub_campaign_narrative(
        "best biryani in town", allowed_values=[], campaign_title=None  # type: ignore[arg-type]
    )
    assert out2 == ""


# --- B0.4: resolver carries the validated campaign_narrative -----------------


def test_resolver_carries_clean_narrative():
    """A brief with a clean (grounded/evocative) narrative → resolved carries it
    unchanged."""
    facts = _two_item_facts() + [_fact("campaign_title", "Weekend Combo Special")]
    brief = _brief(campaign_narrative="Weekend Feast of Family Favorites")
    out = resolve_creative_direction(brief, facts)
    assert out.campaign_narrative == "Weekend Feast of Family Favorites"


def test_resolver_fabricated_narrative_defaults_to_campaign_title():
    """A brief with a FABRICATED narrative → resolved carries the campaign_title
    locked-fact value (the safe default)."""
    facts = _two_item_facts() + [_fact("campaign_title", "Weekend Combo Special")]
    brief = _brief(campaign_narrative="#1 award-winning biryani, 50% off today only")
    out = resolve_creative_direction(brief, facts)
    assert out.campaign_narrative == "Weekend Combo Special"


def test_resolver_fabricated_narrative_no_title_yields_empty():
    """A FABRICATED narrative with NO campaign_title fact → resolved carries ""
    (nothing safe to show)."""
    facts = _two_item_facts()  # no campaign_title fact
    brief = _brief(campaign_narrative="best biryani in town, $5 off")
    out = resolve_creative_direction(brief, facts)
    assert out.campaign_narrative == ""


def test_resolver_grounded_price_narrative_survives():
    """A narrative whose only commercial value IS a grounded locked price survives
    through the resolver."""
    facts = [
        _fact("pricing_structure", "$7.99"),
        _fact("campaign_title", "One Price Weekend"),
    ]
    brief = _brief(campaign_narrative="Everything at $7.99")
    out = resolve_creative_direction(brief, facts)
    assert out.campaign_narrative == "Everything at $7.99"


def test_resolver_default_brief_has_empty_narrative():
    """A brief with no campaign_narrative → resolved carries "" (default)."""
    facts = _two_item_facts() + [_fact("campaign_title", "Weekend Combo Special")]
    brief = _brief()
    out = resolve_creative_direction(brief, facts)
    assert out.campaign_narrative == ""


def test_resolver_narrative_never_raises_empty_facts():
    """resolve_creative_direction stays pure / never raises with a narrative and
    empty locked_facts (no campaign_title => narrative with any claim => "")."""
    brief = _brief(campaign_narrative="best biryani in town")
    out = resolve_creative_direction(brief, [])
    assert isinstance(out, ResolvedCreativeDirection)
    assert out.campaign_narrative == ""
