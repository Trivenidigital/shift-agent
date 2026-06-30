"""Premium Poster Template v1 — best-of-N (SHADOW): generate N textless candidates,
OCR-gate each, render+critique each accepted one, select the highest-critique poster.

Generator / OCR / critique scorer are INJECTED so these are deterministic +
network-free. Selection NEVER routes or gates customer output. PIL-dependent -> test_flyer_*.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")

from agents.flyer.premium_poster_v1_director import compose_best_of_n  # noqa: E402

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


def _ok(_im):
    return True


def _seq(values):
    """Stateful callable: returns values[0], values[1], … on successive calls."""
    it = iter(values)

    def fn(*_a, **_k):
        return next(it)

    return fn


def _scorer_seq(composites):
    """A scorer returning a score dict with the given composite per call."""
    it = iter(composites)

    def scorer(_path, _brief=""):
        c = next(it)
        return {"axes": {"appetite_appeal": {"score": int(round(c)), "critique": "x"}},
                "composite": c, "overall_critique": "c"}

    return scorer


# ── selects the highest-critique candidate ──────────────────────────────────

def test_best_of_n_selects_highest_composite():
    img, report, candidates = compose_best_of_n(
        _snack(), generator=_gen, textless_ocr=_ok, critique_scorer=_scorer_seq([6.0, 8.0, 7.0]), n=3)
    assert report["n"] == 3
    assert report["winner_index"] == 1 and report["winner_composite"] == pytest.approx(8.0)
    assert [c["composite"] for c in report["candidates"]] == [6.0, 8.0, 7.0]
    assert img is not None and img.size == (1080, 1350)


# ── rejects text-bearing candidates (OCR gate), selects best of the rest ────

def test_best_of_n_rejects_text_bearing_and_selects_best_accepted():
    # candidate 1 fails OCR -> rejected; scorer only runs for accepted 0 and 2
    img, report, candidates = compose_best_of_n(
        _snack(), generator=_gen, textless_ocr=_seq([True, False, True]),
        critique_scorer=_scorer_seq([7.0, 9.0]), n=3)
    assert report["candidates"][1]["background_status"] == "image_has_text"
    assert report["candidates"][1]["composite"] is None  # rejected -> not scored
    assert report["winner_index"] == 2 and report["winner_composite"] == pytest.approx(9.0)
    assert img is not None


# ── all rejected -> deterministic fallback poster (still returns a poster) ───

def test_best_of_n_all_rejected_falls_back_deterministically():
    img, report, candidates = compose_best_of_n(
        _snack(), generator=_gen, textless_ocr=lambda im: False,
        critique_scorer=_scorer_seq([]), n=3)
    # the 3 generated candidates were all rejected; the deterministic fallback is
    # appended as a real candidate so winner_index always indexes candidates.
    assert all(c["background_status"] == "image_has_text" for c in report["candidates"][:3])
    assert report["winner_index"] == 3
    assert report["candidates"][3]["background_status"] == "deterministic_fallback"
    assert img is not None                            # deterministic poster still ships
    assert candidates[report["winner_index"]]["img"] is img   # index always valid


def test_best_of_n_ineligible_facts_never_crashes():
    # ineligible facts (no offer) -> compose returns None; must NOT crash critique,
    # must return None best_img (caller falls back to the existing pipeline)
    facts = [f for f in _snack() if f.fact_id != "pricing_structure"]
    img, report, candidates = compose_best_of_n(
        facts, generator=_gen, textless_ocr=_ok, critique_scorer=_scorer_seq([]), n=3)
    assert img is None  # ineligible -> no poster
    assert "compose_ineligible" in {c["background_status"] for c in report["candidates"]}


# ── critique unavailable -> still selects (first accepted), never crashes ────

def test_best_of_n_critique_unavailable_selects_first_accepted():
    img, report, candidates = compose_best_of_n(
        _snack(), generator=_gen, textless_ocr=_ok, critique_scorer=lambda *a: None, n=3)
    assert report["winner_index"] == 0               # first accepted food poster
    assert img is not None


# ── audit log: all candidates recorded with status + composite ──────────────

def test_best_of_n_logs_all_candidates():
    _, report, _ = compose_best_of_n(
        _snack(), generator=_gen, textless_ocr=_seq([True, False, True]),
        critique_scorer=_scorer_seq([5.0, 6.0]), n=3)
    assert len(report["candidates"]) == 3
    for c in report["candidates"]:
        assert "index" in c and "background_status" in c and "composite" in c


# ── winner is fact-safe (all text from locked facts) ────────────────────────

def test_best_of_n_winner_is_fact_safe():
    facts = _snack()
    img, report, candidates = compose_best_of_n(
        facts, generator=_gen, textless_ocr=_ok, critique_scorer=_scorer_seq([8.0, 6.0, 7.0]), n=3)
    assert report["winner_index"] >= 0
    winner = candidates[report["winner_index"]]
    allowed = " ".join(x.value for x in facts).casefold()
    for placed in winner["report"]["placed_text"]:
        for tok in placed.casefold().split():
            if any(ch.isalnum() for ch in tok):
                assert tok in allowed, f"ungrounded token {tok!r}"


def test_rejected_text_bearing_candidate_never_scored_or_selected():
    # candidate 0 fails OCR (text) -> never scored, never eligible to win, even
    # though it was generated first. A text-bearing candidate can NEVER win.
    img, report, candidates = compose_best_of_n(
        _snack(), generator=_gen, textless_ocr=_seq([False, True]),
        critique_scorer=_scorer_seq([5.0]), n=2)
    assert report["candidates"][0]["background_status"] == "image_has_text"
    assert report["candidates"][0]["composite"] is None   # rejected -> never scored
    assert report["winner_index"] == 1                    # only the accepted candidate can win


def test_best_of_n_n1_degenerate():
    img, report, _ = compose_best_of_n(
        _snack(), generator=_gen, textless_ocr=_ok, critique_scorer=_scorer_seq([7.0]), n=1)
    assert report["n"] == 1 and report["winner_index"] == 0 and img is not None


# ── no routing ──────────────────────────────────────────────────────────────

def test_no_routing_render_py_does_not_reference_best_of_n():
    render = (REPO / "src" / "agents" / "flyer" / "render.py").read_text(encoding="utf-8")
    assert "compose_best_of_n" not in render
