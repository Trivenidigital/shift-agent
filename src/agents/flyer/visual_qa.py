"""Visual/OCR QA gate for Flyer Studio generated artifacts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request

from schemas import FlyerProject, FlyerVisualQAReport
try:
    from flyer_facts import requests_generated_item_suggestions  # type: ignore
except ImportError:
    from agents.flyer.facts import requests_generated_item_suggestions
try:
    from flyer_semantic_brief import semantic_visibility_policy, visible_wrong_brand_blockers  # type: ignore
except ImportError:
    from agents.flyer.semantic_brief import semantic_visibility_policy, visible_wrong_brand_blockers


# Bracketed slot leakage ([price], [phone], …) + lorem ipsum + common template-
# editor placeholder text that leaks through generator/templates and would be
# invisible to OCR-vs-locked-fact substring matching (the template text isn't a
# customer fact, so no `missing required fact` blocker would fire). Operator
# triage on production seeing any of these means we shipped a generic template
# to a customer — fail-closed.
PLACEHOLDER_RE = re.compile(
    r"\[(?:price|phone|date|time|address|item|text|business[_ ]?name|tagline|headline|logo)[^\]]*\]"
    r"|lorem ipsum"
    r"|\byour\s+(?:logo|business\s+name|brand|text|tagline|headline|address|phone|contact|number|company\s+name)\s+here\b"
    r"|\bclick\s+(?:here\s+)?to\s+(?:add|edit|insert)\b"
    r"|\b(?:add|insert)\s+your\s+(?:logo|text|business\s+name|brand|headline|tagline)\b"
    r"|\b(?:tap|press)\s+to\s+edit\b"
    r"|\bsample\s+text\b",
    re.IGNORECASE,
)
RAW_REQUEST_INSTRUCTION_RE = re.compile(
    r"\b(?:create|make|generate|design)\s+(?:a\s+)?(?:flyer|flier|poster|banner)\b"
    r"|\bitems?\s+to\s+include\b"
    r"|\b(?:customer\s+request(?:ed)?|user\s+request(?:ed)?)\b"
    r"|\brequested\s+edits?\s*:"
    r"|\buse\s+(?:saved|stored|registered|account)?\s*(?:address|phone|contact|logo|business\s+name)\b"
    r"|\b(?:saved|stored|registered|account)\s+(?:address|phone|contact|logo|business\s+name)\b"
    r"|\b(?:extract|take)\s+(?:item|items|prices?)\b"
    r"|\battaching\s+(?:a\s+)?(?:flyer|flier|poster|banner)\b",
    re.IGNORECASE,
)
INTERNAL_BRAND_ASSET_ID_RE = re.compile(r"\bB\d{4}\b", re.IGNORECASE)


_PHONE_DIGITS_RE = re.compile(r"\D+")
# Localized run of digit-bearing characters (digits + common phone separators).
# Anchors the digit-only comparison to a contiguous visual phone block so a
# stray "17" elsewhere in the OCR doesn't glue onto the locked phone's digits.
_PHONE_RUN_RE = re.compile(r"[\d\s\-().+/]{8,}")
_PRICE_AMOUNT_RE = re.compile(r"(?<![a-z0-9.])(?P<currency>[$₹])?\s*(?P<amount>\d+(?:[.,]\d{1,2})?)(?![a-z0-9.]|[.,]\d)")
_UNIT_OR_QUANTITY_TOKENS = {
    "lb",
    "lbs",
    "pound",
    "pounds",
    "oz",
    "ounce",
    "ounces",
    "kg",
    "g",
    "gram",
    "grams",
    "ml",
    "l",
    "liter",
    "litre",
    "pack",
    "pc",
    "pcs",
    "piece",
    "pieces",
    "dozen",
    "half",
    "full",
    "tray",
    "trays",
    "count",
    "counts",
    "small",
    "medium",
    "large",
}
REGIONAL_SCRIPT_RE = re.compile(r"[\u0900-\u097F\u0A00-\u0A7F\u0A80-\u0AFF\u0B80-\u0BFF\u0C00-\u0C7F\u0C80-\u0CFF\u0D00-\u0D7F]")
OPERATIONAL_CLAIM_PATTERNS = (
    ("delivery", re.compile(r"\b(?:whats\s*app|whatsapp)?\s*delivery\b|\bwe\s+deliver\b", re.IGNORECASE)),
    ("catering", re.compile(r"\bcatering\s+(?:available|orders?|service)\b|\bwe\s+cater\b", re.IGNORECASE)),
    ("payment", re.compile(r"\b(?:cash\s*app|zelle|venmo|paypal|online\s+payment|payment\s+accepted)\b", re.IGNORECASE)),
)


def _normalize_text_for_match(text: str) -> str:
    """Casefold + collapse whitespace + strip common typographic apostrophes."""
    lowered = re.sub(r"\s+", " ", text).casefold()
    for ch in ("‘", "’", "ʼ", "`", "'"):
        lowered = lowered.replace(ch, "")
    return lowered


def _looks_like_phone(value: str) -> bool:
    # Raised lower bound from 7 → 10 digits so short SKUs / order numbers can't
    # be treated as phones (the digits-only path is too permissive for 7-digit
    # values that incidentally collide).
    digits = _PHONE_DIGITS_RE.sub("", value)
    return 10 <= len(digits) <= 15


def _locked_fact_uses_phone_match(*, fact_id: str, label: str, value: str) -> bool:
    if not _looks_like_phone(value):
        return False
    context = f"{fact_id} {label}".casefold()
    return any(token in context for token in ("phone", "contact", "whatsapp", "mobile", "tel"))


def _phone_value_present_in(text: str, fact_value: str) -> bool:
    """Phone presence: locked digits must appear inside a contiguous OCR
    digit-bearing run (digits + spaces/hyphens/parens/dots/plus). Prevents
    cross-region globbing where 'Order 17' + 'price 32-98-37841' get
    concatenated into a false-positive '17329837841'.
    """
    value_digits = _PHONE_DIGITS_RE.sub("", fact_value)
    for run in _PHONE_RUN_RE.findall(text):
        run_digits = _PHONE_DIGITS_RE.sub("", run)
        if value_digits in run_digits:
            return True
    return False


# A single phone-number candidate, captured in two named groups:
#   cc       — ONLY an explicit "+" country code ("+" + 1-4 digits), optionally split from
#              the national digits by up to 6 gap chars (incl. newline / "(") so a country
#              code on its own visual line still attaches (Codex HIGH-3 gap/newline/paren).
#              A preceding bare number (ZIP/price) has no "+" so it is NOT read as a country
#              code (Codex round-4 false positive) — at worst it is absorbed into `national`,
#              whose LAST 10 digits remain the real phone.
#   national — a 10-15 digit number on ONE line (internal separators are space/tab/().-, NOT
#              newline) so a ZIP/price on the line above does not glob into the phone.
# "+digit" is adjacent (no space) so a math/promo "+ 2 deals" is not read as a phone; the
# national excludes "/" so two numbers joined by " / "/" or " stay SEPARATE (Codex HIGH-1).
_PHONE_CANDIDATE_RE = re.compile(
    r"(?:\+(?P<cc>\d{1,4})[\s.\-(]{0,6})?(?P<national>\d(?:[ \t().\-]{0,2}\d){9,14})"
)


def _phone_parts(value: str) -> tuple[str, str]:
    """(country_code, national) — national is the last 10 digits; country_code is
    whatever precedes them ("" for an exactly-10-digit number). The national-number scan
    compares `national` (so a suffix-corrupted ...78410 differs from the locked number,
    Codex HIGH-2) and the country-code scan compares `country_code` (so +91 vs the
    registered +1 is caught, Codex HIGH-3)."""
    digits = _PHONE_DIGITS_RE.sub("", value)
    return (digits[:-10], digits[-10:]) if len(digits) > 10 else ("", digits)


def _phone_cc_compatible(candidate_cc: str, locked_cc: str) -> bool:
    """A candidate's country code is compatible with the registered phone's when it is
    absent, identical, or both are NANP-domestic — a bare 10-digit number and a +1
    number are the same line, but +91/+44/etc. on a +1 customer's flyer is wrong."""
    if candidate_cc in ("", locked_cc):
        return True
    return {candidate_cc, locked_cc} <= {"", "1"}


