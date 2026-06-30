"""Premium Poster Template v1 — Slice A (deterministic composer foundation).

Structural contract tests over the composer's `layout_report` (fact-safety,
readability floors, hierarchy, eligibility, fallbacks) + flag-off / no-routing
proofs. PIL-dependent (flyer render), so named test_flyer_* — runs locally + on
the deploy smoke (send-path-ci has no PIL and excludes test_flyer*).
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")  # PIL absent in minimal CI -> skip (runs locally / VPS smoke)

from agents.flyer.premium_poster_v1 import (  # noqa: E402
    READABILITY_FLOOR_PX,
    compose_premium_poster_v1,
    poster_v1_enabled,
)

REPO = Path(__file__).resolve().parent.parent


def _fact(fact_id: str, value: str):
    return SimpleNamespace(fact_id=fact_id, value=value)


def _snack_fixture():
    # Street-snack / weekend-special class (the reference direction); 7 items.
    return [
        _fact("business_name", "Lakshmi's Kitchen"),
        _fact("campaign_title", "Weekend Snack Specials"),
        _fact("pricing_structure", "Any 2 snacks $9.99"),
        _fact("item:0:name", "Punugulu"),
        _fact("item:1:name", "Egg Bonda"),
        _fact("item:2:name", "Aloo Bonda"),
        _fact("item:3:name", "Veg Lollipop"),
        _fact("item:4:name", "Cut Mirchi"),
        _fact("item:5:name", "Onion Pakora"),
        _fact("item:6:name", "Punjabi Samosa"),
        _fact("schedule", "Saturday & Sunday"),
        _fact("location", "90 Brybar Dr St Johns FL"),
        _fact("contact_phone", "+17329837841"),
    ]


def _items(facts):
    return [f.value for f in facts if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")]


# ── eligibility + output ────────────────────────────────────────────────────

def test_composer_produces_output_for_snack_fixture():
    img, report = compose_premium_poster_v1(_snack_fixture())
    assert report["eligible"] is True
    assert img is not None
    assert img.size == (1080, 1350)


def test_ineligible_when_business_name_missing():
    facts = [f for f in _snack_fixture() if f.fact_id != "business_name"]
    img, report = compose_premium_poster_v1(facts)
    assert report["eligible"] is False
    assert img is None  # caller falls back to the existing path


def test_ineligible_when_too_few_items():
    facts = [f for f in _snack_fixture() if not f.fact_id.startswith("item:")]
    facts += [_fact("item:0:name", "Punugulu"), _fact("item:1:name", "Egg Bonda")]  # only 2
    img, report = compose_premium_poster_v1(facts)
    assert report["eligible"] is False and img is None


def test_ineligible_when_no_offer_or_price():
    facts = [f for f in _snack_fixture() if f.fact_id != "pricing_structure"]
    img, report = compose_premium_poster_v1(facts)
    assert report["eligible"] is False and img is None


# ── hierarchy + readability (the F0190 fixes) ───────────────────────────────

def test_headline_dominates_items_and_footer():
    _, r = compose_premium_poster_v1(_snack_fixture())
    f = r["fonts"]
    assert f["headline"] > f["menu"] > f["footer"]


def test_item_list_meets_readability_floor():
    _, r = compose_premium_poster_v1(_snack_fixture())
    assert r["item_px"] >= READABILITY_FLOOR_PX
    assert r["fonts"]["menu"] >= READABILITY_FLOOR_PX


def test_offer_badge_region_present_and_readable():
    _, r = compose_premium_poster_v1(_snack_fixture())
    assert "offer" in r["regions"]
    assert r["offer_price"]  # a price/offer string is placed
    assert r["fonts"]["offer_price"] >= READABILITY_FLOOR_PX


def test_required_poster_regions_present():
    _, r = compose_premium_poster_v1(_snack_fixture())
    for region in ("brand", "headline", "offer", "items", "footer"):
        assert region in r["regions"], region


# ── fact-safety (no weakening; never fabricate) ─────────────────────────────

def test_all_fixture_items_present():
    facts = _snack_fixture()
    _, r = compose_premium_poster_v1(facts)
    assert set(r["items"]) == set(_items(facts))


def test_no_fabricated_item_or_offer():
    facts = _snack_fixture()
    _, r = compose_premium_poster_v1(facts)
    fixture_items = set(_items(facts))
    assert set(r["items"]).issubset(fixture_items)  # never invents an item
    # every SEMANTIC token placed (price/word/number — ignoring cosmetic
    # separators like '·') is grounded in a locked fact value (fact-safe).
    allowed = " ".join(f.value for f in facts).casefold()
    for placed in r["placed_text"]:
        for tok in placed.casefold().split():
            if not any(ch.isalnum() for ch in tok):
                continue  # pure punctuation / separator — not a claim
            assert tok in allowed, f"ungrounded token {tok!r} in {placed!r}"


def test_price_only_offer_never_fabricates_a_label():
    # offer is a bare price with no label words -> the badge must NOT invent a
    # label like "SPECIAL". placed_text mirrors the canvas, so it must be absent.
    facts = [f for f in _snack_fixture() if f.fact_id != "pricing_structure"]
    facts.append(_fact("pricing_structure", "$9.99"))
    _, r = compose_premium_poster_v1(facts)
    assert r["eligible"] is True
    assert r["offer_price"] == "$9.99" and r["offer_label"] == ""
    # the fabricated default "SPECIAL" badge label must never be drawn. placed_text
    # mirrors the canvas, so an exact-token check catches it (a legit grounded
    # headline like "Snack Specials" is a different token and is fine).
    assert all(p.strip().upper() != "SPECIAL" for p in r["placed_text"])


def test_too_many_items_never_shrink_below_floor():
    facts = [f for f in _snack_fixture() if not f.fact_id.startswith("item:")]
    facts += [_fact(f"item:{i}:name", f"Snack Item {i}") for i in range(24)]  # stress: 24 items
    _, r = compose_premium_poster_v1(facts)
    assert r["item_px"] >= READABILITY_FLOOR_PX           # never below the floor
    placed = set(r["items"])
    assert placed.issubset({f.value for f in facts if f.fact_id.startswith("item:")})  # no fabrication
    # if not all fit at the floor, the report says so honestly (no silent drop pretending complete)
    if len(placed) < 24:
        assert r["items_overflow"] is True


# ── flag + no routing (flag-off byte identity by construction) ──────────────

def test_flag_defaults_off():
    monkey = os.environ.pop("FLYER_PREMIUM_POSTER_V1", None)
    try:
        assert poster_v1_enabled() is False
    finally:
        if monkey is not None:
            os.environ["FLYER_PREMIUM_POSTER_V1"] = monkey


def test_premium_poster_v1_dormant_by_default_in_render():
    # The integration slice wires Premium Poster v1 into render.py, but it is DORMANT
    # by default: with FLYER_PREMIUM_POSTER_V1 unset, _premium_poster_v1_armed returns
    # False, so the render branch is never entered (byte-identical legacy).
    import os
    from types import SimpleNamespace
    from agents.flyer import render as render_mod
    os.environ.pop("FLYER_PREMIUM_POSTER_V1", None)
    assert render_mod._premium_poster_v1_armed(SimpleNamespace(customer_phone="+17329837841")) is False


def test_golden_artifact_committed():
    golden = REPO / "tests" / "fixtures" / "premium_poster_v1" / "snack_weekend_golden.png"
    assert golden.exists() and golden.stat().st_size > 0
