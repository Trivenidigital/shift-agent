from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

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


# Real failure shape: F0226 (2026-08-01) and F0228 (2026-10-03) both died as
# reference_low_confidence because the owner's menu photo carries no prices.
PRICELESS_MENU_RAW_REQUEST = (
    "Create similar flyer for Lakshmi's kitchen , same exact items\n"
    "Uploaded reference image/template is attached. Use it when designing this flyer."
)
PRICELESS_MENU_TEXT = "- Idli Sambar\n- Masala Dosa\n- Medu Vada\n- Pongal\n- Filter Coffee"


def test_menu_reference_with_bullet_items_but_no_prices_is_ok_with_prices_omitted(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import ReferenceExtractionProvider, classify_reference_role, extract_reference

    class UnpricedMenuProvider(ReferenceExtractionProvider):
        provider_name = "test_vision"

        def extract_text(self, _asset, _raw_request):
            return PRICELESS_MENU_TEXT, "ok"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = _asset(tmp_path)
    assert classify_reference_role(PRICELESS_MENU_RAW_REQUEST, asset) == "menu_reference"

    result = extract_reference(
        asset, raw_request=PRICELESS_MENU_RAW_REQUEST, provider=UnpricedMenuProvider(), priceless_menu_allowed=True,
    )

    assert result.status == "ok", result.detail
    assert [fact.value for fact in result.extracted_facts if fact.fact_id.endswith(":name")] == [
        "Idli Sambar",
        "Masala Dosa",
        "Medu Vada",
        "Pongal",
        "Filter Coffee",
    ]
    assert not any("$" in fact.value for fact in result.extracted_facts)
    assert "prices omitted" in result.detail


def test_menu_reference_priceless_items_not_allowlisted_keeps_old_low_confidence(tmp_path, monkeypatch):
    # Witness: without the FLYER_PRICELESS_MENU_ALLOWLIST gate (the default) a
    # price-less menu behaves exactly as before — facts emptied, low_confidence.
    from agents.flyer.reference_extract import ReferenceExtractionProvider, extract_reference

    class UnpricedMenuProvider(ReferenceExtractionProvider):
        provider_name = "test_vision"

        def extract_text(self, _asset, _raw_request):
            return PRICELESS_MENU_TEXT, "ok"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    result = extract_reference(_asset(tmp_path), raw_request=PRICELESS_MENU_RAW_REQUEST, provider=UnpricedMenuProvider())

    assert result.status == "low_confidence"
    assert result.extracted_facts == []
    assert result.detail == (
        "no prices on reference; price-less menus not enabled for this customer "
        "(FLYER_PRICELESS_MENU_ALLOWLIST)"
    )


@pytest.mark.parametrize(
    "allowlist, phone, expected",
    [
        ("+15550100001", "+15550100001", True),
        ("+15550100001", "15550100001@s.whatsapp.net", True),
        ("+15550100001, +17329837841", "+17329837841", True),
        ("*", "+19999999999", True),
        ("+15550100001", "+19999999999", False),
        ("", "+15550100001", False),
        (None, "+15550100001", False),
    ],
)
def test_priceless_menu_enabled_allowlist(monkeypatch, allowlist, phone, expected):
    from agents.flyer.reference_extract import priceless_menu_enabled

    if allowlist is None:
        monkeypatch.delenv("FLYER_PRICELESS_MENU_ALLOWLIST", raising=False)
    else:
        monkeypatch.setenv("FLYER_PRICELESS_MENU_ALLOWLIST", allowlist)

    assert priceless_menu_enabled(phone) is expected


def test_menu_reference_without_items_or_prices_stays_low_confidence(tmp_path, monkeypatch):
    from agents.flyer.reference_extract import ReferenceExtractionProvider, extract_reference

    class HeadingOnlyProvider(ReferenceExtractionProvider):
        provider_name = "test_vision"

        def extract_text(self, _asset, _raw_request):
            return "Lakshmi's Kitchen\nBreakfast Menu", "ok"

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    result = extract_reference(_asset(tmp_path), raw_request=PRICELESS_MENU_RAW_REQUEST, provider=HeadingOnlyProvider())

    assert result.status == "low_confidence"
    assert result.extracted_facts == []


def test_same_exact_items_request_requires_reference_menu_items():
    from agents.flyer.reference_extract import _request_requires_reference_menu_items

    assert _request_requires_reference_menu_items(PRICELESS_MENU_RAW_REQUEST)
    assert _request_requires_reference_menu_items("Make a flyer with the same items")
    assert not _request_requires_reference_menu_items("Make a Diwali flyer")


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
    "2. Replace phone number to +15550100001. "
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


# --- Review fixes F1-F5 (29ce50e3): driven through the REAL OpenRouter vision
# provider with realistic South-Indian OCR JSON (parentheticals, footers,
# section headers, non-dollar currency). -------------------------------------

ROUTER_WRAP_INTENT = "{}\nUploaded reference image/template is attached. Use it when designing this flyer."
ROUTER_WRAP_PLAIN = "Create flyer from uploaded template/reference. Customer requested: {}"

SOUTH_INDIAN_OCR = {
    "visible_text": (
        "LAKSHMI'S KITCHEN\nTIFFINS\nIdli (3 PCS)\nMedu Vada (2 PCS)\nMasala Dosa\nMysore Masala Dosa.\n"
        "Pongal\nServed with sambar & chutney\nOpen 7 days\nCall 904-555-0123\n90 Brybar Dr, St Johns FL\nVeg / Non-Veg"
    ),
    "sections": [
        {"heading": "TIFFINS", "items": ["Idli (3 PCS)", "Medu Vada (2 PCS)", "Masala Dosa", "Mysore Masala Dosa.", "Pongal"]},
        {"heading": "BEVERAGES", "items": ["Filter Coffee", "Mango Lassi"]},
        {"heading": "Contact", "items": [
            "Call 904-555-0123", "Open 7 days", "Served with sambar & chutney", "Veg / Non-Veg",
            "90 Brybar Dr, St Johns FL", "Contains nuts - ask staff",
        ]},
        {"heading": "Specials", "items": ["APPETIZERS", "Chettinad Chicken Curry", "Kothu Parotta", "మసాలా దోశ"]},
    ],
    "confidence": "high",
}
SOUTH_INDIAN_DISHES = [
    "Idli (3 PCS)", "Medu Vada (2 PCS)", "Masala Dosa", "Mysore Masala Dosa", "Pongal",
    "Filter Coffee", "Mango Lassi", "Chettinad Chicken Curry", "Kothu Parotta", "మసాలా దోశ",
]
SOUTH_INDIAN_JUNK = ["Call 904-555-0123", "Open 7 days", "Served with sambar & chutney", "Veg / Non-Veg", "Contains nuts - ask staff"]


def _vision_extract(tmp_path, parsed, raw_request, *, allowed=True):
    from agents.flyer.reference_extract import OpenRouterVisionReferenceExtractionProvider, extract_reference

    provider = OpenRouterVisionReferenceExtractionProvider(api_key="k", call_json=lambda _payload: parsed)
    return extract_reference(_asset(tmp_path), raw_request=raw_request, provider=provider, priceless_menu_allowed=allowed)


def _item_names(result):
    return [fact.value for fact in result.extracted_facts if fact.fact_id.startswith("item:") and fact.fact_id.endswith(":name")]


def test_real_vision_provider_keeps_parenthetical_and_non_latin_dishes_and_drops_footers(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    result = _vision_extract(tmp_path, SOUTH_INDIAN_OCR, ROUTER_WRAP_INTENT.format(PRICELESS_MENU_RAW_REQUEST.splitlines()[0]))

    assert result.status == "ok", result.detail
    assert _item_names(result) == SOUTH_INDIAN_DISHES
    assert not any(junk in _item_names(result) for junk in SOUTH_INDIAN_JUNK)
    assert "APPETIZERS" not in _item_names(result)
    assert "prices omitted" in result.detail
    assert "ignored non-item lines" in result.detail
    for junk in SOUTH_INDIAN_JUNK:
        assert junk in result.detail


def test_same_items_request_with_unparseable_item_is_low_confidence(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    parsed = {
        "visible_text": "Idli\nMasala Dosa\n2 Vada Combo",
        "sections": [{"heading": "Tiffins", "items": ["Idli", "Masala Dosa", "2 Vada Combo"]}],
        "confidence": "high",
    }

    result = _vision_extract(tmp_path, parsed, PRICELESS_MENU_RAW_REQUEST)

    assert result.status == "low_confidence"
    assert result.extracted_facts == []
    assert result.detail.startswith("1 of 3 items unparsed")


def test_priceless_reference_without_menu_intent_stays_low_confidence_even_when_allowlisted(tmp_path, monkeypatch):
    # The router wrapper alone makes a Diwali poster classify as menu_reference;
    # its bullet lines must not become locked menu items.
    from agents.flyer.reference_extract import classify_reference_role

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    raw = ROUTER_WRAP_INTENT.format("Make a Diwali flyer like this for my restaurant")
    parsed = {
        "visible_text": "Happy Diwali\nFestival of Lights\nJoin us for celebrations",
        "sections": [{"heading": "Happy Diwali", "items": ["Live Music", "Rangoli Contest", "Kids Activities", "Fireworks"]}],
        "confidence": "high",
    }
    assert classify_reference_role(raw, _asset(tmp_path)) == "menu_reference"

    result = _vision_extract(tmp_path, parsed, raw)

    assert result.status == "low_confidence"
    assert result.extracted_facts == []
    assert result.detail == "price-less reference without menu intent"


def test_f0226_update_menu_request_shape_is_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    parsed = {
        "visible_text": "Idli Sambar\nMasala Dosa\nPongal",
        "sections": [{"heading": "Tiffins", "items": ["Idli Sambar", "Masala Dosa", "Pongal"]}],
        "confidence": "high",
    }

    result = _vision_extract(tmp_path, parsed, ROUTER_WRAP_PLAIN.format("Update menu"))

    assert result.role == "menu_reference"
    assert result.status == "ok", result.detail
    assert _item_names(result) == ["Idli Sambar", "Masala Dosa", "Pongal"]


def test_vision_price_not_printed_on_photo_is_dropped(tmp_path, monkeypatch):
    from agents.flyer.facts import reference_prices_omitted
    from schemas import FlyerProject

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    parsed = {
        "visible_text": "Tiffins\nIdli\nMasala Dosa\nPongal",
        "sections": [{"heading": "Tiffins", "items": [{"name": "Idli", "price": "$6.99"}, {"name": "Masala Dosa", "price": ""}, "Pongal"]}],
        "confidence": "high",
    }

    result = _vision_extract(tmp_path, parsed, PRICELESS_MENU_RAW_REQUEST)

    assert result.status == "ok", result.detail
    assert _item_names(result) == ["Idli", "Masala Dosa", "Pongal"]
    assert not any(fact.fact_id.endswith(":price") or "$" in fact.value for fact in result.extracted_facts)
    assert "dropped unseen price: $6.99" in result.detail
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0228", status="generating_concepts", customer_phone="+15550100001",
        created_at=now, updated_at=now, original_message_id="m-ref", raw_request=PRICELESS_MENU_RAW_REQUEST,
        locked_facts=result.extracted_facts, reference_extractions=[result],
    )
    assert reference_prices_omitted(project) is True


def test_vision_price_printed_on_photo_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    parsed = {
        "visible_text": "Idli $6.99\nMasala Dosa $8.99",
        "sections": [{"heading": "Tiffins", "items": [{"name": "Idli", "price": "$6.99"}, {"name": "Masala Dosa", "price": "$8.99"}]}],
        "confidence": "high",
    }

    result = _vision_extract(tmp_path, parsed, PRICELESS_MENU_RAW_REQUEST)

    by_id = {fact.fact_id: fact.value for fact in result.extracted_facts}
    assert result.status == "ok", result.detail
    assert by_id["item:0:price"] == "$6.99"
    assert by_id["item:1:price"] == "$8.99"


def test_non_dollar_trailing_price_is_split_off_the_item_name(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    parsed = {
        "visible_text": "Paneer Butter Masala Rs 220\nKothu Parotta ₹180",
        "sections": [{"heading": "Specials", "items": ["Paneer Butter Masala Rs 220", "Kothu Parotta ₹180"]}],
        "confidence": "high",
    }

    result = _vision_extract(tmp_path, parsed, PRICELESS_MENU_RAW_REQUEST, allowed=False)

    by_id = {fact.fact_id: fact.value for fact in result.extracted_facts}
    assert result.status == "ok", result.detail
    assert by_id["item:0:name"] == "Paneer Butter Masala"
    assert by_id["item:0:price"] == "Rs 220"
    assert by_id["item:1:name"] == "Kothu Parotta"
    assert by_id["item:1:price"] == "₹180"


def test_reference_extraction_prompt_forbids_estimated_prices_and_footer_items():
    from agents.flyer.reference_extract import REFERENCE_EXTRACTION_PROMPT

    assert "Never estimate, infer or fill in a price" in REFERENCE_EXTRACTION_PROMPT
    assert "sections[].items are dishes/products only" in REFERENCE_EXTRACTION_PROMPT
