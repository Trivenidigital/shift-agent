"""Locked customer-visible facts for Flyer Studio projects."""
from __future__ import annotations

import re
from typing import Iterable

from schemas import (
    FlyerAsset,
    FlyerConfig,
    FlyerCustomerProfile,
    FlyerLockedFact,
    FlyerProject,
    FlyerRequestFields,
    FlyerSourceContract,
)
try:
    from flyer_semantic_brief import (  # type: ignore
        _offer_is_faithful as _semantic_offer_is_faithful,
        build_hermes_semantic_brief_provider,
        build_semantic_flyer_brief,
    )
except ImportError:
    from agents.flyer.semantic_brief import (
        _offer_is_faithful as _semantic_offer_is_faithful,
        build_hermes_semantic_brief_provider,
        build_semantic_flyer_brief,
    )


ALLOWED_NEW_PROJECT_FACT_SOURCES = {
    "customer_text",
    "customer_confirmed",
    "customer_profile",
    "reference_ocr",
    "reference_vision",
    "uploaded_asset",
    "operator",
    "system",
    "hermes_inferred",
}


def _clean(value: str) -> str:
    return " ".join((value or "").strip(" .,:;").split())


def _normalize_dashes(text: str) -> str:
    """Fold dash variants (en/em/minus/figure/non-breaking-hyphen) to ASCII '-'
    so name↔price patterns (which only accept ASCII '-'/':') match 'Name – $X'.
    Same dash set as visual_qa._normalize_soft_text. Folds ONLY dashes — names,
    prices, and all other content are untouched."""
    for d in "–—−‐‑":
        text = text.replace(d, "-")
    return text


# A complete-dish noun (combo/meal/platter/...) already names a full offering, so a
# category_suffix ("Biryani") must NOT be appended to it — "Veg Combo" must stay
# "Veg Combo", never become "Veg Combo Biryani" (which would fabricate a derived
# item the customer never named).
_COMPLETE_DISH_RE = re.compile(
    r"\b(combo|meal|platter|thali|plate|set|special|offer|deal|bowl|wrap|roll)\b",
    re.IGNORECASE,
)


# BC-1: currency preservation. The extractor recognizes `$`, `₹`, and the Indian
# rupee spellings (Rs / Rs. / rs / rupees) as price markers, plus BARE amounts
# (no symbol). Emitted value keeps the customer's currency: `$` and bare amounts
# render as "$N"; every rupee spelling normalizes to the single "₹N" form
# (downstream QA understands `[$₹]`). A bare (symbol-less) amount is trusted as a
# price ONLY when it carries a `.NN` decimal — a bare INTEGER collides with
# quantities / years / phone digits and is never treated as a price.
def _price_str(cur: str, amount: str) -> str:
    c = (cur or "").strip().lower()
    if c == "₹" or c.startswith("rs") or c.startswith("rupee"):
        return f"₹{amount}"
    return f"${amount}"


# SW-2: category_suffix ("Biryani") is appended ONLY to bare protein/veg
# modifiers that a complete dish name would follow ("Chicken" -> "Chicken
# Biryani"). A complete dish ("Dosa", "Idli") is left untouched — appending the
# suffix there fabricates a dish the customer never named.
_BIRYANI_MODIFIER_RE = re.compile(
    r"^(?:chicken|mutton|goat|lamb|veg|vegetable|veggie|paneer|egg|prawn|prawns|"
    r"shrimp|fish|gosht|keema|kheema|beef|boneless|jumbo|hyderabadi|dum)(?:\s*65)?$",
    re.IGNORECASE,
)


# BC-1: a BARE (symbol-less) price is only trusted on a tight menu line. Reject
# the match when the name carries offer/sentence contamination so combo briefs
# ("veg combo 39.99 includes ...", "with prices 49.99 for ...") never fabricate a
# priced item. Symbol-marked prices ($ / ₹ / Rs) are exempt from this gate.
_BARE_PRICE_NAME_REJECT_RE = re.compile(
    r"\b(?:combo|meal|package|bundle|platter|for|with|includes?|including|price|"
    r"prices|priced|pricing|cost|costs|total|per)\b",
    re.IGNORECASE,
)


def _fact(
    fact_id: str,
    label: str,
    value: str,
    source: str,
    *,
    required: bool = True,
    message_id: str = "",
    confidence: float = 1.0,
) -> FlyerLockedFact | None:
    value = _clean(value)
    if not value:
        return None
    return FlyerLockedFact(
        fact_id=fact_id,
        label=label,
        value=value,
        source=source,
        required=required,
        confidence=confidence,
        source_message_id=message_id,
    )


