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
     a name / hook text is ALWAYS either ``""`` or a locked-fact value (a hook text
     is verbatim; a hero name is verbatim for an ``item:*:name`` flyer, and for a
     combo flyer is the offer's SUBJECT NAME derived from the offer's OWN value by
     TRUNCATION ONLY — so it is always a substring of a locked fact value, FIX D).
     Theme / mood are visual-taste strings: a model could otherwise smuggle a
     fabricated COMMERCIAL value through them (e.g. ``mood="$5 off"``), which the
     strict ``fact_refs`` firewall never scans. So they are validated to carry NO
     UNGROUNDED commercial value — each is scanned with the SAME deterministic
     commercial scanner the brief firewall uses (``_first_ungrounded_commercial``),
     and a field that carries any commercial value NOT present in the locked facts
     is defaulted to ``""``. A grounded number (one whose value IS a locked fact)
     is kept, so legitimate taste like ``"$8.99 hero"`` is not over-stripped.

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

# Reuse the brief firewall's SHARED commercial-taste scrubber — single source of
# truth, no parallel commercial regex here. ``scrub_ungrounded_commercial_taste(
# theme, mood, allowed_values)`` defaults a field to "" when it carries a commercial
# value NOT grounded in ``allowed_values`` (overlay-rendered locked-fact values),
# keeping grounded values verbatim; it wraps the firewall's
# ``_first_ungrounded_commercial`` scanner and is ALSO reused by the advisory scene
# path. ``_norm_ws`` normalizes the locked values exactly the way the validator's
# call sites do, so the grounding comparison matches the firewall's. Flat-on-VPS
# first, package-style fallback (mirrors the FlyerBrief import above).
try:  # pragma: no cover - import-path shim
    from flyer_brief_validator import (  # type: ignore
        _norm_ws,
        scrub_campaign_narrative,
        scrub_ungrounded_commercial_taste,
    )
except ImportError:  # pragma: no cover - import-path shim
    from agents.flyer.flyer_brief_validator import (
        _norm_ws,
        scrub_campaign_narrative,
        scrub_ungrounded_commercial_taste,
    )


# An item-name fact id: ``item:<N>:name`` (N is the item index). "First" item =
# lowest index. Only ``item:*:name`` is a valid hero / supporting source kind.
_ITEM_NAME_RE = re.compile(r"^item:(\d+):name$")

# Allowed hook-source fact-id kinds: the flat price (``pricing_structure``), an
# offer (``offer:<N>``), or an offer price (``offer_price``). An item NAME is NOT
# a valid hook source.
_OFFER_RE = re.compile(r"^offer:\d+$")
# Indexed offer id: ``offer:<N>`` — used to find the PRIMARY (lowest-index) offer
# for the combo-flyer hero fallback (FIX D).
_OFFER_INDEX_RE = re.compile(r"^offer:(\d+)$")
_HOOK_SOURCE_EXACT = frozenset({"pricing_structure", "offer_price"})

_VALID_PRIORITIES = frozenset({"high", "medium", "low"})

# ── offer SUBJECT-NAME derivation (FIX D) ───────────────────────────────────
# A combo offer's locked value carries the subject name as its HEADLINE, e.g.
# ``"Veg Combo - $12.99: Includes 2 curries, dessert"`` → subject "Veg Combo". The
# subject is the headline BEFORE the component list (":"/"includes"/"with") and
# BEFORE the price. Derivation is deterministic + TRUNCATION-ONLY, so the result is
# ALWAYS a substring of the offer's OWN value (NEVER invented). Mirrors the headline
# split facts.py uses (``:|\binclud(e|es|ing)\b|\bwith\b``) but kept self-contained
# (the resolver imports no facts.py and stays pure).
_OFFER_HEADLINE_SPLIT_RE = re.compile(
    r":|\binclud(?:e|es|ing)\b|\bwith\b", re.IGNORECASE
)
# A price token (currency-prefixed or a trailing dash-led price) inside the headline:
# everything from the first price onward is dropped so "Veg Combo - $12.99" → "Veg
# Combo -" → "Veg Combo".
_OFFER_PRICE_RE = re.compile(r"[$₹€£]\s*\d|(?<![\w.])\d{1,4}\.\d{2}(?![\w.])")


