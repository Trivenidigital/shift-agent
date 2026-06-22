"""Tests for the deterministic CD v2 campaign-narrative quality referee."""
from __future__ import annotations

from agents.flyer.flyer_narrative_quality import (
    evaluate_narrative_candidate,
    select_campaign_narrative,
)
from schemas import FlyerLockedFact


EXPECTED_CATEGORY_KEYS = {
    "safe",
    "no_banned_crutches",
    "not_title_restatement",
    "grounded_specificity",
    "offer_context_fit",
    "benefit_or_emotion",
    "not_recent_or_known_repeat",
}


def _fact(fact_id: str, value: str, *, label: str = "Fact") -> FlyerLockedFact:
    return FlyerLockedFact(
        fact_id=fact_id,
        label=label,
        value=value,
        source="customer_text",
        required=True,
    )


def _weekend_specials_facts() -> list[FlyerLockedFact]:
    return [
        _fact("campaign_title", "Weekend Specials"),
        _fact("schedule", "Saturday & Sunday"),
        _fact("item:0:name", "Masala Dosa"),
        _fact("item:1:name", "Idli Sambar"),
        _fact("pricing_structure", "Any item $7.99"),
    ]


def test_good_candidate_reports_exact_seven_category_eval():
    result = evaluate_narrative_candidate(
        "Weekend favorites, one clear price.",
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
        schedule="Saturday & Sunday",
    )

    assert set(result.category_results) == EXPECTED_CATEGORY_KEYS
    assert sum(result.category_results.values()) >= 6
    assert result.accepted
    assert result.score >= 6


def test_rejects_banned_crutch_caption_even_when_food_related():
    result = evaluate_narrative_candidate(
        "Weekend Specials Featuring Famous South Indian Delights",
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
        schedule="Saturday & Sunday",
    )

    assert not result.accepted
    assert not result.category_results["no_banned_crutches"]
    assert "banned_crutch" in result.reasons


def test_rejects_title_restatement_and_known_example_parroting():
    title_repeat = evaluate_narrative_candidate(
        "Grand Opening for everyone.",
        locked_facts=[_fact("campaign_title", "Grand Opening Celebration")],
        campaign_title="Grand Opening Celebration",
    )
    assert not title_repeat.accepted
    assert not title_repeat.category_results["not_title_restatement"]
    assert "title_restatement" in title_repeat.reasons

    for event_title in (
        "Diwali Celebration",
        "Holi Celebration",
        "Christmas Celebration",
        "Eid Celebration",
        "Pongal Festival",
    ):
        title_plus_generic_benefit = evaluate_narrative_candidate(
            f"{event_title} with family.",
            locked_facts=[_fact("campaign_title", event_title)],
            campaign_title=event_title,
        )
        assert not title_plus_generic_benefit.accepted
        assert not title_plus_generic_benefit.category_results[
            "not_title_restatement"
        ]
        assert "title_restatement" in title_plus_generic_benefit.reasons

    for copy in ("Diwali for family.", "Diwali family together."):
        one_token_title_laundering = evaluate_narrative_candidate(
            copy,
            locked_facts=[_fact("campaign_title", "Diwali Celebration")],
            campaign_title="Diwali Celebration",
        )
        assert not one_token_title_laundering.accepted
        assert not one_token_title_laundering.category_results[
            "grounded_specificity"
        ]
        assert "ungrounded_specificity" in one_token_title_laundering.reasons

    known_copy = evaluate_narrative_candidate(
        "Two combos, one simple choice.",
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
    )
    assert not known_copy.accepted
    assert not known_copy.category_results["not_recent_or_known_repeat"]
    assert "known_example" in known_copy.reasons


def test_rejects_generic_caption_without_benefit_or_emotional_angle():
    result = evaluate_narrative_candidate(
        "Bright local moment.",
        locked_facts=[_fact("campaign_title", "Weekend Specials")],
        campaign_title="Weekend Specials",
    )

    assert not result.accepted
    assert not result.category_results["benefit_or_emotion"]
    assert "no_benefit_or_emotion" in result.reasons


def test_offer_facts_require_offer_aware_narrative():
    result = evaluate_narrative_candidate(
        "Bring the family together.",
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
        schedule="Saturday & Sunday",
    )

    assert not result.accepted
    assert not result.category_results["offer_context_fit"]
    assert "offer_ignored" in result.reasons


