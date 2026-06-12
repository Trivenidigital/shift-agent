from __future__ import annotations

import json
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
    assert classify_reference_role("Create flyer. Menu attached.", asset) == "menu_reference"
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


def test_reference_extract_captures_bulleted_items_and_shared_combo_price(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import ReferenceExtractionProvider, extract_reference

    class SnackReferenceProvider(ReferenceExtractionProvider):
        provider_name = "test_vision"

        def extract_text(self, _asset, _raw_request):
            return (
                "Tuesday Night Snack Specials\n"
                "- Onion Pakoda\n"
                "- Mirchi Bajji\n"
                "- Cut Mirchi\n"
                "- Punugulu\n"
                "- Samosa\n"
                "ANY 2 SNACKS $9.99"
            ), "ok"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    result = extract_reference(
        _asset(tmp_path),
        raw_request="Tuesday Night Snack Specials. Use as reference.",
        provider=SnackReferenceProvider(),
    )

    by_id = {fact.fact_id: fact for fact in result.extracted_facts}
    assert result.status == "ok", result.detail
    assert by_id["campaign_title"].value == "Tuesday Night Snack Specials"
    assert [by_id[f"item:{idx}:name"].value for idx in range(5)] == [
        "Onion Pakoda",
        "Mirchi Bajji",
        "Cut Mirchi",
        "Punugulu",
        "Samosa",
    ]
    assert by_id["pricing_structure"].value == "ANY 2 SNACKS $9.99"
    assert not any(fact.fact_id.startswith("item:") and fact.fact_id.endswith(":price") for fact in result.extracted_facts)


def test_reference_extract_captures_real_star_bullets_and_split_combo_price(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import ReferenceExtractionProvider, extract_reference

    class RealSnackReferenceProvider(ReferenceExtractionProvider):
        provider_name = "test_vision"

        def extract_text(self, _asset, _raw_request):
            return (
                "Tuesday Night Specials\n"
                "★ Punugulu\n"
                "★ Egg Bonda\n"
                "★ Mysore Bonda\n"
                "★ Mysore Bajji\n"
                "★ Masala Vada\n"
                "★ Mirapakaya Bajji\n"
                "★ Onion Samosa\n"
                "★ Onion Pakoda\n"
                "★ Veg Noodles\n"
                "★ Egg Noodles\n"
                "ANY 2 SNACKS\n"
                "$9.99"
            ), "ok"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    result = extract_reference(
        _asset(tmp_path),
        raw_request="Use as reference.",
        provider=RealSnackReferenceProvider(),
    )

    by_id = {fact.fact_id: fact for fact in result.extracted_facts}
    assert result.status == "ok", result.detail
    assert by_id["campaign_title"].value == "Tuesday Night Specials"
    assert [by_id[f"item:{idx}:name"].value for idx in range(10)] == [
        "Punugulu",
        "Egg Bonda",
        "Mysore Bonda",
        "Mysore Bajji",
        "Masala Vada",
        "Mirapakaya Bajji",
        "Onion Samosa",
        "Onion Pakoda",
        "Veg Noodles",
        "Egg Noodles",
    ]
    assert by_id["pricing_structure"].value == "ANY 2 SNACKS $9.99"
    assert not any(fact.fact_id.startswith("item:") and fact.fact_id.endswith(":price") for fact in result.extracted_facts)


def test_reference_extract_keeps_named_combos_as_item_prices(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import ReferenceExtractionProvider, extract_reference

    class ComboMenuProvider(ReferenceExtractionProvider):
        provider_name = "test_vision"

        def extract_text(self, _asset, _raw_request):
            return "Non Veg Combo $49.99\nVeg Combo $39.99", "ok"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    result = extract_reference(
        _asset(tmp_path),
        raw_request="Extract item names and prices from attached sample flyer",
        provider=ComboMenuProvider(),
    )

    by_id = {fact.fact_id: fact for fact in result.extracted_facts}
    assert result.status == "ok", result.detail
    assert by_id["item:0:name"].value == "Non Veg Combo"
    assert by_id["item:0:price"].value == "$49.99"
    assert by_id["item:1:name"].value == "Veg Combo"
    assert by_id["item:1:price"].value == "$39.99"
    assert "pricing_structure" not in by_id


def test_menu_reference_with_bullet_items_but_no_prices_stays_low_confidence(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import ReferenceExtractionProvider, extract_reference

    class UnpricedMenuProvider(ReferenceExtractionProvider):
        provider_name = "test_vision"

        def extract_text(self, _asset, _raw_request):
            return "- Onion Pakoda\n- Mirchi Bajji\n- Samosa", "ok"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    result = extract_reference(
        _asset(tmp_path),
        raw_request="Extract item names and prices from attached sample flyer",
        provider=UnpricedMenuProvider(),
    )

    assert result.status == "low_confidence"
    assert result.extracted_facts == []


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


# ─── Source-contract extraction (Task 3) ───────────────────────────


F0061_RAW_REQUEST = (
    "I'd like you use this flyer for Lakshmi's Kitchen. "
    "Do not change anything else in the flyer, except the changes asked explicitly. "
    "Changes I want. "
    "1. Replace Triveni Express with Lakshmi's Kitchen branding. "
    "2. Replace phone number to +17329837841. "
    "3. Veg Thali Special, replace Rice with Jeera Rice. "
    "4. Change address to 90 Brybar Dr, Saint Johns, FL."
)


def test_classify_reference_role_for_f0061_text(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import classify_reference_role

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = _asset(tmp_path)
    assert classify_reference_role(F0061_RAW_REQUEST, asset) == "source_edit_template"


def test_extract_requested_replacements_from_text_strips_role_nouns(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import extract_requested_replacements_from_text

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    repl = extract_requested_replacements_from_text(F0061_RAW_REQUEST)
    assert repl.get("Triveni Express") == "Lakshmi's Kitchen", repl
    assert repl.get("Rice") == "Jeera Rice", repl


def test_source_edit_role_does_not_return_not_run_anymore(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import NoopReferenceExtractionProvider, extract_reference

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = _asset(tmp_path)
    result = extract_reference(
        asset,
        raw_request="Change date on this uploaded flyer",
        provider=NoopReferenceExtractionProvider(),
    )
    assert result.role == "source_edit_template"
    # No longer the literal "not_run" downgrade; provider_unavailable surfaces
    # the real reason so the manual-review queue picks the right reason code.
    assert result.status != "not_run"
    assert result.status == "provider_unavailable"


def test_source_edit_returns_contract_with_replacements_from_provider(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import ReferenceExtractionProvider, extract_reference

    class FakeVisionProvider(ReferenceExtractionProvider):
        provider_name = "test_vision"

        def extract_text(self, _asset, _raw_request):
            payload = json.dumps({
                "source_business_names": ["Triveni Express"],
                "target_business_name": "Lakshmi's Kitchen",
                "required_headings": ["Monday Thali Specials", "Veg Thali Specials"],
                "required_text": [],
                "sections": [
                    {"heading": "Veg Thali Specials", "items": ["Rice", "Dal", "Pakora"]},
                ],
                "requested_replacements": {},
                "forbidden_substrings": [],
                "preserve_layout": True,
                "preserve_unmentioned_text": True,
                "confidence": "high",
                "notes": "",
            })
            return payload, "ok"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = _asset(tmp_path)
    result = extract_reference(
        asset,
        raw_request=F0061_RAW_REQUEST,
        provider=FakeVisionProvider(),
    )

    assert result.role == "source_edit_template"
    assert result.status == "ok", result.detail
    assert result.source_contract is not None
    contract = result.source_contract
    # Customer-text deterministic replacements override / merge vision dict.
    assert contract.requested_replacements.get("Triveni Express") == "Lakshmi's Kitchen"
    assert contract.requested_replacements.get("Rice") == "Jeera Rice"
    assert contract.preserve_layout is True
    assert contract.preserve_unmentioned_text is True
    assert any(s.heading == "Veg Thali Specials" for s in contract.sections)


def test_source_edit_low_confidence_when_vision_returns_garbage(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import ReferenceExtractionProvider, extract_reference

    class JunkVisionProvider(ReferenceExtractionProvider):
        provider_name = "test_junk"

        def extract_text(self, _asset, _raw_request):
            return "not json at all", "ok"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = _asset(tmp_path)
    result = extract_reference(
        asset,
        raw_request=F0061_RAW_REQUEST,
        provider=JunkVisionProvider(),
    )
    assert result.status == "low_confidence"
    # Even on parse failure, text-only replacements are still attached.
    assert result.source_contract is not None
    assert result.source_contract.requested_replacements.get("Triveni Express") == "Lakshmi's Kitchen"
