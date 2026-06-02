"""Pure workflow helpers for Hermes Flyer Studio.

This module intentionally has no filesystem or bridge dependency so tests can
run on Windows while scripts use the same state-machine logic on Linux.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
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

STATUS_LINES = {
    "intake_started": "I have the request open and am checking the flyer details.",
    "collecting_required_info": "I am waiting for the missing flyer details before creating the design.",
    "awaiting_assets": "I am waiting for the logo, photo, menu, or reference image needed for this flyer.",
    "manual_edit_required": "I couldn't finish this automatically. I'll review it and send an update here.",
    "generating_concepts": "The flyer design is being generated now.",
    "awaiting_concept_selection": "The preview is ready. Please choose a concept or send changes.",
    "revising_design": "Your requested changes are saved and the revised design is being prepared.",
    "awaiting_final_approval": "The preview is ready for approval. Reply APPROVE when it looks right.",
    "finalizing_assets": "The final files are being prepared for delivery.",
    "delivered": "The final flyer files have been delivered.",
    "completed": "This flyer project is complete.",
    "closed_no_send": "This flyer request was closed without sending final assets.",
    # P0 #2 2026-05-28 — warn-tier status query reply. Sits alongside the
    # rich initial warn-tier customer copy (Commit 2). Verbs verified
    # against customer_copy_policy.FORBIDDEN_COMPLETION_VERBS.
    "delivered_with_warning": "Your flyer draft is out with a small note. Reply with corrections or OK if you've checked it.",
}


# Per-reason customer-facing copy for projects sitting at manual_edit_required.
# Keyed on FlyerManualReviewReason (S1 enum). Falls back to STATUS_LINES
# generic line when the project's reason_code is not in this table.
# Every reason_code in src/platform/schemas.py::FlyerManualReviewReason MUST
# have an entry here — enforced by test_state_reply_table.py structural
# coverage test.
MANUAL_REVIEW_REASON_LINES: dict[str, str] = {
    "unclassified": (
        "This project is queued for designer review. I'll follow up here when it's ready."
    ),
    "legacy_unknown": (
        "This project is queued for designer review. I'll follow up here when it's ready."
    ),
    "source_edit_provider_unavailable": (
        "Your edit is queued for a designer to apply by hand. "
        "I have the requested changes and the saved account details — no extra information needed from you."
    ),
    "reference_unsupported": (
        "The file you uploaded isn't a supported format for an exact edit. "
        "Please re-upload the source flyer as a JPG or PNG image — once we have it, our designer can pick this up."
    ),
    "reference_provider_unavailable": (
        "I can't find the source flyer to edit. "
        "Please re-upload the flyer image and our designer will continue from there."
    ),
    "reference_low_confidence": (
        "I'm having trouble reading the details from your uploaded reference. "
        "If you can, re-upload a clearer copy, or describe the details you'd like included."
    ),
    "reference_not_run": (
        "I haven't been able to extract the details from your uploaded reference yet. "
        "I'll follow up here as soon as that's done."
    ),
    "visual_qa_failed": (
        "The generated flyer didn't pass our quality checks. "
        "It's queued for designer review and I'll send the corrected version here when it's ready."
    ),
    "missing_required_facts": (
        "I'm missing a couple of required details before I can finish this flyer. "
        "Please send the remaining info and I'll continue."
    ),
    "operator_request": (
        "This project is being reviewed by our team. I'll follow up here when it's ready."
    ),
    "policy_block": (
        "This project is paused for a quick review. I'll follow up here once it's cleared."
    ),
    "provider_timeout": (
        "I hit a temporary issue generating this. It's queued for retry/designer review and I'll follow up here when it's ready."
    ),
    "dependency_missing": (
        "I hit a setup issue while generating this flyer. "
        "It's queued for review and I'll follow up here when it's ready."
    ),
}


# Per-reason customer-facing copy for projects sitting at `closed_no_send`.
# Closed projects are operator-aborted — the customer must learn the project
# won't be delivered AND what to do next (typically: re-send a fresh
# request). Keyed on FlyerManualReviewReason; falls back to
# STATUS_LINES["closed_no_send"] for any unknown code.
# Every reason_code in src/platform/schemas.py::FlyerManualReviewReason MUST
# have an entry here — enforced by test_flyer_state_reply_table.py.
CLOSED_NO_SEND_REASON_LINES: dict[str, str] = {
    "unclassified": (
        "This flyer project was closed without delivering. "
        "Please re-send your request and I'll start a fresh one."
    ),
    "legacy_unknown": (
        "This flyer project was closed without delivering. "
        "Please re-send your request and I'll start a fresh one."
    ),
    "source_edit_provider_unavailable": (
        "I wasn't able to apply that source-flyer edit on this project. "
        "Please re-send the flyer with the changes you want and I'll start a fresh request."
    ),
    "reference_unsupported": (
        "This source-flyer edit couldn't be completed — the file format wasn't supported. "
        "Please re-upload the source flyer as a JPG or PNG image to start a fresh request."
    ),
    "reference_provider_unavailable": (
        "This source-flyer edit couldn't be completed because the source flyer wasn't available. "
        "Please re-upload the flyer image to start a fresh request."
    ),
    "reference_low_confidence": (
        "This flyer project was closed without delivering. "
        "Please re-upload a clearer source flyer, or describe the details you'd like included, and I'll start fresh."
    ),
    "reference_not_run": (
        "This flyer project was closed without delivering. "
        "Please re-send your source flyer and I'll start a fresh request."
    ),
    "visual_qa_failed": (
        "This flyer project was closed without delivering — the generated flyer didn't pass our quality checks. "
        "Please re-send your request and I'll start a fresh one."
    ),
    "missing_required_facts": (
        "This flyer project was closed without delivering because required details were missing. "
        "Please re-send your request with the missing info and I'll start a fresh one."
    ),
    "operator_request": (
        "This flyer project was closed by our team. "
        "Please re-send your request if you'd like us to try again."
    ),
    "policy_block": (
        "This flyer project was closed during review. "
        "Please re-send your request and we'll take another look."
    ),
    "provider_timeout": (
        "This flyer project was closed without delivering due to a temporary issue. "
        "Please re-send your request and I'll try again."
    ),
    "dependency_missing": (
        "This flyer project was closed without delivering because a required setup component was unavailable. "
        "Please re-send your request and I'll start a fresh one."
    ),
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
    requires_confirmation: bool = False
    confirmation_reason: str = ""
    replace_old_text: str = ""
    replace_new_text: str = ""
    price_delta_cents: int = 0
    already_applied: bool = False

    def to_dict(self) -> dict:
        return {
            "field_updates": self.field_updates,
            "notes_update": self.notes_update,
            "raw_request_update": self.raw_request_update,
            "changed": self.changed,
            "visual_only": self.visual_only,
            "ambiguous": self.ambiguous,
            "unresolved_reason": self.unresolved_reason,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_reason": self.confirmation_reason,
            "replace_old_text": self.replace_old_text,
            "replace_new_text": self.replace_new_text,
            "price_delta_cents": self.price_delta_cents,
            "already_applied": self.already_applied,
        }


def _extract_replace_text(body: str) -> tuple[str, str]:
    """Return (old_text, new_text) for simple replace-text instructions."""
    if not body:
        return "", ""

    def clean_new(value: str) -> str:
        value = value.strip(" .,\"'“”`")
        value = re.sub(
            r"\s*(?:,?\s*(?:and\s+)?)?"
            r"(?:increase|raise|bump|decrease|lower|reduce)\s+"
            r"(?:the\s+)?(?:[a-z][a-z0-9 '&/-]{0,40}\s+)?(?:price|cost|amount)\s+"
            r"(?:by\s+)?\$?\d+(?:\.\d{1,2})?\b.*$",
            "",
            value,
            flags=re.IGNORECASE,
        )
        return value.strip(" .,\"'“”`")

    def safe_unquoted(old: str, new: str) -> bool:
        old_lower = old.lower()
        if (
            ("$" in new or re.search(r"\b\d+(?:\.\d{2})?\b", new))
            and ("price" in old_lower)
            and ("price any event" not in old_lower)
        ):
            return False
        if any(tok in old_lower for tok in ("date", "time", "phone", "contact", "location", "venue", "address")):
            return False
        if old_lower in {"it", "this", "that"}:
            return False
        return bool(old and new and old.lower() != new.lower())

    patterns = [
        r"\b(?:replace|change)\b[^\"'\n]{0,120}[\"'](?P<old>[^\"']{1,160})[\"']\s*[-–—:|]*\s*(?:with|to|->)\s*[\"'](?P<new>[^\"']{1,160})[\"']",
        # Curly quotes are often pasted inconsistently on WhatsApp (left/left or right/right).
        r"\b(?:replace|change)\b[^“”\n]{0,120}[“”](?P<old>[^“”]{1,160})[“”]\s*[-–—:|]*\s*(?:with|to|->)\s*[“”](?P<new>[^“”]{1,160})[“”]",
        # Backticks show up frequently when users paste “exact” text.
        r"\b(?:replace|change)\b[^`\n]{0,120}`(?P<old>[^`]{1,160})`\s*[-–—:|]*\s*(?:with|to|->)\s*`(?P<new>[^`]{1,160})`",
    ]
    for pattern in patterns:
        match = re.search(pattern, body, flags=re.IGNORECASE)
        if not match:
            continue
        old = match.group("old").strip(" .,\"'“”`")
        new = clean_new(match.group("new"))
        if old and new and old.lower() != new.lower():
            return old, new

    arrow_body = re.sub(
        r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
        r"(?:(?:change|replace|update|edit)\b\s*(?:this|it|the\s+flyer)?|do\s+this|make\s+this\s+change)\s*[:,-]?\s*",
        "",
        body,
        flags=re.IGNORECASE,
    )
    for arrow_source in (arrow_body, body):
        arrow = re.search(
            r"(?P<old>[A-Za-z][^.?!\n]{2,100}?)\s*(?:->|=>)\s*"
            r"(?P<new>[A-Za-z][^.?!\n]{2,100}?)"
            r"(?=\s*(?:[,;]\s*(?:and\s+)?(?:increase|raise|bump|decrease|lower|reduce)\b|[.!?]|$))",
            arrow_source,
            flags=re.IGNORECASE,
        )
        if not arrow:
            continue
        old = arrow.group("old").strip(" .,:;\"'“”`-–—")
        new = clean_new(arrow.group("new"))
        if (
            safe_unquoted(old, new)
            and re.search(r"[A-Za-z]", old)
            and re.search(r"[A-Za-z]", new)
            and len(re.findall(r"[A-Za-z0-9]+", old)) >= 2
            and len(re.findall(r"[A-Za-z0-9]+", new)) >= 2
        ):
            return old, new
    fallback = re.search(
        r"\b(?:replace|change)\s+(?P<old>[^.?!\n]{1,80}?)\s+(?:with|to|->)\s+(?P<new>[^.?!\n]{1,80}?)(?:[.!?]|$)",
        body,
        flags=re.IGNORECASE,
    )
    if fallback:
        old = fallback.group("old").strip(" .,:;\"'“”`-–—")
        new = clean_new(fallback.group("new").strip(" .,:;\"'“”`-–—"))
        # Avoid stealing structured field edits ("Change X price to $9.99", etc.).
        # Keep a single exception for the common badge phrase "Price any event".
        if safe_unquoted(old, new):
            return old, new
    return "", ""


def _parse_price_cents(raw: str) -> int:
    value = raw.strip().replace(",", "")
    if "." in value:
        dollars, cents = value.split(".", 1)
        cents = cents.ljust(2, "0")[:2]
    else:
        dollars, cents = value, "00"
    return int(dollars) * 100 + int(cents)


def _format_price_cents(cents: int, *, force_cents: bool) -> str:
    dollars, remainder = divmod(cents, 100)
    if force_cents or remainder:
        return f"${dollars}.{remainder:02d}"
    return f"${dollars}"


def _extract_price_delta_cents(text: str) -> int:
    match = next(_iter_price_delta_matches(text), None)
    if not match:
        return 0
    amount = _parse_price_cents(match.group("amount"))
    direction = match.group("dir").lower()
    if direction in {"decrease", "lower", "reduce"}:
        amount *= -1
    return amount


def _iter_price_delta_matches(text: str):
    return re.finditer(
        r"\b(?P<dir>increase|raise|bump|decrease|lower|reduce)\s+"
        r"(?:the\s+)?(?:[a-z][a-z0-9 '&/-]{0,40}\s+)?(?:price|cost|amount)\s+"
        r"(?:by\s+)?\$?(?P<amount>\d+(?:\.\d{1,2})?)\b",
        text or "",
        flags=re.IGNORECASE,
    )


def _apply_price_delta_near_text(source: str, anchor: str, delta_cents: int) -> tuple[str, str]:
    if not source or not anchor or not delta_cents:
        return source, ""
    matches = list(re.finditer(re.escape(anchor), source, flags=re.IGNORECASE))
    if not matches:
        return source, "anchor not found"
    if len(matches) > 1:
        return source, "anchor appears multiple times"
    match = matches[0]
    tail = source[match.end():]
    delimiter = re.search(r";|\n|\.(?!\d)", tail)
    segment_end = delimiter.start() if delimiter else min(len(tail), 120)
    segment = tail[:segment_end]
    prices = list(re.finditer(r"\$\s*(?P<amount>\d+(?:\.\d{1,2})?)", segment))
    if not prices:
        return source, "price not found near edited text"
    if len(prices) > 1:
        return source, "multiple prices found near edited text"
    price = prices[0]
    old_raw = price.group("amount")
    old_cents = _parse_price_cents(old_raw)
    new_cents = old_cents + delta_cents
    if new_cents < 0:
        return source, "price delta would make price negative"
    replacement = _format_price_cents(new_cents, force_cents="." in old_raw)
    start = match.end() + price.start()
    end = match.end() + price.end()
    return f"{source[:start]}{replacement}{source[end:]}", ""


def _replace_once_whitespace_collapsed_or_flag(source: str, old: str, new: str) -> tuple[str, str]:
    if not source:
        return source, "not found"
    tokens = [tok for tok in re.split(r"\s+", old.strip()) if tok]
    if len(tokens) < 2:
        return source, "too short for whitespace match"
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(tok) for tok in tokens) + r"(?!\w)"
    matches = list(re.finditer(pattern, source, flags=re.IGNORECASE))
    if not matches:
        return source, "not found"
    if len(matches) > 1:
        return source, "appears multiple times"
    match = matches[0]
    return f"{source[:match.start()]}{new}{source[match.end():]}", ""


def apply_revision_text_edit_to_value(value: str, old_text: str, new_text: str, price_delta_cents: int = 0) -> tuple[str, str]:
    replaced, reason = _replace_once_or_flag(value, old_text, new_text)
    if reason == "not found":
        replaced, reason = _replace_once_whitespace_collapsed_or_flag(value, old_text, new_text)
    if reason == "not found":
        replaced, reason = _replace_once_normalized_or_flag(value, old_text, new_text)
    if reason:
        return value, reason
    if price_delta_cents:
        return _apply_price_delta_near_text(replaced, new_text, price_delta_cents)
    return replaced, ""


def revision_text_value_contains(value: str, old_text: str) -> bool:
    if not value or not old_text:
        return False
    if old_text.lower() in value.lower():
        return True
    collapsed_value = re.sub(r"\s+", " ", value).strip().lower()
    collapsed_old = re.sub(r"\s+", " ", old_text).strip().lower()
    if collapsed_old and collapsed_old in collapsed_value:
        return True
    _replaced, reason = _replace_once_normalized_or_flag(value, old_text, old_text)
    return not reason


def _extract_remove_time_instruction(text: str) -> str:
    """Handle 'remove/delete/exclude 16:00' even when 'extra/duplicate' isn't said."""
    if not text:
        return ""
    if not re.search(r"\b(?:remove|delete|exclude)\b", text, flags=re.IGNORECASE):
        return ""
    match = re.search(r"(?<![$\d])\b(?P<time>\d{1,2}:\d{2})\b(?!\.\d)", text)
    if match:
        return f'Remove time text "{match.group("time")}" from the flyer.'
    ampm_match = re.search(r"(?<![$\d])\b(?P<time>\d{1,2}\s*(?:am|pm))\b", text, flags=re.IGNORECASE)
    if not ampm_match:
        return ""
    return f'Remove time text "{ampm_match.group("time").upper()}" from the flyer.'


