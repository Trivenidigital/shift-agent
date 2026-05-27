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
