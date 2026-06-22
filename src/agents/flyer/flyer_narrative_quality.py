"""Deterministic quality referee for CD v2 campaign narratives.

The Creative Director proposes the narrative. The existing narrative scrubber
owns safety and fact-grounding; this module adds a small quality pass before the
message-first overlay receives the text. It is pure Python: no I/O, no network,
no clock, and every public function fail-closes to a safe result.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Sequence

from schemas import FlyerLockedFact

try:  # flat on the VPS, package-style in the repo tree
    from flyer_brief_validator import scrub_campaign_narrative  # type: ignore
except ImportError:  # pragma: no cover - import-path shim
    from agents.flyer.flyer_brief_validator import scrub_campaign_narrative


CATEGORY_KEYS = (
    "safe",
    "no_banned_crutches",
    "not_title_restatement",
    "grounded_specificity",
    "offer_context_fit",
    "benefit_or_emotion",
    "not_recent_or_known_repeat",
)

MAX_NARRATIVE_CHARS = 72
MAX_NARRATIVE_WORDS = 10
MIN_NARRATIVE_WORDS = 3
MIN_ACCEPT_SCORE = 6

_WORD_RE = re.compile(r"[a-z0-9]+")
_CURRENCY_OR_PRICE_RE = re.compile(r"[$]?\s*\d+(?:\.\d{1,2})?")
_PRICE_CLAIM_RE = re.compile(r"[$]\s*\d+(?:\.\d{1,2})?")

_BANNED_CRUTCHES = (
    "featuring",
    "famous",
    "delights",
    "await",
    "awaits",
    "enjoy our",
    "indulge in",
    "join us for",
    "limited time",
    "today only",
)

_KNOWN_EXAMPLES = (
    "six favorites one easy price",
    "two combos one easy choice",
    "a table full of favorites one easy price",
    "a feast for the whole family",
)

_TITLE_ONLY_WORDS = {
    "a",
    "an",
    "and",
    "appreciation",
    "celebration",
    "customer",
    "customers",
    "day",
    "dessert",
    "desserts",
    "event",
    "festival",
    "festive",
    "for",
    "grand",
    "menu",
    "opening",
    "our",
    "special",
    "specials",
    "thanks",
    "thank",
    "the",
    "to",
    "weekend",
    "with",
}

_GENERIC_CAPTION_WORDS = {
    "a",
    "ahead",
    "big",
    "bright",
    "choice",
    "easy",
    "every",
    "everybody",
    "everyone",
    "family",
    "favorite",
    "favorites",
    "flavor",
    "flavors",
    "for",
    "fresh",
    "gets",
    "good",
    "here",
    "in",
    "local",
    "made",
    "moment",
    "new",
    "nice",
    "now",
    "simple",
    "table",
    "tables",
    "times",
    "warm",
}

_BENEFIT_WORDS = {
    "bucket",
    "choice",
    "choices",
    "comfort",
    "cravings",
    "dessert",
    "desserts",
    "easy",
    "family",
    "favorites",
    "feast",
    "flavor",
    "flavors",
    "loyal",
    "new",
    "price",
    "room",
    "saving",
    "share",
    "sharing",
    "simple",
    "small",
    "sweets",
    "table",
    "thank",
    "thanks",
    "together",
    "treat",
    "treats",
    "walk",
    "welcome",
}

_OFFER_FACT_IDS = (
    "pricing_structure",
    "offer",
    "offer_price",
)

_OFFER_WORDS = {
    "choice",
    "choices",
    "choose",
    "clear",
    "combo",
    "combos",
    "deal",
    "deals",
    "offer",
    "offers",
    "price",
    "value",
}

_PRODUCT_WORDS = {
    "biryani",
    "brunch",
    "buffet",
    "burger",
    "burgers",
    "cake",
    "cakes",
    "chaat",
    "chicken",
    "coffee",
    "curry",
    "curries",
    "dessert",
    "desserts",
    "dosa",
    "gulab",
    "idli",
    "jamun",
    "kebab",
    "kebabs",
    "masala",
    "noodle",
    "noodles",
    "paneer",
    "pasta",
    "pizza",
    "rasmalai",
    "samosa",
    "samosas",
    "snack",
    "snacks",
    "soup",
    "sweets",
    "taco",
    "tacos",
    "thali",
    "vada",
    "wrap",
    "wraps",
}

_RELATIVE_SCHEDULE_WORDS = {
    "today",
    "tonight",
    "tomorrow",
    "soon",
    "evening",
}

_WEEKEND_DAY_WORDS = {"saturday", "sunday"}

_SOUTH_INDIAN_ITEM_WORDS = {"dosa", "idli", "sambar", "uttapam", "vada"}

_NUMBER_WORD_VALUES = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
}

_ONE_PRICE_RE = re.compile(r"\bone\s+(?:(?:clear|easy|simple)\s+)?price\b")

_COPY_STYLE_WORDS = (
    _TITLE_ONLY_WORDS
    | _GENERIC_CAPTION_WORDS
    | _BENEFIT_WORDS
    | _OFFER_WORDS
    | {
        "bring",
        "at",
        "everything",
        "of",
        "one",
        "savor",
        "something",
        "this",
        "two",
        "whole",
        "worth",
        "you",
    }
)


@dataclass(frozen=True)
class NarrativeQualityResult:
    candidate: str
    accepted: bool
    score: int
    category_results: dict[str, bool]
    reasons: list[str]


def _clean(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _normal(text: object) -> str:
    return " ".join(_tokens(text))


def _tokens(text: object) -> list[str]:
    return _WORD_RE.findall(_clean(text).lower().replace("-", " "))


def _token_set(text: object) -> set[str]:
    return set(_tokens(text))


def _numeric_claims(text: object) -> set[str]:
    claims = {
        m.group(0).replace("$", "").replace(" ", "").lower()
        for m in _CURRENCY_OR_PRICE_RE.finditer(_clean(text))
    }
    claims.update(
        _NUMBER_WORD_VALUES[token]
        for token in _tokens(text)
        if token in _NUMBER_WORD_VALUES
    )
    return claims


def _price_claims(text: object) -> set[str]:
    return {
        m.group(0).replace("$", "").replace(" ", "").lower()
        for m in _PRICE_CLAIM_RE.finditer(_clean(text))
    }


def _locked_values(locked_facts: Sequence[FlyerLockedFact]) -> list[str]:
    values: list[str] = []
    for fact in locked_facts or ():
        try:
            value = _clean(getattr(fact, "value", ""))
        except Exception:  # pragma: no cover - defensive
            value = ""
        if value:
            values.append(value)
    return values


def _has_offer_fact(locked_facts: Sequence[FlyerLockedFact]) -> bool:
    for fact in locked_facts or ():
        try:
            fact_id = str(getattr(fact, "fact_id", "") or "")
            value = _clean(getattr(fact, "value", ""))
        except Exception:  # pragma: no cover - defensive
            continue
        if not value:
            continue
        if fact_id.startswith("offer:") or fact_id in _OFFER_FACT_IDS:
            return True
    return False


def _title_restatement(candidate_tokens: set[str], title_tokens: set[str]) -> bool:
    if not candidate_tokens or not title_tokens:
        return False
    content = candidate_tokens - _TITLE_ONLY_WORDS
    if content and content.issubset(title_tokens):
        return True
    title_overlap = candidate_tokens & title_tokens
    if len(content) <= 1 and len(title_overlap) >= 2:
        return True
    if len(title_overlap) >= 2:
        remainder = candidate_tokens - title_tokens
        if remainder and remainder.issubset(_COPY_STYLE_WORDS):
            return True
    return False


def _grounding_profile(
    candidate_tokens: set[str],
    locked_facts: Sequence[FlyerLockedFact],
    campaign_title: str,
    candidate_text: str = "",
) -> tuple[bool, set[str]]:
    if not candidate_tokens:
        return False, set()
    title_tokens = _token_set(campaign_title)
    grounded_tokens: set[str] = set()
    derived_grounded_tokens: set[str] = set()
    non_title_locked_tokens: set[str] = set()
    locked_values = _locked_values(locked_facts)
    for value in [campaign_title, *locked_values]:
        value_tokens = _token_set(value)
        grounded_tokens.update(value_tokens)
        if value_tokens & _WEEKEND_DAY_WORDS:
            derived_grounded_tokens.add("weekend")
        if value_tokens & _SOUTH_INDIAN_ITEM_WORDS:
            derived_grounded_tokens.update({"india", "indian", "south"})
    for value in locked_values:
        if _clean(value) != _clean(campaign_title):
            non_title_locked_tokens.update(_token_set(value))
    grounded_tokens -= _TITLE_ONLY_WORDS
    grounded_tokens -= {"item", "items", "any", "all"}
    grounded_tokens.update(derived_grounded_tokens)
    ungrounded_specific_tokens = {
        token
        for token in candidate_tokens - grounded_tokens - _COPY_STYLE_WORDS
    }
    if ungrounded_specific_tokens:
        return False, ungrounded_specific_tokens
    grounded_overlap = candidate_tokens & grounded_tokens
    title_grounded_overlap = grounded_overlap & title_tokens
    fact_grounded_overlap = grounded_overlap & (
        non_title_locked_tokens | derived_grounded_tokens
    )
    style_remainder = candidate_tokens - grounded_overlap
    if fact_grounded_overlap:
        return True, set()
    if (
        len(title_grounded_overlap) >= 2
        and style_remainder
        and not style_remainder.issubset(_COPY_STYLE_WORDS)
    ):
        return True, set()
    if _CURRENCY_OR_PRICE_RE.search(candidate_text):
        candidate_prices = {
            m.group(0).replace(" ", "").lower()
            for m in _CURRENCY_OR_PRICE_RE.finditer(candidate_text)
        }
        for value in _locked_values(locked_facts):
            value_prices = {
                m.group(0).replace(" ", "").lower()
                for m in _CURRENCY_OR_PRICE_RE.finditer(value)
            }
            if candidate_prices & value_prices:
                return True, set()
    return False, set()


def _grounded_specificity(
    candidate_tokens: set[str],
    locked_facts: Sequence[FlyerLockedFact],
    campaign_title: str,
    candidate_text: str = "",
) -> bool:
    grounded, _ungrounded_specific_tokens = _grounding_profile(
        candidate_tokens,
        locked_facts,
        campaign_title,
        candidate_text,
    )
    return grounded


def _is_generic_caption(tokens: set[str]) -> bool:
    return bool(tokens) and tokens.issubset(_GENERIC_CAPTION_WORDS)


def _near_repeat(candidate: str, previous: str) -> bool:
    cand = _normal(candidate)
    prev = _normal(previous)
    if not cand or not prev:
        return False
    if cand == prev:
        return True
    ratio = SequenceMatcher(None, cand, prev).ratio()
    if ratio >= 0.78:
        return True
    c_tokens = set(cand.split())
    p_tokens = set(prev.split())
    if not c_tokens or not p_tokens:
        return False
    overlap = len(c_tokens & p_tokens) / max(1, min(len(c_tokens), len(p_tokens)))
    return overlap >= 0.82


def evaluate_narrative_candidate(
    candidate: object,
    *,
    locked_facts: Sequence[FlyerLockedFact],
    campaign_title: str = "",
    schedule: str = "",
    recent_narratives: Sequence[str] = (),
) -> NarrativeQualityResult:
    """Return the seven-category quality result for one candidate narrative."""
    try:
        text = _clean(candidate)
        title = _clean(campaign_title)
        allowed_values = _locked_values(locked_facts)
        safe_text = scrub_campaign_narrative(
            text,
            allowed_values=allowed_values,
            campaign_title=title,
            schedule=_clean(schedule),
        )
        tokens = _token_set(text)
        title_tokens = _token_set(title)
        word_count = len(_tokens(text))
        has_offer = _has_offer_fact(locked_facts)
        grounded_numeric_claims: set[str] = set()
        for value in [title, *allowed_values, _clean(schedule)]:
            grounded_numeric_claims.update(_numeric_claims(value))
        numeric_claims = _numeric_claims(text)
        price_claims = _price_claims(text)
        unsupported_numeric_claims = numeric_claims - grounded_numeric_claims
        if has_offer and "1" in unsupported_numeric_claims and _ONE_PRICE_RE.search(
            text.lower()
        ):
            unsupported_numeric_claims.remove("1")
        grounded_price_claim = bool(price_claims and not unsupported_numeric_claims)
        has_offer_words = bool(tokens & _OFFER_WORDS) or grounded_price_claim
        reasons: list[str] = []

        category_results = {key: True for key in CATEGORY_KEYS}

        if not text or safe_text != text:
            category_results["safe"] = False
            reasons.append("safety_scrubbed")
        if len(text) > MAX_NARRATIVE_CHARS or word_count > MAX_NARRATIVE_WORDS:
            category_results["safe"] = False
            reasons.append("too_long")
        if word_count < MIN_NARRATIVE_WORDS:
            category_results["benefit_or_emotion"] = False
            reasons.append("too_thin")

        lowered = text.lower().replace("-", " ")
        if any(phrase in lowered for phrase in _BANNED_CRUTCHES):
            category_results["no_banned_crutches"] = False
            reasons.append("banned_crutch")

        if _title_restatement(tokens, title_tokens):
            category_results["not_title_restatement"] = False
            reasons.append("title_restatement")

        grounded_specificity, ungrounded_specific_tokens = _grounding_profile(
            tokens,
            locked_facts,
            title,
            text,
        )
        price_grounded = grounded_price_claim and grounded_specificity

        if not grounded_specificity:
            category_results["grounded_specificity"] = False
            if _is_generic_caption(tokens):
                reasons.append("generic_caption")
            elif ungrounded_specific_tokens:
                reasons.append("unsupported_specific_language")
            else:
                reasons.append("ungrounded_specificity")

        if unsupported_numeric_claims:
            category_results["safe"] = False
            category_results["grounded_specificity"] = False
            category_results["offer_context_fit"] = False
            reasons.append("unsupported_numeric_language")

        if has_offer and not has_offer_words:
            category_results["offer_context_fit"] = False
            reasons.append("offer_ignored")
        if not has_offer and has_offer_words:
            category_results["safe"] = False
            category_results["offer_context_fit"] = False
            reasons.append("unsupported_offer_language")

        grounded_product_words = set()
        for value in [title, *allowed_values]:
            grounded_product_words.update(_token_set(value) & _PRODUCT_WORDS)
        unsupported_products = (tokens & _PRODUCT_WORDS) - grounded_product_words
        if unsupported_products:
            category_results["safe"] = False
            category_results["grounded_specificity"] = False
            reasons.append("unsupported_product_language")

        schedule_words = tokens & _RELATIVE_SCHEDULE_WORDS
        if schedule_words and not _clean(schedule):
            category_results["safe"] = False
            reasons.append("unsupported_schedule_language")

        has_benefit = bool(tokens & _BENEFIT_WORDS) or price_grounded
        if _is_generic_caption(tokens):
            has_benefit = False
        if not has_benefit:
            category_results["benefit_or_emotion"] = False
            reasons.append("no_benefit_or_emotion")

        for known in _KNOWN_EXAMPLES:
            if _near_repeat(text, known):
                category_results["not_recent_or_known_repeat"] = False
                reasons.append("known_example")
                break
        if category_results["not_recent_or_known_repeat"]:
            for recent in recent_narratives or ():
                if _near_repeat(text, _clean(recent)):
                    category_results["not_recent_or_known_repeat"] = False
                    reasons.append("recent_repeat")
                    break

        score = sum(1 for passed in category_results.values() if passed)
        mandatory = (
            category_results["safe"]
            and category_results["no_banned_crutches"]
            and category_results["not_title_restatement"]
            and category_results["grounded_specificity"]
            and category_results["benefit_or_emotion"]
            and category_results["not_recent_or_known_repeat"]
        )
        accepted = mandatory and score >= MIN_ACCEPT_SCORE
        return NarrativeQualityResult(
            candidate=text,
            accepted=accepted,
            score=score,
            category_results={key: category_results[key] for key in CATEGORY_KEYS},
            reasons=list(dict.fromkeys(reasons)),
        )
    except Exception:  # pragma: no cover - public API must never raise
        return NarrativeQualityResult(
            candidate="",
            accepted=False,
            score=0,
            category_results={key: False for key in CATEGORY_KEYS},
            reasons=["referee_error"],
        )


def select_campaign_narrative(
    candidates: Sequence[object],
    *,
    locked_facts: Sequence[FlyerLockedFact],
    campaign_title: str = "",
    schedule: str = "",
    recent_narratives: Sequence[str] = (),
) -> str:
    """Select the highest-quality accepted candidate, or the safe campaign title."""
    try:
        best_text = ""
        best_score = -1
        for candidate in candidates or ():
            result = evaluate_narrative_candidate(
                candidate,
                locked_facts=locked_facts,
                campaign_title=campaign_title,
                schedule=schedule,
                recent_narratives=recent_narratives,
            )
            if not result.accepted:
                continue
            if result.score > best_score:
                best_score = result.score
                best_text = result.candidate
        if best_text:
            return best_text
        return _clean(campaign_title)
    except Exception:  # pragma: no cover - public API must never raise
        return _clean(campaign_title)
