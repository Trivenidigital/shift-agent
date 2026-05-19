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
