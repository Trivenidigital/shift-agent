"""Premium Poster Template v1 — Slice C2A: Hermes-directed textless food background
(OFFLINE wiring). The image generator + OCR gate are INJECTED so the whole
direct -> generate -> textless-gate -> compose flow runs deterministically with
NO model call. The real generator (the box's force_background_only path) + real
OCR (visual_qa) are wired at the call site in the C2B VPS shadow run.

Boundary (recorded on PR #517): Hermes = art direction (scene families + the
injected art_director/generator); Python = the safe prompt assembly, the no-text
contract, the OCR gate, fallback, and the deterministic composition. NO model
text is ever trusted: any gate failure -> deterministic fallback.

PIL-dependent (loads images + composes) -> test_flyer_* (runs locally + on the
deploy smoke; send-path-ci has no PIL and excludes test_flyer*).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")

from agents.flyer.campaign_scene_prompts import (  # noqa: E402
    select_campaign_scene,
    select_food_poster_scene,
)
from agents.flyer.premium_poster_v1_director import (  # noqa: E402
    TEXTLESS_CONTRACT,
    build_textless_food_prompt,
    compose_premium_poster_with_generated_background,
    generate_textless_food_background,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "premium_poster_v1" / "textless_food_scene.png"


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


# fixed injected stubs (the box wires the real image model + visual_qa OCR here)
def _gen_returns_fixture(_prompt):
    return str(FIXTURE)


def _ocr_textless(_img):
    return True


# ── scene family selection (deterministic art-direction routing) ─────────────

def test_scene_family_selection_per_occasion():
    assert select_food_poster_scene("weekend snack specials bonda pakora").key == "food_street_snack"
    assert select_food_poster_scene("family combo meal thali deal").key == "food_combo"
    assert select_food_poster_scene("diwali mithai gulab jamun sweets").key == "food_dessert"  # dessert > festival
    assert select_food_poster_scene("navratri festival celebration").key == "food_festival"
    assert select_food_poster_scene("grand opening of our new location").key == "food_grand_opening"
    assert select_food_poster_scene("our regular lunch").key == "food_generic"  # safe default


def test_snack_fixture_selects_street_snack():
    # the real fixture (campaign_title has "snack", items are bondas/pakora/samosa)
    facts = _snack_fixture()
    ctx = " ".join(f.value for f in facts)
    assert select_food_poster_scene(ctx).key == "food_street_snack"


def test_existing_campaign_scene_selector_unchanged():
    # the additive food families MUST NOT change the deployed selector's behaviour
    assert select_campaign_scene("diwali family celebration").key == "family_discovery"
    assert select_campaign_scene("grand opening sale today only").key == "human_billboard"
    assert select_campaign_scene("our storefront").key == "storefront_service"


# ── prompt: forbids ALL text; facts guide food only ─────────────────────────

def test_prompt_forbids_text_logos_numbers_prices():
    prompt = build_textless_food_prompt(_snack_fixture(), select_food_poster_scene("snack"))
    low = prompt.lower()
    for banned in ("no text", "no words", "no letters", "no numbers", "no logos",
                   "no prices", "no menus", "no readable signs", "no watermarks",
                   "packaging text"):
        assert banned in low, f"prompt missing forbid clause: {banned!r}"
    assert TEXTLESS_CONTRACT in prompt


def test_prompt_uses_items_as_food_style_not_copy():
    facts = _snack_fixture()
    prompt = build_textless_food_prompt(facts, select_food_poster_scene("snack"))
    # item names guide the FOOD rendered (Hermes direction)
    assert "Punugulu" in prompt and "Onion Pakora" in prompt
    # but sensitive copy (price, phone, address) is NEVER injected into the image prompt
    assert "$9.99" not in prompt and "Any 2 snacks" not in prompt
    assert "+17329837841" not in prompt
    assert "90 Brybar Dr" not in prompt


def test_art_director_seam_overrides_direction_but_not_contract():
    # Hermes (injected, on the box) may supply enriched direction; Python ALWAYS
    # keeps the no-text contract + the safe assembly around it.
    def _director(_facts, _scene):
        return "a cinematic overhead spread of glistening fried snacks"
    prompt = build_textless_food_prompt(
        _snack_fixture(), select_food_poster_scene("snack"), art_director=_director)
    assert "cinematic overhead spread" in prompt
    assert TEXTLESS_CONTRACT in prompt  # contract survives Hermes direction


# ── orchestrator: validated image accepted, all failures fall back ──────────

def test_injected_generated_image_accepted_and_composed():
    img, report = compose_premium_poster_with_generated_background(
        _snack_fixture(), generator=_gen_returns_fixture, textless_ocr=_ocr_textless)
    assert report["director"]["background_status"] == "ok"
    assert report["director"]["scene_key"] == "food_street_snack"
    assert report["background"] == "food"           # the validated image is used
    assert img is not None and img.size == (1080, 1350)


def test_text_bearing_generated_image_rejected_not_shipped():
    # OCR says the model rendered text -> NEVER ship it; deterministic fallback
    img, report = compose_premium_poster_with_generated_background(
        _snack_fixture(), generator=_gen_returns_fixture, textless_ocr=lambda im: False)
    assert report["director"]["background_status"] == "image_has_text"
    assert report["director"]["food_image_path"] is None   # the model image is dropped
    assert report["background"] == "fallback"               # poster still ships (deterministic)
    assert img is not None


def test_ocr_check_error_is_distinct_and_falls_back():
    def _boom(_img):
        raise RuntimeError("OCR backend down")
    img, report = compose_premium_poster_with_generated_background(
        _snack_fixture(), generator=_gen_returns_fixture, textless_ocr=_boom)
    # a check OUTAGE must be distinguishable from genuine text-detection
    assert report["director"]["background_status"] == "check_error"
    assert report["background"] == "fallback" and img is not None


def test_corrupt_generated_image_falls_back(tmp_path):
    bad = tmp_path / "broken.png"
    bad.write_text("not a real image", encoding="utf-8")
    img, report = compose_premium_poster_with_generated_background(
        _snack_fixture(), generator=lambda p: str(bad), textless_ocr=_ocr_textless)
    assert report["director"]["background_status"] == "image_load_failed"
    assert report["background"] == "fallback" and img is not None


def test_generation_failed_falls_back():
    img, report = compose_premium_poster_with_generated_background(
        _snack_fixture(), generator=lambda p: None, textless_ocr=_ocr_textless)
    assert report["director"]["background_status"] == "generation_failed"
    assert report["background"] == "fallback" and img is not None


def test_generator_raising_falls_back():
    def _explode(_prompt):
        raise RuntimeError("image model 500")
    img, report = compose_premium_poster_with_generated_background(
        _snack_fixture(), generator=_explode, textless_ocr=_ocr_textless)
    assert report["director"]["background_status"] == "generation_failed"
    assert report["background"] == "fallback" and img is not None


# ── fact-safety preserved with a generated background ───────────────────────

def test_all_text_from_facts_with_generated_background():
    facts = _snack_fixture()
    _, report = compose_premium_poster_with_generated_background(
        facts, generator=_gen_returns_fixture, textless_ocr=_ocr_textless)
    assert set(report["items"]) == set(_items(facts))
    allowed = " ".join(x.value for x in facts).casefold()
    for placed in report["placed_text"]:
        for tok in placed.casefold().split():
            if any(ch.isalnum() for ch in tok):
                assert tok in allowed, f"ungrounded token {tok!r}"


# ── no routing / no model-text trust ────────────────────────────────────────

def test_premium_poster_v1_dormant_by_default_in_render(monkeypatch):
    # The integration wires compose_best_of_n into render.py, but it is dormant by
    # default (flag off -> not armed). The SHADOW-only orchestrator
    # compose_premium_poster_with_generated_background is NOT wired into the live path.
    from types import SimpleNamespace
    from agents.flyer import render as render_mod
    render_src = (REPO / "src" / "agents" / "flyer" / "render.py").read_text(encoding="utf-8")
    assert "compose_premium_poster_with_generated_background" not in render_src
    monkeypatch.delenv("FLYER_PREMIUM_POSTER_V1", raising=False)
    assert render_mod._premium_poster_v1_armed(SimpleNamespace(customer_phone="+17329837841")) is False


def test_unit_result_never_returns_unvalidated_path():
    # the orchestrator (without compose) only ever returns a path on status "ok"
    for ocr, expect in ((lambda im: False, None), (_ocr_textless, str(FIXTURE))):
        res = generate_textless_food_background(
            _snack_fixture(), generator=_gen_returns_fixture, textless_ocr=ocr)
        assert res.food_image_path == expect
