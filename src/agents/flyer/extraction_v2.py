"""Flyer Studio v2 — Layer 1: LLM fact extraction with source-parity guard.

Drift-check tag: extends-Hermes.

Hermes-first analysis: Hermes owns the LLM gateway (OpenRouter); this module is
per-customer business logic — the structured-output extraction contract, the
source-parity guard, and the FlyerLockedFact mapping. No substrate re-implemented.

WS1 of the accepted v2 spec (2026-07-03). Promoted from the Workstream #0 Leg 2
Arm B harness, whose pre-registered gate this design passed (9/10 truth-accepted,
10/10 extraction truth-parity vs the deployed regex layer's 5/10 — evidence:
/tmp/ws0-leg2/summary.json, review report §Leg 2).

Contract (mirrors facts.extract_text_facts so the seam swap is a name change):
    extract_text_facts_v2(fields, raw_request, *, message_id="",
                          profile_business_name="", transport=None)
        -> (list[FlyerLockedFact], V2ExtractionReport)

Invariants:
- SOURCE-PARITY GUARD (mandatory, load-bearing): every fact value's alphanumeric
  tokens must appear verbatim in the brief text, except the explicitly authorized
  bounded spelling correction below; violating facts are DROPPED and
  logged in the report, never locked. Whole-token membership (word-boundary):
  sub-token collisions like a hallucinated $9.99 against a brief's $4.99 are
  rejected — fabrication defense holds at the producer for the classes QA
  cannot see downstream.
- SPELLING EXCEPTION: the same extraction model may identify an unmistakable
  typo in an item name or campaign title. The original must be an exact source
  phrase, and only one alphabetic word of at least five letters may change,
  preserving its first/last characters, with at most two edits and >=0.8
  similarity. These bounds limit changes; the model owns semantic equivalence.
  Invalid/ambiguous proposals retain the original. Accepted name spans may be
  mirrored in campaign/price/offer copy, leaving every other byte untouched.
  Identity, numeric values, schedule and restriction semantics remain protected.
  Display notes follow these accepted pairs; raw input stays intact. Accepted
  and propagated pairs are returned in the report and existing audit reason.
- FAIL-CLOSED: any transport/parse failure raises ExtractionV2Error — callers
  fall back to the legacy extractor and log; v2 never silently returns empty.
- Deterministic in CI: `transport` is injectable; tests replay recorded LLM
  responses (fixtures) so no network/key/spend in CI. Live drift is covered by
  the on-box golden eval + the shadow watcher (customer's-path rule).
- Profile hydration stays at the CALLERS (unchanged, proven live) — this module
  returns text-derived facts only.
"""
from __future__ import annotations

import json
from difflib import SequenceMatcher
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# `schemas` resolves in both layouts: flat on the VPS (/opt/shift-agent/schemas.py)
# and via the src/platform path in tests (conftest).
from schemas import FlyerLockedFact

EXTRACTION_MODEL = "openai/gpt-4o-mini"  # pinned by the v2 spec (Layer 1)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TRANSPORT_TIMEOUT_SEC = 45

