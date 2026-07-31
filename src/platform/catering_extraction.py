"""M1 — deterministic catering field extraction (PURE, stdlib-only).

Single home for the regex extraction that used to live only inside the cf-router
plugin (`hooks._extract_catering_fields_from_text`). It moved here because TWO
call sites now need the EXACT same parse:

  * cf-router F7 primary-mode, on the inbound that creates a lead, and
  * ``amend-catering-lead``, on an amendment's captured ``raw_text`` and on a
    slot-filling answer — a script, which cannot import the hyphenated plugin
    package. Two copies of this grammar would drift, and the amendment path
    exists precisely to keep the lead in agreement with what the customer said.

``hooks._extract_catering_fields_from_text`` is now a thin delegation, so the
deployed F7 behavior for the fields it already produced is unchanged.

DETERMINISTIC ONLY — no LLM, no network, no file IO. Regex is the right tool
here (structured tokens: dates, counts, money, fixed vocabularies), not a
keyword whitelist standing in for intent classification.

CONSERVATIVE BY CONSTRUCTION: a field with no confident match is LEFT OUT of the
returned dict (the caller keeps its existing value / the schema default). The
extractor never guesses — a wrong headcount or date is worse than a null the
owner fills in, because the owner can see a blank but cannot see a plausible
wrong number.

Deployed FLAT to /opt/shift-agent/ so scripts + the cf-router plugin can import it.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

# ── Dates ────────────────────────────────────────────────────────────────────
MONTH_NUMBERS = {
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
# Month-name form ("June 15", "june 15th", "Jun 15, 2026"). This is the ONLY
# form the deployed fresh-vs-stale discriminator parses — see
# parse_month_day_event_date's contract note.
MONTH_DAY_RE = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?\b",
    re.IGNORECASE,
)
# Numeric form ("6/15", "06/15/2026", "6-15-26"). US month-first ordering — the
# deployed customer base is US (Triveni, 9 locations across TX/MD/NC/SC/OH/VA).
# The negative lookahead rejects the common fraction-then-unit shape ("1/2 tray",
# "3/4 pan") that would otherwise read as Jan 2 / Mar 4; a bare fraction with no
# unit is rare in inquiry text and month/day range checks catch the rest.
NUMERIC_DATE_RE = re.compile(
    r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b"
    r"(?!\s*(?:tray|trays|pan|pans|cup|cups|lb|lbs|kg|oz|hour|hours|hr|hrs)\b)",
)


def _resolve_year(month: int, day: int, year: Optional[int],
                  today: Optional[Any] = None) -> Optional[str]:
    """(month, day, optional year) -> ISO date, rolling a bare month/day forward
    to next year when it already passed. Returns None for a calendar-invalid date."""
    today = today or datetime.now(timezone.utc).date()
    explicit_year = year is not None
    if year is None:
        year = today.year
    elif year < 100:                     # two-digit year: "6/15/26" -> 2026
        year += 2000
    try:
        candidate = datetime(year, month, day, tzinfo=timezone.utc).date()
    except ValueError:
        return None
    if not explicit_year and candidate < today:
        try:
            candidate = datetime(year + 1, month, day, tzinfo=timezone.utc).date()
        except ValueError:
            return None
    return candidate.isoformat()


def parse_month_day_event_date(text: str) -> Optional[str]:
    """Month-NAME dates only ("June 15", "jun 15 2026") -> ISO, or None.

    CONTRACT (do not widen): the deployed fresh-vs-stale discriminator
    (hooks._inbound_event_identity) calls exactly this function, and its rules are
    ruled binding. Widening the date grammar HERE would silently change which
    follow-ups are read as contradicting a different event. The numeric form lives
    in parse_event_date, which only the extractor uses.
    """
    match = MONTH_DAY_RE.search(text or "")
    if not match:
        return None
    month = MONTH_NUMBERS.get(match.group(1).lower())
    if month is None:
        return None
    year = int(match.group(3)) if match.group(3) else None
    return _resolve_year(month, int(match.group(2)), year)


def parse_numeric_event_date(text: str) -> Optional[str]:
    """Numeric dates ("6/15", "06/15/2026", "6-15-26") -> ISO, or None."""
    for match in NUMERIC_DATE_RE.finditer(text or ""):
        month, day = int(match.group(1)), int(match.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        iso = _resolve_year(month, day, int(match.group(3)) if match.group(3) else None)
        if iso:
            return iso
    return None


def parse_event_date(text: str) -> Optional[str]:
    """Full extractor date grammar: month-name form first (least ambiguous), then
    numeric. None when neither matches confidently."""
    return parse_month_day_event_date(text) or parse_numeric_event_date(text)


# ── Headcount ────────────────────────────────────────────────────────────────
# INVARIANT (pinned by tests/test_catering_extraction.py): these patterns are a
# verbatim copy of cf-router actions._HEADCOUNT_PATTERNS. classify_catering's copy
# stays the source of truth for the 26 pinned classifier cases and is deliberately
# NOT refactored to import from here (that module runs inside the Hermes plugin
# loader); the drift test fails loudly if the two ever diverge.
HEADCOUNT_PATTERNS = [
    re.compile(
        r"(\d+)\s*(?:people|persons?|guests?|ppl|attendees?|heads?|"
        r"meals?|plates?|covers?|settings?|members?|pax)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:for|of|serve|serving|feed|cater(?:ing)?\s+(?:to|for))\s+(\d+)\b", re.IGNORECASE),
    re.compile(r"\b(\d+)\s+(?:vegetarian|veg|non[\s-]?veg|vegan|jain|halal|kosher)\b", re.IGNORECASE),
]
HEADCOUNT_MIN = 5
HEADCOUNT_MAX = 10000


def parse_headcount(text: str) -> Optional[int]:
    """Headcount straight from text, for callers with no classify_catering signals
    (the amendment + answer paths). Same patterns and same 5..10000 acceptance
    band classify_catering applies before emitting a `headcount:N` signal."""
    for pat in HEADCOUNT_PATTERNS:
        m = pat.search(text or "")
        if not m:
            continue
        try:
            hc = int(m.group(1))
        except (ValueError, IndexError):
            continue
        if HEADCOUNT_MIN <= hc <= HEADCOUNT_MAX:
            return hc
    return None


def parse_headcount_from_signals(signals: list) -> Optional[int]:
    """Extract the int from a `headcount:N` signal emitted by classify_catering."""
    for sig in signals or []:
        if isinstance(sig, str) and sig.startswith("headcount:"):
            try:
                return int(sig.split(":", 1)[1])
            except (ValueError, IndexError):
                return None
    return None


# ── Veg / non-veg split ──────────────────────────────────────────────────────
NON_VEG_COUNT_RE = re.compile(
    r"\b(\d{1,5})\s+(?:people\s+)?(?:non[\s-]?vegetarians?|non[\s-]?veg(?:etarians?)?)\b",
    re.IGNORECASE,
)
VEG_COUNT_RE = re.compile(
    r"\b(\d{1,5})\s+(?:people\s+)?(?:vegetarians?|veg(?:etarians?)?)\b",
    re.IGNORECASE,
)
NON_VEG_WORD_RE = re.compile(r"\bnon[\s-]?veg(?:etarian)?s?\b", re.IGNORECASE)
VEG_WORD_RE = re.compile(r"\b(?:vegetarian|veg)\b", re.IGNORECASE)
# "mostly vegetarian but some chicken" — a proportion the customer stated in prose
# with no numbers. Recorded as a NOTE, never as a count: inventing "70/30" from
# "mostly" is exactly the guess this module refuses to make.
VEG_MAJORITY_RE = re.compile(
    r"\b(?:mostly|mainly|primarily|majority|largely)\s+(?:are\s+)?veg(?:etarian)?\b",
    re.IGNORECASE,
)

REQUESTED_MENU_COUNT_RE = re.compile(
    r"\b(\d+|one|two|three)\s+(?:sample\s+)?(?:combinations?\s+)?menus?\b",
    re.IGNORECASE,
)
COUNT_WORDS = {"one": 1, "two": 2, "three": 3}


# ── Event type ───────────────────────────────────────────────────────────────
# First match by this FIXED priority wins. A compound message ("office diwali
# party") resolves to the higher-priority label, which is a hint on the owner card,
# not a decision — the owner edits it like any other extracted field.
EVENT_TYPE_PATTERNS = [
    ("wedding", re.compile(
        r"\b(?:wedding|weddings|marriage|nikah|shaadi|shadi|sangeet|"
        r"engagement|mehendi|mehndi|haldi|reception)\b", re.IGNORECASE)),
    ("birthday", re.compile(r"\b(?:birthday|birthdays|bday|b[\s-]day)\b", re.IGNORECASE)),
    ("religious", re.compile(
        r"\b(?:puja|pooja|havan|homam|satsang|prayer|bhajan|kirtan|langar|"
        r"temple|church|mosque|gurudwara|diwali|deepavali|eid|navratri|navaratri|"
        r"ganesh|onam|pongal|ugadi|religious)\b", re.IGNORECASE)),
    ("corporate", re.compile(
        r"\b(?:corporate|office|company|workplace|conference|seminar|"
        r"client\s+(?:event|lunch|dinner)|team\s+(?:event|lunch|dinner|outing)|"
        r"staff\s+(?:party|lunch|dinner)|board\s+meeting|offsite|off[\s-]site)\b",
        re.IGNORECASE)),
    ("other", re.compile(
        r"\b(?:graduation|anniversary|housewarming|house\s+warming|"
        r"baby\s*shower|bridal\s+shower|retirement|funeral|memorial|"
        r"festival|fundraiser|reunion)\b", re.IGNORECASE)),
]


def parse_event_type(text: str) -> Optional[str]:
    """Canonical event_type, or None when no vocabulary term appears."""
    for label, pattern in EVENT_TYPE_PATTERNS:
        if pattern.search(text or ""):
            return label
    return None


# ── Service style ────────────────────────────────────────────────────────────
SERVICE_STYLE_PATTERNS = [
    ("buffet", re.compile(r"\b(?:buffet|buffet[\s-]style|self[\s-]?serve|serve[\s-]?yourself)\b",
                          re.IGNORECASE)),
    ("plated", re.compile(r"\b(?:plated|sit[\s-]?down|table\s+service|served\s+at\s+the\s+table)\b",
                          re.IGNORECASE)),
    ("boxed", re.compile(
        r"\b(?:boxed|box(?:ed)?\s+lunch(?:es)?|lunch\s+boxes?|"
        r"individual\s+(?:boxes|meals|packs?|portions?)|bento|to[\s-]?go\s+boxes?)\b",
        re.IGNORECASE)),
    ("family_style", re.compile(
        r"\b(?:family[\s-]?style|shared\s+platters?|communal\s+(?:serving|table))\b",
        re.IGNORECASE)),
    ("other", re.compile(
        r"\b(?:live\s+(?:counter|station)s?|food\s+stations?|chaat\s+counter|"
        r"food\s+truck)\b", re.IGNORECASE)),
]


def parse_service_style(text: str) -> Optional[str]:
    for label, pattern in SERVICE_STYLE_PATTERNS:
        if pattern.search(text or ""):
            return label
    return None


# ── Delivery vs pickup ───────────────────────────────────────────────────────
# classify_catering's _DELIVERY_KEYWORDS already matched on this family and threw
# the result away (it only needed the boolean signal). These two split that hit
# into the direction the schema field actually stores. AMBIGUOUS ON PURPOSE: when
# both directions appear (or neither), no key is emitted and the caller's existing
# value / the "unknown" default stands — guessing the logistics of a catering
# order is a money-adjacent guess.
PICKUP_RE = re.compile(
    r"\b(?:pick(?:ing)?[\s-]?up|pickup|we(?:'ll| will)?\s+collect|"
    r"carry[\s-]?out|take[\s-]?away|takeaway|take[\s-]?out|takeout)\b",
    re.IGNORECASE,
)
DELIVERY_RE = re.compile(
    r"\b(?:deliver|delivers|delivery|delivered|delivering|drop[\s-]?off|"
    r"dropped\s+off|bring\s+(?:it|the\s+food|everything)|set\s*up\s+at)\b",
    re.IGNORECASE,
)


def parse_delivery_or_pickup(text: str) -> Optional[str]:
    """"delivery" | "pickup" | None (ambiguous or absent)."""
    wants_delivery = bool(DELIVERY_RE.search(text or ""))
    wants_pickup = bool(PICKUP_RE.search(text or ""))
    if wants_delivery and not wants_pickup:
        return "delivery"
    if wants_pickup and not wants_delivery:
        return "pickup"
    return None


# ── Event time ───────────────────────────────────────────────────────────────
CLOCK_12H_RE = re.compile(r"\b(\d{1,2})(?::([0-5]\d))?\s*([ap])\.?\s?m\.?\b", re.IGNORECASE)
CLOCK_24H_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b(?!\s*[ap]\.?m)", re.IGNORECASE)
# Coarse words map to the middle of the window the phrase names. Approximate BY
# ADMISSION: whenever one of these fires, the source word is recorded in notes so
# the owner sees the time came from "evening", not from a stated clock time.
TIME_WORDS = [
    ("morning", "09:00"),
    ("noon", "12:00"),
    ("lunchtime", "12:00"),
    ("afternoon", "13:00"),
    ("evening", "18:00"),
    ("night", "20:00"),
]
TIME_WORD_RE = re.compile(
    r"\b(morning|noon|lunchtime|afternoon|evening|night)\b", re.IGNORECASE,
)


def parse_event_time(text: str) -> tuple[Optional[str], Optional[str]]:
    """(HH:MM, approx_source_word). approx_source_word is None for a stated clock
    time and the matched word ("evening") when the time was inferred from prose."""
    m = CLOCK_12H_RE.search(text or "")
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if 1 <= hour <= 12:
            meridiem = m.group(3).lower()
            if meridiem == "a":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
            return f"{hour:02d}:{minute:02d}", None
    m = CLOCK_24H_RE.search(text or "")
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}", None
    m = TIME_WORD_RE.search(text or "")
    if m:
        word = m.group(1).lower()
        for label, hhmm in TIME_WORDS:
            if label == word:
                return hhmm, word
    return None, None


# ── Budget ───────────────────────────────────────────────────────────────────
# A TOTAL budget needs an explicit cue word — a bare "$3000" in a pasted menu is
# a price list, not a budget.
BUDGET_TOTAL_RE = re.compile(
    r"\b(?:budget|spend|spending|under|below|less\s+than|up\s+to|max(?:imum)?|"
    r"around|about|approximately|roughly|no\s+more\s+than)\b[^\d$]{0,20}"
    r"\$?\s*(\d[\d,]*)(?:\.\d{2})?",
    re.IGNORECASE,
)
# Per-person budgets are recorded as a NOTE, never as budget_hint_usd (a total
# field). Multiplying by headcount to synthesise a total would be composing a
# price the owner never approved — the exact class BL-CATER-08 forbids.
BUDGET_PER_PERSON_RE = re.compile(
    r"\$\s*(\d[\d,]*)(?:\.\d{2})?\s*(?:per|/|a|each\s+)\s*"
    r"(?:person|head|guest|plate|pax|people)\b",
    re.IGNORECASE,
)
BUDGET_MAX_USD = 1_000_000


def _to_int(raw: str) -> Optional[int]:
    try:
        return int(raw.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def parse_budget(text: str) -> tuple[Optional[int], Optional[int]]:
    """(total_usd, per_person_usd). Either may be None."""
    per_person = None
    m = BUDGET_PER_PERSON_RE.search(text or "")
    if m:
        value = _to_int(m.group(1))
        if value is not None and 0 <= value <= BUDGET_MAX_USD:
            per_person = value
    total = None
    for m in BUDGET_TOTAL_RE.finditer(text or ""):
        value = _to_int(m.group(1))
        if value is None or not (0 <= value <= BUDGET_MAX_USD):
            continue
        # A per-person figure ("under $20 per person") is NOT a total.
        if per_person is not None and value == per_person:
            continue
        total = value
        break
    return total, per_person


# ── Composite extractor ──────────────────────────────────────────────────────
def extract_catering_fields(text: str, signals: Optional[list] = None) -> Optional[dict]:
    """Deterministic CateringLeadExtractedFields subset for `text`.

    Returns a dict of ONLY the fields that matched (so a caller can merge it over
    existing values without nulling anything), or None when nothing matched.

    `signals` is classify_catering's signal list when the caller has one; headcount
    is then read from its `headcount:N` signal so cf-router forwards exactly what
    the classifier already found. Callers with no signals (amendment + answer
    paths) get the same headcount grammar applied straight to the text.
    """
    fields: dict[str, Any] = {}
    notes: list[str] = []
    text = text or ""

    headcount = (parse_headcount_from_signals(signals) if signals
                 else None)
    if headcount is None:
        headcount = parse_headcount(text)
    if headcount is not None:
        fields["headcount"] = headcount

    event_date = parse_event_date(text)
    if event_date:
        fields["event_date"] = event_date

    # Dietary tags + explicit veg/non-veg counts. Tag derivation is UNCHANGED from
    # the deployed extractor (order-stable, deduped); the counts are new.
    dietary: list[str] = []
    non_veg_match = NON_VEG_COUNT_RE.search(text)
    veg_match = VEG_COUNT_RE.search(text)
    if non_veg_match:
        dietary.append("non-veg")
        count = int(non_veg_match.group(1))
        fields["nonveg_guest_count"] = count
        notes.append(f"{count} non-veg")
    elif NON_VEG_WORD_RE.search(text):
        dietary.append("non-veg")
    if veg_match:
        dietary.append("veg")
        count = int(veg_match.group(1))
        fields["veg_guest_count"] = count
        notes.append(f"{count} veg")
    elif VEG_WORD_RE.search(text):
        dietary.append("veg")
    if dietary:
        # Preserve stable order while avoiding duplicates.
        fields["dietary_restrictions"] = [v for v in ("veg", "non-veg") if v in set(dietary)]

    count_match = REQUESTED_MENU_COUNT_RE.search(text)
    if count_match:
        raw_count = count_match.group(1).lower()
        count = COUNT_WORDS.get(raw_count, int(raw_count) if raw_count.isdigit() else 0)
        if count:
            notes.append(f"requested {count} sample menu combinations")

    if VEG_MAJORITY_RE.search(text):
        notes.append("veg-majority stated (no counts given)")

    event_type = parse_event_type(text)
    if event_type:
        fields["event_type"] = event_type

    service_style = parse_service_style(text)
    if service_style:
        fields["service_style"] = service_style

    direction = parse_delivery_or_pickup(text)
    if direction:
        fields["delivery_or_pickup"] = direction

    event_time, approx_word = parse_event_time(text)
    if event_time:
        fields["event_time"] = event_time
        if approx_word:
            notes.append(f'event time approximate — customer said "{approx_word}"')

    total_budget, per_person_budget = parse_budget(text)
    if total_budget is not None:
        fields["budget_hint_usd"] = total_budget
    if per_person_budget is not None:
        notes.append(f"budget stated as ${per_person_budget} per person")

    if notes:
        fields["notes"] = "; ".join(notes)
    return fields or None


# ── Quote acceptance / decline (M3) ──────────────────────────────────────────
# After a quote is sent there was NO acceptance path at all: the only legal moves
# out of SENT_TO_CUSTOMER were CLOSED and STALE, and only an operator script ever
# wrote CLOSED. A customer saying "we accept" reached the generic follow-up reply
# and the booking existed nowhere.
#
# Regex is the RIGHT tool here and an LLM is the wrong one: this is a
# deterministic, audited, money-adjacent state change (it books an event), which
# is exactly the structured-token territory the project rules carve out. The cost
# of a false positive — booking an event the customer never agreed to — is far
# higher than the cost of falling through to the existing follow-up path, so the
# grammar is deliberately narrow and every uncertain shape returns None.
#
# Evaluation order is load-bearing:
#   questions are excluded first  — "should we accept?" / "do you accept venmo?"
#     must never book anything, so only ASSERTION sentences are matched
#   decline before accept          — "not going ahead" contains "going ahead"
#   negated-accept before accept   — "we don't accept credit cards" is a payment
#     remark, not a decline; it returns None (no automated state change at all)
#   ambiguous last, whole-message  — a message that is ONLY "ok"/"sounds good"
#     gets one clarification; the same words with real content after them
#     ("sounds good, add 20 plates") are an amendment and fall through untouched.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_DECLINE_PATTERNS = [
    re.compile(r"\b(?:we|i)\s+(?:have\s+)?declin(?:e|ed)\b", re.IGNORECASE),
    re.compile(r"^declined?\b", re.IGNORECASE),
    re.compile(r"\bnot\s+(?:going|moving)\s+(?:ahead|forward)\b", re.IGNORECASE),
    re.compile(r"\b(?:went|going)\s+with\s+(?:someone|somebody|another|a\s+different|"
               r"other)\b", re.IGNORECASE),
    re.compile(r"\b(?:decided|chose|choosing|picked)\s+(?:to\s+go\s+with\s+|on\s+)?"
               r"(?:someone|somebody|another|a\s+different)\b", re.IGNORECASE),
    re.compile(r"\bcancel\s+(?:the|our|this|my)?\s*"
               r"(?:quote|order|booking|event|catering|inquiry)\b", re.IGNORECASE),
    re.compile(r"\bno\s+longer\s+need(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bnot\s+interested\b", re.IGNORECASE),
    re.compile(r"\bwe(?:'?ll| will)\s+pass\b", re.IGNORECASE),
]

# A negated commitment verb. NOT read as a decline — "we don't accept credit
# cards" is the common real sentence — but it must never be read as acceptance
# either, so it hard-stops the whole detector.
_NEGATED_ACCEPT_RE = re.compile(
    r"\b(?:do(?:n'?t|\s+not)|does(?:n'?t|\s+not)|did(?:n'?t|\s+not)|"
    r"won'?t|will\s+not|can'?t|cannot|could\s+not|couldn'?t|never|not)\s+"
    r"(?:\w+\s+){0,2}?(?:accept(?:ing)?|proceed(?:ing)?|book(?:ing)?|confirm(?:ing)?)\b",
    re.IGNORECASE,
)

_ACCEPT_PATTERNS = [
    # The customer, as the subject, accepting. "do YOU accept venmo" cannot match.
    re.compile(r"\b(?:we|i)(?:'?ve|\s+have)?\s+accept(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\b(?:quote|proposal|estimate|price)\s+(?:is\s+)?accepted\b", re.IGNORECASE),
    re.compile(r"^accepted\b", re.IGNORECASE),
    re.compile(r"\bconfirmed[\s,.!]+(?:go\s+ahead|please\s+proceed|proceed|"
               r"(?:let'?s|lets)\s+(?:go|book|proceed))\b", re.IGNORECASE),
    re.compile(r"\b(?:yes|yeah|yep)[\s,.!]+(?:let'?s|lets|we(?:'?d| would)\s+like\s+to|"
               r"please)\s+(?:book|proceed|go\s+ahead|confirm)\b", re.IGNORECASE),
    re.compile(r"\bwe(?:'?d| would)\s+like\s+to\s+(?:proceed|book|go\s+ahead|confirm)\b",
               re.IGNORECASE),
    re.compile(r"\bwe\s+(?:want|wish)\s+to\s+(?:proceed|book|go\s+ahead|confirm)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:let'?s|lets)\s+(?:book|proceed|go\s+ahead)\b", re.IGNORECASE),
    re.compile(r"\bplease\s+(?:book|proceed|go\s+ahead)\b", re.IGNORECASE),
]

# Whole-message filler. Alone these mean nothing ("ok" to WHAT?) — they get ONE
# clarification, never a booking.
_AMBIGUOUS_TOKEN = (
    r"(?:ok(?:ay)?|kk?|sure|great|good|nice|perfect|cool|alright|all\s+right|fine|"
    r"sounds\s+(?:good|great)|looks\s+good|thanks(?:\s+a\s+lot)?|thank\s+you|thx|ty|"
    r"yes|yeah|yep|yup|noted|got\s+it|received|\U0001F44D|\U0001F44C|\U0001F64F|\U0001F60A)"
)
_AMBIGUOUS_ONLY_RE = re.compile(
    rf"^(?:{_AMBIGUOUS_TOKEN})(?:[\s,.!—\-]+(?:{_AMBIGUOUS_TOKEN}))*[\s,.!?]*$",
    re.IGNORECASE,
)


def _assertion_text(normalized: str) -> str:
    """The message minus its questions.

    A question is never a commitment: "should we accept?" and "can you cancel the
    quote?" must not book or close anything. Splitting per SENTENCE rather than
    discarding the whole message keeps a real acceptance that happens to be
    followed by a question ("We accept. When is the deposit due?") detectable.
    """
    parts = _SENTENCE_SPLIT_RE.split(normalized)
    return " ".join(p for p in parts if not p.rstrip().endswith("?"))


def detect_quote_acceptance(text: str) -> Optional[dict]:
    """Deterministically classify a post-quote customer message.

    Returns ``{"outcome": ..., "matched_phrase": ...}`` where outcome is one of
    ``"accepted"`` / ``"declined"`` / ``"ambiguous"``, or ``None`` when the message
    carries no acceptance signal at all (the overwhelmingly common case — it then
    takes the unchanged follow-up path).

    ``"ambiguous"`` means "this MIGHT be a yes and we refuse to guess": the caller
    sends exactly one clarification and records that it did, never a booking.
    """
    normalized = " ".join((text or "").split())
    if not normalized:
        return None

    statements = _assertion_text(normalized)
    if statements:
        for pattern in _DECLINE_PATTERNS:
            m = pattern.search(statements)
            if m:
                return {"outcome": "declined", "matched_phrase": m.group(0)[:120]}
        if not _NEGATED_ACCEPT_RE.search(statements):
            for pattern in _ACCEPT_PATTERNS:
                m = pattern.search(statements)
                if m:
                    return {"outcome": "accepted", "matched_phrase": m.group(0)[:120]}

    if _AMBIGUOUS_ONLY_RE.match(normalized):
        return {"outcome": "ambiguous", "matched_phrase": normalized[:120]}
    return None


__all__ = [
    "extract_catering_fields",
    "detect_quote_acceptance",
    "parse_month_day_event_date", "parse_numeric_event_date", "parse_event_date",
    "parse_headcount", "parse_headcount_from_signals",
    "parse_event_type", "parse_service_style", "parse_delivery_or_pickup",
    "parse_event_time", "parse_budget",
    "HEADCOUNT_PATTERNS", "MONTH_NUMBERS", "MONTH_DAY_RE",
]