def _max_consecutive_digits(value: str) -> int:
    """Longest run of consecutive digits — a real phone groups digits 3-4 at a time,
    while price columns ('12.99 8.99 5.49') and decimals top out at 1-2 (Codex MEDIUM)."""
    return max((len(group) for group in re.findall(r"\d+", value)), default=0)


def _unexpected_phone_blockers(project: FlyerProject, extracted_text: str) -> list[str]:
    """P1-1: flag any phone-shaped number in the OCR that is NOT the customer's
    registered phone.

    The positive fact loop only checks each locked phone is PRESENT — it is blind to an
    EXTRA / corrupted / hallucinated / duplicated phone rendered alongside the correct
    one. A flyer that shows the customer's real phone AND a wrong one is a wrong-fact
    ship; fail closed -> manual review.

    A candidate is "explained" (not flagged) only when its NATIONAL number (last 10
    digits of the `national` group) matches a locked phone AND its explicit "+" country
    code is compatible (`_phone_cc_compatible`: absent, identical, or both NANP-domestic).
    So a different number, a suffix/middle corruption (different national), and a wrong
    "+" country code (+91 on a +1 customer) all fail closed, while +1 / bare / formatting
    variants of the registered phone pass — and a ZIP/price adjacent to the phone is
    absorbed into `national` (last 10 still the real phone) rather than mistaken for a
    country code. Guards: runs only with a locked phone; a candidate whose longest digit
    group is < 3 is treated as decimals/prices.
    """
    locked = [
        _phone_parts(fact.value)
        for fact in project.locked_facts
        if _locked_fact_uses_phone_match(fact_id=fact.fact_id, label=fact.label, value=fact.value)
    ]
    locked = [(cc, national) for cc, national in locked if national]
    if not locked:
        return []
    blockers: list[str] = []
    seen: set[str] = set()
    for match in _PHONE_CANDIDATE_RE.finditer(extracted_text or ""):
        national_part = match.group("national")
        national_digits = _PHONE_DIGITS_RE.sub("", national_part)
        if not (10 <= len(national_digits) <= 15) or _max_consecutive_digits(national_part) < 3:
            continue
        cand_national = national_digits[-10:]
        cand_cc = match.group("cc") or ""
        explained = any(
            cand_national == national and _phone_cc_compatible(cand_cc, cc)
            for cc, national in locked
        )
        if explained or cand_national in seen:
            continue
        seen.add(cand_national)
        blockers.append(f"unverified phone number visible: {match.group(0).strip()}")
    return blockers


def _past_event_date_blockers(project: FlyerProject) -> list[str]:
    """P1-2: a flyer must not advertise an event date that has already passed. The locked
    event_date drives the rendered date; if it is before the flyer's creation date the
    event is stale (a flyer advertises a future or same-day event), so fail closed ->
    manual review. Compared against created_at (a stable, fast-flow proxy for "now");
    a flyer that goes stale while queued for weeks is the recovery watchdog's concern."""
    event_date = (project.fields.event_date or "").strip()
    if not event_date:
        return []
    try:
        event = datetime.strptime(event_date, "%Y-%m-%d").date()
    except ValueError:
        return []  # malformed dates are not this gate's concern
    if event < project.created_at.date():
        return [f"event date is in the past: {event_date}"]
    return []


_TEXT_DEFECT_NOTE_RE = re.compile(r"duplicat|misspell|mispell|repeated", re.IGNORECASE)
_NEGATED_TEXT_DEFECT_NOTE_RE = re.compile(
    r"\b(?:no|none|not|without)\b.{0,40}\b(?:duplicat|misspell|mispell|repeated|garbled|unreadable|placeholder)",
    re.IGNORECASE,
)


def _text_defect_note_blockers(provider_notes: list[str]) -> list[str]:
    """P1-4: a vision-QA note reporting DUPLICATED or MISSPELLED visible text is a
    looks-broken defect (e.g. a brand header baked into the generated background AND drawn
    again by the overlay, or a 'THURRSDAY'-style typo). Map it to a block-tier blocker ->
    manual review. Distinct from the existing garbled/placeholder notes, which stay warn-
    tier; over-reporting here only costs a manual review, never ships a worse flyer."""
    return [
        f"visible text defect reported by QA: {note}"
        for note in provider_notes
        if _TEXT_DEFECT_NOTE_RE.search(note) and not _NEGATED_TEXT_DEFECT_NOTE_RE.search(note)
    ]


def _price_cents(value: str) -> int | None:
    match = re.search(r"\d+(?:[.,]\d{1,2})?", value or "")
    if not match:
        return None
    raw = match.group(0).replace(",", ".")
    whole, dot, cents = raw.partition(".")
    cents = (cents + "00")[:2] if dot else "00"
    try:
        return int(whole) * 100 + int(cents)
    except ValueError:
        return None


def _price_value_present_in(text: str, fact_value: str) -> bool:
    expected = _price_cents(fact_value)
    if expected is None:
        return False
    requires_currency = bool(re.search(r"[$₹]", fact_value or ""))
    for match in _PRICE_AMOUNT_RE.finditer(text or ""):
        if requires_currency and not match.group("currency"):
            continue
        if _price_cents(match.group("amount")) == expected:
            return True
    return False


def _first_price_cents(text: str, *, requires_currency: bool) -> int | None:
    for match in _PRICE_AMOUNT_RE.finditer(text or ""):
        if requires_currency and not match.group("currency"):
            continue
        return _price_cents(match.group("amount"))
    return None


def _first_price_match(text: str, *, requires_currency: bool) -> re.Match[str] | None:
    for match in _PRICE_AMOUNT_RE.finditer(text or ""):
        if requires_currency and not match.group("currency"):
            continue
        return match
    return None


def _text_value_present_in(normalized_text: str, normalized_value: str) -> bool:
    """Word-boundary-aware presence: locked 'Idly' must NOT match 'Idlysugar',
    locked 'Acme' must NOT match 'Acme Building Services'. Anchors with `\\b`
    only on sides where the value itself starts/ends with a word char, so
    values like '$13.99' (starts non-word) still match.
    """
    if not normalized_value:
        return False
    left = r"\b" if normalized_value[:1].isalnum() else ""
    right = r"\b" if normalized_value[-1:].isalnum() else ""
    pattern = left + re.escape(normalized_value) + right
    return re.search(pattern, normalized_text) is not None


_ADDRESS_TOKEN_ALIASES = {
    "saint": "st",
    "street": "st",
    "st.": "st",
    "drive": "dr",
    "dr.": "dr",
    "road": "rd",
    "rd.": "rd",
    "avenue": "ave",
    "ave.": "ave",
    "boulevard": "blvd",
    "blvd.": "blvd",
    "place": "pl",
    "pl.": "pl",
    "florida": "fl",
    "virginia": "va",
}


