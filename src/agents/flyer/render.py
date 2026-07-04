"""Deterministic flyer rendering for Hermes Flyer Studio.

This module must import cleanly inside the Hermes venv even when Pillow is not
installed there. Rendering uses local Pillow when available, otherwise it
delegates to `/usr/bin/python3` where `python3-pil` can be installed by ops.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import base64
import contextlib
import contextvars
import hashlib
import http.client
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
import uuid

from schemas import FlyerAsset, FlyerCustomerStore, FlyerOutputFormat, FlyerProject

try:
    from flyer_facts import fact_value, requests_generated_item_suggestions  # type: ignore
except ImportError:  # pragma: no cover - src layout fallback
    from agents.flyer.facts import fact_value, requests_generated_item_suggestions
try:
    from flyer_campaign_scene_prompts import campaign_scene_prompt_block, select_campaign_scene  # type: ignore
except ImportError:  # pragma: no cover - src layout fallback
    from agents.flyer.campaign_scene_prompts import campaign_scene_prompt_block, select_campaign_scene

import dataclasses

# Creative Director v2 (Slice B, B2.3) — propose+resolve a creative direction in
# _render_model and carry it on project.creative_direction. Flat on the VPS,
# package-style in the repo tree (mirrors the facts/scene-prompt imports above).
try:
    from flyer_context_builder import propose_creative_brief_v2  # type: ignore
    from flyer_creative_resolver import resolve_creative_direction  # type: ignore
    from flyer_brief import VisualDirection as _CDV2VisualDirection  # type: ignore
    from flyer_brief import FlyerBrief as _CDV2FlyerBrief  # type: ignore
except ImportError:  # pragma: no cover - src layout fallback
    from agents.flyer.flyer_context_builder import propose_creative_brief_v2
    from agents.flyer.flyer_creative_resolver import resolve_creative_direction
    from agents.flyer.flyer_brief import VisualDirection as _CDV2VisualDirection
    from agents.flyer.flyer_brief import FlyerBrief as _CDV2FlyerBrief

# FIX 4 (Codex MAJOR): the poster-archetype router is a Composition-Phase-1
# addition that may be ABSENT on a flat deploy that predates it (or rolled it
# back). It is imported in its OWN guarded block — independent of the CD v2
# chain above — with a fallback that yields the safe ``message_first`` default
# so render.py imports CLEANLY even when ``flyer_poster_archetype`` is missing.
# Without this, a missing module would propagate ImportError and crash render.py
# at import time, breaking flag-off + the entire flyer render path (not just the
# CD-v2 archetype feature). The fallback preserves legacy behavior: the carrier
# simply records ``message_first`` and _compose_mf is engaged only when the
# overlay also sees that archetype on a flag-on render.
try:
    from flyer_poster_archetype import select_poster_archetype  # type: ignore
except ImportError:  # pragma: no cover - src layout fallback
    try:
        from agents.flyer.flyer_poster_archetype import select_poster_archetype
    except ImportError:  # module genuinely absent on a flat/rolled-back deploy
        def select_poster_archetype(request_intent: str, offer_priority: str = "medium") -> str:  # type: ignore
            """Fallback archetype router (module absent): always the safe default."""
            return "message_first"


class FlyerRenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderedAssetSpec:
    path: Path
    kind: str
    output_format: str
    width: int
    height: int
    concept_id: str = ""


@dataclass(frozen=True)
class RenderedAssetQuality:
    ok: bool
    blockers: list[str]
    warnings: list[str]
    width: int | None = None
    height: int | None = None
    size_bytes: int = 0


@dataclass(frozen=True)
class FlyerTextFact:
    fact_id: str
    label: str
    text: str


@dataclass(frozen=True)
class FlyerTextQuality:
    ok: bool
    blockers: list[str]
    warnings: list[str]
    sidecar_path: Path


@dataclass(frozen=True)
class PosterCopyPlan:
    title: str
    schedule: str
    location: str
    contact: str
    items: list[tuple[str, str]]
    detail_lines: list[str]


PALETTES = {
    "C1": {"bg": [252, 244, 226], "primary": [130, 28, 42], "accent": [237, 171, 44], "ink": [39, 39, 39], "soft": [255, 255, 255]},
    "C2": {"bg": [238, 248, 246], "primary": [0, 106, 103], "accent": [230, 91, 63], "ink": [25, 43, 47], "soft": [255, 255, 255]},
    "C3": {"bg": [242, 241, 255], "primary": [54, 58, 122], "accent": [240, 111, 78], "ink": [30, 32, 50], "soft": [255, 255, 255]},
}

FOOD_CATEGORY_TERMS = {
    "restaurant", "grocery", "food", "catering", "menu", "breakfast", "lunch",
    "dinner", "kitchen", "bakery", "biryani", "dosa", "idli", "idly", "buffet",
    "meal", "combo", "sweet", "sweets", "snack", "snacks",
}
SALON_CATEGORY_TERMS = {
    "salon", "hair", "haircut", "perm", "perms", "blowdry", "beauty", "spa",
    "stylist", "barber", "nails", "makeup",
}
TAX_CATEGORY_TERMS = {"tax", "bookkeeping", "accounting", "payroll", "filing", "cpa"}
CLEANING_CATEGORY_TERMS = {"cleaning", "cleaner", "deep clean", "move-out", "maid"}
MARKETING_CATEGORY_TERMS = {"marketing", "seo", "paid ads", "content creation", "social media"}
INSTRUCTION_LEAK_PATTERNS = (
    re.compile(r"\b(?:create|make|generate|need)\s+(?:a\s+)?(?:flyer|flier|poster|banner)\b", re.IGNORECASE),
    re.compile(r"\b(?:flyer|flier|poster|banner)\s+for\b", re.IGNORECASE),
    re.compile(r"\bpromoting\s+the\s+-?\s*\$\s*\d", re.IGNORECASE),
)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "C:/Windows/Fonts/Nirmala.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

OPENROUTER_IMAGE_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT_SEC = 180
OPENROUTER_IMAGE_MAX_TOKENS = 4096
OPENAI_IMAGE_EDIT_URL = "https://api.openai.com/v1/images/edits"
OPENAI_IMAGE_EDIT_TIMEOUT_SEC = 180
def _flyer_state_root() -> Path:
    return Path(os.environ.get("FLYER_STATE_ROOT", "/opt/shift-agent/state/flyer/"))


def _customers_path() -> Path:
    return _flyer_state_root() / "customers.json"


CUSTOMERS_PATH = Path("/opt/shift-agent/state/flyer/customers.json")
DETERMINISTIC_MODEL_NAMES = {"", "deterministic-renderer", "pillow", "local-pillow"}

# Context-var gate: set to True inside _render_model when force_background_only=True.
# Every _background_only_eligible-gated site (including _integrated_poster_eligible,
# which _background_only_eligible delegates to) honours this automatically — no
# per-helper threading needed.
_FORCE_BACKGROUND_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "flyer_force_background_only", default=False
)


@dataclass
class PremiumOverlayOutcome:
    """How a single premium-enabled render resolved (set on a ContextVar, read
    by the generate-flyer-concepts chokepoint). status/reason_class map 1:1 to
    the FlyerPremiumOverlayOutcome audit variant."""
    status: str          # premium_overlay_delivered | premium_overlay_degraded_to_flat | premium_overlay_failed_unexpected
    reason_class: str    # none|fit|coverage|overflow|missing_pil|import_error|subprocess_failure|runtime_exception|serialization_error
    reason_detail: str
    render_path: str     # in_process | subprocess | none
    output_format: str


_PREMIUM_OVERLAY_OUTCOME: contextvars.ContextVar[PremiumOverlayOutcome | None] = contextvars.ContextVar(
    "flyer_premium_overlay_outcome", default=None
)


def consume_premium_overlay_outcome() -> PremiumOverlayOutcome | None:
    """Return the most-recent premium render outcome and clear it, so a later
    render that does NOT run the premium overlay cannot inherit a stale value."""
    outcome = _PREMIUM_OVERLAY_OUTCOME.get()
    _PREMIUM_OVERLAY_OUTCOME.set(None)
    return outcome


def premium_outcome_should_alert(outcome: PremiumOverlayOutcome | None) -> bool:
    """Page the operator only for unrecovered, unexpected premium failures.
    Intentional fail-closed (fit/coverage/overflow) is normal product behavior."""
    return bool(outcome) and outcome.status == "premium_overlay_failed_unexpected"


# ── Premium Poster v1 — render-path opt-in + outcome telemetry ───────────────
# The premium-poster-v1 render branch fires ONLY when the CURRENT render is opted
# in by the orchestrating render path. The opt-in carries the PATH IDENTITY
# (``"bare"`` = WhatsApp-direct, ``"managed"`` = studio/owner-review) so
# observability can attribute each fire to its path. A render with NO opt-in
# (``None``) never enters the premium branch, so any path — and any recovery /
# fallback / rung-ladder re-render that does not explicitly opt in — stays
# byte-identical.
_PREMIUM_POSTER_V1_PATH: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "flyer_premium_poster_v1_path", default=None
)


@contextlib.contextmanager
def _premium_poster_v1_opt_in(path_id: str):
    """Opt the current render into the Premium Poster v1 branch under ``path_id``.
    The token-based reset guarantees the path identity is scoped to exactly the
    wrapped render call and can never leak into a later render (or a nested
    recovery re-render outside the ``with``)."""
    token = _PREMIUM_POSTER_V1_PATH.set(path_id)
    try:
        yield
    finally:
        _PREMIUM_POSTER_V1_PATH.reset(token)


@contextlib.contextmanager
def premium_poster_v1_bare_path():
    """Opt the current render into the Premium Poster v1 branch, tagged as the
    bare/WhatsApp-direct path."""
    with _premium_poster_v1_opt_in("bare"):
        yield


@contextlib.contextmanager
def premium_poster_v1_managed_path():
    """Opt the current render into the Premium Poster v1 branch, tagged as the
    managed/studio (owner-review) path. Wrap ONLY the primary preview render in
    generate-flyer-concepts — NEVER the deterministic fallback, brand retry,
    premium repair, deterministic recovery, legacy ladder, or source-edit
    renders (each of those is a separate render call outside this scope)."""
    with _premium_poster_v1_opt_in("managed"):
        yield


def _premium_poster_v1_opt_in_path() -> str | None:
    """The path identity (``"bare"`` | ``"managed"``) opted into the premium
    branch for the CURRENT render, or ``None`` when no path opted in (the branch
    is not entered → byte-identical legacy render)."""
    return _PREMIUM_POSTER_V1_PATH.get()


@dataclass
class PremiumPosterV1Outcome:
    """How a Premium Poster v1 render resolved (set on a ContextVar; consumed by
    the managed emitter in generate-flyer-concepts AND the bare-path emitter in
    bare_render._consume_and_emit_premium_poster_v1_bare — both write
    decisions.log rows). ``delivered`` is True iff a valid poster was written to
    the target; any other outcome means the caller fell through to the existing
    render path."""
    delivered: bool
    status: str            # delivered | fallback | skipped
    reason: str            # none | unsupported_size | no_winner[:<status>=<count>,…]
    #                      # | no_food_winner[:…] | exception:<T>:<msg-head>
    n: int
    winner_index: int
    winner_composite: float | None
    output_format: str


_PREMIUM_POSTER_V1_OUTCOME: contextvars.ContextVar[PremiumPosterV1Outcome | None] = contextvars.ContextVar(
    "flyer_premium_poster_v1_outcome", default=None
)


def consume_premium_poster_v1_outcome() -> PremiumPosterV1Outcome | None:
    """Return the most-recent premium-poster-v1 outcome and clear it (so a later
    render that does NOT run the premium branch cannot inherit a stale value)."""
    outcome = _PREMIUM_POSTER_V1_OUTCOME.get()
    _PREMIUM_POSTER_V1_OUTCOME.set(None)
    return outcome


TEXT_MANIFEST_SCHEMA_VERSION = 1
# Total critical text facts (menu items + offer/pricing/promo clauses) that fit one
# flyer legibly. The binding output is the square 1080x1080 Instagram post in the final
# package, which holds ~10 menu rows; the taller preview/PDF fit more, but a flyer that
# can't render at every delivered size must route to manual, not ship a partial set.
MAX_DETAIL_FACTS = 10
MAX_TEXT_FACTS = 16
# Explicit customer-supplied item/price menus can use the compact deterministic menu
# overlay. Keep this separate from planner/inferred menus: customer truth may be dense,
# but generated suggestions should not force tiny menu posters.
MAX_COMPACT_MENU_DETAIL_FACTS = 18
MAX_COMPACT_MENU_TEXT_FACTS = 30
# A generated background-only composite writes its raw background and overlaid
# preview together (sub-second). A preview newer than its raw by more than this
# window was edited/regenerated apart from the raw → final export honors the
# preview directly rather than rebuilding from a possibly-stale raw.
_RAW_COMPOSITE_FRESH_SECONDS = 30
MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
MONTH_NUMBER_TO_NAME = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


def _require_ready(project: FlyerProject) -> None:
    missing = project.fields.missing_required_fields()
    if missing:
        raise FlyerRenderError("missing required flyer fields: " + ", ".join(missing))


def _load_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
        return Image, ImageDraw, ImageFont
    except Exception:
        return None


def _has_telugu(text: str) -> bool:
    return any("\u0c00" <= ch <= "\u0c7f" for ch in text or "")


def _has_regional_script(text: str) -> bool:
    return any(
        ("\u0900" <= ch <= "\u0d7f")
        or ("\u0a00" <= ch <= "\u0a7f")
        for ch in text or ""
    )


def _font(ImageFont, size: int, *, bold: bool = False, text: str = ""):
    candidates = list(FONT_CANDIDATES)
    if _has_telugu(text):
        candidates.insert(0, "C:/Windows/Fonts/Nirmala.ttf")
        candidates.insert(0, "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf")
    if bold:
        candidates.insert(0, "C:/Windows/Fonts/arialbd.ttf")
        candidates.insert(0, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    for path in candidates:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _read_env_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    candidates = [
        Path(os.environ.get("HERMES_ENV_PATH", "/root/.hermes/.env")),
        Path(os.environ.get("SHIFT_AGENT_ENV_PATH", "/opt/shift-agent/.env")),
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                if key.strip() != name:
                    continue
                extracted = raw.strip().strip('"').strip("'")
                if extracted:
                    return extracted
        except OSError:
            continue
    return ""


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) == 1 and len(lines[0]) > 28:
        return textwrap.wrap(lines[0], width=28)
    return lines


def _aspect_ratio(size: tuple[int, int] | None) -> str:
    if size is None:
        return "4:5"
    width, height = size
    if width == height:
        return "1:1"
    ratio = width / height
    known = {
        "4:5": 4 / 5,
        "9:16": 9 / 16,
        "2:3": 2 / 3,
        "3:4": 3 / 4,
    }
    return min(known, key=lambda key: abs(known[key] - ratio))


def _telugu_hint(project: FlyerProject) -> str:
    if project.fields.preferred_language not in {"te", "mixed"}:
        return ""
    name = project.fields.event_or_business_name or ""
    hints = [
        "Use Telugu as the primary flyer language for the headline, section labels, schedule/contact labels, and call-to-action.",
        "Do not output an all-English flyer when the customer selected Telugu.",
        "Keep item names, brand names, and prices exactly readable; if source item names are in English, preserve those names/prices and use Telugu-first supporting labels.",
    ]
    if "ugadi" in name.lower():
        hints.append("Use tasteful Telugu script such as \"ఉగాది శుభాకాంక్షలు\" as an accent, while keeping the main title readable.")
    hints.append("Do not render missing-glyph boxes. If Telugu text is used, it must be valid Telugu script.")
    return " ".join(hints)


def _language_constraint_hint(project: FlyerProject) -> str:
    text = f"{project.raw_request or ''} {project.fields.notes or ''}".lower()
    english_only = bool(
        re.search(r"\b(?:language\s*:\s*)?english\s+only\b", text)
        or re.search(r"\b(?:do\s+not|don't|dont|no)\s+use\s+(?:telugu|hindi|tamil|malayalam|kannada|gujarati|marathi|punjabi|regional)", text)
        or "no regional indian language" in text
        or "no regional languages" in text
    )
    if english_only:
        return (
            "Use English only. Do not use Telugu, Hindi, or any regional Indian language. "
            "Do not add non-English script accents."
        )
    return _telugu_hint(project)


def _sanitize_visual_context(text: str) -> str:
    text = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[phone]", text or "")
    text = re.sub(r"\$\s*\d+(?:\.\d{2})?", "[price]", text)
    text = re.sub(r"\b\d{1,4}(?:[-/:]\d{1,4}){1,2}\b", "[date/time]", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "[number]", text)
    return text[:700]


def _schedule_hint(project: FlyerProject) -> str:
    text = project.fields.notes.strip() or project.raw_request.strip()
    if not text:
        return ""
    time_match = re.search(
        r"\b(?:timings?|time)\s*:?\s*(\d{1,2}(?::\d{2})?\s*(?:AM|PM)\s*(?:to|-)\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM))",
        text,
        flags=re.IGNORECASE,
    )
    if not time_match:
        time_match = re.search(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM)\s*(?:to|-)\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM))",
            text,
            flags=re.IGNORECASE,
        )
    days_match = re.search(
        r"\b((?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*(?:to|through|-)\s*(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|weekends?|weekdays?|daily)\b",
        text,
        flags=re.IGNORECASE,
    )
    recurring_days_match = re.search(
        r"\b(?P<first>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+and\s+"
        r"(?P<second>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+"
        r"(?:of\s+)?every\s+week\b",
        text,
        flags=re.IGNORECASE,
    )
    if time_match and days_match:
        return f"{days_match.group(1).title()} | {time_match.group(1).upper()}"
    if time_match:
        return time_match.group(1).upper()
    if recurring_days_match:
        first = recurring_days_match.group("first").title()
        second = recurring_days_match.group("second").title()
        return f"{first} and {second} every week"
    single_recurring_day_match = re.search(
        r"\b(?P<day>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+(?:of\s+)?every\s+week\b",
        text,
        flags=re.IGNORECASE,
    )
    if single_recurring_day_match:
        return f"{single_recurring_day_match.group('day').title()} every week"
    schedule_match = re.search(
        r"((?:starts?|starting)\s+from\s+.+?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend).+?)(?:\.|$)",
        text,
        flags=re.IGNORECASE,
    )
    if schedule_match:
        return schedule_match.group(1).strip(" .")
    recurring_match = re.search(
        r"((?:daily|weekdays|weekends|every\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend)|mon(?:day)?\s*-\s*fri(?:day)?).{0,80})(?:\.|$)",
        text,
        flags=re.IGNORECASE,
    )
    if recurring_match:
        return recurring_match.group(1).strip(" .")
    return ""


def _display_schedule(project: FlyerProject) -> str:
    return fact_value(project, "schedule", fallback=_schedule_hint(project)).strip()


def _schedule_includes_time_range(schedule: str) -> bool:
    if not schedule:
        return False
    return bool(
        re.search(
            r"\b\d{1,2}(?::\d{2})?\s*(?:AM|PM)\s*(?:TO|-)\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM)\b",
            schedule,
            flags=re.IGNORECASE,
        )
    )


def _normalize_fact_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _text_manifest_path(path: Path | str) -> Path:
    return Path(f"{Path(path)}.text.json")


def _clean_fact_text(text: str, *, max_len: int = 180) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip(" ."))
    if len(clean) > max_len:
        raise FlyerRenderError("critical text facts do not fit")
    return clean


def _price_or_phone_clause(text: str) -> bool:
    return bool(
        re.search(r"\$\s*\d+(?:\.\d{1,2})?", text)
        or re.search(r"\b\d+(?:\.\d{1,2})?\s*/\s*(?:piece|pc|lb|pcs)\b", text, flags=re.IGNORECASE)
        or re.search(r"\+?\d[\d\s().-]{7,}\d", text)
        or re.search(r"\b\d+(?:\.\d+)?\s*%\b", text)
        or re.search(
            r"\b(?:buy\s+one\s+get\s+one|bogo|free|sale|discount|off|dine-?in|take\s*away|takeaway)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _digits(text: str) -> str:
    return re.sub(r"\D+", "", text or "")


def _phones_in_text(text: str) -> list[str]:
    return [
        _digits(match.group(0))
        for match in re.finditer(r"\+?\d[\d\s().-]{7,}\d", text or "")
    ]


def _strip_request_instruction_prefix(text: str) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    clean = re.sub(
        r"^\s*(?:hey!?\s*)?(?:please\s+)?(?:create|make|generate|need)\s+(?:a\s+)?(?:flyer|flier|poster|banner)\s+for\s+[^.;:]{2,100}?\s+\b(?:promoting|offering|featuring|advertising|announcing)\b\s+(?:the\s+)?",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"^\s*(?:hey!?\s*)?(?:please\s+)?(?:create|make|generate|need)\s+(?:a\s+)?[^.;:]{0,140}?\b(?:flyer|flier|poster|banner)\b\s*(?:,?\s*(?:which\s+must\s+include|with\s+these\s+items|including|include)\s*)?",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    return clean.strip(" .")


def _request_asks_to_include_line_copy(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:include|show|mention|add)\s+(?:the\s+)?(?:below|following|details?)\b",
            text or "",
            flags=re.IGNORECASE,
        )
    )


def _instruction_only_clause(text: str) -> bool:
    return bool(
        re.search(r"\b(?:flyer|flier|poster|banner)\b", text or "", flags=re.IGNORECASE)
        and re.search(
            r"\b(?:theme|reflect|include|below|following|details?)\b",
            text or "",
            flags=re.IGNORECASE,
        )
    )


def _matches_project_primary_fact(project: FlyerProject, text: str) -> bool:
    candidates = [
        _display_title(project),
        fact_value(project, "business_name", fallback=project.fields.event_or_business_name) or "",
        fact_value(project, "location", fallback=project.fields.venue_or_location) or "",
        fact_value(project, "contact_phone", fallback=project.fields.contact_info) or "",
        fact_value(project, "promotion_end", fallback="") or "",
    ]
    if project.fields.event_date:
        candidates.append(_display_date_text(project))
    schedule = _display_schedule(project)
    if schedule:
        candidates.append(schedule)
    return any(_same_text(text, candidate) for candidate in candidates if candidate)


def _instruction_leak_blockers(facts: list[FlyerTextFact]) -> list[str]:
    blockers: list[str] = []
    for fact in facts:
        for pattern in INSTRUCTION_LEAK_PATTERNS:
            if pattern.search(fact.text or ""):
                blockers.append(
                    f"instruction text leaked into flyer copy: {fact.label}={fact.text}"
                )
                break
    return blockers


def _detail_clauses(project: FlyerProject) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    menu_items = _menu_item_lines(project)
    menu_prices = {
        re.sub(r"\s+", "", price)
        for item in menu_items
        for _, price in [_split_item_price(item)]
        if price
    }

    def is_menu_item_aggregate(value: str) -> bool:
        if not menu_items:
            return False
        price_occurrences = [
            re.sub(r"\s+", "", match.group(0))
            for match in re.finditer(r"\$\s*\d+(?:\.\d{1,2})?", value or "")
        ]
        if len(price_occurrences) < 2 or not set(price_occurrences).issubset(menu_prices):
            return False
        normalized = _normalize_fact_text(value)
        return any(
            _normalize_fact_text(_split_item_price(item)[0]) in normalized
            for item in menu_items
        )

    def add_detail(value: str) -> None:
        value = _clean_fact_text(value)
        if not value:
            return
        if is_menu_item_aggregate(value):
            return
        normalized = _normalize_fact_text(value)
        if normalized in seen:
            return
        seen.add(normalized)
        selected.append(value)

    for fact in project.locked_facts:
        if fact.fact_id == "pricing_structure" and str(fact.value or "").strip():
            add_detail(str(fact.value))
        elif fact.fact_id.startswith("offer:") and str(fact.value or "").strip():
            add_detail(str(fact.value))
        elif fact.fact_id == "promotion_end" and str(fact.value or "").strip():
            add_detail(f"Promotion end: {fact.value}")

    details = (project.fields.notes or project.raw_request or "").strip()
    if not details:
        return selected
    line_copy_requested = _request_asks_to_include_line_copy(details)
    compact = re.sub(r"[ \t\r\f\v]+", " ", details)
    # Vertical-bar separator glyphs split clauses like newlines do. Labeled
    # failure (3 exhibits, 2026-07-03 F0201): a brief copied from a chat
    # blockquote carried U+258E bars instead of newlines; without these split
    # points the ENTIRE brief survived as one mined clause and was painted
    # verbatim as the poster subhead (the "tofu boxes" were the literal bars).
    clauses = [part.strip(" .") for part in re.split(r";|\n+|\u2022|\u258e|\u258f|\u2502|\u2503|\|+|-{2,}|(?<=\.)\s+", compact) if part.strip(" .")]
    # Restatement guard: a mined clause echoing >=2 distinct locked
    # customer_text fact values is the brief restated, not a new detail —
    # brief text is model context, never poster copy.
    _locked_norms = {
        _normalize_fact_text(str(fact.value))
        for fact in project.locked_facts
        if fact.source == "customer_text" and str(fact.value or "").strip()
    }

    def _is_restatement(clause: str) -> bool:
        # DISTINCT fact values (set — PR #542 review F1: four items sharing one
        # "$5.99" must count once, not four times, or "Kids eat for $5.99 on
        # Mondays" gets dropped) with a trailing-digit boundary (F2: "$3" must
        # not match inside "$30").
        norm = _normalize_fact_text(clause)
        hits = 0
        for v in _locked_norms:
            if v and re.search(rf"(?<![\w.]){re.escape(v)}(?![\d.])", norm):
                hits += 1
                if hits >= 2:
                    return True
        return False
    current_contact_digits = _digits(project.fields.contact_info or "")
    for clause in clauses:
        clause = _strip_request_instruction_prefix(clause)
        if clause and _is_restatement(clause):
            continue
        if not clause:
            continue
        if _instruction_only_clause(clause):
            continue
        if _matches_project_primary_fact(project, clause):
            continue
        if not (_price_or_phone_clause(clause) or line_copy_requested):
            continue
        phones = _phones_in_text(clause)
        has_offer_or_price = bool(
            re.search(r"\$\s*\d+(?:\.\d{1,2})?", clause)
            or re.search(r"\b\d+(?:\.\d+)?\s*%\b", clause)
            or re.search(
                r"\b(?:buy\s+one\s+get\s+one|bogo|free|sale|discount|off|dine-?in|take\s*away|takeaway)\b",
                clause,
                flags=re.IGNORECASE,
            )
        )
        clause_prices = {
            re.sub(r"\s+", "", match.group(0))
            for match in re.finditer(r"\$\s*\d+(?:\.\d{1,2})?", clause)
        }
        if (
            menu_items
            and clause_prices
            and clause_prices.issubset(menu_prices)
            and (
                re.search(r"\b(?:add|set|use)\s+price\s+as\b|\bprice\s+as\b", clause, flags=re.IGNORECASE)
                or is_menu_item_aggregate(clause)
                or re.search(r"\b(?:all|any|every|each)\b[^.;,\n]{0,40}\$\s*\d", clause, flags=re.IGNORECASE)
            )
        ):
            continue
        if phones and current_contact_digits and current_contact_digits in phones and not has_offer_or_price:
            continue
        if phones and current_contact_digits and all(phone != current_contact_digits for phone in phones):
            continue
        # A run-on free-text clause with no sentence breaks can exceed one detail line's
        # legible capacity (`_clean_fact_text`'s limit). The 2026-06-06 graduation request
        # arrived as one 197-char clause because `fields.notes` is newline-flattened, so the
        # newline split above could not separate it. Its offer/price content is already locked
        # as structured offer:/pricing_structure facts (added at the top of this function), so
        # skip the redundant over-long *supplementary* clause instead of failing the whole
        # render. Structured facts (above) and menu items (below) keep their hard fail-closed.
        try:
            add_detail(clause)
        except FlyerRenderError:
            continue
    for item in menu_items:
        add_detail(item)
    # Fail closed when the combined critical facts exceed one flyer's legible capacity.
    # The menu-line helpers deliberately do NOT truncate, so an over-long menu reaches
    # this guard and routes to manual instead of silently dropping items 11+.
    detail_cap = (
        MAX_COMPACT_MENU_DETAIL_FACTS
        if _compact_menu_overlay_allowed(project, menu_items)
        else MAX_DETAIL_FACTS
    )
    if len(selected) > detail_cap:
        raise FlyerRenderError("critical text facts do not fit")
    return selected


def _display_date_text(project: FlyerProject) -> str:
    """Return the customer-visible date text, preserving simple two-day ranges."""
    date_value = project.fields.event_date or ""
    if not date_value:
        return ""
    text = project.fields.notes or project.raw_request or ""
    year = date_value[:4]
    month_names = {name: num for name, num in MONTH_NAME_TO_NUMBER.items()}
    month_pattern = "|".join(sorted(month_names, key=len, reverse=True))
    same_month = re.search(
        rf"\b({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:and|&|to|-|through)\s*(\d{{1,2}})(?:st|nd|rd|th)?\b",
        text,
        flags=re.IGNORECASE,
    )
    if same_month:
        month_raw = same_month.group(1)
        month_num = month_names[month_raw.lower()]
        month_label = MONTH_NUMBER_TO_NAME[month_num]
        day_one = int(same_month.group(2))
        day_two = int(same_month.group(3))
        return f"{month_label} {day_one} and {month_label} {day_two}, {year}"
    numeric_pair = re.search(
        r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\s*(?:and|&|to|-|through)\s*(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b",
        text,
        flags=re.IGNORECASE,
    )
    if numeric_pair:
        first_month = int(numeric_pair.group(1))
        first_day = int(numeric_pair.group(2))
        second_month = int(numeric_pair.group(3))
        second_day = int(numeric_pair.group(4))
        first_label = MONTH_NUMBER_TO_NAME.get(first_month, f"{first_month:02d}")
        second_label = MONTH_NUMBER_TO_NAME.get(second_month, f"{second_month:02d}")
        return f"{first_label} {first_day} and {second_label} {second_day}, {year}"
    return date_value


def _menu_item_lines(project: FlyerProject) -> list[str]:
    locked_items = _locked_menu_item_lines(project)
    if locked_items:
        return locked_items
    if any(
        fact.fact_id == "pricing_structure" or fact.fact_id.startswith("offer:")
        for fact in project.locked_facts
    ):
        return []
    text = (project.fields.notes or project.raw_request or "").strip()
    if not text:
        return []
    body = _strip_request_instruction_prefix(text)
    explicit_item_list = False
    match = re.search(
        r"\bitems?\b\s*(?:to include in the flyer\s*)?\s*:?\s*[\"“”']?(.+?)(?:[\"“”']?\s*\.\s*(?:timings?|time)\b|[\"“”']?\s*$)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        explicit_item_list = True
        body = _strip_request_instruction_prefix(match.group(1))
    pairs = re.findall(
        r"([A-Za-z][A-Za-z '&/-]{1,60}?)\s*\$\s*(\d+(?:\.\d{1,2})?)",
        body,
    )
    items: list[str] = []
    seen: set[str] = set()
    for name, price in pairs:
        clean_name = re.sub(r"^\s*(?:and|,)\s*", "", name).strip(" ,.-\"'")
        clean_name = re.sub(r"\s+", " ", clean_name)
        if len(clean_name) < 2:
            continue
        line = f"{clean_name} ${price}"
        key = _normalize_fact_text(line)
        if key in seen:
            continue
        seen.add(key)
        items.append(line)
    price_first_pairs = re.findall(
        r"\$\s*(\d+(?:\.\d{1,2})?)\s*([A-Za-z][A-Za-z '&/-]{1,60}?)(?=,|;|\.|\band\b|\$|$)",
        body,
        flags=re.IGNORECASE,
    )
    for price, name in price_first_pairs:
        clean_name = re.sub(r"^\s*(?:and|the)\s+", "", name, flags=re.IGNORECASE).strip(" ,.-\"'")
        clean_name = re.sub(r"\s+", " ", clean_name)
        if len(clean_name) < 2:
            continue
        line = f"{clean_name} ${price}"
        key = _normalize_fact_text(line)
        if key in seen:
            continue
        seen.add(key)
        items.append(line)
    if not items and explicit_item_list:
        for token in re.split(r",|;|\n|•", body):
            clean_name = re.sub(r"^\s*(?:and|include|items?)\s*:?\s*", "", token, flags=re.IGNORECASE)
            clean_name = re.sub(r"\s+", " ", clean_name).strip(" ,.-\"':")
            if not clean_name or len(clean_name) < 2:
                continue
            if re.search(r"\$|\d", clean_name):
                continue
            if len(clean_name.split()) > 5 or len(clean_name) > 48:
                continue
            key = _normalize_fact_text(clean_name)
            if key in seen:
                continue
            seen.add(key)
            items.append(clean_name)
    if _context_has(_category_context(project), SALON_CATEGORY_TERMS) and "other hair services" in body.lower():
        line = "Other hair services available"
        key = _normalize_fact_text(line)
        if key not in seen:
            seen.add(key)
            items.append(line)
    # Return every parsed item — do NOT truncate. An over-long menu must reach the
    # fail-closed cap in _detail_clauses (or the overlay draw guard) and route to
    # manual, never silently drop the items past the cap.
    return items


def _locked_menu_item_lines(project: FlyerProject) -> list[str]:
    grouped: dict[int, dict[str, str]] = {}
    order: list[int] = []
    allow_inferred_items = requests_generated_item_suggestions(
        " ".join(str(value or "") for value in (project.raw_request, project.fields.notes))
    )
    for fact in project.locked_facts:
        match = re.match(r"^item:(\d+):(name|price)$", fact.fact_id)
        if not match:
            continue
        if getattr(fact, "source", "") == "hermes_inferred" and not allow_inferred_items:
            continue
        index = int(match.group(1))
        if index not in grouped:
            grouped[index] = {}
            order.append(index)
        grouped[index][match.group(2)] = _clean_fact_text(fact.value)
    items: list[str] = []
    seen: set[str] = set()
    for index in order:
        name = grouped[index].get("name", "")
        price = grouped[index].get("price", "")
        if not name:
            continue
        line = f"{name} {price}".strip()
        key = _normalize_fact_text(line)
        if key in seen:
            continue
        seen.add(key)
        items.append(line)
    # Return every locked item — do NOT truncate (see _menu_item_lines): overflow must
    # fail closed downstream and route to manual, never silently drop locked items.
    return items


def _compact_menu_overlay_allowed(project: FlyerProject, menu_items: list[str] | None = None) -> bool:
    """Allow a denser deterministic overlay only for grounded customer/source menus.

    Planner-generated names (`source=hermes_inferred`) keep the old ten-row fail-closed
    contract; they are suggestions, not customer truth. The production dessert case is
    the opposite: every item/price pair came from customer text and must be shown.
    """
    items = menu_items if menu_items is not None else _menu_item_lines(project)
    if not items or len(items) > MAX_COMPACT_MENU_DETAIL_FACTS:
        return False
    structured_extra_count = sum(
        1
        for fact in project.locked_facts
        if str(getattr(fact, "value", "") or "").strip()
        and (
            fact.fact_id == "pricing_structure"
            or fact.fact_id == "promotion_end"
            or fact.fact_id.startswith("offer:")
        )
    )
    if len(items) <= MAX_DETAIL_FACTS and len(items) + structured_extra_count <= MAX_DETAIL_FACTS:
        return False
    grouped: dict[int, dict[str, str]] = {}
    for fact in project.locked_facts:
        match = re.match(r"^item:(\d+):(name|price)$", fact.fact_id)
        if not match or not str(fact.value or "").strip():
            continue
        grouped.setdefault(int(match.group(1)), {})[match.group(2)] = getattr(fact, "source", "")
    named = [index for index in sorted(grouped) if grouped[index].get("name")]
    if len(named) != len(items):
        return False
    return all(
        all(source and source != "hermes_inferred" for source in grouped[index].values())
        for index in named
    )


def _same_text(left: str, right: str) -> bool:
    norm_left = re.sub(r"[^a-z0-9]+", " ", (left or "").lower()).strip()
    norm_right = re.sub(r"[^a-z0-9]+", " ", (right or "").lower()).strip()
    return bool(norm_left and norm_left == norm_right)


_INSTRUCTION_TITLE_TOKENS = {
    "create",
    "make",
    "generate",
    "design",
    "need",
    "new",
    "flyer",
    "flier",
    "poster",
    "banner",
    "multiple",
    "multi",
    "page",
    "pages",
    "single",
    "please",
}


def _instruction_title_fragment(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
    if not normalized:
        return False
    tokens = normalized.split()
    return bool(tokens) and all(token in _INSTRUCTION_TITLE_TOKENS for token in tokens)


def _display_title(project: FlyerProject) -> str:
    business = fact_value(project, "business_name", fallback="")
    for value in (
        fact_value(project, "campaign_title", fallback=""),
        fact_value(project, "headline", fallback=""),
        project.fields.event_or_business_name or "",
    ):
        clean = _clean_fact_text(value)
        if clean and not _same_text(clean, business) and not _instruction_title_fragment(clean):
            return clean
    return "Specials"


def collect_text_facts(project: FlyerProject) -> list[FlyerTextFact]:
    facts: list[FlyerTextFact] = []

    def add(fact_id: str, label: str, text: str) -> None:
        clean = _clean_fact_text(text)
        if clean:
            facts.append(FlyerTextFact(fact_id=fact_id, label=label, text=clean))

    business_text = fact_value(project, "business_name", fallback="")
    if business_text:
        add("brand", "Business", business_text)
    title_text = _display_title(project)
    add("title", "Title", title_text)
    schedule = _display_schedule(project)
    if project.fields.event_date:
        add("date", "Date", _display_date_text(project))
    elif schedule:
        add("schedule", "Schedule", schedule)
    if project.fields.event_time and not _schedule_includes_time_range(schedule):
        add("time", "Time", project.fields.event_time)
    location_text = fact_value(project, "location", fallback=project.fields.venue_or_location)
    if location_text:
        add("location", "Location", location_text)
    contact_text = fact_value(project, "contact_phone", fallback=project.fields.contact_info)
    if contact_text:
        add("contact", "Contact", contact_text)
    promotion_end_text = fact_value(project, "promotion_end", fallback="")
    if promotion_end_text:
        add("promotion_end", "Promotion end", promotion_end_text)
    for idx, clause in enumerate(_detail_clauses(project), start=1):
        add(f"detail_{idx:03d}", "Detail", clause)
    text_cap = (
        MAX_COMPACT_MENU_TEXT_FACTS
        if _compact_menu_overlay_allowed(project)
        else MAX_TEXT_FACTS
    )
    if len(facts) > text_cap:
        raise FlyerRenderError("critical text facts do not fit")
    fact_ids = [fact.fact_id for fact in facts]
    if len(fact_ids) != len(set(fact_ids)):
        raise FlyerRenderError("duplicate critical text fact ids")
    return facts


def _fact_lines(project: FlyerProject) -> list[str]:
    lines: list[str] = []
    for fact in collect_text_facts(project):
        if fact.fact_id == "title":
            lines.append(fact.text)
        else:
            lines.append(f"{fact.label}: {fact.text}")
    return lines


def _shared_price_offer_parts(details: list[str]) -> tuple[str, str, str]:
    for detail in details:
        match = re.search(
            r"^(?P<label>(?:any|all|every|each)\b.{0,80}?)\s+(?P<price>\$\s*\d+(?:\.\d{1,2})?)\s*$",
            detail,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        label = _clean_fact_text(match.group("label"))
        price = re.sub(r"\s+", "", match.group("price"))
        if label and price:
            return f"{label} {price}", label, price
    return "", "", ""


def _menu_overlay_payload(project: FlyerProject) -> dict[str, object]:
    # P0-2: must mirror collect_text_facts() so the drawn image matches the
    # text manifest S5 will OCR-validate against. Same locked-fact preference
    # for title/location/contact.
    items = _menu_item_lines(project)
    schedule = _display_schedule(project)
    # Required visible facts the menu item-cards don't show (offers, promotion
    # end, pricing structure) — drawn in the title card so visual QA finds every
    # required fact, not just items. Exclude anything already shown as a menu item.
    item_norms = {_normalize_fact_text(i) for i in items}
    extras = [c for c in _detail_clauses(project) if _normalize_fact_text(c) not in item_norms]
    shared_offer_text, shared_offer_label, shared_offer_price = _shared_price_offer_parts(extras)
    return {
        "business": _display_business_name(project),
        "title": _display_title(project),
        "schedule": schedule,
        "items": items,
        "extras": extras,
        "shared_offer_text": shared_offer_text,
        "shared_offer_label": shared_offer_label,
        "shared_offer_price": shared_offer_price,
        "location": fact_value(project, "location", fallback=project.fields.venue_or_location) or "",
        "contact": fact_value(project, "contact_phone", fallback=project.fields.contact_info) or "",
    }


def _poster_copy_plan(project: FlyerProject) -> PosterCopyPlan:
    # P0-2: must mirror collect_text_facts() — same locked-fact preference for
    # title/location/contact so the OpenRouter prompt's "Render the following
    # text exactly" block names the same values the text manifest expects.
    items: list[tuple[str, str]] = []
    for item in _menu_item_lines(project):
        name, price = _split_item_price(item)
        items.append((name, price))
    menu_item_text = {f"{name} {price}".strip() for name, price in items}
    detail_lines = []
    for detail in _detail_clauses(project):
        if detail in menu_item_text:
            continue
        detail_lines.append(detail)
    return PosterCopyPlan(
        title=_display_title(project),
        schedule=_display_schedule(project),
        location=fact_value(project, "location", fallback=project.fields.venue_or_location) or "",
        contact=fact_value(project, "contact_phone", fallback=project.fields.contact_info) or "",
        items=items,
        detail_lines=detail_lines,
    )


def _poster_copy_block(project: FlyerProject, *, force_background_only: bool = False) -> str:
    plan = _poster_copy_plan(project)
    if _background_only_eligible(project) or force_background_only:
        lines = [
            "Flyer facts (for theme/imagery relevance ONLY — do NOT render them as text, words, "
            "menu lists, headlines, or price tags in the image; the system composites all exact "
            "text into overlay panels afterwards). Use them only to pick relevant, accurate imagery and mood.",
            "Title is the campaign/product/service headline; Business/brand is the account identity.",
            "Do not invent delivery, catering, payment, ordering-channel, or service-availability claims.",
        ]
    else:
        # Localized / reference-extraction flows: the overlay can't produce this
        # text, so the model must render it exactly.
        lines = [
            "Render the following text exactly. Do not summarize, paraphrase, invent, or omit these customer facts.",
            "Title is the campaign/product/service headline; Business/brand is the account identity.",
            "Do not add delivery, catering, payment, ordering-channel, or service-availability claims unless they appear below.",
            "Do not add secondary brand names, business category subtitles, taglines, slogans, freshness/availability claims, or extra promotional copy unless they appear below.",
        ]
    business_name = _display_business_name(project)
    if business_name:
        lines.append(f"Business/brand: {business_name}")
    lines.append(f"Title: {plan.title}")
    if plan.schedule:
        lines.append(f"Schedule: {plan.schedule}")
    elif project.fields.event_date:
        lines.append(f"Date: {_display_date_text(project)}")
    if project.fields.event_time and not _schedule_includes_time_range(plan.schedule or ""):
        lines.append(f"Time: {project.fields.event_time}")
    if plan.location:
        lines.append(f"Location: {plan.location}")
    if plan.contact:
        lines.append(f"Contact: {plan.contact}")
    if plan.items:
        if force_background_only or _FORCE_BACKGROUND_ONLY.get() or getattr(project, "deterministic_recovery", False):
            _names = ", ".join(name for name, _price in plan.items)
            lines.append(
                "Menu items (use ONLY to inform relevant background imagery and mood — do NOT "
                f"draw them as text, menu cards, lists, or price tags): {_names}"
            )
        else:
            lines.append(f"Menu items to feature - exactly {len(plan.items)} items:")
            lines.append(f"Create exactly {len(plan.items)} menu item cards. Each listed item must appear once and only once; use the exact item name and do not duplicate any item.")
            for name, price in plan.items:
                if price:
                    lines.append(f"- {name} - {price}")
                else:
                    lines.append(f"- {name}")
    if plan.detail_lines:
        lines.append("Offer details:")
        for detail in plan.detail_lines:
            lines.append(f"- {detail}")
    if not (_background_only_eligible(project) or force_background_only):
        # Only the integrated-text path renders these facts itself; the legibility
        # guidance is contradictory under the background-only (textless) contract.
        lines.append("If any required text cannot be rendered legibly, make the typography simpler and larger rather than dropping facts.")
    return "\n".join(lines)


def _request_asks_reference_extraction(project: FlyerProject) -> bool:
    """The request asks to read items/prices OUT of an attached reference image
    (vs attaching it only as a visual/style template)."""
    request = f"{project.raw_request or ''} {project.fields.notes or ''}".lower()
    return any(kw in request for kw in (
        "take items", "breakfast section", "from breakfast",
        "extract items", "extract prices", "items and prices",
        "sample flyer", "sample flier", "use items in this",
    ))


def _has_materialized_reference_menu_facts(project: FlyerProject) -> bool:
    def is_menu_fact(fact) -> bool:
        fact_id = str(getattr(fact, "fact_id", "") or "")
        return (
            fact_id == "pricing_structure"
            or fact_id.startswith("offer:")
            or fact_id.startswith("item:")
        )

    for extraction in getattr(project, "reference_extractions", []) or []:
        if getattr(extraction, "status", "") != "ok":
            continue
        if any(is_menu_fact(fact) for fact in getattr(extraction, "extracted_facts", []) or []):
            return True
    return any(
        str(getattr(fact, "source", "") or "").startswith("reference_") and is_menu_fact(fact)
        for fact in getattr(project, "locked_facts", []) or []
    )


def _needs_reference_extraction(project: FlyerProject) -> bool:
    """A reference IMAGE is attached AND the request asks to read its items/prices
    out of the image — so that copy isn't in `collect_text_facts()` and only the
    model can render it. In that case the deterministic overlay can't own the text.

    NOT true (→ background-only stays eligible):
      - logo/brand asset only (visual identity, not a text source); or
      - the reference is a visual/STYLE template and the copy is already in
        fields/locked facts (no extraction requested) — the overlay draws it.
    """
    has_reference_image = any(
        getattr(asset, "kind", "") == "reference_image"
        for asset in _project_reference_assets(project)
    )
    if has_reference_image and _has_materialized_reference_menu_facts(project):
        return False
    return has_reference_image and _request_asks_reference_extraction(project)


# Deterministic-first content classifier (2026-06-20). Fact-dense flyers (menus,
# price lists, combos, schedule+price) carry exact text the image model garbles
# (~24% first-try success live); they render reliably via the deterministic
# overlay instead. Pure heuristic over structured locked_facts — no model call.
_FACT_ITEM_NAME_RE = re.compile(r"^item:\d+:name$")
_FACT_ITEM_PRICE_RE = re.compile(r"^item:\d+:price$")
_FACT_OFFER_RE = re.compile(r"^offer:\d+$")
_FACT_CURRENCY_RE = re.compile(r"[$₹]\s*\d")  # $ or rupee followed by a digit


def _is_fact_dense(project: FlyerProject) -> bool:
    """True when the project carries fact-dense exact text (menu / multi-item /
    price list / combo / schedule+price). Deterministic over locked_facts."""
    facts = list(getattr(project, "locked_facts", []) or [])

    def _fid(f):
        return (getattr(f, "fact_id", "") or "")

    def _has_currency(f):
        return bool(f) and bool(_FACT_CURRENCY_RE.search(getattr(f, "value", "") or ""))

    item_names = {_fid(f) for f in facts if _FACT_ITEM_NAME_RE.match(_fid(f))}
    item_prices = [f for f in facts if _FACT_ITEM_PRICE_RE.match(_fid(f))]
    offers = [f for f in facts if _FACT_OFFER_RE.match(_fid(f))]
    pricing_structures = [f for f in facts if _fid(f) == "pricing_structure"]
    offer_prices = [f for f in facts if _fid(f) == "offer_price"]
    has_schedule = any(_fid(f) == "schedule" for f in facts)
    # every currency-bearing price fact (used by the schedule+price branch)
    currency_price_facts = item_prices + pricing_structures + offers + offer_prices

    # (a) >=2 distinct menu items
    if len(item_names) >= 2:
        return True
    # (b) >=2 item prices
    if len(item_prices) >= 2:
        return True
    # (c) a currency-amount pricing structure (any of them; not a % discount)
    if any(_has_currency(f) for f in pricing_structures):
        return True
    # (d) >=2 offers (combo / multi-offer)
    if len(offers) >= 2:
        return True
    # (e) recurring schedule + any currency-amount price fact (incl. offer_price)
    if has_schedule and any(_has_currency(f) for f in currency_price_facts):
        return True
    return False


def _integrated_poster_eligible(project: FlyerProject) -> bool:
    """Cases where the image model composes the full poster (Slice 1: PRIMARY path).

    Integrated generation is now the primary path for food/grocery flyers of any
    item count and any language; the post-render referee + deterministic fallback
    catch failures (e.g. fabricated facts, garbled regional glyphs). The remaining
    exclusions are structural: reference-extraction-pending (facts not yet
    materialized), source-edits, non-food, and raw reference IMAGES whose menu
    facts are not materialized (nothing verifiable to render).
    """
    if _FORCE_BACKGROUND_ONLY.get():
        return False
    if getattr(project, "deterministic_recovery", False):
        return False
    if os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER", "").strip() != "1":
        return False
    if _needs_reference_extraction(project):
        return False
    if _is_source_edit_project(project):
        return False
    # Machine-read elements (QR codes, barcodes) must be composited deterministically
    # (Slice 1 guard; no QR fact type exists yet).
    if any((getattr(f, "fact_id", "") or "").strip().lower() in {"qr", "qr_code", "barcode"} for f in project.locked_facts):
        return False
    if not _is_food_or_grocery_project(project):
        return False
    # Deterministic-first routing (2026-06-20): fact-dense food flyers (menus,
    # price lists, combos, schedule+price) skip integrated model-rendered text and
    # render via the deterministic premium overlay (mode 2). Gated + allowlist-scoped
    # via FLYER_DETERMINISTIC_FIRST; flag-off short-circuits -> byte-identical.
    if _deterministic_first_enabled(project) and _is_fact_dense(project):
        return False
    reference_menu = _style_only_reference_requested(project) and _has_materialized_reference_menu_facts(project)
    if reference_menu:
        # Slice 1 narrowing: style-only / reference-menu uploaded-flyer cases STAY on
        # the deterministic overlay path to preserve the 2026-06-10 "use-as-reference"
        # fidelity fix (test_flyer_reference_quality F0151). Integrating these would
        # let the model recompose the borrowed flyer's text instead of overlaying the
        # exact materialized facts.
        return False
    has_reference_image = any(
        getattr(asset, "kind", "") == "reference_image"
        for asset in _project_reference_assets(project)
    )
    if has_reference_image and not reference_menu:
        return False
    try:
        plan = _poster_copy_plan(project)
    except FlyerRenderError:
        # A dense plain-notes project (>10 items with no structured item:N facts)
        # overflows _detail_clauses' MAX_DETAIL_FACTS cap and raises. An eligibility
        # predicate must never throw — such a project is cleanly ineligible and
        # falls back to the background-only path (today's behavior before widening).
        return False
    if not (plan.items or plan.detail_lines or plan.title):
        return False
    return True


def _background_only_eligible(project: FlyerProject) -> bool:
    """Whether the model should render a TEXTLESS background (the deterministic
    overlay owns ALL customer-facing text).

    Principle: the deterministic overlay always owns the text — it renders each
    fact in its OWN script (`_font` loads Telugu/Indic fonts via `_has_telugu`),
    so it is at least as good as the image model for any language. The model
    GARBLES non-English text (the live F0114 Telugu hallucination), so handing
    localized copy back to the model is strictly worse, not a safe fallback.
    Localization is therefore an INTAKE concern (capture/translate facts into the
    target language; the overlay then draws them) — NOT a reason to let the model
    paint text. So language does NOT gate eligibility.

    Reference-extraction is still excluded because items/prices live in an
    attached reference IMAGE and aren't in `collect_text_facts()` yet. Simple
    English typed menu flyers are also excluded by product policy: those are
    now direct integrated posters, with factual QA after generation, because
    the customer-quality baseline is full poster composition rather than
    background art plus pasted menu cards.
    """
    return not _needs_reference_extraction(project) and not _integrated_poster_eligible(project)


def _poster_layout_requirements(project: FlyerProject, *, force_background_only: bool = False) -> str:
    plan = _poster_copy_plan(project)
    footer_safe_area = (
        "\n- Put location/contact in a dedicated footer band with generous bottom padding; "
        "keep every footer character at least 6% of the canvas height above the bottom edge "
        "so WhatsApp/status previews never crop the phone number."
    )
    if _background_only_eligible(project) or force_background_only:
        # Reserved-zone background contract (P1 slice 2): exact text is composited
        # deterministically as overlay panels, so the model produces only the
        # decorative BACKGROUND and leaves calm reserved zones — it must NOT draw
        # text/menu-cards/prices, which diffusion models garble.
        reserve = (
            "- Produce a decorative BACKGROUND image only. Do NOT draw any text, headlines, "
            "menu/item cards, price tags, schedule, location, or contact — the exact text is "
            "composited afterwards into overlay panels.\n"
        )
        if _premium_overlay_enabled(project):
            # Fix C v2.1 (SCOPED): Restaurant-Promo single-hero background. Replaces the
            # reserved-zone banding (which yielded a flat multi-item spread → template) with a
            # cinematic single-hero composition; the deterministic overlay's own gradient
            # scrims provide text legibility (validated). Scoped ⇒ flag-off byte-identical.
            #
            # CD v2 (Task B2.4): when the resolved creative direction carrier is present
            # (project.creative_direction, flag-on only), NAME the chosen hero dish and
            # reflect the chosen theme/mood in the subject line. Carrier absent/empty ⇒
            # the directive is byte-identical to the fixed legacy string (regression-tested).
            _cd = getattr(project, "creative_direction", None)
            _hero_name = ""
            _theme_family = ""
            _mood = ""
            if isinstance(_cd, dict):
                _hero_name = (_cd.get("hero_name") or "").strip()
                _theme_family = (_cd.get("theme_family") or "").strip()
                _mood = (_cd.get("mood") or "").strip()
            if _hero_name or _theme_family or _mood:
                _hero_subject = f"the featured {_hero_name} dish" if _hero_name else "the featured food"
                _scene_clauses = ""
                if _theme_family:
                    _scene_clauses += f" Style the scene for a {_theme_family} theme."
                if _mood:
                    _scene_clauses += f" Convey a {_mood} mood."
                _hero_line = (
                    "- Compose a wordless HERO food photograph for the background: ONE single mouth-watering hero "
                    f"dish ({_hero_subject}) as the bold subject that DOMINATES the frame, with warm golden "
                    "cinematic lighting, gentle steam and visible texture where appropriate, rich shallow depth of "
                    f"field, on a rustic dark wood or slate surface with softly-lit ambiance behind.{_scene_clauses} "
                    "Appetizing, vibrant, and atmospheric.\n"
                )
            else:
                _hero_line = (
                    "- Compose a wordless HERO food photograph for the background: ONE single mouth-watering hero "
                    "dish (the featured food) as the bold subject that DOMINATES the frame, with warm golden "
                    "cinematic lighting, gentle steam and visible texture where appropriate, rich shallow depth of "
                    "field, on a rustic dark wood or slate surface with softly-lit ambiance behind. Appetizing, "
                    "vibrant, and atmospheric.\n"
                )
            reserve += (
                _hero_line
                + (
                    "- This is a PHOTOGRAPH ONLY: absolutely NO text, letters, words, numbers, captions, signage, "
                    "menu boards, price tags, watermarks, or logos anywhere in the image — do not imitate an "
                    "advertisement layout; the exact text is composited afterwards into overlay panels.\n"
                    "- Cinematic and atmospheric, with naturally darker, softer top and bottom edges (a gentle "
                    "vignette) so the composited title and menu stay legible — but the hero dish still fills the frame; "
                    "do NOT leave empty flat bands or blank panels.\n"
                    "- No people, no faces, no hands, no diners, no family scene, no buffet, and no spread of many "
                    "separate dishes — ONE hero dish is the subject.\n"
                )
            )
        else:
            reserve += (
                "- Reserve visually calm, low-detail zones in the upper-left and along the bottom for "
                "those overlay panels; keep the rich hero imagery in the center and right.\n"
            )
        if _style_only_reference_requested(project):
            reserve += (
                "- Do not draw ornamental frames, corner flourishes, border lines, blank menu panels, "
                "card rectangles, signboards, badges, fake logos, or price-sticker shapes from the source/reference. "
                "Leave reserved zones as natural negative space, not visible empty boxes.\n"
                "- Use full-bleed editorial food photography across the canvas. Reserved zones should be "
                "soft-focus or darkened natural food/restaurant background, not flat blank beige/green columns "
                "or large empty color blocks.\n"
            )
        if plan.items:
            if not _is_food_or_grocery_project(project):
                return reserve + (
                    "- Use polished, category-appropriate service imagery for the stated service category.\n"
                    "- Keep the visual language category-safe for the stated business type; avoid "
                    "restaurant/grocery and cultural-celebration styling unless the customer explicitly asks for it."
                )
            shared_offer_text, _shared_offer_label, shared_offer_price = _shared_price_offer_parts(plan.detail_lines)
            has_item_prices = any(price for _name, price in plan.items)
            if shared_offer_text and shared_offer_price and not has_item_prices:
                return reserve + (
                    "- Build a premium Indian street-snack poster BACKGROUND: dark green/charcoal restaurant texture, "
                    "warm gold/red accents, chili/spice energy, and appetizing close-up snack photography.\n"
                    "- Keep the top third dark and text-free for a large headline overlay, keep the left-middle "
                    "calm enough for a snack list overlay, and keep the bottom fifth calm enough for a red/gold "
                    "combo-price band overlay.\n"
                    "- Put the strongest food hero cluster in the center/right and lower-right; avoid blank flat "
                    "color fields, empty black blocks, generic buffet scenes, and visible text/signage."
                )
            return reserve + (
                "- Use appetizing food photography and a premium restaurant ambiance (warm lighting, "
                "garnished dishes, tasteful gold/green/red accents, subtle texture).\n"
                "- Use product-specific close-up food imagery based on the listed menu items. "
                "Avoid generic buffet, dining-family, or unrelated stock-food scenes.\n"
                "- Match the attached reference flyer's premium retail feel (palette, density, motifs) "
                "when a reference is provided — but as background imagery, not text."
            )
        return reserve + (
            "- Use polished, category-appropriate hero imagery and a professional local-business look.\n"
            "- Keep the composition clean behind the reserved overlay zones."
        )
    # Non-eligible flows (localized / reference-extraction): the overlay cannot
    # produce the required text, so the model must still render it.
    if plan.items:
        if not _is_food_or_grocery_project(project):
            return (
                "- Build a complete local service-business poster for the stated service category.\n"
                "- Use a clear brand masthead, large headline, service offer cards with prices, category-appropriate service imagery, and a footer for location/contact.\n"
                "- Service offer cards must pair each service name and price together.\n"
                "- If a service line is listed without a price, show it as a service label without a price, dash, or placeholder.\n"
                "- Keep text large, high-contrast, and centered inside its designed panels; avoid tiny text blocks and generic lower-third captions.\n"
                "- Keep the visual language category-safe for the stated business type; avoid restaurant/grocery and cultural-celebration styling unless the customer explicitly asks for it."
                f"{footer_safe_area}"
            )
        return (
            "- Build a full restaurant/menu poster with a premium/editorial restaurant-advertisement finish, not an ordinary menu template or background template.\n"
            "- Use product-specific close-up food imagery based on the listed menu items.\n"
            "- Use one strong hero food image, a confident brand masthead, a large high-impact promo title, a typographic offer lockup, a supporting menu list, and a restrained footer for location/contact.\n"
            "- Item cards must look like designed menu tiles; include item cards with food imagery and prices where provided while avoiding cluttered coupon-grid styling.\n"
            "- Avoid boxed menu table layouts, large bordered rows, coupon-grid compositions, and default menu-card templates unless the customer explicitly asks for a menu-board style.\n"
            "- Keep item names and prices paired in clean typography; use minimal separators and spacing instead of heavy boxes.\n"
            "- If there is only one item/price, make it the single typographic offer lockup and use food photos as unlabeled supporting imagery. Do not repeat the same item/price in multiple panels.\n"
            "- Match the attached reference flyer's dense premium retail hierarchy when a reference is provided, but remove ordinary template clutter: fewer borders, better spacing, sharp food photography, and phone-readable type.\n"
            "- Keep text large, high-contrast, and intentionally placed; avoid tiny text blocks, generic lower-third captions, and over-framed template sections."
            f"{footer_safe_area}"
        )
    return (
        "- Build a complete finished poster flyer, not a blank background.\n"
        "- Use a clear brand masthead, large headline, offer/details section, visual proof imagery, and footer contact/action area.\n"
        "- Keep all required customer text large, high-contrast, and readable on a phone screen."
        f"{footer_safe_area}"
    )


def _reference_extraction_instruction(project: FlyerProject, *, force_background_only: bool = False) -> str:
    refs = _project_reference_assets(project)
    if not refs:
        return "- none"
    if _style_only_reference_requested(project):
        return (
            "- Use the attached reference image for visual style only and source content extraction/broad inspiration: "
            "palette, cuisine/category, motifs, and layout density.\n"
            "- Do NOT copy, preserve, or render the source/reference business name, logo, masthead, address, phone, "
            "slogan, item-board text, or price text unless it matches the controlled customer copy above."
        )
    if _background_only_eligible(project) or force_background_only:
        # Background-only eligible (English + reference already extracted): the
        # deterministic overlay owns all text, so the model must NOT recreate any
        # text from the reference — only borrow its visual style. Suppressing the
        # "recreate item names/prices as cards" instructions avoids contradicting
        # the textless-background contract and drawing garbled text under the overlay.
        return (
            "- Use the attached reference image for visual style only: palette, brand feel, "
            "cuisine/category, motifs, and layout density.\n"
            "- Do NOT recreate or render any text, item names, prices, or business names from the "
            "reference — all exact text is composited separately into overlay panels."
        )
    request = f"{project.raw_request} {project.fields.notes}".lower()
    instructions = [
        "- Do not render the request wording itself; it is operator instruction, not flyer copy.",
        "- Use the attached reference image for visual hierarchy, brand feel, cuisine/category, and layout density.",
        "- Do not copy business names or logos from the reference unless they match the controlled Business/brand line.",
    ]
    if "take items" in request or "breakfast section" in request or "from breakfast" in request:
        instructions.append(
            "- The request says to take items from breakfast section: read the attached reference image and recreate the visible BREAKFAST section items as menu cards. Do not invent unrelated generic items."
        )
    if (
        "extract items" in request
        or "extract prices" in request
        or "items and prices" in request
        or "sample flyer" in request
        or "sample flier" in request
        or "use items in this" in request
    ):
        instructions.append(
            "- The request says to extract items and prices from the sample/reference flyer: read the attached reference image and recreate the visible item names and prices as product/menu cards. Do not replace them with generic grocery categories."
        )
    return "\n".join(instructions)


def _facts_for_manifest(facts: list[FlyerTextFact]) -> list[dict[str, str]]:
    return [
        {
            "fact_id": fact.fact_id,
            "label": fact.label,
            "text": fact.text,
            "normalized_text": _normalize_fact_text(fact.text),
        }
        for fact in facts
    ]


def write_text_manifest(
    project: FlyerProject,
    artifact_path: Path | str,
    *,
    output_format: str,
    selected_concept_id: str = "",
    source_path: Path | str | None = None,
    verification_mode: str = "declared_render_facts",
    warnings: list[str] | None = None,
) -> Path:
    artifact = Path(artifact_path)
    if verification_mode == "source_edit_integrity_only":
        expected: list[FlyerTextFact] = []
        rendered: list[FlyerTextFact] = []
        missing: list[str] = []
        duplicate_ids: list[str] = []
    else:
        expected = collect_text_facts(project)
        rendered = list(expected)
        missing = []
        expected_by_id = {fact.fact_id: fact for fact in expected}
        rendered_by_id: dict[str, FlyerTextFact] = {}
        duplicate_ids = []
        for fact in rendered:
            if fact.fact_id in rendered_by_id:
                duplicate_ids.append(fact.fact_id)
            rendered_by_id[fact.fact_id] = fact
        for fact_id, fact in expected_by_id.items():
            rendered_fact = rendered_by_id.get(fact_id)
            if rendered_fact is None:
                missing.append(fact_id)
                continue
            if _normalize_fact_text(rendered_fact.text) != _normalize_fact_text(fact.text):
                missing.append(fact_id)
    blockers = []
    if missing:
        blockers.append("missing critical text facts: " + ", ".join(sorted(set(missing))))
    if duplicate_ids:
        blockers.append("duplicate critical text fact ids: " + ", ".join(sorted(set(duplicate_ids))))
    blockers.extend(_instruction_leak_blockers(expected))
    blockers.extend(_instruction_leak_blockers(rendered))
    manifest = {
        "schema_version": TEXT_MANIFEST_SCHEMA_VERSION,
        "project_id": project.project_id,
        "project_version": project.version,
        "selected_concept_id": selected_concept_id or project.selected_concept_id or "",
        "output_format": output_format,
        "artifact_path": str(artifact),
        "artifact_sha256": _sha256(artifact) if artifact.exists() else "",
        "source_sha256": _sha256(Path(source_path)) if source_path and Path(source_path).exists() else "",
        "verification_mode": verification_mode,
        # Additive honesty fields (2026-05-20): `rendered_facts` is a copy of
        # `expected_facts` because this manifest declares the facts the
        # renderer was asked to draw, not the facts proven present in
        # rendered pixels. Image-pixel verification is the QA report's job
        # (run_visual_qa). Field-rename to `declared_facts` deferred to keep
        # this PR scoped; the bool surface lets readers know to look at
        # the QA report for ground truth.
        "is_rendered_proof": False,
        "verification_method": "declared_render_facts",
        "expected_facts": _facts_for_manifest(expected),
        "rendered_facts": _facts_for_manifest(rendered),
        "missing_fact_labels": sorted(set(missing)),
        "warnings": list(warnings or []),
        "ok": not blockers,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    sidecar = _text_manifest_path(artifact)
    manifest_payload = json.dumps(manifest, indent=2, ensure_ascii=False)
    try:
        from safe_io import atomic_write_text  # type: ignore
    except Exception:
        sidecar.write_text(manifest_payload, encoding="utf-8")
    else:
        atomic_write_text(sidecar, manifest_payload)
    result = validate_text_manifest_file(
        artifact,
        project_id=project.project_id,
        project_version=project.version,
        output_format=output_format,
    )
    if not result.ok:
        raise FlyerRenderError(f"text manifest validation failed: {result.blockers}")
    return sidecar


def validate_text_manifest_file(
    artifact_path: Path | str,
    *,
    project_id: str | None = None,
    project_version: int | None = None,
    output_format: str | None = None,
) -> FlyerTextQuality:
    artifact = Path(artifact_path)
    sidecar = _text_manifest_path(artifact)
    blockers: list[str] = []
    warnings: list[str] = []
    if not artifact.exists():
        blockers.append("artifact missing")
    if not sidecar.exists():
        blockers.append("text manifest missing")
        return FlyerTextQuality(False, blockers, warnings, sidecar)
    try:
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as e:
        return FlyerTextQuality(False, [f"text manifest unreadable: {e}"], warnings, sidecar)
    if manifest.get("schema_version") != TEXT_MANIFEST_SCHEMA_VERSION:
        blockers.append("text manifest schema mismatch")
    if project_id is not None and manifest.get("project_id") != project_id:
        blockers.append("text manifest project mismatch")
    if project_version is not None and manifest.get("project_version") != project_version:
        blockers.append("text manifest project version mismatch")
    if output_format is not None and manifest.get("output_format") != output_format:
        blockers.append("text manifest output format mismatch")
    if artifact.exists() and manifest.get("artifact_sha256") != _sha256(artifact):
        blockers.append("text manifest artifact hash mismatch")
    if manifest.get("verification_mode") == "source_edit_integrity_only":
        warnings.append(
            "source edit manifest verifies artifact integrity only; customer approval remains the visual/text QA gate"
        )
    elif manifest.get("verification_mode") == "source_edit_overlay_recomposed":
        warnings.append(
            "source edit output re-composed the deterministic text overlay over the customer artwork; declared facts are visual-QA-checked; customer approval remains the gate"
        )
    expected = manifest.get("expected_facts") or []
    rendered = manifest.get("rendered_facts") or []
    if not isinstance(expected, list) or not isinstance(rendered, list):
        blockers.append("text manifest facts must be lists")
        expected = []
        rendered = []
    rendered_by_id: dict[str, dict] = {}
    duplicates: set[str] = set()
    for fact in rendered:
        if not isinstance(fact, dict):
            blockers.append("text manifest invalid rendered fact entry")
            continue
        fact_id = str(fact.get("fact_id", ""))
        if fact_id in rendered_by_id:
            duplicates.add(fact_id)
        rendered_by_id[fact_id] = fact
    if duplicates:
        blockers.append("duplicate rendered fact ids: " + ", ".join(sorted(duplicates)))
    expected_facts: list[FlyerTextFact] = []
    rendered_facts: list[FlyerTextFact] = []
    for fact in expected:
        if not isinstance(fact, dict):
            blockers.append("text manifest invalid expected fact entry")
            continue
        fact_id = str(fact.get("fact_id", ""))
        expected_facts.append(FlyerTextFact(
            fact_id=fact_id,
            label=str(fact.get("label", "")),
            text=str(fact.get("text", "")),
        ))
        rendered_fact = rendered_by_id.get(fact_id)
        if rendered_fact is None:
            blockers.append(f"missing rendered fact: {fact_id}")
            continue
        if rendered_fact.get("normalized_text") != fact.get("normalized_text"):
            blockers.append(f"rendered fact text mismatch: {fact_id}")
    for fact in rendered:
        if isinstance(fact, dict):
            rendered_facts.append(FlyerTextFact(
                fact_id=str(fact.get("fact_id", "")),
                label=str(fact.get("label", "")),
                text=str(fact.get("text", "")),
            ))
    blockers.extend(_instruction_leak_blockers(expected_facts))
    blockers.extend(_instruction_leak_blockers(rendered_facts))
    missing = manifest.get("missing_fact_labels") or []
    if missing:
        blockers.append("manifest reports missing facts: " + ", ".join(str(item) for item in missing))
    if manifest.get("ok") is not True:
        blockers.append("manifest not ok")
    return FlyerTextQuality(not blockers, blockers, warnings, sidecar)


def _brand_asset_prompt(project: FlyerProject) -> str:
    active_assets = [*_active_brand_assets(project), *_project_reference_assets(project)]
    if not active_assets:
        return "- none"
    style_only = _style_only_reference_requested(project)
    return "\n".join(
        f"- {_brand_asset_prompt_label(asset, style_only=style_only)}; notes={_sanitize_visual_context(getattr(asset, 'notes', '') or 'none')}"
        for asset in active_assets[-4:]
    )


def _brand_asset_prompt_label(asset: FlyerAsset, *, style_only: bool = False) -> str:
    if style_only and asset.kind in {"template", "reference_image"}:
        return "style/content reference only (do not copy source branding or text)"
    if style_only and asset.kind == "logo" and not _looks_like_owned_logo_asset(asset):
        return "style reference only (do not copy source branding or text)"
    if asset.kind == "logo":
        return "saved logo reference"
    if asset.kind == "template":
        return "saved template reference"
    if asset.kind == "reference_image":
        return "uploaded reference image"
    return f"{str(asset.kind).replace('_', ' ')} reference"


def _style_only_reference_requested(project: FlyerProject) -> bool:
    text = f"{project.raw_request or ''} {project.fields.notes or ''}".lower()
    return any(
        marker in text
        for marker in (
            "customer chose path 2",
            "use as reference",
            "only as a reference",
            "reference/inspiration",
            "do not copy the source flyer branding",
            "do not copy another business",
        )
    )


def _looks_like_owned_logo_asset(asset: FlyerAsset) -> bool:
    notes = str(getattr(asset, "notes", "") or "").lower()
    return not re.search(r"\b(?:theme|style|reference|sample|redesign|going forward)\b", notes)


def _generation_brand_assets(project: FlyerProject):
    assets = _active_brand_assets(project)
    if not _style_only_reference_requested(project):
        return assets
    return [
        asset for asset in assets
        if asset.kind == "logo" and _looks_like_owned_logo_asset(asset)
    ]


def _project_disables_saved_brand_assets(project: FlyerProject) -> bool:
    for fact in getattr(project, "locked_facts", []) or []:
        if getattr(fact, "fact_id", "") != "render:disable_brand_assets":
            continue
        value = str(getattr(fact, "value", "") or "").strip().casefold()
        if value in {"1", "true", "yes", "on"}:
            return True
    return False


def _active_brand_assets(project: FlyerProject):
    if os.environ.get("FLYER_DISABLE_BRAND_ASSETS") == "1" or _project_disables_saved_brand_assets(project):
        return []
    if "FLYER_CUSTOMERS_PATH" in os.environ:
        customers_path = Path(os.environ["FLYER_CUSTOMERS_PATH"])
    elif "FLYER_STATE_ROOT" in os.environ:
        customers_path = _customers_path()
    else:
        customers_path = CUSTOMERS_PATH
    if not customers_path.exists():
        return []
    try:
        store = FlyerCustomerStore.model_validate(json.loads(customers_path.read_text(encoding="utf-8")))
    except Exception:
        return []
    customer = store.find_customer_by_phone(str(project.customer_phone))
    if not customer:
        return []
    return [asset for asset in customer.brand_assets if asset.active and Path(asset.path).exists()]


def _registered_business_name(project: FlyerProject) -> str:
    if "FLYER_CUSTOMERS_PATH" in os.environ:
        customers_path = Path(os.environ["FLYER_CUSTOMERS_PATH"])
    elif "FLYER_STATE_ROOT" in os.environ:
        customers_path = _customers_path()
    else:
        customers_path = CUSTOMERS_PATH
    if not customers_path.exists():
        return ""
    try:
        store = FlyerCustomerStore.model_validate(json.loads(customers_path.read_text(encoding="utf-8")))
    except Exception:
        return ""
    customer = store.find_customer_by_phone(str(project.customer_phone))
    if not customer:
        return ""
    return customer.business_name.strip()


def _display_business_name(project: FlyerProject) -> str:
    return (
        fact_value(project, "business_name", fallback="")
        or _registered_business_name(project)
        or project.fields.event_or_business_name
        or ""
    )


def _registered_business_category(project: FlyerProject) -> str:
    if "FLYER_CUSTOMERS_PATH" in os.environ:
        customers_path = Path(os.environ["FLYER_CUSTOMERS_PATH"])
    elif "FLYER_STATE_ROOT" in os.environ:
        customers_path = _customers_path()
    else:
        customers_path = CUSTOMERS_PATH
    if not customers_path.exists():
        return ""
    try:
        store = FlyerCustomerStore.model_validate(json.loads(customers_path.read_text(encoding="utf-8")))
    except Exception:
        return ""
    customer = store.find_customer_by_phone(str(project.customer_phone))
    if not customer:
        return ""
    return customer.business_category.strip()


def _category_context(project: FlyerProject) -> str:
    return " ".join([
        _registered_business_category(project),
        project.fields.event_or_business_name or "",
        project.fields.style_preference or "",
        project.fields.notes or "",
        project.raw_request or "",
    ]).lower()


def _positive_visual_style_context(text: str) -> str:
    """Remove negated visual clauses before deterministic scene selection.

    The full style preference is still passed to the model prompt. This helper
    only keeps negated category words from becoming positive signals in the
    deterministic campaign-scene/layout selector.
    """
    normalized = re.sub(r"\b(?:but|while)\b", ",", text or "", flags=re.IGNORECASE)
    negated = re.compile(r"\b(?:no|not|without|avoid|exclude|don't|dont|do\s+not)\b", flags=re.IGNORECASE)
    return " ".join(
        part.strip()
        for part in re.split(r"[,.;\n]+", normalized)
        if part.strip() and not negated.search(part)
    )


def _visual_prompt_context(project: FlyerProject) -> str:
    """Structured context for scene/layout decisions in image prompts.

    `_category_context` intentionally includes the raw request because older
    extraction helpers still need the literal customer instruction text. The
    model prompt's visual routing should prefer the validated facts once they
    exist, so negative instructions like "no food or festival visuals" do not
    become positive scene-selection signals.
    """
    registered_category = _registered_business_category(project)
    locked_facts = list(getattr(project, "locked_facts", []))
    if not registered_category and not locked_facts:
        return _category_context(project)

    structured_parts = [
        registered_category,
        project.fields.event_or_business_name or "",
        _positive_visual_style_context(project.fields.style_preference or ""),
    ]
    for fact in locked_facts:
        fact_id = getattr(fact, "fact_id", "") or ""
        value = str(getattr(fact, "value", "") or "").strip()
        if not value:
            continue
        if (
            fact_id
            in {
                "business_name",
                "campaign_title",
                "headline",
                "tagline",
                "pricing_structure",
                "offer_price",
                "promotion_end",
            }
            or fact_id.startswith(("offer:", "item:", "detail_"))
        ):
            structured_parts.append(value)

    structured = " ".join(part for part in structured_parts if part).strip()
    if structured:
        return structured.lower()
    return _category_context(project)


def _context_has(context: str, terms: set[str]) -> bool:
    """Word-boundary-aware presence check for category-routing terms.

    Pre-fix: bare substring match — `spa` matched inside `space`,
    `transparent`, `Hispanic`. Multi-word terms (spaces or hyphens)
    keep substring semantics because regex word-boundary doesn't help
    when the term itself contains punctuation; single-word terms use
    `\\bterm\\b`.
    """
    for term in terms:
        if " " in term or "-" in term:
            if term in context:
                return True
        elif re.search(rf"\b{re.escape(term)}\b", context):
            return True
    return False


def _is_food_or_grocery_project(project: FlyerProject) -> bool:
    context = _visual_prompt_context(project)
    if any(_context_has(context, terms) for terms in (
        SALON_CATEGORY_TERMS,
        TAX_CATEGORY_TERMS,
        CLEANING_CATEGORY_TERMS,
        MARKETING_CATEGORY_TERMS,
    )):
        return False
    return _context_has(context, FOOD_CATEGORY_TERMS)


def _design_direction(project: FlyerProject, concept_id: str) -> str:
    context = _visual_prompt_context(project)
    if _context_has(context, SALON_CATEGORY_TERMS):
        return "modern US salon and beauty studio promotion, upscale but approachable, clean service offer cards, hair-service photography, polished local-business branding"
    if _context_has(context, TAX_CATEGORY_TERMS):
        return "clean professional US tax and bookkeeping services flyer, trust-forward typography, simple offer cards, office-service visuals, no festival styling"
    if _context_has(context, CLEANING_CATEGORY_TERMS):
        return "fresh local cleaning service flyer, bright home-service visuals, clear service offer cards, modern US neighborhood-business design"
    if _context_has(context, MARKETING_CATEGORY_TERMS):
        return "modern digital marketing services flyer, crisp business visuals, clear service offer cards, contemporary agency layout"
    if _is_food_or_grocery_project(project):
        style_by_concept = {
            "C1": "premium/editorial restaurant advertisement, magazine-quality food advertising, bold appetizing hero photography, restrained typography, tasteful retail hierarchy",
            "C2": "warm cultural food promotion with regional motifs only when they fit the customer, elegant food spread, refined community-event look",
            "C3": "modern social-media food creative, crisp editorial layout, bright promotional palette, restaurant-quality design",
        }
        return style_by_concept.get(concept_id, style_by_concept["C1"])
    return "neutral US local-business promotional flyer, professional service imagery, clear offer cards, readable prices, category-safe styling"


def _quality_bar(project: FlyerProject) -> str:
    if _is_food_or_grocery_project(project):
        return "- Strong hierarchy, magazine-quality food advertising, one strong hero food image when food is relevant, restrained typography, and no ordinary menu template, boxed menu table, large bordered rows, or empty beige space."
    return "- Strong hierarchy, category-appropriate service visuals, modern local-business polish, and category-safe styling unless a different theme is explicitly requested."


def _campaign_scene_block_for_project(project: FlyerProject, *, context: str, business: str, offer: str) -> str:
    plan = _poster_copy_plan(project)
    selected_scene = select_campaign_scene(context)
    explicit_family_scene = selected_scene.key == "family_discovery"
    if (
        (_background_only_eligible(project) or _integrated_poster_eligible(project))
        and plan.items
        and _is_food_or_grocery_project(project)
        and not explicit_family_scene
    ):
        reserved_zone = (
            " while preserving calm reserved zones for the system-composited copy"
            if _background_only_eligible(project)
            else " as part of a complete integrated poster layout"
        )
        return (
            "Campaign scene direction (menu product close-up):\n"
            f"- Show appetizing close-up product photography for {business or 'the business'}'s listed menu items, "
            "with the food/snacks as the hero visual.\n"
            "- Do not show a generic family, community dining table, buffet spread, or unrelated restaurant scene; "
            "the listed items must drive the visual.\n"
            "- Avoid generic buffet, dining-family, or unrelated stock-food scenes.\n"
            f"- The scene supports the offer ({offer or 'the featured menu offer'}){reserved_zone}."
        )
    return campaign_scene_prompt_block(
        context=context,
        business=business,
        offer=offer,
    )


def _project_reference_assets(project: FlyerProject):
    return [
        asset for asset in project.assets
        if asset.kind in {"logo", "reference_image"} and Path(asset.path).exists()
    ]


def _style_reference_proxy_bytes(path: Path) -> tuple[str, bytes] | None:
    """Return a non-readable reference proxy for visual art direction only.

    Style-only reference requests are allowed to borrow palette, density, and
    cuisine cues, but the image model must not receive readable source flyer
    text or logos it can copy into the generated background.
    """
    pil = _load_pillow()
    if pil is None:
        return None
    Image, _ImageDraw, _ImageFont = pil
    try:
        from PIL import ImageFilter  # type: ignore
        with Image.open(path) as ref:
            ref = ref.convert("RGB")
            if ref.width <= 0 or ref.height <= 0:
                return None
            target_w = min(192, max(72, ref.width // 7))
            target_h = max(72, int(ref.height * (target_w / ref.width)))
            resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
            proxy = ref.resize((target_w, target_h), resample=resample)
            proxy = proxy.filter(ImageFilter.GaussianBlur(radius=max(2.5, target_w * 0.035)))
            buf = io.BytesIO()
            proxy.save(buf, format="PNG", optimize=True)
            return "image/png", buf.getvalue()
    except Exception:
        return None


def _image_message_content(project: FlyerProject, *, concept_id: str, output_format: str, size: tuple[int, int] | None, repair_instruction: str = "", scene_direction=None, force_background_only: bool = False):
    prompt = _image_prompt(project, concept_id=concept_id, output_format=output_format, size=size, repair_instruction=repair_instruction, scene_direction=scene_direction, force_background_only=force_background_only)
    parts: list[dict] = [{"type": "text", "text": prompt}]
    brand_assets = _generation_brand_assets(project)
    refs = _project_reference_assets(project)
    if _style_only_reference_requested(project):
        # Style-only means "do not copy source branding/text", not "hide the
        # reference from the art director." The prompt and QA already enforce
        # text/source-brand safety; the model still needs the uploaded flyer to
        # match hierarchy, palette, cuisine cues, and density.
        refs = [asset for asset in refs if asset.kind == "reference_image"]
    selected_assets = [*brand_assets[-1:], *refs[-1:]] if refs else [*brand_assets[-2:]]
    for asset in selected_assets:
        path = Path(asset.path)
        mime = asset.mime_type or mimetypes.guess_type(str(path))[0] or "image/png"
        if not mime.startswith("image/"):
            continue
        if _style_only_reference_requested(project) and asset.kind == "reference_image":
            proxy = _style_reference_proxy_bytes(path)
            if proxy is None:
                continue
            mime, image_bytes = proxy
        else:
            image_bytes = path.read_bytes()
        data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts if len(parts) > 1 else prompt


def _revision_notes_for_prompt(project: FlyerProject) -> str:
    if project.status not in {"revising_design", "manual_edit_required"}:
        return "- none"
    revisions = [r.request_text for r in project.revisions[-4:]]
    return "\n".join(f"- {r}" for r in revisions) if revisions else "- none"


def _scene_block_from_visual_direction(scene_direction) -> str:
    """Compose the integrated-model scene/theme block from the skill's ADVISORY VisualDirection
    (theme_family / palette / motifs / visual_subjects). The occasion visual language is the SKILL's
    judgment — there is NO Python occasion/holiday keyword list here. This block carries ONLY visual
    taste: no business name, prices, dates, or any fact — the exact facts are injected separately by
    ``_poster_copy_block`` (Codex truth-safety). Duck-typed on the VisualDirection attributes so
    render.py need not import the brief model."""
    def _clean_list(values, limit):
        out = []
        for value in (values or []):
            cleaned = _sanitize_visual_context(str(value).strip())
            if cleaned:
                out.append(cleaned)
        return out[:limit]

    theme = _sanitize_visual_context(str(getattr(scene_direction, "theme_family", "") or "").strip())
    subjects = _clean_list(getattr(scene_direction, "visual_subjects", []), 12)
    motifs = _clean_list(getattr(scene_direction, "motifs", []), 12)
    palette = _clean_list(getattr(scene_direction, "palette", []), 8)
    lines = ["Campaign scene direction (Hermes skill art direction):"]
    if theme:
        lines.append(f"- Build the scene around the {theme} occasion/theme.")
    if subjects:
        lines.append(
            "- Make these visual subjects the hero of the composition, rendered with rich, appealing "
            f"detail: {', '.join(subjects)}."
        )
    if motifs:
        lines.append(f"- Decorate with these motifs/accents: {', '.join(motifs)}.")
    if palette:
        lines.append(f"- Lead with this color palette: {', '.join(palette)}.")
    # General composition rule (NOT an occasion keyword list): the occasion/theme above drives the
    # scene. Do not regress to the model's default food-table composition unless food is the subject.
    lines.append(
        "- Let the occasion/theme above drive the composition; do NOT fall back to a generic family "
        "dinner, dining table, or buffet/food-table spread unless food items are the actual hero subject above."
    )
    return "\n".join(lines)


def _style_registers_active(project: FlyerProject) -> bool:
    """Graduation commit 2 gate (plan: tasks/flyer-prompt-graduation-plan.md).
    Lazy import: a deploy-order skew (module missing) fails CLOSED to legacy
    assembly — the WS1b create-flyer-project lesson."""
    try:
        try:
            from style_registers import style_registers_enabled  # type: ignore
        except ImportError:
            from agents.flyer.style_registers import style_registers_enabled
    except Exception:  # noqa: BLE001 — any import failure -> legacy
        return False
    try:
        return style_registers_enabled(str(project.customer_phone or ""))
    except Exception:  # noqa: BLE001
        return False


def _style_register_parts(project: FlyerProject) -> tuple[str, str, str]:
    """(register_block, typeset_copy_section, ban_line) for the flag-on path.
    Occasion/intensity plumb in at commit 3; until then register-only, accent.
    All fact text enters VERBATIM from locked facts — the spec only assigns
    typographic roles (leak-proofed two-section shape, R2.6 evidence)."""
    try:
        from style_registers import (DEFAULT_REGISTER, forbidden_substrings_for,
                                     style_prompt_block)  # type: ignore
    except ImportError:
        from agents.flyer.style_registers import (DEFAULT_REGISTER,
                                                  forbidden_substrings_for,
                                                  style_prompt_block)
    register_block = style_prompt_block(DEFAULT_REGISTER)

    by_id = {f.fact_id: str(f.value or "").strip() for f in project.locked_facts
             if str(f.value or "").strip()}
    items: list[tuple[str, str]] = []
    for f in project.locked_facts:
        fid = str(f.fact_id or "")
        if fid.startswith("item:") and fid.endswith(":name"):
            idx = fid.split(":")[1]
            items.append((str(f.value or "").strip(), by_id.get(f"item:{idx}:price", "")))
    prices = {pr.replace(" ", "") for _n, pr in items if pr}
    offer = by_id.get("pricing_structure", "")
    uniform = len(prices) <= 1 and (not prices or next(iter(prices)) in offer.replace(" ", ""))

    strings: list[str] = []
    roles: list[str] = []

    def add(text: str, role: str) -> None:
        if not text:
            return
        strings.append(text)
        roles.append(f"Line {len(strings)}: {role}")

    add(by_id.get("business_name", ""), "small top line; widely spaced")
    add(by_id.get("campaign_title", ""),
        "the huge display text; as given; never extend into a sentence; no price in it")
    if offer:
        add(offer, "inside/beside the shaped price element ONLY; never in the display text")
    add(by_id.get("schedule", ""), "its own small line")
    for name, price in items:
        add(name if uniform else f"{name} {price}".strip(),
            "one menu row" + ("; the shared price stays in the price element, never beside items" if uniform else ""))
    footer_bits = [by_id.get("location", "")]
    if by_id.get("contact_phone"):
        footer_bits.append(f"Call {by_id['contact_phone']}")
    add(" | ".join(b for b in footer_bits if b), "the single clean bottom strip")

    sec1 = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(strings))
    sec2 = "\n".join(roles)
    typeset_section = (
        "TEXT TO RENDER - these numbered strings are the ONLY text allowed in the art; "
        f"render each VERBATIM, spelled exactly:\n{sec1}\n\n"
        f"HOW TO SET EACH LINE (instructions for you - these words are NEVER painted):\n{sec2}"
    )
    vocab = ", ".join(forbidden_substrings_for(DEFAULT_REGISTER))
    ban_line = (
        "- Instruction and style vocabulary must NEVER appear as visible text in the art "
        f"(includes: {vocab}); no list numbering digits; no field names or key:value notation."
    )
    return register_block, typeset_section, ban_line


def _image_prompt(project: FlyerProject, *, concept_id: str, output_format: str, size: tuple[int, int] | None, repair_instruction: str = "", scene_direction=None, force_background_only: bool = False) -> str:
    revision_block = _revision_notes_for_prompt(project)
    reference_instruction = _reference_preservation_instruction(project)
    sanitized_style = _sanitize_visual_context(project.fields.style_preference or "festive, clean, professional")
    visual_context = _visual_prompt_context(project)
    if _style_only_reference_requested(project):
        brand_quality_line = "- Use only the registered customer identity and controlled customer copy as identity source."
        reference_quality_line = "- For this reference-only request, do NOT preserve source/reference branding or text; treat references as inspiration only."
    else:
        brand_quality_line = "- If customer brand assets are listed, preserve the business identity and use the active logo/template as the visual reference."
        reference_quality_line = "- If an uploaded reference image/template is attached, preserve its visual identity and offer category but replace stale readable facts with the controlled customer copy above."
    _business = _sanitize_visual_context(
        fact_value(project, "business_name", fallback=project.fields.event_or_business_name) or ""
    )
    if scene_direction is not None:
        # Advisory skill-driven scene (FLYER_SKILL_DRIVEN_SCENE). Falls back to the Python scene
        # automatically because the caller passes scene_direction=None whenever the skill is
        # disabled/unavailable/invalid — this branch only runs with a valid VisualDirection. The
        # block carries NO facts (no business name); facts come from _poster_copy_block below.
        campaign_scene_block = _scene_block_from_visual_direction(scene_direction)
    else:
        campaign_scene_block = _campaign_scene_block_for_project(
            project,
            context=visual_context,
            business=_business,
            offer=_sanitize_visual_context(fact_value(project, "campaign_title", fallback="") or ""),
        )
    repair_block = ""
    if repair_instruction.strip():
        repair_block = f"""
