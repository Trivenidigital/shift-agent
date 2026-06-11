"""Reference media classification and extraction for Flyer Studio."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Callable
import urllib.error
import urllib.request

from schemas import (
    FlyerAsset,
    FlyerLockedFact,
    FlyerReferenceExtraction,
    FlyerReferenceRole,
    FlyerSourceContract,
    FlyerSourceContractSection,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT_SEC = 60
REFERENCE_VISION_MODEL = os.environ.get("FLYER_REFERENCE_VISION_MODEL") or os.environ.get("VISION_MODEL") or "openai/gpt-4o-mini"

REFERENCE_EXTRACTION_PROMPT = """Read this uploaded reference/menu flyer image for Flyer Studio.

Return STRICT JSON only:
{
  "visible_text": "all readable menu/reference text, preserving item names, prices, phone numbers, addresses, badges, and headings",
  "confidence": "high" | "medium" | "low",
  "warnings": ["short factual notes about unreadable, cropped, or ambiguous text"]
}

Rules:
- Do not invent items or prices.
- Preserve prices exactly as visible.
- If no readable menu/reference text exists, use an empty visible_text string and confidence "low".
- Return only JSON. No markdown.
"""

SOURCE_CONTRACT_PROMPT = """Read this uploaded source flyer for an SMB Flyer Studio
exact-edit request. Extract the visible structure and the customer's stated changes.

Return STRICT JSON only:
{
  "source_business_names": ["..."],
  "target_business_name": "...",
  "required_headings": ["..."],
  "required_text": ["..."],
  "sections": [{"heading": "...", "items": ["...", "..."]}],
  "requested_replacements": {"OLD": "NEW", ...},
  "forbidden_substrings": [],
  "preserve_layout": true,
  "preserve_unmentioned_text": true,
  "confidence": "high" | "medium" | "low",
  "notes": "..."
}

Rules:
- Do not invent items, prices, or business names.
- Preserve item names exactly (case + spelling).
- "preserve_unmentioned_text" = true when the customer text contains any of:
  "do not change anything else", "only change", "same layout", "preserve", "keep the rest".
