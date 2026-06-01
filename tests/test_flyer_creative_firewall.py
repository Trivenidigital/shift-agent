"""Slice 3 — hard-fact firewall (truth-guard core).

The firewall is the sole materialization gate for planner candidates. It must let
legitimate item names through and DROP any candidate that smuggles a hard-fact-
class claim (§6b: the #1 risk is a claim disguised as an item name). Fail-closed.
"""
from __future__ import annotations

import pytest

from agents.flyer.creative_firewall import CreativeFirewall, is_hard_fact_claim
from agents.flyer.creative_planner import CreativeCandidate


def _items(*names):
    return [CreativeCandidate(kind="item", value=n) for n in names]


def test_legitimate_item_names_pass():
    fw = CreativeFirewall()
    names = ["Idli", "Masala Dosa", "Veg Manchurian", "Plain Dosa", "Medu Vada",
             "Uttapam", "Pongal", "Filter Coffee", "7 Up", "Item 65",
             "Gluten Free Dosa", "Sugar Free Sweet"]  # compound "free" must PASS (not lone)
    cleared = fw.clear(_items(*names))
    assert [c.value for c in cleared] == names  # all pass; incidental digits OK


@pytest.mark.parametrize("claim", [
    "Free Delivery",          # service claim
    "Open Daily 8-11",        # schedule
    "20% Off",                # discount
    "$8.99 Special",          # price
    "Lowest Prices in Town",  # superlative price claim
    "Best Price Guaranteed",  # guarantee
    "Sat & Sun Brunch",       # day-of-week
    "Call 555-123-4567",      # phone
    "Visit www.shop.com",     # url
    "Weekend Combo Deal",     # weekend + deal
    "From $5",                # from-price
    "Certified Organic",      # legal/cert claim
    "Cash Only",              # payment claim (Codex r1)
    "Card Accepted",          # payment claim (Codex r1)
    "UPI Accepted",           # payment claim (Codex r1)
    "Order at shop.com",      # bare domain (Codex r1)
    "+1 732 555 1212",        # international phone (Codex r1)
    "₹8.99 Thali",            # non-$ currency (Codex r1)
    "We Accept Venmo",        # payment claim
    "Thali 8.99",             # plain decimal price-shape (Codex r2)
    "Combo 12/99",            # slash price-shape (Codex r2)
    "Brunch 8 to 11",         # "to" time range (Codex r2)
    "Best Prices Here",       # plural superlative (Codex r2)
    "Free",                   # lone "Free" claim (Codex r2)
    "",                       # empty
    "   ",                    # whitespace
])
def test_hard_fact_class_claims_are_rejected(claim):
    assert is_hard_fact_claim(claim) is True
    assert CreativeFirewall().clear(_items(claim)) == []


def test_clear_drops_only_the_unsafe_candidates():
    fw = CreativeFirewall()
    cands = _items("Idli", "Free Delivery", "Masala Dosa", "20% Off", "Poori")
    cleared = fw.clear(cands)
    assert [c.value for c in cleared] == ["Idli", "Masala Dosa", "Poori"]
    assert [c.value for c in fw.rejected(cands)] == ["Free Delivery", "20% Off"]


def test_non_item_kind_candidates_are_dropped():
    fw = CreativeFirewall()
    cands = [CreativeCandidate(kind="headline", value="Tasty!"),
             CreativeCandidate(kind="item", value="Dosa")]
    assert [c.value for c in fw.clear(cands)] == ["Dosa"]  # only item-kind passes