def _normalize_address_for_match(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", _normalize_text_for_match(value))
    return " ".join(_ADDRESS_TOKEN_ALIASES.get(token, token) for token in tokens)


def _address_value_present_in(normalized_text: str, fact_value: str) -> bool:
    normalized_address_text = _normalize_address_for_match(normalized_text)
    normalized_address_value = _normalize_address_for_match(fact_value)
    if not normalized_address_value:
        return False
    return _text_value_present_in(normalized_address_text, normalized_address_value)


def _schedule_value_present_in(normalized_text: str, fact_value: str) -> bool:
    normalized_schedule_text = _normalize_text_for_match(normalized_text)
    normalized_schedule_value = _normalize_text_for_match(fact_value)
    if _text_value_present_in(normalized_schedule_text, normalized_schedule_value):
        return True
    match = re.fullmatch(
        r"(?P<day>monday|tuesday|wednesday|thursday|friday|saturday|sunday) every week",
        normalized_schedule_value,
    )
    if match:
        every_day = f"every {match.group('day')}"
        if _text_value_present_in(normalized_schedule_text, every_day):
            return True
    two_day_match = re.fullmatch(
        r"(?P<first>monday|tuesday|wednesday|thursday|friday|saturday|sunday) and "
        r"(?P<second>monday|tuesday|wednesday|thursday|friday|saturday|sunday) every week",
        normalized_schedule_value,
    )
    if two_day_match:
        every_days = f"every {two_day_match.group('first')} and {two_day_match.group('second')}"
        if _text_value_present_in(normalized_schedule_text, every_days):
            return True
    return False


def _value_present_in(
    normalized_text: str,
    fact_value: str,
    *,
    phone_match: bool = False,
    address_match: bool = False,
    schedule_match: bool = False,
    price_match: bool = False,
) -> bool:
    """Smart presence check for a locked-fact value in the OCR'd text.

    Phones: digits-only within a contiguous OCR digit-run (see
    `_phone_value_present_in`).

    Addresses: tokenized matching with common OCR/address aliases (`Saint` vs
    `St`, punctuation/newline differences).

    Other text: apostrophe-strip + whitespace-collapse + casefold + word-
    boundary (see `_text_value_present_in`) so locked "Lakshmi's Kitchen"
    matches "Lakshmis Kitchen" but locked "Idly" does NOT match "Idlysugar".
    """
    if phone_match:
        return _phone_value_present_in(normalized_text, fact_value)
    if address_match:
        return _address_value_present_in(normalized_text, fact_value)
    if schedule_match:
        return _schedule_value_present_in(normalized_text, fact_value)
    if price_match:
        return _price_value_present_in(normalized_text, fact_value)
    normalized_value = _normalize_text_for_match(fact_value)
    return _text_value_present_in(normalized_text, normalized_value)


def _tokens_present(normalized_text: str, value: str) -> bool:
    stopwords = {"with", "any", "the", "a", "an"}
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", _normalize_text_for_match(value))
        if token and token not in stopwords
    ]
    return bool(tokens) and all(_text_value_present_in(normalized_text, token) for token in tokens)


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(1 for a, b in zip(left, right) if a != b) <= 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    i = j = edits = 0
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        j += 1
    return True


def _item_token_present_in_line(expected: str, actual_tokens: list[str]) -> bool:
    if expected in actual_tokens:
        return True
    if len(expected) < 6:
        return False
    return any(
        len(actual) >= 6 and _edit_distance_at_most_one(expected, actual)
        for actual in actual_tokens
    )


def _item_token_matches(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    return len(expected) >= 6 and len(actual) >= 6 and _edit_distance_at_most_one(expected, actual)


def _fuzzy_item_span_in_line(normalized_line: str, expected_tokens: list[str]) -> tuple[int, int] | None:
    token_spans = [
        (match.group(0), match.start(), match.end())
        for match in re.finditer(r"[a-z0-9]+", normalized_line)
    ]
    if not token_spans:
        return None
    first_start: int | None = None
    previous_end: int | None = None
    search_start = 0
    for expected in expected_tokens:
        found: tuple[int, int, int] | None = None
        for token_index in range(search_start, len(token_spans)):
            actual, start, end = token_spans[token_index]
            if _item_token_matches(expected, actual):
                found = (token_index, start, end)
                break
        if found is None:
            return None
        token_index, start, end = found
        if previous_end is not None and not _only_unit_or_quantity_text(normalized_line[previous_end:start]):
            return None
        if first_start is None:
            first_start = start
        previous_end = end
        search_start = token_index + 1
    if first_start is None or previous_end is None:
        return None
    return first_start, previous_end


def _item_name_present(raw_text: str, value: str) -> bool:
    item_descriptor_tokens = {"item", "items", "special", "specials", "daily"}
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", _normalize_text_for_match(value))
        if token and token not in item_descriptor_tokens
    ]
    if not tokens:
        return False
    negative_re = re.compile(r"\b(?:no|not|without|unavailable|sold\s+out)\b", re.IGNORECASE)
    menu_context_re = re.compile(
        r"\b(?:thali|specials?|menu|combo|plate|meal|dish|item|items|sides?|desserts?)\b",
        re.IGNORECASE,
    )
    for line in (raw_text or "").splitlines():
        normalized_line = _normalize_text_for_match(line)
        exact_tokens_present = all(_text_value_present_in(normalized_line, token) for token in tokens)
        line_tokens = re.findall(r"[a-z0-9]+", normalized_line)
        fuzzy_tokens_present = all(_item_token_present_in_line(token, line_tokens) for token in tokens)
        if not exact_tokens_present and not fuzzy_tokens_present:
            continue
        if negative_re.search(line):
            continue
        if _first_price_match(line, requires_currency=False) is not None:
            return True
        if menu_context_re.search(line):
            return True
        # Accept short masthead/card labels such as "GOAT" but not prose
        # notes like "ask about goat catering options".
        if len(re.findall(r"[A-Za-z][A-Za-z'&.-]*", line)) <= 3:
            return True
    if _item_core_name_with_price_present(raw_text, value):
        return True
    if _item_name_present_in_rowwise_layout(raw_text, value):
        return True
    return False


def _name_pattern(value: str) -> re.Pattern[str] | None:
    tokens = re.findall(r"[a-z0-9]+", _normalize_text_for_match(value))
    if not tokens:
        return None
    return re.compile(r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(re.escape(token) for token in tokens) + r"(?![a-z0-9])", re.IGNORECASE)


def _only_unit_or_quantity_text(text: str) -> bool:
    normalized = _normalize_text_for_match(text)
    tokens = re.findall(r"[a-z0-9]+", normalized)
    if not tokens:
        return True
    has_unit = any(token in _UNIT_OR_QUANTITY_TOKENS for token in tokens)
    return has_unit and all(token.isdigit() or token in _UNIT_OR_QUANTITY_TOKENS for token in tokens)


def _no_word_tokens_text(text: str) -> bool:
    return not re.findall(r"[a-z0-9]+", _normalize_text_for_match(text))


def _item_core_tokens(value: str) -> list[str]:
    core, _quantity = _item_core_and_quantity_tokens(value)
    return core


def _item_core_and_quantity_tokens(value: str) -> tuple[list[str], list[str]]:
    tokens = re.findall(r"[a-z0-9]+", _normalize_text_for_match(value))
    if len(tokens) >= 2 and tokens[-1] in _UNIT_OR_QUANTITY_TOKENS and tokens[-2].isdigit():
        return tokens[:-2], tokens[-2:]
    if (
        len(tokens) >= 2
        and tokens[-1] in {"tray", "trays"}
        and tokens[-2] in {"small", "medium", "large", "half", "full"}
    ):
        return tokens[:-2], tokens[-2:]
    if tokens and tokens[-1] in {"small", "medium", "large", "half", "full"}:
        return tokens[:-1], tokens[-1:]
    return tokens, []


def _item_core_name_with_price_present(raw_text: str, value: str) -> bool:
    core_tokens, quantity_tokens = _item_core_and_quantity_tokens(value)
    if not core_tokens or not quantity_tokens:
        return False
    for line in (raw_text or "").splitlines():
        normalized_line = _normalize_text_for_match(line)
        span = _fuzzy_item_span_in_line(normalized_line, core_tokens)
        if span is None:
            continue
        _start, end = span
        tail = normalized_line[end:]
        price_match = _first_price_match(tail, requires_currency=False)
        if price_match is not None and _no_word_tokens_text(tail[: price_match.start()]):
            return True
    return False