def _offer_subject_name(offer_value: str) -> str:
    """The subject NAME of an offer, derived from its OWN text by TRUNCATION ONLY —
    so the result is ALWAYS a substring of ``offer_value`` (never invented). Cuts at
    the component list (":"/"includes"/"with") and at the price, then strips trailing
    separators/whitespace. Returns "" only for an empty/blank value. Guarded so a
    malformed value never raises."""
    try:
        value = offer_value or ""
        if not value.strip():
            return ""
        headline = _OFFER_HEADLINE_SPLIT_RE.split(value, maxsplit=1)[0]
        price_match = _OFFER_PRICE_RE.search(headline)
        if price_match:
            headline = headline[: price_match.start()]
        name = headline.strip().strip("-–—:").strip()
        return name
    except Exception:  # pragma: no cover - defensive: never raise
        return ""


@dataclass(frozen=True)
class ResolvedCreativeDirection:
    hero_name: str
    supporting_names: list[str]  # excludes hero, de-duped, order-preserving
    hook_text: str
    hook_prominence: str  # "high"|"medium"|"low"
    offer_priority: str  # "high"|"medium"|"low"
    theme_family: str
    mood: str
    # CD v2 Slice B (B0.4): the validated model-authored marketing message rendered
    # above the hero. Scoped-scrubbed (``scrub_campaign_narrative``): evocative-but-
    # grounded marketing language survives; fabrication (ungrounded prices/discounts/
    # claims, time-pressure, awards/rankings) defaults to the campaign_title (or ""
    # when no campaign_title is locked).
    campaign_narrative: str


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


def _has_item_name_fact(locked_facts: Sequence[FlyerLockedFact]) -> bool:
    """True iff ANY ``item:<N>:name`` locked fact exists (item-level flyer)."""
    for fact in locked_facts or ():
        try:
            if _ITEM_NAME_RE.match(getattr(fact, "fact_id", None) or ""):
                return True
        except Exception:  # pragma: no cover - defensive: never raise
            continue
    return False


def _first_offer_value(locked_facts: Sequence[FlyerLockedFact]) -> str:
    """The value of the PRIMARY (lowest-index) ``offer:<N>`` locked fact, or ""
    (FIX D). "Primary" = lowest index, mirroring ``_first_item_name``'s convention."""
    best_index: Optional[int] = None
    best_value = ""
    for fact in locked_facts or ():
        try:
            m = _OFFER_INDEX_RE.match(getattr(fact, "fact_id", None) or "")
            if not m:
                continue
            index = int(m.group(1))
            if best_index is None or index < best_index:
                best_index = index
                best_value = getattr(fact, "value", None) or ""
        except Exception:  # pragma: no cover - defensive: never raise
            continue
    return best_value


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
    # 1. hero_ref → an item:*:name (item-level flyer, unchanged).
    hero = _resolve_item_name(getattr(brief, "hero_ref", None), locked_facts)
    if hero:
        return hero
    # FIX 3 (D residual — Codex MAJOR): an ITEM flyer (any item:*:name fact present)
    # resolves its hero from ITEMS ONLY — a model-emitted offer hero_ref is IGNORED so
    # item-flyer behavior never regresses to the offer subject. The offer-subject hero
    # paths below are reached ONLY for COMBO flyers (NO item:*:name fact).
    if _has_item_name_fact(locked_facts):
        # 2. item-level fallback: the first item name (item-level flyer). The offer
        # hero_ref (if any) was deliberately not honored above.
        return _first_item_name(locked_facts)
    # ── COMBO flyers only (no item:*:name) below ────────────────────────────────
    # 3. FIX D — hero_ref → an offer:* fact: the hero is that offer's SUBJECT NAME,
    # derived from the offer's OWN text (truncation-only ⇒ substring, never invented).
    hero_ref_fid = _ref_fact_id(getattr(brief, "hero_ref", None))
    if hero_ref_fid and _OFFER_INDEX_RE.match(hero_ref_fid):
        offer_value = _locked_value_by_id(locked_facts, hero_ref_fid) or ""
        subject = _offer_subject_name(offer_value)
        if subject:
            return subject
    # 4. FIX D — combo flyer with offers but no resolvable offer hero_ref: fall back
    # to the PRIMARY (lowest-index) offer's subject name.
    subject = _offer_subject_name(_first_offer_value(locked_facts))
    if subject:
        return subject
    # 5. neither items nor offers (e.g. pure identity) → "" (unchanged).
    return ""


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


