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
    if project.fields.preferred_language in {"te", "hi"} and not project.assets:
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


def extract_revision_field_updates(project: FlyerProject, text: str) -> dict[str, str]:
    """Extract high-confidence structured field edits from revision text."""
    updates: dict[str, str] = {}
    body = " ".join((text or "").split())
    lower = body.lower()
    current_date = project.fields.event_date or ""

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

    return updates
