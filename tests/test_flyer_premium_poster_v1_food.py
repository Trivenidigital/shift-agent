"""Premium Poster Template v1 — Slice C1: composer consumes a real textless food
image while preserving readability + fact-safety + fallback + the textless gate.

Deterministic / offline (NO model call). PIL-dependent -> test_flyer_* (runs
locally + deploy smoke; send-path-ci has no PIL and excludes test_flyer*).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")

from agents.flyer.premium_poster_v1 import (  # noqa: E402
    READABILITY_FLOOR_PX,
    compose_premium_poster_v1,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "premium_poster_v1" / "textless_food_scene.png"
FOOD_GOLDEN = REPO / "tests" / "fixtures" / "premium_poster_v1" / "snack_weekend_food_golden.png"


def _fact(fact_id, value):
    return SimpleNamespace(fact_id=fact_id, value=value)


def _snack_fixture():
    return [
        _fact("business_name", "Lakshmi's Kitchen"),
        _fact("campaign_title", "Weekend Snack Specials"),
        _fact("pricing_structure", "Any 2 snacks $9.99"),
        _fact("item:0:name", "Punugulu"), _fact("item:1:name", "Egg Bonda"),
        _fact("item:2:name", "Aloo Bonda"), _fact("item:3:name", "Veg Lollipop"),
        _fact("item:4:name", "Cut Mirchi"), _fact("item:5:name", "Onion Pakora"),
        _fact("item:6:name", "Punjabi Samosa"),
        _fact("schedule", "Saturday & Sunday"),
        _fact("location", "90 Brybar Dr St Johns FL"),
        _fact("contact_phone", "+17329837841"),
    ]


def _items(facts):
    return [f.value for f in facts if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")]


# ── the food image is consumed (cover-fit) ──────────────────────────────────

def test_food_image_accepted_and_cover_fit():
    assert FIXTURE.exists()
    img, r = compose_premium_poster_v1(_snack_fixture(), food_image_path=str(FIXTURE))
    assert r["eligible"] is True
    assert r["background"] == "food" and r["food_fallback_reason"] == ""
    assert img is not None and img.size == (1080, 1350)  # cover-fit to portrait


# ── readability preserved OVER the busy real image (scrims + opaque panel) ───

def test_busy_image_preserves_hierarchy_and_readability():
    _, r = compose_premium_poster_v1(_snack_fixture(), food_image_path=str(FIXTURE))
    f = r["fonts"]
    assert f["headline"] > f["menu"] > f["footer"]          # headline dominates over the food
    assert r["item_px"] >= READABILITY_FLOOR_PX             # item list never below the floor
    assert "offer" in r["regions"] and f["offer_price"] >= READABILITY_FLOOR_PX


# ── fact-safety unchanged WITH an image: all text from facts, none from image ─

def test_all_text_from_facts_with_image_present():
    facts = _snack_fixture()
    _, r = compose_premium_poster_v1(facts, food_image_path=str(FIXTURE))
    assert set(r["items"]) == set(_items(facts))            # all items, from facts
    assert set(r["items"]).issubset(set(_items(facts)))     # never invents an item
    allowed = " ".join(x.value for x in facts).casefold()
    for placed in r["placed_text"]:
        for tok in placed.casefold().split():
            if any(ch.isalnum() for ch in tok):
                assert tok in allowed, f"ungrounded token {tok!r}"  # nothing from the image


# ── textless-safety gate (text-bearing / unverifiable -> fallback) ──────────

def test_text_bearing_image_falls_back():
    # textless_check returns False (text detected) -> deterministic fallback, image ignored
    _, r = compose_premium_poster_v1(
        _snack_fixture(), food_image_path=str(FIXTURE), textless_check=lambda im: False)
    assert r["background"] == "fallback" and r["food_fallback_reason"] == "image_has_text"


def test_textless_check_raising_is_fail_safe():
    def _boom(_im):
        raise RuntimeError("OCR exploded")
    _, r = compose_premium_poster_v1(
        _snack_fixture(), food_image_path=str(FIXTURE), textless_check=_boom)
    assert r["background"] == "fallback"  # cannot verify textless -> do not trust the image


def test_textless_check_pass_uses_image():
    _, r = compose_premium_poster_v1(
        _snack_fixture(), food_image_path=str(FIXTURE), textless_check=lambda im: True)
    assert r["background"] == "food"


# ── fallback paths ──────────────────────────────────────────────────────────

def test_missing_image_falls_back_to_warm_background():
    _, r = compose_premium_poster_v1(_snack_fixture())  # no food image
    assert r["background"] == "fallback" and r["food_fallback_reason"] == "no_image"


def test_corrupt_image_falls_back(tmp_path):
    bad = tmp_path / "not_an_image.txt"
    bad.write_text("definitely not a png", encoding="utf-8")
    _, r = compose_premium_poster_v1(_snack_fixture(), food_image_path=str(bad))
    assert r["background"] == "fallback" and r["food_fallback_reason"] == "image_load_failed"


# ── no routing / flag-off unchanged + golden committed ──────────────────────

def test_no_routing_render_py_still_clean():
    render = (REPO / "src" / "agents" / "flyer" / "render.py").read_text(encoding="utf-8")
    assert "premium_poster_v1" not in render and "compose_premium_poster_v1" not in render


def test_food_golden_committed():
    assert FOOD_GOLDEN.exists() and FOOD_GOLDEN.stat().st_size > 0
