"""DEV-ONLY vision-LLM "art director" oracle scorer for Flyer Studio (Slice C, C1).

Scores a rendered flyer PNG on 7 art-direction axes (1-10 + a one-sentence
critique each, plus an overall critique). This is a STANDALONE diagnostic tool:

- It is NOT wired into the render or QA path; it NEVER blocks delivery.
- It is entirely separate from the QA / dangerous-leak verdict logic — it imports
  only the low-level OpenRouter key + URL + image-encoding seam (NOT any verdict
  function) from `visual_qa`, mirroring how `creative_planner` reuses the
  semantic-brief OpenRouter seam while injecting a `provider` for tests.
- It NEVER raises. Malformed JSON, provider errors, or an unreadable image all
  return a safe ArtDirectorScore(axes={}, composite=0.0, overall_critique=<note>).
- Vision calls go through an injectable `provider` callable so tests inject a fake
  (no network, no OpenRouter spend). When `provider` is None the real seam is used
  (NOT exercised in tests — there is no key in the test environment).

Drift-tag: extends-Hermes (reuses the deployed OpenRouter vision seam; adds a
dev-only scorer on top, no new persisted schema / no render-path wiring).
"""
from __future__ import annotations

import base64
import json
import math
import mimetypes
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

# Reuse the deployed OpenRouter seam (flat layout on the VPS). We pull ONLY the
# low-level key + endpoint + timeout + model — NOT any QA verdict logic.
try:  # pragma: no cover - import shim mirrors sibling flyer modules
    from visual_qa import (  # type: ignore
        OPENROUTER_TIMEOUT_SEC,
        OPENROUTER_URL,
        VISION_QA_MODEL,
        _openrouter_key,
    )
except ImportError:  # pragma: no cover - exercised under the package layout
    from agents.flyer.visual_qa import (
        OPENROUTER_TIMEOUT_SEC,
        OPENROUTER_URL,
        VISION_QA_MODEL,
        _openrouter_key,
    )


# The 7 art-direction axes, in canonical order. `composite` averages over the
# axes actually scored (so order is not load-bearing for the mean).
AXES = (
    "theme_clarity",
    "hook_prominence",
    "appetite_appeal",
    "product_merchandising",
    "offer_energy",
    "brand_presence",
    "would_i_post",
)


# A provider takes (image_path, brief_summary) and returns the model's response —
# either a JSON string or an already-parsed dict — or None on failure. Pulled out
# so tests inject a deterministic fake (no network).
ArtDirectorProvider = Callable[..., Any]


ART_DIRECTOR_PROMPT = (
    "You are a senior art director reviewing a finished marketing flyer image. "
    "Score it on these 7 axes, each an INTEGER 1-10, with one short sentence of "
    "critique per axis:\n"
    "- theme_clarity: is the visual theme/concept immediately clear?\n"
    "- hook_prominence: does a single attention-grabbing hook dominate?\n"
    "- appetite_appeal: does the imagery look appetizing / desirable?\n"
    "- product_merchandising: are the products/items shown well and legibly?\n"
    "- offer_energy: does any offer/promo feel exciting and urgent?\n"
    "- brand_presence: is the brand identity present and confident?\n"
    "- would_i_post: would you personally post this to a brand's social feed?\n"
    "Return JSON ONLY in this exact shape:\n"
    '{"axes": {"<axis>": {"score": <int 1-10>, "critique": "<one sentence>"}, ...}, '
    '"overall_critique": "<one short paragraph>"}'
)


@dataclass(frozen=True)
class AxisScore:
    score: int  # clamped 1..10
    critique: str  # one short sentence; "" if the model omitted it


@dataclass(frozen=True)
class ArtDirectorScore:
    axes: dict[str, AxisScore]  # keyed by the 7 AXES (only axes actually scored)
    composite: float  # mean of the scored axes (0.0 if none)
    overall_critique: str


