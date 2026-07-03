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
  tokens must appear verbatim in the brief text; violating facts are DROPPED and
  logged in the report, never locked. The "system never invents customer-facing
  facts" invariant now holds at the producer.
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
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# `schemas` resolves in both layouts: flat on the VPS (/opt/shift-agent/schemas.py)
# and via the src/platform path in tests (conftest).
from schemas import FlyerLockedFact

EXTRACTION_MODEL = "openai/gpt-4o-mini"  # pinned by the v2 spec (Layer 1)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TRANSPORT_TIMEOUT_SEC = 45

# The extraction contract. Values must be VERBATIM substrings of the brief —
# the parity guard enforces it after the model answers.
_SYSTEM_PROMPT = (
    "Extract flyer facts from the customer brief. Return ONLY JSON: "
    '{"business_name":str|null,"campaign_title":str|null,"pricing_structure":str|null,'
    '"schedule":str|null,"location":str|null,"contact_phone":str|null,'
    '"items":[{"name":str,"price":str|null}]}. '
    "Rules: copy values VERBATIM from the brief text; every menu/product item goes in "
    "items; pricing_structure is the offer/price statement; never invent or infer "
    "anything absent from the text."
)
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

    def summary_line(self) -> str:
        parts = [f"items locked: {self.items_locked}", f"fields: {self.scalars_locked}"]
        if self.dropped_by_parity:
            parts.append(f"dropped (not verbatim in brief): {len(self.dropped_by_parity)}")
        return " | ".join(parts)


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
        return doc["choices"][0]["message"]["content"]
    except (OSError, KeyError, IndexError, TypeError, ValueError,
            urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise ExtractionV2Error(f"extraction transport failed: {type(exc).__name__}: {str(exc)[:120]}") from exc


def _tokens(value: str) -> list[str]:
    return [t for t in "".join(c if c.isalnum() else " " for c in value.lower()).split() if t]


def value_has_source_parity(value: str, brief_lower: str) -> bool:
    """True iff every alphanumeric token of ``value`` appears in the brief.
    The guard that makes 'never invents facts' hold at the producer."""
    toks = _tokens(str(value))
    return bool(toks) and all(t in brief_lower for t in toks)


def _fact(fact_id: str, value: str) -> FlyerLockedFact:
    return FlyerLockedFact(fact_id=fact_id, label=fact_id, value=value,
                           source="customer_text", required=True)


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
    except ValueError as exc:
        raise ExtractionV2Error(f"extraction parse failed: {type(exc).__name__}") from exc

    brief_lower = raw.lower()
    report = V2ExtractionReport()
    facts: list[FlyerLockedFact] = []

    for fid in _SCALAR_FACT_IDS:
        v = doc.get(fid)
        if v is None or not str(v).strip():
            continue
        v = str(v).strip()
        if value_has_source_parity(v, brief_lower):
            facts.append(_fact(fid, v))
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
        facts.append(_fact(f"item:{n}:name", name))
        report.items_locked += 1
        if price and value_has_source_parity(price.replace("$", " "), brief_lower):
            facts.append(_fact(f"item:{n}:price", price))
        n += 1

    return facts, report


__all__ = [
    "EXTRACTION_MODEL",
    "ExtractionV2Error",
    "V2ExtractionReport",
    "extract_text_facts_v2",
    "value_has_source_parity",
]
