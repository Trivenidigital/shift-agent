"""Deterministic creative resolver for the Creative-Director `FlyerBrief` — CD v2.

Slice A Task A2. THIS module is the per-field firewall for CD v2's *creative*
fields (hero / supporting / marketing-hook / offer-priority / theme / mood). It
resolves the Hermes-proposed brief's creative refs against the LOCKED FACTS with
PER-FIELD DETERMINISTIC FALLBACK.

Three load-bearing invariants:

  1. **Pure deterministic** — no network, no I/O, no clock; same inputs ⇒ same
     output every time.
  2. **NEVER raises** — every lookup is guarded; a malformed brief or empty
     ``locked_facts`` yields the safe-default ``ResolvedCreativeDirection``
     (``""`` / ``[]`` / fixed default prominence/priority), never an exception.
  3. **NEVER invents** — it SELECTS values that already exist in ``locked_facts``;
     a name / hook text is ALWAYS either ``""`` or a verbatim locked-fact value.
     Theme / mood are pure taste (passthrough from ``visual_direction``) and carry
     no commercial value, so they are not fact-validated.

This is SEPARATE from the strict anti-fabrication ``validate`` in
``flyer_brief_validator.py`` (the fact-authority firewall). That module owns
required-fact enforcement / fail-closed rejection; THIS module owns graceful
per-field selection-with-fallback and is intentionally permissive: it never
rejects a brief, it just resolves the best grounded value it can. The validator
is NOT imported and NOT modified here.

Import layout mirrors the sibling firewall modules: flat ``from schemas import
...`` on the VPS (``/opt/shift-agent`` + ``src/platform`` on ``sys.path``), with
a package-style fallback for the repo-relative layout. DORMANT in slice-1 —
nothing on the live render path imports it unless the CD v2 path is enabled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from schemas import FlyerLockedFact

try:  # sibling FlyerBrief — flat on the VPS, package-style in the repo tree
    from flyer_brief import FactRef, FlyerBrief  # type: ignore
except ImportError:  # pragma: no cover - import-path shim
    from agents.flyer.flyer_brief import FactRef, FlyerBrief


# An item-name fact id: ``item:<N>:name`` (N is the item index). "First" item =
# lowest index. Only ``item:*:name`` is a valid hero / supporting source kind.
_ITEM_NAME_RE = re.compile(r"^item:(\d+):name$")

# Allowed hook-source fact-id kinds: the flat price (``pricing_structure``), an
# offer (``offer:<N>``), or an offer price (``offer_price``). An item NAME is NOT
# a valid hook source.
_OFFER_RE = re.compile(r"^offer:\d+$")
_HOOK_SOURCE_EXACT = frozenset({"pricing_structure", "offer_price"})

_VALID_PRIORITIES = frozenset({"high", "medium", "low"})


@dataclass(frozen=True)
class ResolvedCreativeDirection:
    hero_name: str
    supporting_names: list[str]  # excludes hero, de-duped, order-preserving
    hook_text: str
    hook_prominence: str  # "high"|"medium"|"low"
    offer_priority: str  # "high"|"medium"|"low"
    theme_family: str
    mood: str


def _locked_value_by_id(
    locked_facts: Sequence[FlyerLockedFact], fact_id: str
) -> Optional[str]:
    """The value of the locked fact whose id == ``fact_id``, or None. Guarded so a
    malformed fact (missing attrs) can never raise."""
    if not fact_id:
        return None
    for fact in locked_facts or ():
        try:
            if getattr(fact, "fact_id", None) == fact_id:
                return getattr(fact, "value", None)
        except Exception:  # pragma: no cover - defensive: never raise
            continue
    return None


def _ref_fact_id(ref: Optional[FactRef]) -> str:
    """The locked-fact id a ``FactRef`` selects, or "" when it is None or a
    ``raw_span`` (a raw_span is NOT a locked fact id ⇒ no selection)."""
    if ref is None:
        return ""
    try:
        return (getattr(ref, "fact_id", None) or "").strip()
    except Exception:  # pragma: no cover - defensive: never raise
        return ""


def _first_item_name(locked_facts: Sequence[FlyerLockedFact]) -> str:
    """The value of the lowest-index ``item:<N>:name`` locked fact, or ""."""
    best_index: Optional[int] = None
    best_value = ""
    for fact in locked_facts or ():
        try:
            fid = getattr(fact, "fact_id", None) or ""
            m = _ITEM_NAME_RE.match(fid)
            if not m:
                continue
            index = int(m.group(1))
            if best_index is None or index < best_index:
                best_index = index
                best_value = getattr(fact, "value", None) or ""
        except Exception:  # pragma: no cover - defensive: never raise
            continue
    return best_value


def _resolve_item_name(
    ref: Optional[FactRef], locked_facts: Sequence[FlyerLockedFact]
) -> str:
    """Value of an ``item:*:name`` locked fact selected by ``ref``, or "" if ``ref``
    does not resolve to a locked ``item:*:name``."""
    fid = _ref_fact_id(ref)
    if not fid or not _ITEM_NAME_RE.match(fid):
        return ""
    value = _locked_value_by_id(locked_facts, fid)
    return value or ""


def _is_hook_source_id(fact_id: str) -> bool:
    """True iff ``fact_id`` is an allowed hook source kind: pricing_structure,
    offer:<N>, or offer_price."""
    if fact_id in _HOOK_SOURCE_EXACT:
        return True
    return bool(_OFFER_RE.match(fact_id))


def _pricing_structure_value(locked_facts: Sequence[FlyerLockedFact]) -> str:
    """The value of the ``pricing_structure`` locked fact, or ""."""
    return _locked_value_by_id(locked_facts, "pricing_structure") or ""


def _resolve_hero_name(
    brief: FlyerBrief, locked_facts: Sequence[FlyerLockedFact]
) -> str:
    hero = _resolve_item_name(getattr(brief, "hero_ref", None), locked_facts)
    if hero:
        return hero
    return _first_item_name(locked_facts)


def _resolve_supporting_names(
    brief: FlyerBrief, locked_facts: Sequence[FlyerLockedFact], hero_name: str
) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for ref in getattr(brief, "supporting_refs", None) or ():
        value = _resolve_item_name(ref, locked_facts)
        if not value:
            continue  # fabricated / raw_span / non-item ⇒ dropped
        if value == hero_name:
            continue  # hero excluded from supporting
        if value in seen:
            continue  # de-dup
        seen.add(value)
        names.append(value)
    return names


def _resolve_hook(
    brief: FlyerBrief, locked_facts: Sequence[FlyerLockedFact]
) -> tuple[str, str]:
    hook = getattr(brief, "marketing_hook", None)
    if hook is not None:
        fid = _ref_fact_id(getattr(hook, "text_ref", None))
        if fid and _is_hook_source_id(fid):
            value = _locked_value_by_id(locked_facts, fid)
            if value:
                prominence = getattr(hook, "prominence", None) or "high"
                return value, prominence
    # Fallback: a pricing_structure fact becomes the hook at high prominence.
    pricing = _pricing_structure_value(locked_facts)
    if pricing:
        return pricing, "high"
    return "", "low"


def _resolve_offer_priority(
    brief: FlyerBrief, locked_facts: Sequence[FlyerLockedFact]
) -> str:
    try:
        proposed = getattr(brief, "offer_priority", None) or ""
    except Exception:  # pragma: no cover - defensive: never raise
        proposed = ""
    if proposed in _VALID_PRIORITIES:
        return proposed
    return "high" if _pricing_structure_value(locked_facts) else "medium"


def _passthrough_theme_mood(brief: FlyerBrief) -> tuple[str, str]:
    """Theme family + mood are pure taste — passthrough from visual_direction with
    no fact validation. Guarded so a missing visual_direction never raises."""
    try:
        vd = getattr(brief, "visual_direction", None)
        theme = (getattr(vd, "theme_family", None) or "") if vd is not None else ""
        mood = (getattr(vd, "mood", None) or "") if vd is not None else ""
        return theme, mood
    except Exception:  # pragma: no cover - defensive: never raise
        return "", ""


def resolve_creative_direction(
    brief: FlyerBrief, locked_facts: Sequence[FlyerLockedFact]
) -> ResolvedCreativeDirection:
    """Resolve the brief's creative refs against ``locked_facts`` with per-field
    deterministic fallback. PURE; NEVER raises; NEVER invents (only selects values
    already present in ``locked_facts``)."""
    facts = locked_facts or ()
    hero_name = _resolve_hero_name(brief, facts)
    supporting_names = _resolve_supporting_names(brief, facts, hero_name)
    hook_text, hook_prominence = _resolve_hook(brief, facts)
    offer_priority = _resolve_offer_priority(brief, facts)
    theme_family, mood = _passthrough_theme_mood(brief)
    return ResolvedCreativeDirection(
        hero_name=hero_name,
        supporting_names=supporting_names,
        hook_text=hook_text,
        hook_prominence=hook_prominence,
        offer_priority=offer_priority,
        theme_family=theme_family,
        mood=mood,
    )
