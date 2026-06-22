"""Tests for the deterministic CD v2 campaign-narrative quality referee."""
from __future__ import annotations

from agents.flyer.flyer_narrative_quality import (
    evaluate_narrative_candidate,
    select_campaign_narrative,
)
from schemas import FlyerLockedFact


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
        _fact("item:2:name", "Poori"),
        _fact("pricing_structure", "Any item $7.99"),
    ]


def test_rejects_banned_filler_even_when_other_words_sound_food_related():
    result = evaluate_narrative_candidate(
        "Weekend Specials Featuring Famous South Indian Delights",
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
        schedule="Saturday & Sunday",
    )
    assert not result.accepted
    assert "banned_phrase" in result.reasons


def test_selects_offer_aware_benefit_over_generic_title_restatement():
    selected = select_campaign_narrative(
        [
            "Enjoy Our Weekend Specials with Famous South Indian Dishes",
            "Weekend Specials",
            "Weekend favorites, one clear price.",
        ],
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
        schedule="Saturday & Sunday",
    )
    assert selected == "Weekend favorites, one clear price."


def test_rejects_candidates_that_ignore_offer_when_offer_exists():
    result = evaluate_narrative_candidate(
        "Bring the family together.",
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
        schedule="Saturday & Sunday",
    )
    assert not result.accepted
    assert "offer_ignored" in result.reasons


def test_plain_offer_fact_also_requires_offer_awareness():
    facts = [
        _fact("campaign_title", "Combo Specials"),
        _fact("offer", "Two combo choices"),
        _fact("item:0:name", "Mini Tiffin Combo"),
    ]
    result = evaluate_narrative_candidate(
        "A warm table for everyone.",
        locked_facts=facts,
        campaign_title="Combo Specials",
    )
    assert not result.accepted
    assert "offer_ignored" in result.reasons


def test_item_only_line_does_not_count_as_price_offer_awareness():
    facts = [
        _fact("campaign_title", "Bucket Biryani Special"),
        _fact("pricing_structure", "Bucket Biryani $29.99"),
        _fact("item:0:name", "Bucket Biryani"),
    ]
    result = evaluate_narrative_candidate(
        "Big biryani, easy sharing.",
        locked_facts=facts,
        campaign_title="Bucket Biryani Special",
    )
    assert not result.accepted
    assert "offer_ignored" in result.reasons


def test_rejects_near_copy_of_known_good_example():
    result = evaluate_narrative_candidate(
        "Two combos, one simple choice.",
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
    )
    assert not result.accepted
    assert "known_example" in result.reasons


def test_rejects_copied_known_example_signature_phrase():
    result = evaluate_narrative_candidate(
        "A table full of favorites, one easy price.",
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
    )
    assert not result.accepted
    assert "known_example" in result.reasons


def test_rejects_thin_title_fragments_and_generic_title_extensions():
    dessert = evaluate_narrative_candidate(
        "Desserts.",
        locked_facts=[_fact("campaign_title", "Festival Dessert Specials")],
        campaign_title="Festival Dessert Specials",
    )
    assert not dessert.accepted
    assert "too_thin" in dessert.reasons

    grand_opening = evaluate_narrative_candidate(
        "Grand Opening for everyone.",
        locked_facts=[_fact("campaign_title", "Grand Opening Celebration")],
        campaign_title="Grand Opening Celebration",
    )
    assert not grand_opening.accepted
    assert "title_restatement" in grand_opening.reasons

    customer_appreciation = evaluate_narrative_candidate(
        "Customer Appreciation with thanks.",
        locked_facts=[_fact("campaign_title", "Customer Appreciation Day")],
        campaign_title="Customer Appreciation Day",
    )
    assert not customer_appreciation.accepted
    assert "title_restatement" in customer_appreciation.reasons


def test_rejects_generic_three_word_slogans_without_marketing_signal():
    for probe in (
        "Bright local moment.",
        "Nice food here.",
        "Fresh event now.",
        "Good times ahead.",
    ):
        result = evaluate_narrative_candidate(
            probe,
            locked_facts=[_fact("campaign_title", "Weekend Specials")],
            campaign_title="Weekend Specials",
        )
        assert not result.accepted, probe
        assert "no_quality_signal" in result.reasons


def test_rejects_generic_benefit_captions_even_with_positive_words():
    for probe in (
        "Fresh flavors, easy choice.",
        "Fresh flavors for everyone.",
        "A warm table for everyone.",
        "New flavors for everyone.",
        "Family favorites made easy.",
    ):
        result = evaluate_narrative_candidate(
            probe,
            locked_facts=[_fact("campaign_title", "Weekend Specials")],
            campaign_title="Weekend Specials",
        )
        assert not result.accepted, probe
        assert "generic_caption" in result.reasons
        assert "no_quality_signal" in result.reasons