- "preserve_layout" = true when the customer text references layout, design, or look preservation.
- "forbidden_substrings" stays empty here; it is populated downstream from replacements.
- "requested_replacements" maps explicit "replace X with Y" from the customer text only.
- Return only JSON. No markdown.
"""


_REPLACEMENT_TRAILING_ROLE_NOUNS = re.compile(
    r"\s+\b(?:branding|brand|name|info|information|details|address|phone)\b\s*$",
    flags=re.IGNORECASE,
)

_PRESERVE_UNMENTIONED_RE = re.compile(
    r"\b(?:do\s+not\s+change\s+anything\s+else|only\s+change|same\s+layout|preserve|keep\s+the\s+rest)\b",
    flags=re.IGNORECASE,
)
_PRESERVE_LAYOUT_RE = re.compile(
    r"\b(?:layout|design|look|same\s+layout|same\s+design|preserve\s+the\s+layout)\b",
    flags=re.IGNORECASE,
)


def extract_requested_replacements_from_text(raw_request: str) -> dict[str, str]:
    """Deterministic 'replace X with Y' parser for customer text.

    Strips trailing role nouns from the new capture so
    `replace Triveni Express with Lakshmi's Kitchen branding` resolves to
    `{"Triveni Express": "Lakshmi's Kitchen"}` — not with a stray `branding`.
    """
    replacements: dict[str, str] = {}
    for match in re.finditer(
        r"\breplace\s+(?P<old>.+?)\s+(?:with|to)\s+(?P<new>.+?)(?=\.|\n|\d+\.\s|$)",
        raw_request or "",
        flags=re.IGNORECASE,
    ):
        old = " ".join(match.group("old").strip(" .,:;").split())
        new = " ".join(match.group("new").strip(" .,:;").split())
        new = _REPLACEMENT_TRAILING_ROLE_NOUNS.sub("", new).strip()
        if old and new and len(old) <= 80 and len(new) <= 80:
            replacements[old] = new
    return replacements


def _confidence_to_float(value: str | float | int) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    text = str(value or "").strip().lower()
    return {"high": 0.9, "medium": 0.6, "low": 0.3}.get(text, 0.0)


def classify_reference_role(raw_request: str, asset: FlyerAsset) -> FlyerReferenceRole:
    text = " ".join((raw_request or "").lower().split())
    mime = (asset.mime_type or "").lower()
    if "logo" in text and not re.search(r"\b(?:extract|items?|prices?|remove|change|edit|date|time)\b", text):
        return "logo"
    if re.search(r"\b(?:remove|delete|change|replace|fix|correct|edit|update)\b", text) and re.search(
        r"\b(?:this|attached|uploaded|source|existing).{0,30}\b(?:flyer|poster|image|artwork)\b|\b(?:date|time|extra|text)\b",
        text,
    ):
        return "source_edit_template"
    if re.search(
        r"\b(?:extract|take|use).{0,60}\b(?:items?|prices?|menu)\b"
        r"|\b(?:sample|reference|attached|uploaded|this).{0,30}\b(?:flyer|menu|price\s*list)\b"
        r"|\b(?:from|using)\s+(?:this\s+)?(?:attached|uploaded)\s+(?:menu|price\s*list)\b"
        r"|\b(?:menu|price\s*list)\s+(?:attached|uploaded)\b",
        text,
    ):
        return "menu_reference"
    if not mime.startswith("image/"):
        return "unsupported"
    if "reference" in text or "sample" in text:
        return "old_flyer_reference"
    return "inspiration"


class ReferenceExtractionProvider:
    provider_name = "base"

    def extract_text(self, asset: FlyerAsset, raw_request: str) -> tuple[str, str]:
        raise NotImplementedError


class NoopReferenceExtractionProvider(ReferenceExtractionProvider):
    provider_name = "unavailable"

    def extract_text(self, asset: FlyerAsset, raw_request: str) -> tuple[str, str]:
        return "", "provider_unavailable"


def _read_key_from_env_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if key.strip() == "OPENROUTER_API_KEY":
                return raw.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _openrouter_key() -> str:
    return (
        os.environ.get("OPENROUTER_API_KEY", "").strip()
        or _read_key_from_env_file("/root/.hermes/.env")
        or _read_key_from_env_file("/opt/shift-agent/.env")
    )


def _media_data_url(path: Path, mime_type: str) -> str:
    raw = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{raw}"


class OpenRouterVisionReferenceExtractionProvider(ReferenceExtractionProvider):
    provider_name = "openrouter_vision"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        call_json: Callable[[dict], dict] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model or REFERENCE_VISION_MODEL
        self._call_json = call_json

    def extract_text(self, asset: FlyerAsset, raw_request: str) -> tuple[str, str]:
        key = (self.api_key or _openrouter_key()).strip()
        if not key or "PLACEHOLDER" in key:
            return "", "provider_unavailable"
        path = Path(asset.path)
        if not path.exists() or not path.is_file():
            return "", "provider_unavailable"
        mime = (asset.mime_type or mimetypes.guess_type(str(path))[0] or "").lower()
        if not mime.startswith("image/"):
            return "", "unsupported"
        prompt = f"{REFERENCE_EXTRACTION_PROMPT}\n\nCustomer request text:\n{raw_request or ''}"
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _media_data_url(path, mime)}},
                ],
            }],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }
        try:
            parsed = self._call_json(payload) if self._call_json else self._call_openrouter(payload, key)
        except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError, urllib.error.URLError, urllib.error.HTTPError):
            return "", "provider_unavailable"
        text = str(parsed.get("visible_text") or parsed.get("extracted_text") or "").strip()
        confidence = str(parsed.get("confidence") or "low").lower()
        if not text:
            return "", "low_confidence"
        return text, "ok" if confidence in {"high", "medium"} else "low_confidence"

    @staticmethod
    def _call_openrouter(payload: dict, api_key: str) -> dict:
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=OPENROUTER_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
        doc = json.loads(body)
        content = doc["choices"][0]["message"]["content"]
        return json.loads(content)


class SidecarReferenceExtractionProvider(ReferenceExtractionProvider):
    provider_name = "sidecar"

    def extract_text(self, asset: FlyerAsset, raw_request: str) -> tuple[str, str]:
        sidecar = Path(str(asset.path) + ".ocr.txt")
        if not sidecar.exists():
            return "", "provider_unavailable"
        return sidecar.read_text(encoding="utf-8"), "ok"


def _facts_from_text(text: str, *, asset: FlyerAsset, source: str) -> list[FlyerLockedFact]:
    items: list[dict[str, str]] = []
    pricing_facts: list[FlyerLockedFact] = []
    campaign_title = ""
    seen_names: set[str] = set()
    seen_pricing: set[str] = set()
    pattern = re.compile(
        r"(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,60}?)\s*(?:-|:)?\s*\$\s*(?P<price>\d+(?:\.\d{2})?)\b(?P<tail>[^\n\r,;]*)",
        flags=re.IGNORECASE,
    )
    bullet_item = re.compile(
        r"^\s*(?:[-*]|\u2022|\u2605|\u2606|\u25cf|\u25aa|\u2023|\u27a4|>>?|[»›]|\d+[.)])\s+(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,60})\s*$",
        flags=re.IGNORECASE,
    )
    promo_tail = re.compile(r"^\s*(?:off|discount|save|coupon|credit|cashback|%|\bpercent\b)", flags=re.IGNORECASE)
    promo_name = re.compile(r"^(?:save|coupon|discount|offer|deal|special|weekend special|cashback|credit)\b", flags=re.IGNORECASE)
    shared_price_name = re.compile(r"^(?:any|all|every|each)\b", flags=re.IGNORECASE)
    price_only = re.compile(r"^\s*\$\s*(\d+(?:\.\d{1,2})?)\s*$")
    campaign_heading = re.compile(
        r"\b(?:specials?|sale|offer|menu|night|snacks?|breakfast|lunch|dinner|combo|thali|buffet)\b",
        flags=re.IGNORECASE,
    )

    def clean(value: str) -> str:
        return " ".join((value or "").strip(" .,:;-").split())

    def add_item_name(name: str, price: str = "") -> None:
        name = clean(name)
        name = re.sub(r"^(?:and|with|include|includes)\s+", "", name, flags=re.IGNORECASE).strip()
        if not name or promo_name.search(name):
            return
        key = name.lower()
        if key in seen_names:
            if price:
                for item in items:
                    if item["name"].lower() == key and not item.get("price"):
                        item["price"] = price
                        break
            return
        seen_names.add(key)
        item = {"name": name}
        if price:
            item["price"] = price
        items.append(item)

    def add_pricing(value: str) -> None:
        value = clean(value)
        if not value:
            return
        key = value.lower()
        if key in seen_pricing:
            return
        seen_pricing.add(key)
        fact_id = "pricing_structure" if not pricing_facts else f"offer:{len(pricing_facts) - 1}"
        label = "Pricing" if fact_id == "pricing_structure" else "Offer"
        pricing_facts.append(FlyerLockedFact(
            fact_id=fact_id,
            label=label,
            value=value,
            source=source,
            required=True,
            source_asset_id=asset.asset_id,
            source_sha256=asset.sha256,
        ))

    def maybe_campaign_title(line: str) -> None:
        nonlocal campaign_title
        if campaign_title:
            return
        value = clean(line)
        if not value or "$" in value or len(value) > 80:
            return
        if bullet_item.match(line) or shared_price_name.search(value):
            return
        if campaign_heading.search(value):
            campaign_title = value

    pending_shared_price_name = ""
    for line in (text or "").splitlines():
        clean_line = clean(line)
        if pending_shared_price_name:
            price_match = price_only.match(line)
            if price_match:
                add_pricing(f"{pending_shared_price_name} ${price_match.group(1)}")
                pending_shared_price_name = ""
                continue
            pending_shared_price_name = ""
        maybe_campaign_title(line)
        bullet_match = bullet_item.match(line)
        if bullet_match and "$" not in line:
            add_item_name(bullet_match.group("name"))
            continue
        if clean_line and shared_price_name.search(clean_line) and "$" not in clean_line:
            pending_shared_price_name = clean_line
            continue
        for match in pattern.finditer(line):
            if promo_tail.search(match.group("tail") or ""):
                continue
            name = clean(match.group("name"))
            if shared_price_name.search(name):
                add_pricing(line)
                continue
            price = f"${match.group('price')}"
            add_item_name(name, price)
    facts: list[FlyerLockedFact] = []
    if campaign_title:
        facts.append(FlyerLockedFact(
            fact_id="campaign_title",
            label="Campaign",
            value=campaign_title,
            source=source,
            required=True,
            source_asset_id=asset.asset_id,
            source_sha256=asset.sha256,
        ))
    for idx, item in enumerate(items):
        facts.append(FlyerLockedFact(
            fact_id=f"item:{idx}:name",
            label="Item",
            value=item["name"],
            source=source,
            required=True,
            source_asset_id=asset.asset_id,
            source_sha256=asset.sha256,
        ))
        if item.get("price"):
            facts.append(FlyerLockedFact(
                fact_id=f"item:{idx}:price",
                label="Price",
                value=item["price"],
                source=source,
                required=True,
                source_asset_id=asset.asset_id,
                source_sha256=asset.sha256,
            ))
    facts.extend(pricing_facts)
    return facts


def build_reference_extraction_provider() -> ReferenceExtractionProvider:
    if os.environ.get("FLYER_REFERENCE_ALLOW_SIDECAR") == "1":
        return SidecarReferenceExtractionProvider()
    return OpenRouterVisionReferenceExtractionProvider()


def _parse_source_contract_json(payload: str, *, raw_request: str) -> FlyerSourceContract | None:
    """Permissively parse vision JSON into a strict FlyerSourceContract.

    The vision model output is parsed with json.loads (tolerant); then we
    project the fields into the strict schema. Unknown keys are silently
    dropped before validation so extra="forbid" doesn't reject otherwise-
    usable contracts.
    """
    if not payload:
        return None
    try:
        raw = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    sections: list[FlyerSourceContractSection] = []
    for section in (raw.get("sections") or []):
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()[:160]
        raw_items = section.get("items") or []
        items = [str(it).strip()[:120] for it in raw_items if str(it or "").strip()][:50]
        sections.append(FlyerSourceContractSection(heading=heading, items=items))
    requested = {}
    for key, value in (raw.get("requested_replacements") or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        k = key.strip()[:80]
        v = value.strip()[:80]
        if k and v:
            requested[k] = v
    # Merge customer-text deterministic parser (authoritative on conflict).
    requested.update(extract_requested_replacements_from_text(raw_request))
    customer_text = (raw_request or "").lower()
    preserve_layout = bool(raw.get("preserve_layout")) or bool(_PRESERVE_LAYOUT_RE.search(customer_text))
    preserve_unmentioned = bool(raw.get("preserve_unmentioned_text")) or bool(_PRESERVE_UNMENTIONED_RE.search(customer_text))
    try:
        contract = FlyerSourceContract(
            source_business_names=[str(n).strip()[:120] for n in (raw.get("source_business_names") or []) if str(n).strip()][:10],
            target_business_name=str(raw.get("target_business_name") or "").strip()[:160],
            required_headings=[str(h).strip()[:120] for h in (raw.get("required_headings") or []) if str(h).strip()][:20],
            required_text=[str(t).strip()[:160] for t in (raw.get("required_text") or []) if str(t).strip()][:100],
            sections=sections[:20],
            requested_replacements=dict(list(requested.items())[:50]),
            forbidden_substrings=[],
            preserve_layout=preserve_layout,
            preserve_unmentioned_text=preserve_unmentioned,
            confidence=_confidence_to_float(raw.get("confidence", "")),
            notes=str(raw.get("notes") or "")[:1000],
        )
    except Exception:
        return None
    return contract


def _extract_source_contract(
    asset: FlyerAsset,
    *,
    raw_request: str,
    provider: ReferenceExtractionProvider,
    role: FlyerReferenceRole,
    now: datetime,
) -> FlyerReferenceExtraction:
    """Vision + customer-text -> FlyerSourceContract for source_edit_template role.

    Provider unavailable -> status=provider_unavailable, no contract attached.
    JSON parse / validation failure -> low_confidence with merged text-only
    replacements still attached so the manual queue can show them.
    """
    raw_text, status = provider.extract_text_with_prompt(asset, raw_request, SOURCE_CONTRACT_PROMPT) \
        if hasattr(provider, "extract_text_with_prompt") \
        else provider.extract_text(asset, raw_request)

    text_replacements = extract_requested_replacements_from_text(raw_request)

    if status == "unsupported":
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role=role,
            provider=provider.provider_name,
            status="unsupported",
            detail=f"unsupported reference media type: {asset.mime_type}",
            extracted_at=now,
        )
    if status == "provider_unavailable":
        contract = None
        if text_replacements:
            customer_text = (raw_request or "").lower()
            contract = FlyerSourceContract(
                requested_replacements=text_replacements,
                preserve_layout=bool(_PRESERVE_LAYOUT_RE.search(customer_text)),
                preserve_unmentioned_text=bool(_PRESERVE_UNMENTIONED_RE.search(customer_text)),
                confidence=0.3,
            )
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role=role,
            provider=provider.provider_name,
            status="provider_unavailable",
            detail="source-contract vision provider unavailable",
            source_contract=contract,
            extracted_at=now,
        )
    contract = _parse_source_contract_json(raw_text, raw_request=raw_request)
    if contract is None:
        customer_text = (raw_request or "").lower()
        contract = FlyerSourceContract(
            requested_replacements=text_replacements,
            preserve_layout=bool(_PRESERVE_LAYOUT_RE.search(customer_text)),
            preserve_unmentioned_text=bool(_PRESERVE_UNMENTIONED_RE.search(customer_text)),
            confidence=0.3,
        )
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role=role,
            provider=provider.provider_name,
            status="low_confidence",
            detail="source-contract vision JSON parse failure",
            source_contract=contract,
            extracted_at=now,
        )
    final_status = "ok" if contract.confidence >= 0.5 and (contract.required_headings or contract.sections or contract.requested_replacements) else "low_confidence"
    return FlyerReferenceExtraction(
        asset_id=asset.asset_id,
        role=role,
        provider=provider.provider_name,
        status=final_status,
        source_contract=contract,
        detail="" if final_status == "ok" else "source contract has low confidence or insufficient structure",
        extracted_at=now,
    )


def extract_reference(
    asset: FlyerAsset,
    *,
    raw_request: str,
    provider: ReferenceExtractionProvider | None = None,
) -> FlyerReferenceExtraction:
    role = classify_reference_role(raw_request, asset)
    provider = provider or NoopReferenceExtractionProvider()
    mime = (asset.mime_type or "").lower()
    if role == "unsupported" or not mime.startswith("image/"):
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role=role,
            provider=provider.provider_name,
            status="unsupported",
            detail=f"unsupported reference media type: {asset.mime_type}",
            extracted_at=datetime.now(timezone.utc),
        )
    if role == "source_edit_template":
        return _extract_source_contract(
            asset,
            raw_request=raw_request,
            provider=provider,
            role=role,
            now=datetime.now(timezone.utc),
        )
    if role not in {"menu_reference", "old_flyer_reference"}:
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role=role,
            provider=provider.provider_name,
            status="not_run",
            detail="reference extraction not required for this role",
            extracted_at=datetime.now(timezone.utc),
        )
    text, status = provider.extract_text(asset, raw_request)
    if status == "unsupported":
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role=role,
            provider=provider.provider_name,
            status="unsupported",
            detail=f"unsupported reference media type: {asset.mime_type}",
            extracted_at=datetime.now(timezone.utc),
        )
    if status == "provider_unavailable":
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role=role,
            provider=provider.provider_name,
            status="provider_unavailable",
            detail="reference OCR/vision provider unavailable",
            extracted_at=datetime.now(timezone.utc),
        )
    if status == "low_confidence":
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role=role,
            provider=provider.provider_name,
            status="low_confidence",
            extracted_facts=[],
            detail="reference OCR/vision confidence too low",
            extracted_at=datetime.now(timezone.utc),
        )
    source = "reference_ocr" if provider.provider_name == "sidecar" else "reference_vision"
    facts = _facts_from_text(text, asset=asset, source=source)
    has_pricing_fact = any(
        fact.fact_id == "pricing_structure"
        or fact.fact_id.startswith("offer:")
        or (fact.fact_id.startswith("item:") and fact.fact_id.endswith(":price"))
        for fact in facts
    )
    if role == "menu_reference" and not has_pricing_fact:
        facts = []
    has_menu_fact = any(
        fact.fact_id == "pricing_structure"
        or fact.fact_id.startswith("offer:")
        or fact.fact_id.startswith("item:")
        for fact in facts
    )
    ok = status == "ok" and bool(facts) and has_menu_fact
    return FlyerReferenceExtraction(
        asset_id=asset.asset_id,
        role=role,
        provider=provider.provider_name,
        status="ok" if ok else "low_confidence",
        extracted_facts=facts,
        detail="" if ok else "no high-confidence item/price facts extracted",
        extracted_at=datetime.now(timezone.utc),
    )
