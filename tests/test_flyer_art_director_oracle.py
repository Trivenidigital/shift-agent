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

import os
import subprocess
import sys
from pathlib import Path

from agents.flyer.flyer_art_director_oracle import (
    AXES,
    ArtDirectorScore,
    AxisScore,
    score_art_direction,
    write_sidecar,
)

# Repo roots for subprocess CLI invocation. The CLI script imports the oracle via
# its flat-VPS shim (from flyer_art_director_oracle import ...) with a package
# fallback (from agents.flyer.flyer_art_director_oracle import ...); both src and
# the flyer dir are placed on PYTHONPATH so either import resolves off-box.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "src"
_FLYER_DIR = _SRC_DIR / "agents" / "flyer"
_SCORE_CLI = _SRC_DIR / "platform" / "scripts" / "score-flyer-art-direction"


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
        "message_clarity": 0,
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

    assert result.axes["message_clarity"].score == 1
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


# ── BLOCKER 2: non-finite scores never raise (NaN / Infinity) ────────────────

import math  # noqa: E402  (kept local to the non-finite block for readability)


def test_nan_score_axis_dropped_others_kept_never_raises():
    """json.loads accepts the literal NaN. int(round(nan)) raises ValueError; the
    coercion must instead reject the non-finite score (treat the axis as missing)
    and NEVER raise. Other axes are kept; composite stays finite."""
    raw = '{"axes": {"theme_clarity": {"score": NaN, "critique": "c1"}, '
    raw += '"hook_prominence": {"score": 8, "critique": "c2"}}, '
    raw += '"overall_critique": "o"}'
    # sanity: the literal really does parse to a non-finite float
    assert math.isnan(json.loads(raw)["axes"]["theme_clarity"]["score"])

    provider = _provider_returning(raw)
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert "theme_clarity" not in result.axes  # NaN dropped
    assert result.axes["hook_prominence"].score == 8
    assert result.composite == 8.0
    assert math.isfinite(result.composite)


def test_positive_infinity_score_axis_dropped_never_raises():
    """int(round(inf)) raises OverflowError; the coercion must reject +Infinity."""
    raw = '{"axes": {"theme_clarity": {"score": Infinity, "critique": "c1"}, '
    raw += '"hook_prominence": {"score": 5, "critique": "c2"}}, '
    raw += '"overall_critique": "o"}'
    assert math.isinf(json.loads(raw)["axes"]["theme_clarity"]["score"])

    provider = _provider_returning(raw)
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert "theme_clarity" not in result.axes  # +Infinity dropped
    assert result.axes["hook_prominence"].score == 5
    assert result.composite == 5.0
    assert math.isfinite(result.composite)


def test_negative_infinity_score_axis_dropped_never_raises():
    raw = '{"axes": {"theme_clarity": {"score": -Infinity, "critique": "c1"}, '
    raw += '"hook_prominence": {"score": 6, "critique": "c2"}}, '
    raw += '"overall_critique": "o"}'
    assert math.isinf(json.loads(raw)["axes"]["theme_clarity"]["score"])

    provider = _provider_returning(raw)
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert "theme_clarity" not in result.axes  # -Infinity dropped
    assert result.axes["hook_prominence"].score == 6
    assert result.composite == 6.0


def test_string_infinity_score_rejected_never_raises():
    """A string score "Infinity" → float("Infinity") is non-finite → rejected
    (axis dropped), never raises."""
    payload = {
        "axes": {
            "theme_clarity": {"score": "Infinity", "critique": "c1"},
            "hook_prominence": {"score": 7, "critique": "c2"},
        },
        "overall_critique": "o",
    }
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert "theme_clarity" not in result.axes
    assert result.axes["hook_prominence"].score == 7
    assert result.composite == 7.0


def test_string_overflowing_exponent_score_rejected_never_raises():
    """A string score "1e999" → float overflows to inf → non-finite → rejected."""
    payload = {
        "axes": {
            "theme_clarity": {"score": "1e999", "critique": "c1"},
            "hook_prominence": {"score": 4, "critique": "c2"},
        },
        "overall_critique": "o",
    }
    assert math.isinf(float("1e999"))  # sanity
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert "theme_clarity" not in result.axes
    assert result.axes["hook_prominence"].score == 4
    assert result.composite == 4.0


def test_string_nan_score_rejected_never_raises():
    payload = {
        "axes": {
            "theme_clarity": {"score": "NaN", "critique": "c1"},
            "hook_prominence": {"score": 3, "critique": "c2"},
        },
        "overall_critique": "o",
    }
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert "theme_clarity" not in result.axes
    assert result.axes["hook_prominence"].score == 3
    assert result.composite == 3.0


