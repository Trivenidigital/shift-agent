"""Premium Poster Template v1 — Slice C3: shadow visual critique / quality scoring
(LOG-ONLY) + the negative-space prompt revision.

The critique scorer is INJECTED so these tests are deterministic + network-free
(the real oracle wraps OpenRouter vision and imports visual_qa->safe_io->fcntl,
none of which run here). The box shadow run injects an oracle-backed scorer.

Boundary: Hermes = the eyes (the vision critique); Python = run it, record it,
NEVER gate on it (yet), never raise. PIL-dependent -> test_flyer_*.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")

from agents.flyer.campaign_scene_prompts import select_food_poster_scene  # noqa: E402
from agents.flyer.premium_poster_v1_director import (  # noqa: E402
    brief_summary,
    build_textless_food_prompt,
    compose_premium_poster_with_generated_background,
    critique_composed_poster,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "premium_poster_v1" / "textless_food_scene.png"


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


def _gen(_p):
    return str(FIXTURE)


def _ocr_ok(_im):
    return True


def _good_scorer(_path, _brief=""):
    # the oracle's score_to_dict shape
    return {
        "axes": {
            "message_clarity": {"score": 8, "critique": "clear"},
            "appetite_appeal": {"score": 9, "critique": "appetizing"},
            "would_i_post": {"score": 7, "critique": "yes"},
        },
        "composite": 8.0,
        "overall_critique": "solid premium poster",
    }


# ── prompt revision: negative-space overlay-safe zones (still forbids text) ───

def test_prompt_requests_negative_space_safe_zones():
    p = build_textless_food_prompt(_snack(), select_food_poster_scene("snack"))
    low = p.lower()
    assert "negative space" in low
    assert "upper third" in low and "lower band" in low
    assert ("centre" in low or "center" in low or "mid-background" in low)
    # the revision must NOT weaken the no-text contract
    assert "no text" in low and "no prices" in low and "no logos" in low


# ── critique wrapper: log-only, never raises ────────────────────────────────

def test_critique_with_scorer_returns_scores(tmp_path):
    from PIL import Image
    img = Image.new("RGB", (1080, 1350), (30, 20, 15))
    c = critique_composed_poster(
        img, brief_summary="snack", scorer=_good_scorer, image_save_path=str(tmp_path / "p.png"))
    assert c["available"] is True and c["status"] == "ok"
    assert c["composite"] == pytest.approx(8.0)
    assert c["axes"]["appetite_appeal"]["score"] == 9


def test_critique_unavailable_when_scorer_returns_none(tmp_path):
    from PIL import Image
    img = Image.new("RGB", (1080, 1350), (0, 0, 0))
    c = critique_composed_poster(img, scorer=lambda *a: None, image_save_path=str(tmp_path / "p.png"))
    assert c["available"] is False and c["status"] == "critique_unavailable"
    assert c["axes"] == {}


def test_critique_malformed_result_is_safe(tmp_path):
    from PIL import Image
    img = Image.new("RGB", (1080, 1350), (0, 0, 0))
    c = critique_composed_poster(img, scorer=lambda *a: "not a dict", image_save_path=str(tmp_path / "p.png"))
    assert c["available"] is False and c["status"] == "critique_error"


def test_critique_scorer_raising_is_safe(tmp_path):
    def _boom(*_a):
        raise RuntimeError("vision backend down")
    from PIL import Image
    img = Image.new("RGB", (1080, 1350), (0, 0, 0))
    c = critique_composed_poster(img, scorer=_boom, image_save_path=str(tmp_path / "p.png"))
    assert c["available"] is False and c["status"] == "critique_error"


# ── orchestrator: critique persisted, LOG-ONLY (never gates) ────────────────

def test_compose_persists_critique_when_requested():
    img, report = compose_premium_poster_with_generated_background(
        _snack(), generator=_gen, textless_ocr=_ocr_ok, run_critique=True, critique_scorer=_good_scorer)
    assert "critique" in report and report["critique"]["available"] is True
    assert report["critique"]["composite"] == pytest.approx(8.0)
    assert report["background"] == "food" and img is not None


def test_low_score_recorded_but_does_not_block():
    low = lambda *a: {"axes": {"appetite_appeal": {"score": 1, "critique": "ugly"}},
                      "composite": 1.0, "overall_critique": "weak"}
    img, report = compose_premium_poster_with_generated_background(
        _snack(), generator=_gen, textless_ocr=_ocr_ok, run_critique=True, critique_scorer=low)
    assert report["critique"]["composite"] == pytest.approx(1.0)
    # a LOW score is RECORDED but does NOT block — the poster is still composed + returned
    assert img is not None and report["background"] == "food"


def test_critique_off_by_default_is_c2a_behavior():
    img, report = compose_premium_poster_with_generated_background(
        _snack(), generator=_gen, textless_ocr=_ocr_ok)
    assert "critique" not in report and img is not None


def test_ocr_gate_precedes_critique_text_bearing_falls_back():
    img, report = compose_premium_poster_with_generated_background(
        _snack(), generator=_gen, textless_ocr=lambda im: False,
        run_critique=True, critique_scorer=_good_scorer)
    # the OCR gate runs FIRST — generated text is dropped before critique
    assert report["director"]["background_status"] == "image_has_text"
    assert report["background"] == "fallback"
    # critique still runs (on the safe deterministic fallback poster)
    assert "critique" in report and img is not None


def test_facts_only_preserved_with_critique():
    facts = _snack()
    _, report = compose_premium_poster_with_generated_background(
        facts, generator=_gen, textless_ocr=_ocr_ok, run_critique=True, critique_scorer=_good_scorer)
    assert set(report["items"]) == set(_items(facts))
    allowed = " ".join(x.value for x in facts).casefold()
    for placed in report["placed_text"]:
        for tok in placed.casefold().split():
            if any(ch.isalnum() for ch in tok):
                assert tok in allowed, f"ungrounded token {tok!r}"


# ── brief_summary excludes sensitive copy (goes to the vision model) ────────

def test_brief_summary_excludes_sensitive_copy():
    s = brief_summary(_snack())
    assert "$9.99" not in s and "Any 2 snacks" not in s
    assert "+17329837841" not in s and "90 Brybar" not in s
    assert "Weekend Snack Specials" in s  # campaign title is safe context


# ── no routing ──────────────────────────────────────────────────────────────

def test_no_routing_render_py_does_not_reference_critique():
    render = (REPO / "src" / "agents" / "flyer" / "render.py").read_text(encoding="utf-8")
    assert "critique_composed_poster" not in render
