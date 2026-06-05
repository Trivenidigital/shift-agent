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
# Phone-like contiguous digit run (mirror visual_qa._PHONE_RUN_RE shape): 8+ chars
# of digits + common separators with at least 7 actual digits.
_PHONE_RUN_RE = re.compile(r"[\d\s\-().+/]{8,}")
_DIGITS_RE = re.compile(r"\D+")


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
    """True iff the LOCKED FACTS contain at least one fine-grained ``item:N:name`` /
    ``item:N:price`` fact (B2 advisory split, operator-approved Option 1).

    This is the switch between two regimes for ``offer_groups`` enforcement:

      - **item-level facts EXIST** → the brief is grouping real fine-grained
        item/price slots, so a true item-level collapse / wrong-slot / unknown-ref
        is still a STRUCTURAL bug the firewall hard-rejects (the prior P1 invariant
        is fully preserved).
      - **NO item-level facts** (production extracts each combo as ONE COARSE
        ``offer:N`` locked fact, or a pure-identity flyer) → there are no fine
        item/price slots to collapse; ``offer_groups`` only guides layout. Its
        findings are downgraded to advisory (non-blocking) warnings so a model that
        references non-existent ``item:N:price`` slots cannot fail-close a flyer
        whose required facts are fully covered by ``fact_refs``.

    Derived from the LOCKED FACTS ALONE (never any brief field) — same authority
    discipline as ``required_fact_ids`` / ``expected_offer_keys``."""
    for fact in locked_facts or []:
        if _ITEM_REF_RE.match(getattr(fact, "fact_id", "") or ""):
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
      (c) a commercial value (currency/price/percent-discount/phone-run) in a
          model-authored free-text field — it bypassed fact_refs (BLOCKER #3);
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
    # Locked IDENTITY/COMMERCIAL textual values (business/item name/tagline/claim)
    # must not appear in a free-text field — they would render into the background
    # OUTSIDE the overlay (Codex NEW-BYPASS). OCCASION/THEME/SEASONAL fact values
    # (campaign_title="Memorial Day", schedule) are EXCLUDED (Codex P2): they
    # legitimately belong in visual_direction ("Memorial Day patriotic Americana"),
    # and the commercial-SHAPE scan below still catches any price/phone/discount text
    # in those fields unconditionally. Boundary-aware; length>=4 skips trivial words.
    locked_text_values = [
        _norm_ws(f.value)
        for f in locked_facts or []
        if not _is_occasion_theme_fact_id(getattr(f, "fact_id", "") or "")
    ]
    locked_text_values = [v for v in locked_text_values if len(v) >= 4]
    for field_name, text in free_text_fields.items():
        # Commercial SHAPE (price/phone/discount) is ALWAYS rejected in every
        # free-text field — unconditional, regardless of fact-id classification.
        hit = _commercial_value_hit(text)
        if hit:
            errors.append(f"commercial value outside fact_refs: {field_name}: {hit}")
        norm_text = _norm_ws(text)
        for lv in locked_text_values:
            if re.search(r"(?<![a-z0-9])" + re.escape(lv) + r"(?![a-z0-9])", norm_text):
                errors.append(f"locked value outside fact_refs: {field_name}: {lv}")
                break

    # (c-textless, Codex Finding 3) the TEXTLESS-background prompt fields
    # (background_brief + visual_direction) must not (a) instruct the model to render
    # words into the background, nor (b) invent a non-price operational claim. These
    # ride OUTSIDE the overlay (no fact authority) and defeat the textless invariant.
    textless_fields = {
        "background_brief": brief.background_brief,
        "visual_direction": " ".join(
            [vd.theme_family, *vd.palette, *vd.motifs, *vd.visual_subjects]
        ),
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
    # one of the locked-fact values. A suppression naming a REAL locked commercial
    # value is already caught by the containment check below.
    for entry in brief.must_not_add:
        norm_entry = _norm_ws(entry)
        if not norm_entry:
            continue
        hit = _commercial_value_hit(entry)
        if hit:
            norm_hit = _norm_ws(hit)
            # Allowed only if the hit is part of a locked value (then containment
            # below owns it); an invented commercial shape is rejected here.
            if not any(norm_hit and norm_hit in lv for lv in locked_values):
                errors.append(f"must_not_add invents commercial value: {hit}")
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
