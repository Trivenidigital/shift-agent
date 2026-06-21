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


# --- FIX 1 (Codex BLOCKER): sale/discount/offer WORDS + hyphen forms reject ----
#
# The narrative firewall delegated commercial rejection to a scanner that only
# catches numeric VALUES, so generic claim WORDS ("Discount", "Deal", "Promo",
# "Sale", "Clearance", "BOGO") and hyphenated time-pressure / superlative forms
# ("Limited-time", "Today-only", "Award-winning", "top-rated") survived into the
# prominently-rendered narrative. The operator's reject list forbids them.

# Sale/discount/offer-claim WORDS the operator explicitly forbids — each must
# default to the campaign_title (no numeric value present, so the prior numeric-
# only scanner missed them).
_FIX1_SALE_WORD_REJECTS = [
    "Weekend Discount Feast",
    "Discounted Dosa Platter",
    "Limited-time Deal",
    "Today-only Promo",
    "Big Sale This Weekend",
    "Clearance Specials",
    "BOGO Dosa",
    "Buy One Get One Free Idli",
    "Markdown Mania",
    "Combo Deal of the Day",
    "20% off the menu in words: percent off",
    "Cents off every plate",
    "Dollars off your order",
]

# Hyphenated time-pressure / superlative forms — the prior whitespace-only phrase
# sets missed these. Normalizing hyphens/underscores to spaces makes them match.
_FIX1_HYPHEN_REJECTS = [
    "Award-winning",
    "top-rated",
    "Limited-time only",
    "Today-only feast",
    "Act-now and save",
]


def test_scrub_narrative_sale_words_reject_to_title():
    """Every operator-forbidden sale/discount/offer WORD defaults to the
    campaign_title even though it carries NO numeric value (the numeric-only
    scanner used to let these through)."""
    allowed_values = ["$7.99", "masala dosa", "idli sambar"]
    for phrase in _FIX1_SALE_WORD_REJECTS:
        out = scrub_campaign_narrative(
            phrase, allowed_values=allowed_values, campaign_title=_TITLE
        )
        assert out == _TITLE, f"sale word not caught: {phrase!r} -> {out!r}"


def test_scrub_narrative_hyphenated_forms_reject_to_title():
    """Hyphenated time-pressure / superlative forms reject the same as the spaced
    forms (narrative is normalized before phrase matching)."""
    allowed_values = ["$7.99", "masala dosa"]
    for phrase in _FIX1_HYPHEN_REJECTS:
        out = scrub_campaign_narrative(
            phrase, allowed_values=allowed_values, campaign_title=_TITLE
        )
        assert out == _TITLE, f"hyphen form not caught: {phrase!r} -> {out!r}"


def test_scrub_narrative_fix1_allow_list_still_survives():
    """CRITICAL: the operator ALLOW list MUST still survive the new sale-word /
    normalization sets. "specials" is NOT a sale-word; one-price/weekend/family/
    festive/authentic marketing language is grounded-evocative, not fabrication."""
    allowed_values = ["$7.99", "masala dosa", "idli sambar", "gulab jamun"]
    allow = [
        "One-Price Specials",
        "Weekend Treats",
        "Family Favorites",
        "Festive Desserts",
        "Authentic Classic Flavors",
        "Weekend Feast",
        "Weekend Specials",  # the campaign_title itself — "specials" is not a sale word
        "South Indian Favorites at One Price",
        "Weekend Feast of Family Favorites",
        "One-Price Weekend Treats",
    ]
    for phrase in allow:
        out = scrub_campaign_narrative(
            phrase, allowed_values=allowed_values, campaign_title=_TITLE
        )
        assert out == phrase, f"ALLOW phrase wrongly rejected: {phrase!r} -> {out!r}"


def test_scrub_narrative_time_pressure_extended_set():
    """The extended time-pressure set is caught (ends soon / last chance / dont
    miss / for a limited / this week only / now or never), spaced + hyphenated."""
    allowed_values = ["$7.99"]
    for phrase in (
        "ends soon so order",
        "your last chance for dosa",
        "dont miss this",
        "don't miss this",
        "for a limited run",
        "this week only feast",
        "now or never on idli",
        "while-supplies-last platter",  # hyphenated form of "while supplies last"
    ):
        out = scrub_campaign_narrative(
            phrase, allowed_values=allowed_values, campaign_title=_TITLE
        )
        assert out == _TITLE, f"time-pressure not caught: {phrase!r} -> {out!r}"


