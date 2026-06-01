"""Bounded creative planner — slice 2 (flag-gated, INERT BY CONSTRUCTION).

Design: tasks/flyer-bounded-creative-planner-contract-design.md.

Hermes infers SAFE creative content (category-appropriate item names; later
headlines/section labels) for vague customer requests. This module produces
*candidates* only; candidates can become facts ONLY by passing through the
firewall gate (slice 3). Until a firewall exists, `load_firewall()` returns None
and `materialize_inferred(..., firewall=None)` returns [] — so the planner is
inert even with the flag on (the structural safety interlock).

HARD INVARIANTS (enforced here + verified by tests):
- This module NEVER produces a hard fact (price/date/phone/address/identity/
  discount/claim). It only emits `item:*:name`-class candidates as
  source="hermes_inferred". Hard facts stay in the grounded extractor (facts.py).
- No fact materializes without a firewall (fail-closed).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from schemas import FlyerConfig, FlyerLockedFact, FlyerRequestFields

try:  # reuse the deployed OpenRouter seam (flat layout on the VPS)
    from flyer_semantic_brief import OPENROUTER_URL, _openrouter_key  # type: ignore
except ImportError:
    from agents.flyer.semantic_brief import OPENROUTER_URL, _openrouter_key

import os

CREATIVE_PLANNER_MODEL = (
    os.environ.get("FLYER_CREATIVE_PLANNER_MODEL")
    or os.environ.get("HERMES_DEFAULT_MODEL")
    or "openai/gpt-4o-mini"
)
# Creativity needs > 0 temperature (the grounded extractor runs at 0.0). Capped
# modestly so suggestions stay sensible.
CREATIVE_PLANNER_TEMPERATURE = 0.5
CREATIVE_PLANNER_TIMEOUT_SEC = 30
CREATIVE_PLANNER_MAX_ITEMS = 12


@dataclass(frozen=True)
class CreativeCandidate:
    """A single inferred-content suggestion (slice 2: kind == 'item'). NOT a fact
    until cleared by the firewall and materialized."""
    kind: str  # "item" (slice 2); "headline" / "section" land later
    value: str


# A provider takes (fields, raw_request) and returns candidate item-name strings,
# or None on failure. Pulled out so tests can inject a deterministic provider.
CreativePlannerProvider = Callable[[FlyerRequestFields, str], Optional[Sequence[str]]]


def load_firewall():
    """Return the hard-fact firewall, or None if it does not exist yet.

    SLICE 2: always None — the firewall lands in slice 3. This is the single
    capability probe behind the structural interlock: no firewall ⇒ the planner
    never activates and nothing materializes."""
    return None


def is_active(flyer_cfg: FlyerConfig) -> bool:
    """The planner runs only when the flag is enabled AND a firewall exists to
    clear its output. In slice 2 `load_firewall()` is None ⇒ always False, so
    flipping the flag early cannot run the planner or emit any fact.

    Takes the FlyerConfig (the caller passes `config.flyer`)."""
    planner_cfg = getattr(flyer_cfg, "creative_planner", None)
    if planner_cfg is None or not getattr(planner_cfg, "enabled", False):
        return False
    return load_firewall() is not None


def build_creative_planner_provider() -> Optional[CreativePlannerProvider]:
    """OpenRouter creative-suggestion provider (temp > 0). Mirrors the deployed
    semantic-brief provider shape. Returns None if no key (then the planner is a
    no-op). NOTE: in slice 2 this is never called at runtime (is_active is False)."""
    key = _openrouter_key()
    if not key or "PLACEHOLDER" in key:
        return None

    def provider(fields: FlyerRequestFields, raw_request: str) -> Optional[Sequence[str]]:
        prompt = {
            "task": (
                "Suggest category-appropriate menu/service ITEM NAMES for a flyer, "
                "inferring sensible items when the customer asks vaguely (e.g. "
                "'8 famous South Indian breakfast items')."
            ),
            "customer_message": raw_request,
            "existing_fields": {
                "event_or_business_name": fields.event_or_business_name,
                "notes": fields.notes,
                "style_preference": fields.style_preference,
            },
            "schema": {"items": ["short item name (no price, no claim)"]},
            "rules": [
                "Return JSON only: {\"items\": [...]}.",
                "Item NAMES only — never a price, date, phone, address, discount, "
                "or any claim (those are not yours to invent).",
                "Pick items appropriate to the business/cuisine/category implied.",
                f"At most {CREATIVE_PLANNER_MAX_ITEMS} items.",
            ],
        }
        payload = {
            "model": CREATIVE_PLANNER_MODEL,
            "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            "response_format": {"type": "json_object"},
            "temperature": CREATIVE_PLANNER_TEMPERATURE,
        }
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=CREATIVE_PLANNER_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8")
            doc = json.loads(body)
            content = doc["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            items = parsed.get("items") if isinstance(parsed, dict) else None
            return items if isinstance(items, list) else None
        except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError,
                urllib.error.URLError, urllib.error.HTTPError):
            return None

    return provider


def plan_creative_items(
    fields: FlyerRequestFields,
    raw_request: str,
    *,
    provider: Optional[CreativePlannerProvider] = None,
) -> list[CreativeCandidate]:
    """Produce candidate item names (NOT facts; NOT grounding-filtered; NOT
    materialized). Returns [] when there is no provider/key (inert)."""
    prov = provider or build_creative_planner_provider()
    if prov is None:
        return []
    raw = prov(fields, raw_request) or []
    out: list[CreativeCandidate] = []
    for value in raw:
        if isinstance(value, str) and value.strip():
            out.append(CreativeCandidate(kind="item", value=value.strip()))
        if len(out) >= CREATIVE_PLANNER_MAX_ITEMS:
            break
    return out


def materialize_inferred(
    candidates: Sequence[CreativeCandidate], *, firewall=None
) -> list[FlyerLockedFact]:
    """THE ONLY path from planner candidates to facts — the structural safety
    interlock. Without a firewall to clear candidates, returns [] (fail-closed).
    The firewall (slice 3) decides which candidates are safe (e.g. rejects an
    'item name' that is actually a claim like 'Free Delivery')."""
    if firewall is None:
        return []
    cleared = firewall.clear(candidates)  # firewall API lands in slice 3
    facts: list[FlyerLockedFact] = []
    for index, cand in enumerate(cleared):
        if cand.kind != "item":
            continue
        facts.append(
            FlyerLockedFact(
                fact_id=f"item:{index}:name",
                label="Item",
                value=cand.value,
                source="hermes_inferred",
            )
        )
    return facts
