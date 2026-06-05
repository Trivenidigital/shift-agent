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
    from flyer_brief import FactRef, FlyerBrief  # type: ignore
except ImportError:  # pragma: no cover - import-path shim
    from agents.flyer.flyer_brief import FactRef, FlyerBrief


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


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


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
    """
    errors: list[str] = []
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
    # Locked TEXTUAL values (business/item/tagline) must not appear in a free-text
    # field either — they would render into the background OUTSIDE the overlay
    # (Codex NEW-BYPASS). Boundary-aware; length>=4 skips trivial common words.
    locked_text_values = [
        v for v in (_norm_ws(f.value) for f in locked_facts or []) if len(v) >= 4
    ]
    for field_name, text in free_text_fields.items():
        hit = _commercial_value_hit(text)
        if hit:
            errors.append(f"commercial value outside fact_refs: {field_name}: {hit}")
        norm_text = _norm_ws(text)
        for lv in locked_text_values:
            if re.search(r"(?<![a-z0-9])" + re.escape(lv) + r"(?![a-z0-9])", norm_text):
                errors.append(f"locked value outside fact_refs: {field_name}: {lv}")
                break

    # (d) must_not_add may not CONTAIN a locked-fact value (would suppress a real
    # fact). Containment as a UNIT (not exact-match): "omit Non Veg Combo" must be
    # caught. Boundaries are alphanumeric-aware (NOT \b, which fails on values that
    # start/end in punctuation like "$49.99") — so "Combos" does not match "Combo",
    # but "$49.99" still matches when flanked by space/start/end.
    locked_values = [_norm_ws(f.value) for f in locked_facts or [] if (f.value or "").strip()]
    for entry in brief.must_not_add:
        norm_entry = _norm_ws(entry)
        if not norm_entry:
            continue
        for lv in locked_values:
            if lv and re.search(r"(?<![a-z0-9])" + re.escape(lv) + r"(?![a-z0-9])", norm_entry):
                errors.append(f"must_not_add contains locked value: {entry}")
                break

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

    return ValidationResult(ok=not errors, errors=errors)


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