def test_accepts_six_of_seven_when_only_offer_context_is_missing():
    result = evaluate_narrative_candidate(
        "Dosa favorites for family.",
        locked_facts=[
            _fact("campaign_title", "Weekend Specials"),
            _fact("item:0:name", "Masala Dosa"),
            _fact("pricing_structure", "Any item $7.99"),
        ],
        campaign_title="Weekend Specials",
    )

    assert result.accepted
    assert result.score == 6
    assert not result.category_results["offer_context_fit"]
    assert result.reasons == ["offer_ignored"]


def test_rejects_ungrounded_numeric_claims():
    facts = [
        _fact("campaign_title", "Weekend Specials"),
        _fact("item:0:name", "Masala Dosa"),
        _fact("pricing_structure", "Any item $7.99"),
    ]

    for copy in (
        "Dosa 3 family favorites.",
        "Dosa two family favorites.",
        "Dosa one family favorite.",
    ):
        result = evaluate_narrative_candidate(
            copy,
            locked_facts=facts,
            campaign_title="Weekend Specials",
        )

        assert not result.accepted
        assert not result.category_results["safe"]
        assert not result.category_results["grounded_specificity"]
        assert not result.category_results["offer_context_fit"]
        assert "unsupported_numeric_language" in result.reasons


def test_accepts_grounded_one_price_narrative():
    result = evaluate_narrative_candidate(
        "South Indian Favorites at One Price",
        locked_facts=[
            _fact("campaign_title", "Weekend Specials"),
            _fact("item:0:name", "Masala Dosa"),
            _fact("item:1:name", "Idli Sambar"),
            _fact("pricing_structure", "any item $7.99"),
        ],
        campaign_title="Weekend Specials",
    )

    assert result.accepted
    assert result.score >= 6


def test_rejects_generic_offer_words_without_fact_specific_grounding():
    result = evaluate_narrative_candidate(
        "Easy choice worth sharing.",
        locked_facts=[
            _fact("campaign_title", "Weekend Specials"),
            _fact("pricing_structure", "Any item $7.99"),
        ],
        campaign_title="Weekend Specials",
    )

    assert not result.accepted
    assert not result.category_results["grounded_specificity"]
    assert "ungrounded_specificity" in result.reasons


def test_rejects_unsupported_offer_and_product_language():
    unsupported_offer = evaluate_narrative_candidate(
        "One clear price for every table.",
        locked_facts=[_fact("campaign_title", "Diwali Celebration")],
        campaign_title="Diwali Celebration",
    )
    assert not unsupported_offer.accepted
    assert not unsupported_offer.category_results["safe"]
    assert "unsupported_offer_language" in unsupported_offer.reasons

    title_grounded_unsupported_offer = evaluate_narrative_candidate(
        "Diwali clear price for every table.",
        locked_facts=[_fact("campaign_title", "Diwali Celebration")],
        campaign_title="Diwali Celebration",
    )
    assert not title_grounded_unsupported_offer.accepted
    assert not title_grounded_unsupported_offer.category_results["safe"]
    assert "unsupported_offer_language" in title_grounded_unsupported_offer.reasons

    unsupported_product = evaluate_narrative_candidate(
        "Pizza comfort for every table.",
        locked_facts=[_fact("campaign_title", "Diwali Celebration")],
        campaign_title="Diwali Celebration",
    )
    assert not unsupported_product.accepted
    assert "unsupported_product_language" in unsupported_product.reasons

    for copy in (
        "Taco comfort for every table.",
        "Kebab comfort for every table.",
        "Samosa comfort for every table.",
    ):
        ungrounded_specific = evaluate_narrative_candidate(
            copy,
            locked_facts=[_fact("campaign_title", "Diwali Celebration")],
            campaign_title="Diwali Celebration",
        )
        assert not ungrounded_specific.accepted
        assert not ungrounded_specific.category_results["grounded_specificity"]

    mixed_grounded_product = evaluate_narrative_candidate(
        "Diwali taco comfort for every table.",
        locked_facts=[_fact("campaign_title", "Diwali Celebration")],
        campaign_title="Diwali Celebration",
    )
    assert not mixed_grounded_product.accepted
    assert "unsupported_product_language" in mixed_grounded_product.reasons

    unknown_product = evaluate_narrative_candidate(
        "Diwali falafel comfort for every table.",
        locked_facts=[_fact("campaign_title", "Diwali Celebration")],
        campaign_title="Diwali Celebration",
    )
    assert not unknown_product.accepted
    assert "unsupported_specific_language" in unknown_product.reasons