def test_all_axes_non_finite_yields_safe_finite_composite():
    """When every axis is non-finite, all are dropped; composite is a finite 0.0
    (never NaN/inf), and the oracle never raises."""
    raw = '{"axes": {"theme_clarity": {"score": NaN, "critique": "c"}, '
    raw += '"hook_prominence": {"score": Infinity, "critique": "c"}}, '
    raw += '"overall_critique": "o"}'
    provider = _provider_returning(raw)
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert result.axes == {}
    assert result.composite == 0.0
    assert math.isfinite(result.composite)


# ── Task C2: sidecar writer ─────────────────────────────────────────────────


def test_write_sidecar_default_path_roundtrips(tmp_path):
    """write_sidecar serializes every axis (score + critique), composite, and
    overall_critique to <image>.artdirector.json next to the image; round-trips."""
    image = tmp_path / "flyer.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")  # tiny PNG stub, not parsed
    axes = {axis: AxisScore(score=idx + 2, critique=f"crit-{axis}") for idx, axis in enumerate(AXES)}
    score = ArtDirectorScore(axes=axes, composite=5.5, overall_critique="solid but busy")

    out = write_sidecar(str(image), score)

    expected = Path(str(image) + ".artdirector.json")
    assert Path(out) == expected
    assert expected.exists()

    loaded = json.loads(expected.read_text(encoding="utf-8"))
    assert loaded["composite"] == 5.5
    assert loaded["overall_critique"] == "solid but busy"
    assert set(loaded["axes"].keys()) == set(AXES)
    for axis in AXES:
        assert loaded["axes"][axis]["score"] == axes[axis].score
        assert loaded["axes"][axis]["critique"] == axes[axis].critique


def test_write_sidecar_custom_out_path(tmp_path):
    """An explicit out_path overrides the default <image>.artdirector.json."""
    image = tmp_path / "flyer.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    out_path = tmp_path / "custom-score.json"
    score = ArtDirectorScore(axes={}, composite=0.0, overall_critique="empty")

    out = write_sidecar(str(image), score, out_path=str(out_path))

    assert Path(out) == out_path
    assert out_path.exists()
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded["composite"] == 0.0
    assert loaded["axes"] == {}
    assert loaded["overall_critique"] == "empty"


# ── Task C2: score-flyer-art-direction CLI ──────────────────────────────────


def _cli_env():
    """PYTHONPATH covering both the package import (src) and the flat-VPS import
    (flyer dir), so the CLI's import shim resolves off-box. No API key is set, so
    the default provider is None and the oracle returns a safe empty score."""
    env = {**os.environ}
    extra = os.pathsep.join((str(_SRC_DIR), str(_SRC_DIR / "platform"), str(_FLYER_DIR)))
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = extra + (os.pathsep + existing if existing else "")
    # Ensure no key is visible — the no-key path is what this test exercises.
    env.pop("OPENROUTER_API_KEY", None)
    return env


