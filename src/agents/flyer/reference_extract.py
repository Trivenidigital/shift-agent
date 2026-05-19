"""Reference media classification and extraction for Flyer Studio."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from schemas import FlyerAsset, FlyerLockedFact, FlyerReferenceExtraction, FlyerReferenceRole


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
    if re.search(r"\b(?:extract|take|use).{0,60}\b(?:items?|prices?|menu)\b|\b(?:sample|reference).{0,30}\b(?:flyer|menu)\b", text):
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


class SidecarReferenceExtractionProvider(ReferenceExtractionProvider):
    provider_name = "sidecar"

    def extract_text(self, asset: FlyerAsset, raw_request: str) -> tuple[str, str]:
        sidecar = Path(str(asset.path) + ".ocr.txt")
        if not sidecar.exists():
            return "", "provider_unavailable"
        return sidecar.read_text(encoding="utf-8"), "ok"


def _facts_from_text(text: str) -> list[FlyerLockedFact]:
    facts: list[FlyerLockedFact] = []
    pattern = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9 '&/-]{1,60}?)\s*(?:-|:)?\s*\$\s*(?P<price>\d+(?:\.\d{2})?)")
    for idx, match in enumerate(pattern.finditer(text or "")):
        name = " ".join(match.group("name").strip(" .,:;-").split())
        price = f"${match.group('price')}"
        if not name:
            continue
        facts.append(FlyerLockedFact(
            fact_id=f"item:{idx}:name",
            label="Item",
            value=name,
            source="reference_ocr",
            required=True,
            source_asset_id="A0001",
            source_sha256="",
        ))
        facts.append(FlyerLockedFact(
            fact_id=f"item:{idx}:price",
            label="Price",
            value=price,
            source="reference_ocr",
            required=True,
            source_asset_id="A0001",
            source_sha256="",
        ))
    return facts


def extract_reference(
    asset: FlyerAsset,
    *,
    raw_request: str,
    provider: ReferenceExtractionProvider | None = None,
) -> FlyerReferenceExtraction:
    role = classify_reference_role(raw_request, asset)
    provider = provider or NoopReferenceExtractionProvider()
    if role == "unsupported" or not (asset.mime_type or "").lower().startswith("image/"):
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
    if status != "ok":
        return FlyerReferenceExtraction(
            asset_id=asset.asset_id,
            role=role,
            provider=provider.provider_name,
            status="provider_unavailable",
            detail="reference OCR/vision provider unavailable",
            extracted_at=datetime.now(timezone.utc),
        )
    facts = _facts_from_text(text)
    return FlyerReferenceExtraction(
        asset_id=asset.asset_id,
        role=role,
        provider=provider.provider_name,
        status="ok" if facts else "low_confidence",
        extracted_facts=facts,
        detail="" if facts else "no high-confidence item/price facts extracted",
        extracted_at=datetime.now(timezone.utc),
    )
