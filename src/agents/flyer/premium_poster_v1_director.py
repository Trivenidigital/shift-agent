"""Premium Poster Template v1 — Slice C2A: Hermes-directed textless food background.

Drift-check tag: extends-Hermes.

Hermes-first analysis:
- Hermes owns the LLM/vision gateway, image generation, OCR/vision, skill
  dispatch, identity, WhatsApp I/O, audit, approvals, state. None re-implemented.
- Net-new (allowed business logic): the deterministic *orchestration* that turns
  locked campaign facts into a TEXTLESS food-background art-direction prompt, runs
  it through an injected image generator + an injected textless OCR gate, and
  feeds ONLY a validated textless image into the deterministic poster composer
  (``premium_poster_v1``). Pure wiring over injected callables — no model call
  lives here.

Boundary (recorded on PR #517): **Hermes = art direction** (the scene families +
the optional injected ``art_director`` / the injected image ``generator``);
**Python = facts, safety, the no-text contract, the OCR gate, fallback, and the
exact deterministic layout/shipping**. NO model-rendered text is ever trusted:
any gate failure -> the composer's deterministic warm fallback background.

SHADOW only: nothing here is wired into the live render path (no routing, no
deploy, no flag). The C2B VPS shadow run injects the real generator (render.py's
``force_background_only`` path) + real OCR (``visual_qa``) at the call site.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from agents.flyer.campaign_scene_prompts import (
    CampaignSceneTemplate,
    select_food_poster_scene,
)

# The explicit no-text contract appended to EVERY food-background prompt. The
# image model must produce a TEXTLESS food/background only; Python overlays all
# copy deterministically afterwards. The post-generation OCR gate is the
# *enforcement*; this contract is the *instruction* to the model.
TEXTLESS_CONTRACT = (
    "ABSOLUTELY NO text of any kind anywhere in the image: no text, no words, no "
    "letters, no numbers, no logos, no prices, no menus, no readable signs, no "
    "labels, no watermarks, no packaging text, no captions, no typography. Render a "
    "textless food/background image ONLY."
)
_PREMIUM_STYLE = (
    "Premium restaurant marketing photography, warm inviting lighting, rich "
    "appetizing colours, professional food styling, generous negative space, "
    "portrait composition suitable as a poster background."
)
# Composition guidance (C3): bias the model toward overlay-safe layouts so the
# deterministic text (headline/offer/items/footer) lands on clean areas. Still
# textless — this directs WHERE the food sits, never adds copy.
_COMPOSITION = (
    "Composition: keep the food primarily in the centre / mid-background; leave the "
    "upper third and a lower band as clean, uncluttered negative space (soft "
    "out-of-focus backdrop or empty table surface) as overlay-safe zones for poster "
    "text."
)

# Only these descriptive fact ids feed scene SELECTION (which fixed template to
# use). They never enter the prompt (the prompt is the fixed scene_block + item
# names + the no-text contract). Money / contact / address facts are excluded so
# sensitive copy can never influence anything; the raw owner brief is excluded for
# the same reason (and is not a locked-fact id today regardless).
_DIRECTION_FACT_IDS = (
    "business_category", "business_name", "campaign_title", "occasion",
    "style", "notes",
)

# Injected-callable contracts (the box wires the real implementations):
#   Generator   : prompt -> path of a generated image (or None / raises on failure)
#   TextlessOCR : PIL.Image -> True if the image is textless/safe (or raises)
#   ArtDirector : (facts, scene) -> an enriched art-direction string (Hermes text call)
Generator = Callable[[str], Optional[str]]
TextlessOCR = Callable[[Any], bool]
ArtDirector = Callable[[Sequence[Any], CampaignSceneTemplate], str]
#   Scorer : (image_path, brief_summary) -> the oracle's score_to_dict shape
#            ({"axes": {...}, "composite": float, "overall_critique": str}) or None
#            when no critique is available. Injected for tests + the box shadow run.
Scorer = Callable[..., Optional[dict]]


def _fact_value(facts: Sequence[Any], fact_id: str) -> str:
    for f in facts:
        if getattr(f, "fact_id", None) == fact_id:
            return (getattr(f, "value", "") or "").strip()
    return ""


def _items(facts: Sequence[Any]) -> list[str]:
    out: list[str] = []
    for f in facts:
        fid = getattr(f, "fact_id", "") or ""
        if fid.startswith("item:") and fid.endswith(":name"):
            v = (getattr(f, "value", "") or "").strip()
            if v:
                out.append(v)
    return out


def scene_context(facts: Sequence[Any]) -> str:
    """Build the deterministic scene-selection context from SAFE descriptive facts
    + item names only (never prices/phone/address)."""
    parts = [_fact_value(facts, fid) for fid in _DIRECTION_FACT_IDS]
    parts = [p for p in parts if p]
    parts.extend(_items(facts))
    return " ".join(parts)


def build_textless_food_prompt(
    facts: Sequence[Any],
    scene: CampaignSceneTemplate,
    *,
    art_director: Optional[ArtDirector] = None,
) -> str:
    """Assemble the textless food-background prompt: premium style + scene
    direction + a food-style hint from item facts + the no-text contract.

    Item names guide the FOOD rendered (Hermes's creative direction) and are
    explicitly framed "as food only — never as text"; the no-text contract + the
    post-generation OCR gate ensure no copy leaks. Sensitive copy (prices, phone,
    address) is NEVER injected. Deterministic; if an injected ``art_director`` (a
    Hermes text call, on the box) is supplied its enriched direction replaces the
    static scene block — but Python ALWAYS keeps the no-text contract around it.
    """
    if art_director is not None:
        try:
            direction = (art_director(facts, scene) or "").strip() or scene.scene_block
        except Exception:
            direction = scene.scene_block  # Hermes enrichment is best-effort
    else:
        direction = scene.scene_block

    food_hint = ""
    items = _items(facts)
    if items:
        shown = ", ".join(items[:6])
        food_hint = (
            f" Feature appetizing, freshly-prepared dishes such as {shown} as food in "
            f"the scene (as food only — never rendered as text)."
        )
    return f"{_PREMIUM_STYLE} Scene: {direction}{food_hint} {_COMPOSITION} {TEXTLESS_CONTRACT}"


def brief_summary(facts: Sequence[Any]) -> str:
    """A short, SAFE context line for the vision critique (campaign title +
    category + a few item names). Never includes prices / phone / address."""
    title = _fact_value(facts, "campaign_title") or _fact_value(facts, "business_name")
    category = _fact_value(facts, "business_category")
    parts = [p for p in (title, category) if p]
    items = _items(facts)[:3]
    if items:
        parts.append("featuring " + ", ".join(items))
    return " — ".join(parts)


@dataclass(frozen=True)
class FoodBackgroundResult:
    """Outcome of direct -> generate -> textless-gate. ``food_image_path`` is set
    ONLY on ``status == "ok"`` (a validated, textless image); every other status
    leaves it None so the composer falls back deterministically."""

    status: str          # ok | generation_failed | image_load_failed | image_has_text | check_error
    scene_key: str
    prompt: str
    food_image_path: Optional[str]
    detail: str = ""


def generate_textless_food_background(
    facts: Sequence[Any],
    *,
    generator: Generator,
    textless_ocr: TextlessOCR,
    art_director: Optional[ArtDirector] = None,
    scene: Optional[CampaignSceneTemplate] = None,
) -> FoodBackgroundResult:
    """Select a food scene, build the textless prompt, generate (injected), then
    gate the result through the injected OCR. NEVER returns a path that failed the
    textless gate. Distinguishes a check OUTAGE (``check_error``) from genuine
    text-detection (``image_has_text``) so the C2B run can alert on the former."""
    scene = scene or select_food_poster_scene(scene_context(facts))
    prompt = build_textless_food_prompt(facts, scene, art_director=art_director)

    try:
        path = generator(prompt)
    except Exception as exc:  # generation backend error
        return FoodBackgroundResult(
            "generation_failed", scene.key, prompt, None, f"generator_error:{type(exc).__name__}")
    if not path:
        return FoodBackgroundResult(
            "generation_failed", scene.key, prompt, None, "generator_returned_none")

    # Decode ONCE into an in-memory RGB copy. All image-decode failure (corrupt /
    # truncated / undecodable / a temp path cleaned up) lands here as
    # image_load_failed; the copy survives the closed file so the OCR call below
    # cannot race the path.
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
            rgb = im.convert("RGB")
    except Exception as exc:
        return FoodBackgroundResult(
            "image_load_failed", scene.key, prompt, None, f"load_error:{type(exc).__name__}")

    # ONLY the injected OCR call may raise here -> a check OUTAGE, kept distinct
    # from a bad image (above) and from genuine text-detection (below).
    try:
        textless = bool(textless_ocr(rgb))
    except Exception as exc:
        return FoodBackgroundResult(
            "check_error", scene.key, prompt, None, f"ocr_error:{type(exc).__name__}")

    if not textless:  # the model rendered text — drop it
        return FoodBackgroundResult(
            "image_has_text", scene.key, prompt, None, "ocr_detected_text")

    return FoodBackgroundResult("ok", scene.key, prompt, path, "textless_verified")


def _critique_dict(axes: dict, composite: float, overall: str, status: str, available: bool) -> dict:
    return {"axes": axes, "composite": composite, "overall_critique": overall,
            "status": status, "available": available}


def _oracle_scorer() -> Optional[Scorer]:
    """Default scorer: the dev-only art-director oracle (lazy import — its
    visual_qa->safe_io->fcntl chain is Linux-only). Returns None when the oracle
    is unavailable so the wrapper records ``critique_unavailable`` and never raises.
    NOT used by the unit tests (they inject a deterministic scorer)."""
    try:
        from agents.flyer.flyer_art_director_oracle import score_art_direction, score_to_dict
    except Exception:
        return None

    def scorer(image_path: str, brief: str = "") -> Optional[dict]:
        score = score_art_direction(image_path, brief_summary=brief)
        # The oracle returns an empty-axis safe score for BOTH "no vision provider"
        # (note prefix "art-director oracle unavailable") and internal errors (note
        # prefix "art-director oracle error"). Anchor on the documented prefix —
        # not a bare substring — so only the genuinely-unavailable case maps to
        # critique_unavailable (None); an oracle error keeps its empty-axis dict and
        # the wrapper records critique_error (ran, bad result — a distinct signal).
        if not score.axes and (score.overall_critique or "").lower().startswith(
            "art-director oracle unavailable"
        ):
            return None
        return score_to_dict(score)

    return scorer


def critique_composed_poster(
    image,
    *,
    brief_summary: str = "",
    scorer: Optional[Scorer] = None,
    image_save_path: Optional[str] = None,
) -> dict:
    """LOG-ONLY visual critique of a composed poster (Hermes's *eyes*). NEVER
    gates delivery, NEVER raises. The injected ``scorer`` maps an image path to the
    oracle's score dict (or None when unavailable); when None the real art-director
    oracle is used. Returns a JSON-safe dict: ``available``, ``composite``,
    ``axes``, ``overall_critique``, ``status`` (ok | critique_unavailable |
    critique_error)."""
    try:
        if image_save_path is not None:
            path = str(image_save_path)
            image.save(path)
        else:
            import os
            import tempfile
            fd, path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            image.save(path)
    except Exception as exc:  # could not materialize the image for the oracle
        return _critique_dict({}, 0.0, f"critique image save failed: {type(exc).__name__}",
                              "critique_error", False)

    use = scorer if scorer is not None else _oracle_scorer()
    if use is None:
        return _critique_dict({}, 0.0, "art-director oracle unavailable", "critique_unavailable", False)

    try:
        result = use(path, brief_summary)
    except Exception as exc:  # scorer/vision backend failure -> safe, recorded
        return _critique_dict({}, 0.0, f"critique scorer error: {type(exc).__name__}",
                              "critique_error", False)

    if result is None:
        return _critique_dict({}, 0.0, "art-director oracle unavailable", "critique_unavailable", False)
    if not isinstance(result, dict):
        return _critique_dict({}, 0.0, "malformed critique result", "critique_error", False)

    axes = result.get("axes") if isinstance(result.get("axes"), dict) else {}
    available = bool(axes)
    try:
        composite = float(result.get("composite"))
    except (TypeError, ValueError):
        composite = 0.0
    overall = result.get("overall_critique")
    overall = overall if isinstance(overall, str) else ""
    return _critique_dict(axes, composite, overall, "ok" if available else "critique_error", available)


def compose_premium_poster_with_generated_background(
    facts: Sequence[Any],
    *,
    generator: Generator,
    textless_ocr: TextlessOCR,
    art_director: Optional[ArtDirector] = None,
    run_critique: bool = False,
    critique_scorer: Optional[Scorer] = None,
    poster_save_path: Optional[str] = None,
    size: tuple[int, int] = (1080, 1350),
):
    """SHADOW orchestration (no routing): direct + generate + textless-gate a food
    background, then compose the deterministic premium poster over it. The
    orchestrator is the single textless gate (it only passes a validated path or
    None), so the composer's ``textless_check`` is left unset to avoid a second
    OCR call. When ``run_critique`` is set, a LOG-ONLY visual critique of the
    composed poster is recorded under ``report["critique"]`` — it NEVER blocks.
    Returns ``(PIL.Image | None, report)`` with the director outcome under
    ``report["director"]``."""
    from agents.flyer.premium_poster_v1 import compose_premium_poster_v1

    bg = generate_textless_food_background(
        facts, generator=generator, textless_ocr=textless_ocr, art_director=art_director)
    img, report = compose_premium_poster_v1(
        facts, food_image_path=bg.food_image_path, size=size)
    report["director"] = {
        "background_status": bg.status,
        "scene_key": bg.scene_key,
        "prompt": bg.prompt,
        "detail": bg.detail,
        "food_image_path": bg.food_image_path,
    }
    if run_critique:
        if img is not None:
            report["critique"] = critique_composed_poster(
                img, brief_summary=brief_summary(facts),
                scorer=critique_scorer, image_save_path=poster_save_path)
        else:
            report["critique"] = _critique_dict({}, 0.0, "no poster composed", "no_poster", False)
    return img, report