def test_cli_no_api_key_exits_zero_and_writes_sidecar(tmp_path):
    """With NO API key, the CLI must NOT traceback: it exits 0, the oracle returns
    a safe empty score (composite 0.0), and the sidecar is still written + parses.
    No network is touched (default provider is None when no key is present)."""
    image = tmp_path / "tmp.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")  # fake image; never decoded on the no-key path
    out_path = tmp_path / "tmp.json"

    result = subprocess.run(
        [sys.executable, str(_SCORE_CLI), "--image", str(image), "--out", str(out_path)],
        capture_output=True,
        text=True,
        env=_cli_env(),
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout
    assert out_path.exists()
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded["composite"] == 0.0
    assert loaded["axes"] == {}
    # The safe score carries a non-empty note explaining the unavailable provider.
    assert loaded["overall_critique"] != ""


def test_cli_default_sidecar_path_no_api_key(tmp_path):
    """Without --out, the CLI writes the default <image>.artdirector.json sidecar
    and still exits 0 with no traceback on the no-key path."""
    image = tmp_path / "tmp.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    result = subprocess.run(
        [sys.executable, str(_SCORE_CLI), "--image", str(image)],
        capture_output=True,
        text=True,
        env=_cli_env(),
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "Traceback" not in result.stderr
    expected = Path(str(image) + ".artdirector.json")
    assert expected.exists()
    loaded = json.loads(expected.read_text(encoding="utf-8"))
    assert loaded["composite"] == 0.0


def _cli_env_without_src():
    """A CLEANED environment WITHOUT `src` (or any flyer path) on PYTHONPATH and
    with no OPENROUTER_API_KEY. This proves the CLI is self-contained on import:
    its own sys.path header must put the repo `src/` dir on the path so that the
    oracle's transitive imports (visual_qa → agents.flyer.facts /
    agents.flyer.semantic_brief, which need `src`) resolve. A bare invocation must
    therefore NOT traceback at import time, before main()'s error handling."""
    env = {**os.environ}
    # Set PYTHONPATH to something irrelevant — explicitly NOT containing src.
    env["PYTHONPATH"] = str(tmp_irrelevant_dir())
    env.pop("OPENROUTER_API_KEY", None)
    return env


def tmp_irrelevant_dir():
    """A path guaranteed not to satisfy any flyer/platform import."""
    return _REPO_ROOT / "tasks"


def test_cli_self_contained_without_src_on_pythonpath(tmp_path):
    """BLOCKER 1: a bare CLI invocation with `src` NOT on PYTHONPATH must still
    import cleanly (the script must add the repo `src/` dir to sys.path itself).
    Otherwise the oracle's transitive visual_qa fallback imports
    (from agents.flyer.facts / agents.flyer.semantic_brief) traceback at import,
    BEFORE main()'s error handling — violating 'CLI never tracebacks'.

    Asserts: exit 0, NO traceback in stderr, sidecar written + parses."""
    image = tmp_path / "tmp.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    out_path = tmp_path / "tmp.json"

    result = subprocess.run(
        [sys.executable, str(_SCORE_CLI), "--image", str(image), "--out", str(out_path)],
        capture_output=True,
        text=True,
        env=_cli_env_without_src(),
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "Traceback" not in result.stderr, result.stderr
    assert "Traceback" not in result.stdout
    assert out_path.exists()
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded["composite"] == 0.0
    assert loaded["axes"] == {}
    assert loaded["overall_critique"] != ""


# ── Slice B / Task B1.1: message_clarity headline axis (8-axis rubric) ───────


def test_axes_includes_message_clarity_as_eighth_axis():
    """The rubric now has 8 axes; message_clarity is one of them and is the
    HEADLINE axis (listed FIRST in canonical order)."""
    assert "message_clarity" in AXES
    assert len(AXES) == 8
    assert AXES[0] == "message_clarity"


def test_all_eight_axes_parsed_with_composite_mean_of_eight():
    """A model that returns all 8 axes (including message_clarity {score, critique})
    is parsed in full; composite is the mean over all 8."""
    payload = _well_formed_payload()  # built from AXES → now spans all 8
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction(
        "/tmp/flyer.png", brief_summary="diwali sweets", provider=provider
    )

    assert set(result.axes) == set(AXES)
    assert len(result.axes) == 8
    assert isinstance(result.axes["message_clarity"], AxisScore)
    # message_clarity sits at index 0 → its score is 0 + 2 in _well_formed_payload.
    assert result.axes["message_clarity"].score == 2
    assert result.axes["message_clarity"].critique == "crit-message_clarity"
    expected = sum(idx + 2 for idx in range(len(AXES))) / len(AXES)
    assert result.composite == expected


def test_message_clarity_omitted_tolerated_composite_over_present():
    """If the model omits message_clarity, the axis is simply absent; composite is
    the mean over the 7 axes actually scored. The oracle never raises."""
    present = [a for a in AXES if a != "message_clarity"]
    payload = {
        "axes": {axis: {"score": 6, "critique": "c"} for axis in present},
        "overall_critique": "no headline axis returned",
    }
    provider = _provider_returning(json.dumps(payload))
    result = score_art_direction("/tmp/flyer.png", provider=provider)

    assert "message_clarity" not in result.axes
    assert set(result.axes) == set(present)
    assert len(result.axes) == 7
    assert result.composite == 6.0


def test_message_clarity_score_out_of_range_clamped_1_10():
    """An out-of-range message_clarity score is clamped to 1..10 like every axis."""
    low = {
        "axes": {"message_clarity": {"score": 0, "critique": "c"}},
        "overall_critique": "o",
    }
    high = {
        "axes": {"message_clarity": {"score": 42, "critique": "c"}},
        "overall_critique": "o",
    }
    low_result = score_art_direction(
        "/tmp/flyer.png", provider=_provider_returning(json.dumps(low))
    )
    high_result = score_art_direction(
        "/tmp/flyer.png", provider=_provider_returning(json.dumps(high))
    )

    assert low_result.axes["message_clarity"].score == 1
    assert high_result.axes["message_clarity"].score == 10