Autonomous repair instruction:
- {_sanitize_visual_context(repair_instruction.strip())}
"""
    if _background_only_eligible(project) or force_background_only:
        text_contract_line = (
            "- Generate the decorative BACKGROUND image only — do NOT render flyer text, menu item "
            "cards, prices, schedule, location, or contact as words; the system composites all exact "
            "text into overlay panels afterwards. Leave the upper-left and lower areas visually "
            "calm/uncluttered so those panels read cleanly; keep hero imagery in the center and right."
        )
        if os.environ.get("FLYER_PREMIUM_OVERLAY") == "1":
            text_contract_line += (
                " Keep the top ~22% and the bottom ~32% darker and visually calm/uncluttered "
                "(negative space for a text overlay); place the hero food in the centre band."
            )
        # Overlay owns ALL text → the language hint must NOT instruct text/script
        # rendering (that would reintroduce model-painted, garbled non-English text).
        # Reflect language/culture in IMAGERY only.
        _lang = (project.fields.preferred_language or "").strip().lower()
        if _lang in {"te", "mixed"}:
            language_block = (
                "- Reflect Telugu / South-Indian cultural styling in the imagery, motifs, and palette "
                "ONLY — do NOT render any text, script, or words; all flyer text is composited separately."
            )
        else:
            language_block = (
                "- Do not render any text, script, or words in the background; all flyer text is "
                "composited separately into overlay panels."
            )
    else:
        text_contract_line = (
            "- The final image must already contain the finished flyer text, menu items, prices, "
            "schedule, location, and contact when those facts are provided."
        )
        if _integrated_poster_eligible(project):
            # Branch on whether the CONTENT carries regional script (not the profile
            # language): an English-content menu with a localized profile language
            # still wants the English-only instruction, while a project whose facts
            # are actually in Telugu/regional script must NOT be told English-only —
            # that would produce an English flyer for a regional-language customer.
            _content = " ".join(
                str(value or "")
                for value in (
                    project.raw_request,
                    getattr(project.fields, "notes", ""),
                    *(fact.value for fact in project.locked_facts),
                )
            )
            if _has_regional_script(_content):
                # Integrated path: the MODEL renders the text, so instruct it to render
                # the regional-language text faithfully (NOT suppress it — that would ship
                # a textless flyer for a Telugu customer). _telugu_hint already returns the
                # correct "primary flyer language / valid Telugu script / no missing-glyph
                # boxes" wording; the `or` fallback covers regional-script content with a
                # non-te/mixed profile language.
                _regional = _telugu_hint(project) or (
                    "Render the customer's regional-language text (e.g. Telugu) faithfully in valid script; "
                    "do not convert it to English and do not produce missing-glyph boxes. Keep item names and prices readable."
                )
                language_block = "- " + _regional
            else:
                language_block = (
                    "- Use English text only for this typed menu poster. Do not add Telugu, Hindi, "
                    "or other regional-language text unless the customer explicitly requested it."
                )
        else:
            language_block = _language_constraint_hint(project)
    _registers_on = (
        _style_registers_active(project)
        and not (_background_only_eligible(project) or force_background_only)
    )
    if _registers_on:
        _reg_block, _typeset_section, _ban_line = _style_register_parts(project)
        register_segment = f"\n{_reg_block}\n"
        copy_section = _typeset_section
        ban_segment = f"\n{_ban_line}"
    else:
        register_segment = ""
        copy_section = (
            "Controlled customer copy:\n"
            + _poster_copy_block(project, force_background_only=force_background_only)
        )
        ban_segment = ""
    return f"""Create a complete, finished customer-ready poster flyer for WhatsApp delivery.

