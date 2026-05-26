"""Semantic visibility policy for Flyer Studio QA.

This module is a pure view over an existing FlyerProject. It does not mutate
project state or introduce persisted schema; it only tells QA which account
identity facts are hard requirements for the current brief.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from schemas import FlyerProject


_SAVED_BRAND_RE = re.compile(
    r"\b(?:saved|stored|registered|account)\s+(?:business\s+name|brand|logo)\b"
    r"|\buse\s+(?:the\s+)?(?:saved|stored|registered|account)\s+(?:business\s+name|brand|logo)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SemanticVisibilityPolicy:
    effective_business_name: str = ""
    campaign_title: str = ""
    brand_visibility_required_exact: bool = False
    brand_visibility_preferred: bool = True
    require_contact_anchor: bool = True
    require_location_anchor: bool = True


def _clean(value: str) -> str:
    return " ".join((value or "").strip().split())


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def fact_value(project: FlyerProject, fact_id: str) -> str:
    for fact in project.locked_facts:
        if fact.fact_id == fact_id and str(fact.value or "").strip():
            return _clean(str(fact.value))
    return ""


def _source_contract_requires_exact_brand(project: FlyerProject) -> bool:
    for extraction in project.reference_extractions or []:
        contract = getattr(extraction, "source_contract", None)
        if not contract:
            continue
        if getattr(contract, "preserve_layout", False) or getattr(contract, "preserve_unmentioned_text", False):
            return True
        if getattr(contract, "requested_replacements", None):
            return True
    return False


def _mentions_saved_brand(project: FlyerProject) -> bool:
    text = f"{project.raw_request or ''} {getattr(project.fields, 'notes', '') or ''}"
    return bool(_SAVED_BRAND_RE.search(text))


def semantic_visibility_policy(project: FlyerProject) -> SemanticVisibilityPolicy:
    business = fact_value(project, "business_name")
    campaign = (
        fact_value(project, "campaign_title")
        or fact_value(project, "headline")
        or _clean(project.fields.event_or_business_name or "")
    )
    brand_required = _mentions_saved_brand(project) or _source_contract_requires_exact_brand(project)
    return SemanticVisibilityPolicy(
        effective_business_name=business,
        campaign_title=campaign if _norm(campaign) != _norm(business) else "",
        brand_visibility_required_exact=brand_required,
        brand_visibility_preferred=True,
        require_contact_anchor=True,
        require_location_anchor=True,
    )


def visible_wrong_brand_blockers(project: FlyerProject, extracted_text: str) -> list[str]:
    """Conservative wrong-brand checks for explicit identity labels.

    This is intentionally not broad NER. It only blocks visible `Business:` /
    `Brand:` identity labels that name something other than the current
    effective business, plus source-contract forbidden text already handled by
    visual_qa's existing source-contract loop.
    """
    policy = semantic_visibility_policy(project)
    allowed = {_norm(policy.effective_business_name)}
    allowed.discard("")
    blockers: list[str] = []
    for match in re.finditer(
        r"\b(?:business|brand|company)\s*:\s*(?P<name>[A-Za-z][A-Za-z0-9 '&.-]{1,80})",
        extracted_text or "",
        flags=re.IGNORECASE,
    ):
        name = _clean(match.group("name"))
        name = re.split(r"[\n\r]| {2,}", name, maxsplit=1)[0].strip(" .,:;")
        normalized = _norm(name)
        if normalized and normalized not in allowed:
            blockers.append(f"visible wrong business/brand: {name}")
    return blockers