# Original values stay verbatim. Optional model-proposed spelling corrections
# pass the separate bounded exception before locking canonical name/title copy.
_SYSTEM_PROMPT = (
    "Extract flyer facts from the customer brief. Return ONLY JSON: "
    '{"business_name":str|null,"campaign_title":str|null,"pricing_structure":str|null,'
    '"schedule":str|null,"location":str|null,"contact_phone":str|null,'
    '"items":[{"name":str,"price":str|null,"corrected_name":str|null,"spelling_judgment":str|null}],'
    '"corrected_campaign_title":str|null,"campaign_spelling_judgment":str|null,"offers":[{"text":str}]}. '
    "Rules: copy values VERBATIM from the brief text; every menu/product item goes in "
    "items; pricing_structure is the offer/price statement; never invent or infer "
    "anything absent from the text. "
    "campaign_title is the short offering/event phrase from the brief (for example Lunch Buffet), "
    "not a greeting, request instruction, or business identity. Preserve the complete days/time range in schedule. "
    "offers contains additional customer-visible copy, restrictions and disclaimers verbatim, including "
    "test/not-a-real-offer labels when supplied. Do not duplicate pricing_structure in offers. "
    "For offers, preserve the whole supplied sentence or clause, including all negations and conditions. "
    "Keep items.name and campaign_title VERBATIM. For an unmistakable spelling error only, also return "
    "items.corrected_name and items.spelling_judgment='clear_spelling_error'; for the campaign use "
    "corrected_campaign_title and campaign_spelling_judgment='clear_spelling_error'. "
    "These corrections must name the SAME food/product/event, never substitute another. "
    "Preserve valid regional spellings/transliterations and all ambiguous names. Do not correct brand/business "
    "names, intentional creative wording, quoted names or spelling the customer explicitly asks to retain. "
    "Do not correct identity, addresses, contacts, numbers, prices, quantities, schedules, restrictions or negation. "
    "Omit correction fields when uncertain. No translation, added words, removed words or rewriting. "
    'Additionally return "occasion": one of "july4","diwali","ramadan","thanksgiving" '
    "ONLY when the brief EXPLICITLY names that holiday/festival; generic festive words "
    '(celebration, special, sale, seasonal) are NOT occasions; when unsure return "none". '
    "A wrong festival is worse than none."
)

# Derived from the schema enum (PR #547 review minor): one source of truth.
try:
    from typing import get_args as _get_args

    from schemas import FlyerOccasion as _FlyerOccasion
    _KNOWN_OCCASIONS = {v for v in _get_args(_FlyerOccasion) if v != "none"}
except Exception:  # noqa: BLE001 — deploy-order skew: fall back to the literal set
    _KNOWN_OCCASIONS = {"july4", "diwali", "ramadan", "thanksgiving"}
_SCALAR_FACT_IDS = ("business_name", "campaign_title", "pricing_structure",
                    "schedule", "location", "contact_phone")


class ExtractionV2Error(RuntimeError):
    """Transport/parse failure — callers MUST fall back to legacy extraction."""


@dataclass
class V2ExtractionReport:
    """Self-report for the owner-review details block + shadow comparison
    (spec WS1: surface what gates can't see)."""
    model: str = EXTRACTION_MODEL
    items_locked: int = 0
    scalars_locked: int = 0
    dropped_by_parity: list = field(default_factory=list)  # ["fact_id=value", ...]
    spelling_corrections: list[dict[str, str]] = field(default_factory=list)
    # Interpretive, structurally OUTSIDE the fact contract (schema ruling
    # 2026-07-04): reported for observability + project plumbing, never
    # counted in items_locked/scalars_locked, never parity-checked.
    occasion: str = "none"

    def summary_line(self) -> str:
        parts = [f"items locked: {self.items_locked}", f"fields: {self.scalars_locked}"]
        if self.dropped_by_parity:
            parts.append(f"dropped (not verbatim in brief): {len(self.dropped_by_parity)}")
        return " | ".join(parts)

    def audit_reason(self) -> str:
        if not self.spelling_corrections:
            return ""
        pairs = list(dict.fromkeys(f"{c['original'][:38]}->{c['corrected'][:38]}"
                                  for c in self.spelling_corrections))
        return (f"spelling {len(self.spelling_corrections)}: " + "; ".join(pairs))[:120]