def _headline(text: str) -> str:
    match = re.search(r"\bheadline\s*:\s*(.+?)(?=\.|\btagline\s*:|$)", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _tagline(text: str) -> str:
    match = re.search(
        r"\btagline\s*:\s*(.+?)(?=\b(?:headline|feature|include|use|location|address|phone|contact)\s*:?\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    value = match.group(1).strip()
    price_start = value.find("$")
    if price_start != -1:
        value = value[:price_start]
        value = re.sub(r"\s+[A-Za-z][A-Za-z0-9 '&/-]{1,60}$", "", value).strip()
    return value


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _combined_extraction_text(raw_request: str, notes: str) -> str:
    raw = raw_request or ""
    extra = notes or ""
    if not extra:
        return raw
    raw_norm = _norm(raw)
    extra_norm = _norm(extra)
    if raw_norm and extra_norm and extra_norm in raw_norm:
        return raw
    return f"{raw}\n{extra}" if raw else extra


def _looks_like_instruction_fragment(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if any(
        marker in lowered
        for marker in (
            "i'd like",
            "i’d like",
            "i?d like",
            "help me with",
            "create flyer",
            "create a flyer",
            "make flyer",
            "flier from",
            "flyer from",
            "include ",
        )
    ):
        return True
    return len(text.split()) > 8 and bool(re.search(r"\b(?:create|make|design|help|include|flyer|flier)\b", lowered))


def _business_title_from_text(value: str) -> str:
    clean = _clean(value)
    clean = re.sub(
        r"\b(?:weekend|weekday|daily|monday|tuesday|wednesday|thursday|friday|saturday|sunday)?\s*"
        r"(?:breakfast|brunch|lunch|dinner|snacks?)\s+(?:special|menu|offer|promo|event)\b.*$",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip(" .")
    return clean


# A campaign_title that is a bare article/determiner or instruction stub is extraction garbage (e.g.
# "A" grabbed from "create A flyer"). Better no title (the renderer falls back) than a garbage "A".
# This is a fact-validation guard (Python owns facts), NOT an occasion/theme keyword list.
_DEGENERATE_TITLES = {
    "a", "an", "the", "this", "that", "these", "those", "my", "our", "your", "its", "it",
    "create", "make", "design", "generate", "new", "flyer", "flier", "poster", "banner", "please",
}


def _normalize_campaign_title(value: str) -> str:
    clean = _clean(value)
    if not clean:
        return ""
    clean = re.sub(r"\s+\b(?:flyer|flier|poster|banner)\b\s*$", "", clean, flags=re.IGNORECASE).strip(" .")
    # Drop a degenerate title (a bare article/determiner, or a 1-char fragment).
    if clean.casefold() in _DEGENERATE_TITLES or len(re.sub(r"[^a-z0-9]", "", clean.casefold())) <= 1:
        return ""
    return clean


def _replaceable_customer_text_campaign_title(value: str) -> bool:
    """Generic semantic-brief titles should not shadow source-flyer titles.

    A customer can explicitly name a campaign, but brief generation also creates
    fallback titles such as "Lakshmi's Kitchen Menu" from the account/business
    name. When a reference flyer supplies a concrete campaign title, that source
    title is more faithful than the generic fallback.
    """
    clean = _normalize_campaign_title(value)
    if not clean:
        return True
    if _looks_like_instruction_fragment(clean):
        return True
    lowered = clean.casefold()
    if re.search(r"\b(?:menu|flyer|flier|poster|banner)\b$", lowered) and len(clean.split()) <= 6:
        return True
    return False


def _explicit_business_override(raw_request: str, profile_business_name: str) -> str:
    text = " ".join((raw_request or "").split())
    patterns = [
        r"\bbusiness\s+name\s+is\s+(.+?)(?=\s+\b(?:and|with)\s+(?:headline|title|tagline|phone|contact|location|address)\b|\s+\bfor\s+(?:this|the)\s+(?:flyer|flier|poster|banner)\b|\.|,|\n|$)",
        r"\bchange\s+business\s+name\s+to\s+(.+?)(?=\s+\b(?:and|with)\s+(?:headline|title|tagline|phone|contact|location|address)\b|\s+\bfor\s+(?:this|the)\s+(?:flyer|flier|poster|banner)\b|\.|,|\n|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _clean(match.group(1))
            if value and not _looks_like_instruction_fragment(value):
                return value
    if profile_business_name:
        replace = re.search(
            r"\breplace\s+(.+?)\s+(?:with|to)\s+(.+?)(?=\s+\b(?:and|with)\s+(?:headline|title|tagline|phone|contact|location|address)\b|\s+\bfor\s+(?:this|the)\s+(?:flyer|flier|poster|banner)\b|\.|,|\n|$)",
            text,
            flags=re.IGNORECASE,
        )
        if replace and _norm(replace.group(1)) == _norm(profile_business_name):
            value = _clean(replace.group(2))
            if value and not _looks_like_instruction_fragment(value):
                return value
    return ""


def profile_locked_facts(
    customer: FlyerCustomerProfile,
    *,
    raw_request: str = "",
    message_id: str = "",
) -> list[FlyerLockedFact]:
    facts: list[FlyerLockedFact] = []
    business_override = _explicit_business_override(raw_request, customer.business_name)
    business_name = business_override or customer.business_name
    business_source = "customer_text" if business_override else "customer_profile"
    for fact in [
        _fact("business_name", "Business", business_name, business_source, message_id=message_id),
        _fact("contact_phone", "Contact", str(customer.public_phone), "customer_profile", message_id=message_id),
        _fact("location", "Location", customer.business_address, "customer_profile", message_id=message_id),
    ]:
        if fact:
            facts.append(fact)
    return facts


def _item_price_facts(
    text: str, *, message_id: str = "", conflicts: list[str] | None = None
) -> list[FlyerLockedFact]:
    text = _normalize_dashes(text or "")
    facts: list[FlyerLockedFact] = []
    category_suffix = ""
    if re.search(r"\bbiryani(?:'?s|s)?\b", text or "", flags=re.IGNORECASE):
        category_suffix = "Biryani"
    # BC-1: `_CUR_PRE` matches a leading currency symbol ($ / ₹ / Rs / Rs. / rs);
    # `_CUR_SUF` matches a trailing one ($ / dollars / ₹ / Rs / rupees).
    price_for_name = re.compile(
        r"(?P<cur>\$|₹|Rs\.?|rs\.?)\s*(?P<price>\d+(?:\.\d{2})?)\s*(?:for|of)\s+"
        r"(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,40}?)"
        r"(?=\s+(?:and|or)\s+(?:\$|₹|Rs\.?|rs\.?)|[.!?,;]|$)",
        flags=re.IGNORECASE,
    )
    # BC-1: a currency prefix ($ / ₹ / Rs) allows an integer OR decimal price; a
    # BARE (symbol-less) price MUST carry a .NN decimal. The `(?!\s*[%\d])`
    # digit/percent rejection guards the BARE alternative ONLY — it refuses a bare
    # decimal that is really the head of a longer number ("15.00%", "5.992") or a
    # quantity, and it must NOT touch the symbol branch: a leading $/₹/Rs already
    # disambiguates a price from a trailing quantity, so "Samosa $5.99 2pc" must
    # keep the full $5.99 rather than backtrack the decimals to satisfy a shared
    # lookahead. A bare integer is a quantity / year / phone digit and is refused
    # by the decimal requirement on the bare branch.
    name_before_price = re.compile(
        r"(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,60}?)\s*(?:-|:)?\s*"
        r"(?:(?P<cur>\$|₹|Rs\.?|rs\.?)\s*(?P<price>\d+(?:\.\d{1,2})?)"
        r"|(?P<bareprice>\d+\.\d{1,2})(?!\s*[%\d]))",
        flags=re.IGNORECASE,
    )
    compact_name_before_price = re.compile(
        r"(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,60}?)\s*[-:]\s*"
        r"(?P<cur>\$|₹|Rs\.?|rs\.?)?\s*(?P<price>\d+(?:\.\d{1,2})?)\s*"
        r"(?:each|plate|per\s+plate)\b",
        flags=re.IGNORECASE,
    )
    price_before_name = re.compile(
        r"(?P<cur>\$|₹|Rs\.?|rs\.?)\s*(?P<price>\d+(?:\.\d{2})?)\s*(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,50})",
        flags=re.IGNORECASE,
    )
    suffix_price = re.compile(
        r"(?P<price>\d+(?:\.\d{1,2})?)\s*(?P<cur>\$|dollars?|₹|Rs\.?|rupees?)\s*$",
        flags=re.IGNORECASE,
    )
    seen: set[str] = set()
    seen_prices: dict[str, str] = {}
    promo_name = re.compile(r"^(?:save|coupon|discount|offer|deal|special|cashback|credit)\b", flags=re.IGNORECASE)
    bad_context = re.compile(r"\b(?:create|make|generate|design|flyer|flier|poster|banner|promoting|promote|promotion)\b", flags=re.IGNORECASE)

    # A name-first match whose NAME is a flat-price subject ("any item", "every
    # item", ...) legitimately CLAIMS the segment's price for that subject — the
    # trailing phrase ("...$5 Free Gift") is then a phantom. Such a rejected match
    # must still suppress the price-before-name fallback. Distinct from a name-first
    # match rejected as prompt-prefix/bad-context garbage, where the price belongs
    # to the trailing REAL item and the fallback must run.
    flat_price_subject = re.compile(
        r"^(?:price\s+)?(?:any|every|each|all)\s+items?$|^(?:everything|anything)$",
        flags=re.IGNORECASE,
    )

    def add_item(name: str, price: str, *, bare: bool = False) -> str:
        """Add a name/price item fact, returning a 3-state status:

        - "accepted"    — the name/price pair was recorded as an item fact.
        - "flat_subject" — the (normalized) name is a flat-price subject ("any
          item", "price all items"); it claims the segment price for the whole
          segment, so no item fact is recorded but a name-first match here must
          still suppress the price-before-name fallback.
        - "rejected"    — the name was empty / a stopword / prompt-prefix or
          bad-context garbage; the fallback should still run to recover a real
          trailing item ("...$20 men haircut").
        """
        name = _clean(name)
        original_name = name
        name = re.sub(
            r"^(?:create|make|generate|design)\s+(?:a\s+)?(?:menu\s+)?(?:flyer|flier|poster|banner)\s+(?:with|for)?\s*",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        name = re.sub(r"^.*\b[a-z][a-z0-9 '&/-]*-\s*\d+\s*(?:pcs?|pieces?)\s+", "", name, flags=re.IGNORECASE).strip()
        name = re.sub(r"^.*\b(?:menu|items?|include|including)\s+", "", name, flags=re.IGNORECASE).strip()
        name = re.sub(r"^(?:each|plate|per\s+plate|pc|piece)\s+", "", name, flags=re.IGNORECASE).strip()
        name = re.sub(r"^(?:and|with|include|includes|feature|features|featuring)\s+", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\b(\d+)\s*(count|counts|cups?|trays?|pcs?|pieces?)\b", r"\1 \2", name, flags=re.IGNORECASE)
        name = _clean(name.strip(" -:"))
        if not name:
            return "rejected"
        if name.lower() in seen:
            # A real name-first pattern matched but repeats an already-captured
            # item. It still CLAIMED the segment's price, so the fallback must not
            # mine trailing words — treat as a claim (deduped, not re-added).
            # SW-4: a repeat with a DIFFERENT price is a silent conflict
            # ("Biryani $10 Biryani $12"); record the name so the caller can route
            # to manual review instead of last/first-wins shipping one price.
            if conflicts is not None:
                prev = seen_prices.get(name.lower(), "")
                if prev and _price_norm(prev) != _price_norm(price) and name not in conflicts:
                    conflicts.append(name)
            return "duplicate"
        # A flat-price subject ("any item", "price all items") claims the price for
        # the whole segment; the trailing phrase is not its own priced item. Detect
        # on the NORMALIZED name so prompt-prefixed inputs ("Create a flyer with any
        # item ...") are recognized too.
        if flat_price_subject.match(name):
            return "flat_subject"
        lowered_original = original_name.lower()
        lowered = name.lower()
        # BC-1: a BARE (symbol-less) price is only trusted on a tight menu line —
        # reject offer/sentence contamination or an over-long name so combo/prose
        # briefs never fabricate a priced item from a stray decimal.
        if bare and (_BARE_PRICE_NAME_REJECT_RE.search(lowered) or len(name.split()) > 4):
            return "rejected"
        if (
            lowered_original.startswith("for ")
            or lowered.endswith(" and")
            or lowered in {"add price as", "price as", "price is", "price for"}
            or re.search(r"\b(?:add|set|use)\s+price\b|\bprice\s+(?:as|is|for)\b", lowered)
            or re.search(r"\bprice\s+(?:any|every|each|all)\s+items?\b", lowered)
        ):
            return "rejected"
        if lowered in {
            "any item", "all item", "all items", "every item", "each item",
            "any items", "every items", "each items", "priced at",
            # SW-3: connector / offer-filler words must never become priced items
            # ("... and also $7.99 combo" -> no phantom "also").
            "also", "and also", "plus", "or",
            # SW-4: price-history / correction fillers ("Idly $8 (was $7)") are not
            # items — without this the "(was $N)" annotation makes a phantom "was"
            # item that repeats with two prices and trips the price-conflict gate on
            # a legitimate correction.
            "was", "were", "previously", "originally",
            "a", "an", "the", "and", "with", "include", "includes", "for", "on",
            "at", "each", "plate", "pc", "pcs", "piece", "pieces",
        }:
            return "rejected"
        # SW-2: only complete the suffix onto a bare protein/veg MODIFIER
        # ("Chicken" -> "Chicken Biryani"); a complete dish ("Dosa") stays as-is.
        if (
            category_suffix
            and category_suffix.lower() not in lowered
            and not _COMPLETE_DISH_RE.search(name)
            and _BIRYANI_MODIFIER_RE.match(name)
        ):
            name = f"{name.title()} {category_suffix}"
        if name.lower() in {"a", "an", "the", "and", "with", "include", "includes", "for", "on", "at", "each", "plate", "pc", "pcs", "piece", "pieces"}:
            return "rejected"
        if name.lower() in {"any item", "all item", "all items", "every item", "each item", "priced at"}:
            return "rejected"
        if promo_name.search(name) or bad_context.search(name):
            return "rejected"
        if re.search(
            r"\b(?:morning|evening|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*\b\d{1,2}\s*(?:am|pm)\b"
            r"|\b\d{1,2}\s*(?:am|pm)\b",
            lowered,
        ):
            return "rejected"
        meaningful_words = [part for part in name.split() if part not in {"-", "–", "—"}]
        if len(meaningful_words) > 5:
            return "rejected"
        seen.add(name.lower())
        seen_prices[name.lower()] = price
        name_fact = _fact(f"item:{len(seen)-1}:name", "Item", name, "customer_text", message_id=message_id)
        price_fact = _fact(f"item:{len(seen)-1}:price", "Price", price, "customer_text", message_id=message_id)
        if name_fact:
            facts.append(name_fact)
        if price_fact:
            facts.append(price_fact)
        return "accepted"

    for segment in re.split(r"[\n\r,;]+", text or ""):
        suffix_match = suffix_price.search(segment)
        if suffix_match:
            name = segment[: suffix_match.start()].strip()
            name = re.sub(r"[-:–—]+$", "", name).strip()
            add_item(name, _price_str(suffix_match.group("cur"), suffix_match.group("price")))
            continue
        name_first_claimed = False
        for match in price_for_name.finditer(segment):
            if add_item(match.group("name"), _price_str(match.group("cur"), match.group("price"))) in ("accepted", "flat_subject", "duplicate"):
                name_first_claimed = True
        for match in compact_name_before_price.finditer(segment):
            if add_item(match.group("name"), _price_str(match.group("cur"), match.group("price"))) in ("accepted", "flat_subject", "duplicate"):
                name_first_claimed = True
        for match in name_before_price.finditer(segment):
            cur = match.group("cur")
            raw_price = match.group("price") if cur else match.group("bareprice")
            if add_item(match.group("name"), _price_str(cur, raw_price), bare=not cur) in ("accepted", "flat_subject", "duplicate"):
                name_first_claimed = True
        if name_first_claimed:
            # A name-first pattern PAIRED an item OR claimed the price via a
            # flat-price subject (detected on the normalized name in add_item); do
            # NOT run the price-before-name fallback. A name-first match rejected as
            # prompt-prefix/bad-context garbage returns "rejected" and does NOT set
            # this flag, so a real price-first item ("...$20 men haircut") is still recovered.
            continue
        for match in price_before_name.finditer(segment):
            name = match.group("name")
            name = re.split(r"\b(?:and|with|include|includes|plus|for|on|at)\b|[.!?]", name, maxsplit=1, flags=re.IGNORECASE)[0]
            if not name.strip():
                continue
            add_item(name, _price_str(match.group("cur"), match.group("price")))
    return facts


def price_conflict_signals(text: str) -> list[str]:
    """SW-4: item names the brief prices MORE THAN ONE way ("Biryani $10 Biryani
    $12"). `_item_price_facts` dedups such a repeat to the first price and drops
    the second silently; running the same extraction with a `conflicts` sink
    surfaces the conflicting names so the caller can route the project to manual
    review (reason_code "price_conflict") instead of shipping one price blind.

    Detection ONLY — never rewrites, merges, or picks a price. Returns the
    conflicting item names (dedup, in first-seen order); [] when every repeat
    agrees on price.

    NOTE (cross-file wiring, left as a report note per task scope): turning a
    non-empty result into a FlyerManualReview.reason_code="price_conflict" write
    happens in the project-creation / QA gate script, OUTSIDE facts.py — that
    call site is not wired here.
    """
    conflicts: list[str] = []
    _item_price_facts(text or "", conflicts=conflicts)
    return conflicts


def _generic_item_price(text: str) -> str:
    patterns = (
        r"\bprice\s+(?:any|every|each|all)\s+(?:item|items?)\s*\$?\s*(?P<price>\d+(?:\.\d{2})?)(?!\s*[%\-])\b",
        r"\b(?:any|every|each|all)\s+(?:item|items?)\s+price\s+(?:is\s+)?(?:at|for|=|:)\s*\$?\s*(?P<price>\d+(?:\.\d{2})?)(?!\s*[%\-])\b",
        r"\b(?:any|every|each|all)\s+(?:[A-Za-z][A-Za-z0-9'&/-]*\s+){0,3}items?\s+(?:priced\s+)?(?:at|for|is|=|:)\s*\$\s*(?P<price>\d+(?:\.\d{2})?)(?!\s*[%\-])\b",
        r"\b(?:any|every|each|all)\s+(?:item|items?)\s+(?:priced\s+)?(?:at|for|is|=|:)\s*\$\s*(?P<price>\d+(?:\.\d{2})?)(?!\s*[%\-])\b",
        # Connector-less flat price: "any item $8.99", "every breakfast item $8.99"
        # (no at/for/priced). The $ must follow the item phrase directly, and the
        # (?!\s*[%\-]) guard keeps a plain "$8.99" (not "$8-99" / a "%" discount).
        r"\b(?:any|every|each|all)\s+(?:[A-Za-z][A-Za-z0-9'&/-]*\s+){0,3}items?\s*\$\s*(?P<price>\d+(?:\.\d{2})?)(?!\s*[%\-])\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return f"${match.group('price')}"
    return ""


FAMOUS_ITEM_SETS: tuple[tuple[re.Pattern[str], list[str]], ...] = (
    (
        re.compile(r"\bindo[-\s]?chinese\b", re.IGNORECASE),
        [
            "Veg Manchurian",
            "Gobi Manchurian",
            "Chili Paneer",
            "Hakka Noodles",
            "Schezwan Fried Rice",
            "Chili Garlic Noodles",
            "Manchow Soup",
            "Spring Rolls",
            "American Chopsuey",
            "Chili Chicken",
        ],
    ),
)


def _requested_famous_item_facts(text: str, *, message_id: str = "") -> list[FlyerLockedFact]:
    match = re.search(
        r"\binclude\s+(?P<count>\d{1,2})\s+(?:famous|popular|top)\s+(?P<category>[A-Za-z][A-Za-z\s-]{2,50}?)\s+items?\b",
        text or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return []
    count = max(1, min(10, int(match.group("count"))))
    category = match.group("category")
    generic_price = _generic_item_price(text)
    for pattern, names in FAMOUS_ITEM_SETS:
        if not pattern.search(category):
            continue
        facts: list[FlyerLockedFact] = []
        for index, name in enumerate(names[:count]):
            name_fact = _fact(f"item:{index}:name", "Item", name, "customer_text", message_id=message_id)
            if name_fact:
                facts.append(name_fact)
            if generic_price:
                price_fact = _fact(f"item:{index}:price", "Price", generic_price, "customer_text", message_id=message_id)
                if price_fact:
                    facts.append(price_fact)
        return facts
    return []


def _offer_price_fact(text: str, *, message_id: str = "") -> FlyerLockedFact | None:
    patterns = [
        r"\ball\s+you\s+can\s+eat\s*(?:@|for|at|:)?\s*\$?\s*(?P<price>\d+(?:\.\d{2})?)\b",
        r"\b(?:offer|special|deal)\s+price\s*(?:is|@|for|at|:)?\s*\$?\s*(?P<price>\d+(?:\.\d{2})?)\b",
        r"\bset\s+all\s+[a-z][a-z0-9 '&/-]{1,40}?\s+prices?\s+to\s+\$?\s*(?P<price>\d+(?:\.\d{2})?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return _fact(
                "offer_price",
                "Offer price",
                f"${match.group('price')}",
                "customer_text",
                message_id=message_id,
            )
    return None


def _schedule_fact(text: str, *, message_id: str = "") -> FlyerLockedFact | None:
    day = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    two_days = re.search(
        rf"\b(?P<first>{day})\s+and\s+(?P<second>{day})\s+(?:of\s+)?every\s+week\b",
        text or "",
        flags=re.IGNORECASE,
    )
    if two_days:
        return _fact(
            "schedule",
            "Schedule",
            f"{two_days.group('first').title()} and {two_days.group('second').title()} every week",
            "customer_text",
            message_id=message_id,
        )
    single_day = re.search(
        rf"\b(?P<day>{day})\s+(?:of\s+)?every\s+week\b",
        text or "",
        flags=re.IGNORECASE,
    )
    if single_day:
        return _fact(
            "schedule",
            "Schedule",
            f"{single_day.group('day').title()} every week",
            "customer_text",
            message_id=message_id,
        )
    every_day = re.search(
        rf"\bevery\s+(?P<day>{day})\b",
        text or "",
        flags=re.IGNORECASE,
    )
    if every_day:
        return _fact(
            "schedule",
            "Schedule",
            f"Every {every_day.group('day').title()}",
            "customer_text",
            message_id=message_id,
        )
    return None


# A "N items: a, b, c" colon list (e.g. "6 famous South Indian items: Idli, Dosa,
# Vada, Uttapam, Pongal, Sambar") is a MENU, not one offer. Splitting it into
# individual item:N:name facts (instead of one compound required offer:0) lets the
# referee match each name and the recovery/deterministic overlay redraw a
# misspelled/dropped name — instead of fail-closing the whole flyer on one
# un-matchable compound string (live F0164, 2026-06-16).
# Delimiter is COLON-ONLY (operator spec "N items: a, b, c"). A hyphen/dash after
# "items" is NOT accepted: "Buy 2 items - get 1 free and free chai" is an offer, not
# a menu, and a dash delimiter would wrongly split it (Codex 2026-06-16).
_ITEM_LIST_COLON_RE = re.compile(
    r"\bitems?\s*:\s+(?P<list>[A-Za-z][^.;\n]*)",
    re.IGNORECASE,
)
# An offer/benefit clause (even after "items:") is NOT a menu list — keep it as an
# offer. Catches "get 1 free", "buy ... get", "$/%", "N off/free", "free ... with".
_OFFER_BENEFIT_RE = re.compile(
    r"\$|%|\b\d+\s*(?:off|free)\b|\b(?:buy|spend|order|purchase)\b|\bget\b.*\bfree\b|\bfree\b.*\b(?:with|on|above|over)\b",
    re.IGNORECASE,
)


def _item_list_names(text: str) -> list[str]:
    """Return the individual item names from a 'N items: a, b, c' colon list.

    Returns [] unless the text contains an ``items:``-introduced list with at least
    two members (a comma or 'and') AND the list reads like dish names rather than an
    offer/benefit clause. A genuine offer that merely mentions "items" is left
    untouched. Names are NOT cleaned here — callers run them through ``add_item``
    (which strips connectors, rejects prices/identity, and dedups)."""
    match = _ITEM_LIST_COLON_RE.search(text or "")
    if not match:
        return []
    listing = match.group("list").strip()
    if not ("," in listing or re.search(r"\band\b", listing, flags=re.IGNORECASE)):
        return []
    listing = re.sub(r"\b(?:and|plus)\b", ",", listing, flags=re.IGNORECASE)
    names = [part.strip() for part in re.split(r"[,;/]+", listing) if part.strip()]
    # Offer/benefit clause → not a menu list. Checked PER MEMBER so a clean menu
    # where one dish has "free" and another has "with" ("Gluten Free Dosa, Poori
    # with Aloo") is not cross-matched (Codex NIT 2026-06-16).
    if any(_OFFER_BENEFIT_RE.search(name) for name in names):
        return []
    return names


# BC-2: a bare dish token is short, letters-only (spaces / & / ' / - allowed),
# no digits or currency. Instruction / verb / day-time / context words disqualify
# it so prompt text ("Create a weekend flyer", "open sat and sun morning") can
# never be mistaken for a dish. `special/menu/weekend` etc. ARE disqualifiers —
# they mark a heading or instruction, not a dish name.
_BARE_NAME_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z '&/-]{0,40}$")
_BARE_NAME_STOPWORD_RE = re.compile(
    r"\b(?:create|make|makes|making|design|generate|build|want|wants|need|needs|"
    r"please|help|use|used|using|include|includes|including|add|adds|feature|"
    r"flyer|flier|poster|banner|promo|promote|promoting|promotion|sale|offer|"
    r"special|specials|deal|deals|menu|catering|delivery|pickup|order|orders|"
    r"open|opens|opening|closed|serve|serves|serving|cook|cooks|cooking|fresh|"
    r"today|tomorrow|tonight|weekend|weekday|morning|evening|afternoon|night|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|wed|thu|fri|sat|sun|am|pm|for|with|and|or|the|my|our|your|"
    r"restaurant|store|shop|kitchen|address|phone|contact|logo|colorful|modern|"
    r"colors|color|background|style|theme|price|prices|"
    # F2: color / style / adjective words are theme descriptors, never dish
    # names — a bare "Green" / "Gold" line is a palette, not a menu item.
    r"green|gold|golden|red|blue|maroon|royal|purple|violet|orange|yellow|pink|"
    r"white|black|silver|grey|gray|navy|teal|crimson|"
    r"elegant|festive|bright|dark|light|simple|classic|traditional|vibrant|bold|"
    r"minimal|minimalist|clean|fancy|premium|luxury|rustic|vintage|retro|pastel|neon)\b",
    re.IGNORECASE,
)


def _is_bare_dish_token(tok: str) -> bool:
    tok = (tok or "").strip().strip(".")
    if not tok or not _BARE_NAME_TOKEN_RE.match(tok):
        return False
    if len(tok.split()) > 4:
        return False
    return not _BARE_NAME_STOPWORD_RE.search(tok)


def _bare_menu_names(text: str) -> list[str]:
    """BC-2: bounded bare (price-less) dish-name extraction.

    Fires ONLY for two tightly-bounded shapes so instruction / prompt text never
    becomes a phantom item:
      (a) a RUN of >=2 consecutive newline lines each reading as a bare dish token
          ("Idli\\nDosa\\nVada"); a lone bare line is not enough, and
      (b) a whole trimmed brief that is NOTHING but a >=2-member comma list of
          bare dish tokens ("Idli, Dosa, Vada").

    Every other shape (sentences, single lines, day/time phrases, mixed prose,
    priced text) yields []. Mixed-prose comma runs are deliberately OUT of scope.
    Style / color / adjective lists ("green, gold", "modern, colorful") are
    rejected by the `_BARE_NAME_STOPWORD_RE` disqualifier in `_is_bare_dish_token`
    (F2): those tokens are never dish names, so neither branch (a) nor branch (b)
    can turn them into items."""
    names: list[str] = []
    # (a) consecutive bare newline lines (run length >= 2).
    run: list[str] = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if _is_bare_dish_token(line):
            run.append(line)
        else:
            if len(run) >= 2:
                names.extend(run)
            run = []
    if len(run) >= 2:
        names.extend(run)
    # (b) whole-input pure comma list (single line, all members bare dish tokens).
    stripped = (text or "").strip()
    if "\n" not in stripped and "," in stripped:
        parts = [p.strip() for p in stripped.split(",") if p.strip()]
        if len(parts) >= 2 and all(_is_bare_dish_token(p) for p in parts):
            names.extend(parts)
    return names


def _item_name_facts(text: str, *, message_id: str = "") -> list[FlyerLockedFact]:
    facts: list[FlyerLockedFact] = []
    seen: set[str] = set()
    skip_exact = {
        "all famous",
        "all famous biryanis",
        "all famous biryani's",
        "all famous south indian biryanis",
        "all famous south indian biryani's",
        "address",
        "phone",
        "logo",
        "saved address",
        "saved phone",
        "saved logo",
        "catering note",
        "delivery/payment badges",
        "delivery badges",
        "payment badges",
    }

    def add_item(name: str) -> None:
        name = _clean(name)
        name = re.sub(r"^(?:and|with|include|includes|feature|features|featuring)\s+", "", name, flags=re.IGNORECASE)
        if not name:
            return
        normalized = name.lower()
        # Reject instruction fragments that can appear after "include ...",
        # e.g. "add price as $16.99 for chicken", to avoid poisoning item names.
        if "$" in name or "price" in normalized:
            return
        if re.search(r"\b(?:add|set|use|change|update)\s+(?:the\s+)?price\b", normalized):
            return
        if normalized in skip_exact:
            return
        if normalized.startswith("all famous ") and "biryani" in normalized:
            return
        if any(term in normalized for term in ("address", "phone", "logo")):
            return
        if len(name.split()) > 5:
            return
        key = _norm(name)
        if not key or key in seen:
            return
        seen.add(key)
        fact = _fact(f"item:{len(seen)-1}:name", "Item", name, "customer_text", message_id=message_id)
        if fact:
            facts.append(fact)

    for match in re.finditer(
        r"\binclude\s+(?P<items>.+?)(?=\.|\b(?:use|timings?|time|style|location|address|phone|contact)\b|$)",
        text or "",
        flags=re.IGNORECASE,
    ):
        clause = match.group("items")
        if not ("," in clause or re.search(r"\band\b", clause, flags=re.IGNORECASE)):
            continue
        clause = re.sub(r"\b(?:and|plus)\b", ",", clause, flags=re.IGNORECASE)
        for part in re.split(r"[,;/]+", clause):
            add_item(part)
    # "N items: a, b, c" colon list (no "include" verb) — live F0164.
    for name in _item_list_names(text):
        add_item(name)
    # BC-2: bare (price-less) newline / pure-comma dish list.
    for name in _bare_menu_names(text):
        add_item(name)
    return facts


def _offset_standalone_item_names(
    name_facts: list[FlyerLockedFact],
    paired_item_facts: list[FlyerLockedFact],
) -> list[FlyerLockedFact]:
    paired_names: set[str] = set()
    max_index = -1
    for fact in paired_item_facts:
        match = re.match(r"^item:(?P<index>\d+):(?P<kind>name|price)$", fact.fact_id)
        if not match:
            continue
        max_index = max(max_index, int(match.group("index")))
        if match.group("kind") == "name":
            paired_names.add(_norm(fact.value))
    next_index = max_index + 1
    offset: list[FlyerLockedFact] = []
    for fact in name_facts:
        if _norm(fact.value) in paired_names:
            continue
        offset.append(fact.model_copy(update={"fact_id": f"item:{next_index}:name"}))
        next_index += 1
    return offset


def _requests_more_item_suggestions(text: str) -> bool:
    return bool(re.search(
        r"\b(?:add|include|suggest|recommend|give)\s+(?:some\s+)?(?:more|additional|extra)\s+"
        r"(?:items?|suggestions?|recommendations?|options?)\b",
        text or "",
        flags=re.IGNORECASE,
    ))


_GENERATED_ITEM_SUGGESTION_RE = re.compile(
    r"\b(?:include|add|suggest|recommend|give|show|feature)\s+"
    r"(?:me\s+|us\s+)?"
    r"(?:(?:\d{1,2}|some|more|additional|extra)\s+)?"
    r"(?:(?:famous|popular|top|best|signature|recommended|suggested)\s+)?"
    r"(?:[A-Za-z][\w'/-]*\s+){0,6}items?\b",
    re.IGNORECASE,
)
_GENERATED_VARIETY_SUGGESTION_RE = re.compile(
    r"\b(?:include|add|suggest|recommend|give|show|feature)\s+"
    r"(?:me\s+|us\s+)?"
    r"\d{1,2}\s+(?:[A-Za-z][\w'/-]*\s+){0,6}"
    r"(?:varieties|dishes|options|specials)\b",
    re.IGNORECASE,
)
_GENERATED_ITEM_TOTAL_RE = re.compile(
    r"\b\d{1,2}\s+(?:[A-Za-z][\w'/-]*\s+){0,6}items?\s+"
    r"(?:total|in\s+all|altogether|overall)\b",
    re.IGNORECASE,
)
_PACKAGE_CONTEXT_RE = re.compile(
    r"\b(?:combo|package|meal\s+deal|meal\s+combo|family\s+pack|pick\s+any|bundle)\b",
    re.IGNORECASE,
)
_EXPLICIT_SUGGESTION_VERB_RE = re.compile(
    r"\b(?:suggest|recommend|give)\s+(?:me\s+|us\s+)?"
    r"(?:(?:\d{1,2}|some|more|additional|extra)\s+)?"
    r"(?:[A-Za-z][\w'/-]*\s+){0,6}(?:items?|suggestions?|recommendations?|options?)\b",
    re.IGNORECASE,
)


def requests_generated_item_suggestions(text: str) -> bool:
    """True only when the customer explicitly asks us to invent menu items.

    Ambiguous wording must stay faithful: a plainer correct flyer is safer
    than silently expanding a combo/package request into an unrequested menu.
    """
    source = text or ""
    if _PACKAGE_CONTEXT_RE.search(source):
        return bool(_EXPLICIT_SUGGESTION_VERB_RE.search(source) or _requests_more_item_suggestions(source))
    return bool(
        _GENERATED_ITEM_SUGGESTION_RE.search(source)
        or _GENERATED_VARIETY_SUGGESTION_RE.search(source)
        or _GENERATED_ITEM_TOTAL_RE.search(source)
        or _requests_more_item_suggestions(source)
    )


def extract_text_facts(
    fields: FlyerRequestFields,
    raw_request: str,
    *,
    message_id: str = "",
    profile_business_name: str = "",
    allow_text_identity: bool = True,
    cfg: "FlyerConfig | None" = None,
    brief_provenance: dict | None = None,
) -> list[FlyerLockedFact]:
    text = _combined_extraction_text(raw_request or "", fields.notes or "")
    semantic_brief = build_semantic_flyer_brief(
        fields,
        raw_request,
        profile_business_name=profile_business_name,
        allow_text_identity=allow_text_identity,
        provider=build_hermes_semantic_brief_provider(),
        provenance=brief_provenance,
    )
    facts: list[FlyerLockedFact] = []
    event_or_campaign = fields.event_or_business_name or ""
    campaign_title = _normalize_campaign_title(semantic_brief.campaign_title or event_or_campaign)
    if _norm(campaign_title) == _norm(profile_business_name):
        campaign_title = ""
    if _looks_like_instruction_fragment(campaign_title):
        campaign_title = ""
    text_business = ""
    if allow_text_identity and event_or_campaign and not _looks_like_instruction_fragment(event_or_campaign):
        text_business = _business_title_from_text(event_or_campaign)
    for item in [
        _fact("business_name", "Business", text_business, "customer_text", message_id=message_id) if text_business else None,
        _fact("campaign_title", "Campaign", campaign_title, "customer_text", message_id=message_id),
        _fact("headline", "Headline", _headline(text), "customer_text", message_id=message_id),
        _fact("tagline", "Tagline", _tagline(text), "customer_text", message_id=message_id),
        _fact("pricing_structure", "Pricing", semantic_brief.pricing_structure, "customer_text", message_id=message_id),
        _fact("location", "Location", fields.venue_or_location or "", "customer_text", required=False) if allow_text_identity else None,
        _fact("contact_phone", "Contact", fields.contact_info or "", "customer_text") if allow_text_identity else None,
    ]:
        if item:
            facts.append(item)
    # An `offer:N` fact is REQUIRED and customer-visible. `build_semantic_flyer_brief`
    # already bounds provider offers to faithful spans; this re-checks at the fact
    # boundary so NO offer source (provider, deterministic fallback, or any future
    # producer) can lock a request-tail echo / over-captured paragraph as a required
    # fact. Faithful offers are unchanged; only unbounded/echo values are dropped.
    offer_index = 0
    for offer in semantic_brief.offers:
        if not _semantic_offer_is_faithful(offer.text):
            continue
        if _item_list_names(offer.text):
            # An item-list offer ("N items: a, b, c") is a MENU, not a compound
            # offer. It is captured as individual item:N:name facts by
            # _item_name_facts below (from the raw brief text). Locking it as a
            # required offer:N would force the referee to exact-match an
            # un-matchable compound string → fail-closed to manual (live F0164).
            continue
        item = _fact(f"offer:{offer_index}", "Offer", offer.text, "customer_text", message_id=message_id)
        if item:
            facts.append(item)
            offer_index += 1
    parsed_schedule = _schedule_fact(text, message_id=message_id)
    if semantic_brief.schedule and not parsed_schedule:
        item = _fact("schedule", "Schedule", semantic_brief.schedule, "customer_text", message_id=message_id)
        if item:
            facts.append(item)
    if semantic_brief.promotion_end:
        item = _fact("promotion_end", "Promotion end", semantic_brief.promotion_end, "customer_text", message_id=message_id)
        if item:
            facts.append(item)
    offer_price = _offer_price_fact(text, message_id=message_id)
    if offer_price:
        facts.append(offer_price)
    if parsed_schedule:
        facts.append(parsed_schedule)
    item_name_facts = _item_name_facts(text, message_id=message_id)
    # Bounded creative planner (slice 2 producer; slice 5 per-request category gate).
    # Fires ONLY when armed (flag + firewall + >=1 category opened, is_active) AND THIS
    # request's category is operator-enabled. When it produces inferred items it
    # SUPERSEDES the hardcoded famous-item fallback (the planner covers ANY enabled
    # category, fixing the one-category FAMOUS_ITEM_SETS brittleness). Materialization
    # is firewall-gated (the structural interlock). Dormant default (flag off / category
    # not enabled / not matched) ⇒ inferred_facts == [] ⇒ byte-identical to the hardcoded
    # path below. FAMOUS_ITEM_SETS is physically removed only at operator per-category
    # enablement (design §9 slice 5), never while dormant.
    # creative_planner REMOVED (graduation commit 6, operator ruling #3):
    # inert-by-construction for its whole life — no config category was ever
    # enabled, so this always evaluated to [] (byte-identical to the empty
    # default). The firewall (creative_firewall.is_hard_fact_claim) survives
    # as flyer_brief_validator's shared helper.
    inferred_facts: list[FlyerLockedFact] = []
    famous_item_facts = _requested_famous_item_facts(text, message_id=message_id)
    generic_price = _generic_item_price(text)
    paired_item_price_facts = _item_price_facts(text, message_id=message_id)
    item_price_facts = paired_item_price_facts
    if paired_item_price_facts:
        if item_name_facts:
            item_name_facts = _offset_standalone_item_names(item_name_facts, paired_item_price_facts)
    elif famous_item_facts:
        item_name_facts = []
        item_price_facts = famous_item_facts
    elif generic_price and item_name_facts:
        item_price_facts = []
        for name_fact in item_name_facts:
            match = re.match(r"^item:(?P<index>\d+):name$", name_fact.fact_id)
            if not match:
                continue
            price_fact = _fact(
                f"item:{match.group('index')}:price",
                "Price",
                generic_price,
                "customer_text",
                message_id=message_id,
            )
            if price_fact:
                item_price_facts.append(price_fact)
    facts.extend(item_price_facts)
    facts.extend(item_name_facts)
    return reconcile_priced_facts(merge_locked_facts(facts), text)


def promote_inferred_to_confirmed(facts):
    """Provenance lifecycle (moved from the removed creative_planner,
    graduation commit 6 — the function belongs to the source-priority system,
    not the retired producer): on customer approval, hermes_inferred facts the
    customer signed off on become customer-truthful FOR THIS PROJECT — source
    hermes_inferred -> customer_confirmed. Other sources untouched.
    PROJECT-SCOPED ONLY: never writes durable business menu/profile memory."""
    promoted = []
    for fact in facts or []:
        if getattr(fact, "source", "") == "hermes_inferred":
            promoted.append(fact.model_copy(update={"source": "customer_confirmed"}))
        else:
            promoted.append(fact)
    return promoted


def merge_locked_facts(*fact_lists: Iterable[FlyerLockedFact]) -> list[FlyerLockedFact]:
    # Lower number wins (strict `<`; first-seen wins on a tie). Option B ordering
    # (operator-approved): literal customer text wins; customer_confirmed (an
    # assumption the customer approved for this project) is customer-truthful and
    # ranks just below it; the existing sources keep their RELATIVE order (so
    # behavior for the original 7 is unchanged); hermes_inferred is last so an
    # inferred assumption can never shadow a real fact.
    priority = {
        "customer_text": 0,
        "customer_confirmed": 1,
        "operator": 2,
        "customer_profile": 3,
        "reference_ocr": 4,
        "reference_vision": 5,
        "uploaded_asset": 6,
        "system": 7,
        "hermes_inferred": 8,
    }
    item_pattern = re.compile(r"^item:(?P<index>\d+):(?P<kind>name|price)$")
    materialized = [list(facts) for facts in fact_lists]
    merged: dict[str, FlyerLockedFact] = {}
    item_records: list[dict[str, FlyerLockedFact | int | str]] = []

    def item_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

    def add_or_replace_item(name_fact: FlyerLockedFact, price_fact: FlyerLockedFact | None) -> None:
        key = item_key(name_fact.value)
        if not key:
            return
        new_priority = priority.get(name_fact.source, 99)
        for record in item_records:
            if record["key"] != key:
                continue
            old_priority = int(record["priority"])
            if new_priority < old_priority:
                record["name"] = name_fact
                record["price"] = price_fact
                record["priority"] = new_priority
            return
        item_records.append({"key": key, "name": name_fact, "price": price_fact, "priority": new_priority})

    for facts in materialized:
        for fact in facts:
            if item_pattern.match(fact.fact_id):
                continue
            current = merged.get(fact.fact_id)
            if (
                fact.fact_id == "campaign_title"
                and current is not None
                and getattr(current, "source", "") == "customer_text"
                and str(getattr(fact, "source", "") or "").startswith("reference_")
                and _replaceable_customer_text_campaign_title(current.value)
            ):
                merged[fact.fact_id] = fact
                continue
            if current is None or priority.get(fact.source, 99) < priority.get(current.source, 99):
                merged[fact.fact_id] = fact

        grouped: dict[int, dict[str, FlyerLockedFact]] = {}
        order: list[int] = []
        for fact in facts:
            match = item_pattern.match(fact.fact_id)
            if not match:
                continue
            index = int(match.group("index"))
            if index not in grouped:
                grouped[index] = {}
                order.append(index)
            grouped[index][match.group("kind")] = fact
        for index in order:
            name_fact = grouped[index].get("name")
            if name_fact is None:
                continue
            add_or_replace_item(name_fact, grouped[index].get("price"))

    result = list(merged.values())
    for index, record in enumerate(item_records):
        name_fact = record["name"]
        price_fact = record["price"]
        if isinstance(name_fact, FlyerLockedFact):
            result.append(name_fact.model_copy(update={"fact_id": f"item:{index}:name"}))
        if isinstance(price_fact, FlyerLockedFact):
            result.append(price_fact.model_copy(update={"fact_id": f"item:{index}:price"}))
    return result


def _price_norm(value: str) -> str:
    """Canonical currency-amount form for comparison ('$ 7.9' -> '7.90'); '' if none."""
    m = re.search(r"\d+(?:\.\d+)?", value or "")
    if not m:
        return ""
    return f"{float(m.group(0)):.2f}"


def _offer_is_simple_priced_line(text: str) -> bool:
    """True when an offer is just 'Name $price' (a menu line), False when it has
    descriptive/combo content beyond the name+price (rich offer)."""
    items = _item_price_facts(text or "")
    if len([f for f in items if f.fact_id.endswith(":name")]) != 1:
        return False
    residual = re.sub(r"\$\s*\d+(?:\.\d{1,2})?", " ", _normalize_dashes(text or ""))
    name = next((f.value for f in items if f.fact_id.endswith(":name")), "")
    residual = residual.replace(name, " ")
    residual = re.sub(r"[^a-z]+", " ", residual.lower()).strip()
    return all(w in {"", "and", "the", "a", "for", "-"} for w in residual.split())


def reconcile_priced_facts(facts: list[FlyerLockedFact], source_text: str) -> list[FlyerLockedFact]:
    """SOURCE-BACKED-FIRST suppression pass over merged locked_facts. Removes
    duplicate / derived / unsupported / conflicting PRICED facts so each priced
    fact renders once and reconciles to the customer brief. SUPPRESSION ONLY —
    never invents, infers, rewrites, or auto-corrects a price. (Design 2026-06-20,
    revised: rich-priced-offer-gated derived suppression + name-only preservation.)"""
    item_re = re.compile(r"^item:(?P<i>\d+):(?P<k>name|price)$")

    def nname(v: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", _normalize_dashes(v or "").lower()).strip()

    # Canonical source-backed priced items re-derived from the brief (dash-fixed).
    src_names_to_price: dict[str, str] = {}
    tmp: dict[str, dict[str, str]] = {}
    for f in _item_price_facts(source_text or ""):
        mm = item_re.match(f.fact_id)
        if mm:
            tmp.setdefault(mm.group("i"), {})[mm.group("k")] = f.value
    for rec in tmp.values():
        if "name" in rec:
            src_names_to_price[nname(rec["name"])] = _price_norm(rec.get("price", ""))
    flat_price = ""
    for f in facts:
        if f.fact_id == "pricing_structure":
            flat_price = _price_norm(f.value)
    if not flat_price:
        # "any item $X" briefs attach the flat price per item without emitting a
        # pricing_structure fact; recover the flat price from the source via the
        # same detector extraction uses, so named-in-brief items at that price
        # reconcile as source-backed.
        flat_price = _price_norm(_generic_item_price(source_text or ""))

    def is_source_backed(name: str, price: str) -> bool:
        n, p = nname(name), _price_norm(price)
        if p and src_names_to_price.get(n) == p:
            return True
        # Flat-price briefs ("any item $X") apply the price to ANY item, including
        # system-expanded names not literally in the brief (famous-path). An item
        # AT the flat price is source-backed by price; a conflicting price (!= flat)
        # still fails here and is suppressed.
        if flat_price and p == flat_price:
            return True
        return False

    grouped: dict[str, dict[str, FlyerLockedFact]] = {}
    offers: list[FlyerLockedFact] = []
    others: list[FlyerLockedFact] = []
    rich_priced_offer_subjects: set[tuple[str, str]] = set()
    for f in facts:
        m = item_re.match(f.fact_id)
        if m:
            grouped.setdefault(m.group("i"), {})[m.group("k")] = f
        elif f.fact_id.startswith("offer:"):
            offers.append(f)
            if (not _offer_is_simple_priced_line(f.value)) and re.search(r"[$₹]\s*\d", f.value or ""):
                # The offer's OWN priced subject is its headline (before the component
                # list). Priced COMPONENTS inside ("includes Samosa $2") must NOT be
                # added — else a legitimate standalone item gets wrongly suppressed.
                headline = re.split(
                    r":|\binclud(?:e|es|ing)\b|\bwith\b",
                    f.value or "", maxsplit=1, flags=re.IGNORECASE,
                )[0]
                sub_tmp: dict[str, dict[str, str]] = {}
                for sub in _item_price_facts(headline):
                    sm = item_re.match(sub.fact_id)
                    if sm:
                        sub_tmp.setdefault(sm.group("i"), {})[sm.group("k")] = sub.value
                for rec in sub_tmp.values():
                    if "name" in rec:
                        rich_priced_offer_subjects.add((nname(rec["name"]), _price_norm(rec.get("price", ""))))
        else:
            others.append(f)

    kept_items: list[tuple[FlyerLockedFact, FlyerLockedFact | None]] = []
    kept_item_keys: set[tuple[str, str]] = set()
    for idx in sorted(grouped, key=lambda s: int(s)):
        nf = grouped[idx].get("name")
        pf = grouped[idx].get("price")
        if nf is None:
            continue
        # Inferred items are firewall-gated, not customer-source facts; reconcile
        # does not police them. Keep as-is.
        if getattr(nf, "source", "") == "hermes_inferred":
            kept_items.append((nf, pf))
            continue
        n = nname(nf.value)
        price = pf.value if pf else ""
        p = _price_norm(price)
        # (1) combo-derived: item IS a rich priced offer's OWN subject -> offer canonical -> suppress.
        if p and (n, p) in rich_priced_offer_subjects:
            continue
        # (2) name-only item (no price): NOT a priced fact -> reconcile does not police
        # it (planner/famous-path/menu names legitimately may not be in the brief text).
        # Pass through untouched.
        if pf is None or not p:
            kept_items.append((nf, pf))
            continue
        # (3) priced item: keep iff source-backed; else suppress (covers conflicting price).
        if not is_source_backed(nf.value, price):
            continue
        kept_items.append((nf, pf))
        kept_item_keys.add((n, p))

    kept_offers: list[FlyerLockedFact] = []
    for f in offers:
        if _offer_is_simple_priced_line(f.value):
            items = _item_price_facts(f.value)
            nm = next((x.value for x in items if x.fact_id.endswith(":name")), "")
            pr = next((x.value for x in items if x.fact_id.endswith(":price")), "")
            if (nname(nm), _price_norm(pr)) in kept_item_keys:
                continue
        kept_offers.append(f)

    result = list(others) + list(kept_offers)
    for new_i, (nf, pf) in enumerate(kept_items):
        result.append(nf.model_copy(update={"fact_id": f"item:{new_i}:name"}))
        if pf is not None:
            result.append(pf.model_copy(update={"fact_id": f"item:{new_i}:price"}))
    return result


def facts_by_id(project: FlyerProject | object) -> dict[str, FlyerLockedFact]:
    return {fact.fact_id: fact for fact in getattr(project, "locked_facts", [])}


def required_fact_blockers(project: FlyerProject) -> list[str]:
    return [f"missing required fact: {fact.fact_id}" for fact in project.locked_facts if fact.required and not fact.value.strip()]


# Top-level customer-visible facts that every renderable Flyer project must have.
# Items/prices are validated at generation time, not here — many flyer categories
# (service/salon/tutor/event) have no $-prices to extract.
DEFAULT_REQUIRED_FACT_IDS = ("business_name", "contact_phone")


def missing_required_facts(
    project: FlyerProject,
    *,
    required_ids: tuple[str, ...] = DEFAULT_REQUIRED_FACT_IDS,
) -> list[str]:
    """Return the fact_ids that the project SHOULD carry as required locked facts
    but doesn't — i.e. extraction never produced them from any source (text /
    profile / reference). Surfaces the "required slot missing entirely" class,
    which `required_fact_blockers` cannot — it only catches required facts whose
    value went empty post-creation.
    """
    by_id = facts_by_id(project)
    return [fid for fid in required_ids if not (by_id.get(fid) and by_id[fid].value.strip())]


def fact_value(
    project: FlyerProject | object,
    fact_id: str,
    *,
    fallback: str | None = "",
) -> str:
    """Return the locked-fact value for `fact_id`, falling back to `fallback`
    when the project has no locked fact (or empty value) for that slot.

    Renderer/QA call sites should prefer this over `project.fields.*` so that
    typed customer corrections (customer_text source) and operator overrides
    flow through to generated copy without a separate codepath per field.
    `fallback=None` is coerced to "" so callers can pass Optional[str] fields
    directly without an `or ""` at every call site.
    """
    by_id = facts_by_id(project)
    fact = by_id.get(fact_id)
    if fact and fact.value.strip():
        return fact.value
    return fallback or ""


def _populate_forbidden_substrings(contract: FlyerSourceContract) -> None:
    """Mutate `contract.forbidden_substrings` from requested replacements.

    Three independent backstops guard against false positives that would
    block legitimate flyers:
      1. Skip if the OLD value is already a vision-extracted section item.
      2. Skip if NEW starts with OLD (e.g. `Rice -> Jeera Rice` is a variant).
      3. Skip single-word values that are not phone-shaped or address-shaped
         — single-word brands are too risky to auto-forbid.
    """
    section_items = {item.lower() for section in contract.sections for item in section.items}
    for old, new in contract.requested_replacements.items():
        if not old or len(old) < 3:
            continue
        # Backstop 1: vision-confirmed menu item.
        if old.lower() in section_items:
            continue
        # Backstop 2: new is a variant/extension of old.
        if new and new.lower().startswith(old.lower()):
            continue
        digits = re.sub(r"\D", "", old)
        if len(digits) >= 10:
            if digits not in contract.forbidden_substrings:
                contract.forbidden_substrings.append(digits)
            continue
        if re.search(r"\d", old) and any(
            t in old.lower() for t in (" st", " dr", " ave", " rd", " blvd", " ln", " way", " ct", " pkwy")
        ):
            if old not in contract.forbidden_substrings:
                contract.forbidden_substrings.append(old)
            continue
        # Backstop 3: single-word brands are too risky.
        if len(old.split()) < 2:
            continue
        if any(word and word[0].isupper() for word in old.split()):
            if old not in contract.forbidden_substrings:
                contract.forbidden_substrings.append(old)


def source_contract_locked_facts(
    contract: FlyerSourceContract,
    *,
    asset: FlyerAsset,
    message_id: str = "",
) -> list[FlyerLockedFact]:
    """Project a FlyerSourceContract into locked facts.

    Fact id convention:
      - source_heading:N            (reference_vision; required if preserve_*)
      - source_section:N:heading    (reference_vision; required if preserve_*)
      - source_section:N:item:M     (reference_vision; required if preserve_*)
      - replacement:N:old           (customer_text; never required)
      - replacement:N:new           (customer_text; always required)
    """
    facts: list[FlyerLockedFact] = []
    require = contract.preserve_layout or contract.preserve_unmentioned_text
    asset_meta = {"source_asset_id": asset.asset_id, "source_sha256": asset.sha256}

    for idx, heading in enumerate(contract.required_headings):
        fact = _fact(
            f"source_heading:{idx}",
            "Source heading",
            heading,
            "reference_vision",
            required=require,
            message_id=message_id,
        )
        if fact:
            facts.append(fact.model_copy(update=asset_meta))

    for section_idx, section in enumerate(contract.sections):
        heading_fact = _fact(
            f"source_section:{section_idx}:heading",
            "Source section",
            section.heading,
            "reference_vision",
            required=require,
            message_id=message_id,
        )
        if heading_fact:
            facts.append(heading_fact.model_copy(update=asset_meta))
        for item_idx, item in enumerate(section.items):
            item_fact = _fact(
                f"source_section:{section_idx}:item:{item_idx}",
                "Source item",
                item,
                "reference_vision",
                required=require,
                message_id=message_id,
            )
            if item_fact:
                facts.append(item_fact.model_copy(update=asset_meta))

    # `required_text` carries arbitrary visible source text the vision pass
    # flagged as required (e.g. tagline rows, badges, sides rows). Without
    # locking these as facts, "preserve everything else" survives in the
    # schema but never reaches QA. Required-flag mirrors headings/sections.
    for idx, text in enumerate(contract.required_text):
        text_fact = _fact(
            f"source_required_text:{idx}",
            "Source required text",
            text,
            "reference_vision",
            required=require,
            message_id=message_id,
        )
        if text_fact:
            facts.append(text_fact.model_copy(update=asset_meta))

    for repl_idx, (old, new) in enumerate(contract.requested_replacements.items()):
        for suffix, label, value, required in [
            ("old", "Replaced source text", old, False),
            ("new", "Required replacement text", new, True),
        ]:
            fact = _fact(
                f"replacement:{repl_idx}:{suffix}",
                label,
                value,
                "customer_text",
                required=required,
                message_id=message_id,
            )
            if fact:
                facts.append(fact.model_copy(update=asset_meta))

    return facts


def context_isolation_blockers(project: FlyerProject) -> list[str]:
    blockers: list[str] = []
    for fact in project.locked_facts:
        if fact.source not in ALLOWED_NEW_PROJECT_FACT_SOURCES:
            blockers.append(f"locked fact {fact.fact_id} has invalid source {fact.source}")
        if fact.source_project_id:
            blockers.append(f"locked fact {fact.fact_id} carries stale project provenance {fact.source_project_id}")
    return blockers