def _tokens_pattern(tokens: list[str]) -> re.Pattern[str] | None:
    if not tokens:
        return None
    return re.compile(r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(re.escape(token) for token in tokens) + r"(?![a-z0-9])", re.IGNORECASE)


def _item_name_present_in_rowwise_layout(raw_text: str, value: str) -> bool:
    core_pattern = _tokens_pattern(_item_core_tokens(value))
    if core_pattern is None:
        return False
    lines = [line for line in (raw_text or "").splitlines() if line.strip()]
    for line_index, line in enumerate(lines):
        if not core_pattern.search(line):
            continue
        for adjacent_index in (line_index + 1, line_index + 2):
            if adjacent_index < len(lines) and _only_unit_or_quantity_text(lines[adjacent_index]):
                return True
    return False


def _stacked_item_segment(lines: list[str], start_index: int, name_re: re.Pattern[str], other_name_patterns: list[re.Pattern[str]]) -> str:
    max_window = 5
    end_index = min(len(lines), start_index + max_window)
    for candidate_index in range(start_index + 1, end_index):
        for other_re in other_name_patterns:
            other_window = "\n".join(lines[candidate_index : min(len(lines), candidate_index + max_window)])
            other_match = other_re.search(other_window)
            if other_match and other_match.start() <= len(lines[candidate_index]):
                end_index = candidate_index
                break
        if end_index == candidate_index:
            break
    segment = "\n".join(lines[start_index:end_index])
    if name_re.search(segment):
        return segment
    return ""


def _stacked_item_price_present(
    segment: str,
    name_re: re.Pattern[str],
    price: str,
    *,
    other_core_patterns: list[re.Pattern[str]],
) -> bool:
    match = name_re.search(segment)
    if not match:
        return False
    if any(other_re.search(segment[: match.start()]) for other_re in other_core_patterns):
        return False
    expected = _price_cents(price)
    if expected is None:
        return False
    tail = segment[match.end() :]
    price_match = _first_price_match(tail, requires_currency=bool(re.search(r"[$₹]", price or "")))
    if price_match is None:
        return False
    if not _only_unit_or_quantity_text(tail[: price_match.start()]):
        return False
    return _price_cents(price_match.group("amount")) == expected


def _adjacent_line_price_belongs_to_item(line: str, price: str) -> bool:
    price_match = _first_price_match(line, requires_currency=bool(re.search(r"[$₹]", price or "")))
    if price_match is None:
        return False
    if not _only_unit_or_quantity_text(line[: price_match.start()]):
        return False
    return _price_cents(price_match.group("amount")) == _price_cents(price)


def _same_line_item_price_present(segment: str, name_re: re.Pattern[str], price: str) -> bool:
    name_match = name_re.search(segment)
    if name_match is None:
        return False
    requires_currency = bool(re.search(r"[$₹]", price or ""))
    expected = _price_cents(price)
    previous_price: re.Match[str] | None = None
    for price_match in _PRICE_AMOUNT_RE.finditer(segment[: name_match.start()]):
        if requires_currency and not price_match.group("currency"):
            continue
        previous_price = price_match
    if previous_price is not None:
        between = segment[previous_price.end() : name_match.start()]
        if _only_unit_or_quantity_text(between) and _price_cents(previous_price.group("amount")) == expected:
            return True
    next_price = _first_price_match(segment[name_match.end() :], requires_currency=requires_currency)
    if next_price is None:
        return False
    between = segment[name_match.end() : name_match.end() + next_price.start()]
    if not _only_unit_or_quantity_text(between):
        return False
    return _price_cents(next_price.group("amount")) == expected


def _same_line_tokens_price_match_indices(
    raw_text: str,
    tokens: list[str],
    price: str,
    *,
    allow_unit_text_between: bool,
) -> set[int]:
    expected = _price_cents(price)
    if expected is None:
        return set()
    if not tokens:
        return set()
    requires_currency = bool(re.search(r"[$â‚¹]", price or ""))
    matches: set[int] = set()
    for line_index, line in enumerate((raw_text or "").splitlines()):
        normalized_line = _normalize_text_for_match(line)
        span = _fuzzy_item_span_in_line(normalized_line, tokens)
        if span is None:
            continue
        _start, end = span
        tail = normalized_line[end:]
        price_match = _first_price_match(tail, requires_currency=requires_currency)
        if price_match is None:
            continue
        between = tail[: price_match.start()]
        if allow_unit_text_between:
            between_ok = _only_unit_or_quantity_text(between)
        else:
            between_ok = _no_word_tokens_text(between)
        if not between_ok:
            continue
        if _price_cents(price_match.group("amount")) == expected:
            matches.add(line_index)
    return matches


def _same_line_fuzzy_item_price_match_count(raw_text: str, name: str, price: str) -> int:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", _normalize_text_for_match(name))
        if token
    ]
    matches = _same_line_tokens_price_match_indices(raw_text, tokens, price, allow_unit_text_between=True)
    core_tokens, quantity_tokens = _item_core_and_quantity_tokens(name)
    if quantity_tokens and core_tokens != tokens:
        matches.update(_same_line_tokens_price_match_indices(
            raw_text,
            core_tokens,
            price,
            allow_unit_text_between=False,
        ))
    return len(matches)


def _same_line_fuzzy_item_price_present(raw_text: str, name: str, price: str) -> bool:
    return _same_line_fuzzy_item_price_match_count(raw_text, name, price) > 0


def _item_price_pair_present(raw_text: str, name: str, price: str, *, all_item_names: list[str]) -> bool:
    name_re = _name_pattern(name)
    if name_re is None:
        return False
    other_name_patterns = [
        pattern
        for other in all_item_names
        if _normalize_text_for_match(other) != _normalize_text_for_match(name)
        for pattern in [_name_pattern(other)]
        if pattern is not None
    ]
    other_core_patterns = [
        pattern
        for other in all_item_names
        if _normalize_text_for_match(other) != _normalize_text_for_match(name)
        for pattern in [_tokens_pattern(_item_core_tokens(other))]
        if pattern is not None
    ]
    lines = [line for line in (raw_text or "").splitlines() if line.strip()]
    for line_index, line in enumerate(lines):
        for match in name_re.finditer(line):
            previous_start = 0
            end = len(line)
            for other_re in other_name_patterns:
                previous_match = None
                for candidate in other_re.finditer(line[:match.start()]):
                    previous_match = candidate
                if previous_match:
                    previous_start = max(previous_start, previous_match.end())
                other_match = other_re.search(line, match.end())
                if other_match:
                    end = min(end, other_match.start())
            same_item_segment = f"{line[previous_start:match.start()]} {line[match.start():end]}"
            if _same_line_item_price_present(same_item_segment, name_re, price):
                return True
            candidate_segments = []
            for adjacent_index in (line_index - 1, line_index + 1):
                if 0 <= adjacent_index < len(lines):
                    adjacent = lines[adjacent_index]
                    if not any(other_re.search(adjacent) for other_re in other_name_patterns) and _adjacent_line_price_belongs_to_item(adjacent, price):
                        candidate_segments.append(adjacent)
            if any(_price_value_present_in(segment, price) for segment in candidate_segments):
                return True
    for line_index in range(len(lines)):
        if any(other_re.search(lines[line_index]) for other_re in other_core_patterns):
            continue
        segment = _stacked_item_segment(lines, line_index, name_re, other_name_patterns)
        if segment and _stacked_item_price_present(segment, name_re, price, other_core_patterns=other_core_patterns):
            return True
    return False