# --- FIX (Codex BLOCKER residual): PLURAL / inflected sale WORDS reject ---------
#
# The singular sale-word set ("promo"/"promotion"/"sale"/"clearance"/"markdown"/
# "discount") missed the plural forms, so a title like "Weekend Sales Event",
# "Festival Promos", or "Holiday Promotions" survived into the prominently-
# rendered narrative. The operator's reject list forbids them in any number.
# Each sale word now matches an optional trailing ``s``.

# Plural / inflected sale WORDS — each must default to the campaign_title.
_FIX1_PLURAL_SALE_WORD_REJECTS = [
    "Weekend Sales Event",
    "Festival Promos",
    "Holiday Promotions",
    "Big Discounts",
    "Final Clearances",
    "Markdowns Galore",
    "Combo Deals of the Day",
]


def test_scrub_narrative_plural_sale_words_reject_to_title():
    """Plural / inflected sale WORDS reject the SAME as their singular form (the
    prior singular-only set let "sales"/"promos"/"promotions"/"discounts"/
    "clearances"/"markdowns" through)."""
    allowed_values = ["$7.99", "masala dosa", "idli sambar"]
    for phrase in _FIX1_PLURAL_SALE_WORD_REJECTS:
        out = scrub_campaign_narrative(
            phrase, allowed_values=allowed_values, campaign_title=_TITLE
        )
        assert out == _TITLE, f"plural sale word not caught: {phrase!r} -> {out!r}"


def test_scrub_narrative_trailing_s_allow_words_still_survive():
    """CRITICAL: ALLOW words that merely END in ``s`` (NOT sale words) MUST still
    survive the optional-plural suffix — esp. "specials" (\\bsales?\\b must NOT
    match inside it) and "desserts"/"treats"/"favorites"/"flavors"."""
    allowed_values = ["$7.99", "masala dosa", "idli sambar", "gulab jamun"]
    allow = [
        "One-Price Specials",
        "Festive Desserts",
        "Weekend Treats",
        "Family Favorites",
        "Authentic Classic Flavors",
        "Weekend Specials",  # the campaign_title — "specials" is not a sale word
    ]
    for phrase in allow:
        out = scrub_campaign_narrative(
            phrase, allowed_values=allowed_values, campaign_title=_TITLE
        )
        assert out == phrase, f"trailing-s ALLOW phrase wrongly rejected: {phrase!r} -> {out!r}"


# --- FIX C: scrub allows SCHEDULE-GROUNDED temporal wording -------------------
#
# Operator-approved firewall change: ``scrub_campaign_narrative`` rejected ALL
# scheduling/temporal references (via ``_scheduling_claim_hit``), so a GROUNDED
# "this weekend" (when the flyer's schedule IS the weekend) got scrubbed to the
# bland campaign_title. New boundary: reject scheduling/temporal claims ONLY when
# NOT grounded in the schedule fact; ALLOW them when grounded. Pure time-pressure /
# urgency ("today only", "limited time", "act now", "hurry", "while supplies last")
# stays ALWAYS-reject (it is urgency, not a schedule reference).


def test_scrub_narrative_grounded_weekend_kept_friday_through_sunday():
    """"this Weekend" + schedule "Available Friday through Sunday" → KEPT (the
    weekend = sat+sun is covered by the schedule day-set)."""
    out = scrub_campaign_narrative(
        "Indulge in our Festival Dessert Specials this Weekend",
        allowed_values=["$7.99"],
        campaign_title=_TITLE,
        schedule="Available Friday through Sunday",
    )
    assert out == "Indulge in our Festival Dessert Specials this Weekend"


def test_scrub_narrative_grounded_weekend_kept_saturday_and_sunday():
    """"This Weekend" + schedule "Saturday & Sunday, 4 PM-8 PM" → KEPT."""
    out = scrub_campaign_narrative(
        "Savor the Flavors of South India This Weekend",
        allowed_values=["$7.99"],
        campaign_title=_TITLE,
        schedule="Saturday & Sunday, 4 PM-8 PM",
    )
    assert out == "Savor the Flavors of South India This Weekend"


def test_scrub_narrative_ungrounded_day_scrubbed_to_title():
    """"this Monday" + schedule "Saturday & Sunday" → SCRUBBED → title (Monday is
    NOT in the schedule day-set)."""
    out = scrub_campaign_narrative(
        "Festive Specials this Monday",
        allowed_values=["$7.99"],
        campaign_title=_TITLE,
        schedule="Saturday & Sunday",
    )
    assert out == _TITLE


def test_scrub_narrative_time_pressure_always_rejects_regardless_of_schedule():
    """"Order today only" + ANY schedule → SCRUBBED → title (time-pressure is
    urgency, ALWAYS reject, independent of schedule)."""
    for schedule in ("", "Saturday & Sunday", "Available Friday through Sunday"):
        out = scrub_campaign_narrative(
            "Order today only",
            allowed_values=["$7.99"],
            campaign_title=_TITLE,
            schedule=schedule,
        )
        assert out == _TITLE, f"time-pressure leaked with schedule={schedule!r}: {out!r}"