def _normalized_text_and_map(text: str) -> tuple[str, list[int]]:
    """Normalize text for fuzzy substring match while mapping back to original indices."""
    normalized_chars: list[str] = []
    index_map: list[int] = []
    prev_space = False
    for idx, ch in enumerate(text):
        if ch.isalnum():
            normalized_chars.append(ch.lower())
            index_map.append(idx)
            prev_space = False
            continue
        if ch.isspace():
            if prev_space:
                continue
            normalized_chars.append(" ")
            index_map.append(idx)
            prev_space = True
            continue
        # drop punctuation/symbols
    normalized = "".join(normalized_chars).strip()
    if not normalized:
        return "", []
    # adjust map when we stripped leading spaces via .strip()
    leading = 0
    for ch in normalized_chars:
        if ch != " ":
            break
        leading += 1
    if leading:
        index_map = index_map[leading:]
        normalized = normalized[leading:]
    return normalized, index_map


def _replace_once_normalized_or_flag(source: str, old: str, new: str) -> tuple[str, str]:
    if not source:
        return source, "not found"
    old_tokens = [tok for tok in re.split(r"\s+", old.strip()) if tok]
    if len(old_tokens) < 3:
        return source, "too short for fuzzy match"
    normalized_source, source_map = _normalized_text_and_map(source)
    normalized_old, _old_map = _normalized_text_and_map(old)
    if not normalized_source or not normalized_old:
        return source, "not found"
    hits: list[int] = []
    start = 0
    while True:
        pos = normalized_source.find(normalized_old, start)
        if pos < 0:
            break
        hits.append(pos)
        start = pos + 1
        if len(hits) > 5:
            break
    if not hits:
        return source, "not found"
    if len(hits) > 1:
        return source, "appears multiple times"
    match_start = hits[0]
    match_end = match_start + len(normalized_old) - 1
    if len(normalized_old) > 200:
        return source, "match too long"
    if match_end >= len(source_map):
        return source, "match mapping failed"
    orig_start = source_map[match_start]
    orig_end = source_map[match_end] + 1
    if orig_end - orig_start > 220:
        return source, "match span too long"
    return f"{source[:orig_start]}{new}{source[orig_end:]}", ""


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


