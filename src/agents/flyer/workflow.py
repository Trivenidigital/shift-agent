"""Pure workflow helpers for Hermes Flyer Studio.

This module intentionally has no filesystem or bridge dependency so tests can
run on Windows while scripts use the same state-machine logic on Linux.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from schemas import FlyerProject, FlyerWorkflowStatus


FLYER_INTENT_RE = re.compile(
    r"\b("
    r"flyer|flier|poster|banner|invite|invitation|"
    r"social\s+post|instagram\s+(?:post|story)|ig\s+(?:post|story)|"
    r"graphic|creative|design\s+(?:a\s+)?(?:flyer|poster|post)"
    r")\b",
    re.IGNORECASE,
)

LANGUAGE_NAMES = {
    "en": "English",
    "te": "Telugu",
    "hi": "Hindi",
    "ml": "Malayalam",
    "ta": "Tamil",
    "kn": "Kannada",
    "gu": "Gujarati",
    "mr": "Marathi",
    "pa": "Punjabi",
    "es": "Spanish",
    "mixed": "mixed language",
    "other": "the preferred language",
}

FIELD_LABELS = {
    "event_or_business_name": "event or business name",
    "event_date": "date",
    "event_time": "time",
    "venue_or_location": "venue or location",
    "contact_info": "contact info",
}


@dataclass(frozen=True)
class FlyerQualityResult:
    ok: bool
    blockers: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class RevisionPatchResult:
    field_updates: dict[str, str]
    notes_update: str | None = None
    raw_request_update: str | None = None
    changed: bool = False
    visual_only: bool = False
    ambiguous: bool = False
    unresolved_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "field_updates": self.field_updates,
            "notes_update": self.notes_update,
            "raw_request_update": self.raw_request_update,
            "changed": self.changed,
            "visual_only": self.visual_only,
            "ambiguous": self.ambiguous,
            "unresolved_reason": self.unresolved_reason,
        }


def build_missing_info_prompt(missing: list[str], *, preferred_language: str = "en") -> str:
    labels = [FIELD_LABELS.get(name, name.replace("_", " ")) for name in missing]
    language = LANGUAGE_NAMES.get(preferred_language, "the preferred language")
    if not labels:
        return "Thanks. I have the essentials and can start creating flyer concepts."
    if len(labels) == 1:
        joined = labels[0]
    elif len(labels) == 2:
        joined = f"{labels[0]} and {labels[1]}"
    else:
        joined = ", ".join(labels[:-1]) + f", and {labels[-1]}"
    return f"Please send the {joined}. I will keep the flyer copy in {language}."


def next_status_for_project(
    project: FlyerProject,
    *,
    has_required_assets: bool,
) -> FlyerWorkflowStatus:
    if project.fields.missing_required_fields():
        return "collecting_required_info"
    if not has_required_assets:
        return "awaiting_assets"
    return "generating_concepts"


def quality_check_project(project: FlyerProject) -> FlyerQualityResult:
    blockers = project.fields.missing_required_fields()
    warnings: list[str] = []
    if project.fields.preferred_language in {"te", "hi", "ml", "ta", "kn", "gu", "mr", "pa"} and not project.assets:
        warnings.append("regional_language_font_render_check_required")
    return FlyerQualityResult(ok=not blockers, blockers=blockers, warnings=warnings)


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _replace_once_or_flag(source: str, old: str, new: str) -> tuple[str, str]:
    if not source:
        return source, "not found"
    count = source.lower().count(old.lower())
    if count == 0:
        return source, "not found"
    if count > 1:
        return source, "appears multiple times"
    return re.sub(re.escape(old), new, source, count=1, flags=re.IGNORECASE), ""


def _append_once(source: str, addition: str) -> str:
    if not source:
        return addition
    if addition.lower() in source.lower():
        return source
    return f"{source.rstrip()} {addition}"


def _extract_item_swap(text: str) -> tuple[str, str]:
    body = " ".join((text or "").split())
    patterns = [
        r"\b(?:swap|replace)\s+(?P<old>[A-Za-z][A-Za-z\s/&-]{1,80}?)\s+with\s+(?P<new>[A-Za-z][A-Za-z\s/&-]{1,80}?)(?:\s*\(|[.!]|$)",
        r"\b(?:remove|exclude)\s+(?P<old>[A-Za-z][A-Za-z\s/&-]{1,80}?).*?\b(?:add|use)\s+(?P<new>[A-Za-z][A-Za-z\s/&-]{1,80}?)(?:\s*\(|[.!]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, body, flags=re.IGNORECASE)
        if not match:
            continue
        old = re.sub(r"\bfrom\s+original\s+flyer\b", "", match.group("old"), flags=re.IGNORECASE)
        new = re.sub(r"\bsame\s+price\b", "", match.group("new"), flags=re.IGNORECASE)
        old = old.strip(" .,\"'")
        new = new.strip(" .,\"'")
        if old and new:
            return old, new
    return "", ""


def _extract_phone(text: str) -> str:
    phone = re.search(r"(?:phone|contact|number)\D{0,30}(\+?\d[\d\s().-]{7,}\d)", text, re.IGNORECASE)
    if not phone:
        return ""
    raw = " ".join(phone.group(1).replace("(", "").replace(")", "").split())
    if not raw.startswith("+") and "+" in text[max(0, phone.start(1) - 5):phone.start(1) + 2]:
        raw = "+" + raw
    return raw


def _is_visual_only_revision(text: str) -> bool:
    lower = text.lower()
    visual_markers = (
        "make it", "color", "colour", "bigger", "smaller", "brighter",
        "darker", "use the logo", "use logo", "template", "photo", "image",
        "more festive", "less crowded", "font", "layout", "background",
    )
    critical_markers = ("date", "time", "phone", "contact", "price", "$", "location", "venue", "address", "not ", "from ")
    return any(marker in lower for marker in visual_markers) and not any(marker in lower for marker in critical_markers)


def extract_revision_patch(project: FlyerProject, text: str) -> RevisionPatchResult:
    """Extract high-confidence structured field edits from revision text."""
    updates: dict[str, str] = {}
    body = " ".join((text or "").split())
    lower = body.lower()
    current_date = project.fields.event_date or ""
    notes_update: str | None = None
    raw_request_update: str | None = None
    unresolved: list[str] = []

    month_day = re.search(
        r"\b(?:change|move|set|update)?\s*(?:the\s*)?date\s*(?:from\s+[a-z]+\s+\d{1,2}\s+)?(?:to|as|=|:)?\s*"
        r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(?P<day>\d{1,2})\b",
        lower,
    )
    day_only = re.search(
        r"\b(?:change|move|set|update)?\s*(?:the\s*)?date\s*(?:from\s+[a-z]+\s+\d{1,2}\s+)?(?:to|as|=|:)?\s*(?P<day>\d{1,2})(?:st|nd|rd|th)?\b",
        lower,
    )
    if month_day and current_date:
        year = int(current_date[:4])
        month = MONTHS[month_day.group("month")]
        day = int(month_day.group("day"))
        updates["event_date"] = datetime(year, month, day).date().isoformat()
    elif day_only and current_date:
        year, month, _old_day = [int(part) for part in current_date.split("-")]
        day = int(day_only.group("day"))
        updates["event_date"] = datetime(year, month, day).date().isoformat()

    time_match = re.search(
        r"\b(?:time\s*)?(?:from\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s+)?(?:to|as|=|:)\s*"
        r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)\b",
        lower,
    )
    if time_match:
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute") or "0")
        ampm = time_match.group("ampm")
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        updates["event_time"] = f"{hour:02d}:{minute:02d}"

    title_match = re.search(
        r"(?:title|offer|headline|flyer\s+title)\s*(?:should\s+be|=|:|to)\s+(?P<new>[^.]+?),?\s+not\s+(?P<old>[^.]+)",
        body,
        flags=re.IGNORECASE,
    )
    if not title_match:
        title_match = re.search(r"(?:title|offer|headline)\s+from\s+(?P<old>[^.]+?)\s+to\s+(?P<new>[^.]+?)(?:\.|$)", body, flags=re.IGNORECASE)
    if not title_match:
        title_match = re.search(r"this\s+should\s+be\s+(?P<new>[^.]+?),?\s+not\s+(?P<old>[^.]+)", body, flags=re.IGNORECASE)
    if title_match:
        old = title_match.group("old").strip(" .\"'")
        new = title_match.group("new").strip(" .\"'")
        if old and new and (project.fields.event_or_business_name or "").lower() == old.lower():
            updates["event_or_business_name"] = new
        elif new and ("title" in lower or "offer" in lower or "headline" in lower or "this should be" in lower):
            updates["event_or_business_name"] = new

    phone = _extract_phone(body)
    if phone:
        updates["contact_info"] = phone

    venue_match = re.search(
        r"(?:change|update|set)?\s*(?:venue|location|address)\s*(?:from\s+.+?\s+)?(?:to|as|=|:)\s*(?P<venue>[^.]+)",
        body,
        flags=re.IGNORECASE,
    )
    if venue_match:
        updates["venue_or_location"] = venue_match.group("venue").strip(" .")

    price_match = re.search(
        r"(?:change|update|set)?[^.]{0,80}?(?:price|combo|item)?[^.]{0,80}?\bfrom\s+\$?(?P<old>\d+(?:\.\d{2})?)\s+to\s+\$?(?P<new>\d+(?:\.\d{2})?)",
        body,
        flags=re.IGNORECASE,
    )
    if price_match:
        old_price = f"${price_match.group('old')}"
        new_price = f"${price_match.group('new')}"
        candidate_notes = project.fields.notes or ""
        replaced_notes, reason = _replace_once_or_flag(candidate_notes, old_price, new_price)
        if reason:
            candidate_raw = project.raw_request or ""
            replaced_raw, raw_reason = _replace_once_or_flag(candidate_raw, old_price, new_price)
            if raw_reason:
                unresolved.append(f"price {old_price} {reason if reason != 'not found' else 'not found in flyer details'}")
            else:
                raw_request_update = replaced_raw
        else:
            notes_update = replaced_notes

    old_item, new_item = _extract_item_swap(body)
    if old_item and new_item:
        item_instruction = (
            f"Replace menu item {old_item} with {new_item}. "
            f"Do not include {old_item} on the flyer."
        )
        notes_update = _append_once(notes_update if notes_update is not None else (project.fields.notes or ""), item_instruction)
        raw_request_update = _append_once(raw_request_update if raw_request_update is not None else (project.raw_request or ""), item_instruction)

    changed = bool(updates) or notes_update is not None or raw_request_update is not None
    visual_only = _is_visual_only_revision(body)
    ambiguous = bool(unresolved)
    return RevisionPatchResult(
        field_updates=updates,
        notes_update=notes_update,
        raw_request_update=raw_request_update,
        changed=changed,
        visual_only=visual_only,
        ambiguous=ambiguous,
        unresolved_reason="; ".join(unresolved),
    )


def extract_revision_field_updates(project: FlyerProject, text: str) -> dict[str, str]:
    return extract_revision_patch(project, text).field_updates