def test_rejects_generic_offer_copy_even_when_offer_exists():
    facts = _weekend_specials_facts()
    for probe in (
        "Fresh flavors, big value.",
        "Everyone gets an easy choice.",
        "Family favorites, easy value.",
    ):
        result = evaluate_narrative_candidate(
            probe,
            locked_facts=facts,
            campaign_title="Weekend Specials",
            schedule="Saturday & Sunday",
        )
        assert not result.accepted, probe
        assert "generic_caption" in result.reasons


def test_selects_specific_offer_copy_over_generic_offer_copy():
    selected = select_campaign_narrative(
        [
            "Fresh flavors, big value.",
            "Weekend favorites, one clear price.",
        ],
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
        schedule="Saturday & Sunday",
    )
    assert selected == "Weekend favorites, one clear price."


def test_rejects_offer_language_without_locked_offer_fact():
    for probe in (
        "One clear price for every table.",
        "Combo choices made simple.",
        "Big value for every table.",
    ):
        result = evaluate_narrative_candidate(
            probe,
            locked_facts=[_fact("campaign_title", "Diwali Celebration")],
            campaign_title="Diwali Celebration",
        )
        assert not result.accepted, probe
        assert "unsupported_offer_language" in result.reasons


def test_rejects_unsupported_product_nouns_without_locked_fact():
    for probe in (
        "Pizza comfort for every table.",
        "Buffet feast for every table.",
        "Coffee comfort for every table.",
        "Cake comfort for every table.",
        "Thali comfort for every table.",
        "Brunch comfort for every table.",
    ):
        result = evaluate_narrative_candidate(
            probe,
            locked_facts=[_fact("campaign_title", "Diwali Celebration")],
            campaign_title="Diwali Celebration",
        )
        assert not result.accepted, probe
        assert "unsupported_product_language" in result.reasons


def test_allows_product_nouns_when_grounded_by_locked_facts():
    result = evaluate_narrative_candidate(
        "Biryani comfort for every table.",
        locked_facts=[
            _fact("campaign_title", "Bucket Biryani Special"),
            _fact("item:0:name", "Bucket Biryani"),
        ],
        campaign_title="Bucket Biryani Special",
    )
    assert result.accepted, result.reasons


def test_rejects_relative_schedule_words_without_schedule_grounding():
    for probe in (
        "Family feast tonight.",
        "Family feast today.",
        "Family feast this evening.",
        "Family feast soon.",
    ):
        result = evaluate_narrative_candidate(
            probe,
            locked_facts=[_fact("campaign_title", "Family Feast")],
            campaign_title="Family Feast",
        )
        assert not result.accepted, probe
        assert "unsupported_schedule_language" in result.reasons


def test_grounded_content_token_alone_is_not_a_marketing_signal():
    probes = [
        (
            "Diwali event now.",
            [_fact("campaign_title", "Diwali Celebration"), _fact("event", "Diwali")],
            "Diwali Celebration",
        ),
        (
            "Gulab Jamun here.",
            [
                _fact("campaign_title", "Festival Dessert Specials"),
                _fact("item:0:name", "Gulab Jamun"),
            ],
            "Festival Dessert Specials",
        ),
        (
            "Bucket Biryani now.",
            [
                _fact("campaign_title", "Bucket Biryani Special"),
                _fact("item:0:name", "Bucket Biryani"),
            ],
            "Bucket Biryani Special",
        ),
        (
            "Biryani food here.",
            [
                _fact("campaign_title", "Bucket Biryani Special"),
                _fact("item:0:name", "Bucket Biryani"),
            ],
            "Bucket Biryani Special",
        ),
    ]
    for probe, facts, title in probes:
        result = evaluate_narrative_candidate(
            probe,
            locked_facts=facts,
            campaign_title=title,
        )
        assert not result.accepted, probe
        assert "no_quality_signal" in result.reasons


def test_prefers_non_recent_phrase_when_valid_alternative_exists():
    facts = [
        _fact("campaign_title", "Bucket Biryani Special"),
        _fact("item:0:name", "Bucket Biryani"),
    ]
    selected = select_campaign_narrative(
        [
            "A family feast in one bucket.",
            "Big biryani, easy sharing.",
        ],
        locked_facts=facts,
        campaign_title="Bucket Biryani Special",
        recent_narratives=["A family feast in one bucket."],
    )
    assert selected == "Big biryani, easy sharing."


def test_rejects_near_repeat_of_recent_narrative():
    facts = [
        _fact("campaign_title", "Bucket Biryani Special"),
        _fact("item:0:name", "Bucket Biryani"),
    ]
    for probe in (
        "A family feast in every bucket.",
        "A family feast, one bucket.",
        "Family feast in one bucket.",
    ):
        result = evaluate_narrative_candidate(
            probe,
            locked_facts=facts,
            campaign_title="Bucket Biryani Special",
            recent_narratives=["A family feast in one bucket."],
        )
        assert not result.accepted, probe
        assert "recent_repeat" in result.reasons


def test_selects_clean_alternative_over_recent_near_repeat():
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


