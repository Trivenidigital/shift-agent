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
_SAVED_BRAND_TOKEN_RE = re.compile(
    r"\b(?:saved\s+(?:logo|business\s+name|address|phone)|brand\s+asset|use\s+(?:the\s+)?logo)\b",
    re.IGNORECASE,
)
_ORG_SUFFIX_RE = re.compile(
    r"\b(?:restaurant|kitchen|cafe|bakery|market|grocery|supermarket|bazaar|bazar|studio|salon|express|catering)\b",
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


def _norm_contains(haystack: str, needle: str) -> bool:
    hay = _norm(haystack)
    ndl = _norm(needle)
    if not hay or not ndl:
        return False
    return re.search(r"\b" + re.escape(ndl) + r"\b", hay) is not None


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
    return bool(_SAVED_BRAND_RE.search(text) or _SAVED_BRAND_TOKEN_RE.search(text))


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
    """Conservative wrong-brand checks for visible identity claims.

    This is intentionally not broad NER. It blocks explicit identity labels,
    known source-contract business names, and highly-shaped organization
    masthead lines. Campaign titles are not treated as account identity.
    """
    policy = semantic_visibility_policy(project)
    allowed = {_norm(policy.effective_business_name)}
    for extraction in project.reference_extractions or []:
        contract = getattr(extraction, "source_contract", None)
        if contract:
            allowed.add(_norm(getattr(contract, "target_business_name", "") or ""))
    allowed.discard("")
    blockers: list[str] = []

    def allowed_identity_visible(value: str) -> bool:
        return any(_norm_contains(value, allowed_name) for allowed_name in allowed)

    def append_once(blocker: str) -> None:
        if blocker not in blockers:
            blockers.append(blocker)

    for match in re.finditer(
        r"\b(?:business|brand|company)\s*:\s*(?P<name>[A-Za-z][A-Za-z0-9 '&.-]{1,80})",
        extracted_text or "",
        flags=re.IGNORECASE,
    ):
        name = _clean(match.group("name"))
        name = re.split(r"[\n\r]| {2,}", name, maxsplit=1)[0].strip(" .,:;")
        normalized = _norm(name)
        if normalized and normalized not in allowed:
            append_once(f"visible wrong business/brand: {name}")

    for extraction in project.reference_extractions or []:
        contract = getattr(extraction, "source_contract", None)
        if not contract:
            continue
        for name in getattr(contract, "source_business_names", []) or []:
            source_name = _clean(str(name))
            if not source_name or allowed_identity_visible(source_name):
                continue
            if _norm_contains(extracted_text, source_name):
                append_once(f"visible wrong business/brand: {source_name}")

    for line in (extracted_text or "").splitlines():
        candidate = _clean(line).strip(" .,:;")
        if not candidate or len(candidate) > 80:
            continue
        letters = [ch for ch in candidate if ch.isalpha()]
        if len(letters) < 4:
            continue
        uppercase_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
        if uppercase_ratio < 0.8 or not _ORG_SUFFIX_RE.search(candidate):
            continue
        if allowed_identity_visible(candidate):
            continue
        append_once(f"visible wrong business/brand: {candidate.title()}")
    return blockers