def test_rejects_relative_schedule_words_without_schedule_fact():
    result = evaluate_narrative_candidate(
        "Family feast tonight.",
        locked_facts=[_fact("campaign_title", "Family Feast")],
        campaign_title="Family Feast",
    )

    assert not result.accepted
    assert "unsupported_schedule_language" in result.reasons


def test_selects_non_recent_specific_alternative():
    facts = [
        _fact("campaign_title", "Bucket Biryani Special"),
        _fact("item:0:name", "Bucket Biryani"),
    ]

    selected = select_campaign_narrative(
        [
            "A family feast in every bucket.",
            "Big biryani, easy sharing.",
        ],
        locked_facts=facts,
        campaign_title="Bucket Biryani Special",
        recent_narratives=["A family feast in one bucket."],
    )

    assert selected == "Big biryani, easy sharing."


def test_all_failed_candidates_fall_back_to_safe_title():
    selected = select_campaign_narrative(
        [
            "Weekend Specials Featuring Famous South Indian Delights",
            "Indulge in Our Weekend Specials",
            "Today only, $5 off every item.",
        ],
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
        schedule="Saturday & Sunday",
    )

    assert selected == "Weekend Specials"


def test_required_seven_category_eval_clears_at_least_six_of_seven_categories():
    cases = [
        (
            "Weekend Specials",
            [
                "Weekend Specials Featuring Famous South Indian Delights",
                "Weekend favorites, one clear price.",
            ],
            _weekend_specials_facts(),
        ),
        (
            "Festival Dessert Specials",
            [
                "Indulge in Our Festival Dessert Specials",
                "Gulab Jamun worth saving room for.",
            ],
            [
                _fact("campaign_title", "Festival Dessert Specials"),
                _fact("item:0:name", "Gulab Jamun"),
                _fact("item:1:name", "Rasmalai"),
            ],
        ),
        (
            "Weekend Combo Specials",
            [
                "Weekend Combo Specials Await You",
                "Combo choices made simple.",
            ],
            [
                _fact("campaign_title", "Weekend Combo Specials"),
                _fact("offer:0", "Two combo choices"),
                _fact("item:0:name", "Family Combo"),
            ],
        ),
        (
            "Bucket Biryani Special",
            [
                "Enjoy Our Bucket Biryani Special",
                "Big biryani, easy sharing.",
            ],
            [
                _fact("campaign_title", "Bucket Biryani Special"),
                _fact("item:0:name", "Bucket Biryani"),
            ],
        ),
        (
            "Grand Opening Celebration",
            [
                "Grand Opening for everyone.",
                "Walk in to something new.",
            ],
            [
                _fact("campaign_title", "Grand Opening Celebration"),
                _fact("event_context", "New storefront"),
            ],
        ),
        (
            "Customer Appreciation Day",
            [
                "Customer Appreciation with thanks.",
                "A small thank-you for loyal customers.",
            ],
            [
                _fact("campaign_title", "Customer Appreciation Day"),
                _fact("audience", "Loyal customers"),
            ],
        ),
        (
            "Diwali Celebration",
            [
                "Diwali event now.",
                "Festival sweets for every table.",
            ],
            [
                _fact("campaign_title", "Diwali Celebration"),
                _fact("item:0:name", "Festival Sweets"),
            ],
        ),
    ]

    accepted = 0
    for title, candidates, facts in cases:
        selected = select_campaign_narrative(
            candidates,
            locked_facts=facts,
            campaign_title=title,
            schedule="Saturday & Sunday" if "Weekend" in title else "",
        )
        result = evaluate_narrative_candidate(
            selected,
            locked_facts=facts,
            campaign_title=title,
            schedule="Saturday & Sunday" if "Weekend" in title else "",
        )
        if result.accepted and sum(result.category_results.values()) >= 6:
            accepted += 1

    assert accepted >= 6
