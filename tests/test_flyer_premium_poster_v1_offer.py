"""Premium Poster Template v1 — offer-prominence micro-slice.

The C3 critique consistently flagged offer_energy as the weak axis. This slice
makes the offer badge larger + more dominant + better-separated DETERMINISTICALLY
(no model text; all offer text stays locked-fact-only). PIL-dependent -> test_flyer_*.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")

from agents.flyer.premium_poster_v1 import (  # noqa: E402
    READABILITY_FLOOR_PX,
    compose_premium_poster_v1,
    poster_v1_enabled,
)

REPO = Path(__file__).resolve().parent.parent

# the badge values BEFORE this slice (the "current golden" baseline)
PREV_RADIUS_FRAC = 0.16
PREV_PRICE_FRAC = 0.085


def _fact(i, v):
    return SimpleNamespace(fact_id=i, value=v)


def _snack():
    return [
        _fact("business_name", "Lakshmi's Kitchen"), _fact("campaign_title", "Weekend Snack Specials"),
        _fact("pricing_structure", "Any 2 snacks $9.99"),
        _fact("item:0:name", "Punugulu"), _fact("item:1:name", "Egg Bonda"), _fact("item:2:name", "Aloo Bonda"),
        _fact("item:3:name", "Veg Lollipop"), _fact("item:4:name", "Cut Mirchi"), _fact("item:5:name", "Onion Pakora"),
        _fact("item:6:name", "Punjabi Samosa"),
        _fact("schedule", "Saturday & Sunday"), _fact("location", "90 Brybar Dr St Johns FL"),
        _fact("contact_phone", "+17329837841"),
    ]


def _items(facts):
    return [f.value for f in facts if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")]


# ── the badge is LARGER / more dominant than the previous golden ────────────

def test_offer_badge_is_larger_than_previous_golden():
    _, r = compose_premium_poster_v1(_snack())
    w = r["size"][0]
    assert r["offer_badge_radius"] > int(w * PREV_RADIUS_FRAC)   # bigger circle
    assert r["fonts"]["offer_price"] > int(w * PREV_PRICE_FRAC)  # bigger price


# ── hierarchy stays correct: headline dominant, offer more prominent ────────

def test_hierarchy_headline_dominant_offer_prominent():
    _, r = compose_premium_poster_v1(_snack())
    f = r["fonts"]
    assert f["headline"] >= f["offer_price"]        # headline still at least as dominant
    assert f["offer_price"] > f["footer"]           # offer clearly beats the footer
    assert f["offer_price"] >= READABILITY_FLOOR_PX
    assert f["headline"] > f["menu"] > f["footer"]  # overall hierarchy intact


def test_item_list_readability_unchanged():
    _, r = compose_premium_poster_v1(_snack())
    assert r["item_px"] >= READABILITY_FLOOR_PX
    assert r["fonts"]["menu"] >= READABILITY_FLOOR_PX


# ── fact-safety: offer text exactly matches placed locked facts ─────────────

def test_offer_text_exactly_matches_locked_facts():
    facts = _snack()
    _, r = compose_premium_poster_v1(facts)
    assert r["offer_label"] == "Any 2 snacks" and r["offer_price"] == "$9.99"
    # everything the badge placed is grounded in a locked fact
    allowed = " ".join(x.value for x in facts).casefold()
    for placed in r["placed_text"]:
        for tok in placed.casefold().split():
            if any(ch.isalnum() for ch in tok):
                assert tok in allowed, f"ungrounded token {tok!r}"


def test_price_only_offer_never_fabricates_a_label():
    facts = [f for f in _snack() if f.fact_id != "pricing_structure"]
    facts.append(_fact("pricing_structure", "$9.99"))
    _, r = compose_premium_poster_v1(facts)
    assert r["offer_price"] == "$9.99" and r["offer_label"] == ""
    # a bigger badge must STILL never invent a label like "SPECIAL"
    assert all(p.strip().upper() != "SPECIAL" for p in r["placed_text"])


def test_no_fabricated_items_with_bigger_badge():
    facts = _snack()
    _, r = compose_premium_poster_v1(facts)
    assert set(r["items"]).issubset(set(_items(facts)))


# ── flag off / no routing unchanged ─────────────────────────────────────────

def test_flag_defaults_off():
    monkey = os.environ.pop("FLYER_PREMIUM_POSTER_V1", None)
    try:
        assert poster_v1_enabled() is False
    finally:
        if monkey is not None:
            os.environ["FLYER_PREMIUM_POSTER_V1"] = monkey


def test_no_routing_render_py_clean():
    render = (REPO / "src" / "agents" / "flyer" / "render.py").read_text(encoding="utf-8")
    assert "premium_poster_v1" not in render and "compose_premium_poster_v1" not in render