def build_project_status_reply(project: FlyerProject) -> str:
    """Return the deterministic customer-facing status reply for the project.

    Rules:
      - For manual_edit_required projects with an actively-queued
        manual_review, the reason_code (S1 enum) drives the copy via
        MANUAL_REVIEW_REASON_LINES.
      - For closed_no_send projects with manual_review.status=closed_no_send,
        the reason_code drives the copy via CLOSED_NO_SEND_REASON_LINES so
        the customer learns why the project was aborted and what to do next.
      - For all other statuses, STATUS_LINES is the source of truth.
      - manual_review with status `break_glass_sent` or `completed` is no
        longer a customer-blocking signal; the project's own status drives
        the reply (the operator already acted).
    """
    line = STATUS_LINES.get(project.status, "I have this flyer project open.")
    manual = getattr(project, "manual_review", None)
    manual_status = getattr(manual, "status", "none") if manual is not None else "none"
    if (
        project.status == "manual_edit_required"
        and manual is not None
        and manual_status in {"queued", "in_progress"}
    ):
        reason_code = ((getattr(manual, "reason_code", "") or "unclassified").strip().lower() or "unclassified")
        line = MANUAL_REVIEW_REASON_LINES.get(reason_code, STATUS_LINES["manual_edit_required"])
    elif (
        project.status == "closed_no_send"
        and manual is not None
        and manual_status == "closed_no_send"
    ):
        reason_code = ((getattr(manual, "reason_code", "") or "unclassified").strip().lower() or "unclassified")
        line = CLOSED_NO_SEND_REASON_LINES.get(reason_code, STATUS_LINES["closed_no_send"])
    return (
        "Flyer Studio\n"
        "------------\n"
        f"{line}"
    )


