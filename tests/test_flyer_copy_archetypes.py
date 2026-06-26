"""Tests for Controlled Copy Archetypes (CCA): deterministic archetype classification
from fact structure + headline composition from approved templates + grounded slots.

NO LLM, NO network — pure Python. The composed headline is validated by the existing
deterministic firewall (scrub_campaign_narrative) in the resolver, tested separately.
"""
from __future__ import annotations

from agents.flyer.flyer_copy_archetypes import (
    classify_archetype,
    compose_archetype_headlines,
)
from schemas import FlyerLockedFact


def _fact(fact_id: str, value: str) -> FlyerLockedFact:
    return FlyerLockedFact(fact_id=fact_id, label=fact_id[:80], value=value, source="customer_text")


def _weekend_one_price():
    return [
        _fact("campaign_title", "Weekend Specials"),
        _fact("schedule", "Available Saturday & Sunday, 4 PM-8 PM"),
        _fact("pricing_structure", "Any item $7.99"),
        _fact("item:0:name", "Idli"),
        _fact("item:1:name", "Dosa"),
    ]


def _combo():
    return [
        _fact("campaign_title", "Weekend Combo Specials"),
        _fact("schedule", "Available Friday, Saturday and Sunday"),
        _fact("offer:0", "Veg Combo - $12.99 Includes: 2 curries, dessert"),
        _fact("offer:1", "Non-Veg Combo - $15.99 Includes: 2 curries, chicken biryani, dessert"),
    ]


def _bucket():
    return [
        _fact("campaign_title", "Family Biryani Bucket"),
        _fact("offer:0", "Family Biryani Bucket - $39.99, feeds 4, includes raita, salad, dessert"),
    ]


def _grand_opening():
    return [
        _fact("campaign_title", "Grand Opening"),
        _fact("schedule", "This Saturday"),
        _fact("offer:0", "Free mango lassi for the first 100 guests"),
        _fact("location", "new Plano location"),
    ]


def _appreciation():
    return [
        _fact("campaign_title", "Customer Appreciation Week"),
        _fact("schedule", "All week"),
        _fact("offer:0", "Complimentary dessert with every dinner"),
    ]


def _festival_dessert():
    return [
        _fact("campaign_title", "Festival Dessert Specials"),
        _fact("schedule", "Available Friday through Sunday"),
        _fact("item:0:name", "Gulab Jamun"),
        _fact("item:1:name", "Rasmalai Tres Leches"),
    ]


def _event():
    return [
        _fact("campaign_title", "Diwali Celebration Dinner"),
        _fact("schedule", "Saturday October 19, 6-10 PM"),
        _fact("offer:0", "Live music and a festive thali"),
    ]


# --- classification ---------------------------------------------------------

def test_classify_weekend_one_price():
    assert classify_archetype(_weekend_one_price(), campaign_title="Weekend Specials") == "weekend_one_price"


def test_classify_combo():
    assert classify_archetype(_combo(), campaign_title="Weekend Combo Specials") == "combo"


def test_classify_bucket_meal():
    assert classify_archetype(_bucket(), campaign_title="Family Biryani Bucket") == "bucket_meal"


def test_classify_grand_opening():
    assert classify_archetype(_grand_opening(), campaign_title="Grand Opening") == "grand_opening"


def test_classify_customer_appreciation():
    assert classify_archetype(_appreciation(), campaign_title="Customer Appreciation Week") == "customer_appreciation"


def test_classify_festival_dessert():
    assert classify_archetype(_festival_dessert(), campaign_title="Festival Dessert Specials") == "festival_dessert"


def test_classify_event():
    assert classify_archetype(_event(), campaign_title="Diwali Celebration Dinner") == "event"


def test_classify_empty_is_none():
    assert classify_archetype([], campaign_title="") == "none"


def test_classify_ambiguous_plain_menu_is_none():
    facts = [_fact("campaign_title", "Our Menu"), _fact("item:0:name", "Plain Item")]
    assert classify_archetype(facts, campaign_title="Our Menu") == "none"


# --- composition (deterministic, exact approved phrases) ---------------------

def test_compose_weekend_one_price_offer_explicit_first():
    out = compose_archetype_headlines(_weekend_one_price(), campaign_title="Weekend Specials",
                                      schedule="Available Saturday & Sunday, 4 PM-8 PM")
    assert out[0] == "$7.99 favorites all weekend."
    assert "Weekend favorites, one easy price." in out


def test_compose_combo_two_combos_first():
    out = compose_archetype_headlines(_combo(), campaign_title="Weekend Combo Specials")
    assert out[0] == "Two combos. One easy choice."