def _resolve_theme_mood(
    brief: FlyerBrief, locked_facts: Sequence[FlyerLockedFact]
) -> tuple[str, str]:
    """Theme family + mood are VISUAL-TASTE strings — but a model could smuggle a
    fabricated COMMERCIAL value through either (e.g. ``mood="$5 off"``), which the
    strict ``fact_refs`` firewall never scans. So each is scrubbed of any UNGROUNDED
    commercial value (one NOT present in the locked facts) via the SHARED
    ``scrub_ungrounded_commercial_taste`` helper (single source of truth — same scan
    the advisory scene path uses, no parallel commercial regex). A field whose only
    commercial value IS a locked fact value is GROUNDED and kept (so ``"$8.99 hero"``
    is not over-stripped). Guarded so a missing visual_direction or any scanner error
    never raises (it just yields the safe "" default)."""
    try:
        vd = getattr(brief, "visual_direction", None)
        theme = (getattr(vd, "theme_family", None) or "") if vd is not None else ""
        mood = (getattr(vd, "mood", None) or "") if vd is not None else ""
    except Exception:  # pragma: no cover - defensive: never raise
        return "", ""
    # Normalize locked values exactly as the validator's call sites do, so the
    # grounding comparison matches the brief firewall's behavior.
    allowed_values = [
        _norm_ws(getattr(f, "value", "") or "")
        for f in locked_facts or ()
        if (getattr(f, "value", "") or "").strip()
    ]
    return scrub_ungrounded_commercial_taste(theme, mood, allowed_values)


def _resolve_campaign_narrative(
    brief: FlyerBrief, locked_facts: Sequence[FlyerLockedFact]
) -> str:
    """Resolve the model-authored ``campaign_narrative`` through the SCOPED scrub
    (CD v2 Slice B, B0.4). Evocative-but-grounded marketing language survives;
    fabrication defaults to the ``campaign_title`` locked-fact value (or "" when no
    ``campaign_title`` is locked). Grounded against the SAME normalized locked-fact
    values the theme/mood scrub uses (single source of truth). Guarded so a missing
    field or any scanner error never raises (it yields the safe campaign_title / "")."""
    try:
        narrative = getattr(brief, "campaign_narrative", None) or ""
    except Exception:  # pragma: no cover - defensive: never raise
        narrative = ""
    if not isinstance(narrative, str):  # pragma: no cover - schema guarantees str
        narrative = ""
    # Normalize locked values exactly as the theme/mood scrub does, so the
    # narrative's grounding comparison matches the brief firewall's behavior.
    allowed_values = [
        _norm_ws(getattr(f, "value", "") or "")
        for f in locked_facts or ()
        if (getattr(f, "value", "") or "").strip()
    ]
    title = _locked_value_by_id(locked_facts, "campaign_title") or ""
    # FIX C: pass the "schedule" locked fact so a SCHEDULE-GROUNDED temporal reference
    # in the narrative (e.g. "this weekend" when the schedule IS Sat/Sun) is KEPT,
    # while an ungrounded day (or pure time-pressure) still defaults to the title.
    schedule = _locked_value_by_id(locked_facts, "schedule") or ""
    try:
        return scrub_campaign_narrative(
            narrative,
            allowed_values=allowed_values,
            campaign_title=title,
            schedule=schedule,
        )
    except Exception:  # pragma: no cover - defensive: never raise (scrub fail-closes)
        return title


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
    theme_family, mood = _resolve_theme_mood(brief, facts)
    campaign_narrative = _resolve_campaign_narrative(brief, facts)
    return ResolvedCreativeDirection(
        hero_name=hero_name,
        supporting_names=supporting_names,
        hook_text=hook_text,
        hook_prominence=hook_prominence,
        offer_priority=offer_priority,
        theme_family=theme_family,
        mood=mood,
        campaign_narrative=campaign_narrative,
    )