def _rowwise_item_price_pair_present(raw_text: str, records: dict[int, dict[str, str]], target_index: int) -> bool:
    sorted_records = [(index, record) for index, record in sorted(records.items()) if record.get("name") and record.get("price")]
    if len(sorted_records) < 2:
        return False
    lines = [line for line in (raw_text or "").splitlines() if line.strip()]
    for line_index in range(0, max(0, len(lines) - 2)):
        name_line = lines[line_index]
        qty_line = lines[line_index + 1]
        price_line = lines[line_index + 2]
        core_groups: list[list[str]] = []
        quantity_groups: list[list[str]] = []
        for _index, record in sorted_records:
            core, quantity = _item_core_and_quantity_tokens(record["name"])
            pattern = _tokens_pattern(core)
            if pattern is None:
                break
            match = pattern.search(name_line)
            if match is None:
                break
            core_groups.append(core)
            quantity_groups.append(quantity)
        else:
            expected_name_tokens = [token for group in core_groups for token in group]
            if re.findall(r"[a-z0-9]+", _normalize_text_for_match(name_line)) != expected_name_tokens:
                continue
            expected_quantity_tokens = [token for group in quantity_groups for token in group]
            actual_quantity_tokens = re.findall(r"[a-z0-9]+", _normalize_text_for_match(qty_line))
            if actual_quantity_tokens != expected_quantity_tokens:
                continue
            price_matches = [match for match in _PRICE_AMOUNT_RE.finditer(price_line) if match.group("currency")]
            if len(price_matches) < len(sorted_records):
                continue
            for ordinal, (index, record) in enumerate(sorted_records):
                if index != target_index:
                    continue
                return _price_cents(price_matches[ordinal].group("amount")) == _price_cents(record["price"])
    return False


def _item_price_pair_blockers(project: FlyerProject, raw_text: str) -> list[str]:
    item_re = re.compile(r"^item:(?P<index>\d+):(?P<kind>name|price)$")
    records: dict[int, dict[str, str]] = {}
    for fact in project.locked_facts:
        if not fact.required:
            continue
        match = item_re.match(fact.fact_id)
        if not match:
            continue
        index = int(match.group("index"))
        records.setdefault(index, {})[match.group("kind")] = str(fact.value or "")
    all_names = [record["name"] for _index, record in sorted(records.items()) if record.get("name")]
    blockers: list[str] = []
    for index, record in sorted(records.items()):
        name = record.get("name", "").strip()
        price = record.get("price", "").strip()
        if not name or not price:
            continue
        if (
            not _item_price_pair_present(raw_text, name, price, all_item_names=all_names)
            and not _rowwise_item_price_pair_present(raw_text, records, index)
            and not _same_line_fuzzy_item_price_present(raw_text, name, price)
        ):
            blockers.append(f"item price mismatch: item:{index} expected {name} {price}")
            continue
        if _same_line_fuzzy_item_price_match_count(raw_text, name, price) > 1:
            blockers.append(f"duplicate item price visible: item:{index} {name} {price}")
    return blockers


def _inferred_item_coverage_blockers(project: FlyerProject, raw_text: str) -> list[str]:
    """Intent-aware QA (bounded-creative-planner slice 3): every planner-inferred
    item (source='hermes_inferred') that the project committed to MUST be rendered.
    The planner promised these items; a draft that silently drops them does not
    satisfy the customer's request. This is coverage of committed inferred items;
    the requested-count + pricing-type reconciliation lands with the live planner
    wiring (slice 5). Inert today — no fact carries source='hermes_inferred' until
    the planner is enabled, so this contributes no blocker in the default state."""
    if not requests_generated_item_suggestions(
        " ".join(str(value or "") for value in (project.raw_request, project.fields.notes))
    ):
        return []
    item_re = re.compile(r"^item:(?P<index>\d+):name$")
    blockers: list[str] = []
    for fact in project.locked_facts:
        if getattr(fact, "source", "") != "hermes_inferred":
            continue
        if not item_re.match(fact.fact_id):
            continue
        value = str(fact.value or "").strip()
        if value and not _item_name_present(raw_text, value):
            blockers.append(f"inferred item not rendered: {value}")
    return blockers


_REQUESTED_ITEM_COUNT_RE = re.compile(
    r"\b(?P<count>\d{1,2})\s+(?:[A-Za-z][\w'-]*\s+){0,6}?items?\b",
    re.IGNORECASE,
)


def _requested_item_count(raw_request: str) -> int | None:
    """Slice 5b intent-QA: the explicit item count the customer asked for, e.g.
    "include 8 famous South Indian breakfast items" → 8. None when no count is stated.
    Requires the word "items" within a few words of the number so prices/times/dates
    (e.g. "$8.99", "8 AM") are not mistaken for an item count."""
    match = _REQUESTED_ITEM_COUNT_RE.search(raw_request or "")
    if not match:
        return None
    count = int(match.group("count"))
    return count if 1 <= count <= 30 else None


def _inferred_intent_count_blockers(project: FlyerProject) -> list[str]:
    """Intent-aware QA (slice 5b, design §7a count + §7b hard+creative reconciliation):
    when the planner contributed inferred items AND the customer asked for a specific
    count N, the project must commit to exactly N distinct item names — customer-named
    ("hard") items + planner-inferred items combined. Catches the planner/firewall
    producing fewer (or more) than requested. Fact-level + deterministic; that each
    committed item actually renders is covered by _inferred_item_coverage_blockers +
    the per-fact visibility checks.

    Inert in the dormant default: gated on >=1 hermes_inferred item, and no fact carries
    source='hermes_inferred' until the planner is enabled, so this adds no blocker."""
    if not requests_generated_item_suggestions(
        " ".join(str(value or "") for value in (project.raw_request, project.fields.notes))
    ):
        return []
    name_re = re.compile(r"^item:(?P<index>\d+):name$")
    inferred_names: set[str] = set()
    all_names: set[str] = set()
    for fact in project.locked_facts:
        if not name_re.match(fact.fact_id):
            continue
        value = str(fact.value or "").strip()
        if not value:
            continue
        all_names.add(value.casefold())
        if getattr(fact, "source", "") == "hermes_inferred":
            inferred_names.add(value.casefold())
    if not inferred_names:
        return []  # dormant / no planner contribution ⇒ no intent-count assertion
    requested = _requested_item_count(project.raw_request)
    if requested is None:
        return []
    if len(all_names) != requested:
        return [f"requested item count not satisfied: asked {requested}, have {len(all_names)}"]
    return []


def _campaign_title_present(normalized_text: str, value: str) -> bool:
    normalized_value = _normalize_text_for_match(value)
    if _text_value_present_in(normalized_text, normalized_value):
        return True
    tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_value) if token]
    if not tokens:
        return False
    if len(tokens) == 1:
        return _text_value_present_in(normalized_text, tokens[0])
    pattern = r"\b" + re.escape(tokens[0]) + r"\b"
    for token in tokens[1:]:
        pattern += r"(?:\s+[a-z0-9]+){0,1}\s+\b" + re.escape(token) + r"\b"
    return re.search(pattern, normalized_text) is not None


def _semantic_visible_fact_present(fact_id: str, label: str, value: str, normalized_text: str, raw_text: str) -> bool:
    context = f"{fact_id} {label}".casefold()
    normalized_value = _normalize_text_for_match(value)
    if fact_id.startswith("item:") and fact_id.endswith(":name"):
        return _item_name_present(raw_text, value)
    if fact_id.startswith("item:") and fact_id.endswith(":price"):
        return _price_value_present_in(raw_text, value)
    if fact_id in {"campaign_title", "headline"}:
        return _campaign_title_present(normalized_text, value)
    if fact_id == "pricing_structure" or "pricing" in context:
        digits = _PHONE_DIGITS_RE.sub("", value)
        if digits and digits not in _PHONE_DIGITS_RE.sub("", normalized_text):
            return False
        return _tokens_present(normalized_text, value)
    if fact_id.startswith("offer:") or "offer" in context:
        digits = _PHONE_DIGITS_RE.sub("", value)
        if digits and digits not in _PHONE_DIGITS_RE.sub("", normalized_text):
            return False
        return _tokens_present(normalized_text, value)
    if fact_id == "promotion_end" or "promotion end" in context:
        if not _tokens_present(normalized_text, value):
            return False
        value_tokens = r"\s+".join(re.escape(token) for token in normalized_value.split())
        return bool(re.search(r"\b(?:until|through|thru|expires?|valid|runs|promotion\s+end)\b.{0,40}" + value_tokens, normalized_text))
    return False


