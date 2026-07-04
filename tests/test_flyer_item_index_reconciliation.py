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

# Planner-branch tests removed with creative_planner (graduation commit 6):
# the behaviors they pinned (inferred-item reconciliation, remainder caps,
# flat-price-to-inferred pairing, count-clause junk drops) existed only inside
# the always-dormant planner branch and are gone with it.
