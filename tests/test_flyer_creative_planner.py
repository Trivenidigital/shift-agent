"""Slice 2 — bounded creative planner (flag-gated, inert by construction).

Proves: (1) the structural interlock — candidates materialize ONLY through a
firewall, and there is no firewall in slice 2; (2) the planner is doubly inert
(flag default off + is_active False without a firewall); (3) the planner produces
hermes_inferred candidates correctly (with an injected provider); (4) end-to-end,
extract_text_facts emits NO hermes_inferred fact even with the flag on, and is
byte-identical to flag-off / cfg=None (no-regression).
"""
from __future__ import annotations

from schemas import FlyerConfig, FlyerCreativePlannerConfig, FlyerLockedFact, FlyerRequestFields

from agents.flyer import creative_planner as cp
from agents.flyer.facts import extract_text_facts


def _fields() -> FlyerRequestFields:
    return FlyerRequestFields(
        event_or_business_name="Lakshmis Kitchen",
        notes="include 8 famous South Indian breakfast items, any item $8.99",
    )


_RAW = "Flyer for Lakshmis Kitchen, 8 famous South Indian breakfast items, any item $8.99"


class _PassThroughFirewall:
    """Stand-in for the slice-3 firewall: clears every candidate unchanged."""
    def clear(self, candidates):
        return list(candidates)


# ── the structural interlock ────────────────────────────────────────────────

def test_materialize_inferred_without_firewall_is_empty():
    cands = [cp.CreativeCandidate(kind="item", value="Idli"),
             cp.CreativeCandidate(kind="item", value="Masala Dosa")]
    assert cp.materialize_inferred(cands, firewall=None) == []
    # default arg is also None
    assert cp.materialize_inferred(cands) == []


def test_load_firewall_returns_firewall_in_slice3():
    """Slice 3 supplies the firewall — the planner is now CAPABLE (still flag-gated)."""
    fw = cp.load_firewall()
    assert fw is not None and hasattr(fw, "clear")


def test_materialize_inferred_with_firewall_tags_hermes_inferred():
    """Forward-looking: WITH a firewall, candidates become hermes_inferred facts
    (proves materialize is correct once slice 3 supplies the firewall)."""
    cands = [cp.CreativeCandidate(kind="item", value="Idli"),
             cp.CreativeCandidate(kind="item", value="Masala Dosa")]
    facts = cp.materialize_inferred(cands, firewall=_PassThroughFirewall())
    assert [f.value for f in facts] == ["Idli", "Masala Dosa"]
    assert all(isinstance(f, FlyerLockedFact) and f.source == "hermes_inferred" for f in facts)
    assert [f.fact_id for f in facts] == ["item:0:name", "item:1:name"]


# ── activation gate (flag AND firewall AND a category opened) ───────────────

def test_is_active_false_when_flag_disabled():
    assert cp.is_active(FlyerConfig()) is False


def test_is_active_false_when_flag_on_but_no_category_enabled():
    """Codex r2 readiness gate: enabling the flag alone — before slice 5 opens a
    category — does NOT activate the planner (structural, not operator-discipline)."""
    cfg = FlyerConfig(creative_planner=FlyerCreativePlannerConfig(enabled=True))
    assert cp.is_active(cfg) is False  # enabled_categories empty ⇒ inert


def test_is_active_true_only_when_flag_on_and_category_enabled():
    """Fully armed = flag on + firewall present + ≥1 category opened (the slice-5
    operator action). Only then is the planner active."""
    cfg = FlyerConfig(creative_planner=FlyerCreativePlannerConfig(
        enabled=True, enabled_categories=["restaurant"]))
    assert cp.is_active(cfg) is True


# ── planner candidate production ────────────────────────────────────────────

def test_plan_creative_items_with_injected_provider_filters_and_tags():
    provider = lambda _f, _r: ["Idli", "Dosa", "", None, "  Vada  ", 123]  # noqa: E731
    cands = cp.plan_creative_items(_fields(), _RAW, provider=provider)
    assert [c.value for c in cands] == ["Idli", "Dosa", "Vada"]  # blanks/non-str dropped, trimmed
    assert all(c.kind == "item" for c in cands)


def test_plan_creative_items_caps_item_count():
    provider = lambda _f, _r: [f"Item{i}" for i in range(50)]  # noqa: E731
    cands = cp.plan_creative_items(_fields(), _RAW, provider=provider)
    assert len(cands) == cp.CREATIVE_PLANNER_MAX_ITEMS


def test_plan_creative_items_empty_without_provider(monkeypatch):
    monkeypatch.setattr(cp, "build_creative_planner_provider", lambda: None)
    assert cp.plan_creative_items(_fields(), _RAW) == []


# ── end-to-end: dormant by default; materializes (firewall-cleared) when on ──

def test_extract_text_facts_flag_off_no_inferred():
    """Default state (flag off) emits NO inferred fact — the dormancy guarantee
    that holds regardless of firewall presence."""
    facts = extract_text_facts(_fields(), _RAW, cfg=FlyerConfig())
    assert not any(f.source == "hermes_inferred" for f in facts)


def test_extract_text_facts_armed_materializes_firewall_cleared_items(monkeypatch):
    """Slice 3 end-to-end: fully ARMED (flag ON + ≥1 category opened + real
    firewall), the planner's safe item candidates materialize as hermes_inferred
    facts, while a claim smuggled as an 'item name' ('Free Delivery') is dropped
    by the firewall. (Slice 5 added a per-request category gate, so the enabled
    category must also match the request — _RAW mentions "South Indian".)"""
    monkeypatch.setattr(
        cp, "build_creative_planner_provider",
        lambda: (lambda _f, _r: ["Idli", "Free Delivery", "Masala Dosa"]),
    )
    cfg = FlyerConfig(creative_planner=FlyerCreativePlannerConfig(
        enabled=True, enabled_categories=["south indian"]))
    facts = extract_text_facts(_fields(), _RAW, cfg=cfg)
    inferred = [f.value for f in facts if f.source == "hermes_inferred"]
    assert "Idli" in inferred and "Masala Dosa" in inferred
    assert "Free Delivery" not in inferred  # firewall dropped the claim-as-item
    assert all(f.fact_id.startswith("item:") and f.fact_id.endswith(":name")
               for f in facts if f.source == "hermes_inferred")


