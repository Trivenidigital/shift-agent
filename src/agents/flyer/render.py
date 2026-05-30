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
import hashlib
import http.client
import io
import json
import mimetypes
import os
import re
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
    from flyer_facts import fact_value  # type: ignore
except ImportError:  # pragma: no cover - src layout fallback
    from agents.flyer.facts import fact_value
try:
    from flyer_campaign_scene_prompts import campaign_scene_prompt_block  # type: ignore
except ImportError:  # pragma: no cover - src layout fallback
    from agents.flyer.campaign_scene_prompts import campaign_scene_prompt_block


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
OPENAI_IMAGE_EDIT_URL = "https://api.openai.com/v1/images/edits"
OPENAI_IMAGE_EDIT_TIMEOUT_SEC = 180
def _flyer_state_root() -> Path:
    return Path(os.environ.get("FLYER_STATE_ROOT", "/opt/shift-agent/state/flyer/"))


def _customers_path() -> Path:
    return _flyer_state_root() / "customers.json"


CUSTOMERS_PATH = Path("/opt/shift-agent/state/flyer/customers.json")
DETERMINISTIC_MODEL_NAMES = {"", "deterministic-renderer", "pillow", "local-pillow"}
TEXT_MANIFEST_SCHEMA_VERSION = 1
MAX_DETAIL_FACTS = 10
MAX_TEXT_FACTS = 16
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

    def add_detail(value: str) -> None:
        value = _clean_fact_text(value)
        if not value:
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
    menu_items = _menu_item_lines(project)
    menu_prices = {
        re.sub(r"\s+", "", price)
        for item in menu_items
        for _, price in [_split_item_price(item)]
        if price
    }
    compact = re.sub(r"\s+", " ", details)
    clauses = [part.strip(" .") for part in re.split(r";|\n|•|-{2,}|(?<=\.)\s+", compact) if part.strip(" .")]
    current_contact_digits = _digits(project.fields.contact_info or "")
    for clause in clauses:
        clause = _strip_request_instruction_prefix(clause)
        if not clause:
            continue
        if not _price_or_phone_clause(clause):
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
            and re.search(r"\b(?:add|set|use)\s+price\s+as\b|\bprice\s+as\b", clause, flags=re.IGNORECASE)
        ):
            continue
        if phones and current_contact_digits and current_contact_digits in phones and not has_offer_or_price:
            continue
        if phones and current_contact_digits and all(phone != current_contact_digits for phone in phones):
            continue
        add_detail(clause)
    for item in menu_items:
        add_detail(item)
    if len(selected) > MAX_DETAIL_FACTS:
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
    match = re.search(
        r"\bitems?\b\s*(?:to include in the flyer\s*)?[\"“”']?(.+?)(?:[\"“”']?\s*\.\s*(?:timings?|time)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
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
    if _context_has(_category_context(project), SALON_CATEGORY_TERMS) and "other hair services" in body.lower():
        line = "Other hair services available"
        key = _normalize_fact_text(line)
        if key not in seen:
            seen.add(key)
            items.append(line)
    return items[:MAX_DETAIL_FACTS]


def _locked_menu_item_lines(project: FlyerProject) -> list[str]:
    grouped: dict[int, dict[str, str]] = {}
    order: list[int] = []
    for fact in project.locked_facts:
        match = re.match(r"^item:(\d+):(name|price)$", fact.fact_id)
        if not match:
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
    return items[:MAX_DETAIL_FACTS]


def _same_text(left: str, right: str) -> bool:
    norm_left = re.sub(r"[^a-z0-9]+", " ", (left or "").lower()).strip()
    norm_right = re.sub(r"[^a-z0-9]+", " ", (right or "").lower()).strip()
    return bool(norm_left and norm_left == norm_right)


def _display_title(project: FlyerProject) -> str:
    business = fact_value(project, "business_name", fallback="")
    for value in (
        fact_value(project, "campaign_title", fallback=""),
        fact_value(project, "headline", fallback=""),
        project.fields.event_or_business_name or "",
    ):
        clean = _clean_fact_text(value)
        if clean and not _same_text(clean, business):
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
    if len(facts) > MAX_TEXT_FACTS:
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
    return {
        "business": _display_business_name(project),
        "title": _display_title(project),
        "schedule": schedule,
        "items": items,
        "extras": extras,
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


def _poster_copy_block(project: FlyerProject) -> str:
    plan = _poster_copy_plan(project)
    lines = [
        "Render the following text exactly. Do not summarize, paraphrase, invent, or omit these customer facts.",
        "Title is the campaign/product/service headline; Business/brand is the account identity or footer brand.",
        "Do not add delivery, catering, payment, ordering-channel, or service-availability claims unless they appear below.",
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
        lines.append("Menu item cards:")
        for name, price in plan.items:
            lines.append(f"- {name} - {price}")
    if plan.detail_lines:
        lines.append("Offer details:")
        for detail in plan.detail_lines:
            lines.append(f"- {detail}")
    lines.append("If any required text cannot be rendered legibly, make the typography simpler and larger rather than dropping facts.")
    return "\n".join(lines)


def _poster_layout_requirements(project: FlyerProject) -> str:
    plan = _poster_copy_plan(project)
    if plan.items:
        if not _is_food_or_grocery_project(project):
            return (
                "- Build a complete local service-business poster for the stated service category.\n"
                "- Use a clear brand masthead, large headline, service offer cards with prices, category-appropriate service imagery, and a footer for location/contact.\n"
                "- Service offer cards must pair each service name and price together.\n"
                "- If a service line is listed without a price, show it as a service label without a price, dash, or placeholder.\n"
                "- Keep text large, high-contrast, and centered inside its designed panels; avoid tiny text blocks and generic lower-third captions.\n"
                "- Keep the visual language category-safe for the stated business type; avoid restaurant/grocery and cultural-celebration styling unless the customer explicitly asks for it."
            )
        return (
            "- Build a full restaurant/menu poster, not a background template.\n"
            "- Use a brand masthead at the top, a large high-impact promo title, item cards with food imagery and prices, and a footer for location/contact.\n"
            "- Item cards must look like designed menu tiles, with each item name and price paired together.\n"
            "- Match the attached reference flyer's dense premium retail hierarchy when a reference is provided: bold headline, food photography, gold/green/red accents, ornamental separators, and phone-readable cards.\n"
            "- Keep text large, high-contrast, and centered inside its designed panels; avoid tiny text blocks and generic lower-third captions."
        )
    return (
        "- Build a complete finished poster flyer, not a blank background.\n"
        "- Use a clear brand masthead, large headline, offer/details section, visual proof imagery, and footer contact/action area.\n"
        "- Keep all required customer text large, high-contrast, and readable on a phone screen."
    )


def _reference_extraction_instruction(project: FlyerProject) -> str:
    refs = _project_reference_assets(project)
    if not refs:
        return "- none"
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
    sidecar.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
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
    return "\n".join(
        f"- {asset.kind}: {asset.asset_id} ({Path(asset.path).name}) notes={_sanitize_visual_context(getattr(asset, 'notes', '') or 'none')}"
        for asset in active_assets[-4:]
    )


def _active_brand_assets(project: FlyerProject):
    if os.environ.get("FLYER_DISABLE_BRAND_ASSETS") == "1":
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
    context = _category_context(project)
    return _context_has(context, FOOD_CATEGORY_TERMS)


def _design_direction(project: FlyerProject, concept_id: str) -> str:
    context = _category_context(project)
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
            "C1": "premium ethnic grocery or restaurant poster, bold food photography, tasteful retail hierarchy",
            "C2": "warm cultural food promotion with regional motifs only when they fit the customer, elegant food spread, refined community-event look",
            "C3": "modern social-media food creative, crisp editorial layout, bright promotional palette, restaurant-quality design",
        }
        return style_by_concept.get(concept_id, style_by_concept["C1"])
    return "neutral US local-business promotional flyer, professional service imagery, clear offer cards, readable prices, category-safe styling"


def _quality_bar(project: FlyerProject) -> str:
    if _is_food_or_grocery_project(project):
        return "- Strong hierarchy, appetizing food visuals when food is relevant, tasteful cultural warmth only when it fits the customer, no empty beige space."
    return "- Strong hierarchy, category-appropriate service visuals, modern local-business polish, and category-safe styling unless a different theme is explicitly requested."


def _project_reference_assets(project: FlyerProject):
    return [
        asset for asset in project.assets
        if asset.kind in {"logo", "reference_image"} and Path(asset.path).exists()
    ]


def _image_message_content(project: FlyerProject, *, concept_id: str, output_format: str, size: tuple[int, int] | None, repair_instruction: str = ""):
    prompt = _image_prompt(project, concept_id=concept_id, output_format=output_format, size=size, repair_instruction=repair_instruction)
    parts: list[dict] = [{"type": "text", "text": prompt}]
    brand_assets = _active_brand_assets(project)
    refs = _project_reference_assets(project)
    selected_assets = [*brand_assets[-1:], *refs[-1:]] if refs else [*brand_assets[-2:]]
    for asset in selected_assets:
        path = Path(asset.path)
        mime = asset.mime_type or mimetypes.guess_type(str(path))[0] or "image/png"
        if not mime.startswith("image/"):
            continue
        data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts if len(parts) > 1 else prompt


def _revision_notes_for_prompt(project: FlyerProject) -> str:
    if project.status not in {"revising_design", "manual_edit_required"}:
        return "- none"
    revisions = [r.request_text for r in project.revisions[-4:]]
    return "\n".join(f"- {r}" for r in revisions) if revisions else "- none"


def _image_prompt(project: FlyerProject, *, concept_id: str, output_format: str, size: tuple[int, int] | None, repair_instruction: str = "") -> str:
    revision_block = _revision_notes_for_prompt(project)
    reference_instruction = _reference_preservation_instruction(project)
    sanitized_style = _sanitize_visual_context(project.fields.style_preference or "festive, clean, professional")
    campaign_scene_block = campaign_scene_prompt_block(
        context=_category_context(project),
        business=_sanitize_visual_context(
            fact_value(project, "business_name", fallback=project.fields.event_or_business_name) or ""
        ),
        offer=_sanitize_visual_context(fact_value(project, "campaign_title", fallback="") or ""),
    )
    repair_block = ""
    if repair_instruction.strip():
        repair_block = f"""
Autonomous repair instruction:
- {_sanitize_visual_context(repair_instruction.strip())}
"""
    return f"""Create a complete, finished customer-ready poster flyer for WhatsApp delivery.

Design direction: {_design_direction(project, concept_id)}.
Customer style notes: {sanitized_style}.
Output format: {output_format}; aspect ratio {_aspect_ratio(size)}.

{campaign_scene_block}

Controlled customer copy:
{_poster_copy_block(project)}

Visual context for style and imagery:
- theme/category: {_sanitize_visual_context(fact_value(project, "business_name", fallback=project.fields.event_or_business_name) or project.raw_request or "local SMB promotion")}
- style: {sanitized_style}

Layout requirements:
{_poster_layout_requirements(project)}

Reference/menu extraction instructions:
{_reference_extraction_instruction(project)}

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
- The final image must already contain the finished flyer text, menu item cards, prices, schedule, location, and contact when those facts are provided.
- If customer brand assets are listed, preserve the business identity and use the active logo/template as the visual reference.
- If an uploaded reference image/template is attached, preserve its visual identity and offer category but replace stale readable facts with the controlled customer copy above.
- If there is no one-time date, present the recurring schedule clearly instead of inventing a date.
- Avoid QR codes, fake logos, watermarks, unreadable microtext, and placeholder glyph boxes.
{_language_constraint_hint(project)}
"""


def _reference_preservation_instruction(project: FlyerProject) -> str:
    if not [*_active_brand_assets(project), *_project_reference_assets(project)]:
        return "- none"
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
            title_font = _font(ImageFont, max(32, int(width * 0.046)), bold=True, text=str(menu_payload["title"]))
            sub_font = _font(ImageFont, max(18, int(width * 0.023)), bold=True)
            item_font = _font(ImageFont, max(18, int(width * 0.023)), bold=True)
            small_font = _font(ImageFont, max(15, int(width * 0.018)))

            # Title card (top-left): brand + full campaign title + schedule +
            # promo/offer facts. Content-adaptive height so no required visible
            # fact is truncated — the card grows downward (capped above the menu
            # panel). `extras` carries the offer/promotion/pricing facts the menu
            # item cards don't show, so visual QA finds every required fact.
            biz_font = _font(ImageFont, max(19, int(width * 0.025)), bold=True)
            business = str(menu_payload.get("business") or "").strip()
            title_text = str(menu_payload["title"]).strip()
            box_x0, box_y0, box_x1 = margin, int(height * 0.055), int(width * 0.58)
            inner_w = box_x1 - box_x0 - 44
            card_lines: list[tuple[object, tuple[int, int, int, int], str]] = []
            if business and not _same_text(business, title_text):
                for ln in _wrap(draw, business, biz_font, inner_w)[:1]:
                    card_lines.append((biz_font, (255, 255, 245, 255), ln))
            for ln in _wrap(draw, title_text, title_font, inner_w)[:3]:
                card_lines.append((title_font, (255, 218, 85, 255), ln))
            if menu_payload["schedule"]:
                for ln in _wrap(draw, str(menu_payload["schedule"]), sub_font, inner_w)[:1]:
                    card_lines.append((sub_font, (255, 255, 240, 250), ln))
            for extra in list(menu_payload.get("extras") or [])[:2]:
                for ln in _wrap(draw, str(extra), small_font, inner_w)[:1]:
                    card_lines.append((small_font, (255, 236, 205, 250), ln))
            content_h = sum(int(getattr(f, "size", 18) * 1.2) for f, _c, _t in card_lines)
            box_y1 = min(int(height * 0.585), box_y0 + content_h + 34)
            draw.rounded_rectangle((box_x0, box_y0, box_x1, box_y1), radius=22, fill=(42, 86, 42, 232), outline=(255, 205, 74, 245), width=3)
            y = box_y0 + 18
            for f, color, ln in card_lines:
                if y + getattr(f, "size", 18) > box_y1 - 10:
                    break
                draw.text((box_x0 + 22, y), ln, font=f, fill=color)
                y += int(getattr(f, "size", 18) * 1.2)

            panel = (margin, int(height * 0.64), width - margin, height - margin)
            draw.rounded_rectangle(panel, radius=24, fill=(18, 54, 34, 236), outline=(255, 205, 74, 245), width=3)
            px0, py0, px1, py1 = panel
            draw.text((px0 + 24, py0 + 22), "MENU", font=sub_font, fill=(255, 218, 85, 255))
            items = list(menu_payload["items"])
            cols = 2 if width >= 900 and len(items) > 3 else 1
            gap = 14
            card_w = (px1 - px0 - 48 - gap * (cols - 1)) // cols
            card_h = max(58, min(84, (py1 - py0 - 118) // max(1, (len(items) + cols - 1) // cols)))
            start_y = py0 + 66
            for idx, item in enumerate(items):
                col = idx % cols
                row = idx // cols
                x = px0 + 24 + col * (card_w + gap)
                cy = start_y + row * (card_h + 10)
                if cy + card_h > py1 - 58:
                    break
                draw.rounded_rectangle((x, cy, x + card_w, cy + card_h), radius=14, fill=(116, 18, 30, 238), outline=(255, 190, 58, 230), width=2)
                name, price = _split_item_price(item)
                draw.text((x + 16, cy + 12), name, font=item_font, fill=(255, 255, 245, 255))
                price_bbox = draw.textbbox((0, 0), price, font=item_font)
                draw.text((x + card_w - 16 - (price_bbox[2] - price_bbox[0]), cy + 12), price, font=item_font, fill=(255, 218, 85, 255))
            footer = " | ".join(str(v) for v in (menu_payload["location"], menu_payload["contact"]) if v)
            if footer:
                draw.text((px0 + 24, py1 - 42), footer, font=small_font, fill=(255, 255, 240, 245))
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


def _apply_critical_text_overlay(project: FlyerProject, source: Path | str, target: Path | str, *, size: tuple[int, int], output_format: str) -> None:
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


def _openrouter_image_bytes(project: FlyerProject, *, concept_id: str, output_format: str, size: tuple[int, int] | None, model: str, quality: str, repair_instruction: str = "") -> bytes:
    api_key = _read_env_value("OPENROUTER_API_KEY")
    if not api_key:
        raise FlyerRenderError("OPENROUTER_API_KEY is missing")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _image_message_content(project, concept_id=concept_id, output_format=output_format, size=size, repair_instruction=repair_instruction)}],
        "modalities": ["image", "text"],
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


def _openrouter_source_edit_bytes(
    project: FlyerProject,
    *,
    size: tuple[int, int] | None,
    model: str,
    quality: str,
) -> bytes:
    api_key = _read_env_value("OPENROUTER_API_KEY")
    if not api_key or "PLACEHOLDER" in api_key.upper():
        raise FlyerRenderError("OPENROUTER_API_KEY is missing or placeholder")
    reference = _source_edit_reference_asset(project)
    reference_path = Path(reference.path)
    mime = reference.mime_type or mimetypes.guess_type(str(reference_path))[0] or "image/png"
    data_url = (
        f"data:{mime};base64,"
        + base64.b64encode(reference_path.read_bytes()).decode("ascii")
    )
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _source_edit_prompt(project)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "modalities": ["image", "text"],
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
    try:
        with urllib.request.urlopen(req, timeout=OPENROUTER_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:1000]
        raise FlyerRenderError(f"OpenRouter source edit HTTP {e.code}: {err}") from e
    except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError) as e:
        if isinstance(e, urllib.error.URLError):
            raise FlyerRenderError(f"OpenRouter source edit connection failed: {e.reason}") from e
        raise FlyerRenderError(f"OpenRouter source edit response failed: {type(e).__name__}: {e}") from e
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


def _source_edit_reference_asset(project: FlyerProject) -> FlyerAsset:
    for asset in reversed(project.assets):
        if asset.kind == "reference_image" and Path(asset.path).exists():
            mime = asset.mime_type or mimetypes.guess_type(asset.path)[0] or ""
            if not mime.startswith("image/"):
                raise FlyerRenderError(f"source edit reference must be an image, got {mime or 'unknown'}")
            return asset
    raise FlyerRenderError("source edit requires an uploaded reference image")


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


def _render_model(project: FlyerProject, path: Path, *, concept_id: str, output_format: str, size: tuple[int, int] | None, model: str, quality: str, repair_instruction: str = "") -> None:
    if model.strip().lower() in DETERMINISTIC_MODEL_NAMES:
        _render(project, path, concept_id=concept_id, size=size)
        return
    raw = _openrouter_image_bytes(project, concept_id=concept_id, output_format=output_format, size=size, model=model, quality=quality, repair_instruction=repair_instruction)
    raw_path = _raw_background_path(path)
    raw_path.unlink(missing_ok=True)
    if size is None:
        _write_generated_image(raw, path, size=size)
        return
    _write_generated_image(raw, raw_path, size=size)
    # Deterministic exact-text composition (Priority-1 fix for the ~100%
    # `visual_qa_failed` incident): the image model cannot reliably render exact
    # text, so we composite it ourselves. The critical overlay is self-contained
    # — brand + campaign title + schedule (title card), menu items/prices (menu
    # panel), location + contact (footer) — covering every required visible fact
    # in one coherent pass over the model background. This brings forward to the
    # CONCEPT stage the same deterministic text layer that already runs at
    # `render_final_package`, so visual QA reads our crisp text instead of the
    # model's garbled rendering. `_apply_critical_text_overlay` carries the
    # system-python3 Pillow fallback for VPSes whose Hermes venv lacks Pillow.
    _apply_critical_text_overlay(project, raw_path, path, size=size, output_format=output_format)


def render_concept_previews(project: FlyerProject, output_dir: Path | str, *, model: str = "deterministic-renderer", quality: str = "low", concept_count: int = 1, repair_instruction: str = "") -> list[RenderedAssetSpec]:
    output_dir = Path(output_dir)
    specs: list[RenderedAssetSpec] = []
    for concept_id in ("C1", "C2", "C3")[:concept_count]:
        path = output_dir / f"{project.project_id}-{concept_id}-preview.png"
        _render_model(project, path, concept_id=concept_id, output_format="concept_preview", size=(1080, 1350), model=model, quality=quality, repair_instruction=repair_instruction)
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
    _raw_background_path(path).unlink(missing_ok=True)
    _write_generated_image_contained(raw, path, size=(1080, 1350))
    quality_report = inspect_rendered_asset(path, expected_width=1080, expected_height=1350, mime_type="image/png")
    if not quality_report.ok:
        # P0-5 follow-up: clean up the orphan preview + raw-background files
        # before propagating the FlyerRenderError. Otherwise every quality-
        # check retry left a stale png in asset_dir (bounded but unbounded
        # over many retries on the same project). The generate-flyer-concepts
        # FlyerRenderError handler downstream rewrites manual_review state
        # but doesn't touch disk artifacts; this is the right place.
        path.unlink(missing_ok=True)
        _raw_background_path(path).unlink(missing_ok=True)
        raise FlyerRenderError(f"edited concept failed quality check: {quality_report.blockers}")
    reference = _source_edit_reference_asset(project)
    write_text_manifest(
        project,
        path,
        output_format="concept_preview",
        selected_concept_id=concept_id,
        source_path=reference.path,
        verification_mode="source_edit_integrity_only",
        warnings=[
            "Source-preserving edit output is model-edited artwork; inspect the preview visually before approval."
        ],
    )
    return RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview", width=1080, height=1350, concept_id=concept_id)


def render_final_package(project: FlyerProject, output_dir: Path | str, *, model: str = "deterministic-renderer", quality: str = "medium") -> list[RenderedAssetSpec]:
    output_dir = Path(output_dir)
    concept_id = project.selected_concept_id or "C1"
    selected_preview: Path | None = None
    if project.selected_concept_id:
        concept = next((c for c in project.concepts if c.concept_id == project.selected_concept_id), None)
        if concept is not None:
            asset = next((a for a in project.assets if a.asset_id == concept.preview_asset_id), None)
            if asset is not None:
                candidate = Path(asset.path)
                quality_report = inspect_rendered_asset(candidate, expected_width=1080, expected_height=1350, mime_type="image/png")
                if quality_report.ok:
                    selected_preview = candidate
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
        if selected_preview is not None:
            source = _raw_background_path(selected_preview)
            direct_poster_source = (
                not source.exists()
                or (
                    selected_preview.exists()
                    and selected_preview.stat().st_mtime > source.stat().st_mtime
                )
            )
            if direct_poster_source:
                source = selected_preview
            source_for_manifest = source
            if size is None:
                if direct_poster_source:
                    _export_from_source_image(source, path, size=None)
                else:
                    temp_png = path.with_suffix(".overlay-source.png")
                    overlaid_png = path.with_suffix(".overlaid.png")
                    _export_from_source_image(source, temp_png, size=(1275, 1650))
                    _apply_critical_text_overlay(project, temp_png, overlaid_png, size=(1275, 1650), output_format=output_format)
                    _export_from_source_image(overlaid_png, path, size=None)
                    temp_png.unlink(missing_ok=True)
                    overlaid_png.unlink(missing_ok=True)
            else:
                if direct_poster_source:
                    if _is_source_edit_project(project):
                        _export_from_source_image_contained(source, path, size=size)
                    else:
                        _export_from_source_image(source, path, size=size)
                else:
                    temp_png = path.with_suffix(".overlay-source.png")
                    _export_from_source_image(source, temp_png, size=size)
                    _apply_critical_text_overlay(project, temp_png, path, size=size, output_format=output_format)
                    temp_png.unlink(missing_ok=True)
        else:
            _render_model(project, path, concept_id=concept_id, output_format=output_format, size=size, model=model, quality=quality)
        width, height = size or (1275, 1650)
        quality_report = inspect_rendered_asset(path, expected_width=width, expected_height=height, mime_type="application/pdf" if size is None else "image/png")
        if not quality_report.ok:
            raise FlyerRenderError(f"rendered final failed quality check: {quality_report.blockers}")
        if _is_source_edit_project(project):
            write_text_manifest(
                project,
                path,
                output_format=output_format,
                selected_concept_id=concept_id,
                source_path=source_for_manifest,
                verification_mode="source_edit_integrity_only",
                warnings=[
                    "Source-preserving edit output is model-edited artwork; final files derive from the approved preview."
                ],
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
