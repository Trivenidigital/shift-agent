from __future__ import annotations

from agents.flyer.semantic_brief import (
    FlyerSemanticBrief,
    FlyerSemanticOffer,
    build_semantic_flyer_brief,
)
from schemas import FlyerRequestFields


def test_semantic_brief_parses_f0106_campaign_pricing_and_offer():
    raw = (
        "Create a flyer for Diwali sale, All items 5-10% off. "
        "Lucky draw eligible with purchase above $100."
    )

    brief = build_semantic_flyer_brief(
        FlyerRequestFields(event_or_business_name="Diwali sale, All items 5-10% off"),
        raw,
        profile_business_name="Lakshmi's Kitchen",
        allow_text_identity=False,
    )

    assert brief.campaign_title == "Diwali Sale"
    assert brief.pricing_structure == "All items 5-10% off"
    assert [offer.text for offer in brief.offers] == ["Lucky draw eligible with purchase above $100"]
    assert brief.account_business == ""


def test_semantic_brief_parses_f0107_sale_price_free_offer_and_dates():
    raw = (
        "Create a flyer for evening snacks sale, Wednesday and Thursday, any item $7.99. "
        "Free Masala Chai with any purchase above $12. This promotion runs until June 25."
    )

    brief = build_semantic_flyer_brief(FlyerRequestFields(), raw, profile_business_name="Lakshmi's Kitchen")

    assert brief.campaign_title == "Evening Snacks Sale"
    assert brief.pricing_structure == "Any item $7.99"
    assert [offer.text for offer in brief.offers] == ["Free Masala Chai with any purchase above $12"]
    assert brief.schedule == "Wednesday and Thursday"
    assert brief.promotion_end == "June 25"


def test_semantic_brief_rejects_provider_values_not_grounded_in_source():
    raw = "Create a flyer for evening snacks sale, any item $7.99."

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(
            campaign_title="Evening Snacks Sale",
            pricing_structure="Any item $9.99",
            offers=[FlyerSemanticOffer(text="Free Mango Lassi with $20 purchase")],
            promotion_end="July 4",
        )

    brief = build_semantic_flyer_brief(FlyerRequestFields(), raw, provider=provider)

    assert brief.campaign_title == "Evening Snacks Sale"
    assert brief.pricing_structure == "Any item $7.99"
    assert brief.offers == []
    assert brief.promotion_end == ""


def test_provider_grounding_does_not_validate_against_corrupted_extracted_fields():
    raw = (
        "Create a flyer for evening snacks sale, Wednesday and Thursday, any item $7.99. "
        "Free Masala Chai with any purchase above $12. This promotion runs until June 25."
    )
    fields = FlyerRequestFields(event_or_business_name="evening snacks sale, Wednesday and Thursday , any item $7")

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(campaign_title="evening snacks sale, Wednesday and Thursday , any item $7")

    brief = build_semantic_flyer_brief(fields, raw, provider=provider)

    assert brief.campaign_title == "Evening Snacks Sale"


def test_provider_grounding_requires_exact_money_amount_tokens():
    raw = "Create a flyer for evening snacks sale, any item $17.99."

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(pricing_structure="Any item $7.99")

    brief = build_semantic_flyer_brief(FlyerRequestFields(), raw, provider=provider)

    assert brief.pricing_structure == "Any item $17.99"


def test_semantic_brief_strips_weekday_from_campaign_title_and_preserves_hyphen_case():
    raw = (
        "Create a flyer for Indo-Chinese specials on Wednesday. "
        "Include 8 famous Indo-Chinese items. Any item priced at $9.99."
    )

    brief = build_semantic_flyer_brief(FlyerRequestFields(), raw)

    assert brief.campaign_title == "Indo-Chinese Specials"
    assert brief.pricing_structure == "Any item $9.99"


# ── extractor truth fix: offers must be bounded faithful spans ───────────────
# Live combo incident (2026-06-06): the meal-combo request produced two garbage
# REQUIRED offer facts — a request-tail echo (offer:0) and an invented-price +
# generated-prose offer (offer:1). These poisoned the fact set so the firewall
# stochastically rejected (invalid) or they overflowed render. An offer must be a
# bounded, faithful, grounded span — not a paragraph echoing the request.

