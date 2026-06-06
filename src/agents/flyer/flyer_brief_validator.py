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
# path; bare "discount"/"cashback"/"combo price" by the _commercial_value_hit residual.)
_DISCOUNT_OFFER_PHRASE_RE = re.compile(
    r"\b(?:bogo|buy\s+one\s+get(?:\s+one)?(?:\s+free)?)\b"
    r"|\b(?:free|complimentary|gift|bonus)(?:\s+[a-z][a-z'-]+)?",
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
#   - numeric date ("6/15/2026", "06-15-26", "2026-06-15");
#   - clock time ("9:00", "9:00 pm", "9 am").
_MONTH_RE = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE_TIME_SHAPE_RE = re.compile(
    r"\b" + _MONTH_RE + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*(?:19|20)\d{2})?\b"
    r"|\b" + _MONTH_RE + r"\s+(?:19|20)\d{2}\b"
    r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
    r"|\b(?:19|20)\d{2}[/-]\d{1,2}[/-]\d{1,2}\b"
    r"|\b\d{1,2}:\d{2}(?:\s*(?:am|pm))?\b"
    r"|\b\d{1,2}\s*(?:am|pm)\b",
    re.IGNORECASE,
)


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

    ALL-HITS (Codex BLOCKERs + operator round-4): a field/entry passes ONLY when
    EVERY commercial value it carries is grounded — a grounded value FOLLOWED by an
    invented one (e.g. "20% off and $5 off", or "20% off and BOGO free") is rejected
    on the invented one. Three independent ALL-HITS scans:
      - whole digit-bearing tokens (full percentage / currency / bare price / phone
        digit-run) each grounded via ``_token_is_grounded`` (NOT a loose substring —
        "30%" does not ground against a locked "20%");
      - non-numeric discount/offer PHRASES (free X / complimentary X / gift X / bonus
        X / BOGO / "buy one get one") each grounded as a WHOLE PHRASE via
        ``_phrase_is_grounded`` (operator round-4 BLOCKER 2) — so an invented "free
        dessert" does NOT ride a locked "free delivery", and a grounded "free
        delivery" is not double-rejected by the commercial path; scanned regardless of
        whether numeric tokens are also present;
      - any RESIDUAL non-numeric commercial hit (bare "discount"/"cashback"/"combo
        price") from ``_commercial_value_hit`` not already covered above, grounded as
        a whole word.
    The first ungrounded value found (in that order) is returned. Used by BOTH the
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
    # (3) residual non-numeric commercial shape (discount/cashback/combo price) not
    # already covered by (1)/(2) — grounded as a whole word.
    if not _commercial_grounding_tokens(text):
        hit = _commercial_value_hit(text)
        if (
            hit
            and not _DISCOUNT_OFFER_PHRASE_RE.search(hit)
            and not _phrase_is_grounded(hit, allowed_values)
        ):
            return hit
    return ""


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
        claim_hit = _operational_claim_hit(text)
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
    # whose commercial shape (currency/bare-price/percent/discount/phone-run) is not
    # one of the locked-fact values — TOKEN-ANCHORED, ALL-HITS (Codex BLOCKER 2): the
    # same grounding as the non-rendering free-text scan, so "no 30% off badge" is NOT
    # falsely grounded by a locked "20% off ..." (the truncated "0%" substring would
    # have been). A suppression naming a REAL locked commercial value is already
    # caught by the containment check below (unchanged).
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