def test_scrub_narrative_limited_time_always_rejects():
    """"limited time offer" → SCRUBBED → title even with a matching schedule."""
    out = scrub_campaign_narrative(
        "limited time offer",
        allowed_values=["$7.99"],
        campaign_title=_TITLE,
        schedule="Saturday & Sunday",
    )
    assert out == _TITLE


def test_scrub_narrative_schedule_param_defaults_empty_existing_behavior():
    """With the default schedule="" the existing behavior is unchanged: an ungrounded
    scheduling claim ("tables available this weekend") still rejects (nothing in the
    schedule to ground it)."""
    out = scrub_campaign_narrative(
        "tables available this weekend",
        allowed_values=["$7.99"],
        campaign_title=_TITLE,
    )
    assert out == _TITLE


def test_scrub_narrative_fix_c_allow_list_still_survives_with_schedule():
    """The existing ALLOW list still survives unchanged when a schedule is supplied —
    a grounded weekend schedule must not start rejecting clean evocative phrases."""
    allowed_values = ["$7.99", "masala dosa", "idli sambar", "gulab jamun"]
    for phrase in (
        "Delicious Weekend Combos for Every Taste",
        "One-Price Specials",
        "Weekend Feast of Family Favorites",
        "Authentic Classic Flavors",
    ):
        out = scrub_campaign_narrative(
            phrase,
            allowed_values=allowed_values,
            campaign_title=_TITLE,
            schedule="Saturday & Sunday",
        )
        assert out == phrase, f"ALLOW phrase wrongly rejected with schedule: {phrase!r} -> {out!r}"


def test_scrub_narrative_fix_c_reject_classes_still_reject_with_schedule():
    """SAFETY: a grounded schedule must NOT let through a fabricated price/%/discount,
    a sale word, a superlative, or an ungrounded scheduling claim. Every existing
    reject class still rejects even with a matching weekend schedule."""
    allowed_values = ["$7.99", "masala dosa"]
    for phrase in (
        "$5 off",                      # ungrounded price
        "50% off the menu",            # ungrounded percentage
        "best biryani in town",        # superlative
        "#1 South Indian spot",        # ranking
        "award-winning dosa",          # award
        "Weekend Discount Feast",      # sale word
        "BOGO Dosa",                   # offer word
        "Festive Specials this Monday",  # ungrounded day (not in sat/sun schedule)
    ):
        out = scrub_campaign_narrative(
            phrase,
            allowed_values=allowed_values,
            campaign_title=_TITLE,
            schedule="Saturday & Sunday",
        )
        assert out == _TITLE, f"reject class leaked with schedule: {phrase!r} -> {out!r}"


def test_scrub_narrative_grounded_explicit_day_kept():
    """An explicit day named in the schedule is grounded and kept; a day NOT in the
    schedule rejects."""
    out_ok = scrub_campaign_narrative(
        "Join us this Saturday for the feast",
        allowed_values=["$7.99"],
        campaign_title=_TITLE,
        schedule="Saturday & Sunday",
    )
    assert out_ok == "Join us this Saturday for the feast"
    out_bad = scrub_campaign_narrative(
        "Join us this Friday for the feast",
        allowed_values=["$7.99"],
        campaign_title=_TITLE,
        schedule="Saturday & Sunday",
    )
    assert out_bad == _TITLE


# --- FIX C: resolver passes the schedule locked fact into the narrative scrub --


def test_resolver_schedule_grounded_weekend_narrative_survives():
    """Resolver integration: a brief whose narrative is schedule-grounded "this
    weekend" → resolved campaign_narrative == the brain's narrative (NOT the title),
    because the resolver passes the "schedule" locked fact into the scrub."""
    facts = _two_item_facts() + [
        _fact("campaign_title", "Weekend Combo Special"),
        _fact("schedule", "Saturday & Sunday, 4 PM-8 PM"),
    ]
    brief = _brief(campaign_narrative="Savor the Flavors of South India This Weekend")
    out = resolve_creative_direction(brief, facts)
    assert out.campaign_narrative == "Savor the Flavors of South India This Weekend"


def test_resolver_ungrounded_day_narrative_defaults_to_title():
    """Resolver integration: a narrative referencing a day NOT in the schedule fact
    defaults to the campaign_title."""
    facts = _two_item_facts() + [
        _fact("campaign_title", "Weekend Combo Special"),
        _fact("schedule", "Saturday & Sunday"),
    ]
    brief = _brief(campaign_narrative="Festive Specials this Monday")
    out = resolve_creative_direction(brief, facts)
    assert out.campaign_narrative == "Weekend Combo Special"


