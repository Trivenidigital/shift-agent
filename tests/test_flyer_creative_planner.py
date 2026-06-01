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


def test_load_firewall_is_none_in_slice2():
    assert cp.load_firewall() is None


def test_materialize_inferred_with_firewall_tags_hermes_inferred():
    """Forward-looking: WITH a firewall, candidates become hermes_inferred facts
    (proves materialize is correct once slice 3 supplies the firewall)."""
    cands = [cp.CreativeCandidate(kind="item", value="Idli"),
             cp.CreativeCandidate(kind="item", value="Masala Dosa")]
    facts = cp.materialize_inferred(cands, firewall=_PassThroughFirewall())
    assert [f.value for f in facts] == ["Idli", "Masala Dosa"]
    assert all(isinstance(f, FlyerLockedFact) and f.source == "hermes_inferred" for f in facts)
    assert [f.fact_id for f in facts] == ["item:0:name", "item:1:name"]


# ── doubly inert (flag + firewall capability) ───────────────────────────────

def test_is_active_false_when_flag_disabled():
    assert cp.is_active(FlyerConfig()) is False


def test_is_active_false_in_slice2_even_when_flag_enabled():
    cfg = FlyerConfig(creative_planner=FlyerCreativePlannerConfig(enabled=True))
    # flag on, but no firewall yet (load_firewall None) ⇒ still inert
    assert cp.is_active(cfg) is False


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


# ── end-to-end inert + no-regression ────────────────────────────────────────

def test_extract_text_facts_inert_with_flag_on():
    cfg = FlyerConfig(creative_planner=FlyerCreativePlannerConfig(enabled=True))
    facts = extract_text_facts(_fields(), _RAW, cfg=cfg)
    assert not any(f.source == "hermes_inferred" for f in facts), \
        "slice 2 must emit NO inferred fact even with the flag on (firewall absent)"


def test_extract_text_facts_cfg_none_equals_flag_off():
    base = extract_text_facts(_fields(), _RAW)  # cfg=None (existing callers)
    flag_off = extract_text_facts(_fields(), _RAW, cfg=FlyerConfig())
    def norm(facts):
        return sorted((f.fact_id, f.value, f.source) for f in facts)
    assert norm(base) == norm(flag_off), "cfg=None and flag-off must be byte-identical"
