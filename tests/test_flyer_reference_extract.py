from __future__ import annotations

from datetime import datetime, timezone

from schemas import FlyerAsset


def _asset(tmp_path, name="sample.png", mime="image/png"):
    path = tmp_path / name
    path.write_bytes(b"fake image")
    return FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path=str(path),
        mime_type=mime,
        sha256="a" * 64,
        original_message_id="m-ref",
        received_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
    )


def test_classifies_logo_menu_reference_and_source_edit(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import classify_reference_role

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = _asset(tmp_path)

    assert classify_reference_role("Use this as our logo", asset) == "logo"
    assert classify_reference_role("Extract item names and prices from attached sample flyer", asset) == "menu_reference"
    assert classify_reference_role("Create a flyer from this attached menu", asset) == "menu_reference"
    assert classify_reference_role("Remove extra 08:00 from this uploaded flyer", asset) == "source_edit_template"


def test_noop_provider_fails_closed_for_extraction_required(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import NoopReferenceExtractionProvider, extract_reference

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    result = extract_reference(
        _asset(tmp_path),
        raw_request="Extract item names and prices from attached sample flyer",
        provider=NoopReferenceExtractionProvider(),
    )

    assert result.status == "provider_unavailable"
    assert result.role == "menu_reference"
    assert not result.extracted_facts


def test_sidecar_provider_extracts_items_and_prices(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import SidecarReferenceExtractionProvider, extract_reference

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = _asset(tmp_path)
    (tmp_path / "sample.png.ocr.txt").write_text("Idly $7\nDosa $8\nSamosa $3", encoding="utf-8")

    result = extract_reference(
        asset,
        raw_request="Extract item names and prices from attached sample flyer",
        provider=SidecarReferenceExtractionProvider(),
    )

    values = {fact.value for fact in result.extracted_facts}
    assert result.status == "ok"
    assert {"Idly", "$7", "Dosa", "$8"}.issubset(values)


def test_low_confidence_reference_does_not_return_facts(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import ReferenceExtractionProvider, extract_reference

    class LowConfidenceProvider(ReferenceExtractionProvider):
        provider_name = "test_low"

        def extract_text(self, _asset, _raw_request):
            return "Idly $7\nDosa $8", "low_confidence"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    result = extract_reference(
        _asset(tmp_path),
        raw_request="Extract item names and prices from attached sample flyer",
        provider=LowConfidenceProvider(),
    )

    assert result.status == "low_confidence"
    assert result.extracted_facts == []


def test_reference_extraction_does_not_treat_discount_copy_as_menu_item(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import ReferenceExtractionProvider, extract_reference

    class PromoProvider(ReferenceExtractionProvider):
        provider_name = "test_promo"

        def extract_text(self, _asset, _raw_request):
            return "Weekend Special $5 off\nSave $5 on Dosa\nCoupon $4\nIdly $7\nDosa $8", "ok"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    result = extract_reference(
        _asset(tmp_path),
        raw_request="Extract item names and prices from attached sample flyer",
        provider=PromoProvider(),
    )

    values = {fact.value for fact in result.extracted_facts}
    assert "Weekend Special" not in values
    assert "Save" not in values
    assert "Coupon" not in values
    assert {"Idly", "$7", "Dosa", "$8"}.issubset(values)


def test_openrouter_provider_extracts_menu_text_from_image(monkeypatch, tmp_path):
    from agents.flyer.reference_extract import OpenRouterVisionReferenceExtractionProvider

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    asset = _asset(tmp_path)

    def fake_call(_payload):
        return {
            "visible_text": "Lakshmis Kitchen\nIdly $7.00\nDosa $8.00\nCall 904-555-0123",
            "confidence": "high",
            "warnings": [],
        }

    provider = OpenRouterVisionReferenceExtractionProvider(call_json=fake_call)

    text, status = provider.extract_text(asset, "Extract item names and prices from attached sample flyer")

    assert status == "ok"
    assert "Idly $7.00" in text
    assert "Dosa $8.00" in text


def test_unsupported_pdf_queues_manual_not_extraction(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import NoopReferenceExtractionProvider, extract_reference

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    result = extract_reference(
        _asset(tmp_path, name="sample.pdf", mime="application/pdf"),
        raw_request="Change date on this uploaded flyer",
        provider=NoopReferenceExtractionProvider(),
    )

    assert result.status == "unsupported"
    assert result.role == "source_edit_template"
