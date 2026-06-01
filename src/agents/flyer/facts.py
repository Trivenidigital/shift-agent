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
    from flyer_semantic_brief import build_hermes_semantic_brief_provider, build_semantic_flyer_brief  # type: ignore
except ImportError:
    from agents.flyer.semantic_brief import build_hermes_semantic_brief_provider, build_semantic_flyer_brief
try:
    import flyer_creative_planner as _creative_planner  # type: ignore
except ImportError:
    from agents.flyer import creative_planner as _creative_planner


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


def _normalize_campaign_title(value: str) -> str:
    clean = _clean(value)
    if not clean:
        return ""
    clean = re.sub(r"\s+\b(?:flyer|flier|poster|banner)\b\s*$", "", clean, flags=re.IGNORECASE).strip(" .")
    return clean


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


def _item_price_facts(text: str, *, message_id: str = "") -> list[FlyerLockedFact]:
    facts: list[FlyerLockedFact] = []
    category_suffix = ""
    if re.search(r"\bbiryani(?:'?s|s)?\b", text or "", flags=re.IGNORECASE):
        category_suffix = "Biryani"
    price_for_name = re.compile(
        r"\$\s*(?P<price>\d+(?:\.\d{2})?)\s*(?:for|of)\s+"
        r"(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,40}?)"
        r"(?=\s+(?:and|or)\s+\$|[.!?,;]|$)",
        flags=re.IGNORECASE,
    )
    name_before_price = re.compile(
        r"(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,60}?)\s*(?:-|:)?\s*\$\s*(?P<price>\d+(?:\.\d{2})?)",
        flags=re.IGNORECASE,
    )
    compact_name_before_price = re.compile(
        r"(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,60}?)\s*[-:]\s*\$?(?P<price>\d+(?:\.\d{1,2})?)\s*"
        r"(?:each|plate|per\s+plate)\b",
        flags=re.IGNORECASE,
    )
    price_before_name = re.compile(
        r"\$\s*(?P<price>\d+(?:\.\d{2})?)\s*(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,50})",
        flags=re.IGNORECASE,
    )
    seen: set[str] = set()
    promo_name = re.compile(r"^(?:save|coupon|discount|offer|deal|special|cashback|credit)\b", flags=re.IGNORECASE)
    bad_context = re.compile(r"\b(?:create|make|generate|design|flyer|flier|poster|banner|promoting|promote|promotion)\b", flags=re.IGNORECASE)

    def add_item(name: str, price: str) -> None:
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
        if not name or name.lower() in seen:
            return
        lowered_original = original_name.lower()
        lowered = name.lower()
        if (
            lowered_original.startswith("for ")
            or lowered.endswith(" and")
            or lowered in {"add price as", "price as", "price is", "price for"}
            or re.search(r"\b(?:add|set|use)\s+price\b|\bprice\s+(?:as|is|for)\b", lowered)
            or re.search(r"\bprice\s+(?:any|every|each|all)\s+items?\b", lowered)
        ):
            return
        if category_suffix and category_suffix.lower() not in lowered:
            name = f"{name.title()} {category_suffix}"
        if name.lower() in {"a", "an", "the", "and", "with", "include", "includes", "for", "on", "at", "each", "plate", "pc", "pcs", "piece", "pieces"}:
            return
        if name.lower() in {"any item", "all item", "all items", "every item", "each item", "priced at"}:
            return
        if promo_name.search(name) or bad_context.search(name):
            return
        if re.search(
            r"\b(?:morning|evening|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*\b\d{1,2}\s*(?:am|pm)\b"
            r"|\b\d{1,2}\s*(?:am|pm)\b",
            lowered,
        ):
            return
        if len(name.split()) > 5:
            return
        seen.add(name.lower())
        name_fact = _fact(f"item:{len(seen)-1}:name", "Item", name, "customer_text", message_id=message_id)
        price_fact = _fact(f"item:{len(seen)-1}:price", "Price", price, "customer_text", message_id=message_id)
        if name_fact:
            facts.append(name_fact)
        if price_fact:
            facts.append(price_fact)

    for segment in re.split(r"[\n\r,;]+", text or ""):
        for match in price_for_name.finditer(segment):
            add_item(match.group("name"), f"${match.group('price')}")
        for match in compact_name_before_price.finditer(segment):
            add_item(match.group("name"), f"${match.group('price')}")
        for match in name_before_price.finditer(segment):
            add_item(match.group("name"), f"${match.group('price')}")
        for match in price_before_name.finditer(segment):
            name = match.group("name")
            name = re.split(r"\b(?:and|with|include|includes|plus|for|on|at)\b|[.!?]", name, maxsplit=1, flags=re.IGNORECASE)[0]
            if not name.strip():
                continue
            add_item(name, f"${match.group('price')}")
    return facts


def _generic_item_price(text: str) -> str:
    patterns = (
        r"\bprice\s+(?:any|every|each|all)\s+(?:item|items?)\s*\$?\s*(?P<price>\d+(?:\.\d{2})?)(?!\s*[%\-])\b",
        r"\b(?:any|every|each|all)\s+(?:item|items?)\s+(?:priced\s+)?(?:at|for|is|=|:)\s*\$\s*(?P<price>\d+(?:\.\d{2})?)(?!\s*[%\-])\b",
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


def extract_text_facts(
    fields: FlyerRequestFields,
    raw_request: str,
    *,
    message_id: str = "",
    profile_business_name: str = "",
    allow_text_identity: bool = True,
    cfg: "FlyerConfig | None" = None,
) -> list[FlyerLockedFact]:
    text = f"{raw_request or ''} {fields.notes or ''}"
    semantic_brief = build_semantic_flyer_brief(
        fields,
        raw_request,
        profile_business_name=profile_business_name,
        allow_text_identity=allow_text_identity,
        provider=build_hermes_semantic_brief_provider(),
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
    for index, offer in enumerate(semantic_brief.offers):
        item = _fact(f"offer:{index}", "Offer", offer.text, "customer_text", message_id=message_id)
        if item:
            facts.append(item)
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
    inferred_facts: list[FlyerLockedFact] = []
    if (
        cfg is not None
        and _creative_planner.is_active(cfg)
        and _creative_planner.request_matches_enabled_category(raw_request, cfg)
    ):
        inferred_facts = _creative_planner.materialize_inferred(
            _creative_planner.plan_creative_items(fields, raw_request),
            firewall=_creative_planner.load_firewall(),
        )
    famous_item_facts = (
        [] if inferred_facts
        else _requested_famous_item_facts(text, message_id=message_id)
    )
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
    # Slice 5b — flat-price reconciliation for planner items: when the customer stated
    # a FLAT price ("any item $8.99" — a hard customer_text fact) and the planner produced
    # inferred item names, pair that flat price with each inferred item. The NAME is
    # hermes_inferred (the planner's assumption); the PRICE is customer_text (the
    # customer's stated fact). Dormant default ⇒ inferred_facts == [] ⇒ no-op.
    inferred_price_facts: list[FlyerLockedFact] = []
    if inferred_facts and generic_price:
        for name_fact in inferred_facts:
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
                inferred_price_facts.append(price_fact)
    facts.extend(item_price_facts)
    facts.extend(item_name_facts)
    facts.extend(inferred_facts)  # superseding/grounded merge handled by merge_locked_facts
    facts.extend(inferred_price_facts)  # slice 5b: customer's flat price paired to planner items
    return merge_locked_facts(facts)


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