def _locked_fact_present_in_ocr(
    project: FlyerProject,
    fact_id: str,
    normalized_text: str,
    raw_text: str,
    *,
    source: str | None = None,
) -> bool:
    for fact in project.locked_facts:
        if fact.fact_id != fact_id or not str(fact.value or "").strip():
            continue
        if source is not None and getattr(fact, "source", "") != source:
            continue
        return _value_present_in(
            raw_text if _locked_fact_uses_phone_match(fact_id=fact.fact_id, label=fact.label, value=fact.value) else normalized_text,
            fact.value,
            phone_match=_locked_fact_uses_phone_match(
                fact_id=fact.fact_id,
                label=fact.label,
                value=fact.value,
            ),
            address_match=fact.fact_id == "location" or "address" in fact.label.casefold() or "location" in fact.label.casefold(),
        )
    return False


def _can_skip_exact_business_name(project: FlyerProject, normalized_text: str, raw_text: str) -> bool:
    policy = semantic_visibility_policy(project)
    if policy.brand_visibility_required_exact:
        return False
    if _requires_exact_business_name_for_menu_poster(project):
        return False
    if not policy.effective_business_name or not policy.campaign_title:
        return False
    if not _locked_fact_present_in_ocr(project, "campaign_title", normalized_text, raw_text):
        return False
    if policy.require_contact_anchor and not _locked_fact_present_in_ocr(
        project,
        "contact_phone",
        normalized_text,
        raw_text,
        source="customer_profile",
    ):
        return False
    if policy.require_location_anchor and not _locked_fact_present_in_ocr(
        project,
        "location",
        normalized_text,
        raw_text,
        source="customer_profile",
    ):
        return False
    return True


def _requires_exact_business_name_for_menu_poster(project: FlyerProject) -> bool:
    """Full-poster menu generations must carry the actual business masthead.

    The deterministic overlay path can stamp identity. Integrated menu posters
    cannot, so OCR/vision QA must not use the looser campaign/contact/location
    anchor exception for itemized menu flyers.
    """
    has_item = any(
        str(getattr(fact, "fact_id", "")).startswith("item:")
        for fact in getattr(project, "locked_facts", []) or []
    )
    if not has_item:
        return False
    return any(
        getattr(fact, "fact_id", "") == "business_name"
        and bool(str(getattr(fact, "value", "") or "").strip())
        and bool(getattr(fact, "required", False))
        for fact in getattr(project, "locked_facts", []) or []
    )


def _requires_english_only_menu_poster_contract(project: FlyerProject) -> bool:
    if not _requires_exact_business_name_for_menu_poster(project):
        return False
    source_text = " ".join(
        str(value or "")
        for value in (
            project.raw_request,
            getattr(project.fields, "notes", ""),
            *(fact.value for fact in getattr(project, "locked_facts", []) or []),
        )
    )
    if REGIONAL_SCRIPT_RE.search(source_text):
        return False
    return not bool(
        re.search(
            r"\b(?:in|use|using|language\s*:?)\s+(?:telugu|hindi|tamil|malayalam|kannada|gujarati|marathi|punjabi)\b",
            source_text.casefold(),
        )
    )


def _requires_english_only(project: FlyerProject) -> bool:
    text = f"{project.raw_request or ''} {getattr(project.fields, 'notes', '') or ''}".lower()
    return bool(
        re.search(r"\b(?:language\s*:\s*)?english\s+only\b", text)
        or re.search(r"\bonly\s+english\b", text)
        or re.search(r"\b(?:do\s+not|don't|dont|no)\s+use\s+(?:telugu|hindi|tamil|malayalam|kannada|gujarati|marathi|punjabi|regional)", text)
        or "no regional indian language" in text
        or "no regional languages" in text
    )


def _unrequested_operational_claim_blockers(project: FlyerProject, extracted_text: str) -> list[str]:
    source_text = " ".join(
        str(value or "")
        for value in (
            project.raw_request,
            getattr(project.fields, "notes", ""),
            *(fact.value for fact in project.locked_facts),
        )
    ).casefold()
    blockers: list[str] = []
    for claim, pattern in OPERATIONAL_CLAIM_PATTERNS:
        # Credit the claim as REQUESTED only when the SAME detector pattern matches the customer's
        # source (raw_request + notes + locked facts) — i.e. the customer actually stated it. A bare
        # `claim in source_text` keyword check missed the customer's own offer: they write "we cater"
        # / "we deliver" (the verb), not the gerund keyword "catering"/"delivery", so their own
        # locked offer fact got flagged as unrequested. This stays strict and grounded: a source with
        # NO matching claim phrasing still blocks the rendered claim (does NOT broadly allow it).
        if pattern.search(source_text):
            continue
        if pattern.search(extracted_text or ""):
            blockers.append(f"unrequested operational claim visible: {claim}")
    return blockers


# ─────────────────────────────────────────────────────────────────
# P0 #2 — severity classifier (pass / warn / block)
# Pure-function: blocker list + project → severity label.
# DICTIONARY is the policy; this evaluates it. No workflow side
# effects (Hermes-as-brain invariant: classifier is a validator,
# not a workflow owner). The decision to act on `warn` lives in
# generate-flyer-concepts (state-write) and cf-router (send).
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _WarnTierBlockerSpec:
    pattern: re.Pattern[str]
    label: str
    is_core_promise: bool = False
    is_brand_identity: bool = False
    is_event_essential: bool = False


_BLOCK_TIER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^placeholder text is visible in generated flyer$"), "placeholder"),
    (re.compile(r"^English-only flyer contains regional/non-English script$"), "regional_script"),
    (re.compile(r"^unrequested operational claim visible: "), "unrequested_claim"),
    (re.compile(r"^ocr/vision text unavailable for generated artifact"), "ocr_unavailable"),
    (re.compile(r"^replaced source text still visible: "), "source_text_visible"),
    # P1-1: a phone-shaped number, or a foreign country code, that does not match the
    # registered phone is a corrupted/hallucinated contact detail — customer-harmful,
    # fail closed to manual. Covers both "...number visible" and "...country code visible".
    (re.compile(r"^unverified phone "), "unverified_phone"),
    # P1-2: a flyer advertising an already-passed event date is customer-harmful — fail
    # closed to manual.
    (re.compile(r"^event date is in the past: "), "past_event_date"),
    # P1-4: vision-QA-reported duplicated/misspelled visible text (looks-broken) — fail
    # closed to manual.
    (re.compile(r"^visible text defect reported by QA: "), "text_defect"),
    (re.compile(r"^missing required visible fact: business_name$"), "missing_business_name"),
    (re.compile(r"^missing required visible fact: item:\d+:name$"), "missing_item_name"),
    (re.compile(r"^item price mismatch: item:\d+ expected "), "item_price_mismatch"),
    (re.compile(r"^duplicate item price visible: item:\d+ "), "duplicate_item_price"),
    (re.compile(r"^internal asset id visible: "), "internal_asset_id"),
    # bounded-creative-planner: a committed inferred item that did not render is a
    # block-tier intent failure (explicit, not implicit-via-default; Codex r5 #2).
    (re.compile(r"^inferred item not rendered: "), "inferred_item_not_rendered"),
    # bounded-creative-planner slice 5b: the customer asked for N items but the planner
    # contribution leaves the project short of N — the literal request is not satisfied.
    (re.compile(r"^requested item count not satisfied: "), "intent_count_unsatisfied"),
    (re.compile(r"placeholder|unreadable|garbled", re.IGNORECASE), "quality_note_corruption"),
)