def _read_env_value(name: str, *, env_path: Path | None = None) -> str:
    """Lookup an env value: process env first, then file-based env stores.

    P0-5 follow-up: align with `visual_qa.py::_openrouter_key` which checks
    BOTH `/root/.hermes/.env` and `/opt/shift-agent/.env`. Source-edit had
    historically only checked the agent .env, missing keys provisioned via
    Hermes' own env store. When the caller passes `env_path` explicitly, only
    that file is consulted (preserves test isolation).
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if env_path is not None:
        candidates = [env_path]
    else:
        # Order matters: Hermes-managed env first (operator-provisioned),
        # then agent-local env (legacy fallback). The first file that holds
        # a non-empty value wins.
        candidates = [
            Path(os.environ.get("HERMES_ENV_PATH", "/root/.hermes/.env")),
            Path(os.environ.get("SHIFT_AGENT_ENV_PATH", "/opt/shift-agent/.env")),
        ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                if key.strip() == name:
                    extracted = raw.strip().strip('"').strip("'")
                    if extracted:
                        return extracted
        except OSError:
            continue
    return ""


def _source_edit_provider_parts(provider) -> tuple[str, str]:
    if provider is None:
        return "manual_review", "manual_review"
    if isinstance(provider, str):
        return provider.strip().lower(), ""
    if isinstance(provider, dict):
        return (
            str(provider.get("provider") or "manual_review").strip().lower(),
            str(provider.get("model") or "").strip(),
        )
    return (
        str(getattr(provider, "provider", "manual_review") or "manual_review").strip().lower(),
        str(getattr(provider, "model", "") or "").strip(),
    )


def source_edit_provider_ready(project_or_asset, *, provider=None, env_path: Path | None = None) -> tuple[bool, str]:
    provider_name, model = _source_edit_provider_parts(provider)
    if provider_name == "openrouter":
        key_name = "OPENROUTER_API_KEY"
    elif provider_name == "openai":
        key_name = "OPENAI_API_KEY"
    elif provider_name == "manual_review":
        return False, "source edit provider configured for manual review"
    else:
        return False, f"source edit provider is unsupported: {provider_name or 'unknown'}"
    key = _read_env_value(key_name, env_path=env_path)
    if not key or "PLACEHOLDER" in key.upper():
        return False, f"source edit provider is not configured: {key_name} missing"
    assets = []
    if isinstance(project_or_asset, dict):
        if "assets" in project_or_asset:
            assets = list(project_or_asset.get("assets") or [])
        else:
            assets = [project_or_asset]
    else:
        assets = list(getattr(project_or_asset, "assets", []) or [])
    reference = next((asset for asset in reversed(assets) if (asset.get("kind") if isinstance(asset, dict) else getattr(asset, "kind", "")) == "reference_image"), None)
    if reference is None:
        return False, "source edit needs an uploaded reference image"
    mime = reference.get("mime_type", "") if isinstance(reference, dict) else getattr(reference, "mime_type", "")
    if mime and not str(mime).startswith("image/"):
        return False, f"source edit reference must be an image, got {mime}"
    detail_model = model or "openai/gpt-5.4-image-2"
    return True, f"source edit provider configured: {provider_name}/{detail_model}"


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
DAY_PATTERN = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"


def _replace_once_or_flag(source: str, old: str, new: str) -> tuple[str, str]:
    if not source:
        return source, "not found"
    normalized_old, _old_map = _normalized_text_and_map(old)
    normalized_new, _new_map = _normalized_text_and_map(new)
    if normalized_old and normalized_old in normalized_new:
        normalized_source, _source_map = _normalized_text_and_map(source)
        if normalized_new and normalized_new in normalized_source:
            without_new = normalized_source.replace(normalized_new, " ")
            if normalized_old not in without_new:
                return source, "already applied"
    elif old.lower() in new.lower() and new.lower() in source.lower():
        without_new = re.sub(re.escape(new), "", source, flags=re.IGNORECASE)
        if old.lower() not in without_new.lower():
            return source, "already applied"
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


def _append_instruction(
    notes_update: str | None,
    raw_request_update: str | None,
    project: FlyerProject,
    instruction: str,
) -> tuple[str, str]:
    return (
        _append_once(notes_update if notes_update is not None else (project.fields.notes or ""), instruction),
        _append_once(raw_request_update if raw_request_update is not None else (project.raw_request or ""), instruction),
    )


def _title_day_range(start: str, end: str) -> str:
    return f"{start.strip().title()} to {end.strip().title()}"


def _extract_existing_day_range(project: FlyerProject) -> str:
    source = f"{project.fields.notes or ''} {project.raw_request or ''}"
    match = re.search(
        rf"\b(?P<start>{DAY_PATTERN})\s*(?:to|through|-)\s*(?P<end>{DAY_PATTERN})\b",
        source,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return _title_day_range(match.group("start"), match.group("end"))


def _extract_day_range_instruction(project: FlyerProject, text: str) -> str:
    if not text:
        return ""
    if not re.search(r"\b(?:change|update|set|make|switch)\b", text, flags=re.IGNORECASE):
        return ""
    match = re.search(
        rf"\b(?:change|update|set|make|switch)\b[^.?!]{{0,80}}?\b(?:to|as)\s+"
        rf"(?P<start>{DAY_PATTERN})\s*(?:to|through|-)\s*(?P<end>{DAY_PATTERN})\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    new_range = _title_day_range(match.group("start"), match.group("end"))
    instruction = f"Use schedule {new_range}."
    old_range = _extract_existing_day_range(project)
    if old_range and old_range.lower() != new_range.lower():
        instruction += f" Do not use {old_range}."
    return instruction


def _extract_item_swap(text: str) -> tuple[str, str]:
    body = " ".join((text or "").split())
    # If this looks like a replace-text instruction (not a menu-item swap),
    # avoid interpreting it as a menu edit.
    old_text, new_text = _extract_replace_text(body)
    if old_text and new_text:
        return "", ""
    patterns = [
        r"\b(?:swap|replace)\s+(?P<old>[A-Za-z][A-Za-z\s/&-]{1,80}?)\s+with\s+(?P<new>[A-Za-z][A-Za-z\s/&-]{1,80}?(?:\s+for\s+\$?\d+(?:\.\d{2})?)?)(?:\s*\(|[.!]|$)",
        r"\b(?:remove|exclude)\s+(?P<old>[A-Za-z][A-Za-z\s/&-]{1,80}?)\s+(?:and\s+)?(?:add|use)\s+(?P<new>[A-Za-z][A-Za-z\s/&-]{1,80}?(?:\s+same\s+price)?)(?:\s*\(|[.!]|$)",
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


def _extract_extra_time_instruction(text: str) -> str:
    if re.search(r"\b(?:remove|delete|exclude)\b", text, flags=re.IGNORECASE):
        time_before_marker = re.search(
            r"(?<![$\d])(?:\btime\s*[:=]?\s*)?(?P<time>\d{1,2}:\d{2})\b(?!\.\d)"
            r"[^.?!]{0,80}\b(?:duplicated|duplicate|extra)\b",
            text,
            flags=re.IGNORECASE,
        )
        if time_before_marker:
            return f'Remove duplicate/extra time text "{time_before_marker.group("time")}" from the flyer.'
    marker = re.search(r"\b(?:extra|duplicate)\b(?P<tail>[^.?!]*)", text, flags=re.IGNORECASE)
    if not marker:
        return ""
    match = re.search(
        r"(?<![$\d])\b(?P<time>\d{1,2}(?::\d{2})?)\b(?!\.\d)",
        marker.group("tail"),
    )
    if not match:
        ampm_match = re.search(
            r"(?<![$\d])\b(?P<time>\d{1,2}\s*(?:am|pm))\b",
            marker.group("tail"),
            flags=re.IGNORECASE,
        )
        if ampm_match:
            match_time = ampm_match.group("time").upper()
        else:
            match_time = ""
    else:
        match_time = match.group("time")
    if not match_time or not re.search(r"\b(?:remove|delete|exclude)\b", text[:marker.start()], flags=re.IGNORECASE):
        return ""
    return f'Remove duplicate/extra time text "{match_time}" from the flyer.'


def _extract_item_add_instruction(text: str) -> str:
    match = re.search(
        r"\b(?:add|include|put)\s+(?P<item>[A-Za-z][A-Za-z\s/&-]{1,80}?)\s+for\s+\$?(?P<price>\d+(?:\.\d{2})?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    item = match.group("item").strip(" .,\"'")
    price = match.group("price")
    return f"Add menu item {item} for ${price}."


def _extract_remove_add_instruction(text: str) -> tuple[str, str]:
    match = re.search(
        r"\b(?:remove|exclude)\s+(?P<old>[A-Za-z][A-Za-z\s/&-]{1,80}?)\s+(?:and\s+)?(?:add|use)\s+(?P<new>[A-Za-z][A-Za-z\s/&-]{1,80}?(?:\s+same\s+price)?)(?:\s*\(|[.!]|$)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", ""
    old = match.group("old").strip(" .,\"'")
    new = match.group("new").strip(" .,\"'")
    if not old or not new:
        return "", ""
    return f"Remove menu item {old}.", f"Add menu item {new}."


def _replace_item_price_once(source: str, item: str, new_price: str) -> tuple[str, str]:
    if not source:
        return source, "not found"
    matches = list(re.finditer(re.escape(item), source, flags=re.IGNORECASE))
    if not matches:
        return source, "not found"
    if len(matches) > 1:
        return source, "appears multiple times"
    match = matches[0]
    delimiter = r"[,;\n]|(?<!\d)\.(?!\d)"
    before_stops = [m.end() for m in re.finditer(delimiter, source[:match.start()])]
    segment_start = max(before_stops) if before_stops else 0
    after_match = re.search(delimiter, source[match.end():])
    segment_end = match.end() + after_match.start() if after_match else len(source)
    segment = source[segment_start:segment_end]
    price_match = re.search(r"\$\s*\d+(?:\.\d{2})?", segment)
    if not price_match:
        return source, "price not found near item"
    absolute_start = segment_start + price_match.start()
    absolute_end = segment_start + price_match.end()
    return f"{source[:absolute_start]}{new_price}{source[absolute_end:]}", ""


def _extract_item_price_to_new(text: str) -> tuple[str, str]:
    match = re.search(
        r"\b(?:change|update|set)\s+(?P<item>[A-Za-z][A-Za-z\s/&-]{1,80}?)\s+(?:price\s+)?(?:to|as|=|:)\s+\$?(?P<price>\d+(?:\.\d{2})?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", ""
    item = re.sub(r"\bprice\b", "", match.group("item"), flags=re.IGNORECASE).strip(" .,\"'")
    return item, f"${match.group('price')}"


def _extract_category_price_instruction(text: str) -> str:
    match = re.search(
        r"\b(?:change|update|set)\s+prices\s+of\s+(?:any|all|every|the)?\s*"
        r"(?P<category>[A-Za-z][A-Za-z0-9 '&/-]{1,40}?)\s+(?:to|as|=|:)\s+\$?(?P<price>\d+(?:\.\d{2})?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"\b(?:change|update|set)\s+(?:any|all|every|the)?\s*"
            r"(?P<category>[A-Za-z][A-Za-z0-9 '&/-]{1,40}?)\s+prices\s+(?:to|as|=|:)\s+\$?(?P<price>\d+(?:\.\d{2})?)\b",
            text,
            flags=re.IGNORECASE,
        )
    if not match:
        return ""
    category = match.group("category").strip(" .,\"'")
    category = re.sub(r"\b(?:item|items|any|all|every|the)\b", "", category, flags=re.IGNORECASE).strip(" .,\"'")
    if not category:
        return ""
    return f"Set all {category} prices to ${match.group('price')}."


def _format_revision_price_list(raw_prices: str) -> str:
    values: list[str] = []
    for match in re.finditer(r"\$?\s*(\d+(?:\.\d{1,2})?)", raw_prices or ""):
        value = match.group(1)
        if "." in value:
            whole, cents = value.split(".", 1)
            value = f"{whole}.{cents.ljust(2, '0')[:2]}"
        values.append(f"${value}")
    return ", ".join(values)


def _extract_service_price_list_instruction(text: str) -> str:
    match = re.search(
        r"\b(?:keep|set|change|update|use)?\s*prices?\s*(?:to|as|=|:)\s*"
        r"(?P<prices>\$?\s*\d+(?:\.\d{1,2})?(?:\s*,\s*\$?\s*\d+(?:\.\d{1,2})?){1,8})\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    prices = _format_revision_price_list(match.group("prices"))
    if prices.count("$") < 2:
        return ""
    return f"Set flyer service prices to {prices}."


def _extract_visual_design_revision_instruction(text: str) -> str:
    lower = text.lower()
    if not re.search(r"\b(?:apply|change|update|edit|modify|revise|keep|use|make|set)\b", lower):
        return ""
    if not re.search(
        r"\b(?:background|color|colour|photo|photos|picture|pictures|image|images|"
        r"celebrity|celebrities|hairstyle|hairstyles|layout|font|template)\b",
        lower,
    ):
        return ""
    instruction = " ".join(text.split())
    instruction = re.sub(
        r"^\s*apply\s+(?:these\s+)?changes(?:\s+to\s+(?:the\s+)?(?:existing|current|same)\s+flyer)?\s*:?\s*",
        "",
        instruction,
        flags=re.IGNORECASE,
    )
    instruction = re.sub(
        r"\s*(?:and\s+)?(?:keep|set|change|update|use)?\s*prices?\s*(?:to|as|=|:)\s*"
        r"\$?\s*\d+(?:\.\d{1,2})?(?:\s*,\s*\$?\s*\d+(?:\.\d{1,2})?){1,8}\b.*$",
        "",
        instruction,
        flags=re.IGNORECASE,
    ).strip(" .")
    if not instruction:
        return ""
    if len(instruction) > 240:
        instruction = instruction[:237].rstrip() + "..."
    return f"Apply visual design change: {instruction}."


def _extract_layout_emphasis_revision_instruction(text: str) -> str:
    lower = text.lower()
    has_size_request = bool(
        re.search(r"\b(?:look|make|keep|show)\b.{0,80}\b(?:smaller|less\s+prominent|tiny|smaller\s+font)\b", lower)
        or re.search(r"\b(?:smaller|less\s+prominent|tiny|smaller\s+font)\b.{0,80}\b(?:contact|phone|number|address|location)\b", lower)
    )
    targets_contact = bool(re.search(r"\b(?:contact|phone|number|address|location)\b", lower))
    has_focus_request = bool(re.search(r"\b(?:main\s+focus|focus\s+should\s+be|focus\s+on|highlight|emphasize|emphasis)\b", lower))
    targets_offer = bool(re.search(r"\b(?:service|services|offer|offers|items|menu|products|specials)\b", lower))
    if not ((has_size_request and targets_contact) or (has_focus_request and targets_offer)):
        return ""
    instruction = " ".join(text.split()).strip(" .")
    if len(instruction) > 240:
        instruction = instruction[:237].rstrip() + "..."
    return f"Apply layout/emphasis revision: {instruction}."


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


def _roll_event_date_forward(year: int, month: int, day: int, *, by: str, not_before) -> str | None:
    """P1-2 roll-forward: a flyer advertises a future or same-day event, so an explicit date
    edit that lands BEFORE the flyer's creation date is rolled to its next occurrence.
    month-day edits advance the YEAR (keep the named month); day-only edits advance the
    MONTH (skipping months that lack that day, e.g. the 31st). Crash-safe: an invalid edit
    (e.g. the 30th of February) rolls to the next valid month instead of raising as the bare
    datetime() previously did. Returns the soonest valid ISO date >= not_before, or None when
    no valid calendar date exists for the request (e.g. day 32/00/99) — the caller then leaves
    event_date unset and records an unresolved edit rather than emitting an invalid date that
    would fail schema validation downstream (Codex review)."""
    def _mk(y: int, m: int, d: int):
        try:
            return datetime(y, m, d).date()
        except ValueError:
            return None

    original = _mk(year, month, day)
    if original is not None and original >= not_before:
        return original.isoformat()
    for _ in range(24):
        if by == "year":
            year += 1
        else:
            month += 1
            if month > 12:
                month, year = 1, year + 1
        candidate = _mk(year, month, day)
        if candidate is not None and candidate >= not_before:
            return candidate.isoformat()
    # A valid day always finds a future occurrence within the bound; reaching here means the
    # day itself is not a valid calendar day (e.g. 32/00/99). Signal None so the caller skips
    # the update — never emit an invalid date string.
    return original.isoformat() if original is not None else None


def extract_revision_patch(project: FlyerProject, text: str) -> RevisionPatchResult:
    """Extract high-confidence structured field edits from revision text."""
    updates: dict[str, str] = {}
    body = " ".join((text or "").split())
    lower = body.lower()
    current_date = project.fields.event_date or ""
    notes_update: str | None = None
    raw_request_update: str | None = None
    unresolved: list[str] = []
    fuzzy_confirmation_required = False
    replace_old_text = ""
    replace_new_text = ""
    replace_price_delta_cents = 0
    already_applied = False

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
        rolled = _roll_event_date_forward(year, month, day, by="year", not_before=project.created_at.date())
        if rolled is not None:
            updates["event_date"] = rolled
        else:
            unresolved.append(f"requested event date (day {day}) is not a valid calendar date")
    elif day_only and current_date:
        year, month, _old_day = [int(part) for part in current_date.split("-")]
        day = int(day_only.group("day"))
        rolled = _roll_event_date_forward(year, month, day, by="month", not_before=project.created_at.date())
        if rolled is not None:
            updates["event_date"] = rolled
        else:
            unresolved.append(f"requested event date (day {day}) is not a valid calendar date")

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

    item_for_price, new_item_price = _extract_item_price_to_new(body)
    category_price_instruction = _extract_category_price_instruction(body)
    if category_price_instruction:
        notes_update, raw_request_update = _append_instruction(notes_update, raw_request_update, project, category_price_instruction)
    elif item_for_price and new_item_price:
        replaced_notes, reason = _replace_item_price_once(project.fields.notes or "", item_for_price, new_item_price)
        if reason:
            replaced_raw, raw_reason = _replace_item_price_once(project.raw_request or "", item_for_price, new_item_price)
            if raw_reason:
                unresolved.append(f"item price for {item_for_price} {reason if reason != 'not found' else 'not found in flyer details'}")
            else:
                raw_request_update = replaced_raw
        else:
            notes_update = replaced_notes

    service_price_instruction = _extract_service_price_list_instruction(body)
    if service_price_instruction:
        notes_update, raw_request_update = _append_instruction(notes_update, raw_request_update, project, service_price_instruction)

    visual_design_instruction = _extract_visual_design_revision_instruction(body)
    if visual_design_instruction:
        notes_update, raw_request_update = _append_instruction(notes_update, raw_request_update, project, visual_design_instruction)

    layout_emphasis_instruction = _extract_layout_emphasis_revision_instruction(body)
    if layout_emphasis_instruction:
        notes_update, raw_request_update = _append_instruction(notes_update, raw_request_update, project, layout_emphasis_instruction)

    old_item, new_item = _extract_item_swap(body)
    if old_item and new_item:
        item_instruction = (
            f"Replace menu item {old_item} with {new_item}. "
            f"Do not include {old_item} on the flyer."
        )
        notes_update, raw_request_update = _append_instruction(notes_update, raw_request_update, project, item_instruction)

    remove_instruction, add_instruction = _extract_remove_add_instruction(body)
    for instruction in (remove_instruction, add_instruction):
        if instruction:
            notes_update, raw_request_update = _append_instruction(notes_update, raw_request_update, project, instruction)

    for instruction in (_extract_extra_time_instruction(body), _extract_item_add_instruction(body)):
        if instruction:
            notes_update, raw_request_update = _append_instruction(notes_update, raw_request_update, project, instruction)
    remove_time_instruction = _extract_remove_time_instruction(body)
    if remove_time_instruction:
        notes_update, raw_request_update = _append_instruction(notes_update, raw_request_update, project, remove_time_instruction)

    day_range_instruction = _extract_day_range_instruction(project, body)
    if day_range_instruction:
        notes_update, raw_request_update = _append_instruction(notes_update, raw_request_update, project, day_range_instruction)

    old_text, new_text = _extract_replace_text(body)
    if old_text and new_text:
        price_delta_matches = list(_iter_price_delta_matches(body))
        price_delta_cents = _extract_price_delta_cents(body)
        notes_already_applied = False
        locked_matches = [
            fact
            for fact in getattr(project, "locked_facts", []) or []
            if getattr(fact, "source", "") == "customer_text"
            and revision_text_value_contains(str(getattr(fact, "value", "")), old_text)
        ]
        if len(locked_matches) > 1:
            unresolved.append(f"text {old_text!r} appears multiple times in locked facts")
        candidate_notes = project.fields.notes or ""
        if len(price_delta_matches) > 1:
            unresolved.append("multiple price deltas not supported")
        replaced_notes, reason = _replace_once_or_flag(candidate_notes, old_text, new_text)
        if unresolved:
            pass
        elif reason == "already applied":
            notes_already_applied = True
        elif reason:
            if candidate_notes and reason == "not found" and revision_text_value_contains(candidate_notes, old_text):
                collapsed_notes, collapsed_reason = _replace_once_whitespace_collapsed_or_flag(candidate_notes, old_text, new_text)
                if not collapsed_reason:
                    if price_delta_cents:
                        collapsed_notes, delta_reason = _apply_price_delta_near_text(collapsed_notes, new_text, price_delta_cents)
                        if delta_reason:
                            unresolved.append(f"price delta {delta_reason}")
                        else:
                            notes_update = collapsed_notes
                            fuzzy_confirmation_required = True
                    else:
                        notes_update = collapsed_notes
                        fuzzy_confirmation_required = True
                else:
                    unresolved.append(f"text {old_text!r} {collapsed_reason} in flyer details")
            elif candidate_notes and reason != "not found":
                unresolved.append(f"text {old_text!r} {reason} in flyer details")
            else:
                candidate_raw = project.raw_request or ""
                replaced_raw, raw_reason = _replace_once_or_flag(candidate_raw, old_text, new_text)
                if raw_reason:
                    if raw_reason == "not found" and revision_text_value_contains(candidate_raw, old_text):
                        collapsed_raw, collapsed_raw_reason = _replace_once_whitespace_collapsed_or_flag(candidate_raw, old_text, new_text)
                        if not collapsed_raw_reason:
                            if price_delta_cents:
                                collapsed_raw, delta_reason = _apply_price_delta_near_text(collapsed_raw, new_text, price_delta_cents)
                                if delta_reason:
                                    unresolved.append(f"price delta {delta_reason}")
                                else:
                                    raw_request_update = collapsed_raw
                                    fuzzy_confirmation_required = True
                            else:
                                raw_request_update = collapsed_raw
                                fuzzy_confirmation_required = True
                        else:
                            unresolved.append(f"text {old_text!r} {collapsed_raw_reason} in raw request")
                        raw_reason = ""
                    if not raw_reason:
                        pass
                    elif raw_reason == "already applied":
                        pass
                    else:
                        fuzzy_notes, fuzzy_reason = _replace_once_normalized_or_flag(candidate_notes, old_text, new_text)
                        if not fuzzy_reason:
                            if price_delta_cents:
                                fuzzy_notes, delta_reason = _apply_price_delta_near_text(fuzzy_notes, new_text, price_delta_cents)
                                if delta_reason:
                                    unresolved.append(f"price delta {delta_reason}")
                                else:
                                    notes_update = fuzzy_notes
                                    fuzzy_confirmation_required = True
                            else:
                                notes_update = fuzzy_notes
                                fuzzy_confirmation_required = True
                        else:
                            fuzzy_raw, fuzzy_raw_reason = _replace_once_normalized_or_flag(candidate_raw, old_text, new_text)
                            if not fuzzy_raw_reason:
                                if price_delta_cents:
                                    fuzzy_raw, delta_reason = _apply_price_delta_near_text(fuzzy_raw, new_text, price_delta_cents)
                                    if delta_reason:
                                        unresolved.append(f"price delta {delta_reason}")
                                    else:
                                        raw_request_update = fuzzy_raw
                                        fuzzy_confirmation_required = True
                                else:
                                    raw_request_update = fuzzy_raw
                                    fuzzy_confirmation_required = True
                            else:
                                unresolved.append(
                                    f"text {old_text!r} {raw_reason if raw_reason != 'not found' else 'not found in flyer details'}"
                                )
                else:
                    if price_delta_cents:
                        replaced_raw, delta_reason = _apply_price_delta_near_text(replaced_raw, new_text, price_delta_cents)
                        if delta_reason:
                            unresolved.append(f"price delta {delta_reason}")
                        else:
                            raw_request_update = replaced_raw
                    else:
                        raw_request_update = replaced_raw
        else:
            if price_delta_cents:
                replaced_notes, delta_reason = _apply_price_delta_near_text(replaced_notes, new_text, price_delta_cents)
                if delta_reason:
                    unresolved.append(f"price delta {delta_reason}")
                else:
                    notes_update = replaced_notes
            else:
                notes_update = replaced_notes
        if (notes_update is not None or notes_already_applied) and raw_request_update is None and not unresolved:
            candidate_raw = project.raw_request or ""
            if revision_text_value_contains(candidate_raw, old_text):
                replaced_raw, raw_reason = apply_revision_text_edit_to_value(candidate_raw, old_text, new_text, price_delta_cents)
                if raw_reason == "already applied":
                    pass
                elif raw_reason and revision_text_value_contains(replaced_raw, old_text):
                    unresolved.append(f"text {old_text!r} {raw_reason} in raw request")
                else:
                    raw_request_update = replaced_raw
        # If we couldn't match the exact text anywhere, fall back to an explicit
        # instruction append. This handles template-origin visible text that
        # doesn't exist in `notes`/`raw_request` yet.
        if (
            notes_update is None
            and raw_request_update is None
            and unresolved
            and not price_delta_matches
            and all("not found" in reason for reason in unresolved)
        ):
            replace_instruction = f"Replace visible text {old_text!r} with {new_text!r} on the flyer. Do not keep {old_text!r} anywhere in the artwork."
            notes_update, raw_request_update = _append_instruction(notes_update, raw_request_update, project, replace_instruction)
            # Require confirmation because this is not a precise field edit.
            fuzzy_confirmation_required = True
            unresolved.clear()
        if (notes_update is not None or raw_request_update is not None) and not unresolved:
            replace_old_text = old_text
            replace_new_text = new_text
            replace_price_delta_cents = price_delta_cents
        elif notes_already_applied and not unresolved:
            replace_old_text = old_text
            replace_new_text = new_text
            replace_price_delta_cents = price_delta_cents
            already_applied = True

    changed = bool(updates) or notes_update is not None or raw_request_update is not None
    visual_only = _is_visual_only_revision(body)
    ambiguous = bool(unresolved)
    requires_confirmation = bool(fuzzy_confirmation_required)
    unresolved_clean = list(unresolved)
    confirmation_reason = ""
    if requires_confirmation:
        confirmation_reason = f"replace text {old_text!r} -> {new_text!r}"
    return RevisionPatchResult(
        field_updates=updates,
        notes_update=notes_update,
        raw_request_update=raw_request_update,
        changed=changed,
        visual_only=visual_only,
        ambiguous=ambiguous,
        unresolved_reason="; ".join(unresolved_clean),
        requires_confirmation=requires_confirmation,
        confirmation_reason=confirmation_reason,
        replace_old_text=replace_old_text,
        replace_new_text=replace_new_text,
        price_delta_cents=replace_price_delta_cents,
        already_applied=already_applied,
    )


def extract_revision_field_updates(project: FlyerProject, text: str) -> dict[str, str]:
    return extract_revision_patch(project, text).field_updates
