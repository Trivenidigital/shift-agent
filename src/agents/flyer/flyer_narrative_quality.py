"""Deterministic quality referee for CD v2 campaign narratives.

The Creative Director proposes marketing copy. This module decides whether that
copy is strong enough to render as the dominant message-first headline. Safety
still belongs to the existing narrative firewall; this referee adds marketing
quality filters: no filler captions, no generic title restatement, offer/value
awareness, brevity, variety, and no close parroting of known examples.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from dataclasses import dataclass
from typing import Sequence

from schemas import FlyerLockedFact

try:  # flat on the VPS, package-style in the repo tree
    from flyer_brief_validator import _norm_ws, scrub_campaign_narrative  # type: ignore
except ImportError:  # pragma: no cover - import-path shim
    from agents.flyer.flyer_brief_validator import _norm_ws, scrub_campaign_narrative


MAX_NARRATIVE_CHARS = 68
MAX_NARRATIVE_WORDS = 10
MIN_NARRATIVE_WORDS = 3
MIN_NARRATIVE_SCORE = 3
RECENT_REPEAT_RATIO = 0.78

BANNED_PHRASES = (
    "featuring",
    "available",
    "await",
    "awaits",
    "enjoy",
    "indulge",
    "savor the weekend",
    "join us for",
)

KNOWN_EXAMPLE_NORMALS = frozenset(
    {
        "six favorites one easy price",
        "two combos one easy choice",
        "satisfy your sweet cravings this weekend",
        "a feast for the whole family",
    }
)

GENERIC_TITLE_WORDS = frozenset(
    {
        "special",
        "specials",
        "festival",
        "festive",
        "weekend",
        "combo",
        "combos",
        "dessert",
        "desserts",
        "opening",
        "grand",
        "customer",
        "appreciation",
        "event",
        "diwali",
        "menu",
        "offer",
        "offers",
    }
)

WEAK_TITLE_EXTENSION_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "all",
        "for",
        "from",
        "our",
        "the",
        "to",
        "with",
        "everyone",
        "everybody",
        "customers",
        "customer",
        "table",
        "tables",
        "thanks",
        "thank",
    }
)

BENEFIT_WORDS = frozenset(
    {
        "easy",
        "choice",
        "choose",
        "family",
        "together",
        "share",
        "spread",
        "table",
        "favorites",
        "favorite",
        "feast",
        "cravings",
        "craving",
        "sweet",
        "sweets",
        "dessert",
        "desserts",
        "treat",
        "treats",
        "price",
        "value",
        "plate",
        "plates",
        "saving",
        "room",
        "crowd",
        "everyone",
        "whole",
        "comfort",
        "door",
        "doors",
        "flavor",
        "flavors",
        "celebrate",
        "celebration",
        "welcome",
        "thanks",
        "thank",
    }
)

OFFER_SIGNAL_WORDS = frozenset(
    {
        "price",
        "value",
        "combo",
        "combos",
        "choice",
        "choices",
        "choose",
        "deal",
        "offer",
    }
)

GENERIC_CAPTION_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "in",
        "new",
        "fresh",
        "flavor",
        "flavors",
        "easy",
        "choice",
        "choices",
        "big",
        "family",
        "favorite",
        "favorites",
        "gets",
        "made",
        "simple",
        "warm",
        "table",
        "tables",
        "everyone",
        "everybody",
        "every",
        "good",
        "nice",
        "local",
        "moment",
        "food",
        "here",
        "ahead",
        "times",
        "bright",
    }
)

RELATIVE_SCHEDULE_WORDS = frozenset(
    {
        "afternoon",
        "evening",
        "morning",
        "night",
        "now",
        "soon",
        "today",
        "tomorrow",
        "tonight",
    }
)

GENERIC_CAPTION_PHRASES = frozenset(
    {
        "big value",
        "easy choice",
        "easy value",
        "family favorites",
        "for everyone",
        "fresh flavors",
    }
)

MARKETING_SAFE_WORDS = (
    BENEFIT_WORDS
    | OFFER_SIGNAL_WORDS
    | GENERIC_TITLE_WORDS
    | WEAK_TITLE_EXTENSION_WORDS
    | GENERIC_CAPTION_WORDS
    | RELATIVE_SCHEDULE_WORDS
    | frozenset(
        {
            "at",
            "brighter",
            "clear",
            "covered",
            "every",
            "everything",
            "festive",
            "gather",
            "here",
            "made",
            "one",
            "our",
            "saving",
            "served",
            "sharing",
            "start",
            "starts",
            "this",
            "worth",
            "you",
            "your",
        }
    )
)

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class NarrativeEvaluation:
    text: str
    accepted: bool
    score: int
    reasons: tuple[str, ...] = ()


def _normalize(text: str) -> str:
    text = _norm_ws(text or "").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(_normalize(text)))


def _expanded_tokens(text: str) -> set[str]:
    tokens = _tokens(text)
    expanded = set(tokens)
    for token in tokens:
        if token.endswith("ies") and len(token) > 3:
            expanded.add(token[:-3] + "y")
        elif token.endswith(("ches", "shes", "xes", "zes", "oes")) and len(token) > 4:
            expanded.add(token[:-2])
        elif token.endswith("s") and len(token) > 3 and not token.endswith(("is", "ss", "us")):
            expanded.add(token[:-1])
    return expanded


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def _locked_value(locked_facts: Sequence[FlyerLockedFact], fact_id: str) -> str:
    for fact in locked_facts or ():
        if getattr(fact, "fact_id", "") == fact_id:
            value = getattr(fact, "value", "") or ""
            if value.strip():
                return value.strip()
    return ""


def _has_offer(locked_facts: Sequence[FlyerLockedFact]) -> bool:
    for fact in locked_facts or ():
        fid = (getattr(fact, "fact_id", "") or "").strip().lower()
        value = (getattr(fact, "value", "") or "").strip()
        if not value:
            continue
        if fid in {"pricing_structure", "offer_price", "offer", "deal", "discount", "promotion"}:
            return True
        if fid.startswith("offer:"):
            return True
    return False


def _allowed_values(locked_facts: Sequence[FlyerLockedFact]) -> list[str]:
    return [
        _norm_ws(getattr(f, "value", "") or "")
        for f in locked_facts or ()
        if (getattr(f, "value", "") or "").strip()
    ]


def _grounded_claim_tokens(locked_facts: Sequence[FlyerLockedFact]) -> set[str]:
    out: set[str] = set()
    for fact in locked_facts or ():
        fid = (getattr(fact, "fact_id", "") or "").lower()
        if fid in {"business_name", "contact_phone", "location", "schedule"}:
            continue
        if fid.endswith(":price") or fid in {"offer_price", "pricing_structure"}:
            continue
        out |= _expanded_tokens(getattr(fact, "value", "") or "")
    return {t for t in out if len(t) > 2}


def _has_grounded_offer_value_text(
    text: str,
    locked_facts: Sequence[FlyerLockedFact],
) -> bool:
    norm = _norm_ws(text or "")
    if not norm:
        return False
    for fact in locked_facts or ():
        fid = (getattr(fact, "fact_id", "") or "").strip().lower()
        if fid not in {"pricing_structure", "offer_price", "offer", "deal", "discount", "promotion"} and not fid.startswith("offer:"):
            continue
        value = _norm_ws(getattr(fact, "value", "") or "")
        if value and value in norm:
            return True
    return False


def _content_tokens(locked_facts: Sequence[FlyerLockedFact]) -> set[str]:
    out: set[str] = set()
    for fact in locked_facts or ():
        fid = (getattr(fact, "fact_id", "") or "").lower()
        if fid in {"business_name", "contact_phone", "location", "schedule", "campaign_title"}:
            continue
        if fid.endswith(":price") or fid in {"offer_price", "pricing_structure"}:
            continue
        out |= _tokens(getattr(fact, "value", "") or "")
    return {t for t in out if len(t) > 2}


def _nonprice_offer_value_tokens(locked_facts: Sequence[FlyerLockedFact]) -> set[str]:
    out: set[str] = set()
    for fact in locked_facts or ():
        fid = (getattr(fact, "fact_id", "") or "").strip().lower()
        if fid in {"offer", "deal", "discount", "promotion"} or fid.startswith("offer:"):
            out |= _tokens(getattr(fact, "value", "") or "")
    return {t for t in out if len(t) > 2}


def _banned_hit(text: str) -> str:
    norm = _normalize(text)
    for phrase in BANNED_PHRASES:
        if phrase in norm:
            return phrase
    return ""


def _is_known_example_copy(text: str) -> bool:
    norm = _normalize(text)
    if norm in KNOWN_EXAMPLE_NORMALS:
        return True
    for example in KNOWN_EXAMPLE_NORMALS:
        if SequenceMatcher(None, norm, example).ratio() >= 0.82:
            return True
        example_words = example.split()
        for start in range(0, max(0, len(example_words) - 2)):
            signature = " ".join(example_words[start : start + 3])
            if signature in norm:
                return True
    return False


def _is_title_restatement(text: str, campaign_title: str) -> bool:
    norm = _normalize(text)
    title_norm = _normalize(campaign_title)
    if not norm or not title_norm:
        return False
    if norm == title_norm:
        return True
    cand = _tokens(norm)
    title = _tokens(title_norm)
    extra = cand - title - GENERIC_TITLE_WORDS
    if not title:
        return False
    if title <= cand and (not extra or extra <= WEAK_TITLE_EXTENSION_WORDS):
        return True
    overlap = title & cand
    return (
        len(overlap) >= 2
        and len(overlap) / max(1, len(title)) >= 0.66
        and (not extra or extra <= WEAK_TITLE_EXTENSION_WORDS)
    )


def _is_offer_aware(text: str, locked_facts: Sequence[FlyerLockedFact]) -> bool:
    words = _tokens(text)
    if _has_grounded_offer_value_text(text, locked_facts):
        return True
    if words & {"price", "value", "deal", "offer"}:
        return True
    if words & {"combo", "combos", "choice", "choices", "choose"}:
        return True
    if words & _nonprice_offer_value_tokens(locked_facts):
        return True
    return False


def _uses_offer_language(text: str) -> bool:
    return bool(_tokens(text) & OFFER_SIGNAL_WORDS)


def _unsupported_product_words(
    text: str,
    locked_facts: Sequence[FlyerLockedFact],
) -> set[str]:
    unsupported = set()
    grounded = _grounded_claim_tokens(locked_facts)
    for token in _expanded_tokens(text):
        if token.isdigit() or len(token) <= 2:
            continue
        if token in MARKETING_SAFE_WORDS or token in grounded:
            continue
        unsupported.add(token)
    return unsupported


def _unsupported_relative_schedule_words(text: str, schedule: str) -> set[str]:
    words = _expanded_tokens(text) & RELATIVE_SCHEDULE_WORDS
    if not words:
        return set()
    grounded = _expanded_tokens(schedule or "")
    return words - grounded


def _is_generic_caption(text: str) -> bool:
    norm = _normalize(text)
    if any(phrase in norm for phrase in GENERIC_CAPTION_PHRASES):
        return True
    words = _tokens(text)
    return bool(words) and words <= GENERIC_CAPTION_WORDS


def _is_recent_repeat(text: str, recent_narratives: Sequence[str]) -> bool:
    norm = _normalize(text)
    if not norm:
        return False
    for recent in recent_narratives or ():
        recent_norm = _normalize(recent if isinstance(recent, str) else "")
        if not recent_norm:
            continue
        if norm == recent_norm:
            return True
        if SequenceMatcher(None, norm, recent_norm).ratio() >= RECENT_REPEAT_RATIO:
            return True
    return False


def _has_quality_signal(text: str, locked_facts: Sequence[FlyerLockedFact]) -> bool:
    if _is_generic_caption(text):
        return False
    if _has_grounded_offer_value_text(text, locked_facts):
        return True
    words = _tokens(text)
    return bool(words & BENEFIT_WORDS or words & OFFER_SIGNAL_WORDS)


def _score_candidate(
    text: str,
    *,
    locked_facts: Sequence[FlyerLockedFact],
    recent_narratives: Sequence[str],
) -> int:
    words = _tokens(text)
    score = 0
    if words & BENEFIT_WORDS:
        score += 4
    if words & OFFER_SIGNAL_WORDS:
        score += 3
    if _has_grounded_offer_value_text(text, locked_facts):
        score += 3
    if words & _content_tokens(locked_facts):
        score += 2
    wc = _word_count(text)
    if 3 <= wc <= 7:
        score += 2
    elif wc <= MAX_NARRATIVE_WORDS:
        score += 1
    if "," in text or "." in text:
        score += 1
    if _normalize(text) in {_normalize(v) for v in recent_narratives or ()}:
        score -= 6
    elif _is_recent_repeat(text, recent_narratives):
        score -= 6
    if _normalize(text) in KNOWN_EXAMPLE_NORMALS:
        score -= 4
    return score


def evaluate_narrative_candidate(
    candidate: str,
    *,
    locked_facts: Sequence[FlyerLockedFact],
    campaign_title: str,
    schedule: str = "",
    recent_narratives: Sequence[str] = (),
) -> NarrativeEvaluation:
    text = candidate.strip() if isinstance(candidate, str) else ""
    reasons: list[str] = []
    if not text:
        reasons.append("empty")
    if len(text) > MAX_NARRATIVE_CHARS or _word_count(text) > MAX_NARRATIVE_WORDS:
        reasons.append("too_long")
    if _banned_hit(text):
        reasons.append("banned_phrase")

    safe = scrub_campaign_narrative(
        text,
        allowed_values=_allowed_values(locked_facts),
        campaign_title=campaign_title,
        schedule=schedule,
    )
    if safe != text:
        reasons.append("unsafe_claim")

    if _is_title_restatement(text, campaign_title):
        reasons.append("title_restatement")
    if not _has_offer(locked_facts) and _uses_offer_language(text):
        reasons.append("unsupported_offer_language")
    if _unsupported_product_words(text, locked_facts):
        reasons.append("unsupported_product_language")
    if _unsupported_relative_schedule_words(text, schedule):
        reasons.append("unsupported_schedule_language")
    if _has_offer(locked_facts) and not _is_offer_aware(text, locked_facts):
        reasons.append("offer_ignored")
    if _is_generic_caption(text):
        reasons.append("generic_caption")

    norm = _normalize(text)
    if _is_known_example_copy(text):
        reasons.append("known_example")
    if _is_recent_repeat(text, recent_narratives):
        reasons.append("recent_repeat")

    score = _score_candidate(
        text,
        locked_facts=locked_facts,
        recent_narratives=recent_narratives,
    )
    if not _has_quality_signal(text, locked_facts):
        reasons.append("no_quality_signal")
    if _word_count(text) < MIN_NARRATIVE_WORDS or score < MIN_NARRATIVE_SCORE:
        reasons.append("too_thin")

    accepted = not reasons
    return NarrativeEvaluation(
        text=text,
        accepted=accepted,
        score=score,
        reasons=tuple(reasons),
    )


def select_campaign_narrative(
    candidates: Sequence[str],
    *,
    locked_facts: Sequence[FlyerLockedFact],
    campaign_title: str,
    schedule: str = "",
    recent_narratives: Sequence[str] = (),
) -> str:
    """Return the best accepted candidate, or the safe deterministic fallback.

    The fallback is the locked campaign title. If even that is absent, return "".
    This function never invents a new customer-facing phrase.
    """
    seen: set[str] = set()
    evaluations: list[NarrativeEvaluation] = []
    for raw in candidates or ():
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        norm = _normalize(text)
        if not text or norm in seen:
            continue
        seen.add(norm)
        evaluations.append(
            evaluate_narrative_candidate(
                text,
                locked_facts=locked_facts,
                campaign_title=campaign_title,
                schedule=schedule,
                recent_narratives=recent_narratives,
            )
        )

    accepted = [ev for ev in evaluations if ev.accepted]
    if not accepted:
        return campaign_title or _locked_value(locked_facts, "campaign_title")

    accepted.sort(key=lambda ev: ev.score, reverse=True)

    # Known-example or recent phrases are acceptable only when no clean
    # alternative exists. This preserves safety while avoiding parroting/reuse.
    clean = [
        ev
        for ev in accepted
        if _normalize(ev.text) not in KNOWN_EXAMPLE_NORMALS
        and _normalize(ev.text) not in {_normalize(v) for v in recent_narratives or ()}
    ]
    if clean:
        clean.sort(key=lambda ev: ev.score, reverse=True)
        return clean[0].text
    return accepted[0].text