def test_compose_bucket_uses_grounded_product():
    out = compose_archetype_headlines(_bucket(), campaign_title="Family Biryani Bucket")
    assert out[0] == "Biryani feast, served by the bucket."


def test_compose_grand_opening_free_item_grounded():
    out = compose_archetype_headlines(_grand_opening(), campaign_title="Grand Opening")
    assert out[0] == "New location. Free mango lassi."
    assert "A warm welcome, on us." in out  # safe fallback always present


def test_compose_appreciation_free_item_grounded():
    out = compose_archetype_headlines(_appreciation(), campaign_title="Customer Appreciation Week")
    assert out[0] == "A thank-you dessert on us."
    assert "Our treat for your table." in out


def test_compose_festival_dessert():
    out = compose_archetype_headlines(_festival_dessert(), campaign_title="Festival Dessert Specials")
    assert out[0] == "Sweet trays for every celebration."


def test_compose_event_uses_grounded_occasion():
    out = compose_archetype_headlines(_event(), campaign_title="Diwali Celebration Dinner")
    assert out[0] == "Diwali dinner, served festive."
    assert "Celebrate Diwali around the table." in out


# --- fail-closed / grounding safety -----------------------------------------

def test_compose_none_archetype_returns_empty():
    assert compose_archetype_headlines([_fact("campaign_title", "Our Menu")], campaign_title="Our Menu") == []


def test_weekend_one_price_requires_shared_price():
    """Different item prices = no shared price → NOT weekend_one_price (fail-closed)."""
    facts = [
        _fact("campaign_title", "Weekend Specials"),
        _fact("schedule", "Saturday & Sunday"),
        _fact("item:0:name", "Idli"), _fact("item:0:price", "$5.99"),
        _fact("item:1:name", "Dosa"), _fact("item:1:price", "$8.99"),
    ]
    assert classify_archetype(facts, campaign_title="Weekend Specials") != "weekend_one_price"


def test_grand_opening_without_free_offer_falls_to_safe_template():
    """No grounded free offer → the 'Free ${item}' template is ineligible; the safe
    no-offer template is still produced (fail-closed, never fabricates a free item)."""
    facts = [_fact("campaign_title", "Grand Opening"), _fact("schedule", "This Saturday")]
    out = compose_archetype_headlines(facts, campaign_title="Grand Opening")
    assert out  # still produces a headline
    assert all("Free " not in c for c in out)  # never an ungrounded free claim
    assert "A warm welcome, on us." in out


def test_compose_fabricated_price_never_appears():
    """compose only uses grounded prices; an item-price set never yields an unstated price."""
    out = compose_archetype_headlines(_weekend_one_price(), campaign_title="Weekend Specials",
                                      schedule="Saturday & Sunday")
    assert all("$9.99" not in c and "$5.99" not in c for c in out)  # only the grounded $7.99


def test_classify_combo_from_title_only():
    """A 'combo' signal in the TITLE classifies combo even with no combo-bearing offers
    (mirrors bucket_meal scanning the title)."""
    facts = [
        _fact("campaign_title", "Weekend Combo Specials"),
        _fact("item:0:name", "Veg Plate"),
        _fact("item:1:name", "Rice Bowl"),
    ]
    assert classify_archetype(facts, campaign_title="Weekend Combo Specials") == "combo"


def test_compose_single_combo_skips_two_combos_template():
    facts = [
        _fact("campaign_title", "Combo Special"),
        _fact("offer:0", "Lunch Combo - $9.99"),
    ]
    out = compose_archetype_headlines(facts, campaign_title="Combo Special")
    assert out[0] == "Dinner combos made easy."  # 'Two combos.' needs >=2 combos


def test_classify_festival_dessert_title_is_not_event():
    """'Festival Dessert' must NOT hit the event archetype ('festival' is not an occasion)."""
    assert classify_archetype(_festival_dessert(), campaign_title="Festival Dessert Specials") == "festival_dessert"


def test_classify_event_beats_festival_dessert_when_occasion_present():
    """An occasion name in the title (Diwali) wins over dessert items (event > dessert)."""
    facts = [
        _fact("campaign_title", "Diwali Sweets Festival"),
        _fact("item:0:name", "Gulab Jamun"),
        _fact("item:1:name", "Jalebi"),
    ]
    assert classify_archetype(facts, campaign_title="Diwali Sweets Festival") == "event"


def test_compose_bucket_without_food_noun_uses_safe_fallback():
    """A bucket with NO grounded food noun never fabricates a product — only the safe
    no-slot template is produced (fail-closed)."""
    facts = [_fact("campaign_title", "Family Value Bucket")]
    out = compose_archetype_headlines(facts, campaign_title="Family Value Bucket")
    assert out == ["A feast for the whole table, by the bucket."]