def _default_transport(system: str, user: str) -> str:
    """One OpenRouter chat call -> raw content string. Injectable for tests."""
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise ExtractionV2Error("OPENROUTER_API_KEY missing")
    payload = {
        "model": EXTRACTION_MODEL, "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        OPENROUTER_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=TRANSPORT_TIMEOUT_SEC) as resp:
            doc = json.loads(resp.read().decode("utf-8", errors="replace"))
        content = doc["choices"][0]["message"]["content"]
        if not isinstance(content, str):  # refusals / empty completions: content null
            raise ExtractionV2Error("extraction transport returned non-string content")
        return content
    except ExtractionV2Error:
        raise
    except (OSError, KeyError, IndexError, TypeError, ValueError,
            urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise ExtractionV2Error(f"extraction transport failed: {type(exc).__name__}: {str(exc)[:120]}") from exc


def _tokens(value: str) -> list[str]:
    return [t for t in "".join(c if c.isalnum() else " " for c in value.lower()).split() if t]


def value_has_source_parity(value: str, brief_text: str) -> bool:
    """True iff every alphanumeric token of ``value`` appears as a WHOLE TOKEN
    in the brief (word-boundary membership, not substring: a hallucinated
    "$9.99" never passes against "$4.99"; "rice" never passes against "price"
    — PR #535 review F2, proven exploit class). This is the only line of
    defense for facts downstream QA verifies AGAINST (QA checks the render
    against locked facts, so a fabricated locked fact is invisible later)."""
    toks = _tokens(str(value))
    return bool(toks) and set(toks) <= set(_tokens(brief_text))


def _fact(fact_id: str, value: str) -> FlyerLockedFact:
    return FlyerLockedFact(fact_id=fact_id, label=fact_id, value=value,
                           source="customer_text", required=True)


def _bounded_spelling(original: str, corrected, judgment, raw: str, *, protected: tuple[str, ...]) -> str:
    """Validate the blast radius of a model decision, never choose a spelling."""
    if judgment != "clear_spelling_error" or not isinstance(corrected, str):
        return original
    corrected = corrected.strip()
    if not corrected or original.casefold() in {v.casefold() for v in protected if v}:
        return original
    if not re.search(r"(?<!\w)" + re.escape(original) + r"(?!\w)", raw, flags=re.IGNORECASE):
        return original
    old_words, new_words = original.split(), corrected.split()
    if len(old_words) != len(new_words):
        return original
    changed = 0
    protected_words = {word for identity in protected for word in _tokens(identity)}
    for old, new in zip(old_words, new_words):
        if old.casefold() == new.casefold():
            continue
        if old.casefold() in protected_words or new.casefold() in protected_words:
            return original
        if not old.isalpha() or not new.isalpha() or min(len(old), len(new)) < 5:
            return original
        # Restrict automatic changes to internal spelling, preserving affixes
        # (e.g. Unsweetened must not become Sweetened via a short deletion).
        if old[0].casefold() != new[0].casefold() or old[-1].casefold() != new[-1].casefold():
            return original
        matcher = SequenceMatcher(None, old.casefold(), new.casefold(), autojunk=False)
        edits = sum(max(b-a, d-c) for op, a, b, c, d in matcher.get_opcodes() if op != "equal")
        if edits > 2 or matcher.ratio() < 0.8:
            return original
        changed += 1
    return corrected if changed == 1 else original


def apply_accepted_spelling(text: str, corrections: list[dict[str, str]]) -> str:
    """Propagate only accepted whole spans, leaving every other byte intact."""
    replacements: dict[str, str] = {}
    originals: dict[str, str] = {}
    for correction in corrections:
        key = correction["original"].casefold()
        value = correction["corrected"]
        if key in replacements and replacements[key].casefold() != value.casefold():
            raise ExtractionV2Error("conflicting spelling targets")
        replacements.setdefault(key, value)
        originals.setdefault(key, correction["original"])
    if not replacements:
        return text
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(s) for s in sorted(originals.values(), key=len, reverse=True)) + r")(?!\w)"
    return re.sub(pattern, lambda match: replacements.get(match.group(0).casefold(), match.group(0)), text, flags=re.IGNORECASE)


def extract_text_facts_v2(fields, raw_request: str, *, message_id: str = "",
                          profile_business_name: str = "",
                          transport=None):
    """LLM extraction + source-parity guard. Returns (facts, report).
    Raises ExtractionV2Error on transport/parse failure (callers fall back to
    the legacy extractor — fail-closed, never silently empty)."""
    raw = (raw_request or "").strip()
    if not raw:
        return [], V2ExtractionReport()
    call = transport or _default_transport
    content = call(_SYSTEM_PROMPT, raw)
    try:
        doc = json.loads(content)
        if not isinstance(doc, dict):
            raise ValueError("non-object extraction payload")
    except (TypeError, ValueError) as exc:  # TypeError: injected non-str content
        raise ExtractionV2Error(f"extraction parse failed: {type(exc).__name__}") from exc

    brief_lower = raw.lower()
    report = V2ExtractionReport()
    occ = str(doc.get("occasion") or "").strip().lower()  # lowercase before lookup
    report.occasion = occ if occ in _KNOWN_OCCASIONS else "none"  # fail-neutral
    facts: list[FlyerLockedFact] = []
    protected = (profile_business_name, str(doc.get("business_name") or ""))

    def corrected_fact(fid: str, original: str, corrected, judgment) -> FlyerLockedFact:
        value = _bounded_spelling(original, corrected, judgment, raw, protected=protected)
        previous = next((c for c in report.spelling_corrections if c["original"].casefold() == original.casefold()), None)
        if previous is not None:
            if value != original and value.casefold() != previous["corrected"].casefold():
                raise ExtractionV2Error("conflicting spelling targets")
            value = previous["corrected"]
        if value != original:
            report.spelling_corrections.append({"fact_id": fid, "original": original, "corrected": value})
        return _fact(fid, value).model_copy(update={"source_message_id": message_id})

    for fid in _SCALAR_FACT_IDS:
        v = doc.get(fid)
        if v is None or not str(v).strip():
            continue
        v = str(v).strip()
        if value_has_source_parity(v, brief_lower):
            facts.append(corrected_fact(fid, v, doc.get("corrected_campaign_title"),
                                        doc.get("campaign_spelling_judgment"))
                         if fid == "campaign_title" else _fact(fid, v))
            report.scalars_locked += 1
        else:
            report.dropped_by_parity.append(f"{fid}={v[:60]}")

    n = 0
    for it in (doc.get("items") or []):
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        price = str(it.get("price") or "").strip() if it.get("price") else ""
        if not name:
            continue
        if not value_has_source_parity(name, brief_lower):
            report.dropped_by_parity.append(f"item:{name[:40]}")
            continue
        facts.append(corrected_fact(f"item:{n}:name", name, it.get("corrected_name"), it.get("spelling_judgment")))
        report.items_locked += 1
        if price and value_has_source_parity(price, brief_lower):
            facts.append(_fact(f"item:{n}:price", price))
        n += 1

    raw_offers = doc.get("offers") or []
    if not isinstance(raw_offers, list):
        raise ExtractionV2Error("offers must be an array")
    # Secondary copy must preserve a whole supplied statement. Substring
    # grounding would turn 'Not valid on weekends' into 'valid on weekends'.
    # This validates model-selected copy; it does not infer facts from prose.
    def normalize_copy(text: str) -> str:
        return " ".join(text.casefold().split()).strip(" .!?;:")
    source_statements = {normalize_copy(part) for part in re.split(r"(?<=[.!?;:])\s+|\n+", raw)}
    for offer in raw_offers:
        # Match semantic_brief's existing coercion contract: providers may
        # return either verbatim strings or objects carrying a text value.
        if isinstance(offer, str):
            value = offer.strip()
        elif isinstance(offer, dict):
            value = str(offer.get("text") or "").strip()
        else:
            continue
        if not value or any(f.value == value for f in facts):
            continue
        # Secondary copy can contain negation/restrictions. Token membership
        # alone would permit reordering those into the opposite claim.
        if value_has_source_parity(value, brief_lower) and normalize_copy(value) in source_statements:
            index = sum(f.fact_id.startswith("offer:") for f in facts)
            facts.append(_fact(f"offer:{index}", value).model_copy(update={"source_message_id": message_id}))
            report.scalars_locked += 1
        else:
            report.dropped_by_parity.append(f"offer={value[:60]}")
    accepted_names = list(report.spelling_corrections)
    for index, fact in enumerate(facts):
        if fact.fact_id not in {"campaign_title", "pricing_structure"} and not fact.fact_id.startswith("offer:"):
            continue
        canonical = apply_accepted_spelling(fact.value, accepted_names)
        if canonical != fact.value:
            report.spelling_corrections.append({"fact_id": fact.fact_id, "original": fact.value, "corrected": canonical})
            facts[index] = fact.model_copy(update={"value": canonical, "source_message_id": message_id})
    return facts, report


__all__ = [
    "EXTRACTION_MODEL",
    "ExtractionV2Error",
    "V2ExtractionReport",
    "extract_text_facts_v2",
    "value_has_source_parity",
]