Design direction: {_design_direction(project, concept_id)}.
Customer style notes: {sanitized_style}.
Output format: {output_format}; aspect ratio {_aspect_ratio(size)}.

{campaign_scene_block}
{register_segment}
{copy_section}

Visual context for style and imagery:
- theme/category: {_sanitize_visual_context(fact_value(project, "business_name", fallback=project.fields.event_or_business_name) or visual_context or "local SMB promotion")}
- style: {sanitized_style}

Layout requirements:
{_poster_layout_requirements(project, force_background_only=force_background_only)}

Reference/menu extraction instructions:
{_reference_extraction_instruction(project, force_background_only=force_background_only)}

Customer brand assets to honor:
{_brand_asset_prompt(project)}

Revision notes to honor:
{_sanitize_visual_context(revision_block)}

Reference/template policy:
{reference_instruction}
{repair_block}

Quality bar:
- Looks like a paid local marketing designer made it, not a generic template.
{_quality_bar(project)}
- High contrast and readable on a phone screen.
{text_contract_line}
{brand_quality_line}
{reference_quality_line}
- If there is no one-time date, present the recurring schedule clearly instead of inventing a date.
- Avoid QR codes, fake logos, watermarks, unreadable microtext, and placeholder glyph boxes.{ban_segment}
{language_block}
"""


def build_image_generation_prompt(
    project: FlyerProject,
    *,
    concept_id: str,
    output_format: str,
    size: tuple[int, int] | None,
    repair_instruction: str = "",
    force_background_only: bool = False,
) -> str:
    token = _FORCE_BACKGROUND_ONLY.set(True) if force_background_only else None
    try:
        return _image_prompt(
            project,
            concept_id=concept_id,
            output_format=output_format,
            size=size,
            repair_instruction=repair_instruction,
            force_background_only=force_background_only,
        )
    finally:
        if token is not None:
            _FORCE_BACKGROUND_ONLY.reset(token)


def _reference_preservation_instruction(project: FlyerProject) -> str:
    if not [*_active_brand_assets(project), *_project_reference_assets(project)]:
        return "- none"
    if _style_only_reference_requested(project):
        return (
            "- Treat attached images/templates as style/content inspiration only, not as identity source.\n"
            "- Use the registered customer business identity and controlled customer copy as the only text/brand source.\n"
            "- Do not preserve, copy, or render source/reference business names, logos, mastheads, addresses, phone numbers, slogans, prices, or menu-board text."
        )
    return (
        "- Use the attached image/logo/template as source of truth for visual identity.\n"
        "- Do not redesign from scratch.\n"
        "- Preserve business/logo identity, layout feel, cuisine/event category, and visual mood.\n"
        "- Latest revision facts override older text. Do not keep stale readable prices, dates, phone numbers, or addresses."
    )


# Canonical pixel shape per final output format. Single source of truth shared
# by render_final_package (generation) and the send-time truthfulness gate.
# None = no fixed pixel shape (PDF).
FINAL_FORMAT_PIXEL_SHAPES: dict[str, tuple[int, int] | None] = {
    "whatsapp_image": (1080, 1350),
    "instagram_post": (1080, 1080),
    "instagram_story": (1080, 1920),
    "printable_pdf": None,
}


def png_pixel_dimensions(path: Path | str) -> tuple[int, int] | None:
    """Read a PNG's (width, height) from its IHDR header without Pillow.

    Returns None when the file is missing or is not a readable PNG. Pillow is
    not reliably present in the Hermes venv (it is the F0105 root cause), so the
    send-time format-truthfulness check must not depend on it; the PNG IHDR
    header is fixed-layout and parseable from the first 24 bytes.
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return (width, height)