_WARN_TIER_PATTERNS: tuple[_WarnTierBlockerSpec, ...] = (
    _WarnTierBlockerSpec(
        pattern=re.compile(r"^visible wrong business/brand: (?P<name>.+)$"),
        label="brand_variant",
        is_brand_identity=True,
    ),
    _WarnTierBlockerSpec(
        pattern=re.compile(r"^missing required visible fact: location$"),
        label="missing_location",
        is_event_essential=True,
    ),
    _WarnTierBlockerSpec(
        pattern=re.compile(r"^missing required visible fact: contact_info$"),
        label="missing_contact_info",
    ),
    _WarnTierBlockerSpec(
        pattern=re.compile(r"^missing required visible fact: schedule$"),
        label="missing_schedule",
        is_event_essential=True,
    ),
    _WarnTierBlockerSpec(
        pattern=re.compile(r"^missing required visible fact: promotion_end$"),
        label="missing_promotion_end",
        is_event_essential=True,
    ),
)


_WARN_TIER_COMBINATION_LIMIT: int = 3
_CORE_PROMISE_ESCALATION_LIMIT: int = 2


def _normalize_brand_for_match(value: str) -> str:
    """Casefold + strip apostrophes/backticks + collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[’ʼ'`]", "", value or "")).strip().casefold()


def _brand_tokens(normalized: str) -> set[str]:
    """Tokenize on non-alphanumeric. Returns set of tokens."""
    return {t for t in re.split(r"[^a-z0-9]+", normalized) if t}


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein. Pure function, no deps. Symmetric."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def _is_brand_typo(extracted: str, project_brand: str) -> bool:
    """AND-of-3 gate (operator decision 2026-05-28):
    edit-distance ≤ 2 AND token-overlap ≥ 0.5 AND
    (common-prefix ≥ 4 OR overlap ≥ 0.75).

    All comparisons use ``>=`` — boundary case at overlap == 0.5 with
    distance == 2 and prefix == 4 classifies as warn (per plan §5
    worked example F0108 "Laksmi'S Kitchen" vs "Lakshmi's Kitchen").

    Returns True if extracted is a typo of the project's brand (warn-tier
    fold). Returns False if extracted is structurally distinct (block-tier
    fold — wrong customer entirely)."""
    e = _normalize_brand_for_match(extracted)
    p = _normalize_brand_for_match(project_brand)
    if _edit_distance(e, p) > 2:
        return False
    et, pt = _brand_tokens(e), _brand_tokens(p)
    if not pt:
        return False
    overlap = len(et & pt) / len(pt)
    if overlap < 0.5:
        return False
    prefix_len = 0
    for c1, c2 in zip(e, p):
        if c1 != c2:
            break
        prefix_len += 1
    return prefix_len >= 4 or overlap >= 0.75


def _project_business_name(project: FlyerProject) -> str:
    """Lookup the canonical business_name from the project's locked_facts.
    Returns empty string if absent — brand-typo gate then rejects all
    variants (no known brand to compare against → fail safe to block)."""
    for fact in project.locked_facts:
        if fact.fact_id == "business_name":
            return fact.value or ""
    return ""


def classify_qa_severity(
    blockers: list[str],
    *,
    project: FlyerProject,
) -> str:
    """Pure-function classifier. Returns 'pass' | 'warn' | 'block'.

    Rule order (first match wins):
      1. any block-tier blocker → block
      2. ≥ 2 core-promise warn blockers → block (core-promise escalation)
      3. brand-identity warn AND event-essential warn → block (combo
         escalation — owner gets draft with name typo AND no event time
         is structurally worse than count=2 suggests)
      4. ≥ 3 total warn blockers → block (count cap)
      5. any warn-tier → warn
      6. else → pass

    The DICTIONARY (_BLOCK_TIER_PATTERNS, _WARN_TIER_PATTERNS) is the
    policy. This function only evaluates it — no workflow side effects."""
    block_hits: list[str] = []
    warn_specs: list[_WarnTierBlockerSpec] = []
    brand_name = _project_business_name(project)
    for blocker in blockers:
        # Block-tier first
        matched_block = False
        for pattern, _label in _BLOCK_TIER_PATTERNS:
            if pattern.search(blocker):
                block_hits.append(blocker)
                matched_block = True
                break
        if matched_block:
            continue
        # Then warn-tier
        for spec in _WARN_TIER_PATTERNS:
            m = spec.pattern.search(blocker)
            if not m:
                continue
            if spec.label == "brand_variant":
                # Brand-variant splits into warn (typo) vs block (wrong brand
                # entirely) via the Levenshtein + token + prefix gate.
                if not _is_brand_typo(m.group("name"), brand_name):
                    block_hits.append(blocker)
                    break
            warn_specs.append(spec)
            break
    if block_hits:
        return "block"
    if sum(1 for s in warn_specs if s.is_core_promise) >= _CORE_PROMISE_ESCALATION_LIMIT:
        return "block"
    if any(s.is_brand_identity for s in warn_specs) and any(s.is_event_essential for s in warn_specs):
        return "block"
    if len(warn_specs) >= _WARN_TIER_COMBINATION_LIMIT:
        return "block"
    if warn_specs:
        return "warn"
    if blockers:
        return "block"
    return "pass"


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT_SEC = 60
VISION_QA_MODEL = os.environ.get("FLYER_VISUAL_QA_MODEL") or os.environ.get("VISION_MODEL") or "openai/gpt-4o-mini"
VISION_QA_PROMPT = """Read this generated flyer/poster image as OCR/vision QA.

Return STRICT JSON only:
{
  "extracted_text": "all visible flyer text you can read, preserving names, prices, dates, phones, addresses, badges, and placeholders",
  "quality_notes": ["short factual notes about unreadable/garbled text, visible placeholders, any text that appears duplicated, or any clearly misspelled word"]
}