_COMBO_RAW = (
    "Can we do meal combo flyer for veg and non veg with prices 49.99 "
    "for non veg combo include"
)
# The two offers the LIVE extractor produced (3/3 runs):
_COMBO_OFFER_ECHO = (
    "Non Veg Combo: $49.99 includes Veg And Non Veg Can we do meal combo flyer "
    "for veg and non veg with prices 49.99 for non veg combo include"
)
_COMBO_OFFER_INVENTED = (
    "Non Veg Combo: $99 includes professional local food menu flyer with "
    "appetizing photography, strong promotional"
)


def test_memorial_day_combo_drops_request_echo_and_invented_prose_offers():
    """The garbage offer:0 (request echo) + offer:1 ($99 invented + prose) yield NO
    surviving offers — no $99, no prose tail, nothing over 180 chars."""
    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(
            offers=[
                FlyerSemanticOffer(text=_COMBO_OFFER_ECHO),
                FlyerSemanticOffer(text=_COMBO_OFFER_INVENTED),
            ]
        )

    brief = build_semantic_flyer_brief(
        FlyerRequestFields(notes=_COMBO_RAW), _COMBO_RAW, provider=provider
    )

    assert brief.offers == []
    blob = " ".join(offer.text for offer in brief.offers)
    assert "$99" not in blob
    assert "photography" not in blob
    assert all(len(offer.text) <= 180 for offer in brief.offers)


def test_combo_offer_grounded_but_request_echo_is_dropped():
    """An offer whose EVERY token is in the source (so token-grounding passes) but is a
    request-tail paragraph must still be dropped — grounding alone is insufficient."""
    echo = (
        "Non Veg Combo 49.99 for non veg combo meal combo flyer for veg and non "
        "veg with prices"
    )

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(offers=[FlyerSemanticOffer(text=echo)])

    brief = build_semantic_flyer_brief(
        FlyerRequestFields(notes=_COMBO_RAW), _COMBO_RAW, provider=provider
    )

    assert brief.offers == []


def test_faithful_short_grounded_offer_still_survives():
    """The fix must NOT over-reject: a short, grounded, faithful offer is kept."""
    raw = (
        "Create a flyer for evening snacks sale, any item $7.99. "
        "Free Masala Chai with any purchase above $12."
    )

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(
            offers=[FlyerSemanticOffer(text="Free Masala Chai with any purchase above $12")]
        )

    brief = build_semantic_flyer_brief(FlyerRequestFields(notes=raw), raw, provider=provider)

    assert [offer.text for offer in brief.offers] == [
        "Free Masala Chai with any purchase above $12"
    ]


def test_faithful_offer_with_includes_is_not_over_dropped():
    """"includes" is legitimate offer language — a short, grounded "X includes Y"
    offer must survive. The instruction detector must NOT treat bare "includes" as a
    request echo (the live echo offers are caught by the length bound instead)."""
    raw = "Create a flyer for our thali combo. Thali includes rice dal and curry."

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(
            offers=[FlyerSemanticOffer(text="Thali includes rice dal and curry")]
        )

    brief = build_semantic_flyer_brief(FlyerRequestFields(notes=raw), raw, provider=provider)

    assert [offer.text for offer in brief.offers] == ["Thali includes rice dal and curry"]


def test_faithful_long_enumerated_offer_survives():
    """A legit, grounded, long-but-render-safe enumerated combo offer (~100 chars, well
    under render's 180) must NOT be dropped — length alone is not a faithfulness test
    (Codex MAJOR)."""
    offer = (
        "Family Combo includes chicken biryani, veg biryani, paneer tikka, "
        "gulab jamun, and drinks for $49.99"
    )
    raw = "Create a flyer for our family combo. " + offer

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(offers=[FlyerSemanticOffer(text=offer)])

    brief = build_semantic_flyer_brief(FlyerRequestFields(notes=raw), raw, provider=provider)
    assert [o.text for o in brief.offers] == [offer]


def test_faithful_redemption_offers_mentioning_flyer_survive():
    """Legit redemption offers that mention the physical flyer as a token must survive —
    bare "please" and "<flyer> for" are NOT instruction markers (Codex MAJOR)."""
    for offer in ("Bring this flyer for 10% off", "Please mention this flyer for free dessert"):
        raw = "Create a poster for our weekend sale. " + offer

        def provider(_fields, _raw_request, _offer=offer):
            return FlyerSemanticBrief(offers=[FlyerSemanticOffer(text=_offer)])

        brief = build_semantic_flyer_brief(FlyerRequestFields(notes=raw), raw, provider=provider)
        assert [o.text for o in brief.offers] == [offer], offer