def test_extract_text_facts_cfg_none_equals_flag_off():
    base = extract_text_facts(_fields(), _RAW)  # cfg=None (existing callers)
    flag_off = extract_text_facts(_fields(), _RAW, cfg=FlyerConfig())
    def norm(facts):
        return sorted((f.fact_id, f.value, f.source) for f in facts)
    assert norm(base) == norm(flag_off), "cfg=None and flag-off must be byte-identical"


# ── slice 4: provenance lifecycle (inferred -> confirmed on customer approval) ─

def test_promote_inferred_to_confirmed_flips_only_inferred():
    """Customer approval promotes hermes_inferred items to customer_confirmed (now
    customer-truthful FOR THIS PROJECT); the value is unchanged and every other
    source is left untouched."""
    facts = [
        FlyerLockedFact(fact_id="item:0:name", label="Item", value="Idli", source="hermes_inferred"),
        FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmis Kitchen", source="customer_profile"),
        FlyerLockedFact(fact_id="title", label="Title", value="Weekend Specials", source="customer_text"),
    ]
    by_id = {f.fact_id: f for f in cp.promote_inferred_to_confirmed(facts)}
    assert by_id["item:0:name"].source == "customer_confirmed"
    assert by_id["item:0:name"].value == "Idli"  # only provenance flips, never the value
    assert by_id["business_name"].source == "customer_profile"  # untouched
    assert by_id["title"].source == "customer_text"  # untouched


def test_promote_inferred_to_confirmed_noop_without_inferred():
    """No inferred facts (the dormant default) or empty input ⇒ identity — no
    spurious provenance flips, and customer_confirmed is NOT re-flipped."""
    facts = [
        FlyerLockedFact(fact_id="title", label="Title", value="Specials", source="customer_text"),
        FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_confirmed"),
    ]
    promoted = cp.promote_inferred_to_confirmed(facts)
    assert [(f.fact_id, f.source) for f in promoted] == [
        ("title", "customer_text"), ("item:0:name", "customer_confirmed")]
    assert cp.promote_inferred_to_confirmed([]) == []


# ── slice 5: per-request category gate + planner supersedes hardcoded path ─────

def test_request_matches_enabled_category_dormant_and_matching():
    """The per-request gate: inert when no category is enabled; a normalized
    (hyphen/space/case-insensitive) substring match when one is."""
    dormant = FlyerConfig()  # no categories ⇒ never matches
    assert cp.request_matches_enabled_category("include 6 famous indo-chinese items", dormant) is False
    armed = FlyerConfig(creative_planner=FlyerCreativePlannerConfig(
        enabled=True, enabled_categories=["indo-chinese"]))
    assert cp.request_matches_enabled_category("include 6 famous Indo Chinese items", armed) is True  # normalized
    assert cp.request_matches_enabled_category("include 6 famous south indian items", armed) is False  # not enabled
    assert cp.request_matches_enabled_category("", armed) is False


def test_planner_supersedes_hardcoded_famous_path_for_enabled_category(monkeypatch):
    """Slice 5: when the planner is armed AND the request's category is enabled, the
    planner produces hermes_inferred items and SUPERSEDES the hardcoded famous-item
    fallback (no customer_text famous items remain)."""
    monkeypatch.setattr(
        cp, "build_creative_planner_provider",
        lambda: (lambda _f, _r: ["Veg Manchurian", "Hakka Noodles", "Chili Paneer"]),
    )
    cfg = FlyerConfig(creative_planner=FlyerCreativePlannerConfig(
        enabled=True, enabled_categories=["indo-chinese"]))
    raw = "Flyer for Dragon Bowl, include 6 famous indo-chinese items"
    facts = extract_text_facts(FlyerRequestFields(event_or_business_name="Dragon Bowl"), raw, cfg=cfg)
    item_names = [(f.value, f.source) for f in facts
                  if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")]
    assert item_names, "planner should have produced items"
    assert all(src == "hermes_inferred" for _v, src in item_names)  # planner produced them
    assert any(v == "Veg Manchurian" for v, _s in item_names)
    assert not any(src == "customer_text" for _v, src in item_names)  # famous fallback superseded


def test_planner_does_not_fire_when_request_category_not_enabled(monkeypatch):
    """Slice 5: armed (flag on + a category opened + firewall) but the request's
    category is NOT the enabled one ⇒ the planner does not fire; the hardcoded
    famous path still produces its customer_text items (no supersede, no inferred)."""
    monkeypatch.setattr(
        cp, "build_creative_planner_provider",
        lambda: (lambda _f, _r: ["should not be used"]),
    )
    cfg = FlyerConfig(creative_planner=FlyerCreativePlannerConfig(
        enabled=True, enabled_categories=["south indian"]))
    raw = "Flyer, include 5 famous indo-chinese items"  # indo-chinese ∉ enabled
    facts = extract_text_facts(FlyerRequestFields(), raw, cfg=cfg)
    names = [(f.value, f.source) for f in facts
             if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")]
    assert names, "hardcoded famous path should still produce items"
    assert all(src == "customer_text" for _v, src in names)  # NOT the planner
    assert not any(f.source == "hermes_inferred" for f in facts)
    assert "should not be used" not in [v for v, _s in names]