Do not invent missing text. If no readable text exists, return an empty extracted_text string.
"""


@dataclass(frozen=True)
class VisualQAValidation:
    ok: bool
    blockers: list[str]
    report_path: Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def visual_qa_path(artifact_path: Path | str) -> Path:
    return Path(str(artifact_path) + ".qa.json")


def _read_key_from_env_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if key.strip() == "OPENROUTER_API_KEY":
                return raw.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _openrouter_key() -> str:
    return (
        os.environ.get("OPENROUTER_API_KEY", "").strip()
        or _read_key_from_env_file("/root/.hermes/.env")
        or _read_key_from_env_file("/opt/shift-agent/.env")
    )


def _vision_text(path: Path) -> tuple[str, str, str, list[str]]:
    key = _openrouter_key()
    if not key or "PLACEHOLDER" in key:
        return "", "unavailable", "ocr_vision", ["OPENROUTER_API_KEY missing"]
    if not path.exists() or not path.is_file():
        return "", "unavailable", "ocr_vision", ["artifact missing"]
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "image/png"
    if not mime.startswith("image/") and mime != "application/pdf":
        return "", "unavailable", "ocr_vision", [f"unsupported OCR media type: {mime}"]
    raw = base64.b64encode(path.read_bytes()).decode("ascii")
    payload = {
        "model": VISION_QA_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_QA_PROMPT},
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
    try:
        with urllib.request.urlopen(req, timeout=OPENROUTER_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
        doc = json.loads(body)
        content = doc["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        return "", "unavailable", "ocr_vision", [f"vision OCR failed: {type(exc).__name__}"]
    notes = [str(item) for item in parsed.get("quality_notes") or [] if str(item).strip()]
    return str(parsed.get("extracted_text") or ""), "openrouter", "ocr_vision", notes


def _sidecar_text(path: Path, *, allow_sidecar: bool) -> tuple[str, str, str]:
    sidecar = Path(str(path) + ".ocr.txt")
    if allow_sidecar and sidecar.exists():
        return sidecar.read_text(encoding="utf-8"), "sidecar", "sidecar_test"
    return "", "unavailable", "ocr_vision"


def _internal_asset_id_blockers(extracted_text: str) -> list[str]:
    seen: set[str] = set()
    blockers: list[str] = []
    for match in INTERNAL_BRAND_ASSET_ID_RE.finditer(extracted_text or ""):
        asset_id = match.group(0).upper()
        if asset_id in seen:
            continue
        seen.add(asset_id)
        blockers.append(f"internal asset id visible: {asset_id}")
    return blockers


def run_visual_qa(
    project: FlyerProject,
    artifact_path: Path | str,
    *,
    output_format: str,
    asset_id: str = "",
    allow_sidecar: bool | None = None,
) -> FlyerVisualQAReport:
    artifact = Path(artifact_path)
    if allow_sidecar is None:
        allow_sidecar = os.environ.get("FLYER_QA_ALLOW_SIDECAR") == "1"
    extracted_text, provider, qa_source = _sidecar_text(artifact, allow_sidecar=allow_sidecar)
    provider_notes: list[str] = []
    if not extracted_text:
        extracted_text, provider, qa_source, provider_notes = _vision_text(artifact)
    blockers: list[str] = []
    if not extracted_text:
        _early_blockers = ["ocr/vision text unavailable for generated artifact", *provider_notes]
        return FlyerVisualQAReport(
            project_id=project.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact),
            artifact_sha256=sha256_file(artifact),
            project_version=project.version,
            output_format=output_format,
            provider=provider,
            qa_source=qa_source,
            status="provider_unavailable",
            blockers=_early_blockers,
            severity=classify_qa_severity(_early_blockers, project=project),
            extracted_text="",
            checked_at=datetime.now(timezone.utc),
        )
    normalized = _normalize_text_for_match(extracted_text)
    if PLACEHOLDER_RE.search(extracted_text):
        blockers.append("placeholder text is visible in generated flyer")
    if RAW_REQUEST_INSTRUCTION_RE.search(extracted_text):
        blockers.append("raw request instruction text is visible in generated flyer")
    blockers.extend(_internal_asset_id_blockers(extracted_text))
    if (_requires_english_only(project) or _requires_english_only_menu_poster_contract(project)) and REGIONAL_SCRIPT_RE.search(extracted_text):
        blockers.append("English-only flyer contains regional/non-English script")
    blockers.extend(_unrequested_operational_claim_blockers(project, extracted_text))
    blockers.extend(_past_event_date_blockers(project))
    blockers.extend(_inferred_item_coverage_blockers(project, extracted_text))
    blockers.extend(_inferred_intent_count_blockers(project))
    blockers.extend(note for note in provider_notes if "placeholder" in note.lower() or "unreadable" in note.lower() or "garbled" in note.lower())
    blockers.extend(_text_defect_note_blockers(provider_notes))
    blockers.extend(visible_wrong_brand_blockers(project, extracted_text))
    skip_business_name_exact = _can_skip_exact_business_name(project, normalized, extracted_text)
    for fact in project.locked_facts:
        if not fact.required:
            continue
        if fact.fact_id == "business_name" and skip_business_name_exact:
            continue
        # Phone/contact facts use digit-run matching; other locked facts use
        # text matching even if they contain address/ZIP digits.
        semantic_present = _semantic_visible_fact_present(fact.fact_id, fact.label, fact.value, normalized, extracted_text)
        if semantic_present:
            continue
        if fact.fact_id == "promotion_end" or "promotion end" in fact.label.casefold():
            blockers.append(f"missing required visible fact: {fact.fact_id}")
            continue
        if not _value_present_in(
            normalized,
            fact.value,
            phone_match=_locked_fact_uses_phone_match(
                fact_id=fact.fact_id,
                label=fact.label,
                value=fact.value,
            ),
            address_match=fact.fact_id == "location" or "address" in fact.label.casefold() or "location" in fact.label.casefold(),
            schedule_match=fact.fact_id == "schedule" or "schedule" in fact.label.casefold(),
            price_match=fact.fact_id.endswith(":price") or "price" in fact.label.casefold(),
        ):
            blockers.append(f"missing required visible fact: {fact.fact_id}")
    blockers.extend(_item_price_pair_blockers(project, extracted_text))
    blockers.extend(_unexpected_phone_blockers(project, extracted_text))
    # Source-contract negative-assertion gate: any value in
    # forbidden_substrings (populated upstream from brand/phone/address
    # replacements) must NOT appear in the OCR text. Reuses the same
    # word-boundary-aware presence check as the positive loop.
    for ext in getattr(project, "reference_extractions", []) or []:
        contract = getattr(ext, "source_contract", None)
        if not contract:
            continue
        for forbidden in getattr(contract, "forbidden_substrings", []) or []:
            if not forbidden:
                continue
            if _looks_like_phone(forbidden):
                if _phone_value_present_in(extracted_text, forbidden):
                    blockers.append(f"replaced source text still visible: {forbidden}")
                continue
            normalized_forbidden = _normalize_text_for_match(forbidden)
            if not normalized_forbidden:
                continue
            if _text_value_present_in(normalized, normalized_forbidden):
                blockers.append(f"replaced source text still visible: {forbidden}")
    return FlyerVisualQAReport(
        project_id=project.project_id,
        asset_id=asset_id,
        artifact_path=str(artifact),
        artifact_sha256=sha256_file(artifact),
        project_version=project.version,
        output_format=output_format,
        provider=provider,
        qa_source=qa_source,
        status="failed" if blockers else "passed",
        blockers=blockers,
        severity=classify_qa_severity(blockers, project=project),
        extracted_text=extracted_text,
        checked_at=datetime.now(timezone.utc),
    )


def write_visual_qa_report(report: FlyerVisualQAReport, artifact_path: Path | str) -> Path:
    artifact = Path(artifact_path)
    data = report.model_dump(mode="json")
    data["artifact_sha256"] = sha256_file(artifact)
    path = visual_qa_path(artifact)
    text = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        from safe_io import atomic_write_text  # type: ignore
    except Exception:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    else:
        atomic_write_text(path, text)
    return path


def validate_visual_qa_report(
    artifact_path: Path | str,
    *,
    project_id: str,
    project_version: int,
    output_format: str,
    allow_sidecar: bool | None = None,
) -> VisualQAValidation:
    artifact = Path(artifact_path)
    path = visual_qa_path(artifact)
    blockers: list[str] = []
    if allow_sidecar is None:
        allow_sidecar = os.environ.get("FLYER_QA_ALLOW_SIDECAR") == "1"
    if not path.exists():
        return VisualQAValidation(False, ["visual QA report missing"], path)
    try:
        report = FlyerVisualQAReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        return VisualQAValidation(False, [f"visual QA report unreadable: {exc}"], path)
    if report.project_id != project_id:
        blockers.append("visual QA project mismatch")
    if report.project_version != project_version:
        blockers.append("visual QA project version mismatch")
    if report.output_format != output_format:
        blockers.append("visual QA output format mismatch")
    if report.artifact_sha256 != sha256_file(artifact):
        blockers.append("visual QA artifact hash mismatch")
    if report.status != "passed":
        blockers.append("visual QA did not pass")
    if report.qa_source == "sidecar_test" and not allow_sidecar:
        blockers.append("sidecar visual QA is disabled")
    # `operator_review` is the cockpit-completion path: a fresh-OTP'd
    # operator uploaded an approved designer asset with a reason. The
    # operator's cockpit-audit row + the project's `manual_review.detail`
    # are the audit trail; the customer's APPROVE reply on the resulting
    # preview is the final visual/text QA gate. We accept it without the
    # sidecar env flag because it is NOT a dev-test bypass — it carries
    # operator authority by construction.
    blockers.extend(report.blockers)
    return VisualQAValidation(not blockers, blockers, path)