# --- FIX D: hero falls back to the primary OFFER subject for combo flyers ------
#
# For combo flyers production extracts each combo as ONE COARSE ``offer:N`` fact (no
# fine ``item:*:name`` slots). The resolver only accepted ``item:*:name`` for the
# hero, so ``hero_name`` resolved to "" — the hero was unnamed. FIX D: if hero_ref
# resolves to an ``offer:*`` fact, OR there are no item:*:name facts but offers
# exist, the hero falls back to the PRIMARY offer's subject NAME, derived
# deterministically from the offer's OWN text (the name before the price/":"/
# "includes"). NEVER invented — always a substring of a locked fact value.


def _combo_offer_facts() -> list[FlyerLockedFact]:
    """A combo flyer: only coarse offer:N facts, NO item:*:name."""
    return [
        _fact("offer:0", "Veg Combo - $12.99: Includes 2 curries, dessert"),
        _fact("offer:1", "Non-Veg Combo - $15.99: Includes biryani, kebab"),
    ]


def test_hero_offer_ref_resolves_to_offer_subject_name():
    """hero_ref pointing at offer:0 → hero_name is the offer's subject name
    ("Veg Combo"), derived from the offer text — and a substring of offer:0's value."""
    facts = _combo_offer_facts()
    brief = _brief(hero_ref=FactRef(fact_id="offer:0"))
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Veg Combo"
    # Never invented — the hero name is a substring of the offer's OWN locked value.
    assert out.hero_name and out.hero_name in facts[0].value


def test_hero_no_ref_with_offers_falls_back_to_first_offer_subject():
    """No hero_ref, but offers present and NO item:*:name → hero_name is the FIRST
    (lowest-index) offer's subject name."""
    facts = _combo_offer_facts()
    brief = _brief()  # no hero_ref
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Veg Combo"
    assert out.hero_name in facts[0].value


def test_hero_item_flyer_unchanged_when_item_names_exist():
    """An item flyer (has item:*:name) is UNCHANGED: hero = item name as before, even
    if an offer:* fact also exists."""
    facts = _two_item_facts() + [_fact("offer:0", "Veg Combo - $12.99")]
    brief = _brief(hero_ref=FactRef(fact_id="item:1:name"))
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Idli Sambar"  # item name, not the offer


def test_hero_item_flyer_default_first_item_unchanged_with_offer_present():
    """No hero_ref, item:*:name facts present (plus an offer) → first ITEM name wins
    (item-level facts take precedence over the offer fallback)."""
    facts = _two_item_facts() + [_fact("offer:0", "Veg Combo - $12.99")]
    brief = _brief()
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Masala Dosa"  # first item name, unchanged


def test_hero_no_items_no_offers_is_empty():
    """Neither items nor offers → hero_name "" (unchanged behavior)."""
    facts = [_fact("business_name", "Lakshmi's Kitchen")]
    brief = _brief()
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == ""


def test_hero_offer_subject_derivation_handles_no_separator():
    """An offer with no price/":"/"includes" separator → the whole (cleaned) value is
    the subject name (still a substring of the offer value)."""
    facts = [_fact("offer:0", "Family Feast Platter")]
    brief = _brief(hero_ref=FactRef(fact_id="offer:0"))
    out = resolve_creative_direction(brief, facts)
    assert out.hero_name == "Family Feast Platter"
    assert out.hero_name in facts[0].value


def test_hero_offer_fallback_invariant_substring_of_locked_fact():
    """INVARIANT: across offer-ref / no-ref-with-offers / item / empty cases the hero
    name is ALWAYS "" or a substring of SOME locked fact value (never invented)."""
    cases = [
        (_combo_offer_facts(), _brief(hero_ref=FactRef(fact_id="offer:0"))),
        (_combo_offer_facts(), _brief(hero_ref=FactRef(fact_id="offer:1"))),
        (_combo_offer_facts(), _brief()),
        (_combo_offer_facts(), _brief(hero_ref=FactRef(fact_id="offer:99"))),  # bad ref → first offer
        (_two_item_facts(), _brief(hero_ref=FactRef(fact_id="item:0:name"))),
        ([_fact("business_name", "X Cafe")], _brief()),
    ]
    for facts, brief in cases:
        out = resolve_creative_direction(brief, facts)
        if out.hero_name:
            assert any(out.hero_name in f.value for f in facts), (
                f"hero_name {out.hero_name!r} not a substring of any locked fact value"
            )
