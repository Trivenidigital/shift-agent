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

from schemas import FlyerAsset, FlyerLockedFact, FlyerReferenceExtraction, FlyerReferenceRole

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
        r"|\b(?:from|using)\s+(?:this\s+)?(?:attached|uploaded)\s+(?:menu|price\s*list)\b",
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
    facts: list[FlyerLockedFact] = []
    pattern = re.compile(
        r"(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,60}?)\s*(?:-|:)?\s*\$\s*(?P<price>\d+(?:\.\d{2})?)\b(?P<tail>[^\n\r,;]*)",
        flags=re.IGNORECASE,
    )
    promo_tail = re.compile(r"^\s*(?:off|discount|save|coupon|credit|cashback|%|\bpercent\b)", flags=re.IGNORECASE)
    promo_name = re.compile(r"^(?:save|coupon|discount|offer|deal|special|weekend special|cashback|credit)\b", flags=re.IGNORECASE)
    for line in (text or "").splitlines():
        for match in pattern.finditer(line):
            if promo_tail.search(match.group("tail") or ""):
                continue
            name = " ".join(match.group("name").strip(" .,:;-").split())
            name = re.sub(r"^(?:and|with|include|includes)\s+", "", name, flags=re.IGNORECASE).strip()
            if not name or promo_name.search(name):
                continue
            idx = len(facts) // 2
            price = f"${match.group('price')}"
            facts.append(FlyerLockedFact(
                fact_id=f"item:{idx}:name",
                label="Item",
                value=name,
                source=source,
                required=True,
                source_asset_id=asset.asset_id,
                source_sha256=asset.sha256,
            ))
            facts.append(FlyerLockedFact(
                fact_id=f"item:{idx}:price",
                label="Price",
                value=price,
                source=source,
                required=True,
                source_asset_id=asset.asset_id,
                source_sha256=asset.sha256,
            ))
    return facts


def build_reference_extraction_provider() -> ReferenceExtractionProvider:
    if os.environ.get("FLYER_REFERENCE_ALLOW_SIDECAR") == "1":
        return SidecarReferenceExtractionProvider()
    return OpenRouterVisionReferenceExtractionProvider()


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
    return FlyerReferenceExtraction(
        asset_id=asset.asset_id,
        role=role,
        provider=provider.provider_name,
        status="ok" if status == "ok" and facts else "low_confidence",
        extracted_facts=facts,
        detail="" if status == "ok" and facts else "no high-confidence item/price facts extracted",
        extracted_at=datetime.now(timezone.utc),
    )