def inspect_rendered_asset(path: Path | str, *, expected_width: int, expected_height: int, mime_type: str) -> RenderedAssetQuality:
    path = Path(path)
    blockers: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return RenderedAssetQuality(False, ["missing"], warnings)
    size_bytes = path.stat().st_size
    min_size = 500 if mime_type == "application/pdf" else 1000
    if size_bytes < min_size:
        blockers.append(f"tiny file: {size_bytes} bytes")
    if mime_type == "application/pdf":
        try:
            header = path.read_bytes()[:4]
        except OSError as e:
            blockers.append(f"unreadable pdf: {e}")
            header = b""
        if header != b"%PDF":
            blockers.append("pdf header missing")
        return RenderedAssetQuality(not blockers, blockers, warnings, expected_width, expected_height, size_bytes)
    pil = _load_pillow()
    if pil is None:
        warnings.append("pillow unavailable; skipped pixel inspection")
        return RenderedAssetQuality(not blockers, blockers, warnings, None, None, size_bytes)
    Image, _ImageDraw, _ImageFont = pil
    try:
        with Image.open(path) as img:
            width, height = img.size
            if (width, height) != (expected_width, expected_height):
                blockers.append(f"dimensions {width}x{height} != {expected_width}x{expected_height}")
            sample = img.convert("RGB").resize((32, 32))
            colors = sample.getcolors(maxcolors=4096) or []
            extrema = sample.getextrema()
            variance = sum(channel[1] - channel[0] for channel in extrema)
            if len(colors) < 4 or variance < 20:
                blockers.append("blank or low-variance image")
            return RenderedAssetQuality(not blockers, blockers, warnings, width, height, size_bytes)
    except Exception as e:
        blockers.append(f"image open failed: {e}")
        return RenderedAssetQuality(False, blockers, warnings, None, None, size_bytes)


def _critical_lines(project: FlyerProject) -> list[str]:
    return _fact_lines(project)


