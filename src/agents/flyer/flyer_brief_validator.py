"""Deterministic fact authority for the Creative-Director `FlyerBrief` — the firewall.

Slice-1 of the Flyer Marketing Agent (design 2026-06-05, §5 step 2). The Hermes
skill proposes *structure*; THIS module — deterministic Python, never the skill —
decides truth:

  1. ``required_fact_ids(locked_facts)`` computes which visible facts the overlay
     MUST place — derived ENTIRELY from the locked facts (their own ``.required``
     flag + a deterministic item/price/offer/pricing supplement). It takes NO
     brief field: the model-authored ``request_intent`` must never be able to dodge
     a requirement (Codex BLOCKER #1).
  2. ``validate(brief, locked_facts, raw_request)`` checks every ``FactRef`` maps
     to a locked fact id OR a verified verbatim span of the request, scans the
     model-authored free-text fields for commercial values that bypassed
     ``fact_refs`` (Codex BLOCKER #3), enforces ``must_not_add`` carries no
     locked-fact value (containment, not exact-match — Codex #4), and fails closed
     if the brief omits a required fact. The skill can neither *invent* a fact
     (a/e) nor *omit* a required one (d).
  3. ``materialize_spans(brief, raw_request)`` turns each validated
     ``customer_text`` span into a real ``FlyerLockedFact(source="customer_text")``
     so the overlay renders ``required_fact_ids ∩ locked_facts``. Spans are NEVER
     rendered directly.

Import layout mirrors ``facts.py``: these scripts run on the VPS with
``/opt/shift-agent`` and ``src/platform`` on ``sys.path`` (flat ``from schemas
import ...``), with a package-style fallback for the repo-relative layout.
DORMANT in slice-1 — only reached when ``FLYER_CREATIVE_DIRECTOR_ENABLED=1``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from schemas import FlyerLockedFact

try:  # sibling FlyerBrief — flat on the VPS, package-style in the repo tree
    from flyer_brief import FactRef, FlyerBrief, OfferGroup  # type: ignore
except ImportError:  # pragma: no cover - import-path shim
    from agents.flyer.flyer_brief import FactRef, FlyerBrief, OfferGroup

# Reuse the bounded-creative-planner hard-fact firewall's claim detector (Codex
# Finding 3b): it already classifies service/operational/date/superlative claims
# ("Open Daily", "Free Delivery", "#1") that a textless background must never
# carry. Flat-on-VPS first, package-style fallback (mirrors the FlyerBrief import).
try:
    from creative_firewall import is_hard_fact_claim as _is_hard_fact_claim  # type: ignore
except ImportError:  # pragma: no cover - import-path shim
    try:
        from agents.flyer.creative_firewall import is_hard_fact_claim as _is_hard_fact_claim
    except ImportError:  # pragma: no cover - firewall unavailable ⇒ fail-closed mirror
        _is_hard_fact_claim = None  # type: ignore


# Required-fact authority is each fact's OWN ``.required`` flag (facts.py sets it
# via _fact(), default required=True for customer-stated content). NO parallel id
# allowlist/supplement lives here: it would drift from facts.py AND would force
# advisory planner suggestions (required=False) to render against owner intent
# (Codex #2). The validator trusts the facts' own required flag.


# ── free-text commercial-value detectors (Codex BLOCKER #3) ─────────────────
# Mirror the canonical visual_qa.py detectors (currency symbols, monetary-decimal
# shape, phone-run length) so the brief firewall and the OCR gate agree on what a
# "commercial value" looks like. Facts belong in fact_refs (overlay-rendered);
# the background is textless; the structure fields are not content. Any hit in a
# model-authored free-text field means a value rode in OUTSIDE fact_refs → reject.
_CURRENCY_AMOUNT_RE = re.compile(r"[$₹€£]\s*\d")
# Bare monetary decimal: "9.99", "49.99" — a price-shaped number with NO unit
# context. The (?<!...) / (?!...) guards mirror visual_qa._PRICE_AMOUNT_RE so a
# version string or a date fragment isn't mistaken for a price.
_BARE_PRICE_RE = re.compile(r"(?<![\w.])\d{1,4}\.\d{2}(?![\w.])")
_PERCENT_OFF_RE = re.compile(r"\d\s*%|\bpercent\b", re.IGNORECASE)
_DISCOUNT_CLAIM_RE = re.compile(
    r"\b(?:bogo|buy\s+one\s+get|free\b|discount|% ?off|flat\s+\d|save\s+\$?\d|cashback|combo\s+price)\b",
    re.IGNORECASE,
)
# Non-numeric discount/offer PHRASES — the offer word + its IMMEDIATE object word
# (capped at one, the discriminating head noun) so "free dessert" and "free delivery"
# are DISTINCT phrases that ground independently (operator round-4 BLOCKER 2). BOGO /
# "buy one get one [free]" are standalone multi-word offers. Used by the all-hits
# non-rendering / must_not_add commercial scan; each phrase is grounded as a WHOLE via
# ``_phrase_is_grounded`` so an invented "free dessert" cannot ride a locked "free
# delivery". (currency/percent/price/phone shapes are handled by the numeric token
# path; standalone invented-offer WORDS by ``_RESIDUAL_DISCOUNT_WORD_RE`` below.)
_DISCOUNT_OFFER_PHRASE_RE = re.compile(
    r"\b(?:bogo|buy\s+one\s+get(?:\s+one)?(?:\s+free)?)\b"
    r"|\b(?:free|complimentary|gift|bonus)(?:\s+[a-z][a-z'-]+)?",
    re.IGNORECASE,
)
# Residual standalone invented-offer-TYPE WORDS — GENUINE discount signals not already
# covered by the numeric-token / offer-phrase scans (operator round-5 BLOCKER). Each
# is matched ALL-HITS and grounded as a whole word via ``_phrase_is_grounded`` (a
# locked value containing the offer type grounds a planning mention of it; an invented
# "cashback" with no fact rejects). PRECISION (operator round-5 guard): "combo
# price" / "price" / "combo" are STRUCTURAL words (the real price is a grounded
# overlay fact), NOT discount CLAIMS — they are DELIBERATELY EXCLUDED here so the
# combo's offer_structure ("two combo cards", "combo price layout") is not over-
# blocked. Kept aggressive in ``_DISCOUNT_CLAIM_RE`` for the render-reaching
# background_brief HARD LINE (where "combo price" reaching pixels is a price claim).
#
# GENERIC "discount"/"discounted" is DELIBERATELY EXCLUDED from this NON-rendering
# residual scan (operator 2026-06-06 graduation fail-close): "discount" is a generic
# CATEGORY word that legitimately REFERS to a stated/grounded offer — a must_not_add
# entry "no prices other than the stated discount" against a locked
# pricing_structure="20% off entire order" is a valid suppression, NOT a smuggled
# commercial value, yet "discount" never appears verbatim in the locked value so the
# whole-word grounding can never clear it. Specific INVENTED discounts are still
# caught: numeric ones ("30% off", "$5") by the numeric-token scan, NAMED offer types
# (cashback / cash back / rebate / voucher / coupon) by this residual scan, and BOGO /
# "buy one get one" by the offer-phrase scan. The render-reaching background_brief
# HARD LINE keeps "discount" via ``_DISCOUNT_CLAIM_RE`` (a "discount" reaching pixels
# IS a price claim) — only this non-rendering residual detector drops the generic word.
#
# Operator's specific invented-offer-TYPE set (cashback, rebate, voucher, coupon) +
# the "cash back" spaced variant. BOGO / "buy one get one" are already all-hits via
# ``_DISCOUNT_OFFER_PHRASE_RE`` (scan 2). Intentionally NARROW — no generic "discount"/
# "discounted" (generic category word, handled above) and no "promo"/"promotion"/
# "deal"/"clearance" (those have benign creative/structural uses and are not invented
# offer types).
_RESIDUAL_DISCOUNT_WORD_RE = re.compile(
    r"\b(?:cashback|cash\s+back|rebate|voucher|coupon)\b",
    re.IGNORECASE,
)
# Phone-like contiguous digit run (mirror visual_qa._PHONE_RUN_RE shape): 8+ chars
# of digits + common separators with at least 7 actual digits.
_PHONE_RUN_RE = re.compile(r"[\d\s\-().+/]{8,}")
_DIGITS_RE = re.compile(r"\D+")

# ── address + date/time shape detectors (operator round-4 BLOCKER 1) ─────────
# The textless render-reaching background_brief is the HARD LINE for ALL fact shapes
# the overlay owns (contact/date/address reject grounded OR not — they must never be
# model-rendered into pixels). Commercial / locked-value / phone scans miss an
# INVENTED address ("123 Main St") and some invented date/time forms, so add explicit
# shape detectors used ONLY on the render-reaching path (NOT non-rendering fields —
# there a grounded contact/date resolves via fact_refs and never reaches pixels).
# Street-address shape: a street number + words + a street-type suffix; OR a unit
# designator + number ("Suite 200", "#42"). (No bare 5-digit ZIP rule — a lone
# number is too prone to creative false positives; the overlay owns the ZIP via the
# locked location fact, and the locked-value scan catches the real one.)
_ADDRESS_SHAPE_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9.'-]+\s+){0,4}"
    r"(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|way|ct|court|"
    r"pl|place|hwy|highway|pkwy|parkway|ter|terrace|cir|circle|sq|square|"
    r"suite|ste|unit|apt|fl|floor)\b\.?"
    r"|\b(?:suite|ste|unit|apt|fl|floor)\s+\d{1,6}\b"
    r"|#\s*\d{1,6}\b",
    re.IGNORECASE,
)
# Date / clock-time shapes the overlay owns (the schedule/promotion_end facts):
#   - month name + optional day + year ("June 15 2026", "Dec 2026", "December 1st");
#   - numeric date WITH year ("6/15/2026", "06-15-26", "2026-06-15");
#   - numeric date WITHOUT year ("6/15", "6-15", "06/15") — operator round-2 MINOR: the
#     overlay owns the promotion date, so a bare month/day shape must reject too.
#   - clock time ("9:00", "9:00 pm", "9 am").
#
# DATE vs RATIO separation is by MONTH BOUND (round-4, reverting round-3's denylist which
# hid real dates): a year-less N[/-]M is a date ONLY when N<=12 (plausible month) and
# M<=31 (plausible day). So "4/5","6/15","9/16","12/25" (N<=12) ARE dates → reject; while
# "16/9","21/9" (N>12) are NOT dates → not matched here (clear ratios). The genuinely
# ambiguous small cases ("4/5","9/16" = ratio OR April-5 / Sep-16) INTENTIONALLY reject —
# the render-reaching HARD LINE errs toward DATE, never toward allow (an allowed date is a
# leak). There is NO ratio denylist and NO pre-strip: those err toward allow and hide dates.
_MONTH_RE = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE_TIME_SHAPE_RE = re.compile(
    r"\b" + _MONTH_RE + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*(?:19|20)\d{2})?\b"
    r"|\b" + _MONTH_RE + r"\s+(?:19|20)\d{2}\b"
    r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
    r"|\b(?:19|20)\d{2}[/-]\d{1,2}[/-]\d{1,2}\b"
    r"|\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])\b"
    r"|\b\d{1,2}:\d{2}(?:\s*(?:am|pm))?\b"
    r"|\b\d{1,2}\s*(?:am|pm)\b",
    re.IGNORECASE,
)

# CLEAR-RATIO neutralizer for the background_brief OPERATIONAL scan ONLY (round-4). The
# firewall's broad slash pattern (``creative_firewall._CLAIM_PATTERNS``: ``\d{1,2}/\d{1,2}``)
# flags ANY N/M slash, including a clear aspect ratio like "16/9". To let the operator's
# named clear ratios ("16/9","21/9", N>12 → not dates) pass the operational scan (matching
# the date-shape detector, which already ignores N>12) WITHOUT hiding any date, price, or
# hours idiom, this is an EXPLICIT ALLOWLIST of widescreen ratios — NOT a numeric N>12 class.
# A class rule (N>12, single-digit M) would wrongly sweep in the hours idiom "24/7"; the
# explicit allowlist cannot. Everything else — possible dates ("4/5","6/15", N<=12), possible
# slash-prices ("16/10","16/99"), and "24/7"/other slashes — is UNTOUCHED and still rejects.
# Err-toward-reject preserved; no date/price/hours claim is hidden. SLASH-ONLY (round-5
# BLOCKER 3): the hyphen forms "16-9"/"21-9" are NOT exempt (a hyphen reads as a range/time,
# e.g. "16-9" could be "16 to 9"), so they reject via the broad scan; only "16/9","21/9" pass.
_CLEAR_RATIO_RE = re.compile(r"\b(?:16/9|21/9)\b")


def _address_shape_hit(text: str) -> str:
    """First street-address-shaped substring in ``text`` (number + words + street
    suffix, or a unit "#N"), or "" if none. Render-reaching HARD LINE only."""
    if not text:
        return ""
    m = _ADDRESS_SHAPE_RE.search(text)
    return m.group(0).strip() if m else ""


def _date_time_shape_hit(text: str) -> str:
    """First date/clock-time-shaped substring in ``text`` (month-day-year, numeric
    date, or clock time), or "" if none. Render-reaching HARD LINE only."""
    if not text:
        return ""
    m = _DATE_TIME_SHAPE_RE.search(text)
    return m.group(0).strip() if m else ""


def _norm_ws(value: str) -> str:
    """Whitespace-normalized, case-folded form for substring matching."""
    return " ".join((value or "").split()).casefold()


def _locked_ids(locked_facts: Iterable[FlyerLockedFact]) -> set[str]:
    return {f.fact_id for f in locked_facts or []}


def _commercial_value_hit(text: str) -> str:
    """Return the first commercial-value-looking substring in ``text`` (currency /
    bare price / percentage-discount / phone-like digit run), or "" if clean."""
    if not text:
        return ""
    for rx in (_CURRENCY_AMOUNT_RE, _BARE_PRICE_RE, _PERCENT_OFF_RE, _DISCOUNT_CLAIM_RE):
        m = rx.search(text)
        if m:
            return m.group(0).strip()
    for run in _PHONE_RUN_RE.findall(text):
        if len(_DIGITS_RE.sub("", run)) >= 7:
            return run.strip()
    return ""


# free-text fields whose text is passed to image generation (can reach pixels).
# Only ``background_brief`` is sent to the image model — ``_render_creative_director``
# calls ``_generate_image(background_brief)`` then overlays ONLY locked facts
# (bare_render.py ~586/602/608/767); ``offer_structure``/``layout_strategy``/
# ``grouping``/``visual_direction`` are planning metadata the renderer NEVER passes
# to image gen, so a value there cannot reach pixels. The commercial/textless scans
# stay STRICT for the fields in this set; for non-rendering fields only an INVENTED
# commercial value is blocked (a grounded one cannot reach pixels). Add any future
# field that is sent to the image model here.
_RENDER_REACHING_FIELDS = frozenset({"background_brief"})


# Full-token forms for the GROUNDED test (non-rendering free-text fields +
# must_not_add invented-commercial). The minimal ``_commercial_value_hit`` returns
# only the FIRST hit and truncates a percentage to the LAST digit ("30%" → "0%"),
# which (a) misses a SECOND commercial value after a grounded one, and (b) would
# falsely ground "30% off" against a locked "20% off ..." (both contain "0%"). For
# the grounded comparison we instead extract ALL FULL digit-bearing commercial tokens
# (whole percentage / currency-amount / bare price / phone digit-run) and require
# EACH to be contained in an OVERLAY-RENDERED locked value — so "30%" ≠ "20%", and a
# grounded value followed by an invented one is still rejected. This does NOT alter
# ``_commercial_value_hit`` (the strict render-reaching path keeps its exact
# behavior/messages; the must_not_add locked-value containment check is unchanged).
_FULL_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
_FULL_CURRENCY_RE = re.compile(r"[$₹€£]\s*\d+(?:\.\d{1,2})?")
_FULL_BARE_PRICE_RE = re.compile(r"(?<![\w.])\d{1,4}\.\d{2}(?![\w.])")


def _commercial_grounding_tokens(text: str) -> list[str]:
    """Whole digit-bearing commercial tokens in ``text`` (normalized) used ONLY by the
    non-rendering grounded test: full percentages, currency amounts, bare prices, and
    phone-like digit runs (>=7 digits). Whitespace-normalized so "20 %" == "20%"."""
    if not text:
        return []
    tokens: list[str] = []
    for rx in (_FULL_PERCENT_RE, _FULL_CURRENCY_RE, _FULL_BARE_PRICE_RE):
        for m in rx.finditer(text):
            tok = _norm_ws(m.group(0)).replace(" ", "")
            if tok:
                tokens.append(tok)
    for run in _PHONE_RUN_RE.findall(text):
        digits = _DIGITS_RE.sub("", run)
        if len(digits) >= 7:
            tokens.append(digits)
    return tokens


def _phrase_is_grounded(phrase: str, allowed_values: Sequence[str]) -> bool:
    """True iff ``phrase`` (a non-numeric word/phrase — a discount word like "free"
    or an operational claim like "free delivery") appears as a WHOLE word/phrase in
    some OVERLAY-RENDERED locked value. Boundary-aware (alphanumeric-aware, mirrors
    the must_not_add containment guards) so a bare "free" grounds against a locked
    "free delivery on all orders" but NOT against "Freedom Cafe". Used for the non-
    rendering grounded-or-rejected scan (non-numeric commercial words + operational
    claims); numeric commercial tokens use ``_token_is_grounded`` instead."""
    norm = _norm_ws(phrase)
    if not norm:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(norm) + r"(?![a-z0-9])"
    return any(re.search(pattern, lv) for lv in allowed_values)


def _token_is_grounded(token: str, allowed_values: Sequence[str]) -> bool:
    """True iff a single whole commercial ``token`` (normalized) is contained in some
    OVERLAY-RENDERED locked value. Digit-runs (phone) also match on bare digits so a
    phone grounds against the locked number whatever its separators; currency / full-
    percent / bare-price tokens keep their symbol (so "30%" ≠ "20%")."""
    digit_tok = _DIGITS_RE.sub("", token)
    for lv in allowed_values:
        if token in lv:
            return True
        if len(digit_tok) >= 7 and digit_tok in _DIGITS_RE.sub("", lv):
            return True
    return False


def _first_ungrounded_commercial(text: str, allowed_values: Sequence[str]) -> str:
    """The first INVENTED (ungrounded) commercial value in ``text``, or "" if every
    commercial value is grounded in an OVERLAY-RENDERED locked value. "" is also
    returned for text with NO commercial content (clean).

    ALL-HITS (Codex BLOCKERs + operator rounds 4-5): a field/entry passes ONLY when
    EVERY commercial value it carries is grounded — a grounded value FOLLOWED by an
    invented one (e.g. "20% off and $5 off", "20% off and BOGO free", "20% off and
    cashback") is rejected on the invented one. THREE INDEPENDENT, UNCONDITIONAL,
    ALL-HITS scans (each its own ``finditer``, each grounded via ``_phrase_is_grounded``
    / ``_token_is_grounded``, NONE gated behind the others — operator round-5):
      - (1) whole digit-bearing tokens (full percentage / currency / bare price /
        phone digit-run) each grounded via ``_token_is_grounded`` (NOT a loose
        substring — "30%" does not ground against a locked "20%");
      - (2) non-numeric discount/offer PHRASES (free X / complimentary X / gift X /
        bonus X / BOGO / "buy one get one") each grounded as a WHOLE PHRASE — so an
        invented "free dessert" does NOT ride a locked "free delivery", and a grounded
        "free delivery" is not double-rejected;
      - (3) residual standalone invented-offer-TYPE WORDS (cashback / cash back /
        rebate / voucher / coupon — ``_RESIDUAL_DISCOUNT_WORD_RE``) each grounded as a
        whole word; UNCONDITIONAL (not gated behind a "no numeric token" guard), so
        "20% off and cashback" rejects on "cashback" even though "20% off" grounds.
        GENERIC "discount"/"discounted" is EXCLUDED (it refers to a stated/grounded
        offer, not a smuggled value — see ``_RESIDUAL_DISCOUNT_WORD_RE``); specific
        invented discounts are still caught numerically (scan 1), as named types here,
        or as BOGO (scan 2). Structural words ("combo price"/"price"/"combo") are
        EXCLUDED from this set so
        the combo is not over-blocked.
    The first ungrounded value across the three scans is returned. Used by BOTH the
    non-rendering free-text scan AND the must_not_add invented-commercial check so the
    two agree on grounding; the strict render-reaching path and the must_not_add
    locked-value containment check are unaffected."""
    if not text:
        return ""
    # (1) numeric commercial tokens — all-hits, token-anchored.
    for tok in _commercial_grounding_tokens(text):
        if not _token_is_grounded(tok, allowed_values):
            return tok
    # (2) non-numeric discount/offer phrases — all-hits, whole-phrase grounded.
    for phrase in (" ".join(m.group(0).split())
                   for m in _DISCOUNT_OFFER_PHRASE_RE.finditer(text)):
        if phrase and not _phrase_is_grounded(phrase, allowed_values):
            return phrase
    # (3) residual standalone invented-offer words — all-hits, UNCONDITIONAL, whole-
    # word grounded (operator round-5: no "no numeric token" gate, no first-hit).
    for word in (" ".join(m.group(0).split())
                 for m in _RESIDUAL_DISCOUNT_WORD_RE.finditer(text)):
        if word and not _phrase_is_grounded(word, allowed_values):
            return word
    return ""


def scrub_ungrounded_commercial_taste(
    theme: str, mood: str, allowed_values: Sequence[str]
) -> tuple[str, str]:
    """Scrub a (theme_family, mood) pair of any UNGROUNDED commercial value.

    ``theme_family`` and ``mood`` are VISUAL-TASTE strings (e.g. "South Indian
    Weekend Feast", "warm and festive") — but a model could smuggle a fabricated
    COMMERCIAL value through either (e.g. ``mood="$5 off"``), and these strings can
    reach the image prompt. The strict ``fact_refs`` firewall never scans them, so
    each is scanned here for the FIRST UNGROUNDED commercial value (one NOT present
    in ``allowed_values``) via ``_first_ungrounded_commercial`` — the SAME single-
    source-of-truth scanner the brief firewall uses, NO parallel commercial regex.
    A field that carries an ungrounded commercial value is defaulted to ``""``; a
    field whose only commercial value IS in ``allowed_values`` is GROUNDED and kept
    (so ``"$8.99 hero"`` is not over-stripped). With an EMPTY ``allowed_values`` any
    commercial value is ungrounded by definition (advisory scene themes carry no
    grounded numbers), so all commercial taste is stripped.

    Shared by the CD v2 deterministic resolver (``flyer_creative_resolver``) and the
    advisory scene path (``flyer_context_builder.advise_scene_direction``).

    NEVER raises: a non-str input coerces to ``""``; any scanner error fail-closes
    the offending field to ``""`` rather than letting an unscanned value through."""
    safe_theme = theme if isinstance(theme, str) else ""
    safe_mood = mood if isinstance(mood, str) else ""
    allowed = list(allowed_values or ())
    if safe_theme and not _taste_value_is_clean(safe_theme, allowed):
        safe_theme = ""
    if safe_mood and not _taste_value_is_clean(safe_mood, allowed):
        safe_mood = ""
    return safe_theme, safe_mood


def _taste_value_is_clean(value: str, allowed_values: Sequence[str]) -> bool:
    """True iff ``value`` carries NO UNGROUNDED commercial value (safe to keep).
    Guarded so any scanner error defaults to NOT-clean (fail-closed: strip the
    field) rather than letting an unscanned value reach the image prompt."""
    try:
        return not _first_ungrounded_commercial(value, allowed_values)
    except Exception:  # pragma: no cover - defensive: scanner error => strip the field
        return False


# ── scoped-scrub narrative firewall (CD v2 Slice B, Task B0.3) ──────────────
# ``campaign_narrative`` is a model-authored marketing message that RENDERS
# prominently above the hero — the ONLY new fabrication surface in CD v2 Slice B.
# The operator approved Option B (a SCOPED scrub, NOT the full strict battery):
# keep evocative-but-grounded marketing language ("South Indian Favorites at One
# Price", "Weekend Feast of Family Favorites"), reject fabrication (prices/
# discounts/percentages not in facts, "today only"/"limited time", delivery/
# operational/scheduling claims, awards/rankings/superlatives), and on reject
# DEFAULT to the campaign_title.
#
# Why a scoped composition and NOT the full ``validate`` battery (the whole point
# of Option B): the render-reaching ``_operational_claim_hit`` /
# ``_occasion_aware_operational_claim_hit`` HARD-LINE detector OVER-REJECTS the
# operator's ALLOW list — it flags bare date/occasion-overlap words ("weekend" in
# "Weekend Feast of Family Favorites", "One-Price Weekend Treats", "weekend
# treats") and fail-closes when the broad classifier is absent. Marketing language
# legitimately evokes the occasion, so the narrative MUST NOT be subjected to that
# aggressive scan. Instead the scoped scrub composes the NARROWEST set that
# satisfies the operator's exact ALLOW/REJECT lists, REUSING the existing scanners:
#   - ``_first_ungrounded_commercial`` — prices / currency / bare prices /
#     percentage-discounts / free-X offer phrases, token-anchored + GROUNDING-aware
#     (so a grounded "$7.99" survives but "$5 off" / "50% off" / an ungrounded
#     "$9.99" reject). This is the SAME single-source-of-truth commercial scanner
#     ``scrub_ungrounded_commercial_taste`` uses — NO parallel commercial regex.
#   - ``_first_ungrounded_operational`` — the PRECISE strict operational detector
#     (genuine service/availability/hours/credential claims, NOT theme/celebration
#     words), grounding-aware (so a grounded "free delivery" survives, an invented
#     one rejects). The aggressive ``_operational_claim_hit`` is DELIBERATELY NOT
#     used (it over-rejects the ALLOW list, per above).
#   - ``_scheduling_claim_hit`` — scheduling/availability/booking claims ("tables
#     available", "book now", "reserve", "until/till", "this/every weekend").
#   - an EXPLICIT superlative/award/ranking + time-pressure phrase set
#     (``_NARRATIVE_SUPERLATIVE_RE`` / ``_NARRATIVE_TIME_PRESSURE_RE``) for the
#     specific REJECT tokens the broad scanners MISS without over-rejecting the
#     ALLOW list: "best"/"#1"/"number one"/"voted"/"top-rated"/"finest"/"greatest"/
#     "award-winning" and "today only"/"limited time"/"act now"/"hurry"/"while
#     supplies last". These are required because the strict operational regex only
#     catches "best <in town/seller/...>" (context-bound), so the operator's "best
#     biryani in town" and bare superlatives would otherwise slip through — yet a
#     blanket "best" ban is fine here (no ALLOW phrase contains any of these tokens,
#     verified). The award/ranking subset DOUBLES the strict detector's coverage
#     (intentional belt-and-suspenders; matching either rejects).
#
# REJECT → return campaign_title. The aggressive render-reaching scan, the strict
# ``validate`` logic, and every existing scanner's behavior are UNCHANGED.

# EXPLICIT superlative / award / ranking phrase set (the specific REJECT tokens the
# strict operational detector misses out of context, e.g. bare "best", "#1",
# "voted", "top-rated", "finest", "greatest"). Verified to NOT match any operator
# ALLOW phrase (none contain these tokens). ``#1`` is matched without a leading
# word-boundary (``\b`` does not anchor before ``#``).
_NARRATIVE_SUPERLATIVE_RE = re.compile(
    r"#\s*1\b"
    r"|\b(?:best|finest|greatest|top[\s-]?rated|number\s+one|"
    r"award[\s-]?winning|award[\s-]?winner|voted)\b",
    re.IGNORECASE,
)
# EXPLICIT time-pressure phrase set ("today only" / "limited time" / "act now" /
# "hurry" / "while supplies last" + a few close cousins). None appear in the ALLOW
# list. The grounded-occasion words the ALLOW list DOES use ("weekend", "festive",
# "celebration") are intentionally absent here. Matched against the NORMALIZED
# narrative (``_narrative_normalize``: lowercased, hyphens/underscores → spaces,
# whitespace collapsed) so hyphenated forms ("limited-time", "today-only",
# "while-supplies-last") match the same as the spaced forms.
_NARRATIVE_TIME_PRESSURE_RE = re.compile(
    r"\b(?:today\s+only|limited\s+time|act\s+now|hurry|"
    r"while\s+supplies\s+last|last\s+chance|ends?\s+(?:today|tonight|soon)|"
    r"ends\s+soon|don'?t\s+miss|for\s+a\s+limited|this\s+week\s+only|"
    r"now\s+or\s+never)\b",
    re.IGNORECASE,
)
# EXPLICIT sale/discount/offer-claim WORD set (FIX 1 — Codex BLOCKER). The narrative
# firewall's commercial scanner (``_first_ungrounded_commercial``) intentionally
# excludes generic claim WORDS like "discount"/"deal"/"promo"/"sale"/"clearance"
# (it catches only numeric VALUES), so a wordy narrative ("Weekend Discount Feast",
# "Today-only Promo", "BOGO Dosa") slipped through. The operator's reject list
# explicitly forbids discounts/promos/sales, so the scoped narrative scrub adds an
# EXPLICIT whole-word/phrase ban here. Matched against the NORMALIZED narrative so
# hyphenated forms ("combo-deal", "%-off") match too. CRITICAL: "specials" is NOT a
# sale-word (operator ALLOW list: "Weekend Specials", "Clearance Specials" rejects
# only on "clearance"); "combo"/"price"/"feast"/"treats"/"favorites"/"festive"/
# "authentic" are NOT here so the grounded-evocative ALLOW list survives.
_NARRATIVE_SALE_WORD_RE = re.compile(
    r"\b(?:discount|discounted|deal|deals|promo|promotion|promotional|"
    r"sale|clearance|markdown|bogo|"
    r"buy\s+one\s+get|buy\s+1\s+get|combo\s+deal)\b"
    r"|%\s*off|\bpercent\s+off\b|\bcents\s+off\b|\bdollars\s+off\b",
    re.IGNORECASE,
)


def _narrative_normalize(text: str) -> str:
    """Lowercase + hyphens/underscores → spaces + collapsed whitespace (FIX 1).

    The narrative phrase sets (sale-word / time-pressure / superlative) are matched
    against this normalized form so a hyphenated/underscored form ("limited-time",
    "today_only", "award-winning", "top-rated", "while-supplies-last") matches the
    SAME as the spaced form. Used ONLY for the narrative phrase scans — never for
    the commercial/operational scanners (which keep their own normalization)."""
    if not text:
        return ""
    lowered = text.casefold().replace("-", " ").replace("_", " ")
    return " ".join(lowered.split())


def _narrative_sale_word_hit(text: str) -> str:
    """First explicit sale/discount/offer-claim WORD in ``text`` (FIX 1 — the WORDS
    the numeric-only commercial scanner misses: discount/deal/promo/sale/clearance/
    BOGO/% off/…), or "". Scanned on the NORMALIZED narrative so hyphenated forms
    ("combo-deal", "%-off") match the spaced forms."""
    if not text:
        return ""
    m = _NARRATIVE_SALE_WORD_RE.search(_narrative_normalize(text))
    return m.group(0).strip() if m else ""


def _narrative_superlative_hit(text: str) -> str:
    """First explicit superlative / award / ranking token in ``text`` (the REJECT
    tokens the strict operational detector misses out of context), or "". Scanned on
    the NORMALIZED narrative so "award-winning"/"top-rated" match the spaced forms."""
    if not text:
        return ""
    m = _NARRATIVE_SUPERLATIVE_RE.search(_narrative_normalize(text))
    return m.group(0).strip() if m else ""


def _narrative_time_pressure_hit(text: str) -> str:
    """First explicit time-pressure phrase in ``text`` ("today only" / "limited
    time" / "act now" / "hurry" / "while supplies last" / …), or "". Scanned on the
    NORMALIZED narrative so "limited-time"/"today-only" match the spaced forms."""
    if not text:
        return ""
    m = _NARRATIVE_TIME_PRESSURE_RE.search(_narrative_normalize(text))
    return m.group(0).strip() if m else ""


def scrub_campaign_narrative(
    narrative: str,
    *,
    allowed_values: Sequence[str],
    campaign_title: str,
) -> str:
    """Scoped-scrub the model-authored ``campaign_narrative`` (CD v2 Slice B, B0.3).

    ``campaign_narrative`` is a marketing message that RENDERS prominently — the
    only new fabrication surface in Slice B. This is the operator-approved Option B
    SCOPED scrub: keep evocative-but-grounded marketing language, reject
    fabrication, and on reject default to ``campaign_title``.

    Returns:
      - ``""`` if ``narrative`` is empty / blank (nothing to render);
      - ``campaign_title`` if ``narrative`` contains ANY of:
          * an UNGROUNDED commercial value (price / discount / percentage / free-X
            offer not present in ``allowed_values``) — ``_first_ungrounded_commercial``;
          * an explicit sale/discount/offer-claim WORD (discount / deal / promo /
            sale / clearance / BOGO / % off / …) — ``_narrative_sale_word_hit``
            (FIX 1: the WORDS the numeric-only commercial scanner misses);
          * an UNGROUNDED genuine operational / delivery claim —
            ``_first_ungrounded_operational`` (PRECISE detector, grounding-aware);
          * a scheduling / availability / booking claim — ``_scheduling_claim_hit``;
          * an explicit superlative / award / ranking token —
            ``_narrative_superlative_hit``;
          * an explicit time-pressure phrase ("today only" / "limited time" / …) —
            ``_narrative_time_pressure_hit``;
        (when ``campaign_title`` is itself empty/absent the reject default is ``""`` —
        there is nothing safe to show);
      - otherwise the ``narrative`` unchanged.

    A GROUNDED value is fine: "Everything at $7.99" survives when "$7.99" is in
    ``allowed_values`` (the commercial scan is grounding-aware); "Free delivery on
    all orders" survives when grounded. An UNGROUNDED "$9.99" rejects even when
    "$7.99" is grounded (token-anchored).

    PURE; NEVER raises: a non-str ``narrative`` coerces to ``""``; any scanner
    error fail-closes to ``campaign_title`` (or ``""`` when the title is absent)
    rather than letting an unverified narrative through. REUSES the existing
    scanners — no new commercial regex; the aggressive render-reaching detector is
    DELIBERATELY not used (it over-rejects the grounded marketing ALLOW list)."""
    safe_narrative = narrative if isinstance(narrative, str) else ""
    safe_narrative = safe_narrative.strip()
    safe_title = campaign_title if isinstance(campaign_title, str) else ""
    if not safe_narrative:
        return ""
    allowed = [v for v in (allowed_values or ()) if isinstance(v, str)]
    try:
        if _first_ungrounded_commercial(safe_narrative, allowed):
            return safe_title
        if _narrative_sale_word_hit(safe_narrative):
            return safe_title
        if _first_ungrounded_operational(safe_narrative, allowed):
            return safe_title
        if _scheduling_claim_hit(safe_narrative):
            return safe_title
        if _narrative_superlative_hit(safe_narrative):
            return safe_title
        if _narrative_time_pressure_hit(safe_narrative):
            return safe_title
    except Exception:  # pragma: no cover - defensive: cannot prove clean ⇒ default
        return safe_title
    return safe_narrative


# ── textless-background firewall (Codex Finding 3) ──────────────────────────
# background_brief / visual_direction are the TEXTLESS-background prompt: the model
# must render NO words there (all visible text is overlaid deterministically from
# locked facts). Two escape hatches the price/phone/identity scans miss:
#   (a) an instruction to render arbitrary text ("a sign reading 'Open Daily'", "a
#       banner that says ...", "text reading ...") + any quoted literal; and
#   (b) an invented non-price operational CLAIM ("open daily", "delivery available",
#       "now hiring", "fresh daily").
# Both are deterministic + fail-closed; the textless rule is design-critical.
_TEXT_RENDER_INSTRUCTION_RE = re.compile(
    r"\b(?:sign|signs|banner|banners|text|texts|label|labels|caption|captions|"
    r"word|words|letter|letters|message|writing|slogan|headline|title|placard|"
    r"poster|billboard|menu\s*board|chalkboard|marquee|ticker|subtitle)\b"
    r"\s+(?:that\s+)?(?:reading|read|reads|saying|say|says|that\s+says|spelling|"
    r"spells|spelled|written|writes|displaying|displays|showing|shows|with\s+the\s+"
    r"words?|with\s+text|containing\s+the\s+(?:words?|text))\b",
    re.IGNORECASE,
)
# Any quoted literal of length>=3 (straight or curly quotes) is a verbatim string
# the model is being told to render — never allowed in a textless prompt.
_QUOTED_LITERAL_RE = re.compile(r"""['"‘’“”]\s*([^'"‘’“”]{3,})\s*['"‘’“”]""")
# Invented operational/service claims (mirror of creative_firewall's claim classes,
# used only if that module is not importable — kept small + fail-closed). These are
# NON-price claims; the commercial-shape scan already covers price/phone/discount.
# NB: bare open/opens/opening are intentionally NOT in this blanket list — "open"
# is context-dependent ("an open central area" is a LAYOUT instruction, not a
# business-hours claim; live false positive 2026-06-05). It is classified by
# creative_firewall's context-aware `_open_is_operational` via `_open_claim_hit`
# below, so "now open"/"open daily"/"grand opening" stay caught while compositional
# uses pass. closed/closes/closing/hours remain unconditional (no layout sense).
_OPERATIONAL_CLAIM_RE = re.compile(
    r"\b(?:closed|closes|closing|hours|daily|"
    r"delivery|takeout|take[\s-]?out|takeaway|dine[\s-]?in|curbside|pickup|"
    r"hiring|now\s+hiring|fresh|freshly|guarantee|guaranteed|best|#\s*1|"
    r"number\s+one|award|award[\s-]?winning|certified|licensed|insured|"
    r"voted|family[\s-]?owned|since\s+\d{4})\b",
    re.IGNORECASE,
)

# Context-aware "open" claim detector (reuses creative_firewall's classifier so
# the brief firewall and planner item-name firewall agree on what "open" means).
# Flat-on-VPS first, package-style fallback, then a small inline mirror so the
# textless rule never silently loses "open" coverage if the module is absent.
try:  # pragma: no cover - import-path shim (mirrors _is_hard_fact_claim above)
    from creative_firewall import _open_is_operational as _cf_open_is_operational  # type: ignore
    from creative_firewall import _OPEN_TOKEN_RE as _cf_open_token_re  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from agents.flyer.creative_firewall import (  # type: ignore
            _open_is_operational as _cf_open_is_operational,
            _OPEN_TOKEN_RE as _cf_open_token_re,
        )
    except ImportError:  # pragma: no cover - firewall unavailable ⇒ fail-closed mirror
        _cf_open_token_re = re.compile(r"\b(?:re[- ]?)?open(?:ing|ed|s)?\b", re.IGNORECASE)
        _cf_open_is_operational = None  # type: ignore


# Sentinel returned when the broad claim classifier is unavailable/raising — a
# non-empty hit so the textless-background field is treated as carrying a claim
# (fail-closed), never silently accepted (Codex round-4 MAJOR).
_CLAIM_CLASSIFIER_UNAVAILABLE = "operational_claim_classifier_unavailable"


def _open_claim_hit(text: str) -> str:
    """First operational "open" claim in ``text`` (context-aware), or "" if the
    only "open" uses are compositional ("open central area", "left open"). Defers
    to creative_firewall._open_is_operational; if that is unavailable, fail closed
    (treat any "open" token as a claim) so the textless rule never weakens."""
    if not text:
        return ""
    m = _cf_open_token_re.search(text)
    if not m:
        return ""
    if _cf_open_is_operational is None:
        return m.group(0).strip()  # fail-closed: no classifier ⇒ block any "open"
    return m.group(0).strip() if _cf_open_is_operational(text) else ""


def _all_open_claim_hits(text: str) -> list[str]:
    """ALL operational "open" claims in ``text`` (context-aware), in order — the
    all-hits sibling of ``_open_claim_hit`` (operator round-4 MAJOR). Each "open"
    token is classified on its OWN LOCAL window (the open word + a few following
    words), so "open daily and open until 10" yields BOTH "open daily" and "open
    until 10" rather than folding to the first. A compositional "open" ("open central
    area") in the same text contributes nothing. If the context classifier is
    unavailable, fail closed: every "open" token is returned (the textless rule never
    weakens)."""
    if not text:
        return []
    hits: list[str] = []
    for m in _cf_open_token_re.finditer(text):
        # local window: from this open token to the end of the next ~3 tokens, which
        # is enough to carry an anchored open-phrase ("open daily", "open until 10",
        # "open 9am") or a co-occurring clock time.
        tail = text[m.start():]
        window = " ".join(tail.split()[:4])
        if _cf_open_is_operational is None:
            hits.append(m.group(0).strip())  # fail-closed
            continue
        if _cf_open_is_operational(window):
            hits.append(window)
    return hits


def _text_render_instruction_hit(text: str) -> str:
    """First text-into-background-rendering instruction or quoted literal in ``text``
    (Codex Finding 3a), or "" if clean. A textless-background prompt must never tell
    the model to render words."""
    if not text:
        return ""
    m = _TEXT_RENDER_INSTRUCTION_RE.search(text)
    if m:
        return m.group(0).strip()
    q = _QUOTED_LITERAL_RE.search(text)
    if q:
        return q.group(0).strip()
    return ""


def _operational_claim_hit(text: str) -> str:
    """First invented operational/service claim in ``text`` (Codex Finding 3b), or ""
    if clean. Prefers creative_firewall.is_hard_fact_claim (already classifies these
    claim classes); falls back to a small fail-closed list if that module is absent.
    Commercial SHAPE (price/phone/discount) is owned by ``_commercial_value_hit`` —
    this catches the NON-price claims a textless background must not assert."""
    if not text:
        return ""
    m = _OPERATIONAL_CLAIM_RE.search(text)
    if m:
        return m.group(0).strip()
    # "open" is classified by context (compositional "open central area" passes;
    # operational "now open"/"open daily"/"grand opening" is flagged). Done before
    # the broad fallback so a compositional "open" is not re-flagged by it.
    open_hit = _open_claim_hit(text)
    if open_hit:
        return open_hit
    # Defense-in-depth: reuse the planner firewall's broader claim taxonomy
    # (service/legal/payment/availability claims not in the small list). This is
    # FAIL-CLOSED (Codex round-4 MAJOR): if the broad classifier is unavailable
    # (import missing) OR raises, we cannot prove the text is claim-free, so we
    # return a non-empty sentinel — the textless-background field is treated as
    # carrying a claim (reject + retry) rather than silently accepted. Mirrors the
    # fail-closed posture of `_open_claim_hit` when its classifier is missing.
    if _is_hard_fact_claim is None:
        return _CLAIM_CLASSIFIER_UNAVAILABLE
    try:
        if _is_hard_fact_claim(text):
            return text.strip()[:60]
    except Exception:  # the firewall must never crash validation — fail closed.
        return _CLAIM_CLASSIFIER_UNAVAILABLE
    return ""


# ── occasion-aware exemption for the background_brief HARD LINE (operator 2026-06-06) ─
# NARROW fix (Option A, round 2): the render-reaching ``background_brief`` operational/
# date-claim scan over-flags the GROUNDED OCCASION THEME. A campaign whose occasion is
# literally "Memorial Day Weekend" (the grounded ``campaign_title``) cannot describe its
# own theme in the textless background prompt, because the date/schedule class in
# ``creative_firewall._CLAIM_PATTERNS`` matches the token "weekend" (weekday / month
# tokens too) regardless of whether it is the grounded occasion. Describing the occasion
# theme is legitimate creative direction — the image renders a textless scene; "No words/
# lettering anywhere" keeps it textless — so a GROUNDED DATE/OCCASION-CLASS token is
# exempted from the operational claim scan, and ONLY there.
#
# The exemption is GROUNDED-OCCASION-ONLY and TIGHTLY CLASSED. It does NOT weaken any
# other fact authority:
#   - the exempt token set is derived ONLY from facts whose fact_id is an occasion/theme
#     id (``_is_occasion_theme_fact_id`` → campaign_title + theme_*/occasion*); schedule,
#     promotion_end, prices, discounts, phones, addresses, items/offers are NEVER a source;
#   - a campaign_title word is exemptable ONLY if it is a DATE/OCCASION-CLASS token
#     (``_DATE_OCCASION_CLASS_RE``: weekend(s)/holiday(s)/eve/season(al), weekday names,
#     month names, occasion proper-nouns). Operational/service/commercial words
#     (delivery/sale/best/award/fresh/free/open/now/hours/…) are NEVER exempted even when
#     they appear in campaign_title (round-2 BLOCKER 1): "Free Delivery Weekend" exempts
#     ONLY "weekend"; "free"/"delivery" still reject;
#   - if the field carries ANY scheduling/availability/booking claim (``_FIELD_SCHEDULING
#     _CLAIM_RE``, run on the ORIGINAL text) — "available …", "book …", "reserve …",
#     "weekend availability", "sale ends … weekend", "this/all/every/each weekend",
#     "until/till …", "hours" — NOTHING is stripped: the UNCHANGED detector runs on the
#     original and rejects (round-2 BLOCKER 2). A bare layout "open central area" is NOT a
#     scheduling claim (B1 classifies "open" contextually), so it does not field-veto;
#   - explicit date/time SHAPES ("June 15", "6/15", "Friday 6 PM", "9:00") stay strict via
#     the separate ``_date_time_shape_hit`` render-block scan (ORIGINAL text), and the
#     month/clock patterns also still fire on the un-neutralized shape;
#   - the commercial scan, locked-value (non-occasion) scan, address-shape, phone, and
#     text-render scans on background_brief are all untouched; the non-rendering scan,
#     fact_refs coverage, offer_groups, must_not_add, and the fail-closed posture are all
#     untouched.
# Mechanism: field-gate then per-occurrence — if the ORIGINAL field carries a scheduling
# claim, do not exempt at all; otherwise neutralize each grounded DATE/OCCASION-CLASS token
# occurrence and run the UNCHANGED ``_operational_claim_hit`` on the residual. Anything
# else (a real claim, an invented date token, a non-classed word) still fires. The
# exemption can never turn a real claim into a pass.

# Word tokenizer for occasion-phrase splitting (apostrophe/hyphen-aware so "memorial day"
# splits cleanly and "we're" / "new year's" stay coherent tokens).
_CONTEXT_WORD_RE = re.compile(r"[a-z0-9]+(?:['’-][a-z0-9]+)*", re.IGNORECASE)

# DATE/OCCASION-CLASS token vocabulary (round-2 BLOCKER 1) — the ONLY token class an
# occasion fact may contribute to the exemption set. Strictly date/calendar + occasion
# proper-nouns; NO operational/service/commercial words. Defined ONCE as a shared
# alternation fragment so the exemption allowlist (``_DATE_OCCASION_CLASS_RE``) and the
# authoritative "open <date/occasion>" detector (``_OPEN_DATE_OCCASION_RE``, round-5
# BLOCKER 2) use IDENTICAL vocabulary and cannot drift.
_DATE_OCCASION_TOKEN_ALT = (
    # generic date/occasion category words
    r"weekend|weekends|holiday|holidays|eve|season|seasonal|"
    # days of week (full + common abbreviations)
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|"
    # months (full + abbreviations)
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|"
    # occasion proper-noun words (the named-holiday vocabulary)
    r"memorial|independence|labor|labour|thanksgiving|christmas|halloween|easter|"
    r"diwali|hanukkah|chanukah|kwanzaa|ramadan|eid|lunar|valentine|valentines|"
    r"mother|mothers|father|fathers|patriot|patriots|veterans|presidents|"
    r"cinco|mayo|juneteenth|oktoberfest|new|years|year"
)
# A campaign_title word that is flagged by the operational scan but is NOT in this class
# (delivery/sale/best/award/fresh/free/open/now/hours/…) is therefore NEVER exempted.
_DATE_OCCASION_CLASS_RE = re.compile(r"^(?:" + _DATE_OCCASION_TOKEN_ALT + r")$", re.IGNORECASE)

# AUTHORITATIVE "open <date/occasion token>" detector (round-5 BLOCKER 2). Checked on the
# ORIGINAL background_brief BEFORE any occasion-token stripping, so "open Memorial Day
# weekend" / "open weekend" / "open Saturday" / "opens Saturday" REJECT even when the
# occasion is grounded (stripping "weekend" would else leave "open Memorial Day" → the
# context-aware "open" classifier reads it as benign → leak). "open central area" stays
# benign ("central" is not a date/occasion token). Tight adjacency: ``open`` (optional
# re-/-ing/-s) + at most two scheduling connectors (this/that/next/every/all/each/on/the/
# for) + a DATE/OCCASION-class token — so a non-connector word ("layout") breaks the match
# ("open layout for Memorial Day" stays benign).
_OPEN_DATE_OCCASION_RE = re.compile(
    r"\b(?:re[- ]?)?open(?:s|ing|ed)?\s+"
    r"(?:(?:this|that|next|every|all|each|on|the|for)\s+){0,2}"
    r"(?:" + _DATE_OCCASION_TOKEN_ALT + r")\b",
    re.IGNORECASE,
)

# FIELD-LEVEL scheduling/availability/booking-claim detector (round-2 BLOCKER 2). Run on
# the ORIGINAL background_brief text: a hit VETOES the whole exemption for the field (so a
# grounded occasion token cannot launder a scheduling claim like "available for Memorial
# Day weekend"). Operator-specified vocabulary; precise — bare "open" is NOT here (B1
# classifies "open" contextually) so a layout "open central area" does not field-veto.
# BARE "available" is INTENTIONAL (round-4 BLOCKER 2 revert): every availability phrasing
# ("spots available", "tables available", "available for X", "X availability", "is
# available") must veto. The contrived creative "available warm light" rejecting is
# ACCEPTED hard-line strictness — an availability word in the pixel-reaching prompt errs
# toward reject; the model does not realistically write it and the combo/graduation never
# contain it.
_FIELD_SCHEDULING_CLAIM_RE = re.compile(
    r"\b(?:available|availability|book|booking|reserve|reservation)\b"
    r"|\bsale\s+ends\b"
    r"|\bends?\b.{0,15}\bweekend\b"
    r"|\b(?:until|till)\b"
    r"|\bhours\b"
    r"|\b(?:this|all|every|each)\s+weekend\b",
    re.IGNORECASE,
)


def _scheduling_claim_hit(text: str) -> str:
    """The first scheduling/availability/booking-claim substring in ``text`` (round-5
    BLOCKER 1), or "" if none. This is AUTHORITATIVE on the render-reaching background_brief:
    a hit REJECTS the field directly (not merely gates occasion stripping) — so a bare
    availability claim ("spots available", "tables available", "available now") that
    ``_operational_claim_hit`` does not independently catch can never pass. Scanned on the
    ORIGINAL text."""
    if not text:
        return ""
    m = _FIELD_SCHEDULING_CLAIM_RE.search(text)
    return m.group(0).strip() if m else ""


def _has_scheduling_claim(text: str) -> bool:
    """True iff the field text carries a scheduling/availability/booking claim. Used to VETO
    the occasion exemption (round-2 BLOCKER 2) AND, at the background_brief call site, to
    REJECT the field outright (round-5 BLOCKER 1). Scanned on the ORIGINAL text."""
    return bool(_scheduling_claim_hit(text))


def _occasion_claim_tokens(occasion_phrases: Sequence[str]) -> set[str]:
    """The DATE/OCCASION-CLASS claim-tokens that live INSIDE grounded occasion phrases —
    the ONLY tokens the background_brief operational scan will exempt.

    Derived from the grounded occasion phrases ALONE (campaign_title + theme_*/occasion*
    values). A word is exempt ONLY when it is BOTH (a) flagged by the operational/date scan
    AND (b) a DATE/OCCASION-CLASS token (``_DATE_OCCASION_CLASS_RE``). For "memorial day
    weekend" this is exactly {"weekend"}. For "free delivery weekend" it is STILL exactly
    {"weekend"} — "free"/"delivery" are flagged but not date/occasion-class, so they are
    never exempted (round-2 BLOCKER 1). A phrase with no flagged date/occasion word
    yields {}."""
    tokens: set[str] = set()
    for phrase in occasion_phrases:
        norm = _norm_ws(phrase)
        if not norm:
            continue
        for word in _CONTEXT_WORD_RE.findall(norm):
            if _DATE_OCCASION_CLASS_RE.match(word) and _operational_claim_hit(word):
                tokens.add(word)
    return tokens


def _strip_grounded_occasion_claims(text: str, exempt_tokens: set[str]) -> str:
    """Replace each grounded DATE/OCCASION-CLASS token occurrence in ``text`` with a
    neutral placeholder. Returns ``text`` unchanged when there is nothing to exempt. Called
    ONLY after the field-level scheduling veto has cleared (``_has_scheduling_claim`` is
    False), so every occurrence here is a legitimate occasion-theme use. The result is fed
    ONLY to ``_operational_claim_hit`` for background_brief — never rendered, and never used
    by the date-shape / commercial / locked-value / text-render scans."""
    if not text or not exempt_tokens:
        return text
    result = text
    for tok in sorted(exempt_tokens, key=len, reverse=True):
        pattern = re.compile(
            r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])", re.IGNORECASE
        )
        result = pattern.sub(" occasion ", result)
    return result


def _strip_clear_ratios(text: str) -> str:
    """Replace each EXPLICITLY-ALLOWLISTED widescreen ratio ("16/9","21/9", "/" or "-") with
    a neutral placeholder before the background_brief operational scan (round-4), so those
    named clear ratios pass the firewall's broad slash pattern (matching the date-shape
    detector, which ignores N>12). The allowlist is closed (``_CLEAR_RATIO_RE``): a possible
    date ("4/5","6/15", N<=12), a possible 2-digit-cents slash-price ("16/10","16/99"), the
    hours idiom "24/7", and any other slash are UNTOUCHED and still reject. This CANNOT hide
    a date/price/hours claim; it errs toward reject on every ambiguous case. NO general ratio
    denylist and NO N>12 class rule (both leaked — round-3 BLOCKER 1 revert + "24/7" guard)."""
    if not text:
        return text
    return _CLEAR_RATIO_RE.sub(" ratio ", text)


def _occasion_aware_operational_claim_hit(
    text: str, occasion_claim_tokens: set[str]
) -> str:
    """``_operational_claim_hit`` for the render-reaching background_brief, with two NARROW,
    scoped relaxations layered on top — applied ONLY to background_brief, never to the
    shared ``_operational_claim_hit`` or any other field. BOTH err toward REJECT on every
    ambiguous input (the HARD-LINE principle: an allowed date/claim is a leak):

      A. CLEAR RATIOS (round-4): only the explicitly-allowlisted widescreen ratios
         ("16/9","21/9" — never a date, never a price, never the "24/7" hours idiom) are
         neutralized FIRST. "4/5"/"6/15" (possible dates), "16/10"/"16/99" (possible prices),
         and "24/7" (hours) are NOT neutralized → they still reject.
      B. GROUNDED OCCASION THEME: then, IFF there are grounded DATE/OCCASION-CLASS tokens
         AND the field carries no scheduling/availability/booking claim
         (``_has_scheduling_claim`` on the ORIGINAL text — round-2 BLOCKER 2), each grounded
         date/occasion token is neutralized.

    AUTHORITATIVE pre-check (round-5 BLOCKER 2): "open <date/occasion token>"
    ("open weekend","open Saturday","open Memorial Day weekend","opens Saturday") is caught
    on the ORIGINAL text FIRST and returned as a claim — BEFORE any stripping, which would
    else turn "open Memorial Day weekend" into a benign-looking "open Memorial Day". This
    fires even when the occasion is grounded.

    With no clear ratios AND no grounded occasion tokens (and no "open <occasion>") this is
    byte-for-byte ``_operational_claim_hit`` (fail-closed posture + every other claim class
    verbatim). EVERY other class (dates incl. "6/15"/"4/5", prices, phones, addresses,
    service/credential claims) still fires — no relaxation can turn a real claim into a pass."""
    open_occasion = _OPEN_DATE_OCCASION_RE.search(text)
    if open_occasion:
        return open_occasion.group(0).strip()
    scan_text = _strip_clear_ratios(text)
    if occasion_claim_tokens and not _has_scheduling_claim(text):
        scan_text = _strip_grounded_occasion_claims(scan_text, occasion_claim_tokens)
    return _operational_claim_hit(scan_text)


# ── STRICT operational detector for NON-RENDERING fields (operator round-3) ──
# The aggressive ``_operational_claim_hit`` above is the HARD LINE for the textless
# render-reaching background_brief: it flags bare creative-overlap words ("fresh",
# "best", "award", "daily") and fail-closes when the broad classifier is absent.
# That precision is correct for the pixel-reaching prompt but OVER-BLOCKS creative
# theme text ("Fresh spring blossoms", "Award-style trophy motif", "Best-of-season")
# when re-applied to the NON-RENDERING planning fields (offer_structure /
# layout_strategy / grouping / visual_direction). For those fields we need the
# operational scan too (an INVENTED "free delivery" in offer_structure must reject),
# but PRECISE: only GENUINE service / availability / hours / credential claims, NOT
# theme / color / motif / celebration words. This detector is used ONLY for the
# non-rendering grounded-or-rejected operational scan; background_brief keeps the
# aggressive detector unchanged. Verified against the operator's exact false-positive
# string ("Graduation celebration, gold navy white, graduation caps") + the fresh/
# best/award/open creative edge cases — all clean — and 21 genuine claims — all flag.
_STRICT_OPERATIONAL_CLAIM_RE = re.compile(
    r"\b(?:"
    # availability / service claims (multi-word or unambiguous)
    r"free\s+delivery|delivery\s+available|home\s+delivery|delivery\s+service|"
    r"we\s+deliver|"
    r"takeout|take[\s-]?out|takeaway|dine[\s-]?in|drive[\s-]?thru|"
    # bare service terms (operator round-4 MINOR) — genuine service words. A small
    # guard skips the obvious creative motif "pickup truck"; the grounded-or-rejected
    # rule covers any other compositional use.
    r"curbside|pick[\s-]?up(?!\s+truck)|delivery|"
    r"order\s+online|online\s+order(?:ing|s)?|"
    r"reservations?|book\s+(?:now|a\s+table)|walk[\s-]?ins?\s+welcome|"
    r"catering\s+available|catering\s+service|now\s+catering|"
    # hiring
    r"now\s+hiring|hiring\s+now|help\s+wanted|join\s+our\s+team|"
    # hours / open-close (genuine business hours, not a layout "open"). The "until/
    # till" + clock forms are anchored open-phrases (operator round-4 MAJOR all-hits).
    r"open\s+(?:daily|\d|today|now|late|until|till|til|24[\s/]?7|"
    r"mon|tue|wed|thu|fri|sat|sun)|"
    r"open\s+for\s+business|grand\s+opening|now\s+open|re[\s-]?open(?:ing|ed)?|"
    r"closed\s+(?:on|mon|tue|wed|thu|fri|sat|sun|today)|"
    r"closes?\s+at|hours\s*:|business\s+hours|store\s+hours|"
    r"\d\s*(?:am|pm)\b|"
    # superiority / credential claims — REQUIRE the claim context (not the bare word)
    r"fresh\s+daily|made\s+fresh|freshly\s+(?:made|baked|prepared)|"
    r"best\s+(?:in\s+town|seller|price|deal|value|of\s+the)|voted\s+best|"
    r"#\s*1\b|number\s+one\b|award[\s-]?winning|award[\s-]?winner|"
    r"family[\s-]?owned|locally\s+owned|certified|licensed|insured|"
    r"satisfaction\s+guaranteed|money[\s-]?back|100%\s+guarantee|"
    r"since\s+(?:19|20)\d{2}"
    r")\b",
    re.IGNORECASE,
)


def _strict_operational_hits(text: str) -> list[str]:
    """ALL genuine service/availability/hours/credential operational claims in
    ``text`` (normalized strings), in order. PRECISE — creative theme/color/motif/
    celebration words do NOT match. Used ONLY for the non-rendering grounded-or-
    rejected operational scan (NOT the background_brief hard line). Also folds in the
    context-aware operational "open" (so "we are now open" is caught while "open
    central area" passes), de-duplicated."""
    if not text:
        return []
    hits: list[str] = []
    for m in _STRICT_OPERATIONAL_CLAIM_RE.finditer(text):
        tok = m.group(0).strip()
        if tok:
            hits.append(tok)
    # ALL context-aware operational "open" claims (all-hits — operator round-4 MAJOR),
    # for any open-phrase not already captured by the regex's open-forms above.
    for open_hit in _all_open_claim_hits(text):
        if open_hit and not any(open_hit.casefold() in h.casefold() for h in hits):
            hits.append(open_hit)
    return hits


def _first_ungrounded_operational(text: str, allowed_values: Sequence[str]) -> str:
    """The first INVENTED (ungrounded) GENUINE operational claim in ``text``, or "" if
    every strict operational claim is grounded in an OVERLAY-RENDERED locked value
    (referenced or required) — or there are none. ALL-HITS: a grounded operational
    claim followed by an invented one is rejected on the invented one. Grounding is
    boundary-aware whole-phrase containment (``_phrase_is_grounded``): the claim must
    appear as a whole phrase in some allowed fact value (e.g. a locked tagline "Free
    delivery on all catering orders" grounds an offer_structure mention of "free
    delivery"). PRECISE detector ⇒ creative theme text never reaches this check."""
    if not text:
        return ""
    for hit in _strict_operational_hits(text):
        if not _phrase_is_grounded(hit, allowed_values):
            return hit
    return ""


def required_fact_ids(locked_facts: Sequence[FlyerLockedFact]) -> set[str]:
    """The deterministic required-visible-fact set — derived from the LOCKED FACTS
    ONLY (Codex BLOCKER #1: no brief field may influence this, or the model could
    dodge a requirement by claiming a different ``request_intent``).

    Authority = each fact's OWN ``.required`` flag (Codex #2), exactly what facts.py
    sets (customer-stated combo item names/prices default required=True via _fact()).
    NO brief field and NO parallel id allowlist influence this — a required=False
    fact (e.g. an advisory planner suggestion) is intentionally NOT required-visible.
    """
    required: set[str] = set()
    for fact in locked_facts or []:
        fid = getattr(fact, "fact_id", "")
        if not fid:
            continue
        if getattr(fact, "required", False):
            required.add(fid)
    return required


# ── fact_id taxonomy classification (Codex P2 — occasion/theme over-block) ──
# The free-text locked-value scan must block IDENTITY/COMMERCIAL fact values (a
# business name / item name / price / claim leaking into the textless background or
# the structure fields), but must NOT block OCCASION/THEME/SEASONAL fact values —
# "Memorial Day" legitimately belongs in visual_direction ("Memorial Day patriotic
# Americana"), and that value can be a locked fact (facts.py `campaign_title`). The
# classification below is derived from the fact_id taxonomy in facts.py.
#
#   OCCASION / THEME / SEASONAL (allowed in visual_direction / background_brief):
#     - campaign_title  (facts.py:600 — the occasion/campaign, e.g. "Memorial Day")
#   NOT exempt — `schedule` is a DATE/TIME hard fact (the real promotion timing the
#   OVERLAY must render); letting "Saturday evening" ride into model-authored
#   background text would duplicate/garble it (Codex P1). Everything else a fact_id
#   can be (business_name, contact_phone, location, headline, tagline, schedule,
#   promotion_end, pricing_structure, item:N:name, item:N:price, offer:N, offer_price,
#   replacement:*, source_*) is IDENTITY/COMMERCIAL/DATE and its locked value is
#   blocked from the free-text fields.
_OCCASION_THEME_FACT_IDS = frozenset({"campaign_title"})
_OCCASION_THEME_FACT_PREFIXES = ("theme_", "occasion")


def _is_occasion_theme_fact_id(fact_id: str) -> bool:
    """True iff ``fact_id`` is an occasion/theme/seasonal fact whose locked value may
    appear in the free-text visual fields (its value is a *theme*, not identity or a
    commercial value). Derived from the facts.py fact_id taxonomy. The exact ids
    cover today's producers; the prefixes future-proof any theme_*/occasion* id."""
    if fact_id in _OCCASION_THEME_FACT_IDS:
        return True
    return any(fact_id.startswith(p) for p in _OCCASION_THEME_FACT_PREFIXES)


# ── expected offer structure, DERIVED FROM LOCKED FACTS (Codex P1) ──────────
_ITEM_REF_RE = re.compile(r"^item:(?P<index>\d+):(?P<kind>name|price)$")
_OFFER_REF_RE = re.compile(r"^offer:(?P<index>\d+)$")

# The ONE non-grounded FlyerFactSource (schemas.FlyerFactSource): a planner
# ASSUMPTION the model proposed, not a customer/operator/reference-grounded fact.
# `creative_planner.materialize_inferred` stamps inferred item names with this
# source (required=False). All other sources — customer_text / customer_confirmed /
# customer_profile / reference_ocr / reference_vision / uploaded_asset / operator /
# system — are GROUNDED. Excluding this single value (rather than allow-listing the
# grounded set) future-proofs the regime switch against new grounded sources.
_MODEL_INFERRED_SOURCE = "hermes_inferred"


def expected_offer_keys(locked_facts: Sequence[FlyerLockedFact]) -> set[str]:
    """The set of DISTINCT offers the locked facts imply — each must own a distinct
    OfferGroup. Derived from the LOCKED FACTS ONLY (never request_intent / any brief
    field): each distinct ``item:N:*`` index is one offer ("item:N"), and each
    ``offer:N`` id is one offer ("offer:N"). The Memorial-Day two-combo set
    (item:0:*, item:1:*) therefore implies {"item:0", "item:1"} → two cards."""
    keys: set[str] = set()
    for fact in locked_facts or []:
        fid = getattr(fact, "fact_id", "") or ""
        m_item = _ITEM_REF_RE.match(fid)
        if m_item:
            keys.add(f"item:{m_item.group('index')}")
            continue
        if _OFFER_REF_RE.match(fid):
            keys.add(fid)
    return keys


def _offer_key_for_ref(fact_id: str) -> str:
    """Map a single fact_id to the offer-key it belongs to (item:N for an
    ``item:N:name|price``, offer:N for an ``offer:N``), or "" if it is neither."""
    m_item = _ITEM_REF_RE.match(fact_id or "")
    if m_item:
        return f"item:{m_item.group('index')}"
    if _OFFER_REF_RE.match(fact_id or ""):
        return fact_id
    return ""


def _has_item_level_facts(locked_facts: Sequence[FlyerLockedFact]) -> bool:
    """True iff the LOCKED FACTS contain at least one GROUNDED fine-grained
    ``item:N:name`` / ``item:N:price`` fact (B2 advisory split, operator-approved
    Option 1; Codex round-2 MAJOR fix).

    This is the switch between two regimes for ``offer_groups`` enforcement:

      - **grounded item-level facts EXIST** → the customer/operator/reference
        ACTUALLY stated fine item/price slots, so a true item-level collapse /
        wrong-slot / unknown-ref is still a STRUCTURAL bug the firewall hard-rejects
        (the prior P1 invariant is fully preserved).
      - **NO grounded item-level facts** (production extracts each combo as ONE
        COARSE ``offer:N`` locked fact, or a pure-identity flyer) → there are no fine
        item/price slots to collapse; ``offer_groups`` only guides layout. Its
        findings are downgraded to advisory (non-blocking) warnings so a model that
        references non-existent ``item:N:price`` slots cannot fail-close a flyer
        whose required facts are fully covered by ``fact_refs``.

    GROUNDED, not merely present (Codex round-2 MAJOR): the bounded creative planner
    (`creative_planner.materialize_inferred`, enabled in cfg) can add
    ``item:N:name`` facts with ``source="hermes_inferred"`` / ``required=False``
    ALONGSIDE coarse ``offer:N`` facts. A MODEL-SUGGESTED item must NOT flip the
    regime back to blocking (that would reintroduce the very fail-close B2 fixes —
    the safe direction, but it defeats B2's purpose). An item fact counts as
    item-level ONLY when it is genuinely grounded: its ``source`` is not the lone
    non-grounded ``hermes_inferred`` AND it is ``required`` (every grounded
    ``item:N:*`` fact from facts.py is ``required=True``; the inferred ones are
    ``required=False``). The operator's contract is "hard-reject only when item-level
    facts ACTUALLY exist" = genuine/grounded, not model-suggested.

    Derived from the LOCKED FACTS ALONE (never any brief field) — same authority
    discipline as ``required_fact_ids`` / ``expected_offer_keys``."""
    for fact in locked_facts or []:
        if not _ITEM_REF_RE.match(getattr(fact, "fact_id", "") or ""):
            continue
        if getattr(fact, "source", "") == _MODEL_INFERRED_SOURCE:
            continue  # a planner ASSUMPTION — must not flip the regime to blocking.
        if not getattr(fact, "required", False):
            continue  # an optional item is not a hard structural commitment.
        return True
    return False


def _required_slot_refs_for_offer(
    offer_key: str, locked_ids: set[str]
) -> tuple[str, str]:
    """The (name_ref, price_ref) fact_ids an offer's OWN card must slot, derived
    from the LOCKED FACTS only (never request_intent). For an ``item:N`` offer the
    name is ``item:N:name`` and price is ``item:N:price`` — but only if that price
    fact is actually locked (a name-only item has no price card slot). For an
    ``offer:N`` offer the name IS ``offer:N`` and there is no separate price fact.
    A "" slot means "no such locked fact ⇒ not required in this card"."""
    if offer_key.startswith("item:"):
        index = offer_key.split(":", 1)[1]
        name_ref = f"item:{index}:name"
        price_ref = f"item:{index}:price"
        return (
            name_ref if name_ref in locked_ids else "",
            price_ref if price_ref in locked_ids else "",
        )
    if _OFFER_REF_RE.match(offer_key):
        # offer:N is itself the name; no separate price fact in the taxonomy.
        return (offer_key if offer_key in locked_ids else "", "")
    return ("", "")


def _group_offer_keys(group: OfferGroup) -> set[str]:
    """The distinct offer-keys an OfferGroup's refs touch. A group that references
    both item:0:* and item:1:* touches two offer-keys → it collapsed two offers."""
    keys: set[str] = set()
    for ref in (group.title_ref, group.price_ref, *group.inclusion_refs):
        key = _offer_key_for_ref(ref or "")
        if key:
            keys.add(key)
    return keys


def _validate_offer_structure(
    brief: FlyerBrief,
    locked_ids: set[str],
    locked_facts: Sequence[FlyerLockedFact],
) -> tuple[list[str], list[str]]:
    """Deterministic offer-structure check, derived from LOCKED FACTS (never
    request_intent). Returns ``(errors, warnings)`` — B2 advisory split, operator-
    approved Option 1.

    The SAME structural findings are computed in both regimes; the only thing the
    regime decides is whether a finding is BLOCKING (``errors``) or ADVISORY
    (``warnings``):

      - **item-level facts EXIST** (``_has_item_level_facts``): the brief groups
        real fine-grained ``item:N:name``/``item:N:price`` slots, so the prior
        Codex-P1 invariant is fully preserved — every finding is a BLOCKING error:
          - every ``offer_groups`` ref must be a real locked fact id (no new
            invention vector — same authority as ``fact_refs``);
          - NO single OfferGroup may span two distinct locked offers ("combo
            structure collapsed: offers X and Y share one group");
          - each expected locked offer maps to a DISTINCT OfferGroup that SLOTS its
            required refs (NAME in ``title_ref``/``inclusion_refs``, PRICE in
            ``price_ref``) ⇒ "offer X missing price_ref/title_ref in its card".

      - **NO item-level facts** (production extracts each combo as ONE COARSE
        ``offer:N`` fact, or a pure-identity flyer): there are no fine item/price
        slots to collapse and a model may reference non-existent ``item:N:price``
        slots. ``offer_groups`` then only guides layout, so ALL of the above findings
        are ADVISORY warnings — never blocking. Required-fact coverage via
        ``fact_refs`` (check (e) in ``validate``) stays the SOLE blocking authority
        for facts; ``offer_groups`` can neither suppress nor replace it.

    A group that references a coarse ``offer:N`` fact DIRECTLY (no item-level
    name/price slots) is structurally complete in both regimes — ``offer:N`` is its
    own name and the taxonomy has no separate price fact, so it raises no finding.
    """
    findings: list[str] = []
    expected = expected_offer_keys(locked_facts)
    # Regime switch: blocking when the locked facts carry fine item:N:* slots,
    # advisory when they are coarse offer:N (or pure identity). Computed from the
    # LOCKED FACTS alone — no brief field may flip a finding from blocking to advisory.
    blocking = _has_item_level_facts(locked_facts)

    def _split() -> tuple[list[str], list[str]]:
        """Route the accumulated structural findings into (errors, warnings)."""
        return (findings, []) if blocking else ([], findings)

    # (1) every ref in offer_groups resolves to a real locked fact id.
    for group in brief.offer_groups:
        for ref in (group.title_ref, group.price_ref, *group.inclusion_refs):
            rid = (ref or "").strip()
            if rid and rid not in locked_ids:
                findings.append(f"unknown offer_group ref {rid}")

    # If there is nothing structural to enforce (no locked offers), stop here: a
    # non-itemized flyer (pure identity) has no combo structure to preserve.
    if not expected:
        return _split()

    # (2) no single group may collapse two distinct locked offers into one card.
    for group in brief.offer_groups:
        touched = _group_offer_keys(group)
        if len(touched) > 1:
            a, b = sorted(touched)[:2]
            findings.append(f"combo structure collapsed: offers {a} and {b} share one group")

    # (3) each expected locked offer maps to a DISTINCT group that SLOTS its required
    # refs in the right slots (Codex Finding 1). A group merely TOUCHING the offer is
    # not enough: a combo card is structurally incomplete unless its NAME fact is in
    # title_ref/inclusion_refs AND its PRICE fact (when one is locked) is in price_ref.
    # Build offer-key -> ordered list of group indices that carry that offer's refs
    # ANYWHERE (so the distinct-card accounting matches step-2's collapse detection).
    key_to_groups: dict[str, list[int]] = {k: [] for k in expected}
    for gi, group in enumerate(brief.offer_groups):
        for key in _group_offer_keys(group):
            if key in key_to_groups:
                key_to_groups[key].append(gi)

    def _group_slots_offer(group: OfferGroup, name_ref: str, price_ref: str) -> bool:
        """True iff this group slots the offer's required refs correctly — NAME in
        title_ref/inclusion_refs and PRICE (when locked) in price_ref."""
        name_slots = {group.title_ref or "", *group.inclusion_refs}
        if name_ref and name_ref not in name_slots:
            return False
        if price_ref and (group.price_ref or "") != price_ref:
            return False
        return True

    used_group_indices: set[int] = set()
    for key in sorted(expected):
        name_ref, price_ref = _required_slot_refs_for_offer(key, locked_ids)
        groups_for_key = key_to_groups.get(key, [])
        if not groups_for_key:
            findings.append(f"offer {key} is not grouped into a distinct card")
            continue
        # A distinct, not-yet-claimed group that ALSO slots the required refs.
        slotted = [
            gi
            for gi in groups_for_key
            if gi not in used_group_indices
            and _group_slots_offer(brief.offer_groups[gi], name_ref, price_ref)
        ]
        if slotted:
            used_group_indices.add(slotted[0])
            continue
        # The offer has a (distinct) card but its required name/price ref is not
        # slotted into it — structurally incomplete (Codex Finding 1). Distinguish a
        # shared-only card (collapse already flagged in step 2) from a wrong-slot card.
        unclaimed = [gi for gi in groups_for_key if gi not in used_group_indices]
        if not unclaimed:
            findings.append(f"offer {key} is not grouped into a distinct card")
            continue
        missing = []
        if price_ref and not any(
            (brief.offer_groups[gi].price_ref or "") == price_ref for gi in unclaimed
        ):
            missing.append("price_ref")
        if name_ref and not any(
            name_ref in {brief.offer_groups[gi].title_ref or "", *brief.offer_groups[gi].inclusion_refs}
            for gi in unclaimed
        ):
            missing.append("title_ref")
        if not missing:  # defensive: should not happen given _group_slots_offer failed
            missing.append("title_ref")
        # Claim the card so a sibling offer isn't double-counted against it.
        used_group_indices.add(unclaimed[0])
        findings.append(f"offer {key} missing {'/'.join(missing)} in its card")

    return _split()


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    # Non-blocking advisory findings (B2 advisory split): unknown / mis-slotted
    # ``offer_groups`` refs in the coarse ``offer:N`` regime are logged here, NOT in
    # ``errors``. ``ok`` is derived from ``errors`` ALONE — warnings never fail-close.
    warnings: list[str] = field(default_factory=list)


def _materialized_span_id(index: int) -> str:
    """Deterministic id for the Nth validated customer_text span."""
    return f"customer_span:{index}"


def _validated_span_indices(brief: FlyerBrief, raw_request: str) -> list[int]:
    """Indices (positional within brief.fact_refs) of customer_text spans that are
    verified substrings of the request. Used by both validate() and
    materialize_spans() so the two agree on exactly which spans materialize."""
    haystack = _norm_ws(raw_request)
    indices: list[int] = []
    for i, ref in enumerate(brief.fact_refs):
        if ref.provenance != "customer_text":
            continue
        span = _norm_ws(ref.raw_span or "")
        if span and span in haystack:
            indices.append(i)
    return indices


def validate(
    brief: FlyerBrief,
    locked_facts: Sequence[FlyerLockedFact],
    raw_request: str,
) -> ValidationResult:
    """Fail-closed validation of a Creative-Director brief against truth.

    Errors (each appended, never raised) make ``ok=False``:
      (a) a FactRef.fact_id that matches no locked fact id;
      (b) a FactRef.raw_span that is not a verified substring of raw_request
          (an invented commercial value);
      (c) a commercial value (currency/price/percent-discount/phone-run) OR a genuine
          operational claim (service/availability/hours/credential) in a model-
          authored free-text field — it bypassed fact_refs (BLOCKER #3). RENDER-
          REACHING ``background_brief`` is the HARD LINE: ANY commercial value, locked
          identity value, text-render instruction, or operational claim rejects (even
          if grounded — it reaches pixels). For non-rendering planning fields the rule
          is GROUNDED-OR-REJECTED, ALL-HITS: EVERY commercial value AND EVERY genuine
          operational claim must resolve through a locked fact that ACTUALLY renders
          via the overlay (referenced by fact_refs OR required); an invented /
          ungrounded one rejects (operator scope decisions 2026-06-06 + round-3).
          Commercial grounding is token-anchored; operational uses a PRECISE detector
          so creative theme/color/motif words are not flagged;
      (d) must_not_add whose entry CONTAINS a locked-fact value (containment, not
          exact-match — "omit Non Veg Combo" suppresses the locked value; #4);
      (e) referenced fact_ids ∪ materialized-span ids failing to cover
          required_fact_ids(locked_facts) — "omits required fact <id>".

    ``offer_groups`` structure is NON-AUTHORITATIVE for facts (B2 advisory split):
    it is enforced as a BLOCKING error ONLY when fine ``item:N:*`` facts exist; for
    the coarse ``offer:N`` (or pure-identity) case its findings are downgraded to
    non-blocking ``warnings``. ``offer_groups`` can never suppress or replace required
    rendering — check (e) above is the SOLE blocking authority for facts.
    """
    errors: list[str] = []
    warnings: list[str] = []
    locked_by_id = {f.fact_id: f for f in locked_facts or []}
    locked_ids = set(locked_by_id)

    # (a) every fact_id ref resolves to a real locked fact.
    referenced_ids: set[str] = set()
    for ref in brief.fact_refs:
        if ref.provenance == "locked" and (ref.fact_id or "").strip():
            fid = ref.fact_id or ""
            referenced_ids.add(fid)
            if fid not in locked_ids:
                errors.append(f"unknown fact id {fid}")

    # (b) every customer_text span is a verbatim substring of the request.
    haystack = _norm_ws(raw_request)
    for ref in brief.fact_refs:
        if ref.provenance != "customer_text":
            continue
        span = (ref.raw_span or "").strip()
        if not span or _norm_ws(span) not in haystack:
            errors.append(f"invented span not in request: {span}")

    # (c) no commercial value may ride in the model-authored free-text fields —
    # facts belong in fact_refs (overlay-rendered), the background is textless, and
    # the structure fields are not content. Scan each field; first hit per field.
    #
    # SCOPE (operator decision 2026-06-06, live retest): the strictness depends on
    # whether the field's text can reach pixels. Only ``background_brief`` is passed
    # to the image model (``_RENDER_REACHING_FIELDS``); the structure/planning fields
    # are NEVER sent to image gen, so a value there cannot render. Therefore:
    #   - RENDER-REACHING field (background_brief): STRICT — ANY commercial SHAPE
    #     rejects, AND a locked identity/commercial textual value rejects (it would
    #     render into the background OUTSIDE the overlay).
    #   - NON-RENDERING field (offer_structure/layout_strategy/grouping/
    #     visual_direction): reject ONLY an INVENTED commercial value. "Grounded" is
    #     TIGHT (operator merge-blocker): the value must resolve through a fact that
    #     ACTUALLY renders via the overlay — i.e. it is contained in a locked fact
    #     that is either REFERENCED by ``fact_refs`` OR ``required``. A commercial
    #     value matching an UNreferenced, non-required locked fact STILL rejects (it
    #     would never render, so the planning mention is not anchored to a real
    #     overlay fact). The locked-text identity scan is SKIPPED for these fields
    #     (grounded + non-rendering = harmless).
    vd = brief.visual_direction
    free_text_fields = {
        "background_brief": brief.background_brief,
        "offer_structure": brief.offer_structure,
        "layout_strategy": brief.layout_strategy,
        "grouping": " ".join(brief.grouping),
        "visual_direction": " ".join(
            [vd.theme_family, *vd.palette, *vd.motifs, *vd.visual_subjects]
        ),
    }
    # Values that ACTUALLY render via the overlay — a locked fact is rendered iff it
    # is REFERENCED by fact_refs OR is required. A non-rendering commercial hit is
    # "grounded" (allowed) ONLY when it is contained in one of these (operator
    # merge-blocker #1: ties the planning-field mention to a fact that renders).
    referenced_ids = {
        (r.fact_id or "").strip() for r in brief.fact_refs if (r.fact_id or "").strip()
    }
    allowed_values = [
        _norm_ws(f.value)
        for f in (locked_facts or [])
        if (f.value or "").strip()
        and (
            ((getattr(f, "fact_id", "") or "") in referenced_ids)
            or getattr(f, "required", False)
        )
    ]
    # Locked IDENTITY/COMMERCIAL textual values (business/item name/tagline/claim)
    # must not appear in a RENDER-REACHING free-text field — they would render into
    # the background OUTSIDE the overlay (Codex NEW-BYPASS). OCCASION/THEME/SEASONAL
    # fact values (campaign_title="Memorial Day", schedule) are EXCLUDED (Codex P2):
    # they legitimately belong in visual_direction ("Memorial Day patriotic
    # Americana"). Boundary-aware; length>=4 skips trivial words.
    locked_text_values = [
        _norm_ws(f.value)
        for f in locked_facts or []
        if not _is_occasion_theme_fact_id(getattr(f, "fact_id", "") or "")
    ]
    locked_text_values = [v for v in locked_text_values if len(v) >= 4]
    # Grounded OCCASION/THEME phrases (campaign_title + theme_*/occasion*) → the ONLY
    # source for the NARROW background_brief operational-claim exemption (operator
    # 2026-06-06). Their date/schedule claim-tokens (e.g. "weekend" from "Memorial Day
    # Weekend") are exempted from the operational scan when used as the occasion theme in
    # a non-scheduling context — see ``_occasion_aware_operational_claim_hit``. Schedule /
    # prices / contact / items are NOT occasion ids, so they never feed this set.
    occasion_phrases = [
        _norm_ws(f.value)
        for f in locked_facts or []
        if (f.value or "").strip()
        and _is_occasion_theme_fact_id(getattr(f, "fact_id", "") or "")
    ]
    occasion_claim_tokens = _occasion_claim_tokens(occasion_phrases)
    for field_name, text in free_text_fields.items():
        if field_name in _RENDER_REACHING_FIELDS:
            # STRICT (HARD LINE): any commercial SHAPE is rejected (it can reach
            # pixels), the locked identity/commercial textual scan runs (would render
            # outside the overlay), AND invented address / date-time shapes reject —
            # contact/date/address are overlay-owned facts that must never be model-
            # rendered into the background, grounded OR not (operator round-4 BLOCKER 1).
            hit = _commercial_value_hit(text)
            if hit:
                errors.append(f"commercial value outside fact_refs: {field_name}: {hit}")
            addr_hit = _address_shape_hit(text)
            if addr_hit:
                errors.append(f"address shape outside fact_refs: {field_name}: {addr_hit}")
            date_hit = _date_time_shape_hit(text)
            if date_hit:
                errors.append(f"date/time shape outside fact_refs: {field_name}: {date_hit}")
            norm_text = _norm_ws(text)
            for lv in locked_text_values:
                if re.search(r"(?<![a-z0-9])" + re.escape(lv) + r"(?![a-z0-9])", norm_text):
                    errors.append(f"locked value outside fact_refs: {field_name}: {lv}")
                    break
        else:
            # NON-RENDERING: GROUNDED-OR-REJECTED for BOTH commercial AND operational
            # hits (operator round-3) — the field passes ONLY IF EVERY commercial
            # value/token AND EVERY genuine operational claim in it is grounded
            # (contained in an OVERLAY-RENDERED locked value — referenced or required).
            # ALL-HITS: a grounded value followed by an invented one (e.g. "20% off and
            # $5 off", or a grounded "free delivery" then an invented "now hiring") is
            # still rejected on the invented one. Commercial is token-anchored
            # ("30% off" is not falsely grounded by a locked "20% off"); operational
            # uses the PRECISE strict detector so creative theme/color/motif words
            # ("Graduation celebration", "Fresh spring blossoms") never reach this
            # scan. SKIP the locked-text identity scan (grounded + non-rendering =
            # harmless).
            ungrounded = _first_ungrounded_commercial(text, allowed_values)
            if ungrounded:
                errors.append(f"invented commercial value in {field_name}: {ungrounded}")
            ungrounded_op = _first_ungrounded_operational(text, allowed_values)
            if ungrounded_op:
                errors.append(f"invented operational claim in {field_name}: {ungrounded_op}")

    # (c-textless, Codex Finding 3) the TEXTLESS-background prompt — scan ONLY the
    # render-reaching fields (``background_brief``). They must not (a) instruct the
    # model to render words into the background, nor (b) invent a non-price
    # operational claim. These ride OUTSIDE the overlay (no fact authority) and defeat
    # the textless invariant. ``visual_direction`` is NOT render-reaching — its text
    # never goes to image gen — so its operational/text-render content cannot reach
    # pixels and is NOT scanned here (removes the prior visual_direction over-scan).
    textless_fields = {
        k: v
        for k, v in {
            "background_brief": brief.background_brief,
            "visual_direction": " ".join(
                [vd.theme_family, *vd.palette, *vd.motifs, *vd.visual_subjects]
            ),
        }.items()
        if k in _RENDER_REACHING_FIELDS
    }
    for field_name, text in textless_fields.items():
        render_hit = _text_render_instruction_hit(text)
        if render_hit:
            errors.append(
                f"text rendering instruction in textless background: {field_name}: {render_hit}"
            )
        # AUTHORITATIVE scheduling/availability rejection (round-5 BLOCKER 1) — ONLY for
        # background_brief. A scheduling/availability/booking claim ("spots available",
        # "tables available", "available now", "book a table", "reserve a spot") REJECTS the
        # field directly, NOT merely gating occasion stripping. ``_operational_claim_hit``
        # does not independently catch a bare "available", so without this an availability
        # claim with no occasion token would leak. Scanned on the ORIGINAL text.
        if field_name == "background_brief":
            sched_hit = _scheduling_claim_hit(text)
            if sched_hit:
                errors.append(
                    f"scheduling/availability claim in textless background: {field_name}: {sched_hit}"
                )
        # NARROW occasion-aware exemption (operator 2026-06-06) applies ONLY to
        # background_brief: a GROUNDED occasion claim-token (e.g. "weekend" from a locked
        # campaign_title "Memorial Day Weekend") used as the occasion theme in a
        # NON-scheduling context is exempted from this operational scan. Every other claim
        # class — invented dates, "open <occasion>", scheduling-context occasion tokens,
        # service/credential claims, the fail-closed sentinel — still fires. With no grounded
        # occasion claim-tokens this is byte-for-byte ``_operational_claim_hit`` (fail-closed).
        claim_hit = (
            _occasion_aware_operational_claim_hit(text, occasion_claim_tokens)
            if field_name == "background_brief"
            else _operational_claim_hit(text)
        )
        if claim_hit:
            errors.append(
                f"invented operational claim in textless background: {field_name}: {claim_hit}"
            )

    # (d) must_not_add may not CONTAIN a locked-fact value (would suppress a real
    # fact). Containment as a UNIT (not exact-match): "omit Non Veg Combo" must be
    # caught. Boundaries are alphanumeric-aware (NOT \b, which fails on values that
    # start/end in punctuation like "$49.99") — so "Combos" does not match "Combo",
    # but "$49.99" still matches when flanked by space/start/end.
    locked_values = [_norm_ws(f.value) for f in locked_facts or [] if (f.value or "").strip()]
    # (d-commercial, Codex Finding 2) must_not_add ALSO rides the commercial-value
    # scan: an entry like "no $19.99 price badge" smuggles an INVENTED price (a
    # commercial value that is NOT one of the locked values) into the brief via the
    # suppression list, where checks (c)/(d) never looked. Reject any must_not_add
    # whose commercial value (numeric token / offer phrase / residual offer word) is
    # not grounded in a locked-fact value — the SAME three-scan ALL-HITS grounding as
    # the non-rendering free-text scan (``_first_ungrounded_commercial``). So "no 30%
    # off badge" is not falsely grounded by a locked "20% off ..." and "no 20% off and
    # cashback badge" rejects on the invented "cashback" (operator round-5). A
    # suppression naming a REAL locked commercial value is already caught by the
    # containment check below (unchanged).
    for entry in brief.must_not_add:
        norm_entry = _norm_ws(entry)
        if not norm_entry:
            continue
        ungrounded = _first_ungrounded_commercial(entry, locked_values)
        if ungrounded:
            errors.append(f"must_not_add invents commercial value: {ungrounded}")
        for lv in locked_values:
            if lv and re.search(r"(?<![a-z0-9])" + re.escape(lv) + r"(?![a-z0-9])", norm_entry):
                errors.append(f"must_not_add contains locked value: {entry}")
                break

    # (d2) structural pairing (Codex P1) — B2 advisory split. When fine item:N:*
    # facts exist, coverage in (e) proves every required fact is REFERENCED but not
    # that combo STRUCTURE is preserved, so the offer-structure findings are BLOCKING
    # (a brief that refs both combos yet groups them into one card collapses invariant
    # #4). When the locked facts are coarse offer:N (or pure identity) there are no
    # fine slots to collapse and the model may reference non-existent item:N slots, so
    # the SAME findings are returned as non-blocking warnings. Derived from the locked
    # facts alone (never request_intent); (e) below stays the sole blocking authority.
    structural_errors, structural_warnings = _validate_offer_structure(
        brief, locked_ids, locked_facts
    )
    errors.extend(structural_errors)
    warnings.extend(structural_warnings)

    # (e) coverage: referenced locked ids + the ids the validated spans will
    # materialize into must cover the required set. The required set is derived
    # from the locked facts ALONE (no brief field — BLOCKER #1). Spans materialize
    # to customer_span:N ids, so named required ids (item:*, business_name, ...)
    # are only covered by an explicit fact_id reference — the skill can't satisfy
    # a named requirement by quoting a free span.
    span_indices = _validated_span_indices(brief, raw_request)
    materialized_ids = {_materialized_span_id(i) for i, _ in enumerate(span_indices)}
    covered = referenced_ids | materialized_ids
    for fid in sorted(required_fact_ids(locked_facts)):
        if fid not in covered:
            errors.append(f"omits required fact {fid}")

    # ``ok`` is derived from ``errors`` ALONE — advisory ``warnings`` (downgraded
    # coarse-offer offer_groups findings) NEVER fail-close the brief.
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def materialize_spans(
    brief: FlyerBrief,
    raw_request: str,
) -> list[FlyerLockedFact]:
    """Turn each validated ``customer_text`` span into a real locked fact.

    Mirrors facts.py construction (``source="customer_text"``, cleaned value,
    explicit label). Only spans that are verified substrings of ``raw_request``
    materialize — invented spans never reach here (validate() rejects them, and
    this re-checks). Ids are deterministic ``customer_span:N`` so the overlay can
    render ``required_fact_ids ∩ locked_facts`` without colliding with extractor
    ids. Spans are NEVER rendered directly — only as the facts they become.
    """
    facts: list[FlyerLockedFact] = []
    for out_index, ref_index in enumerate(_validated_span_indices(brief, raw_request)):
        ref = brief.fact_refs[ref_index]
        value = " ".join((ref.raw_span or "").split())
        if not value:
            continue
        facts.append(
            FlyerLockedFact(
                fact_id=_materialized_span_id(out_index),
                label="Customer text",
                value=value,
                source="customer_text",
            )
        )
    return facts