def _clamp_score(value: Any) -> Optional[int]:
    """Coerce a model score to an int clamped to 1..10. Non-numeric → None (the
    caller treats the axis as missing). Bools are rejected (a stray True/False is
    not a real score). Non-finite numbers (NaN / ±Infinity) are ALSO rejected:
    json.loads accepts the literals NaN / Infinity, and a string "Infinity" /
    "1e999" coerces to a non-finite float — int(round(nan)) raises ValueError and
    int(round(inf)) raises OverflowError, so we must reject them up front and
    coerce inside try/except so this helper NEVER raises."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        try:
            number_f = float(value.strip()) if isinstance(value, str) else float(value)
            if not math.isfinite(number_f):  # NaN / ±Infinity → treat as missing
                return None
            number = int(round(number_f))
        except (ValueError, OverflowError, TypeError, AttributeError):
            return None
    else:
        return None
    return max(1, min(10, number))


def _parse_axes(raw_axes: Any) -> dict[str, AxisScore]:
    """Parse the model's `axes` map into AxisScore entries. Unknown axes ignored;
    non-numeric scores drop the axis; a missing critique becomes ""."""
    if not isinstance(raw_axes, dict):
        return {}
    parsed: dict[str, AxisScore] = {}
    for axis in AXES:
        entry = raw_axes.get(axis)
        if not isinstance(entry, dict):
            continue
        score = _clamp_score(entry.get("score"))
        if score is None:
            continue  # non-numeric / absent score → treat axis as missing
        critique = entry.get("critique")
        critique = critique.strip() if isinstance(critique, str) else ""
        parsed[axis] = AxisScore(score=score, critique=critique)
    return parsed


def _to_document(response: Any) -> Optional[dict]:
    """Normalize a provider response (JSON string or dict) into a dict, or None."""
    if isinstance(response, dict):
        return response
    if isinstance(response, (str, bytes, bytearray)):
        try:
            doc = json.loads(response)
        except (ValueError, TypeError):
            return None
        return doc if isinstance(doc, dict) else None
    return None


def _safe_score(note: str) -> ArtDirectorScore:
    return ArtDirectorScore(axes={}, composite=0.0, overall_critique=note)


def _build_real_provider() -> Optional[ArtDirectorProvider]:
    """Real OpenRouter vision provider. Mirrors visual_qa._vision_text's encoding
    seam (base64 data-URL + json_object response) but uses the art-director prompt.
    Returns None when no key is configured — then `score_art_direction` fails safe.
    NOT exercised in tests (no key in the test environment)."""
    key = _openrouter_key()
    if not key or "PLACEHOLDER" in key:
        return None

    def provider(image_path: str, brief_summary: str = "") -> Any:
        from pathlib import Path

        path = Path(image_path)
        if not path.exists() or not path.is_file():
            return None
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/png"
        raw = base64.b64encode(path.read_bytes()).decode("ascii")
        prompt = ART_DIRECTOR_PROMPT
        if brief_summary:
            prompt = f"{prompt}\n\nBrief context: {brief_summary}"
        payload = {
            "model": VISION_QA_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{raw}"}},
                ],
            }],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=OPENROUTER_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
        doc = json.loads(body)
        return doc["choices"][0]["message"]["content"]

    return provider


def score_art_direction(
    image_path: str,
    *,
    brief_summary: str = "",
    provider: Optional[ArtDirectorProvider] = None,
) -> ArtDirectorScore:
    """Score a rendered flyer PNG on the 7 art-direction axes.

    DEV-ONLY. NEVER raises. NEVER blocks delivery. Vision calls go through
    `provider` (injectable for tests); when None, the real OpenRouter seam is used
    — and if no key/provider is available, a safe score is returned.
    """
    try:
        prov = provider or _build_real_provider()
        if prov is None:
            return _safe_score("art-director oracle unavailable: no vision provider")
        response = prov(image_path, brief_summary)

        doc = _to_document(response)
        if doc is None:
            return _safe_score("art-director oracle error: malformed model response")

        # Parse INSIDE the try (defense in depth): _parse_axes / _clamp_score are
        # hardened against non-finite scores, but keeping the whole parse here
        # guarantees ANY unforeseen parse error still returns the safe score
        # rather than raising — the oracle's contract is "NEVER raises".
        axes = _parse_axes(doc.get("axes"))
        composite = round(sum(a.score for a in axes.values()) / len(axes), 4) if axes else 0.0
        overall = doc.get("overall_critique")
        overall = overall.strip() if isinstance(overall, str) else ""
        return ArtDirectorScore(axes=axes, composite=composite, overall_critique=overall)
    except Exception as exc:  # noqa: BLE001 - dev tool MUST never raise
        return _safe_score(f"art-director oracle error: {type(exc).__name__}")


def _sidecar_path(image_path: str) -> Path:
    """Default sidecar location: <image>.artdirector.json, next to the rendered
    image. Mirrors the visual-QA `<image>.qa.json` / render `<image>.text.json`
    naming so all flyer sidecars sit alongside their artifact for version diffing.
    """
    return Path(str(image_path) + ".artdirector.json")


def score_to_dict(score: ArtDirectorScore) -> dict:
    """Plain JSON-serializable dict for an ArtDirectorScore. Every axis carries
    its score + critique; composite + overall_critique sit at the top level."""
    return {
        "axes": {
            axis: {"score": axis_score.score, "critique": axis_score.critique}
            for axis, axis_score in score.axes.items()
        },
        "composite": score.composite,
        "overall_critique": score.overall_critique,
    }


def write_sidecar(
    image_path: str,
    score: ArtDirectorScore,
    *,
    out_path: str | None = None,
) -> str:
    """Write an ArtDirectorScore as a sidecar JSON next to the rendered image.

    DEV-ONLY. Default sidecar path is ``<image>.artdirector.json`` (override via
    ``out_path``). Mirrors the visual-QA `.qa.json` writer: `json.dumps(...,
    indent=2, ensure_ascii=False)` via `safe_io.atomic_write_text` when importable
    (the deployed pattern), falling back to a plain tmp-write+replace off-box.
    Returns the path written (as a string).
    """
    path = Path(out_path) if out_path else _sidecar_path(image_path)
    text = json.dumps(score_to_dict(score), indent=2, ensure_ascii=False)
    try:  # pragma: no cover - import shim mirrors sibling flyer modules
        from safe_io import atomic_write_text  # type: ignore
    except ImportError:  # pragma: no cover
        try:
            from platform.safe_io import atomic_write_text  # type: ignore
        except ImportError:
            atomic_write_text = None  # type: ignore
    if atomic_write_text is not None:
        atomic_write_text(path, text)
    else:  # plain atomic-ish write when safe_io is unavailable (off-box dev)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return str(path)
