"""Item-index reconciliation — planner-inferred item facts must never overwrite
customer-grounded item facts. Design: tasks/flyer-item-index-reconciliation-design.md.

Proves the item-index ownership invariant: grounded items own item:0..M (original
values); inferred items are offset to item:M+1..; the request's count phrase ("6 famous
indo-chinese items") is dropped (but a real count-shaped name is kept); a flat customer
price applies to inferred items as customer_text; mixed N=K+inferred reconciles; and the
whole thing is dormant (byte-identical) when the planner is off.
"""
from __future__ import annotations

from schemas import FlyerConfig, FlyerCreativePlannerConfig, FlyerLockedFact, FlyerRequestFields

from agents.flyer import creative_planner as cp
from agents.flyer.facts import (
    _distinct_grounded_item_count,
    _max_item_index,
    _reindex_item_facts,
    _requested_item_count_and_phrase,
    extract_text_facts,
)


def _armed(*categories: str) -> FlyerConfig:
    return FlyerConfig(creative_planner=FlyerCreativePlannerConfig(
        enabled=True, enabled_categories=list(categories) or ["indo-chinese"]))


def _provider(*names: str):
    return lambda: (lambda _f, _r: list(names))


def _item_names(facts):
    return {f.value.casefold(): f for f in facts
            if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")}


# ── helper unit tests (deterministic) ────────────────────────────────────────

def test_offset_helpers_yield_non_colliding_indices():
    grounded = [
        FlyerLockedFact(fact_id="item:0:name", label="Item", value="Samosa", source="customer_text"),
        FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text"),
    ]
    inferred = [
        FlyerLockedFact(fact_id="item:0:name", label="Item", value="Veg Manchurian", source="hermes_inferred"),
        FlyerLockedFact(fact_id="item:1:name", label="Item", value="Hakka Noodles", source="hermes_inferred"),
    ]
    assert _max_item_index(grounded) == 0
    shifted = _reindex_item_facts(inferred, _max_item_index(grounded) + 1)
    # inferred now occupy item:1,2 — no (index,kind) shared with grounded item:0
    assert [f.fact_id for f in shifted] == ["item:1:name", "item:2:name"]
    assert _reindex_item_facts(inferred, 0) == inferred  # base 0 (no grounded) ⇒ no-op
    assert _distinct_grounded_item_count(grounded) == 1


def test_requested_item_count_and_phrase():
    assert _requested_item_count_and_phrase("include 6 famous indo-chinese items") == (
        6, "6 famous indo-chinese items")
    assert _requested_item_count_and_phrase("any item at $8.99") == (None, None)  # a price
    assert _requested_item_count_and_phrase("open 8 AM to 11 AM") == (None, None)  # a time
    cnt, phrase = _requested_item_count_and_phrase("we want 8 items total")
    assert cnt == 8 and "items" in phrase


# ── end-to-end: grounded facts cannot be overwritten ─────────────────────────

def test_grounded_paired_item_survives_planner(monkeypatch):
    """Example B: a customer-grounded paired item (Samosa $5.00) is NOT overwritten by an
    inferred item — both coexist; Samosa keeps customer_text source + its price."""
    monkeypatch.setattr(cp, "build_creative_planner_provider", _provider("Veg Manchurian", "Hakka Noodles"))
    raw = "Indo-chinese flyer. Samosa $5.00. Add more suggestions."
    facts = extract_text_facts(FlyerRequestFields(), raw, cfg=_armed("indo-chinese"))
    names = _item_names(facts)
    assert names["samosa"].source == "customer_text"  # grounded survives, not overwritten
    assert names["veg manchurian"].source == "hermes_inferred"
    assert names["hakka noodles"].source == "hermes_inferred"
    by_id = {f.fact_id: f for f in facts}
    samosa_idx = names["samosa"].fact_id.split(":")[1]
    price = by_id.get(f"item:{samosa_idx}:price")
    assert price is not None and price.value == "$5.00" and price.source == "customer_text"


def test_junk_count_phrase_dropped(monkeypatch):
    """Example C: the request's count phrase mis-parsed as an item is dropped; only the
    planner's items render."""
    monkeypatch.setattr(cp, "build_creative_planner_provider", _provider("Veg Manchurian", "Hakka Noodles"))
    raw = "Flyer, include 6 famous indo-chinese items"
    facts = extract_text_facts(FlyerRequestFields(), raw, cfg=_armed("indo-chinese"))
    names = _item_names(facts)
    assert set(names) == {"veg manchurian", "hakka noodles"}
    assert "6 famous indo-chinese items" not in names
    assert all(f.source == "hermes_inferred" for f in names.values())


def test_real_count_shaped_name_not_dropped(monkeypatch):
    """Junk-drop false-positive guard (Codex r1): a real listed item that merely looks
    count-shaped ("10 items combo") is NOT dropped — the count regex matches only the
    "10 items" clause, which differs from the full item value, so it is preserved as
    customer_text. A broad `^\\d+ ... items$` regex would have wrongly dropped it."""
    monkeypatch.setattr(cp, "build_creative_planner_provider", _provider("Veg Manchurian"))
    raw = "Indo-chinese flyer: include 10 items combo and Paneer Tikka."
    facts = extract_text_facts(FlyerRequestFields(), raw, cfg=_armed("indo-chinese"))
    names = _item_names(facts)
    assert "10 items combo" in names and names["10 items combo"].source == "customer_text"
    assert "paneer tikka" in names and names["paneer tikka"].source == "customer_text"


def test_planner_items_get_customer_flat_price(monkeypatch):
    """Flat price applies to inferred items; NAME stays hermes_inferred, PRICE is
    customer_text (the customer's stated fact)."""
    monkeypatch.setattr(cp, "build_creative_planner_provider", _provider("Veg Manchurian", "Hakka Noodles"))
    raw = "Flyer for Dragon Bowl, include 6 famous indo-chinese items, any item at $8.99"
    facts = extract_text_facts(FlyerRequestFields(event_or_business_name="Dragon Bowl"), raw, cfg=_armed("indo-chinese"))
    by_id = {f.fact_id: f for f in facts}
    inferred = [f for f in facts if f.fact_id.endswith(":name") and f.source == "hermes_inferred"]
    assert {f.value for f in inferred} == {"Veg Manchurian", "Hakka Noodles"}
    for name_fact in inferred:
        idx = name_fact.fact_id.split(":")[1]
        price = by_id.get(f"item:{idx}:price")
        assert price is not None and price.value == "$8.99" and price.source == "customer_text"


def test_any_item_flat_price_does_not_create_junk_menu_item(monkeypatch):
    """A flat-price clause ("Any item at $14.99") is pricing, not an item named
    "At Biryani"; inferred menu names keep their own indices and receive the price."""
    monkeypatch.setattr(
        cp, "build_creative_planner_provider",
        _provider("Chicken Biryani", "Mutton Biryani", "Veg Biryani", "Egg Biryani"),
    )
    raw = "Biryani specials flyer. Include 7 popular Indian biryani items. Any item at $14.99."
    facts = extract_text_facts(FlyerRequestFields(), raw, cfg=_armed("biryani"))
    names = _item_names(facts)
    assert "at biryani" not in names
    assert {f.value for f in names.values()} == {
        "Chicken Biryani", "Mutton Biryani", "Veg Biryani", "Egg Biryani"
    }
    by_id = {f.fact_id: f for f in facts}
    for name_fact in names.values():
        price = by_id.get(f"item:{name_fact.fact_id.split(':')[1]}:price")
        assert price is not None and price.value == "$14.99" and price.source == "customer_text"


def test_category_item_flat_price_applies_to_inferred_items(monkeypatch):
    """A category-scoped flat price ("Every dosa item at $10.99") applies to each
    planner-inferred item without requiring the generic word "item" alone."""
    monkeypatch.setattr(
        cp, "build_creative_planner_provider",
        _provider("Masala Dosa", "Plain Dosa", "Rava Dosa", "Mysore Dosa", "Onion Dosa", "Podi Dosa"),
    )
    raw = "Dosa Night flyer. Include 6 dosa varieties. Every dosa item at $10.99."
    facts = extract_text_facts(FlyerRequestFields(), raw, cfg=_armed("dosa"))
    inferred = [f for f in facts if f.fact_id.endswith(":name") and f.source == "hermes_inferred"]
    assert len(inferred) == 6
    by_id = {f.fact_id: f for f in facts}
    for name_fact in inferred:
        price = by_id.get(f"item:{name_fact.fact_id.split(':')[1]}:price")
        assert price is not None and price.value == "$10.99" and price.source == "customer_text"


def test_pure_vague_inferred_start_at_zero(monkeypatch):
    """No grounded items ⇒ inferred occupy item:0+ (offset base 0)."""
    monkeypatch.setattr(cp, "build_creative_planner_provider", _provider("Veg Manchurian", "Hakka Noodles"))
    raw = "Flyer, include 6 famous indo-chinese items"
    facts = extract_text_facts(FlyerRequestFields(), raw, cfg=_armed("indo-chinese"))
    inferred_idx = sorted(int(f.fact_id.split(":")[1]) for f in facts
                          if f.fact_id.endswith(":name") and f.source == "hermes_inferred")
    assert inferred_idx == [0, 1]


def test_mixed_named_plus_filled_commits_to_total(monkeypatch):
    """Mixed: customer names 2 items and asks for 8 total; the planner fills the
    remainder (6), so the project commits to exactly 8 distinct items — the 2 named are
    customer_text (unchanged), the 6 filled are hermes_inferred."""
    monkeypatch.setattr(
        cp, "build_creative_planner_provider",
        _provider(*[f"Inferred {i}" for i in range(10)]),  # planner offers more than needed
    )
    raw = ("Indo-chinese flyer with 8 items total. Samosa $5.00 and Veg Roll $6.00. "
           "Add more suggestions.")
    facts = extract_text_facts(FlyerRequestFields(), raw, cfg=_armed("indo-chinese"))
    names = _item_names(facts)
    customer = {k for k, f in names.items() if f.source == "customer_text"}
    inferred = {k for k, f in names.items() if f.source == "hermes_inferred"}
    assert {"samosa", "veg roll"} <= customer
    assert len(names) == 8  # exactly N total
    assert len(inferred) == 8 - len(customer)  # planner filled the remainder


def test_dormant_byte_identical(monkeypatch):
    """Planner off ⇒ extract_text_facts output is byte-identical to no-cfg (no junk-drop,
    no offset, no flat-price reconciliation runs)."""
    raw = "Flyer for Lakshmis Kitchen, include 6 famous indo-chinese items, any item at $8.99"
    fields = FlyerRequestFields(event_or_business_name="Lakshmis Kitchen")

    def norm(facts):
        return sorted((f.fact_id, f.value, f.source) for f in facts)

    base = extract_text_facts(fields, raw)  # cfg=None
    flag_off = extract_text_facts(fields, raw, cfg=FlyerConfig())  # flag default-off
    assert norm(base) == norm(flag_off)
    assert not any(f.source == "hermes_inferred" for f in flag_off)
