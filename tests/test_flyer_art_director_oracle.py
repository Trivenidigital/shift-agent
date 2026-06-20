"""Slice C / Task C1 — vision-LLM art-director oracle scorer (DEV-ONLY).

Proves the standalone scorer:
- parses well-formed model JSON into an ArtDirectorScore (7 axes + composite + overall critique);
- clamps every axis score to 1..10;
- tolerates missing axes (composite = mean over only the axes actually scored);
- treats a non-numeric score as a missing axis; a missing critique becomes "";
- NEVER raises — malformed JSON / provider error / unreadable image all yield a
  safe ArtDirectorScore(axes={}, composite=0.0, overall_critique=<non-empty note>).

All vision calls are INJECTED via a fake provider — no network, no OpenRouter spend.
"""
from __future__ import annotations

import json

from agents.flyer.flyer_art_director_oracle import (
    AXES,
    ArtDirectorScore,
    AxisScore,
    score_art_direction,
)


def _provider_returning(payload):
    """Build a fake provider that returns `payload` (str or dict) and asserts it
    is called with the image path + a brief_summary kwarg-compatible signature."""

    def provider(image_path, brief_summary=""):
        provider.calls.append((image_path, brief_summary))
        return payload

    provider.calls = []
    return provider


def _well_formed_payload():
    axes = {axis: {"score": idx + 2, "critique": f"crit-{axis}"} for idx, axis in enumerate(AXES)}
    return {"axes": axes, "overall_critique": "solid but busy"}


# ── well-formed JSON ────────────────────────────────────────────────────────


def test_well_formed_json_parsed_with_correct_composite():
    payload = _well_formed_payload()
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", brief_summary="diwali sweets", provider=provider)

    assert isinstance(result, ArtDirectorScore)
    assert set(result.axes) == set(AXES)
    for idx, axis in enumerate(AXES):
        assert isinstance(result.axes[axis], AxisScore)
        assert result.axes[axis].score == idx + 2
        assert result.axes[axis].critique == f"crit-{axis}"
    expected = sum(idx + 2 for idx in range(len(AXES))) / len(AXES)
    assert result.composite == expected
    assert result.overall_critique == "solid but busy"
    # provider was actually invoked with the path + brief
    assert provider.calls == [("/tmp/flyer.png", "diwali sweets")]


def test_provider_may_return_dict_directly():
    provider = _provider_returning(_well_formed_payload())
    result = score_art_direction("/tmp/flyer.png", provider=provider)
    assert set(result.axes) == set(AXES)
    assert result.composite == sum(idx + 2 for idx in range(len(AXES))) / len(AXES)


# ── clamping ────────────────────────────────────────────────────────────────


def test_scores_out_of_range_clamped_to_1_10():
    raw_scores = {
        "theme_clarity": 0,
        "hook_prominence": 11,
        "appetite_appeal": -3,
        "product_merchandising": 99,
        "offer_energy": 10,
        "brand_presence": 1,
        "would_i_post": 7,
    }
    payload = {
        "axes": {axis: {"score": raw_scores[axis], "critique": "c"} for axis in AXES},
        "overall_critique": "x",
    }
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert result.axes["theme_clarity"].score == 1
    assert result.axes["hook_prominence"].score == 10
    assert result.axes["appetite_appeal"].score == 1
    assert result.axes["product_merchandising"].score == 10
    assert result.axes["offer_energy"].score == 10
    assert result.axes["brand_presence"].score == 1
    assert result.axes["would_i_post"].score == 7
    for axis in AXES:
        assert 1 <= result.axes[axis].score <= 10


# ── missing axes ────────────────────────────────────────────────────────────


def test_missing_axes_omitted_and_composite_is_mean_of_present():
    payload = {
        "axes": {
            "theme_clarity": {"score": 8, "critique": "clear"},
            "hook_prominence": {"score": 6, "critique": "ok"},
        },
        "overall_critique": "partial",
    }
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert set(result.axes) == {"theme_clarity", "hook_prominence"}
    assert result.composite == (8 + 6) / 2
    # absent axes are not invented
    for axis in AXES:
        if axis not in {"theme_clarity", "hook_prominence"}:
            assert axis not in result.axes


def test_no_axes_present_yields_zero_composite():
    payload = {"axes": {}, "overall_critique": "nothing scored"}
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", provider=provider)
    assert result.axes == {}
    assert result.composite == 0.0
    assert result.overall_critique == "nothing scored"


# ── non-numeric score / missing critique ────────────────────────────────────


def test_non_numeric_score_treated_as_missing_axis():
    payload = {
        "axes": {
            "theme_clarity": {"score": "great", "critique": "c1"},
            "hook_prominence": {"score": 9, "critique": "c2"},
        },
        "overall_critique": "o",
    }
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert "theme_clarity" not in result.axes  # non-numeric → axis dropped
    assert result.axes["hook_prominence"].score == 9
    assert result.composite == 9.0


def test_missing_critique_becomes_empty_string():
    payload = {
        "axes": {
            "theme_clarity": {"score": 7},
            "hook_prominence": {"score": 5, "critique": ""},
        },
        "overall_critique": "",
    }
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert result.axes["theme_clarity"].score == 7
    assert result.axes["theme_clarity"].critique == ""
    assert result.axes["hook_prominence"].critique == ""
    assert result.composite == (7 + 5) / 2


def test_extra_unknown_axes_ignored():
    payload = {
        "axes": {
            "theme_clarity": {"score": 8, "critique": "c"},
            "totally_made_up_axis": {"score": 10, "critique": "ignore me"},
        },
        "overall_critique": "o",
    }
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert set(result.axes) == {"theme_clarity"}
    assert "totally_made_up_axis" not in result.axes
    assert result.composite == 8.0


# ── never raises ────────────────────────────────────────────────────────────


def test_malformed_json_never_raises_returns_safe_score():
    provider = _provider_returning("this is not json {{{")
    result = score_art_direction("/tmp/flyer.png", provider=provider)
    assert isinstance(result, ArtDirectorScore)
    assert result.axes == {}
    assert result.composite == 0.0
    assert result.overall_critique != ""  # carries a short error note


def test_provider_exception_caught_returns_safe_score():
    def boom(image_path, brief_summary=""):
        raise RuntimeError("provider exploded")

    result = score_art_direction("/tmp/flyer.png", provider=boom)
    assert isinstance(result, ArtDirectorScore)
    assert result.axes == {}
    assert result.composite == 0.0
    assert result.overall_critique != ""


def test_provider_returns_none_returns_safe_score():
    provider = _provider_returning(None)
    result = score_art_direction("/tmp/flyer.png", provider=provider)
    assert result.axes == {}
    assert result.composite == 0.0
    assert result.overall_critique != ""


def test_unreadable_image_with_default_provider_never_raises(tmp_path):
    """No provider injected + nonexistent image: the real seam must fail safe,
    NEVER raise (and never make a network call we can observe — no key in tests)."""
    missing = tmp_path / "does-not-exist.png"
    result = score_art_direction(str(missing))
    assert isinstance(result, ArtDirectorScore)
    assert result.axes == {}
    assert result.composite == 0.0
    assert result.overall_critique != ""