def test_creation_echo_with_demonstrative_is_dropped():
    """A creation echo that uses a demonstrative ("Make THIS flyer for veg…", or a bare
    "This flyer for veg…") must STILL drop — a demonstrative alone is not redemption; a
    redemption VERB (bring/show/mention) is required to rescue "<flyer> for" (Codex
    round-2: the demonstrative must not launder a creation/describe echo)."""
    raw = "Make this flyer for veg and non veg combo for $49.99"
    for echo in ("Make this flyer for veg and non veg", "This flyer for veg and non veg combo"):
        def provider(_fields, _raw_request, _echo=echo):
            return FlyerSemanticBrief(offers=[FlyerSemanticOffer(text=_echo)])

        brief = build_semantic_flyer_brief(FlyerRequestFields(notes=raw), raw, provider=provider)
        assert brief.offers == [], echo


def test_flyer_medium_echoes_drop_even_via_laundering_paths():
    """Residual-strip closes the Codex round-3 laundering paths: a redemption sentence
    cannot rescue a second flyer-describing echo in the same value, and a four-token
    creation noun phrase ("Make our new meal combo flyer") still drops because a flyer
    NOUN remains after stripping any redemption phrase."""
    raw = (
        "Make our new meal combo flyer for $49.99. Bring this flyer for 10% off. "
        "This flyer for veg combo."
    )
    for echo in (
        "Bring this flyer for 10% off. This flyer for veg combo",  # mixed: redemption + echo
        "Make our new meal combo flyer",                            # 4-token creation noun phrase
    ):
        def provider(_f, _r, _e=echo):
            return FlyerSemanticBrief(offers=[FlyerSemanticOffer(text=_e)])

        brief = build_semantic_flyer_brief(FlyerRequestFields(notes=raw), raw, provider=provider)
        assert brief.offers == [], echo


def test_redemption_offers_with_broad_determiners_survive():
    """Codex round-3 MINOR: a redemption verb with a/an/our before the token is legit —
    the redemption VERB is the load-bearing signal, not the determiner."""
    raw = "Create a flyer for our sale. Bring a flyer for 10% off. Mention our poster for free dessert."
    for offer in ("Bring a flyer for 10% off", "Mention our poster for free dessert"):
        def provider(_f, _r, _o=offer):
            return FlyerSemanticBrief(offers=[FlyerSemanticOffer(text=_o)])

        brief = build_semantic_flyer_brief(FlyerRequestFields(notes=raw), raw, provider=provider)
        assert [o.text for o in brief.offers] == [offer], offer


def test_verb_plus_flyer_without_payoff_is_not_redemption():
    """Codex round-4: the redemption strip must require a PAYOFF (discount/freebie). A
    verb+flyer that merely DESCRIBES content ("Show this flyer with veg combo prices",
    "Mention our poster should include the veg combo") is an echo, not redemption — its
    flyer noun must still trip the echo scan and drop."""
    raw = (
        "Show this flyer with veg combo prices. Mention our poster should include the "
        "veg combo. Bring this flyer for 10% off."
    )
    for echo in (
        "Show this flyer with veg combo prices",
        "Mention our poster should include the veg combo",
        "Show this flyer for veg combo prices",  # 'for' but content, not a payoff
    ):
        def provider(_f, _r, _e=echo):
            return FlyerSemanticBrief(offers=[FlyerSemanticOffer(text=_e)])

        brief = build_semantic_flyer_brief(FlyerRequestFields(notes=raw), raw, provider=provider)
        assert brief.offers == [], echo


def test_graduation_request_offers_stay_bounded_and_grounded():
    """Graduation flyer (no real $ offer). A model that returns a long campaign-y offer
    plus a faithful short one yields ONLY the bounded faithful offer; nothing > 180 chars
    and the campaign title stays sane."""
    raw = (
        "Create a flyer for Graduation celebration, gold navy white, graduation caps. "
        "Lucky draw eligible with purchase above $50."
    )
    long_offer = (
        "Create a flyer for Graduation celebration with gold navy white graduation "
        "caps and a beautiful elegant campus background for the whole ceremony season"
    )

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(
            campaign_title="Graduation Celebration",
            offers=[
                FlyerSemanticOffer(text=long_offer),
                FlyerSemanticOffer(text="Lucky draw eligible with purchase above $50"),
            ],
        )

    brief = build_semantic_flyer_brief(FlyerRequestFields(notes=raw), raw, provider=provider)

    assert brief.campaign_title == "Graduation Celebration"
    assert [offer.text for offer in brief.offers] == [
        "Lucky draw eligible with purchase above $50"
    ]
    assert all(len(offer.text) <= 180 for offer in brief.offers)
