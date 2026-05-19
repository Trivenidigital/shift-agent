"""Locked customer-visible facts for Flyer Studio projects."""
from __future__ import annotations

import re
from typing import Iterable

from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields


ALLOWED_NEW_PROJECT_FACT_SOURCES = {
    "customer_text",
    "customer_profile",
    "reference_ocr",
    "reference_vision",
    "uploaded_asset",
    "operator",
    "system",
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


def _item_price_facts(text: str, *, message_id: str = "") -> list[FlyerLockedFact]:
    facts: list[FlyerLockedFact] = []
    pattern = re.compile(
        r"(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,60}?)\s*(?:-|:)?\s*\$\s*(?P<price>\d+(?:\.\d{2})?)",
        flags=re.IGNORECASE,
    )
    seen: set[str] = set()
    for idx, match in enumerate(pattern.finditer(text or "")):
        name = _clean(match.group("name"))
        name = re.sub(
            r"^(?:create|make|generate|design)\s+(?:a\s+)?(?:menu\s+)?(?:flyer|flier|poster|banner)\s+(?:with|for)?\s*",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        name = re.sub(r"^(?:and|with|include|includes)\s+", "", name, flags=re.IGNORECASE)
        price = f"${match.group('price')}"
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        name_fact = _fact(f"item:{len(seen)-1}:name", "Item", name, "customer_text", message_id=message_id)
        price_fact = _fact(f"item:{len(seen)-1}:price", "Price", price, "customer_text", message_id=message_id)
        if name_fact:
            facts.append(name_fact)
        if price_fact:
            facts.append(price_fact)
    return facts


def extract_text_facts(fields: FlyerRequestFields, raw_request: str, *, message_id: str = "") -> list[FlyerLockedFact]:
    text = f"{raw_request or ''} {fields.notes or ''}"
    facts: list[FlyerLockedFact] = []
    for item in [
        _fact("business_name", "Business", fields.event_or_business_name or "", "customer_text", message_id=message_id),
        _fact("headline", "Headline", _headline(text), "customer_text", message_id=message_id),
        _fact("tagline", "Tagline", _tagline(text), "customer_text", message_id=message_id),
        _fact("location", "Location", fields.venue_or_location or "", "customer_profile", required=False),
        _fact("contact_phone", "Contact", fields.contact_info or "", "customer_profile"),
    ]:
        if item:
            facts.append(item)
    facts.extend(_item_price_facts(text, message_id=message_id))
    return merge_locked_facts(facts)


def merge_locked_facts(*fact_lists: Iterable[FlyerLockedFact]) -> list[FlyerLockedFact]:
    priority = {
        "customer_text": 0,
        "operator": 1,
        "customer_profile": 2,
        "reference_ocr": 3,
        "reference_vision": 4,
        "uploaded_asset": 5,
        "system": 6,
    }
    merged: dict[str, FlyerLockedFact] = {}
    for facts in fact_lists:
        for fact in facts:
            current = merged.get(fact.fact_id)
            if current is None or priority.get(fact.source, 99) < priority.get(current.source, 99):
                merged[fact.fact_id] = fact
    return list(merged.values())


def facts_by_id(project: FlyerProject | object) -> dict[str, FlyerLockedFact]:
    return {fact.fact_id: fact for fact in getattr(project, "locked_facts", [])}


def required_fact_blockers(project: FlyerProject) -> list[str]:
    return [f"missing required fact: {fact.fact_id}" for fact in project.locked_facts if fact.required and not fact.value.strip()]


def context_isolation_blockers(project: FlyerProject) -> list[str]:
    blockers: list[str] = []
    for fact in project.locked_facts:
        if fact.source not in ALLOWED_NEW_PROJECT_FACT_SOURCES:
            blockers.append(f"locked fact {fact.fact_id} has invalid source {fact.source}")
        if fact.source_project_id:
            blockers.append(f"locked fact {fact.fact_id} carries stale project provenance {fact.source_project_id}")
    return blockers