def test_rejects_unsafe_claims_before_quality_scoring():
    selected = select_campaign_narrative(
        [
            "Today only, everything at $5.",
            "Cravings covered at one clear price.",
        ],
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
        schedule="Saturday & Sunday",
    )
    assert selected == "Cravings covered at one clear price."


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


def test_rejects_overlong_caption_like_candidate():
    result = evaluate_narrative_candidate(
        "Bring everyone together for a long weekend celebration of authentic South Indian classics and freshly prepared family favorites",
        locked_facts=_weekend_specials_facts(),
        campaign_title="Weekend Specials",
        schedule="Saturday & Sunday",
    )
    assert not result.accepted
    assert "too_long" in result.reasons


def test_required_seven_category_eval_replaces_caption_copy_with_marketing_messages():
    """Operator-required eval: the seven canonical flyer categories must clear
    the deterministic narrative gate with meaningful, non-parroting variation."""
    cases = [
        (
            "Weekend Specials",
            "Weekend Specials Featuring Famous South Indian Delights",
            [
                "Weekend Specials Featuring Famous South Indian Delights",
                "Weekend favorites, one clear price.",
                "Favorites made easy for every table.",
            ],
                [
                    _fact("campaign_title", "Weekend Specials"),
                    _fact("schedule", "Saturday & Sunday"),
                    _fact("pricing_structure", "Any item $7.99"),
                    _fact("item:0:name", "Masala Dosa"),
                    _fact("item:1:name", "Idli Sambar"),
            ],
        ),
        (
            "Dessert",
            "Indulge in Our Festival Dessert Specials",
            [
                "Indulge in Our Festival Dessert Specials",
                "Desserts worth saving room for.",
                "Sweet cravings, weekend made simple.",
            ],
            [
                _fact("campaign_title", "Festival Dessert Specials"),
                _fact("item:0:name", "Gulab Jamun"),
                _fact("item:1:name", "Rasmalai"),
            ],
        ),
        (
            "Combo",
            "Weekend Combo Specials Await You",
            [
                "Weekend Combo Specials Await You",
                "Combo choices made simple.",
                "Dinner decisions made easy.",
            ],
            [
                _fact("campaign_title", "Weekend Combo Specials"),
                _fact("offer", "2 combos"),
                _fact("item:0:name", "Family Combo"),
            ],
        ),
        (
            "Bucket Biryani",
            "Enjoy Our Bucket Biryani Special",
            [
                "Enjoy Our Bucket Biryani Special",
                "A family feast in one bucket.",
                "Big biryani, easy sharing.",
            ],
            [
                _fact("campaign_title", "Bucket Biryani Special"),
                _fact("item:0:name", "Bucket Biryani"),
            ],
        ),
        (
            "Event / Diwali",
            "Join Us for Our Diwali Celebration",
            [
                "Join Us for Our Diwali Celebration",
                "Celebrate Diwali with a festive feast.",
                "A brighter table for Diwali.",
            ],
            [
                _fact("campaign_title", "Diwali Celebration"),
                _fact("event", "Diwali"),
            ],
        ),
        (
            "Grand Opening",
            "Grand Opening Celebration Await You",
            [
                "Grand Opening Celebration Await You",
                "A warm welcome starts here.",
                "New doors, fresh flavors.",
            ],
            [_fact("campaign_title", "Grand Opening Celebration")],
        ),
        (
            "Customer Appreciation",
            "Enjoy Our Customer Appreciation Day",
            [
                "Enjoy Our Customer Appreciation Day",
                "A thank-you for every table.",
                "Our thanks, served fresh.",
            ],
            [_fact("campaign_title", "Customer Appreciation Day")],
        ),
    ]

    selected: list[tuple[str, list[FlyerLockedFact]]] = []
    for _category, before, candidates, facts in cases:
        title = next(f.value for f in facts if f.fact_id == "campaign_title")
        before_eval = evaluate_narrative_candidate(
            before,
            locked_facts=facts,
            campaign_title=title,
            schedule=next((f.value for f in facts if f.fact_id == "schedule"), ""),
        )
        assert not before_eval.accepted
        selected_text = select_campaign_narrative(
            candidates,
            locked_facts=facts,
            campaign_title=title,
            schedule=next((f.value for f in facts if f.fact_id == "schedule"), ""),
        )
        after_eval = evaluate_narrative_candidate(
            selected_text,
            locked_facts=facts,
            campaign_title=title,
            schedule=next((f.value for f in facts if f.fact_id == "schedule"), ""),
        )
        selected.append((selected_text, facts))
        assert after_eval.accepted, (selected_text, after_eval.reasons)

    assert len(selected) == 7
    assert len({_text.lower() for _text, _facts in selected}) == 7
    assert sum(
        evaluate_narrative_candidate(
            chosen,
            locked_facts=facts,
            campaign_title=next(f.value for f in facts if f.fact_id == "campaign_title"),
            schedule=next((f.value for f in facts if f.fact_id == "schedule"), ""),
        ).accepted
        for chosen, facts in selected
    ) >= 6
