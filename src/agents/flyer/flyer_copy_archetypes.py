"""Controlled Copy Archetypes (CCA) — deterministic campaign-narrative headlines.

The marketing HEADLINE is no longer free-written by an LLM. Instead:
  1. ``classify_archetype`` deterministically maps the campaign to ONE copy archetype
     from a fixed enum, using ONLY the locked-fact structure (title, offers, prices,
     products, schedule) + request_intent. No LLM, no network.
  2. ``compose_archetype_headlines`` fills APPROVED templates for that archetype using
     ONLY grounded locked-fact values (price / product / free-item / combo / event /
     date). A template whose precondition is unmet or whose required slot is ungrounded
     is INELIGIBLE (fail-closed) — it can never fabricate a price/product/offer.

The resolver runs each composed candidate through the existing deterministic safety
firewall (``scrub_campaign_narrative``) and uses the first that passes, else the safe
``campaign_title``. This module is PURE: no I/O, no network, no clock; it duck-types the
locked facts via ``getattr`` so it has no schema import.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

# Archetypes in CLASSIFIER PRECEDENCE order (``classify_archetype`` checks these
# top-to-bottom; first match wins). Keep this tuple in sync with that function's order.
ARCHETYPES = (
    "grand_opening",
    "customer_appreciation",
    "bucket_meal",
    "combo",
    "event",
    "festival_dessert",
    "weekend_one_price",
)

# Occasion names that mark an EVENT archetype (specific festivals/occasions — NOT the
# generic words "festival"/"celebration", so "Festival Dessert Specials" is NOT an event).
_OCCASIONS = (
    "diwali", "holi", "eid", "navratri", "dussehra", "dasara", "pongal", "onam",
    "sankranti", "makar sankranti", "ugadi", "lohri", "baisakhi", "raksha bandhan",
    "rakhi", "ganesh", "janmashtami", "christmas", "new year", "anniversary",
    "thanksgiving", "valentine", "ramadan", "ramzan", "navaratri",
)
# Food nouns used to validate a grounded product slot (bucket archetype).
_FOOD_NOUNS = (
    "biryani", "dosa", "idli", "vada", "uttapam", "pongal", "sambar", "thali",
    "curry", "curries", "kebab", "kebabs", "paneer", "tikka", "samosa", "samosas",
    "pizza", "burger", "burgers", "pasta", "noodles", "naan", "roti", "chaat",
    "gulab", "jamun", "rasmalai", "jalebi", "ladoo", "laddu", "barfi", "halwa",
    "kheer", "lassi", "dessert", "desserts", "sweets", "cake", "cakes",
)
# Dessert signal for the festival_dessert archetype.
_DESSERT_NOUNS = (
    "dessert", "desserts", "sweet", "sweets", "gulab", "jamun", "rasmalai", "jalebi",
    "ladoo", "laddu", "barfi", "halwa", "kheer", "mithai", "cake", "cakes", "pastry",
)
# Structural words stripped when deriving a bucket product from the title.
_STRUCTURAL = {"family", "bucket", "pack", "meal", "combo", "the", "our", "a", "an",
               "special", "specials", "feast", "value", "size", "box"}
# Stop words that end a free-item object phrase.
_FREE_STOP = {"for", "with", "on", "to", "and", "the", "a", "an", "every", "this", "all",
              "first", "when", "per", "of", "your", "our", "you", "each", "any"}

_PRICE_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")
_FREE_RE = re.compile(r"\b(?:free|complimentary)\b", re.IGNORECASE)
_OPENING_RE = re.compile(r"\bopening\b", re.IGNORECASE)


def _val(fact) -> str:
    try:
        v = getattr(fact, "value", "")
    except Exception:  # pragma: no cover - defensive
        v = ""
    return v if isinstance(v, str) else ""


def _fid(fact) -> str:
    try:
        f = getattr(fact, "fact_id", "")
    except Exception:  # pragma: no cover - defensive
        f = ""
    return f if isinstance(f, str) else ""


def _norm(text: str) -> str:
    """Trim + collapse whitespace; PRESERVE case/currency/wording (operator rule 3)."""
    return " ".join((text or "").split())


def _values_with(prefix: str, facts: Sequence[object]) -> list[str]:
    return [_val(f) for f in (facts or ()) if _fid(f).startswith(prefix) and _val(f).strip()]


def _value_of(fact_id: str, facts: Sequence[object]) -> str:
    for f in facts or ():
        if _fid(f) == fact_id:
            return _norm(_val(f))
    return ""


def _offers(facts) -> list[str]:
    # Match the ``offer:`` namespace (or a bare ``offer`` fact) — NOT any id merely
    # starting with "offer" (so e.g. a future ``offer_disclaimer`` is not ingested).
    return [_val(f) for f in (facts or ())
            if (_fid(f) == "offer" or _fid(f).startswith("offer:")) and _val(f).strip()]


def _is_grounded_slot(value: str, facts) -> bool:
    """Defense-in-depth: True iff ``value`` appears (case-insensitively, whitespace-
    normalized) within some locked-fact value. Slots are already EXTRACTED from facts, so
    this is always true today — but re-verifying before emitting means a future extractor
    change (or a template the firewall does not backstop) can never leak an ungrounded
    slot value into the headline (silent-failure guard, reviewer 2026-06-26)."""
    v = _norm(value).casefold()
    if not v:
        return False
    return any(v in _norm(_val(f)).casefold() for f in (facts or ()))


def _item_names(facts) -> list[str]:
    return [v for f in (facts or ()) if _fid(f).endswith(":name") and _fid(f).startswith("item:")
            for v in [_val(f)] if v.strip()]


def _schedule(facts) -> str:
    return _value_of("schedule", facts)


def _campaign_title(facts) -> str:
    return _value_of("campaign_title", facts)


def _scan_text(facts, campaign_title: str = "") -> str:
    parts = [campaign_title or _campaign_title(facts), *_offers(facts), *_item_names(facts),
             _value_of("pricing_structure", facts)]
    return " ".join(p for p in parts if p).lower()


def _shared_price(facts) -> Optional[str]:
    """A single shared price across the menu: the price in ``pricing_structure``, else
    the common price when every priced item shares it. Returns the EXACT price text."""
    ps = _value_of("pricing_structure", facts)
    m = _PRICE_RE.search(ps)
    if m:
        return _norm(m.group(0))
    prices = [_norm(_val(f)) for f in (facts or ())
              if _fid(f).startswith("item:") and _fid(f).endswith(":price") and _val(f).strip()]
    prices = [p for p in prices if _PRICE_RE.fullmatch(p) or _PRICE_RE.search(p)]
    norm_prices = []
    for p in prices:
        mm = _PRICE_RE.search(p)
        if mm:
            norm_prices.append(mm.group(0))
    if len(norm_prices) >= 2 and len(set(norm_prices)) == 1:
        return norm_prices[0]
    return None


def _covers_weekend(schedule: str) -> bool:
    s = (schedule or "").lower()
    if "weekend" in s:
        return True
    if ("saturday" in s and "sunday" in s) or (re.search(r"\bsat\b", s) and re.search(r"\bsun\b", s)):
        return True
    return False


def _combo_count(facts) -> int:
    n = 0
    for v in _offers(facts) + _item_names(facts):
        if re.search(r"\bcombo\b", v, re.IGNORECASE):
            n += 1
    return n


def _has_complimentary(facts) -> bool:
    return any(re.search(r"\bcomplimentary\b", v, re.IGNORECASE) for v in _offers(facts))


def _has_dessert(facts, campaign_title: str = "") -> bool:
    blob = ((campaign_title or _campaign_title(facts)) + " " + " ".join(_item_names(facts))).lower()
    return any(re.search(r"\b" + re.escape(w) + r"\b", blob) for w in _DESSERT_NOUNS)


def _free_item(facts) -> Optional[str]:
    """The grounded object of a free/complimentary offer (e.g. 'mango lassi', 'dessert'),
    taken VERBATIM from the locked offer value. None if no free/complimentary offer."""
    for v in _offers(facts):
        m = _FREE_RE.search(v)
        if not m:
            continue
        obj: list[str] = []
        for tok in v[m.end():].split():
            w = re.sub(r"[^A-Za-z'\-]", "", tok)
            if not w or w.lower() in _FREE_STOP:
                break
            obj.append(w)
            if len(obj) >= 3:
                break
        if obj:
            return " ".join(obj)
    return None


def _bucket_product(facts, campaign_title: str) -> Optional[str]:
    """A grounded food-noun product for a bucket/family pack, VERBATIM from the title or
    item names. None if no recognized food noun is grounded (→ no-slot template)."""
    candidates = _item_names(facts) + [campaign_title or _campaign_title(facts)]
    for src in candidates:
        for tok in src.split():
            w = re.sub(r"[^A-Za-z'\-]", "", tok)
            if w and w.lower() in _FOOD_NOUNS and w.lower() not in _STRUCTURAL:
                return w
    return None


def _event_name(facts, campaign_title: str) -> Optional[str]:
    title = campaign_title or _campaign_title(facts)
    low = title.lower()
    for occ in _OCCASIONS:
        idx = low.find(occ)
        if idx != -1:
            # Return the occasion VERBATIM from the title (preserve case/wording).
            return _norm(title[idx:idx + len(occ)])
    return None


def classify_archetype(
    facts: Sequence[object],
    *,
    request_intent: str = "",
    campaign_title: str = "",
    raw_request: str = "",
) -> str:
    """Deterministically pick ONE archetype from the fact structure, or 'none'. Pure;
    never raises. Precedence is most-specific-first; ties resolve in listed order."""
    try:
        title = (campaign_title or _campaign_title(facts) or "").lower()
        text = _scan_text(facts, campaign_title) + " " + (raw_request or "").lower()
        if "grand opening" in title or _OPENING_RE.search(title):
            return "grand_opening"
        if "appreciation" in title or "thank you" in title or "thank-you" in title or _has_complimentary(facts):
            return "customer_appreciation"
        if re.search(r"\bbucket\b", text) or "family pack" in text:
            return "bucket_meal"
        # Combo from a grounded combo offer/item OR a "combo" signal in the title/text
        # (mirrors bucket_meal scanning the title, not just offers/items).
        if _combo_count(facts) >= 1 or re.search(r"\bcombo\b", text):
            return "combo"
        if _event_name(facts, campaign_title):
            return "event"
        if _has_dessert(facts, campaign_title):
            return "festival_dessert"
        if _shared_price(facts) and _covers_weekend(_schedule(facts) or _value_of("schedule", facts)):
            return "weekend_one_price"
        return "none"
    except Exception:  # pragma: no cover - public API must never raise
        return "none"


def compose_archetype_headlines(
    facts: Sequence[object],
    *,
    request_intent: str = "",
    campaign_title: str = "",
    schedule: str = "",
) -> list[str]:
    """Approved, grounded headline candidates for the classified archetype, in priority
    order (offer-explicit first, safe no-slot fallback last). Empty list when the
    archetype is 'none'. Slots are filled ONLY from grounded locked facts; a template
    with an ungrounded required slot is omitted (fail-closed). Pure; never raises.

    ``schedule`` and ``request_intent`` are accepted for caller symmetry / forward use;
    v1 classification + grounding read the locked facts directly (the resolver does the
    real schedule grounding via the firewall), so they are currently inert."""
    try:
        title = campaign_title or _campaign_title(facts)
        arch = classify_archetype(facts, request_intent=request_intent, campaign_title=title)
        if arch == "none":
            return []
        out: list[str] = []
        # Slot-filled templates emit ONLY when the slot value is grounded in a locked
        # fact (``_is_grounded_slot``) — defense-in-depth even though slots are extracted
        # from facts, since the firewall does not backstop a bare interpolated noun.
        if arch == "weekend_one_price":
            price = _shared_price(facts)
            if price and _is_grounded_slot(price, facts):
                out.append(f"{price} favorites all weekend.")
            out.append("Weekend favorites, one easy price.")
            out.append("One price. Weekend favorites.")
        elif arch == "combo":
            if _combo_count(facts) >= 2:
                out.append("Two combos. One easy choice.")
            out.append("Dinner combos made easy.")
            out.append("Family combos, ready to serve.")
        elif arch == "bucket_meal":
            product = _bucket_product(facts, title)
            if product and _is_grounded_slot(product, facts):
                out.append(f"{product} feast, served by the bucket.")
            out.append("A feast for the whole table, by the bucket.")
        elif arch == "grand_opening":
            free = _free_item(facts)
            if free and _is_grounded_slot(free, facts):
                out.append(f"New location. Free {free}.")
            out.append("A warm welcome, on us.")
        elif arch == "customer_appreciation":
            free = _free_item(facts)
            if free and _is_grounded_slot(free, facts):
                out.append(f"A thank-you {free} on us.")
            out.append("Our treat for your table.")
        elif arch == "festival_dessert":
            out.append("Sweet trays for every celebration.")
            out.append("Desserts made for sharing.")
        elif arch == "event":
            ev = _event_name(facts, title)
            if ev and _is_grounded_slot(ev, facts):
                out.append(f"{ev} dinner, served festive.")
                out.append(f"Celebrate {ev} around the table.")
        # de-dup, drop empties, preserve order
        seen: set[str] = set()
        result: list[str] = []
        for c in out:
            c = _norm(c)
            if c and c not in seen:
                seen.add(c)
                result.append(c)
        return result
    except Exception:  # pragma: no cover - public API must never raise
        return []


__all__ = ["ARCHETYPES", "classify_archetype", "compose_archetype_headlines"]