def apply_critical_text_overlay(project: FlyerProject, source: Path | str, target: Path | str, *, size: tuple[int, int], output_format: str) -> None:
    pil = _load_pillow()
    if pil is None:
        raise FlyerRenderError("Pillow is required for critical text overlay")
    Image, ImageDraw, ImageFont = pil
    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        img = img.convert("RGB")
        if img.size != size:
            img = img.resize(size)
        draw = ImageDraw.Draw(img, "RGBA")
        width, height = size
        menu_payload = _menu_overlay_payload(project)
        if menu_payload["items"]:
            margin = max(28, int(width * 0.038))
            title_font = _font(ImageFont, max(42, int(width * 0.056)), bold=True, text=str(menu_payload["title"]))
            item_font = _font(ImageFont, max(28, int(width * 0.034)), bold=True)
            price_font = _font(ImageFont, max(30, int(width * 0.038)), bold=True)
            small_font = _font(ImageFont, max(19, int(width * 0.021)))

            # Title card (top-left): brand + full campaign title + schedule +
            # promo/offer facts. Content-adaptive height so no required visible
            # fact is truncated — the card grows downward (capped above the menu
            # panel). `extras` carries the offer/promotion/pricing facts the menu
            # item cards don't show, so visual QA finds every required fact.
            biz_font = _font(ImageFont, max(24, int(width * 0.030)), bold=True)
            business = str(menu_payload.get("business") or "").strip()
            title_text = str(menu_payload["title"]).strip()
            shared_offer_text = str(menu_payload.get("shared_offer_text") or "").strip()
            shared_offer_label = str(menu_payload.get("shared_offer_label") or "").strip()
            shared_offer_price = str(menu_payload.get("shared_offer_price") or "").strip()
            items = list(menu_payload["items"])
            has_item_prices = any(_split_item_price(item)[1] for item in items)
            shared_snack_poster = bool(shared_offer_price and items and not has_item_prices)
            box_x0, box_y0, box_x1 = margin, int(height * 0.026), int(width * (0.58 if shared_offer_price else 0.68))
            inner_w = box_x1 - box_x0 - 44
            card_lines: list[tuple[object, tuple[int, int, int, int], str]] = []
            # Every required visible fact is fully wrapped — NO silent line caps
            # anywhere (brand/title/schedule/extras). Overflow is handled solely
            # by the fail-closed fit check below, so a long brand or title can
            # never be truncated into a QA "missing required visible fact".
            if business and not _same_text(business, title_text):
                for ln in _wrap(draw, business, biz_font, inner_w):
                    card_lines.append((biz_font, (255, 255, 245, 255), ln))
            for ln in _wrap(draw, title_text, title_font, inner_w):
                card_lines.append((title_font, (255, 218, 85, 255), ln))
            # Secondary required facts (date / schedule / time / promotion_end /
            # offers / details) are sourced directly from `collect_text_facts()`
            # — the SAME set visual QA checks — so the title card cannot omit a
            # required visible fact. The other regions cover the rest: the big
            # title (above), the brand line, the menu item cards, and the footer
            # (location + contact). Items are skipped here (drawn as cards). If
            # the full set can't fit, the fail-closed check below routes the
            # project to manual review rather than shipping an incomplete concept.
            item_norms = {_normalize_fact_text(i) for i in menu_payload["items"]}
            for fact in collect_text_facts(project):
                if fact.fact_id in ("brand", "title", "location", "contact"):
                    continue
                value = str(fact.text).strip()
                if not value or _normalize_fact_text(value) in item_norms:
                    continue
                if _same_text(value, title_text) or (business and _same_text(value, business)):
                    continue
                if shared_offer_text and _same_text(value, shared_offer_text):
                    continue
                for ln in _wrap(draw, value, small_font, inner_w):
                    card_lines.append((small_font, (255, 236, 205, 250), ln))
            content_h = sum(int(getattr(f, "size", 18) * 1.2) for f, _c, _t in card_lines)
            box_y1 = box_y0 + content_h + 34
            if box_y1 > int(height * 0.50):
                raise FlyerRenderError("critical text overlay does not fit")
            if shared_snack_poster:
                # Style-only references often carry a bright source masthead.
                # Mask the header deterministically before drawing the real
                # customer copy so OCR misses cannot ship copied source branding.
                mask_y1 = int(height * 0.335)
                draw.rectangle((0, 0, width, mask_y1), fill=(8, 5, 6, 224))
                draw.rectangle((0, mask_y1 - 18, width, mask_y1), fill=(8, 5, 6, 150))
                y = box_y0 + 10
                for f, color, ln in card_lines:
                    if getattr(f, "size", 18) >= title_font.size:
                        text_color = (255, 226, 130, 255)
                    elif getattr(f, "size", 18) >= biz_font.size:
                        text_color = (255, 248, 222, 255)
                    else:
                        text_color = (255, 232, 174, 255)
                    draw.text((box_x0 + 10 + 4, y + 4), ln, font=f, fill=(45, 8, 12, 190))
                    draw.text((box_x0 + 10, y), ln, font=f, fill=text_color)
                    y += int(getattr(f, "size", 18) * 1.2)
            else:
                draw.rounded_rectangle((box_x0 + 7, box_y0 + 7, box_x1 + 7, box_y1 + 7), radius=24, fill=(0, 0, 0, 70))
                draw.rounded_rectangle((box_x0, box_y0, box_x1, box_y1), radius=24, fill=(255, 248, 224, 248), outline=(179, 37, 47, 235), width=3)
                y = box_y0 + 18
                for f, color, ln in card_lines:
                    if color[0] > 240 and color[1] > 200:
                        color = (128, 22, 34, 255)
                    elif color[0] > 240:
                        color = (50, 66, 42, 255)
                    draw.text((box_x0 + 22, y), ln, font=f, fill=color)
                    y += int(getattr(f, "size", 18) * 1.2)
            if shared_offer_price and not shared_snack_poster:
                badge_x0 = max(box_x1 + 18, int(width * 0.60))
                badge_x1 = width - margin
                badge_y0 = box_y0
                badge_y1 = max(box_y1, badge_y0 + int(height * 0.115))
                if badge_x1 - badge_x0 < int(width * 0.22) or badge_y1 > int(height * 0.50):
                    raise FlyerRenderError("critical text overlay does not fit")
                label_font = _font(ImageFont, max(22, int(width * 0.025)), bold=True, text=shared_offer_label)
                badge_price_font = _font(ImageFont, max(56, int(width * 0.072)), bold=True, text=shared_offer_price)
                draw.rounded_rectangle((badge_x0 + 5, badge_y0 + 5, badge_x1 + 5, badge_y1 + 5), radius=18, fill=(0, 0, 0, 72))
                draw.rounded_rectangle((badge_x0, badge_y0, badge_x1, badge_y1), radius=18, fill=(92, 13, 24, 232), outline=(246, 214, 132, 230), width=2)
                label_lines = _wrap(draw, shared_offer_label, label_font, badge_x1 - badge_x0 - 40)
                label_y = badge_y0 + 18
                for line in label_lines:
                    draw.text((badge_x0 + 20, label_y), line, font=label_font, fill=(255, 242, 204, 255))
                    label_y += int(label_font.size * 1.08)
                price_bbox = draw.textbbox((0, 0), shared_offer_price, font=badge_price_font)
                price_w = price_bbox[2] - price_bbox[0]
                price_x = badge_x0 + max(20, (badge_x1 - badge_x0 - price_w) // 2)
                draw.text((price_x + 2, badge_y1 - badge_price_font.size - 19 + 2), shared_offer_price, font=badge_price_font, fill=(0, 0, 0, 130))
                draw.text((price_x, badge_y1 - badge_price_font.size - 19), shared_offer_price, font=badge_price_font, fill=(255, 255, 246, 255))

            if shared_offer_price and not has_item_prices:
                if shared_snack_poster:
                    # Exact-text safety cannot force this customer-visible path
                    # into a debug-looking template. Use a poster composition
                    # close to the reference class: dominant headline, snack
                    # list, large bottom deal band, and trim-safe footer.
                    headline_text = title_text.upper()
                    if "STREET" not in headline_text and "SNACK" in shared_offer_label.upper():
                        headline_text = "STREET SNACK SPECIALS"
                    combo_text = title_text.upper()
                    schedule_text = str(menu_payload.get("schedule") or "").strip().upper()
                    footer = " | ".join(str(v) for v in (menu_payload["location"], menu_payload["contact"]) if v)

                    draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 44))
                    top_h = int(height * 0.325)
                    draw.rectangle((0, 0, width, top_h), fill=(5, 8, 6, 255))
                    # Add visible brand/retail energy to the thumbnail-critical
                    # top third. A flat black header with plain text reads as a
                    # template even when the food hero below is good.
                    for i, alpha in enumerate((210, 156, 104)):
                        inset = i * 16
                        draw.arc((22 + inset, 60 + inset, 298 - inset, 282 - inset), 190, 332, fill=(190, 24, 26, alpha), width=14)
                        draw.arc((width - 298 + inset, 60 + inset, width - 22 - inset, 282 - inset), 208, 350, fill=(190, 24, 26, alpha), width=14)
                    for x0, flip in ((50, 1), (width - 50, -1)):
                        for idx in range(6):
                            y0 = 66 + idx * 25
                            x1 = x0 + flip * (92 + idx * 13)
                            draw.line((x0, y0, x1, y0 + 10), fill=(232, 172, 58, 118), width=3)
                    mast_x0 = int(width * 0.17)
                    mast_x1 = int(width * 0.83)
                    mast_y0 = int(height * 0.030)
                    mast_y1 = int(height * 0.112)
                    draw.rounded_rectangle((mast_x0 + 7, mast_y0 + 7, mast_x1 + 7, mast_y1 + 7), radius=28, fill=(0, 0, 0, 110))
                    draw.rounded_rectangle((mast_x0, mast_y0, mast_x1, mast_y1), radius=28, fill=(248, 244, 224, 248), outline=(232, 184, 84, 235), width=3)
                    seal_r = int((mast_y1 - mast_y0) * 0.34)
                    seal_cx = mast_x0 + seal_r + 26
                    seal_cy = (mast_y0 + mast_y1) // 2
                    draw.ellipse((seal_cx - seal_r, seal_cy - seal_r, seal_cx + seal_r, seal_cy + seal_r), fill=(18, 63, 42, 255), outline=(210, 154, 45, 255), width=3)
                    seal_font = _font(ImageFont, max(16, int(width * 0.020)), bold=True, text="LK")
                    seal_bbox = draw.textbbox((0, 0), "LK", font=seal_font)
                    draw.text((seal_cx - (seal_bbox[2] - seal_bbox[0]) // 2, seal_cy - (seal_bbox[3] - seal_bbox[1]) // 2 - 2), "LK", font=seal_font, fill=(255, 231, 142, 255))
                    draw.rectangle((0, top_h - 28, width, top_h), fill=(122, 18, 18, 150))
                    draw.line((margin, top_h - 86, width - margin, top_h - 86), fill=(235, 178, 58, 230), width=4)
                    draw.line((margin, top_h - 18, width - margin, top_h - 18), fill=(235, 178, 58, 230), width=4)

                    brand_font = _font(ImageFont, max(31, int(width * 0.039)), bold=True, text=business)
                    headline_font = _font(ImageFont, max(58, int(width * 0.070)), bold=True, text=headline_text)
                    schedule_font = _font(ImageFont, max(24, int(width * 0.030)), bold=True, text=schedule_text)

                    if business:
                        for line in _wrap(draw, business, brand_font, width - margin * 2):
                            bbox = draw.textbbox((0, 0), line, font=brand_font)
                            x = mast_x0 + 88 + max(12, (mast_x1 - mast_x0 - 104 - (bbox[2] - bbox[0])) // 2)
                            y = mast_y0 + max(6, (mast_y1 - mast_y0 - (bbox[3] - bbox[1])) // 2) - 2
                            draw.text((x + 2, y + 2), line, font=brand_font, fill=(190, 155, 87, 110))
                            draw.text((x, y), line, font=brand_font, fill=(26, 68, 45, 255))
                            break
                    if schedule_text:
                        bbox = draw.textbbox((0, 0), schedule_text, font=schedule_font)
                        x = (width - (bbox[2] - bbox[0])) // 2
                        schedule_y = int(height * 0.122)
                        draw.text((x + 2, schedule_y + 2), schedule_text, font=schedule_font, fill=(0, 0, 0, 160))
                        draw.text((x, schedule_y), schedule_text, font=schedule_font, fill=(255, 244, 214, 255))
                    headline_lines = _wrap(draw, headline_text, headline_font, width - margin * 2)
                    headline_y = int(height * 0.170)
                    for line in headline_lines[:2]:
                        bbox = draw.textbbox((0, 0), line, font=headline_font)
                        x = (width - (bbox[2] - bbox[0])) // 2
                        draw.text((x + 4, headline_y + 4), line, font=headline_font, fill=(0, 0, 0, 190))
                        draw.text((x, headline_y), line, font=headline_font, fill=(255, 220, 92, 255))
                        headline_y += int(headline_font.size * 1.08)

                    left_column_x1 = int(width * 0.50)
                    left_column_y0 = top_h + 12
                    left_column_y1 = int(height * 0.755)
                    draw.rectangle((0, left_column_y0, left_column_x1, left_column_y1), fill=(3, 12, 8, 255))

                    panel_x0 = margin
                    panel_y0 = int(height * 0.365)
                    panel_x1 = int(width * 0.475)
                    panel_y1 = int(height * 0.735)
                    draw.rounded_rectangle((panel_x0 + 8, panel_y0 + 8, panel_x1 + 8, panel_y1 + 8), radius=22, fill=(0, 0, 0, 95))
                    draw.rounded_rectangle((panel_x0, panel_y0, panel_x1, panel_y1), radius=22, fill=(6, 13, 9, 205), outline=(232, 184, 84, 230), width=3)
                    section_font = _font(ImageFont, max(22, int(width * 0.026)), bold=True, text="Snack Picks")
                    content_x0 = panel_x0 + 30
                    content_x1 = panel_x1 - 24
                    heading_y = panel_y0 + 18
                    draw.text((content_x0 + 2, heading_y + 2), "Snack Picks", font=section_font, fill=(0, 0, 0, 160))
                    draw.text((content_x0, heading_y), "Snack Picks", font=section_font, fill=(255, 216, 92, 255))
                    rule_y = heading_y + section_font.size + 8
                    draw.line((content_x0, rule_y, content_x1, rule_y), fill=(232, 184, 84, 170), width=2)

                    rows = len(items)
                    row_area_top = rule_y + 13
                    row_area_bottom = panel_y1 - 16
                    row_h = (row_area_bottom - row_area_top) // max(1, rows)
                    if row_h < 31:
                        raise FlyerRenderError(f"menu overlay cannot fit all {len(items)} items (drew 0)")
                    item_font = _font(ImageFont, max(21, min(int(width * 0.030), row_h - 2)), bold=True)
                    for idx, item in enumerate(items):
                        y0_item = row_area_top + idx * row_h
                        name, _price = _split_item_price(item)
                        name_lines = _wrap(draw, name, item_font, content_x1 - content_x0 - 42)
                        if len(name_lines) > 2:
                            raise FlyerRenderError(f"menu overlay cannot fit all {len(items)} items (drew {idx})")
                        text_y = y0_item + max(0, (row_h - len(name_lines) * int(item_font.size * 0.98)) // 2)
                        dot_y = text_y + int(item_font.size * 0.47)
                        draw.regular_polygon((content_x0 + 9, dot_y, 7), n_sides=5, rotation=-90, fill=(245, 196, 52, 255))
                        for ln in name_lines:
                            draw.text((content_x0 + 30 + 2, text_y + 2), ln, font=item_font, fill=(0, 0, 0, 170))
                            draw.text((content_x0 + 30, text_y), ln, font=item_font, fill=(255, 255, 246, 255))
                            text_y += int(item_font.size * 0.98)

                    band_y0 = int(height * 0.755)
                    band_y1 = int(height * 0.910)
                    draw.rounded_rectangle((0, band_y0 + 10, width, band_y1 + 10), radius=8, fill=(0, 0, 0, 120))
                    draw.rectangle((0, band_y0, width, band_y1), fill=(111, 13, 21, 236))
                    draw.rectangle((0, band_y0, width, band_y0 + 20), fill=(236, 170, 54, 238))
                    draw.rectangle((0, band_y1 - 20, width, band_y1), fill=(236, 170, 54, 238))

                    label_font = _font(ImageFont, max(33, int(width * 0.040)), bold=True, text=shared_offer_label)
                    price_font_big = _font(ImageFont, max(76, int(width * 0.092)), bold=True, text=shared_offer_price)
                    combo_font = _font(ImageFont, max(26, int(width * 0.032)), bold=True, text=combo_text)
                    combo_y = band_y0 + 20
                    if combo_text:
                        combo_lines = _wrap(draw, combo_text, combo_font, int(width * 0.48))
                        for line in combo_lines[:1]:
                            draw.text((margin + 10 + 2, combo_y + 2), line, font=combo_font, fill=(0, 0, 0, 170))
                            draw.text((margin + 10, combo_y), line, font=combo_font, fill=(255, 225, 121, 255))
                    label_lines = _wrap(draw, shared_offer_label.upper(), label_font, int(width * 0.52))
                    label_y = band_y0 + 66
                    for line in label_lines[:1]:
                        draw.text((margin + 10 + 3, label_y + 3), line, font=label_font, fill=(0, 0, 0, 180))
                        draw.text((margin + 10, label_y), line, font=label_font, fill=(255, 244, 205, 255))
                    price_bbox = draw.textbbox((0, 0), shared_offer_price, font=price_font_big)
                    price_w = price_bbox[2] - price_bbox[0]
                    price_x = width - margin - price_w - 24
                    price_y = band_y0 + max(24, (band_y1 - band_y0 - price_font_big.size) // 2)
                    draw.text((price_x + 5, price_y + 5), shared_offer_price, font=price_font_big, fill=(0, 0, 0, 185))
                    draw.text((price_x, price_y), shared_offer_price, font=price_font_big, fill=(255, 218, 82, 255))

                    draw.rectangle((0, band_y1, width, height), fill=(52, 24, 17, 255))

                    if footer:
                        footer_y0 = int(height * 0.928)
                        footer_y1 = height - margin
                        footer_font = _font(ImageFont, max(19, int(width * 0.022)), bold=True, text=footer)
                        draw.rounded_rectangle((margin, footer_y0, width - margin, footer_y1), radius=16, fill=(7, 10, 8, 190), outline=(232, 184, 84, 135), width=1)
                        footer_bbox = draw.textbbox((0, 0), footer, font=footer_font)
                        footer_y = footer_y0 + max(4, (footer_y1 - footer_y0 - (footer_bbox[3] - footer_bbox[1])) // 2)
                        draw.text((margin + 18, footer_y), footer, font=footer_font, fill=(255, 239, 190, 250))
                    img.save(target, format="PNG", optimize=True)
                    return

                menu_panel = (margin, int(height * 0.405), int(width * 0.585), int(height * 0.875))
                px0, py0, px1, py1 = menu_panel
                draw.rounded_rectangle((px0 + 7, py0 + 7, px1 + 7, py1 + 7), radius=24, fill=(0, 0, 0, 68))
                draw.rounded_rectangle(menu_panel, radius=24, fill=(18, 9, 9, 182), outline=(232, 184, 84, 210), width=2)
                content_x0 = px0 + 22
                content_x1 = px1 - 22
                section_font = _font(ImageFont, max(22, int(width * 0.024)), bold=True, text="Snack Picks")
                cols = 2 if width >= 900 and len(items) > 4 else 1
                gap = 18
                rows = (len(items) + cols - 1) // cols
                top_pad = 72
                row_gap = 8
                col_w = (content_x1 - content_x0 - gap * (cols - 1)) // cols
                row_h = ((py1 - py0 - top_pad - 22) - row_gap * (rows - 1)) // max(1, rows)
                if row_h < 42:
                    raise FlyerRenderError(f"menu overlay cannot fit all {len(items)} items (drew 0)")
                item_font = _font(ImageFont, max(20, min(int(width * 0.027), row_h - 8)), bold=True)
                heading_y = py0 + 18
                draw.text((content_x0, heading_y), "Snack Picks", font=section_font, fill=(255, 225, 142, 255))
                rule_y = heading_y + section_font.size + 8
                draw.line((content_x0, rule_y, content_x1, rule_y), fill=(232, 184, 84, 145), width=2)
                start_y = py0 + top_pad
                for idx, item in enumerate(items):
                    col = idx % cols
                    row = idx // cols
                    x = content_x0 + col * (col_w + gap)
                    cy = start_y + row * (row_h + row_gap)
                    if cy + row_h > py1 - 18:
                        raise FlyerRenderError(f"menu overlay cannot fit all {len(items)} items (drew {idx})")
                    name, _price = _split_item_price(item)
                    name_lines = _wrap(draw, name, item_font, col_w - 34)
                    name_y = cy + max(0, (row_h - len(name_lines) * int(item_font.size * 1.03)) // 2)
                    dot_y = name_y + int(item_font.size * 0.48)
                    draw.ellipse((x, dot_y - 5, x + 10, dot_y + 5), fill=(245, 207, 94, 255))
                    for ln in name_lines:
                        draw.text((x + 20 + 2, name_y + 2), ln, font=item_font, fill=(0, 0, 0, 130))
                        draw.text((x + 20, name_y), ln, font=item_font, fill=(255, 247, 226, 255))
                        name_y += int(item_font.size * 1.03)

                deal_x0 = int(width * 0.61)
                deal_y0 = int(height * 0.535)
                deal_x1 = width - margin
                deal_y1 = int(height * 0.775)
                draw.rounded_rectangle((deal_x0 + 8, deal_y0 + 8, deal_x1 + 8, deal_y1 + 8), radius=24, fill=(0, 0, 0, 80))
                draw.rounded_rectangle((deal_x0, deal_y0, deal_x1, deal_y1), radius=24, fill=(92, 13, 24, 232), outline=(246, 214, 132, 230), width=3)
                draw.line((deal_x0 + 28, deal_y0 + 70, deal_x1 - 28, deal_y0 + 70), fill=(246, 214, 132, 210), width=2)
                deal_label_font = _font(ImageFont, max(24, int(width * 0.028)), bold=True, text=shared_offer_label)
                deal_price_font = _font(ImageFont, max(70, int(width * 0.083)), bold=True, text=shared_offer_price)
                label_y = deal_y0 + 24
                for line in _wrap(draw, shared_offer_label.upper(), deal_label_font, deal_x1 - deal_x0 - 56):
                    draw.text((deal_x0 + 28, label_y), line, font=deal_label_font, fill=(255, 242, 204, 255))
                    label_y += int(deal_label_font.size * 1.06)
                price_bbox = draw.textbbox((0, 0), shared_offer_price, font=deal_price_font)
                price_w = price_bbox[2] - price_bbox[0]
                price_x = deal_x0 + max(24, (deal_x1 - deal_x0 - price_w) // 2)
                price_y = deal_y1 - deal_price_font.size - 26
                draw.text((price_x + 3, price_y + 3), shared_offer_price, font=deal_price_font, fill=(0, 0, 0, 145))
                draw.text((price_x, price_y), shared_offer_price, font=deal_price_font, fill=(255, 255, 246, 255))

                footer = " | ".join(str(v) for v in (menu_payload["location"], menu_payload["contact"]) if v)
                if footer:
                    footer_y0 = int(height * 0.935)
                    footer_y1 = height - margin
                    draw.rounded_rectangle((margin, footer_y0, width - margin, footer_y1), radius=16, fill=(18, 9, 9, 155), outline=(232, 184, 84, 110), width=1)
                    footer_bbox = draw.textbbox((0, 0), footer, font=small_font)
                    footer_y = footer_y0 + max(4, (footer_y1 - footer_y0 - (footer_bbox[3] - footer_bbox[1])) // 2)
                    draw.text((margin + 18, footer_y), footer, font=small_font, fill=(255, 232, 176, 245))
                img.save(target, format="PNG", optimize=True)
                return
            compact_menu = len(items) > MAX_DETAIL_FACTS or bool(shared_offer_price and len(items) >= 8)
            panel_top_ratio = 0.42 if shared_offer_price and len(items) >= 8 else (0.50 if compact_menu else 0.56)
            panel = (margin, int(height * panel_top_ratio), width - margin, height - margin)
            draw.rounded_rectangle((panel[0] + 8, panel[1] + 8, panel[2] + 8, panel[3] + 8), radius=28, fill=(0, 0, 0, 75))
            draw.rounded_rectangle(panel, radius=28, fill=(255, 247, 222, 246), outline=(179, 37, 47, 238), width=4)
            px0, py0, px1, py1 = panel
            # No hardcoded English "MENU" label — keeps the overlay language-neutral
            # (item cards are self-evidently a menu). `_font` already renders the
            # actual fact text in its own script (Telugu/Indic) via _has_telugu.
            # Localizing facts captured in the wrong language is an intake concern.
            cols = 3 if compact_menu and width >= 900 else (2 if width >= 900 and len(items) > 3 else 1)
            if len(items) > MAX_COMPACT_MENU_DETAIL_FACTS:
                raise FlyerRenderError(
                    f"menu overlay cannot fit all {len(items)} items (drew 0)"
                )
            gap = 16
            if shared_offer_price and not has_item_prices:
                cols = 2 if width >= 900 and len(items) > 4 else 1
            card_w = (px1 - px0 - 56 - gap * (cols - 1)) // cols
            # Size cards so the full allowed item count (MAX_DETAIL_FACTS = 10, i.e.
            # up to 5 two-column rows) fits the panel: subtract the start offset
            # (66), the footer reserve (58), and the 10px inter-row gaps from the
            # available height before dividing by rows. Min 50 lets 5 rows fit.
            rows = (len(items) + cols - 1) // cols
            top_pad = 24 if compact_menu else 34
            row_gap = 8 if compact_menu else 12
            footer_reserve = 48 if compact_menu else 62
            card_h_raw = ((py1 - py0 - top_pad - footer_reserve - 20) - row_gap * (rows - 1)) // max(1, rows)
            min_card_h = 46 if compact_menu else (86 if rows <= 3 else 58)
            card_h = max(min_card_h, min(128, card_h_raw))
            if compact_menu:
                item_font = _font(ImageFont, max(17, int(width * 0.018)), bold=True)
                price_font = _font(ImageFont, max(18, int(width * 0.020)), bold=True)
            elif rows >= 5:
                item_font = _font(ImageFont, max(21, int(width * 0.026)), bold=True)
                price_font = _font(ImageFont, max(22, int(width * 0.028)), bold=True)
            elif rows >= 4:
                item_font = _font(ImageFont, max(23, int(width * 0.029)), bold=True)
                price_font = _font(ImageFont, max(24, int(width * 0.031)), bold=True)
            if shared_offer_price and not has_item_prices:
                item_font = _font(ImageFont, max(27, int(width * 0.030)), bold=True)
            start_y = py0 + top_pad
            for idx, item in enumerate(items):
                col = idx % cols
                row = idx // cols
                x = px0 + 28 + col * (card_w + gap)
                cy = start_y + row * (card_h + row_gap)
                if cy + card_h > py1 - footer_reserve:
                    # Fail closed instead of silently dropping items. Under the
                    # background-only contract the overlay is the SOLE source of
                    # item facts (the model draws none), and `write_text_manifest`
                    # declares every collected item — so a silent break would ship
                    # a flyer missing required items with no QA blocker. Raising
                    # routes the project to manual review (same fail-closed contract
                    # as the title card / non-menu critical panel).
                    raise FlyerRenderError(
                        f"menu overlay cannot fit all {len(items)} items (drew {idx})"
                    )
                draw.rounded_rectangle((x + 5, cy + 5, x + card_w + 5, cy + card_h + 5), radius=18, fill=(0, 0, 0, 45))
                draw.rounded_rectangle((x, cy, x + card_w, cy + card_h), radius=18, fill=(255, 253, 244, 245), outline=(223, 176, 72, 235), width=3)
                name, price = _split_item_price(item)
                price_bbox = draw.textbbox((0, 0), price, font=price_font)
                price_w = price_bbox[2] - price_bbox[0]
                text_w = max(160, card_w - price_w - 56) if price else card_w - 74
                name_lines = _wrap(draw, name, item_font, text_w)
                name_y = cy + max(18, (card_h - len(name_lines) * int(item_font.size * 1.05)) // 2)
                if not price:
                    dot_y = cy + card_h // 2
                    draw.ellipse((x + 22, dot_y - 6, x + 34, dot_y + 6), fill=(180, 42, 50, 255))
                for ln in name_lines:
                    draw.text((x + (46 if not price else 22), name_y), ln, font=item_font, fill=(64, 42, 32, 255))
                    name_y += int(item_font.size * 1.05)
                if price:
                    draw.text((x + card_w - 22 - price_w, cy + (card_h - price_font.size) // 2), price, font=price_font, fill=(150, 24, 38, 255))
            footer = " | ".join(str(v) for v in (menu_payload["location"], menu_payload["contact"]) if v)
            if footer:
                draw.text((px0 + 28, py1 - 44), footer, font=small_font, fill=(54, 65, 48, 255))
            img.save(target, format="PNG", optimize=True)
            return
        margin = max(24, int(width * 0.035))
        panel_h = min(int(height * 0.60), max(int(height * 0.24), 58 + len(_critical_lines(project)) * max(30, int(width * 0.032))))
        y0 = height - panel_h - margin
        draw.rounded_rectangle((margin, y0, width - margin, height - margin), radius=18, fill=(12, 16, 24, 218), outline=(255, 196, 58, 240), width=3)
        y = y0 + int(margin * 0.75)
        lines = _critical_lines(project)
        for idx, line in enumerate(lines):
            font = _font(ImageFont, max(30, int(width * 0.043)), bold=True, text=line) if idx == 0 else _font(ImageFont, max(17, int(width * 0.021)), text=line)
            fill = (255, 214, 79, 255) if idx == 0 else (255, 255, 255, 245)
            wrapped = _wrap(draw, line, font, width - margin * 3)
            for wrapped_line in wrapped:
                if y + font.size > height - margin:
                    raise FlyerRenderError("critical text overlay does not fit")
                draw.text((margin + 18, y), wrapped_line, font=font, fill=fill)
                y += int(font.size * 1.18)
        img.save(target, format="PNG", optimize=True)


def _split_item_price(item: str) -> tuple[str, str]:
    match = re.search(r"(.+?)\s+(\$\s*\d+(?:\.\d{1,2})?)$", item.strip())
    if not match:
        return item.strip(), ""
    name = re.sub(r"\bfor$", "", match.group(1).strip(), flags=re.IGNORECASE).strip()
    return name, match.group(2).replace(" ", "")


OVERLAY_RENDERER = r'''
import json, re, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
spec=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
src=Path(spec["source"]); target=Path(spec["target"]); size=tuple(spec["size"]); lines=spec["lines"]
menu=spec.get("menu_payload") or {}
def has_telugu(text):
    return any("\\u0c00" <= ch <= "\\u0c7f" for ch in text or "")
def font(sz,bold=False,text=""):
    c=["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf","/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf","/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"]
    if has_telugu(text): c.insert(0,"/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf")
    if bold: c.insert(0,"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    for p in c:
        try:
            if Path(p).exists(): return ImageFont.truetype(p, sz)
        except OSError: pass
    return ImageFont.load_default()
def wrap(draw,text,f,maxw):
    words=(text or "").split(); out=[]; cur=""
    for w in words:
        cand=(cur+" "+w).strip(); box=draw.textbbox((0,0),cand,font=f)
        if box[2]-box[0] <= maxw or not cur: cur=cand
        else: out.append(cur); cur=w
    if cur: out.append(cur)
    return out
with Image.open(src) as img:
    img=img.convert("RGB")
    if img.size != size: img=img.resize(size)
    draw=ImageDraw.Draw(img,"RGBA"); width,height=size; margin=max(24,int(width*.035))
    if menu.get("items"):
        biz=str(menu.get("business") or "").strip(); title=str(menu.get("title") or "").strip(); schedule=str(menu.get("schedule") or "").strip()
        items=list(menu.get("items") or []); footer=" | ".join(str(v) for v in (menu.get("location"),menu.get("contact")) if v)
        shared_text=str(menu.get("shared_offer_text") or "").strip(); shared_label=str(menu.get("shared_offer_label") or "").strip(); shared_price=str(menu.get("shared_offer_price") or "").strip()
        has_prices=any(re.search(r"\$\s*\d",str(item)) for item in items)
        shared_snack_poster=bool(shared_price and items and not has_prices)
        tf=font(max(42,int(width*.056)),True,title); bf=font(max(24,int(width*.030)),True,biz)
        sf=font(max(19,int(width*.021)),False); itemf=font(max(28,int(width*.034)),True); pricef=font(max(30,int(width*.038)),True)
        bx0,by0,bx1=margin,int(height*.026),int(width*(.58 if shared_price else .68)); inner=bx1-bx0-44
        card=[]
        if biz and biz.lower()!=title.lower():
            for ln in wrap(draw,biz,bf,inner): card.append((bf,(50,66,42,255),ln))
        for ln in wrap(draw,title,tf,inner): card.append((tf,(128,22,34,255),ln))
        if schedule:
            for ln in wrap(draw,schedule,sf,inner): card.append((sf,(128,22,34,255),ln))
        for extra in menu.get("extras") or []:
            if shared_text and str(extra).strip().lower()==shared_text.lower(): continue
            for ln in wrap(draw,str(extra),sf,inner): card.append((sf,(50,66,42,255),ln))
        bh=sum(int(getattr(f,"size",18)*1.2) for f,_c,_t in card)+34; by1=by0+bh
        if by1 > int(height*.50): raise SystemExit("critical text overlay does not fit")
        if shared_snack_poster:
            my1=int(height*.335); draw.rectangle((0,0,width,my1),fill=(8,5,6,224)); draw.rectangle((0,my1-18,width,my1),fill=(8,5,6,150))
            y=by0+10
            for f,c,ln in card:
                if getattr(f,"size",18) >= tf.size: color=(255,226,130,255)
                elif getattr(f,"size",18) >= bf.size: color=(255,248,222,255)
                else: color=(255,232,174,255)
                draw.text((bx0+14,y+4), ln, font=f, fill=(45,8,12,190))
                draw.text((bx0+10,y), ln, font=f, fill=color)
                y += int(getattr(f,"size",18)*1.2)
        else:
            draw.rounded_rectangle((bx0+7,by0+7,bx1+7,by1+7), radius=24, fill=(0,0,0,70))
            draw.rounded_rectangle((bx0,by0,bx1,by1), radius=24, fill=(255,248,224,248), outline=(179,37,47,235), width=3)
            y=by0+18
            for f,c,ln in card:
                draw.text((bx0+22,y), ln, font=f, fill=c); y += int(getattr(f,"size",18)*1.2)
        if shared_price and not shared_snack_poster:
            gx0=max(bx1+18,int(width*.60)); gx1=width-margin; gy0=by0; gy1=max(by1,gy0+int(height*.115))
            if gx1-gx0 < int(width*.22) or gy1 > int(height*.50): raise SystemExit("critical text overlay does not fit")
            lf=font(max(22,int(width*.025)),True,shared_label); pf=font(max(56,int(width*.072)),True,shared_price)
            draw.rounded_rectangle((gx0+5,gy0+5,gx1+5,gy1+5), radius=18, fill=(0,0,0,72))
            draw.rounded_rectangle((gx0,gy0,gx1,gy1), radius=18, fill=(92,13,24,232), outline=(246,214,132,230), width=2)
            ly=gy0+18
            for ln in wrap(draw,shared_label,lf,gx1-gx0-40):
                draw.text((gx0+20,ly),ln,font=lf,fill=(255,242,204,255)); ly += int(lf.size*1.08)
            pbox=draw.textbbox((0,0),shared_price,font=pf); pw=pbox[2]-pbox[0]; px=gx0+max(20,(gx1-gx0-pw)//2)
            draw.text((px+2,gy1-pf.size-17),shared_price,font=pf,fill=(0,0,0,130))
            draw.text((px,gy1-pf.size-19),shared_price,font=pf,fill=(255,255,246,255))
        if shared_price and not has_prices:
            if shared_snack_poster:
                footer=" | ".join(str(v) for v in (menu.get("location"),menu.get("contact")) if v)
                headline=title.upper()
                if "STREET" not in headline and "SNACK" in shared_label.upper(): headline="STREET SNACK SPECIALS"
                combo=title.upper(); sched=schedule.upper()
                draw.rectangle((0,0,width,height),fill=(0,0,0,44))
                th=int(height*.325); draw.rectangle((0,0,width,th),fill=(5,8,6,255))
                for i,alpha in enumerate((210,156,104)):
                    ins=i*16
                    draw.arc((22+ins,60+ins,298-ins,282-ins),190,332,fill=(190,24,26,alpha),width=14)
                    draw.arc((width-298+ins,60+ins,width-22-ins,282-ins),208,350,fill=(190,24,26,alpha),width=14)
                for x0,flip in ((50,1),(width-50,-1)):
                    for idx in range(6):
                        y0=66+idx*25; x1=x0+flip*(92+idx*13); draw.line((x0,y0,x1,y0+10),fill=(232,172,58,118),width=3)
                mx0=int(width*.17); mx1=int(width*.83); my0=int(height*.030); my1=int(height*.112)
                draw.rounded_rectangle((mx0+7,my0+7,mx1+7,my1+7),radius=28,fill=(0,0,0,110))
                draw.rounded_rectangle((mx0,my0,mx1,my1),radius=28,fill=(248,244,224,248),outline=(232,184,84,235),width=3)
                sr=int((my1-my0)*.34); scx=mx0+sr+26; scy=(my0+my1)//2
                draw.ellipse((scx-sr,scy-sr,scx+sr,scy+sr),fill=(18,63,42,255),outline=(210,154,45,255),width=3)
                skf=font(max(16,int(width*.020)),True,"LK"); sb=draw.textbbox((0,0),"LK",font=skf)
                draw.text((scx-(sb[2]-sb[0])//2,scy-(sb[3]-sb[1])//2-2),"LK",font=skf,fill=(255,231,142,255))
                draw.rectangle((0,th-28,width,th),fill=(122,18,18,150))
                draw.line((margin,th-86,width-margin,th-86),fill=(235,178,58,230),width=4); draw.line((margin,th-18,width-margin,th-18),fill=(235,178,58,230),width=4)
                bf=font(max(31,int(width*.039)),True,biz); hf=font(max(58,int(width*.070)),True,headline); scf=font(max(24,int(width*.030)),True,sched)
                if biz:
                    for ln in wrap(draw,biz,bf,width-margin*2):
                        box=draw.textbbox((0,0),ln,font=bf); x=mx0+88+max(12,(mx1-mx0-104-(box[2]-box[0]))//2); y=my0+max(6,(my1-my0-(box[3]-box[1]))//2)-2
                        draw.text((x+2,y+2),ln,font=bf,fill=(190,155,87,110)); draw.text((x,y),ln,font=bf,fill=(26,68,45,255)); break
                if sched:
                    box=draw.textbbox((0,0),sched,font=scf); x=(width-(box[2]-box[0]))//2
                    sy=int(height*.122); draw.text((x+2,sy+2),sched,font=scf,fill=(0,0,0,160)); draw.text((x,sy),sched,font=scf,fill=(255,244,214,255))
                hy=int(height*.170)
                for ln in wrap(draw,headline,hf,width-margin*2)[:2]:
                    box=draw.textbbox((0,0),ln,font=hf); x=(width-(box[2]-box[0]))//2
                    draw.text((x+4,hy+4),ln,font=hf,fill=(0,0,0,190)); draw.text((x,hy),ln,font=hf,fill=(255,220,92,255)); hy += int(hf.size*1.08)
                draw.rectangle((0,th+12,int(width*.50),int(height*.755)),fill=(3,12,8,255))
                px0=margin; py0=int(height*.365); px1=int(width*.475); py1=int(height*.735)
                draw.rounded_rectangle((px0+8,py0+8,px1+8,py1+8),radius=22,fill=(0,0,0,95))
                draw.rounded_rectangle((px0,py0,px1,py1),radius=22,fill=(6,13,9,205),outline=(232,184,84,230),width=3)
                cx0=px0+30; cx1=px1-24; secf=font(max(22,int(width*.026)),True,"Snack Picks"); shy=py0+18
                draw.text((cx0+2,shy+2),"Snack Picks",font=secf,fill=(0,0,0,160)); draw.text((cx0,shy),"Snack Picks",font=secf,fill=(255,216,92,255))
                ry=shy+secf.size+8; draw.line((cx0,ry,cx1,ry),fill=(232,184,84,170),width=2)
                rows=len(items); rat=ry+13; rab=py1-16; rowh=(rab-rat)//max(1,rows)
                if rowh < 31: raise SystemExit(f"menu overlay cannot fit all {len(items)} items (drew 0)")
                itf=font(max(21,min(int(width*.030),rowh-2)),True)
                for idx,item in enumerate(items):
                    iy=rat+idx*rowh; name=str(item).strip(); lines2=wrap(draw,name,itf,cx1-cx0-42)
                    if len(lines2)>2: raise SystemExit(f"menu overlay cannot fit all {len(items)} items (drew {idx})")
                    ty=iy+max(0,(rowh-len(lines2)*int(itf.size*.98))//2); dy=ty+int(itf.size*.47)
                    draw.ellipse((cx0+2,dy-7,cx0+16,dy+7),fill=(245,196,52,255))
                    for ln in lines2:
                        draw.text((cx0+32,ty+2),ln,font=itf,fill=(0,0,0,170)); draw.text((cx0+30,ty),ln,font=itf,fill=(255,255,246,255)); ty += int(itf.size*.98)
                by0=int(height*.755); by1=int(height*.910)
                draw.rounded_rectangle((0,by0+10,width,by1+10),radius=8,fill=(0,0,0,120))
                draw.rectangle((0,by0,width,by1),fill=(111,13,21,236)); draw.rectangle((0,by0,width,by0+20),fill=(236,170,54,238)); draw.rectangle((0,by1-20,width,by1),fill=(236,170,54,238))
                lf=font(max(33,int(width*.040)),True,shared_label); pf=font(max(76,int(width*.092)),True,shared_price); cf=font(max(26,int(width*.032)),True,combo)
                if combo:
                    for ln in wrap(draw,combo,cf,int(width*.48))[:1]:
                        draw.text((margin+12,by0+22),ln,font=cf,fill=(0,0,0,170)); draw.text((margin+10,by0+20),ln,font=cf,fill=(255,225,121,255))
                for ln in wrap(draw,shared_label.upper(),lf,int(width*.52))[:1]:
                    draw.text((margin+13,by0+69),ln,font=lf,fill=(0,0,0,180)); draw.text((margin+10,by0+66),ln,font=lf,fill=(255,244,205,255))
                pbox=draw.textbbox((0,0),shared_price,font=pf); pw=pbox[2]-pbox[0]; px=width-margin-pw-24; py=by0+max(24,(by1-by0-pf.size)//2)
                draw.text((px+5,py+5),shared_price,font=pf,fill=(0,0,0,185)); draw.text((px,py),shared_price,font=pf,fill=(255,218,82,255))
                draw.rectangle((0,by1,width,height),fill=(52,24,17,255))
                if footer:
                    fy0=int(height*.928); fy1=height-margin; ff=font(max(19,int(width*.022)),True,footer)
                    draw.rounded_rectangle((margin,fy0,width-margin,fy1),radius=16,fill=(7,10,8,190),outline=(232,184,84,135),width=1)
                    fb=draw.textbbox((0,0),footer,font=ff); fy=fy0+max(4,(fy1-fy0-(fb[3]-fb[1]))//2)
                    draw.text((margin+18,fy),footer,font=ff,fill=(255,239,190,250))
                target.parent.mkdir(parents=True, exist_ok=True); img.save(target, format="PNG", optimize=True); raise SystemExit(0)
            panel=(margin,int(height*.405),int(width*.585),int(height*.875))
            px0,py0,px1,py1=panel
            draw.rounded_rectangle((px0+7,py0+7,px1+7,py1+7), radius=24, fill=(0,0,0,68))
            draw.rounded_rectangle(panel, radius=24, fill=(18,9,9,182), outline=(232,184,84,210), width=2)
            cx0=px0+22; cx1=px1-22; secf=font(max(22,int(width*.024)),True,"Snack Picks")
            cols=2 if width>=900 and len(items)>4 else 1; gap=18; rows=(len(items)+cols-1)//cols; top_pad=72; row_gap=8
            colw=(cx1-cx0-gap*(cols-1))//cols; rowh=((py1-py0-top_pad-22)-row_gap*(rows-1))//max(1,rows)
            if rowh < 42: raise SystemExit(f"menu overlay cannot fit all {len(items)} items (drew 0)")
            itemf=font(max(20,min(int(width*.027),rowh-8)),True)
            hy=py0+18; draw.text((cx0,hy),"Snack Picks",font=secf,fill=(255,225,142,255))
            ry=hy+secf.size+8; draw.line((cx0,ry,cx1,ry),fill=(232,184,84,145),width=2)
            start=py0+top_pad
            for idx,item in enumerate(items):
                col=idx%cols; row=idx//cols; x=cx0+col*(colw+gap); cy=start+row*(rowh+row_gap)
                if cy+rowh > py1-18: raise SystemExit(f"menu overlay cannot fit all {len(items)} items (drew {idx})")
                name=str(item).strip(); lines2=wrap(draw,name,itemf,colw-34); ny=cy+max(0,(rowh-len(lines2)*int(itemf.size*1.03))//2); dy=ny+int(itemf.size*.48)
                draw.ellipse((x,dy-5,x+10,dy+5),fill=(245,207,94,255))
                for ln in lines2:
                    draw.text((x+22,ny+2),ln,font=itemf,fill=(0,0,0,130)); draw.text((x+20,ny),ln,font=itemf,fill=(255,247,226,255)); ny += int(itemf.size*1.03)
            dx0=int(width*.61); dy0=int(height*.535); dx1=width-margin; dy1=int(height*.775)
            draw.rounded_rectangle((dx0+8,dy0+8,dx1+8,dy1+8), radius=24, fill=(0,0,0,80))
            draw.rounded_rectangle((dx0,dy0,dx1,dy1), radius=24, fill=(92,13,24,232), outline=(246,214,132,230), width=3)
            draw.line((dx0+28,dy0+70,dx1-28,dy0+70), fill=(246,214,132,210), width=2)
            lf=font(max(24,int(width*.028)),True,shared_label); pf=font(max(70,int(width*.083)),True,shared_price)
            ly=dy0+24
            for ln in wrap(draw,shared_label.upper(),lf,dx1-dx0-56):
                draw.text((dx0+28,ly),ln,font=lf,fill=(255,242,204,255)); ly += int(lf.size*1.06)
            pbox=draw.textbbox((0,0),shared_price,font=pf); pw=pbox[2]-pbox[0]; px=dx0+max(24,(dx1-dx0-pw)//2); py=dy1-pf.size-26
            draw.text((px+3,py+3),shared_price,font=pf,fill=(0,0,0,145))
            draw.text((px,py),shared_price,font=pf,fill=(255,255,246,255))
            if footer:
                fy0=int(height*.935); fy1=height-margin
                draw.rounded_rectangle((margin,fy0,width-margin,fy1), radius=16, fill=(18,9,9,155), outline=(232,184,84,110), width=1)
                fbox=draw.textbbox((0,0),footer,font=sf); fy=fy0+max(4,(fy1-fy0-(fbox[3]-fbox[1]))//2)
                draw.text((margin+18,fy),footer,font=sf,fill=(255,232,176,245))
            target.parent.mkdir(parents=True, exist_ok=True); img.save(target, format="PNG", optimize=True); raise SystemExit(0)
        compact=len(items)>10 or bool(shared_price and len(items)>=8)
        panel=(margin,int(height*(.42 if shared_price and len(items)>=8 else (.50 if compact else .56))),width-margin,height-margin)
        draw.rounded_rectangle((panel[0]+8,panel[1]+8,panel[2]+8,panel[3]+8), radius=28, fill=(0,0,0,75))
        draw.rounded_rectangle(panel, radius=28, fill=(255,247,222,246), outline=(179,37,47,238), width=4)
        px0,py0,px1,py1=panel; cols=3 if compact and width>=900 else (2 if width>=900 and len(items)>3 else 1); gap=16
        if shared_price and not has_prices: cols=2 if width>=900 and len(items)>4 else 1
        if len(items)>18: raise SystemExit(f"menu overlay cannot fit all {len(items)} items (drew 0)")
        cardw=(px1-px0-56-gap*(cols-1))//cols; rows=(len(items)+cols-1)//cols
        top_pad=24 if compact else 34; row_gap=8 if compact else 12; footer_reserve=48 if compact else 62
        rawh=((py1-py0-top_pad-footer_reserve-20)-row_gap*(rows-1))//max(1,rows); cardh=max(46 if compact else (86 if rows<=3 else 58),min(128,rawh))
        if compact:
            itemf=font(max(17,int(width*.018)),True); pricef=font(max(18,int(width*.020)),True)
        elif rows>=5:
            itemf=font(max(21,int(width*.026)),True); pricef=font(max(22,int(width*.028)),True)
        elif rows>=4:
            itemf=font(max(23,int(width*.029)),True); pricef=font(max(24,int(width*.031)),True)
        if shared_price and not has_prices: itemf=font(max(27,int(width*.030)),True)
        def split_price(item):
            m=re.search(r"(.+?)\\s+(\\$\\s*\\d+(?:\\.\\d{1,2})?)$", str(item).strip())
            return (str(item).strip(),"") if not m else (re.sub(r"\\bfor$","",m.group(1).strip(),flags=re.I).strip(), m.group(2).replace(" ",""))
        start=py0+top_pad
        for idx,item in enumerate(items):
            col=idx%cols; row=idx//cols; x=px0+28+col*(cardw+gap); cy=start+row*(cardh+row_gap)
            if cy+cardh > py1-footer_reserve: raise SystemExit(f"menu overlay cannot fit all {len(items)} items (drew {idx})")
            draw.rounded_rectangle((x+5,cy+5,x+cardw+5,cy+cardh+5), radius=18, fill=(0,0,0,45))
            draw.rounded_rectangle((x,cy,x+cardw,cy+cardh), radius=18, fill=(255,253,244,245), outline=(223,176,72,235), width=3)
            name,price=split_price(item); pbox=draw.textbbox((0,0),price,font=pricef); pw=pbox[2]-pbox[0]
            name_lines=wrap(draw,name,itemf,max(160,cardw-pw-56) if price else cardw-74); ny=cy+max(10,(cardh-len(name_lines)*int(itemf.size*1.05))//2)
            if not price:
                dy=cy+cardh//2; draw.ellipse((x+22,dy-6,x+34,dy+6),fill=(180,42,50,255))
            for ln in name_lines:
                draw.text((x+(46 if not price else 22),ny),ln,font=itemf,fill=(64,42,32,255)); ny += int(itemf.size*1.05)
            if price: draw.text((x+cardw-22-pw,cy+(cardh-pricef.size)//2),price,font=pricef,fill=(150,24,38,255))
        if footer: draw.text((px0+28,py1-44),footer,font=sf,fill=(54,65,48,255))
        target.parent.mkdir(parents=True, exist_ok=True); img.save(target, format="PNG", optimize=True); raise SystemExit(0)
    panel_h=min(int(height*.60),max(int(height*.24),58+len(lines)*max(30,int(width*.032))))
    y0=height-panel_h-margin
    draw.rounded_rectangle((margin,y0,width-margin,height-margin), radius=18, fill=(12,16,24,218), outline=(255,196,58,240), width=3)
    y=y0+int(margin*.75)
    for idx,line in enumerate(lines):
        f=font(max(30,int(width*.043)), True, line) if idx==0 else font(max(17,int(width*.021)), False, line); fill=(255,214,79,255) if idx==0 else (255,255,255,245)
        for wrapped in wrap(draw,line,f,width-margin*3):
            if y+f.size > height-margin:
                raise SystemExit("critical text overlay does not fit")
            draw.text((margin+18,y), wrapped, font=f, fill=fill)
            y += int(f.size*1.18)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, format="PNG", optimize=True)
'''


PREMIUM_OVERLAY_RENDERER = r'''
import json, sys, traceback
from pathlib import Path
spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for _p in reversed(spec.get("sys_path") or []):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from schemas import FlyerProject
    try:
        import flyer_premium_overlay as premium_overlay  # box (flat layout)
    except ImportError:
        from agents.flyer import premium_overlay          # repo / tests
    project = FlyerProject.model_validate_json(spec["project_json"])
    # creative_direction is exclude=True on FlyerProject (rollback-safe: never in
    # projects.json), so model_dump_json above OMITS it. Re-attach the carrier the
    # parent delivered via the spec so the overlay leads with the marketing message.
    _cd = spec.get("creative_direction")
    if _cd is not None:
        project.creative_direction = _cd
except Exception as e:
    sys.stderr.write(f"{type(e).__name__}: {e}")
    sys.exit(1)  # import/serialization error -> unexpected
try:
    premium_overlay.render_premium_overlay(
        project, Path(spec["source"]), Path(spec["target"]),
        size=tuple(spec["size"]), output_format=spec["output_format"],
    )
except Exception as e:
    # FlyerRenderError = intentional fit/coverage fail-closed -> exit 3 ;
    # anything else = unexpected renderer crash -> exit 1.
    sys.stderr.write(f"{type(e).__name__}: {e}")
    sys.exit(3 if type(e).__name__ == "FlyerRenderError" else 1)
sys.exit(0)
'''


def _classify_fail_closed_reason(message: str) -> str:
    """Best-effort reason_class for an intentional FlyerRenderError fail-closed.
    Telemetry only — the alert decision is by status, never by reason_class."""
    low = (message or "").lower()
    if "overflow" in low:
        return "overflow"
    if any(k in low for k in ("cover", "required fact", "missing", "not present")):
        return "coverage"
    return "fit"


def _premium_subprocess_sys_path() -> list[str]:
    """Import roots for the /usr/bin/python3 premium subprocess: ONLY the dir(s)
    holding the flat-deployed modules (schemas + flyer_*). Deliberately EXCLUDES
    the gateway venv's site-packages — those carry compiled extensions
    (pydantic_core, PIL) built for the venv Python's ABI, which fail to import
    under the system /usr/bin/python3 (ModuleNotFoundError:
    pydantic_core._pydantic_core). The subprocess uses /usr/bin/python3's OWN
    site-packages for pydantic + Pillow; it only needs our flat modules on path."""
    import schemas as _schemas
    roots: list[str] = []
    for f in (getattr(_schemas, "__file__", None), __file__):
        if f:
            d = os.path.dirname(os.path.abspath(f))
            if d and d not in roots:
                roots.append(d)
    return roots


def _render_premium_overlay_with_fallback(project: FlyerProject, source: Path | str, target: Path | str, *, size: tuple[int, int], output_format: str) -> PremiumOverlayOutcome:
    """Render the premium overlay in-process; on any import/runtime failure
    (the PIL-less gateway venv) re-render in a /usr/bin/python3 subprocess that
    HAS Pillow. Returns a PremiumOverlayOutcome; never raises (the caller owns
    the flat fallback)."""
    # 1) In-process attempt (the path tests + system-python contexts use).
    try:
        try:
            import flyer_premium_overlay as premium_overlay  # box (flat layout)
        except ImportError:
            from agents.flyer import premium_overlay          # repo / tests
        premium_overlay.render_premium_overlay(project, source, target, size=size, output_format=output_format)
        return PremiumOverlayOutcome("premium_overlay_delivered", "none", "", "in_process", output_format)
    except FlyerRenderError as e:
        msg = str(e)
        return PremiumOverlayOutcome("premium_overlay_degraded_to_flat", _classify_fail_closed_reason(msg), msg[:300], "none", output_format)
    except Exception as e:
        in_process_detail = f"{type(e).__name__}: {e}"  # expected on the box: ModuleNotFoundError: No module named 'PIL'

    # 2) Subprocess recovery via /usr/bin/python3 (PIL-capable), mirrors the flat OVERLAY_RENDERER path.
    if not Path("/usr/bin/python3").exists():
        return PremiumOverlayOutcome("premium_overlay_failed_unexpected", "missing_pil", in_process_detail[:300], "none", output_format)
    try:
        project_json = project.model_dump_json()
    except Exception as e:
        return PremiumOverlayOutcome("premium_overlay_failed_unexpected", "serialization_error", f"{type(e).__name__}: {e}"[:300], "none", output_format)
    spec = {
        "project_json": project_json,
        # creative_direction is exclude=True (omitted from project_json above for
        # rollback safety), so deliver the carrier to the subprocess SEPARATELY;
        # the renderer re-attaches it to the reconstructed project. None/absent is
        # fine (the renderer guards it).
        "creative_direction": getattr(project, "creative_direction", None),
        "source": str(source),
        "target": str(target),
        "size": list(size),
        "output_format": output_format,
        "sys_path": _premium_subprocess_sys_path(),
    }
    spec_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as fh:
            spec_path = fh.name
            json.dump(spec, fh)
        proc = subprocess.run(["/usr/bin/python3", "-c", PREMIUM_OVERLAY_RENDERER, spec_path], capture_output=True, text=True, timeout=60)
    except Exception as e:
        return PremiumOverlayOutcome("premium_overlay_failed_unexpected", "subprocess_failure", f"{type(e).__name__}: {e}"[:300], "none", output_format)
    finally:
        if spec_path:
            try:
                Path(spec_path).unlink(missing_ok=True)
            except OSError:
                pass
    if proc.returncode == 0:
        return PremiumOverlayOutcome("premium_overlay_delivered", "none", in_process_detail[:300], "subprocess", output_format)
    detail = (proc.stderr or proc.stdout or "").strip()
    if proc.returncode == 3:
        return PremiumOverlayOutcome("premium_overlay_degraded_to_flat", _classify_fail_closed_reason(detail), detail[:300], "none", output_format)
    return PremiumOverlayOutcome("premium_overlay_failed_unexpected", "subprocess_failure", detail[:300], "none", output_format)


def _apply_critical_text_overlay(project: FlyerProject, source: Path | str, target: Path | str, *, size: tuple[int, int], output_format: str) -> None:
    if _premium_overlay_enabled(project) and _is_food_or_grocery_project(project):
        outcome = _render_premium_overlay_with_fallback(project, source, target, size=size, output_format=output_format)
        _PREMIUM_OVERLAY_OUTCOME.set(outcome)
        if outcome.status == "premium_overlay_delivered":
            return
        if outcome.status == "premium_overlay_failed_unexpected":
            logging.getLogger(__name__).error(
                "premium overlay failed unexpectedly (%s); degrading to flat overlay: %s",
                outcome.reason_class, outcome.reason_detail,
            )
        # delivered -> returned above; degraded_to_flat / failed_unexpected -> fall through to the flat path below.
    try:
        apply_critical_text_overlay(project, source, target, size=size, output_format=output_format)
        return
    except FlyerRenderError as e:
        if "Pillow is required" not in str(e) or not Path("/usr/bin/python3").exists():
            raise
    spec = {
        "source": str(source),
        "target": str(target),
        "size": list(size),
        "output_format": output_format,
        "lines": _critical_lines(project),
        "menu_payload": _menu_overlay_payload(project),
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as fh:
        json.dump(spec, fh)
        spec_path = fh.name
    try:
        proc = subprocess.run(["/usr/bin/python3", "-c", OVERLAY_RENDERER, spec_path], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise FlyerRenderError(f"critical text overlay failed: {proc.stderr.strip() or proc.stdout.strip()}")
    finally:
        Path(spec_path).unlink(missing_ok=True)


def _decode_data_url(data_url: str) -> bytes:
    if "," not in data_url:
        raise FlyerRenderError("image response missing data URL comma")
    _prefix, encoded = data_url.split(",", 1)
    try:
        return base64.b64decode(encoded)
    except Exception as e:
        raise FlyerRenderError(f"image response base64 decode failed: {e}") from e


def _openrouter_image_bytes(project: FlyerProject, *, concept_id: str, output_format: str, size: tuple[int, int] | None, model: str, quality: str, repair_instruction: str = "", scene_direction=None, force_background_only: bool = False) -> bytes:
    api_key = _read_env_value("OPENROUTER_API_KEY")
    if not api_key or "PLACEHOLDER" in api_key.upper():
        raise FlyerRenderError("OPENROUTER_API_KEY is missing or placeholder")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _image_message_content(project, concept_id=concept_id, output_format=output_format, size=size, repair_instruction=repair_instruction, scene_direction=scene_direction, force_background_only=force_background_only)}],
        "modalities": ["image", "text"],
        "max_tokens": OPENROUTER_IMAGE_MAX_TOKENS,
        "stream": False,
        "image_config": {
            "aspect_ratio": _aspect_ratio(size),
            "image_size": "2K" if quality == "high" else "1K",
        },
    }
    req = urllib.request.Request(
        OPENROUTER_IMAGE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Trivenidigital/SME-Agents",
            "X-Title": "Hermes Flyer Studio",
        },
        method="POST",
    )
    body = ""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=OPENROUTER_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:1000]
            raise FlyerRenderError(f"OpenRouter image HTTP {e.code}: {err}") from e
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError) as e:
            last_error = e
            if attempt == 2:
                if isinstance(e, urllib.error.URLError):
                    raise FlyerRenderError(f"OpenRouter image connection failed: {e.reason}") from e
                raise FlyerRenderError(f"OpenRouter image response failed: {type(e).__name__}: {e}") from e
            time.sleep(2 * (attempt + 1))
    if not body and last_error is not None:
        raise FlyerRenderError(f"OpenRouter image response failed: {type(last_error).__name__}: {last_error}") from last_error
    doc = json.loads(body)
    choices = doc.get("choices") or []
    if not choices:
        raise FlyerRenderError(f"OpenRouter image response had no choices: {body[:500]}")
    images = choices[0].get("message", {}).get("images") or []
    if not images:
        raise FlyerRenderError(f"OpenRouter image response had no images: {body[:500]}")
    url = images[0].get("image_url", {}).get("url") or ""
    if not url.startswith("data:image/"):
        raise FlyerRenderError("OpenRouter image response did not include base64 image data")
    return _decode_data_url(url)


def _openrouter_image_edit_bytes(
    *,
    base_image_path: Path | str,
    mime: str,
    prompt: str,
    size: tuple[int, int] | None,
    model: str,
    quality: str,
) -> bytes:
    """Generic OpenRouter/gemini image-to-image edit: base64 the supplied base
    image as the image_url part + ``prompt`` as the text part → edited image
    bytes. The 3-retry urlopen + full response parsing + error handling extracted
    from the original source-edit caller so the repair-edit path can reuse the
    SAME proven gateway call. The caller owns *which* image + *what* prompt; this
    helper owns the HTTP transaction only (no behavior change for source edit)."""
    api_key = _read_env_value("OPENROUTER_API_KEY")
    if not api_key or "PLACEHOLDER" in api_key.upper():
        raise FlyerRenderError("OPENROUTER_API_KEY is missing or placeholder")
    base_path = Path(base_image_path)
    data_url = (
        f"data:{mime};base64,"
        + base64.b64encode(base_path.read_bytes()).decode("ascii")
    )
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "modalities": ["image", "text"],
        "max_tokens": OPENROUTER_IMAGE_MAX_TOKENS,
        "stream": False,
        "image_config": {
            "aspect_ratio": _aspect_ratio(size),
            "image_size": "2K" if quality == "high" else "1K",
        },
    }
    req = urllib.request.Request(
        OPENROUTER_IMAGE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Trivenidigital/SME-Agents",
            "X-Title": "Hermes Flyer Studio",
        },
        method="POST",
    )
    body = ""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=OPENROUTER_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:1000]
            raise FlyerRenderError(f"OpenRouter source edit HTTP {e.code}: {err}") from e
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError) as e:
            last_error = e
            if attempt == 2:
                if isinstance(e, urllib.error.URLError):
                    raise FlyerRenderError(f"OpenRouter source edit connection failed: {e.reason}") from e
                raise FlyerRenderError(f"OpenRouter source edit response failed: {type(e).__name__}: {e}") from e
            time.sleep(2 * (attempt + 1))
    if not body and last_error is not None:
        raise FlyerRenderError(f"OpenRouter source edit response failed: {type(last_error).__name__}: {last_error}") from last_error
    try:
        doc = json.loads(body)
    except json.JSONDecodeError as e:
        raise FlyerRenderError(f"OpenRouter source edit invalid JSON response: {body[:500]}") from e
    if not isinstance(doc, dict):
        raise FlyerRenderError(f"OpenRouter source edit invalid response shape: {body[:500]}")
    choices = doc.get("choices") or []
    if not isinstance(choices, list):
        raise FlyerRenderError(f"OpenRouter source edit invalid choices shape: {body[:500]}")
    if not choices:
        raise FlyerRenderError(f"OpenRouter source edit response had no choices: {body[:500]}")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise FlyerRenderError(f"OpenRouter source edit invalid choice shape: {body[:500]}")
    message = first_choice.get("message") or {}
    if not isinstance(message, dict):
        raise FlyerRenderError(f"OpenRouter source edit invalid message shape: {body[:500]}")
    images = message.get("images") or []
    if not isinstance(images, list):
        raise FlyerRenderError(f"OpenRouter source edit invalid images shape: {body[:500]}")
    if not images:
        raise FlyerRenderError(f"OpenRouter source edit response had no images: {body[:500]}")
    first_image = images[0]
    if not isinstance(first_image, dict):
        raise FlyerRenderError(f"OpenRouter source edit invalid image shape: {body[:500]}")
    image_url = first_image.get("image_url") or {}
    if not isinstance(image_url, dict):
        raise FlyerRenderError(f"OpenRouter source edit invalid image_url shape: {body[:500]}")
    url = image_url.get("url") or ""
    if not url.startswith("data:image/"):
        raise FlyerRenderError("OpenRouter source edit response did not include base64 image data")
    return _decode_data_url(url)


def _openrouter_source_edit_bytes(
    project: FlyerProject,
    *,
    size: tuple[int, int] | None,
    model: str,
    quality: str,
) -> bytes:
    reference = _source_edit_reference_asset(project)
    reference_path = Path(reference.path)
    mime = reference.mime_type or mimetypes.guess_type(str(reference_path))[0] or "image/png"
    return _openrouter_image_edit_bytes(
        base_image_path=reference_path,
        mime=mime,
        prompt=_source_edit_prompt(project),
        size=size,
        model=model,
        quality=quality,
    )


def _source_edit_reference_asset(project: FlyerProject) -> FlyerAsset:
    for asset in reversed(project.assets):
        if asset.kind == "reference_image" and Path(asset.path).exists():
            mime = asset.mime_type or mimetypes.guess_type(asset.path)[0] or ""
            if not mime.startswith("image/"):
                raise FlyerRenderError(f"source edit reference must be an image, got {mime or 'unknown'}")
            return asset
    raise FlyerRenderError("source edit requires an uploaded reference image")


# --- Slice 2: premium image-to-image repair-edit gate ------------------------
# The gate env vars (read at call time, NOT import time, so tests + an operator
# `export` take effect without a reimport). The flag MUST be exactly "1" for the
# repair path to arm; when an allowlist env is ALSO set, the project's resolved
# customer phone must be in it — otherwise (flag "1", no allowlist) the path is
# global. Anything else is byte-identical legacy behavior (the existing recovery
# ladder + deterministic-overlay floor). Mirrors bare_render's CD gate pattern.
PREMIUM_REPAIR_ENABLED_ENV = "FLYER_PREMIUM_REPAIR"
PREMIUM_REPAIR_ALLOWLIST_ENV = "FLYER_PREMIUM_REPAIR_ALLOWLIST"
PREMIUM_OVERLAY_ENABLED_ENV = "FLYER_PREMIUM_OVERLAY"
PREMIUM_OVERLAY_ALLOWLIST_ENV = "FLYER_PREMIUM_OVERLAY_ALLOWLIST"


def _normalize_sender(value: str) -> str:
    """Canonical comparison form for a phone/LID so the allowlist and the
    project's customer phone match across format variants. Strips a chat-JID
    suffix, a leading ``+``, internal phone punctuation/whitespace, and
    case-folds. (Mirrors bare_render._normalize_sender.)"""
    s = (value or "").strip()
    if "@" in s:
        s = s.split("@", 1)[0]
    s = s.lstrip("+")
    s = re.sub(r"[\s\-().]", "", s)
    return s.casefold()


def _premium_repair_allowlist() -> set[str]:
    """Parse FLYER_PREMIUM_REPAIR_ALLOWLIST (comma-separated phones/LIDs) into a
    normalized set. Empty/unset ⇒ empty set ⇒ no allowlist scoping (global)."""
    raw = os.environ.get(PREMIUM_REPAIR_ALLOWLIST_ENV, "") or ""
    return {n for n in (_normalize_sender(p) for p in raw.split(",")) if n}


def _premium_repair_enabled(project: FlyerProject) -> bool:
    """Slice 2 gate: flag == "1" AND, when the allowlist env is set, the
    project's customer phone is in it. Flag "1" + no allowlist ⇒ global ON. OFF
    for anything else (the entire repair rung is skipped → byte-identical)."""
    if os.environ.get(PREMIUM_REPAIR_ENABLED_ENV) != "1":
        return False
    allow = _premium_repair_allowlist()
    if not allow:
        return True
    return _normalize_sender(getattr(project, "customer_phone", "") or "") in allow


def _premium_overlay_allowlist() -> set[str]:
    """Parse FLYER_PREMIUM_OVERLAY_ALLOWLIST (comma-separated phones/LIDs) into a
    normalized set. Empty/unset ⇒ empty set ⇒ no allowlist scoping (global)."""
    raw = os.environ.get(PREMIUM_OVERLAY_ALLOWLIST_ENV, "") or ""
    return {n for n in (_normalize_sender(p) for p in raw.split(",")) if n}


def _premium_overlay_enabled(project: FlyerProject) -> bool:
    """Fix C gate: flag == "1" AND, when the allowlist env is set, the
    project's customer phone is in it. Flag "1" + no allowlist ⇒ global ON. OFF
    for anything else (the branch is skipped → byte-identical legacy behavior).
    Mirrors _premium_repair_enabled exactly."""
    if os.environ.get(PREMIUM_OVERLAY_ENABLED_ENV) != "1":
        return False
    allow = _premium_overlay_allowlist()
    if not allow:
        return True
    return _normalize_sender(getattr(project, "customer_phone", "") or "") in allow


PREMIUM_DETERMINISTIC_RECOVERY_ENV = "FLYER_DETERMINISTIC_RECOVERY"


def _deterministic_recovery_enabled(project: FlyerProject) -> bool:
    """Routing gate for integrated-fail -> deterministic recovery. Flag
    FLYER_DETERMINISTIC_RECOVERY == "1" AND (the shared FLYER_PREMIUM_OVERLAY_ALLOWLIST
    is empty => global, else project.customer_phone is in it). Independent of
    FLYER_PREMIUM_OVERLAY (which separately controls premium-vs-flat overlay)."""
    if os.environ.get(PREMIUM_DETERMINISTIC_RECOVERY_ENV) != "1":
        return False
    allow = _premium_overlay_allowlist()
    if not allow:
        return True
    return _normalize_sender(getattr(project, "customer_phone", "") or "") in allow


DETERMINISTIC_FIRST_ENV = "FLYER_DETERMINISTIC_FIRST"


def _deterministic_first_enabled(project: FlyerProject) -> bool:
    """Routing gate for deterministic-first: fact-dense flyers skip integrated
    model text and render via the deterministic overlay. Flag
    FLYER_DETERMINISTIC_FIRST == "1" AND (the shared FLYER_PREMIUM_OVERLAY_ALLOWLIST
    is empty => global, else project.customer_phone is in it). Independent of
    FLYER_PREMIUM_OVERLAY / FLYER_DETERMINISTIC_RECOVERY. Mirrors
    _deterministic_recovery_enabled exactly."""
    if os.environ.get(DETERMINISTIC_FIRST_ENV) != "1":
        return False
    allow = _premium_overlay_allowlist()
    if not allow:
        return True
    return _normalize_sender(getattr(project, "customer_phone", "") or "") in allow


CREATIVE_DIRECTOR_V2_ENV = "FLYER_CREATIVE_DIRECTOR_V2"


def _creative_director_v2_enabled(project: FlyerProject) -> bool:
    """Routing gate for Creative Director v2: resolve a creative direction
    upstream and carry it into the render. Flag FLYER_CREATIVE_DIRECTOR_V2 == "1"
    AND the shared FLYER_PREMIUM_OVERLAY_ALLOWLIST is NON-EMPTY AND
    project.customer_phone is in it. Independent of FLYER_PREMIUM_OVERLAY /
    FLYER_DETERMINISTIC_RECOVERY / FLYER_DETERMINISTIC_FIRST. Flag-off =>
    byte-identical legacy.

    SCOPED-ROLLOUT GUARD (Codex FINAL review, FINDING 2 MAJOR): CD v2 is rolling
    out to +17329837841 ONLY. UNLIKE the sibling gates (_deterministic_first /
    _deterministic_recovery / _premium_overlay) which treat an empty allowlist as
    GLOBAL, CD v2 must NOT inherit that broadening footgun — an empty/unset
    allowlist must DISABLE CD v2 entirely (not enable it for every phone). CD v2
    therefore requires the flag "1" AND a NON-EMPTY allowlist AND membership."""
    if os.environ.get(CREATIVE_DIRECTOR_V2_ENV) != "1":
        return False
    allow = _premium_overlay_allowlist()
    if not allow:
        # Empty/unset allowlist => DISABLED (scoped-rollout guard), NOT global.
        return False
    return _normalize_sender(getattr(project, "customer_phone", "") or "") in allow


# ── Premium Poster v1 — flag + allowlist + N + eligibility gates ─────────────
PREMIUM_POSTER_V1_ENABLED_ENV = "FLYER_PREMIUM_POSTER_V1"
PREMIUM_POSTER_V1_ALLOWLIST_ENV = "FLYER_PREMIUM_POSTER_V1_ALLOWLIST"
PREMIUM_POSTER_V1_N_ENV = "FLYER_PREMIUM_POSTER_V1_N"
PREMIUM_POSTER_V1_TIMEOUT_ENV = "FLYER_PREMIUM_POSTER_V1_TIMEOUT_SEC"


def _premium_poster_v1_allowlist() -> set[str]:
    """Parse FLYER_PREMIUM_POSTER_V1_ALLOWLIST (comma-separated phones/LIDs) into a
    normalized set."""
    raw = os.environ.get(PREMIUM_POSTER_V1_ALLOWLIST_ENV, "") or ""
    return {n for n in (_normalize_sender(p) for p in raw.split(",")) if n}


def _premium_poster_v1_armed(project: FlyerProject) -> bool:
    """Scoped-rollout guard (mirrors CD v2, NOT the global-on overlay gates): flag
    FLYER_PREMIUM_POSTER_V1 == "1" AND a NON-EMPTY allowlist AND the project's
    customer phone is in it. Empty/unset allowlist => DISABLED (not global) —
    Premium Poster v1 rolls out to +17329837841 ONLY. Flag-off / not-allowlisted =>
    byte-identical legacy (the branch is never entered)."""
    if os.environ.get(PREMIUM_POSTER_V1_ENABLED_ENV) != "1":
        return False
    allow = _premium_poster_v1_allowlist()
    if not allow:
        return False  # scoped-rollout guard: empty allowlist disables, never global
    return _normalize_sender(getattr(project, "customer_phone", "") or "") in allow


def _premium_poster_v1_n() -> int:
    """Best-of-N candidate count: FLYER_PREMIUM_POSTER_V1_N (default 1, clamp 1..3).
    The first live test runs N=1 (lowest latency/cost); N=2 is opt-in insurance."""
    raw = (os.environ.get(PREMIUM_POSTER_V1_N_ENV, "") or "").strip()
    try:
        n = int(raw) if raw else 1
    except ValueError:
        n = 1
    return max(1, min(3, n))


def _premium_poster_v1_timeout_sec() -> float:
    """Total wall-clock budget for the premium path: FLYER_PREMIUM_POSTER_V1_TIMEOUT_SEC
    (default 120s, clamp 30..180). On exceed, the path falls through to the existing
    render."""
    raw = (os.environ.get(PREMIUM_POSTER_V1_TIMEOUT_ENV, "") or "").strip()
    try:
        v = float(raw) if raw else 120.0
    except ValueError:
        v = 120.0
    return max(30.0, min(180.0, v))


def _premium_poster_v1_required_facts_present(project: FlyerProject) -> bool:
    """Coarse pre-check mirroring compose_premium_poster_v1's eligibility (business
    name + an offer/price + >=3 menu items) so a generation is not spent on facts the
    deterministic composer would reject anyway. The composer stays authoritative
    (returns None -> the orchestrator yields no winner -> fall through)."""
    facts = getattr(project, "locked_facts", []) or []

    def _val(f) -> str:
        return (getattr(f, "value", "") or "").strip()

    has_business = any(getattr(f, "fact_id", "") == "business_name" and _val(f) for f in facts)
    has_offer = any(getattr(f, "fact_id", "") in ("pricing_structure", "offer", "offer:0") and _val(f) for f in facts)
    items = sum(1 for f in facts
                if (getattr(f, "fact_id", "") or "").startswith("item:")
                and (getattr(f, "fact_id", "") or "").endswith(":name") and _val(f))
    return has_business and has_offer and items >= 3


# Mirrors premium_poster_v1._PRICE_RE (the composer stays authoritative; this
# pre-check only avoids spending N generations on a brief the composer refuses).
_PPV1_PRICE_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")
# Beyond ~12 items the composer's readable item zone overflows at the floor and
# it refuses fail-closed (partial menus never ship) — don't burn generations.
_PPV1_MAX_ITEMS = 12
# >=2 consecutive Indic chars (mirrors visual_qa.REGIONAL_WORD_RE's rationale:
# a single stray glyph must NOT force regional routing / disable premium).
_PPV1_REGIONAL_RUN_RE = re.compile(r"[ऀ-ൿ]{2}")
# Only the fact ids the COMPOSER actually paints gate regional exclusion — a
# regional glyph in an unpainted fact (notes, customer_text spans) is irrelevant
# to the Latin-only poster fonts.
_PPV1_PAINTED_FACT_IDS = (
    "business_name", "campaign_title", "pricing_structure", "offer", "offer:0",
    "schedule", "location", "contact_phone",
)


def _premium_poster_v1_composer_unfit(project: FlyerProject) -> bool:
    """Briefs the deterministic composer will REFUSE fail-closed (so arming them
    only burns N generations + OCR + critique before falling through):
    multi-price offers (a single badge price would mutate the offer), menus past
    the readable-zone cap, and regional-script facts (the vendored poster fonts
    are Latin-only — tofu boxes would fail QA every time)."""
    facts = getattr(project, "locked_facts", []) or []

    def _val(f) -> str:
        return (getattr(f, "value", "") or "").strip()

    offer_text = ""
    for fid in ("pricing_structure", "offer:0", "offer"):
        offer_text = next((_val(f) for f in facts if getattr(f, "fact_id", "") == fid and _val(f)), "")
        if offer_text:
            break
    if len(_PPV1_PRICE_RE.findall(offer_text)) > 1:
        return True
    items = sum(1 for f in facts
                if (getattr(f, "fact_id", "") or "").startswith("item:")
                and (getattr(f, "fact_id", "") or "").endswith(":name") and _val(f))
    if items > _PPV1_MAX_ITEMS:
        return True
    for f in facts:
        fid = (getattr(f, "fact_id", "") or "")
        painted = fid in _PPV1_PAINTED_FACT_IDS or (fid.startswith("item:") and fid.endswith(":name"))
        if painted and _PPV1_REGIONAL_RUN_RE.search(_val(f)):
            return True
    return False


def _premium_poster_v1_eligible(project: FlyerProject) -> bool:
    """Eligible flyers: food/grocery AND required locked facts present AND NOT a
    reference-extraction project (whose items live in an attached image, not facts)
    AND not a brief the composer would refuse fail-closed (multi-price / dense menu
    / regional script — see _premium_poster_v1_composer_unfit).
    Deliberately NOT gated on _background_only_eligible — food menu flyers are
    _integrated_poster_eligible today, and the premium poster is their replacement."""
    return (_is_food_or_grocery_project(project)
            and _premium_poster_v1_required_facts_present(project)
            and not _needs_reference_extraction(project)
            and not _premium_poster_v1_composer_unfit(project))


def _openrouter_repair_edit_bytes(
    project: FlyerProject,
    *,
    base_image_path: Path | str,
    repair_instruction: str,
    size: tuple[int, int] | None,
    model: str,
    quality: str,
) -> bytes:
    """Image-to-image repair edit of the PRIOR PREMIUM RENDER. Unlike
    _openrouter_source_edit_bytes (which edits the customer's reference artwork),
    this edits the model's own prior premium render with a scoped minimal-edit
    repair instruction so only the defective text changes while the composition
    is preserved. The base image is always a managed PNG render → image/png."""
    base_path = Path(base_image_path)
    mime = mimetypes.guess_type(str(base_path))[0] or "image/png"
    return _openrouter_image_edit_bytes(
        base_image_path=base_path,
        mime=mime,
        prompt=repair_instruction,
        size=size,
        model=model,
        quality=quality,
    )


def _source_edit_prompt(project: FlyerProject) -> str:
    business_name = (
        _display_business_name(project)
        or "this business"
    )
    request = " ".join((project.raw_request or project.fields.notes or "").split())[:1200]
    return f"""Edit the attached flyer image. Preserve the existing flyer design.

Business/brand to preserve: {business_name}
Requested edits: {request}

Rules:
- Make only the requested changes to the uploaded flyer.
- Preserve the original layout, colors, logo, food/product imagery, typography style, contact area, and overall composition.
- Do not redesign from scratch.
- Do not add a title such as "Uploaded Flyer Template".
- Remove stale text only when requested; keep all other readable text as close as possible to the source.
- Return one finished customer-ready flyer image with the edited text integrated into the source artwork.
"""


def build_source_edit_generation_prompt(project: FlyerProject) -> str:
    return _source_edit_prompt(project)


def _openai_edit_size(size: tuple[int, int] | None) -> str:
    if size is None:
        return "1024x1536"
    width, height = size
    if width == height:
        return "1024x1024"
    return "1536x1024" if width > height else "1024x1536"


def _multipart_form_data(
    fields: dict[str, str],
    files: list[tuple[str, str, str, bytes]],
) -> tuple[bytes, str]:
    boundary = f"----FlyerStudio{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, filename, content_type, data in files:
        safe_filename = Path(filename).name or "reference.png"
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{safe_filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def _openai_source_edit_bytes(
    project: FlyerProject,
    *,
    size: tuple[int, int] | None,
    model: str,
    quality: str,
) -> bytes:
    # P0-5 defense-in-depth: mirror workflow.py::source_edit_provider_ready —
    # treat PLACEHOLDER as missing so an operator CLI / retry path that
    # bypassed the cf-router preflight doesn't waste an OpenAI request and
    # surface a 401 mid-customer-flow.
    api_key = _read_env_value("OPENAI_API_KEY")
    if not api_key or "PLACEHOLDER" in api_key.upper():
        raise FlyerRenderError("OPENAI_API_KEY is missing or placeholder")
    reference = _source_edit_reference_asset(project)
    reference_path = Path(reference.path)
    mime = reference.mime_type or mimetypes.guess_type(str(reference_path))[0] or "image/png"
    body, boundary = _multipart_form_data(
        {
            "model": model,
            "prompt": _source_edit_prompt(project),
            "size": _openai_edit_size(size),
            "quality": quality,
            "input_fidelity": "high",
        },
        [
            (
                "image",
                reference_path.name,
                mime,
                reference_path.read_bytes(),
            )
        ],
    )
    req = urllib.request.Request(
        os.environ.get("OPENAI_IMAGE_EDIT_URL", OPENAI_IMAGE_EDIT_URL),
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OPENAI_IMAGE_EDIT_TIMEOUT_SEC) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:1000]
        raise FlyerRenderError(f"OpenAI image edit HTTP {e.code}: {err}") from e
    except urllib.error.URLError as e:
        raise FlyerRenderError(f"OpenAI image edit connection failed: {e.reason}") from e
    try:
        doc = json.loads(raw_body)
    except json.JSONDecodeError as e:
        raise FlyerRenderError(f"OpenAI image edit invalid JSON response: {raw_body[:500]}") from e
    if not isinstance(doc, dict):
        raise FlyerRenderError(f"OpenAI image edit invalid response shape: {raw_body[:500]}")
    data = doc.get("data") or []
    if not isinstance(data, list):
        raise FlyerRenderError(f"OpenAI image edit invalid data shape: {raw_body[:500]}")
    if not data:
        raise FlyerRenderError(f"OpenAI image edit response had no data: {raw_body[:500]}")
    first = data[0]
    if not isinstance(first, dict):
        raise FlyerRenderError(f"OpenAI image edit invalid item shape: {raw_body[:500]}")
    encoded = first.get("b64_json") or ""
    if encoded:
        try:
            return base64.b64decode(encoded)
        except Exception as e:
            raise FlyerRenderError(f"OpenAI image edit base64 decode failed: {e}") from e
    url = str(first.get("url") or "")
    if url.startswith("data:image/"):
        return _decode_data_url(url)
    raise FlyerRenderError("OpenAI image edit response did not include image data")


def _write_generated_image(raw: bytes, path: Path, *, size: tuple[int, int] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pil = _load_pillow()
    if size is None:
        if pil is None:
            raise FlyerRenderError("Pillow is required to convert generated image to PDF")
        Image, _ImageDraw, _ImageFont = pil
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(raw)
            tmp_path = Path(fh.name)
        try:
            with Image.open(tmp_path) as img:
                img.convert("RGB").save(path, "PDF", resolution=150.0)
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        if pil is None:
            path.write_bytes(raw)
            return
        Image, _ImageDraw, _ImageFont = pil
        width, height = size
        with Image.open(io.BytesIO(raw)) as img:
            img = img.convert("RGB")
            src_ratio = img.width / img.height
            dst_ratio = width / height
            if src_ratio > dst_ratio:
                new_w = int(img.height * dst_ratio)
                left = (img.width - new_w) // 2
                img = img.crop((left, 0, left + new_w, img.height))
            elif src_ratio < dst_ratio:
                new_h = int(img.width / dst_ratio)
                top = (img.height - new_h) // 2
                img = img.crop((0, top, img.width, top + new_h))
            img = img.resize((width, height), Image.Resampling.LANCZOS)
            img.save(path, format="PNG", optimize=True)


def _write_generated_image_contained(raw: bytes, path: Path, *, size: tuple[int, int] | None) -> None:
    if size is None:
        _write_generated_image(raw, path, size=None)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pil = _load_pillow()
    if pil is None:
        path.write_bytes(raw)
        return
    Image, _ImageDraw, _ImageFont = pil
    width, height = size
    with Image.open(io.BytesIO(raw)) as img:
        img = img.convert("RGB")
        scale = min(width / img.width, height / img.height)
        new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        canvas.paste(img, ((width - new_size[0]) // 2, (height - new_size[1]) // 2))
        canvas.save(path, format="PNG", optimize=True)


def _raw_background_path(path: Path) -> Path:
    if path.suffix.lower() == ".png":
        return path.with_name(f"{path.stem}.raw.png")
    return path.with_name(f"{path.stem}.raw-background.png")


EXACT_IDENTITY_OVERLAY_RENDERER = r'''
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
source = Path(payload["source"])
target = Path(payload["target"])
width = int(payload["width"])
height = int(payload["height"])
business = str(payload.get("business") or "").strip()
location = str(payload.get("location") or "").strip()
contact = str(payload.get("contact") or "").strip()
schedule = str(payload.get("schedule") or "").strip()

def _font(size):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=size)
    except Exception:
        return ImageFont.load_default()

def _wrap(draw, text, font, max_width):
    words = str(text or "").split()
    if not words:
        return []
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines

target.parent.mkdir(parents=True, exist_ok=True)
with Image.open(source) as img:
    img = img.convert("RGB")
    if img.size != (width, height):
        img = img.resize((width, height))
    draw = ImageDraw.Draw(img, "RGBA")
    top_h = max(96, int(height * 0.105))
    bottom_h = max(100, int(height * 0.105))
    burgundy = (102, 18, 28, 248)
    green = (18, 54, 34, 248)
    gold = (242, 198, 84, 255)
    white = (255, 255, 245, 255)
    draw.rectangle((0, 0, width, top_h), fill=burgundy)
    draw.rectangle((0, top_h - 4, width, top_h), fill=gold)
    draw.rectangle((0, height - bottom_h, width, height), fill=green)
    draw.rectangle((0, height - bottom_h, width, height - bottom_h + 4), fill=gold)
    if business:
        font = _font(max(34, int(width * 0.048)))
        lines = _wrap(draw, business, font, int(width * 0.88))[:2]
        total_h = len(lines) * int(getattr(font, "size", 34) * 1.05)
        y = max(10, (top_h - total_h) // 2)
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            draw.text(((width - (bbox[2] - bbox[0])) // 2, y), line, font=font, fill=white)
            y += int(getattr(font, "size", 34) * 1.05)
    if any((schedule, location, contact)):
        font = _font(max(22, int(width * 0.03)))
        lines = []
        if schedule:
            lines.extend(_wrap(draw, schedule.upper(), font, int(width * 0.9))[:1])
        if location:
            lines.extend(_wrap(draw, location.upper(), font, int(width * 0.9))[:1])
        if contact:
            lines.extend(_wrap(draw, f"CONTACT: {contact}".upper(), font, int(width * 0.9))[:1])
        total_h = len(lines) * int(getattr(font, "size", 22) * 1.06)
        y = height - bottom_h + max(10, (bottom_h - total_h) // 2)
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            draw.text(((width - (bbox[2] - bbox[0])) // 2, y), line, font=font, fill=white)
            y += int(getattr(font, "size", 22) * 1.06)
    img.save(target, format="PNG", optimize=True)
'''


def _exact_identity_overlay_payload(project: FlyerProject, source: Path, target: Path, *, size: tuple[int, int]) -> dict:
    return {
        "source": str(source),
        "target": str(target),
        "width": int(size[0]),
        "height": int(size[1]),
        "business": _display_business_name(project).strip(),
        "location": fact_value(project, "location", fallback=project.fields.venue_or_location).strip(),
        "contact": fact_value(project, "contact_phone", fallback=project.fields.contact_info).strip(),
        "schedule": _display_schedule(project),
    }


def _apply_exact_identity_overlay_with_system_pillow(payload: dict) -> None:
    if not Path("/usr/bin/python3").exists():
        raise FlyerRenderError("Pillow is unavailable for exact identity overlay: /usr/bin/python3 missing")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        payload_path = fh.name
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", "-c", EXACT_IDENTITY_OVERLAY_RENDERER, payload_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise FlyerRenderError(
                "Pillow is unavailable for exact identity overlay: "
                f"system Pillow renderer failed: {proc.stderr.strip()[:300]}"
            )
    finally:
        Path(payload_path).unlink(missing_ok=True)


def apply_exact_identity_overlay(project: FlyerProject, source: Path | str, target: Path | str, *, size: tuple[int, int]) -> None:
    pil = _load_pillow()
    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _exact_identity_overlay_payload(project, source, target, size=size)
    business = str(payload["business"])
    location = str(payload["location"])
    contact = str(payload["contact"])
    schedule = str(payload["schedule"])
    if not any((business, location, contact, schedule)):
        _export_from_source_image(source, target, size=size)
        return
    if pil is None:
        _apply_exact_identity_overlay_with_system_pillow(payload)
        return
    Image, ImageDraw, ImageFont = pil
    with Image.open(source) as img:
        img = img.convert("RGB")
        if img.size != size:
            img = img.resize(size)
        draw = ImageDraw.Draw(img, "RGBA")
        width, height = size
        top_h = max(96, int(height * 0.105))
        bottom_h = max(100, int(height * 0.105))
        burgundy = (102, 18, 28, 248)
        green = (18, 54, 34, 248)
        gold = (242, 198, 84, 255)
        white = (255, 255, 245, 255)
        draw.rectangle((0, 0, width, top_h), fill=burgundy)
        draw.rectangle((0, top_h - 4, width, top_h), fill=gold)
        draw.rectangle((0, height - bottom_h, width, height), fill=green)
        draw.rectangle((0, height - bottom_h, width, height - bottom_h + 4), fill=gold)
        if business:
            font = _font(ImageFont, max(34, int(width * 0.048)), bold=True, text=business)
            lines = _wrap(draw, business, font, int(width * 0.88))[:2]
            total_h = len(lines) * int(font.size * 1.05)
            y = max(10, (top_h - total_h) // 2)
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                draw.text(((width - (bbox[2] - bbox[0])) // 2, y), line, font=font, fill=white)
                y += int(font.size * 1.05)
        if any((schedule, location, contact)):
            font = _font(ImageFont, max(22, int(width * 0.03)), bold=True, text=" ".join([schedule, location, contact]))
            lines: list[str] = []
            if schedule:
                lines.extend(_wrap(draw, schedule.upper(), font, int(width * 0.9))[:1])
            if location:
                lines.extend(_wrap(draw, location.upper(), font, int(width * 0.9))[:1])
            if contact:
                lines.extend(_wrap(draw, f"CONTACT: {contact}".upper(), font, int(width * 0.9))[:1])
            total_h = len(lines) * int(font.size * 1.06)
            y = height - bottom_h + max(10, (bottom_h - total_h) // 2)
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                draw.text(((width - (bbox[2] - bbox[0])) // 2, y), line, font=font, fill=white)
                y += int(font.size * 1.06)
        img.save(target, format="PNG", optimize=True)


EXPORT_FROM_SOURCE_RENDERER = r'''
import sys
from pathlib import Path
from PIL import Image
src=Path(sys.argv[1]); out=Path(sys.argv[2]); width=int(sys.argv[3]); height=int(sys.argv[4]); is_pdf=sys.argv[5]=="1"
out.parent.mkdir(parents=True, exist_ok=True)
with Image.open(src) as img:
    img=img.convert("RGB")
    if is_pdf:
        img.save(out, "PDF", resolution=150.0)
    else:
        src_ratio=img.width/img.height
        dst_ratio=width/height
        if src_ratio > dst_ratio:
            new_w=int(img.height*dst_ratio)
            left=(img.width-new_w)//2
            img=img.crop((left,0,left+new_w,img.height))
        elif src_ratio < dst_ratio:
            new_h=int(img.width/dst_ratio)
            top=(img.height-new_h)//2
            img=img.crop((0,top,img.width,top+new_h))
        img=img.resize((width,height), Image.Resampling.LANCZOS)
        img.save(out, format="PNG", optimize=True)
'''


def _export_from_source_image(source: Path, path: Path, *, size: tuple[int, int] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pil = _load_pillow()
    if pil is not None:
        Image, _ImageDraw, _ImageFont = pil
        with Image.open(source) as img:
            img = img.convert("RGB")
            if size is None:
                img.save(path, "PDF", resolution=150.0)
                return
            width, height = size
            src_ratio = img.width / img.height
            dst_ratio = width / height
            if src_ratio > dst_ratio:
                new_w = int(img.height * dst_ratio)
                left = (img.width - new_w) // 2
                img = img.crop((left, 0, left + new_w, img.height))
            elif src_ratio < dst_ratio:
                new_h = int(img.width / dst_ratio)
                top = (img.height - new_h) // 2
                img = img.crop((0, top, img.width, top + new_h))
            img = img.resize((width, height), Image.Resampling.LANCZOS)
            img.save(path, format="PNG", optimize=True)
            return
    if not Path("/usr/bin/python3").exists():
        raise FlyerRenderError("Pillow is unavailable and /usr/bin/python3 fallback is missing")
    width, height = size or (1275, 1650)
    proc = subprocess.run(
        ["/usr/bin/python3", "-c", EXPORT_FROM_SOURCE_RENDERER, str(source), str(path), str(width), str(height), "1" if size is None else "0"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise FlyerRenderError(f"source image export failed: {proc.stderr.strip()}")


def _export_from_source_image_contained(source: Path, path: Path, *, size: tuple[int, int] | None) -> None:
    if size is None:
        _export_from_source_image(source, path, size=None)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pil = _load_pillow()
    if pil is None:
        _export_from_source_image(source, path, size=size)
        return
    Image, _ImageDraw, _ImageFont = pil
    width, height = size
    with Image.open(source) as img:
        img = img.convert("RGB")
        scale = min(width / img.width, height / img.height)
        new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        canvas.paste(img, ((width - new_size[0]) // 2, (height - new_size[1]) // 2))
        canvas.save(path, format="PNG", optimize=True)


def _is_source_edit_project(project: FlyerProject) -> bool:
    # P0-5: a project is source-edit only when there's positive evidence —
    # either an explicit raw_request/notes marker, or it's queued for manual
    # review WITH an uploaded reference image to edit. The bare
    # `status == "manual_edit_required"` disjunct previously misclassified
    # missing_required_facts / visual_qa_failed projects as source-edit,
    # which sent them down the source-preservation rendering path and lost
    # their original manual_review context.
    text = f"{project.raw_request} {project.fields.notes}".lower()
    has_marker = (
        "edit uploaded flyer/source artwork" in text
        or "authorized flyer/source artwork update" in text
    )
    if has_marker:
        return True
    if project.status != "manual_edit_required":
        return False
    return any(asset.kind == "reference_image" for asset in (project.assets or []))


def _is_manual_completed_operator_preview(project: FlyerProject, asset: FlyerAsset | None) -> bool:
    if asset is None:
        return False
    if project.manual_review.status != "completed":
        return False
    if asset.kind != "concept_preview" or asset.source != "uploaded":
        return False
    operator_ids = set(project.manual_review.operator_asset_ids or [])
    return asset.asset_id in operator_ids


def _draw_flyer_pil(project: FlyerProject, *, concept_id: str, size: tuple[int, int], pil_modules):
    Image, ImageDraw, ImageFont = pil_modules
    _require_ready(project)
    width, height = size
    palette = PALETTES.get(concept_id, PALETTES["C1"])
    img = Image.new("RGB", size, tuple(palette["bg"]))
    draw = ImageDraw.Draw(img)
    margin = int(width * 0.07)
    title_font = _font(ImageFont, max(38, int(width * 0.060)), bold=True)
    subtitle_font = _font(ImageFont, max(14, int(width * 0.017)), bold=True)
    small_font = _font(ImageFont, max(11, int(width * 0.012)))

    draw.rectangle((0, 0, width, int(height * 0.19)), fill=tuple(palette["primary"]))
    draw.rectangle((0, int(height * 0.19), width, int(height * 0.205)), fill=tuple(palette["accent"]))
    for i in range(9):
        cx = int(width * (0.08 + i * 0.105))
        cy = int(height * 0.16)
        r = int(width * 0.025)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tuple(palette["accent"]))

    language_label = {
        "te": "Telugu", "hi": "Hindi", "ml": "Malayalam", "ta": "Tamil", "kn": "Kannada",
        "gu": "Gujarati", "mr": "Marathi", "pa": "Punjabi", "es": "Spanish",
        "mixed": "Multilingual", "other": "Local language",
    }.get(project.fields.preferred_language, "English")
    draw.text((margin, int(height * 0.045)), language_label.upper(), font=small_font, fill=tuple(palette["soft"]))

    y = int(height * 0.245)
    title_text = _display_title(project)
    for line in _wrap(draw, title_text, title_font, width - margin * 2):
        if y + title_font.size > int(height * 0.45):
            raise FlyerRenderError("critical text facts do not fit")
        draw.text((margin, y), line, font=title_font, fill=tuple(palette["primary"]))
        y += int(title_font.size * 1.08)
    if project.fields.style_preference:
        for line in _wrap(draw, project.fields.style_preference, small_font, width - margin * 2)[:2]:
            draw.text((margin, y + 8), line, font=small_font, fill=tuple(palette["ink"]))
            y += int(small_font.size * 1.2)

    card_top = max(y + 22, int(height * 0.40))
    card_bottom = height - int(margin * 1.45)
    draw.rounded_rectangle((margin, card_top, width - margin, card_bottom), radius=18, fill=tuple(palette["soft"]), outline=tuple(palette["accent"]), width=4)
    facts = [
        (fact.label.upper(), fact.text)
        for fact in collect_text_facts(project)
        if fact.fact_id != "title"
    ]
    fy = card_top + 36
    for label, value in facts:
        if fy + small_font.size + subtitle_font.size > card_bottom - 14:
            raise FlyerRenderError("critical text facts do not fit")
        draw.text((margin + 34, fy), label, font=small_font, fill=tuple(palette["accent"]))
        fy += int(small_font.size * 0.95)
        for line in _wrap(draw, value, subtitle_font, width - margin * 2 - 68):
            if fy + subtitle_font.size > card_bottom - 14:
                raise FlyerRenderError("critical text facts do not fit")
            draw.text((margin + 34, fy), line, font=subtitle_font, fill=tuple(palette["ink"]))
            fy += int(subtitle_font.size * 1.05)
        fy += 4

    footer = "Send APPROVE to finalize - Flyer Studio"
    bbox = draw.textbbox((0, 0), footer, font=small_font)
    draw.text(((width - (bbox[2] - bbox[0])) // 2, height - margin), footer, font=small_font, fill=tuple(palette["ink"]))
    return img


def _render_with_local_pillow(project: FlyerProject, path: Path, *, concept_id: str, size: tuple[int, int] | None) -> bool:
    pil = _load_pillow()
    if pil is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if size is None:
        img = _draw_flyer_pil(project, concept_id=concept_id, size=(1275, 1650), pil_modules=pil)
        img.save(path, "PDF", resolution=150.0)
    else:
        img = _draw_flyer_pil(project, concept_id=concept_id, size=size, pil_modules=pil)
        img.save(path, format="PNG", optimize=True)
    return True


SUBPROCESS_RENDERER = r'''
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
spec=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out=Path(spec["path"]); out.parent.mkdir(parents=True, exist_ok=True)
palette=spec["palette"]; size=tuple(spec["size"])
img=Image.new("RGB", size, tuple(palette["bg"])); draw=ImageDraw.Draw(img)
def font(sz,bold=False):
    c=["/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf","/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    if bold: c.insert(0,"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    for p in c:
        try:
            if Path(p).exists(): return ImageFont.truetype(p, sz)
        except OSError: pass
    return ImageFont.load_default()
def wrap(text, f, maxw):
    words=(text or "").split(); lines=[]; cur=""
    for w in words:
        cand=(cur+" "+w).strip(); box=draw.textbbox((0,0),cand,font=f)
        if box[2]-box[0] <= maxw or not cur: cur=cand
        else: lines.append(cur); cur=w
    if cur: lines.append(cur)
    return lines
w,h=size; m=int(w*.07); tf=font(max(38,int(w*.060)),True); sf=font(max(14,int(w*.017)),True); sm=font(max(11,int(w*.012)))
draw.rectangle((0,0,w,int(h*.19)), fill=tuple(palette["primary"])); draw.rectangle((0,int(h*.19),w,int(h*.205)), fill=tuple(palette["accent"]))
for i in range(9):
    cx=int(w*(.08+i*.105)); cy=int(h*.16); r=int(w*.025); draw.ellipse((cx-r,cy-r,cx+r,cy+r), fill=tuple(palette["accent"]))
draw.text((m,int(h*.045)), spec["language"].upper(), font=sm, fill=tuple(palette["soft"]))
y=int(h*.245)
for line in wrap(spec["title"], tf, w-m*2):
    if y+tf.size > int(h*.45):
        raise SystemExit("critical text facts do not fit")
    draw.text((m,y), line, font=tf, fill=tuple(palette["primary"])); y += int(tf.size*1.08)
for line in wrap(spec.get("style",""), sm, w-m*2)[:2]:
    draw.text((m,y+8), line, font=sm, fill=tuple(palette["ink"])); y += int(sm.size*1.2)
top=max(y+22,int(h*.40)); bottom=h-int(m*1.45)
draw.rounded_rectangle((m,top,w-m,bottom), radius=18, fill=tuple(palette["soft"]), outline=tuple(palette["accent"]), width=4)
fy=top+36
for label,value in spec["facts"]:
    if fy+sm.size+sf.size > bottom-14:
        raise SystemExit("critical text facts do not fit")
    draw.text((m+34,fy), label, font=sm, fill=tuple(palette["accent"])); fy += int(sm.size*.95)
    for line in wrap(value, sf, w-m*2-68):
        if fy+sf.size > bottom-14:
            raise SystemExit("critical text facts do not fit")
        draw.text((m+34,fy), line, font=sf, fill=tuple(palette["ink"])); fy += int(sf.size*1.05)
    fy += 4
footer="Send APPROVE to finalize - Flyer Studio"; box=draw.textbbox((0,0),footer,font=sm)
draw.text(((w-(box[2]-box[0]))//2,h-m), footer, font=sm, fill=tuple(palette["ink"]))
if spec["format"]=="PDF": img.save(out,"PDF",resolution=150.0)
else: img.save(out,format="PNG",optimize=True)
'''


def _render_with_system_pillow(project: FlyerProject, path: Path, *, concept_id: str, size: tuple[int, int] | None) -> None:
    if not Path("/usr/bin/python3").exists():
        raise FlyerRenderError("Pillow is unavailable and /usr/bin/python3 fallback is missing")
    _require_ready(project)
    language = {
        "te": "Telugu", "hi": "Hindi", "ml": "Malayalam", "ta": "Tamil", "kn": "Kannada",
        "gu": "Gujarati", "mr": "Marathi", "pa": "Punjabi", "es": "Spanish",
        "mixed": "Multilingual", "other": "Local language",
    }.get(project.fields.preferred_language, "English")
    spec = {
        "path": str(path),
        "size": list(size or (1275, 1650)),
        "format": "PDF" if size is None else "PNG",
        "palette": PALETTES.get(concept_id, PALETTES["C1"]),
        "language": language,
        "title": _display_title(project),
        "style": project.fields.style_preference,
        "facts": [
            [fact.label.upper(), fact.text]
            for fact in collect_text_facts(project)
            if fact.fact_id != "title"
        ],
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as fh:
        json.dump(spec, fh)
        spec_path = fh.name
    try:
        proc = subprocess.run(["/usr/bin/python3", "-c", SUBPROCESS_RENDERER, spec_path], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise FlyerRenderError(f"system Pillow renderer failed: {proc.stderr.strip()}")
    finally:
        Path(spec_path).unlink(missing_ok=True)


def _render(project: FlyerProject, path: Path, *, concept_id: str, size: tuple[int, int] | None) -> None:
    if not _render_with_local_pillow(project, path, concept_id=concept_id, size=size):
        _render_with_system_pillow(project, path, concept_id=concept_id, size=size)


def _populate_creative_direction_v2(project: FlyerProject) -> None:
    """CD v2 (Slice B, B2.3): when the V2 gate is ON for this project, PROPOSE a
    creative brief (Hermes proposes the creative fields), RESOLVE it over the
    project's EXISTING locked_facts, and store ``dataclasses.asdict(resolved)`` on
    ``project.creative_direction`` so it round-trips into the overlay subprocess.

    Strict boundaries (B2.3):
      - This is ONLY reached when ``_creative_director_v2_enabled(project)`` — flag-off
        the caller never calls it, so the carrier stays None, ``propose_creative_brief_v2``
        is NEVER invoked, and NO locked_facts mutation happens (byte-identical legacy).
      - The V2 propose path NEVER mutates ``project.locked_facts`` (it does not call
        ``materialize_spans``); the resolver is PURE.
      - Failure ANYWHERE ⇒ resolve from an EMPTY ``FlyerBrief`` (deterministic
        defaults) so the render is still enriched, NEVER blocked — and on a truly
        unexpected error leave ``creative_direction`` None. This populates the
        carrier only; B2.4/B2.5 consume it in the bg prompt / overlay.
    """
    try:
        raw_request = (project.raw_request or project.fields.notes or "").strip()
        brief = propose_creative_brief_v2(
            raw_request, project.locked_facts, None
        ) or _CDV2FlyerBrief(
            request_intent="new", visual_direction=_CDV2VisualDirection()
        )
        resolved = resolve_creative_direction(brief, project.locked_facts)
        project.creative_direction = dataclasses.asdict(resolved)
        # Composition Phase 1: route the poster archetype from the brief's
        # request_intent (offer_priority accepted but unused this phase). Guarded
        # so any failure simply omits poster_archetype and leaves the carrier as-is.
        try:
            archetype = select_poster_archetype(
                getattr(brief, "request_intent", "") or "", resolved.offer_priority
            )
            project.creative_direction["poster_archetype"] = archetype
        except Exception:  # noqa: BLE001 — never block; just omit poster_archetype
            pass
    except Exception:  # noqa: BLE001 — never block the render; carrier stays None
        project.creative_direction = None


def _openrouter_textless_image(prompt: str, *, model: str, quality: str, size: tuple[int, int] | None,
                               timeout: float | None = None, attempts: int = 3) -> bytes:
    """OpenRouter image generation for a RAW text prompt (Premium Poster v1's
    director prompt, ``build_textless_food_prompt``). Mirrors _openrouter_image_bytes's
    request shape + parse but takes the prompt directly instead of building it via
    _image_message_content. ``timeout`` (per-call socket timeout) + ``attempts`` are
    parameterized so the premium path can bound a single call within its wall-clock
    budget (the live path passes a budget-derived timeout + attempts=1). Returns PNG
    bytes; raises FlyerRenderError on failure. (Isolated copy — does NOT touch the
    existing _openrouter_image_bytes path.)"""
    eff_timeout = timeout if timeout is not None else OPENROUTER_TIMEOUT_SEC
    attempts = max(1, attempts)
    api_key = _read_env_value("OPENROUTER_API_KEY")
    if not api_key or "PLACEHOLDER" in api_key.upper():
        raise FlyerRenderError("OPENROUTER_API_KEY is missing or placeholder")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"],
        "max_tokens": OPENROUTER_IMAGE_MAX_TOKENS,
        "stream": False,
        "image_config": {"aspect_ratio": _aspect_ratio(size), "image_size": "2K" if quality == "high" else "1K"},
    }
    req = urllib.request.Request(
        OPENROUTER_IMAGE_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/Trivenidigital/SME-Agents", "X-Title": "Hermes Flyer Studio"},
        method="POST")
    body = ""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=eff_timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:1000]
            raise FlyerRenderError(f"OpenRouter image HTTP {e.code}: {err}") from e
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError) as e:
            last_error = e
            if attempt == attempts - 1:
                if isinstance(e, urllib.error.URLError):  # parity with _openrouter_image_bytes
                    raise FlyerRenderError(f"OpenRouter image connection failed: {e.reason}") from e
                raise FlyerRenderError(f"OpenRouter image response failed: {type(e).__name__}: {e}") from e
            time.sleep(2 * (attempt + 1))
    if not body and last_error is not None:
        raise FlyerRenderError(f"OpenRouter image response failed: {type(last_error).__name__}: {last_error}") from last_error
    doc = json.loads(body)
    choices = doc.get("choices") or []
    if not choices:
        raise FlyerRenderError(f"OpenRouter image response had no choices: {body[:500]}")
    images = choices[0].get("message", {}).get("images") or []
    if not images:
        raise FlyerRenderError(f"OpenRouter image response had no images: {body[:500]}")
    url = images[0].get("image_url", {}).get("url") or ""
    if not url.startswith("data:image/"):
        raise FlyerRenderError("OpenRouter image response did not include base64 image data")
    return _decode_data_url(url)


def openrouter_image_with_transport_retry(project: FlyerProject, *, transport_attempts: int = 2, **kw) -> bytes:
    """v2 spec amendment A3 (labeled failure class: Workstream #0 Leg 2 B02): an
    OpenRouter response with NO image ("had no images"/"had no choices" — the
    exact raise strings in _openrouter_image_bytes; keep in sync) is a
    TRANSPORT-class transient, not a content failure — retried here, separately
    from the regen counter (regens are for verification mismatches only). Real
    render errors re-raise immediately.

    INTENT (PR #535 review F4): deliberately wraps the INTEGRATED path
    (_openrouter_image_bytes) — v2 Layer 2. The premium textless path is slated
    for demotion and is NOT a client of this wrapper."""
    last: Exception | None = None
    for attempt in range(max(1, transport_attempts)):
        try:
            return _openrouter_image_bytes(project, **kw)
        except FlyerRenderError as exc:
            msg = str(exc)
            if "had no images" in msg or "had no choices" in msg:
                last = exc
                if attempt + 1 < transport_attempts:  # no dead sleep before final re-raise
                    time.sleep(1.5 * (attempt + 1))
                continue
            raise
    assert last is not None
    raise last


def _ppv1_default_generator(project: FlyerProject, *, model: str, quality: str,
                            size: tuple[int, int] | None, deadline: float,
                            created_paths: list | None = None):
    """Real best-of-N candidate generator: (director prompt) -> saved PNG path.
    RAISES on failure so the director's generation_failed branch records the REAL
    error class (a blanket swallow here previously collapsed auth failures, quota
    exhaustion, provider outages, and budget exhaustion into one indistinguishable
    "generator_returned_none" — 2026-07-02 review SF-2/FM-1). Budget exhaustion
    raises TimeoutError, keeping timeouts a distinct fallback reason. The director
    contains every raise per candidate (never-raises contract preserved).
    Temp files are appended to ``created_paths`` so the orchestrator can clean up
    after compose (SF-7: ppv1-bg-*.png previously leaked on every fire)."""
    def gen(prompt: str):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("premium poster v1 wall-clock budget exhausted")
        # Bound the single call by the remaining budget + no retries on the live
        # path: fail fast -> fall through to the existing render (which has its
        # own retry ladder). Keeps the gen inside _premium_poster_v1_timeout_sec.
        raw = _openrouter_textless_image(
            prompt, model=model, quality=quality, size=size,
            timeout=max(5.0, min(remaining, OPENROUTER_TIMEOUT_SEC)), attempts=1)
        fd, p = tempfile.mkstemp(suffix=".png", prefix="ppv1-bg-")
        os.close(fd)
        Path(p).write_bytes(raw)
        if created_paths is not None:
            created_paths.append(p)
        return p
    return gen


def _ppv1_default_textless_ocr(deadline: float):
    """Real textless gate over the deployed vision OCR (visual_qa._vision_text):
    empty extracted_text -> textless True; vision outage / deadline -> raise
    (-> check_error, the candidate is dropped, never trusted as textless)."""
    try:  # flat (VPS, deployed as flyer_visual_qa.py) then package (tests)
        from flyer_visual_qa import _vision_text  # type: ignore
    except ImportError:  # pragma: no cover - src layout fallback
        from agents.flyer.visual_qa import _vision_text

    def ocr(pil) -> bool:
        if time.monotonic() > deadline:
            raise TimeoutError("premium poster v1 textless OCR deadline exceeded")
        fd, p = tempfile.mkstemp(suffix=".png", prefix="ppv1-ocr-")
        os.close(fd)
        try:
            pil.save(p)
            text, source, _kind, _notes = _vision_text(Path(p))
            if source == "unavailable":
                raise RuntimeError("premium poster v1 textless OCR unavailable")
            return str(text or "").strip() == ""
        finally:
            Path(p).unlink(missing_ok=True)
    return ocr


def _ppv1_default_critique_scorer(deadline: float):
    """Real critique selector over the dev-only art-director oracle (vision). Returns
    the score dict, or None when unavailable / past the wall-clock budget (-> first-
    accepted selection; the critique is a selector, never a gate)."""
    try:
        from flyer_art_director_oracle import score_art_direction, score_to_dict  # type: ignore
    except ImportError:  # pragma: no cover - src layout fallback
        from agents.flyer.flyer_art_director_oracle import score_art_direction, score_to_dict

    def scorer(image_path: str, brief: str = ""):
        if time.monotonic() > deadline:
            return None  # budget exhausted -> skip the critique call, first-accepted wins
        score = score_art_direction(image_path, brief_summary=brief)
        # Prefix-anchored (mirrors the director's _oracle_scorer, which documents
        # why a bare substring is wrong): only the genuinely-unavailable case maps
        # to None; an oracle ERROR keeps its empty-axis dict and is recorded as
        # critique_error — a distinct signal (2026-07-02 review CQ-5).
        if not score.axes and (score.overall_critique or "").lower().startswith(
            "art-director oracle unavailable"
        ):
            return None
        return score_to_dict(score)
    return scorer


def render_premium_poster_v1(project: FlyerProject, target: Path, *, concept_id: str,
                             output_format: str, size: tuple[int, int] | None, model: str, quality: str,
                             generator=None, textless_ocr=None, critique_scorer=None,
                             compose=None, n: int | None = None, timeout_sec: float | None = None) -> PremiumPosterV1Outcome:
    """Premium Poster v1 render (both the bare and managed opt-in paths route
    here): best-of-N textless food-background gen ->
    textless gate -> deterministic compose_premium_poster_v1 -> critique selector ->
    write the winning poster to ``target``. NEVER raises; ANY failure returns
    ``delivered=False`` so the caller falls through to the existing render path. The
    adapters (generator / OCR / critique) are injectable for tests; the defaults wire
    the real image model / vision OCR / oracle. The existing visual_qa / visible-
    contract / send gates run on ``target`` downstream, unchanged + authoritative."""
    n = _premium_poster_v1_n() if n is None else n
    if size != (1080, 1350):  # only the concept-preview size in this slice; PDF/other -> existing path
        return PremiumPosterV1Outcome(False, "skipped", "unsupported_size", n, -1, None, output_format)
    deadline = time.monotonic() + (timeout_sec if timeout_sec is not None else _premium_poster_v1_timeout_sec())
    created_paths: list = []  # temp files the DEFAULT generator writes (cleaned in finally)
    try:
        if compose is None:
            try:  # flat (VPS, deployed as flyer_premium_poster_v1_director.py) then package
                from flyer_premium_poster_v1_director import compose_best_of_n  # type: ignore
            except ImportError:  # pragma: no cover - src layout fallback
                from agents.flyer.premium_poster_v1_director import compose_best_of_n
            compose = compose_best_of_n
        facts = list(getattr(project, "locked_facts", []) or [])
        gen = generator if generator is not None else _ppv1_default_generator(
            project, model=model, quality=quality, size=size, deadline=deadline,
            created_paths=created_paths)
        ocr = textless_ocr if textless_ocr is not None else _ppv1_default_textless_ocr(deadline=deadline)
        scorer = critique_scorer if critique_scorer is not None else _ppv1_default_critique_scorer(deadline=deadline)
        best_img, report, candidates = compose(facts, generator=gen, textless_ocr=ocr, critique_scorer=scorer, n=n)
        wi = report.get("winner_index", -1)
        winner = candidates[wi] if isinstance(wi, int) and 0 <= wi < len(candidates) else None
        # Deliver ONLY when a real FOOD candidate won (background_status == "ok"). If
        # every candidate was rejected, compose_best_of_n returns its DETERMINISTIC
        # gradient fallback — we do NOT ship that here; we fall through to the existing
        # render path (which owns the richer recovery ladder + its own gradient floor).
        won_food = best_img is not None and winner is not None and winner.get("background_status") == "ok"
        if won_food:
            best_img.save(target)
            target_p = Path(target)
            # Write-time invariants for the FINAL package (2026-07-02 review
            # ST-1/FM-3/FA-1/FA-2/CF-1):
            # 1. No stale raw sibling may survive a premium delivery — otherwise
            #    render_final_package's 30s mtime heuristic can rebuild finals
            #    from an UNRELATED earlier background (final != approved design).
            _raw_background_path(target_p).unlink(missing_ok=True)
            # 2. Persist premium provenance + the OCR-verified winner background
            #    so finals RECOMPOSE the same deterministic poster at each target
            #    aspect instead of center-cropping the brand band + footer off
            #    (which silently dropped the Instagram formats at QA). Best-effort:
            #    without provenance, finals letterbox/degrade — never crash.
            try:
                wb = winner.get("food_image_path")
                if wb:
                    shutil.copy2(wb, _ppv1_background_path(target_p))
                _ppv1_provenance_path(target_p).write_text(json.dumps({
                    "schema": 1,
                    "delivered_at": datetime.now(timezone.utc).isoformat(),
                    "n": n,
                    "winner_index": report.get("winner_index"),
                    "winner_composite": report.get("winner_composite"),
                }), encoding="utf-8")
            except OSError:
                pass
            return PremiumPosterV1Outcome(True, "delivered", "none", n, wi, report.get("winner_composite"), output_format)
        # Carry the per-candidate failure taxonomy into the audited reason so an
        # OCR outage / auth failure / timeout is distinguishable from the normal
        # "model painted text" fail-closed case (2026-07-02 review SF-2/PR-H1):
        # e.g. "no_food_winner:check_error=2" vs "no_food_winner:image_has_text=1".
        reason = "no_food_winner" if best_img is not None else "no_winner"
        reason = f"{reason}:{_ppv1_candidate_status_summary(report)}"[:80]
        return PremiumPosterV1Outcome(False, "fallback", reason, n, wi, report.get("winner_composite"), output_format)
    except Exception as exc:  # noqa: BLE001 — premium path must never raise into _render_model
        # Keep the message head, not just the type: "exception:OSError" cannot
        # distinguish disk-full from a missing font during a live incident (SF-3).
        detail = f"exception:{type(exc).__name__}:{str(exc)[:50]}"[:80]
        return PremiumPosterV1Outcome(False, "fallback", detail, n, -1, None, output_format)
    finally:
        for p in created_paths:  # default-generator temp backgrounds only (we own them)
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass


def _ppv1_provenance_path(preview: Path) -> Path:
    """Sidecar marking a preview as a Premium Poster v1 delivery (JSON: schema,
    delivered_at, n, winner_index, winner_composite). Its EXISTENCE is the
    provenance signal render_final_package uses — never a timing heuristic."""
    return Path(str(preview) + ".ppv1.json")


def _ppv1_background_path(preview: Path) -> Path:
    """The delivered winner's textless food background (already OCR-verified at
    delivery), persisted so finals can recompose the same deterministic poster at
    each target aspect. Deliberately NOT the legacy .raw.png name — the raw
    sidecar carries background-only overlay-rebuild semantics that must never
    apply to a premium poster."""
    return Path(str(preview) + ".ppv1-bg.png")


def _ppv1_final_fixed_size(project: FlyerProject, preview: Path, path: Path, *, size: tuple[int, int]) -> str:
    """Derive a fixed-size premium final. Center-cropping the 4:5 premium poster
    deterministically destroys the brand band (top ~6%) and footer (bottom ~6%)
    for instagram_post/story — those formats then fail per-format QA and are
    silently dropped (2026-07-02 review FA-2/CF-1). Instead:
    1. RECOMPOSE the same deterministic poster at the target size from the
       persisted, already-OCR-verified winner background (zero new generations;
       the composer is size-parametric and fact-locked). The composer's
       fail-closed refusals apply per aspect (e.g. a menu that fits 4:5 may
       overflow the square readable zone) — then:
    2. LETTERBOX the approved preview (every fact stays visible; QA passes).
    Returns "recomposed" | "letterboxed" for observability/tests."""
    bg = _ppv1_background_path(preview)
    if bg.exists():
        try:
            try:  # flat (VPS, deployed as flyer_premium_poster_v1.py) then package
                from flyer_premium_poster_v1 import compose_premium_poster_v1  # type: ignore
            except ImportError:  # pragma: no cover - src layout fallback
                from agents.flyer.premium_poster_v1 import compose_premium_poster_v1
            img, report = compose_premium_poster_v1(
                list(getattr(project, "locked_facts", []) or []),
                food_image_path=str(bg), size=size)
            # Require the FOOD background (design continuity with the approved
            # preview) — a gradient-fallback recompose would look like a different
            # product than what the owner approved.
            if img is not None and report.get("background") == "food":
                path.parent.mkdir(parents=True, exist_ok=True)
                img.save(path)
                return "recomposed"
        except Exception:  # noqa: BLE001 — fall through to the letterbox floor
            pass
    _export_from_source_image_contained(preview, path, size=size)
    return "letterboxed"


def _clear_stale_ppv1_sidecars(path: Path) -> None:
    """Remove premium provenance sidecars AFTER a legacy render successfully
    overwrote ``path``. Ordering matters (2026-07-02 structural review, HIGH): an
    eager pre-render unlink would strip provenance from a still-valid premium
    poster whenever the legacy fallthrough render then FAILED (path untouched,
    provenance gone → render_final_package silently falls back to center-crop).
    Called only at each successful-write exit of _render_model; premium delivery
    re-writes the sidecars fresh."""
    _ppv1_provenance_path(path).unlink(missing_ok=True)
    _ppv1_background_path(path).unlink(missing_ok=True)


def _ppv1_candidate_status_summary(report: dict) -> str:
    """Compact 'status=count' summary of best-of-N candidate outcomes, ordered by
    count desc then name, e.g. 'check_error=2,image_has_text=1'. Fits the 80-char
    audit reason budget for N<=3."""
    counts: dict[str, int] = {}
    for c in report.get("candidates", []) or []:
        s = str(c.get("background_status", "") or "unknown")
        counts[s] = counts.get(s, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ",".join(f"{s}={k}" for s, k in ordered) or "no_candidates"


def _render_model(project: FlyerProject, path: Path, *, concept_id: str, output_format: str, size: tuple[int, int] | None, model: str, quality: str, repair_instruction: str = "", scene_direction=None, force_background_only: bool = False) -> None:
    token = _FORCE_BACKGROUND_ONLY.set(True) if force_background_only else None
    try:
        # Premium Poster v1 (render-path opt-in + flag + allowlist + food/grocery +
        # required facts). On success writes `path` and returns; ANY miss falls
        # through to the existing render below (byte-identical when not armed). The
        # existing QA / visible-contract / send gates run on the result downstream,
        # unchanged + authoritative. The opt-in is set ONLY around the PRIMARY render
        # of each path (bare _generate_poster / managed generate-flyer-concepts) —
        # never around a rung; and this branch is additionally skipped during a
        # force_background_only recovery re-render so the premium path is a one-shot
        # primary attempt, not a rung. Reset the outcome per render so a prior
        # premium fire can never leave a stale value (None unambiguously means
        # "premium branch not entered this render").
        _PREMIUM_POSTER_V1_OUTCOME.set(None)
        # Guard gates added by the 2026-07-02 review:
        # - not repair_instruction (PR-B1): a strict-note / revision-feedback render
        #   must NEVER enter premium — the premium composer works from stored locked
        #   facts and would silently DROP the instruction (QA validates against the
        #   same stored facts, so the dropped instruction would ship undetected).
        # - deterministic model (FA-3): under FLYER_INTEGRATED_KILLSWITCH (or an
        #   explicitly deterministic draft model) the render must make ZERO
        #   generative calls — the premium branch would still POST to OpenRouter.
        # - concept_id == "C1" (FM-7/PR-M1): with concept_count > 1 each concept
        #   would burn its own N generations + full budget and the managed emitter
        #   would record only the LAST concept's outcome; premium is a one-shot
        #   primary attempt on the first concept only.
        if (not force_background_only
                and not repair_instruction
                and concept_id == "C1"
                and model.strip().lower() not in DETERMINISTIC_MODEL_NAMES
                and _premium_poster_v1_opt_in_path() is not None
                and _premium_poster_v1_armed(project)
                and _premium_poster_v1_eligible(project)):
            outcome = render_premium_poster_v1(
                project, path, concept_id=concept_id, output_format=output_format,
                size=size, model=model, quality=quality)
            _PREMIUM_POSTER_V1_OUTCOME.set(outcome)
            if outcome.delivered:
                return
            # not delivered -> fall through to the existing render path (outcome recorded)
        if _creative_director_v2_enabled(project):
            _populate_creative_direction_v2(project)
        if model.strip().lower() in DETERMINISTIC_MODEL_NAMES:
            _render(project, path, concept_id=concept_id, size=size)
            _clear_stale_ppv1_sidecars(path)
            return
        raw = _openrouter_image_bytes(project, concept_id=concept_id, output_format=output_format, size=size, model=model, quality=quality, repair_instruction=repair_instruction, scene_direction=scene_direction, force_background_only=force_background_only)
        raw_path = _raw_background_path(path)
        raw_path.unlink(missing_ok=True)
        if _integrated_poster_eligible(project) and not force_background_only:
            _write_generated_image(raw, path, size=size)
            _clear_stale_ppv1_sidecars(path)
            return
        # The prompt and the overlay MUST agree (same gate). For non-eligible flows
        # (localized / reference-extraction) the model renders the text itself, so we
        # must NOT composite the deterministic critical overlay on top — that would
        # duplicate text and reintroduce untranslated/incomplete facts. Those flows
        # keep the identity banner only (pre-overlay behavior). Background-only
        # eligible flows get the full deterministic overlay (the P1 fix).
        if not _background_only_eligible(project) and not force_background_only:
            if size is None:
                _write_generated_image(raw, path, size=size)
                _clear_stale_ppv1_sidecars(path)
                return
            _write_generated_image(raw, raw_path, size=size)
            apply_exact_identity_overlay(project, raw_path, path, size=size)
            _clear_stale_ppv1_sidecars(path)
            return
        # Background-only eligible: the model emitted a textless background; the
        # critical overlay (brand + title + schedule + menu items/prices + footer)
        # is the sole source of every required visible fact. `_apply_critical_text_
        # overlay` carries the system-python3 Pillow fallback for VPSes whose Hermes
        # venv lacks Pillow.
        if size is None:
            # PDF: composite the overlay on a PNG, then export to PDF (same pattern as
            # render_final_package's primary PDF path) — else a textless background PDF.
            pdf_px = (1275, 1650)
            _write_generated_image(raw, raw_path, size=pdf_px)
            overlaid = path.with_suffix(".overlaid.png")
            overlaid.unlink(missing_ok=True)
            try:
                _apply_critical_text_overlay(project, raw_path, overlaid, size=pdf_px, output_format=output_format)
                _export_from_source_image(overlaid, path, size=None)
                _clear_stale_ppv1_sidecars(path)
            finally:
                overlaid.unlink(missing_ok=True)
            return
        _write_generated_image(raw, raw_path, size=size)
        _apply_critical_text_overlay(project, raw_path, path, size=size, output_format=output_format)
        _clear_stale_ppv1_sidecars(path)
    finally:
        if token is not None:
            _FORCE_BACKGROUND_ONLY.reset(token)


def render_concept_previews(project: FlyerProject, output_dir: Path | str, *, model: str = "deterministic-renderer", quality: str = "low", concept_count: int = 1, repair_instruction: str = "", scene_direction=None, force_background_only: bool = False) -> list[RenderedAssetSpec]:
    output_dir = Path(output_dir)
    specs: list[RenderedAssetSpec] = []
    for concept_id in ("C1", "C2", "C3")[:concept_count]:
        path = output_dir / f"{project.project_id}-{concept_id}-preview.png"
        _render_model(project, path, concept_id=concept_id, output_format="concept_preview", size=(1080, 1350), model=model, quality=quality, repair_instruction=repair_instruction, scene_direction=scene_direction, force_background_only=force_background_only)
        quality_report = inspect_rendered_asset(path, expected_width=1080, expected_height=1350, mime_type="image/png")
        if not quality_report.ok:
            raise FlyerRenderError(f"rendered concept failed quality check: {quality_report.blockers}")
        write_text_manifest(project, path, output_format="concept_preview", selected_concept_id=concept_id, source_path=_raw_background_path(path))
        specs.append(RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview", width=1080, height=1350, concept_id=concept_id))
    return specs


def render_source_edit_preview(project: FlyerProject, output_dir: Path | str, *, model: str, quality: str = "medium", provider: str | None = None) -> RenderedAssetSpec:
    output_dir = Path(output_dir)
    concept_id = "C1"
    path = output_dir / f"{project.project_id}-{concept_id}-preview.png"
    provider_name = (provider or "manual_review").strip().lower()
    if provider_name == "openrouter":
        raw = _openrouter_source_edit_bytes(project, size=(1080, 1350), model=model, quality=quality)
    elif provider_name == "openai":
        raw = _openai_source_edit_bytes(project, size=(1080, 1350), model=model, quality=quality)
    elif provider_name == "manual_review":
        raise FlyerRenderError("source edit provider configured for manual review")
    else:
        raise FlyerRenderError(f"unsupported source edit provider: {provider_name or 'unknown'}")
    # Render-side robustness: give the source-edit output the SAME deterministic
    # critical-text overlay new flyers get. The generative-edit model has no
    # structural text guarantee (it drops/garbles required facts → visual_qa_failed),
    # so we write its edit as the raw background, then composite the deterministic
    # overlay (brand + title + schedule + items/prices + footer) on top. The raw
    # background is preserved separately so render_final_package can re-apply the
    # overlay per output format. `_apply_critical_text_overlay` carries the
    # system-python3 Pillow fallback for VPSes whose Hermes runtime lacks Pillow.
    raw_path = _raw_background_path(path)
    raw_path.unlink(missing_ok=True)
    _write_generated_image_contained(raw, raw_path, size=(1080, 1350))
    try:
        _apply_critical_text_overlay(project, raw_path, path, size=(1080, 1350), output_format="concept_preview")
    except FlyerRenderError:
        # Fail-closed: if the overlay cannot fit every required fact, do NOT ship a
        # silently-incomplete edit — clean up and propagate (downstream queues
        # manual review). Same contract as new-flyer generation.
        path.unlink(missing_ok=True)
        raw_path.unlink(missing_ok=True)
        raise
    quality_report = inspect_rendered_asset(path, expected_width=1080, expected_height=1350, mime_type="image/png")
    if not quality_report.ok:
        # P0-5 follow-up: clean up the orphan preview + raw-background files
        # before propagating the FlyerRenderError. Otherwise every quality-
        # check retry left a stale png in asset_dir (bounded but unbounded
        # over many retries on the same project). The generate-flyer-concepts
        # FlyerRenderError handler downstream rewrites manual_review state
        # but doesn't touch disk artifacts; this is the right place.
        path.unlink(missing_ok=True)
        raw_path.unlink(missing_ok=True)
        raise FlyerRenderError(f"edited concept failed quality check: {quality_report.blockers}")
    reference = _source_edit_reference_asset(project)
    write_text_manifest(
        project,
        path,
        output_format="concept_preview",
        selected_concept_id=concept_id,
        source_path=reference.path,
        # The output is the customer's uploaded artwork with the deterministic text
        # overlay RE-COMPOSED on top — not a pixel-preserving integrity edit — so the
        # manifest declares the overlaid facts (corroborating visual QA) rather than
        # claiming integrity-only.
        verification_mode="source_edit_overlay_recomposed",
        warnings=[
            "Source edit: customer artwork with the deterministic text overlay re-composed on top; required facts are declared and visual-QA-checked. Inspect the preview before approval."
        ],
    )
    return RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview", width=1080, height=1350, concept_id=concept_id)


def render_repair_edit(
    project: FlyerProject,
    base_png: Path | str,
    output_dir: Path | str,
    *,
    repair_instruction: str,
    model: str,
    quality: str = "high",
    output_name: str = "",
) -> RenderedAssetSpec:
    """Slice 2 premium repair: image-to-image edit of the PRIOR PREMIUM RENDER
    (``base_png``) with a scoped minimal-edit ``repair_instruction`` → ship the
    model's premium text VERBATIM.

    The repair render is written to ``output_name`` (a bare filename) when given,
    else the canonical ``<id>-C1-preview.png``. The ladder ALWAYS passes a
    DISTINCT per-attempt name (e.g. ``<id>-C1-repair1.png``) so a failed/dangerous
    repair NEVER overwrites the original integrated render at
    ``<id>-C1-preview.png`` — the original stays byte-untouched for the existing
    fallback ladder. A passing repair asset at this distinct name finalizes
    identically to a normal integrated render (``render_final_package`` selects
    the preview by ``asset.path`` and, finding no raw-background sidecar, exports
    it directly with no overlay — the same ``direct_poster_source`` path the
    integrated-poster-eligible flow already uses).

    Critically — and unlike ``render_source_edit_preview`` — this path does NOT
    composite the deterministic ``_apply_critical_text_overlay`` on top. The
    repair's whole purpose is to preserve the model-rendered premium text/layout;
    re-overlaying would defeat that (and re-introduce the flat-overlay look the
    repair is meant to avoid). The edited render's correctness is verified by the
    caller re-running the full referee (``run_visual_qa``); here we only run the
    structural ``inspect_rendered_asset`` quality check (dimensions / non-blank /
    file size) and fail closed (cleanup + FlyerRenderError) on a bad render."""
    output_dir = Path(output_dir)
    concept_id = "C1"
    name = output_name or f"{project.project_id}-{concept_id}-preview.png"
    path = output_dir / name
    raw = _openrouter_repair_edit_bytes(
        project,
        base_image_path=base_png,
        repair_instruction=repair_instruction,
        size=(1080, 1350),
        model=model,
        quality=quality,
    )
    # Write the model's edited bytes directly as the preview (contained to the
    # 4:5 canvas), preserving the premium text — NO deterministic overlay.
    path.unlink(missing_ok=True)
    _write_generated_image_contained(raw, path, size=(1080, 1350))
    quality_report = inspect_rendered_asset(path, expected_width=1080, expected_height=1350, mime_type="image/png")
    if not quality_report.ok:
        # Fail-closed: clean up the orphan preview before propagating so a bad
        # repair render leaves no stale png in asset_dir (the caller then falls
        # through to the existing recovery ladder / deterministic-overlay floor).
        path.unlink(missing_ok=True)
        raise FlyerRenderError(f"repaired concept failed quality check: {quality_report.blockers}")
    write_text_manifest(
        project,
        path,
        output_format="concept_preview",
        selected_concept_id=concept_id,
        source_path=base_png,
        warnings=[
            "Premium repair: image-to-image edit of the prior premium render; the "
            "model-rendered text is preserved (no deterministic overlay) and is "
            "verified by the visual-QA referee. Inspect the preview before approval."
        ],
    )
    return RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview", width=1080, height=1350, concept_id=concept_id)


def render_final_package(project: FlyerProject, output_dir: Path | str, *, model: str = "deterministic-renderer", quality: str = "medium") -> list[RenderedAssetSpec]:
    output_dir = Path(output_dir)
    concept_id = project.selected_concept_id or "C1"
    source_edit_project = _is_source_edit_project(project)
    selected_preview: Path | None = None
    selected_preview_asset: FlyerAsset | None = None
    if project.selected_concept_id:
        concept = next((c for c in project.concepts if c.concept_id == project.selected_concept_id), None)
        if concept is not None:
            asset = next((a for a in project.assets if a.asset_id == concept.preview_asset_id), None)
            if asset is not None:
                candidate = Path(asset.path)
                quality_report = inspect_rendered_asset(candidate, expected_width=1080, expected_height=1350, mime_type="image/png")
                if quality_report.ok:
                    selected_preview = candidate
                    selected_preview_asset = asset
    if selected_preview is None and (project.selected_concept_id or source_edit_project):
        if source_edit_project:
            raise FlyerRenderError("source edit final package requires an approved preview")
        raise FlyerRenderError("final package requires an approved preview")
    formats: list[tuple[FlyerOutputFormat, str, tuple[int, int] | None]] = [
        ("whatsapp_image", "final_whatsapp_image", FINAL_FORMAT_PIXEL_SHAPES["whatsapp_image"]),
        ("instagram_post", "final_instagram_post", FINAL_FORMAT_PIXEL_SHAPES["instagram_post"]),
        ("instagram_story", "final_instagram_story", FINAL_FORMAT_PIXEL_SHAPES["instagram_story"]),
        ("printable_pdf", "final_printable_pdf", FINAL_FORMAT_PIXEL_SHAPES["printable_pdf"]),
    ]
    specs: list[RenderedAssetSpec] = []
    for output_format, kind, size in formats:
        suffix = "pdf" if size is None else "png"
        path = output_dir / f"{project.project_id}-{output_format}.{suffix}"
        source_for_manifest: Path | None = None
        verification_mode = "source_edit_overlay_recomposed"
        manifest_warnings = [
            "Source edit: customer artwork with the deterministic text overlay re-composed on top; final files derive from the approved preview. Required facts are declared and visual-QA-checked."
        ]
        if selected_preview is not None:
            source = _raw_background_path(selected_preview)
            manual_completed_operator_preview = _is_manual_completed_operator_preview(project, selected_preview_asset)
            # Provenance, not mtime: eligibility itself is the signal.
            # - Background-only-eligible projects: the preview is a generated
            #   composite (raw background + overlay), and the raw is ALWAYS written
            #   together with the preview (render_concept_previews / repair both
            #   write a matched raw+preview), so re-applying the overlay from the
            #   raw at each output size is correct and never drops an edit — there
            #   are no edits, the overlay IS the text. This avoids cropping the 4:5
            #   preview (which under the no-text contract would drop required copy).
            # - Non-eligible (reference-extraction / operator-upload):
            #   the preview is the authoritative artifact (its text is model- or
            #   operator-produced) and there is no matched raw, so use it directly
            #   and never composite the overlay on top.
            # - Source-edit previews produced by this renderer are a separate case:
            #   the generative edit is stored as raw and the deterministic overlay
            #   owns the text. If the raw sidecar is missing, fail closed instead of
            #   claiming a recomposed text guarantee over an old pre-overlay preview.
            if source_edit_project and not manual_completed_operator_preview and not source.exists():
                raise FlyerRenderError("source edit final package requires raw edited background sidecar")
            direct_poster_source = (
                manual_completed_operator_preview
                or not source.exists()
                # Source-edits with a preserved raw model edit re-apply the
                # deterministic overlay per format (below) — new-flyer parity — so
                # they are NOT forced down the direct/no-re-overlay path here.
                or (not _background_only_eligible(project) and not source_edit_project)
                # Defensive stale-raw guard: a generated background-only composite
                # writes its raw and overlaid preview together (within ~1s), so a
                # preview MEANINGFULLY newer than its raw means the preview was
                # edited/regenerated apart from this raw — honor that approved
                # preview directly instead of rebuilding from a possibly-stale raw.
                # The tight window cleanly separates composites (sub-second) from
                # any later edit (seconds-to-minutes).
                # Source-edits are excluded from this guard: if their raw sidecar
                # exists, the source-edit contract is to re-apply the deterministic
                # overlay per final format rather than resize the 4:5 preview.
                or (
                    not source_edit_project
                    and selected_preview.exists()
                    and selected_preview.stat().st_mtime - source.stat().st_mtime > _RAW_COMPOSITE_FRESH_SECONDS
                )
            )
            if direct_poster_source:
                source = selected_preview
            if manual_completed_operator_preview:
                verification_mode = "source_edit_integrity_only"
                manifest_warnings = [
                    "Source edit: final files derive from the operator-approved manual asset; customer approval remains the visual/text QA gate."
                ]
            source_for_manifest = source
            if size is None:
                if direct_poster_source:
                    _export_from_source_image(source, path, size=None)
                else:
                    temp_png = path.with_suffix(".overlay-source.png")
                    overlaid_png = path.with_suffix(".overlaid.png")
                    if source_edit_project:
                        # Preserve the uploaded flyer's aspect (letterbox); fill/crop
                        # would cut off the customer's own artwork.
                        _export_from_source_image_contained(source, temp_png, size=(1275, 1650))
                    else:
                        _export_from_source_image(source, temp_png, size=(1275, 1650))
                    _apply_critical_text_overlay(project, temp_png, overlaid_png, size=(1275, 1650), output_format=output_format)
                    _export_from_source_image(overlaid_png, path, size=None)
                    temp_png.unlink(missing_ok=True)
                    overlaid_png.unlink(missing_ok=True)
            else:
                if direct_poster_source:
                    if source_edit_project:
                        _export_from_source_image_contained(source, path, size=size)
                    elif (_ppv1_provenance_path(selected_preview).exists()
                          and size != FINAL_FORMAT_PIXEL_SHAPES["whatsapp_image"]):
                        # Premium Poster v1 provenance: recompose the same
                        # deterministic poster at the target aspect (or letterbox)
                        # instead of center-cropping the brand band + footer off
                        # (2026-07-02 review FA-2/CF-1). whatsapp_image shares the
                        # preview's 4:5 aspect and stays a direct export of the
                        # EXACT approved artifact.
                        _ppv1_final_fixed_size(project, selected_preview, path, size=size)
                    else:
                        # WS2b (v2 spec amendment A1; labeled failure FA-2/CF-1):
                        # raw-less direct previews (v2/integrated renders,
                        # reference previews) must NEVER be center-cropped into
                        # fixed-shape formats — instagram_post cut the brand band
                        # and footer off and the format was then dropped at
                        # per-format QA. Letterbox instead: every fact stays
                        # visible; same-aspect targets are unaffected (contained
                        # == plain resize when aspects match).
                        _export_from_source_image_contained(source, path, size=size)
                else:
                    temp_png = path.with_suffix(".overlay-source.png")
                    if source_edit_project:
                        # Preserve the uploaded flyer's aspect (letterbox); fill/crop
                        # would cut off the customer's own artwork.
                        _export_from_source_image_contained(source, temp_png, size=size)
                    else:
                        _export_from_source_image(source, temp_png, size=size)
                    _apply_critical_text_overlay(project, temp_png, path, size=size, output_format=output_format)
                    temp_png.unlink(missing_ok=True)
        else:
            _render_model(project, path, concept_id=concept_id, output_format=output_format, size=size, model=model, quality=quality)
        width, height = size or (1275, 1650)
        quality_report = inspect_rendered_asset(path, expected_width=width, expected_height=height, mime_type="application/pdf" if size is None else "image/png")
        if not quality_report.ok:
            raise FlyerRenderError(f"rendered final failed quality check: {quality_report.blockers}")
        if source_edit_project:
            write_text_manifest(
                project,
                path,
                output_format=output_format,
                selected_concept_id=concept_id,
                source_path=source_for_manifest,
                verification_mode=verification_mode,
                warnings=manifest_warnings,
            )
        else:
            write_text_manifest(project, path, output_format=output_format, selected_concept_id=concept_id, source_path=source_for_manifest)
        specs.append(RenderedAssetSpec(path=path, kind=kind, output_format=output_format, width=width, height=height, concept_id=concept_id))
    return specs


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_asset_manifest(specs: list[RenderedAssetSpec], *, first_asset_number: int, source: str, original_message_id: str) -> list[FlyerAsset]:
    now = datetime.now(timezone.utc)
    assets: list[FlyerAsset] = []
    for offset, spec in enumerate(specs):
        mime = "application/pdf" if spec.path.suffix.lower() == ".pdf" else "image/png"
        assets.append(FlyerAsset(
            asset_id=f"A{first_asset_number + offset:04d}",
            kind=spec.kind,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            path=str(spec.path),
            mime_type=mime,
            sha256=_sha256(spec.path),
            original_message_id=original_message_id,
            received_at=now,
        ))
    return assets


def next_asset_number(project: FlyerProject) -> int:
    max_seen = 0
    for asset in project.assets:
        if asset.asset_id.startswith("A") and asset.asset_id[1:].isdigit():
            max_seen = max(max_seen, int(asset.asset_id[1:]))
    return max_seen + 1
