"""Renderer tests for production Flyer Studio assets."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import http.client
import io
import json
import subprocess
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.render import (  # noqa: E402
    FlyerRenderError,
    _image_message_content,
    _image_prompt,
    _menu_overlay_payload,
    apply_exact_identity_overlay,
    apply_critical_text_overlay,
    build_asset_manifest,
    collect_text_facts,
    inspect_rendered_asset,
    render_concept_previews,
    render_final_package,
    render_source_edit_preview,
    validate_text_manifest_file,
    write_text_manifest,
)
import agents.flyer.render as render_module  # noqa: E402
from schemas import FlyerAsset, FlyerConcept, FlyerLockedFact, FlyerManualReview, FlyerProject, FlyerReferenceExtraction, FlyerRequestFields, FlyerRevision  # noqa: E402


def _complete_project() -> FlyerProject:
    return FlyerProject(
        project_id="F0001",
        status="generating_concepts",
        customer_phone="+19045550123",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="wamid.flyer.1",
        raw_request="Need flyer for Bathukamma Oct 10",
        fields=FlyerRequestFields(
            event_or_business_name="Bathukamma Celebrations",
            event_date="2026-10-10",
            event_time="6:00 PM",
            venue_or_location="Triveni Community Hall",
            contact_info="+1 904 555 0123",
            preferred_language="te",
            style_preference="festive Telangana flowers, premium but readable",
        ),
    )


def _english_project() -> FlyerProject:
    """Simple English typed menu project eligible for integrated poster mode."""
    return _complete_project().model_copy(update={"fields": FlyerRequestFields(
        event_or_business_name="Lakshmi's Kitchen",
        contact_info="+1 732 983 7841",
        venue_or_location="90 Brybar Dr St Johns FL",
        preferred_language="en",
        notes="Dosa $6.99; Idli $5.99",
        style_preference="warm festive south-indian, premium readable",
    )})


def _png_bytes(size=(1080, 1350), color=(240, 120, 40)) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size[0], size[1] // 3), fill=(color[2], color[0], color[1]))
    draw.rectangle((0, size[1] * 2 // 3, size[0], size[1]), fill=(color[1] // 2, color[2], color[0] // 2))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


DESSERT_GRADUATION_SUFFIX_PRICE_BRIEF = (
    "Graduation is here and time to celebrate our kids \n\n"
    "We take customized orders - Desserts\n\n"
    "Mango tresleches - half tray - 75$\n"
    "Rasmalai tresleches - half tray 70$\n"
    "Apricot delight - half tray - 80$\n"
    "Butter scotch - half tray 75$\n"
    "Strawberry pastry - half 70$\n"
    "Chocolate pastry - half 70$\n"
    "Gulab jamun - 100count - 80$\n"
    "Gulabjamun fusion - half tray - 75$\n"
    "Kheer(Ramadan style) half tray - 55$\n"
    "Kadhu ki sheet - small tray - 65$\n"
    "Double ka meeta - small tray - 45$\n"
    "Kurbanika meeta - small tray - 70$\n"
    "Carrot halwa - small tray 55$\n"
    "Khalakhandh - 100 count 100$"
)


def _dessert_graduation_project() -> FlyerProject:
    from agents.flyer.facts import extract_text_facts, merge_locked_facts

    now = datetime(2026, 6, 7, tzinfo=timezone.utc)
    fields = FlyerRequestFields(
        event_or_business_name="Graduation Dessert Specials",
        contact_info="+17329837841",
        venue_or_location="90 Brybar Dr St Johns FL",
        preferred_language="en",
        notes=DESSERT_GRADUATION_SUFFIX_PRICE_BRIEF,
    )
    profile_facts = [
        FlyerLockedFact(
            fact_id="business_name",
            label="Business",
            value="Lakshmis Kitchen",
            source="customer_profile",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="contact_phone",
            label="Contact",
            value="+17329837841",
            source="customer_profile",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="location",
            label="Location",
            value="90 Brybar Dr St Johns FL",
            source="customer_profile",
            required=True,
        ),
    ]
    return FlyerProject(
        project_id="F9014",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="wamid.dessert-graduation",
        raw_request=DESSERT_GRADUATION_SUFFIX_PRICE_BRIEF,
        fields=fields,
        locked_facts=merge_locked_facts(
            profile_facts,
            extract_text_facts(
                fields,
                DESSERT_GRADUATION_SUFFIX_PRICE_BRIEF,
                message_id="wamid.dessert-graduation",
                profile_business_name="Lakshmis Kitchen",
                allow_text_identity=False,
            ),
        ),
    )


TRIVENI_TUESDAY_SNACK_ITEMS = [
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


def _triveni_shared_price_reference_project() -> FlyerProject:
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    locked_facts = [
        FlyerLockedFact(
            fact_id="business_name",
            label="Business",
            value="Lakshmi's Kitchen",
            source="customer_profile",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="campaign_title",
            label="Campaign",
            value="Tuesday Night Specials",
            source="reference_vision",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="location",
            label="Location",
            value="90 Brybar Dr St Johns FL",
            source="customer_profile",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="contact_phone",
            label="Contact",
            value="+17329837841",
            source="customer_profile",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="pricing_structure",
            label="Pricing",
            value="Any 2 Snacks $9.99",
            source="reference_vision",
            required=True,
        ),
    ]
    locked_facts.extend(
        FlyerLockedFact(
            fact_id=f"item:{idx}:name",
            label="Item",
            value=name,
            source="reference_vision",
            required=True,
        )
        for idx, name in enumerate(TRIVENI_TUESDAY_SNACK_ITEMS)
    )
    return FlyerProject(
        project_id="F0144",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="wamid.triveni-reference",
        raw_request="Lakshmi's kitchen, same content, but I'd like you to use Lakshmi's kitchen theme",
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's kitchen, same content, but I'd like you to use Lakshmi's kitchen theme",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes="use as reference",
            preferred_language="en",
            style_preference="Lakshmi's Kitchen theme",
        ),
        locked_facts=locked_facts,
    )


def test_render_concept_previews_creates_one_png_by_default(tmp_path):
    project = _complete_project()
    specs = render_concept_previews(project, tmp_path)
    assert [s.concept_id for s in specs] == ["C1"]
    for spec in specs:
        assert spec.path.exists()
        assert spec.path.suffix == ".png"
        assert spec.path.stat().st_size > 1000
        assert spec.width == 1080
        assert spec.height == 1350
        assert validate_text_manifest_file(
            spec.path,
            project_id=project.project_id,
            project_version=project.version,
            output_format="concept_preview",
        ).ok is True


def test_image_prompt_adds_repair_instruction_only_when_supplied():
    project = _complete_project()
    base = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    repaired = _image_prompt(
        project,
        concept_id="C1",
        output_format="concept_preview",
        size=(1080, 1350),
        repair_instruction="Remove duplicate item lines and do not add a generic footer title.",
    )

    assert "Autonomous repair instruction:" not in base
    assert "Autonomous repair instruction:" in repaired
    assert "Remove duplicate item lines" in repaired


def test_text_manifest_rejects_stale_project_version_and_hash(tmp_path):
    project = _complete_project()
    spec = render_concept_previews(project, tmp_path)[0]
    assert validate_text_manifest_file(spec.path, project_id="F0001", project_version=1).ok is True
    stale = validate_text_manifest_file(spec.path, project_id="F0001", project_version=2)
    assert stale.ok is False
    assert "text manifest project version mismatch" in stale.blockers

    spec.path.write_bytes(spec.path.read_bytes() + b"stale")
    changed = validate_text_manifest_file(spec.path, project_id="F0001", project_version=1)
    assert changed.ok is False
    assert "text manifest artifact hash mismatch" in changed.blockers


def test_text_manifest_invalid_shape_fails_closed(tmp_path):
    project = _complete_project()
    spec = render_concept_previews(project, tmp_path)[0]
    sidecar = Path(f"{spec.path}.text.json")
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    doc["expected_facts"] = ["bad"]
    sidecar.write_text(json.dumps(doc), encoding="utf-8")

    result = validate_text_manifest_file(spec.path, project_id="F0001", project_version=1)

    assert result.ok is False
    assert "text manifest invalid expected fact entry" in result.blockers


def test_collect_text_facts_keeps_revised_price_phone_location_and_schedule():
    project = _complete_project()
    fields = FlyerRequestFields(
        event_or_business_name="Thursday Dosa Night Special",
        venue_or_location="Lakshmi's Kitchen",
        contact_info="+1 980 200 5022",
        notes="Starts from 5 PM every Thursday. Non-veg combo $16.99; Veg combo $12.99",
    )
    project = project.model_copy(update={"fields": fields})
    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}
    assert facts["title"] == "Thursday Dosa Night Special"
    assert facts["schedule"].startswith("Starts from 5 PM")
    assert facts["location"] == "Lakshmi's Kitchen"
    assert facts["contact"] == "+1 980 200 5022"
    assert "$16.99" in facts["detail_001"]
    assert "$12.99" in facts["detail_002"]


def test_collect_text_facts_ignores_unrequested_hermes_inferred_items():
    raw_request = (
        "Can we do meal combo flyer for veg and non veg with prices 49.99 for non veg combo "
        "includes 2 non veg curries, 1 chicken pulav or chicken Biryani and 1 dessert. "
        "And a veg combo 39.99 includes 2 veg curries, 1 dessert on the occasion of Memorial Day weekend"
    )
    inferred_items = [
        "Spicy Chicken Curry",
        "Butter Chicken",
        "Chicken Biryani",
        "Chicken Pulav",
        "Tandoori Chicken",
        "Paneer Butter Masala",
        "Chana Masala",
        "Vegetable Korma",
        "Dal Makhani",
        "Aloo Gobi",
        "Gulab Jamun",
        "Rasgulla",
    ]
    locked_facts = [
        FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Memorial Day Weekend Meal Combos", source="customer_text", required=True),
        FlyerLockedFact(
            fact_id="offer:0",
            label="Offer",
            value="Non Veg Combo: $49.99 includes 2 non veg curries, 1 chicken pulav or chicken Biryani, and 1 dessert",
            source="customer_text",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="offer:1",
            label="Offer",
            value="Veg Combo: $39.99 includes 2 veg curries and 1 dessert",
            source="customer_text",
            required=True,
        ),
    ]
    locked_facts.extend(
        FlyerLockedFact(fact_id=f"item:{index}:name", label="Item", value=item, source="hermes_inferred")
        for index, item in enumerate(inferred_items)
    )
    project = _complete_project().model_copy(update={
        "raw_request": raw_request,
        "fields": FlyerRequestFields(
            event_or_business_name="Memorial Day Weekend Meal Combos",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes=raw_request,
            preferred_language="en",
        ),
        "locked_facts": locked_facts,
    })

    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}
    rendered = "\n".join(facts.values())

    assert facts["detail_001"].startswith("Non Veg Combo: $49.99")
    assert facts["detail_002"].startswith("Veg Combo: $39.99")
    assert "Spicy Chicken Curry" not in rendered
    assert "Paneer Butter Masala" not in rendered
    assert "Rasgulla" not in rendered


def test_semantic_offer_facts_feed_text_manifest_and_generation_prompt():
    project = _complete_project().model_copy(update={
        "raw_request": (
            "Create a flyer for evening snacks sale, Wednesday and Thursday, any item $7.99. "
            "Free Masala Chai with any purchase above $12. This promotion runs until June 25."
        ),
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile"),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile"),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile"),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Evening Snacks Sale", source="customer_text"),
            FlyerLockedFact(fact_id="pricing_structure", label="Pricing", value="Any item $7.99", source="customer_text"),
            FlyerLockedFact(fact_id="offer:0", label="Offer", value="Free Masala Chai with any purchase above $12", source="customer_text"),
            FlyerLockedFact(fact_id="schedule", label="Schedule", value="Wednesday and Thursday", source="customer_text"),
            FlyerLockedFact(fact_id="promotion_end", label="Promotion end", value="June 25", source="customer_text"),
        ],
    })

    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}
    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert facts["title"] == "Evening Snacks Sale"
    assert "Any item $7.99" in facts.values()
    assert "Free Masala Chai with any purchase above $12" in facts.values()
    assert "June 25" in facts.values()
    assert "Any item $7.99" in prompt
    assert "Free Masala Chai with any purchase above $12" in prompt
    assert "Promotion end: June 25" in prompt


def test_collect_text_facts_avoids_duplicate_time_when_schedule_has_time_range():
    project = _complete_project()
    fields = FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        event_time="16:00",
        venue_or_location="90 Brybar Dr St Johns FL",
        contact_info="+17329837841",
        notes="Evening snacks offer. Schedule 4 PM to 7 PM. Wednesday through Saturday.",
    )
    project = project.model_copy(update={"fields": fields})

    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}

    assert "schedule" in facts
    assert "4 PM TO 7 PM" in facts["schedule"]
    assert "time" not in facts
    assert "Time: 16:00" not in _image_prompt(
        project,
        concept_id="C1",
        output_format="concept_preview",
        size=(1080, 1350),
    )


def test_collect_text_facts_separates_business_brand_from_campaign_title():
    """Business identity and campaign title are different customer-visible facts.

    The brand must remain visible, but the poster title should be the campaign
    title/headline rather than duplicating the business name.
    """
    project = _complete_project().model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Evening Snacks",
            venue_or_location="Old Stale Address",
            contact_info="+19999999999",
            notes="-",
        ),
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmis Kitchn", source="customer_profile"),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Evening Snacks", source="customer_text"),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile"),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile"),
        ],
    })

    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}
    assert facts["brand"] == "Lakshmis Kitchn"
    assert facts["title"] == "Evening Snacks"
    assert facts["location"] == "90 Brybar Dr St Johns FL"
    assert facts["contact"] == "+17329837841"
    assert _menu_overlay_payload(project)["title"] == "Evening Snacks"
    assert "Business/brand: Lakshmis Kitchn" in _image_prompt(
        project,
        concept_id="C1",
        output_format="concept_preview",
        size=(1080, 1350),
    )
    assert "Title: Evening Snacks" in _image_prompt(
        project,
        concept_id="C1",
        output_format="concept_preview",
        size=(1080, 1350),
    )


def test_collect_text_facts_uses_headline_when_campaign_title_is_absent():
    project = _complete_project().model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Lakshmis Kitchn",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes="Headline: Family Combo Feast",
        ),
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmis Kitchn", source="customer_profile"),
            FlyerLockedFact(fact_id="headline", label="Headline", value="Family Combo Feast", source="customer_text"),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile"),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile"),
        ],
    })

    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}
    assert facts["brand"] == "Lakshmis Kitchn"
    assert facts["title"] == "Family Combo Feast"


def test_collect_text_facts_falls_back_to_fields_when_locked_slot_missing():
    """P0-2: if locked_facts has no entry for a slot, the existing field is used unchanged.
    Regression guard so the locked-fact preference doesn't blank out projects that only
    populated the legacy fields path."""
    project = _complete_project().model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Lakshmis Kitchn",
            venue_or_location="St Johns FL",
            contact_info="+17329837841",
            notes="-",
        ),
        "locked_facts": [],  # no locked facts at all
    })

    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}
    assert facts["title"] == "Lakshmis Kitchn"
    assert facts["location"] == "St Johns FL"
    assert facts["contact"] == "+17329837841"


def test_collect_text_facts_drops_instruction_field_title_fallbacks():
    for poisoned_title in ("Create", "Multiple Page"):
        project = _complete_project().model_copy(update={
            "fields": FlyerRequestFields(
                event_or_business_name=poisoned_title,
                venue_or_location="St Johns FL",
                contact_info="+17329837841",
                notes="Create flyer with menu details.",
            ),
            "locked_facts": [
                FlyerLockedFact(fact_id="business_name", label="Business", value="MK kitchen", source="customer_profile"),
                FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile"),
                FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile"),
            ],
        })

        facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}

        assert facts["brand"] == "MK kitchen"
        assert facts["title"] == "Specials"
        assert poisoned_title not in facts.values()


def test_triveni_shared_price_reference_menu_uses_hero_offer_layout():
    """Production quality regression for the Tuesday snack reference flyer.

    This is not a conventional `Samosa $4.99` menu. It is a snack list plus a
    shared combo price. The renderer must treat the shared price as the main
    deal and still render every item once without inventing per-item prices.
    """
    project = _triveni_shared_price_reference_project()

    assert render_module._compact_menu_overlay_allowed(project) is True

    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}
    details = [text for fact_id, text in facts.items() if fact_id.startswith("detail_")]
    payload = _menu_overlay_payload(project)

    assert facts["title"] == "Tuesday Night Specials"
    assert "Any 2 Snacks $9.99" in details
    assert len([item for item in details if item in TRIVENI_TUESDAY_SNACK_ITEMS]) == 10
    assert payload["items"] == TRIVENI_TUESDAY_SNACK_ITEMS
    assert payload["shared_offer_text"] == "Any 2 Snacks $9.99"
    assert payload["shared_offer_label"] == "Any 2 Snacks"
    assert payload["shared_offer_price"] == "$9.99"
    assert not any("$9.99" in item for item in payload["items"])


def test_triveni_shared_price_reference_overlay_renders_without_fit_failure(tmp_path):
    from PIL import Image

    source = tmp_path / "background.png"
    target = tmp_path / "triveni-shared-price.png"
    Image.new("RGB", (1080, 1350), (82, 42, 30)).save(source)

    apply_critical_text_overlay(
        _triveni_shared_price_reference_project(),
        source,
        target,
        size=(1080, 1350),
        output_format="concept_preview",
    )

    assert inspect_rendered_asset(
        target,
        expected_width=1080,
        expected_height=1350,
        mime_type="image/png",
    ).ok is True


def test_triveni_shared_price_reference_overlay_avoids_menu_table_look(tmp_path):
    from PIL import Image

    source = tmp_path / "background.png"
    target = tmp_path / "triveni-shared-price.png"
    Image.new("RGB", (1080, 1350), (82, 42, 30)).save(source)

    apply_critical_text_overlay(
        _triveni_shared_price_reference_project(),
        source,
        target,
        size=(1080, 1350),
        output_format="concept_preview",
    )

    with Image.open(target).convert("RGB") as img:
        width, height = img.size
        title_region = img.crop((40, 30, int(width * 0.60), int(height * 0.24)))
        item_region = img.crop((40, int(height * 0.42), width - 40, height - 70))
        title_pixels = list(title_region.getdata())
        pixels = list(item_region.getdata())
    title_near_white = sum(1 for r, g, b in title_pixels if r > 225 and g > 218 and b > 195)
    near_white = sum(1 for r, g, b in pixels if r > 225 and g > 218 and b > 195)
    gold_outline = sum(1 for r, g, b in pixels if r > 210 and 140 < g < 230 and b < 120)
    title_near_white_ratio = title_near_white / max(1, len(title_pixels))
    near_white_ratio = near_white / max(1, len(pixels))
    gold_outline_ratio = gold_outline / max(1, len(pixels))

    assert title_near_white_ratio < 0.30
    assert near_white_ratio < 0.45
    assert gold_outline_ratio < 0.12


def test_triveni_shared_price_reference_overlay_has_premium_poster_hierarchy(tmp_path):
    from PIL import Image, ImageDraw

    background = (82, 42, 30)
    source = tmp_path / "background.png"
    target = tmp_path / "triveni-shared-price.png"
    with Image.new("RGB", (1080, 1350), background) as src_img:
        ImageDraw.Draw(src_img).rectangle((40, 60, 240, 300), fill=(225, 225, 225))
        ImageDraw.Draw(src_img).rectangle((70, 455, 380, 490), fill=(112, 118, 90))
        ImageDraw.Draw(src_img).rectangle((540, 80, 1040, 280), fill=(255, 255, 255))
        ImageDraw.Draw(src_img).rectangle((80, 1230, 390, 1270), fill=(245, 245, 235))
        src_img.save(source)

    apply_critical_text_overlay(
        _triveni_shared_price_reference_project(),
        source,
        target,
        size=(1080, 1350),
        output_format="concept_preview",
    )

    with Image.open(target).convert("RGB") as img:
        width, height = img.size
        left_source_region = img.crop((40, int(height * 0.16), 170, int(height * 0.24)))
        left_column_ghost_region = img.crop((70, int(height * 0.335), 380, int(height * 0.360)))
        bottom_ghost_region = img.crop((80, int(height * 0.912), 390, int(height * 0.925)))
        source_header_region = img.crop((int(width * 0.50), int(height * 0.06), width - 40, int(height * 0.24)))
        middle_region = img.crop((40, int(height * 0.46), width - 40, int(height * 0.68)))
        bottom_strip = img.crop((0, int(height * 0.82), width, height))
        left_source_pixels = list(left_source_region.getdata())
        left_column_ghost_pixels = list(left_column_ghost_region.getdata())
        bottom_ghost_pixels = list(bottom_ghost_region.getdata())
        source_header_pixels = list(source_header_region.getdata())
        middle_pixels = list(middle_region.getdata())
        bottom_pixels = list(bottom_strip.getdata())

    left_source_leak = sum(1 for r, g, b in left_source_pixels if r > 190 and g > 190 and b > 190)
    left_column_mask_miss = sum(1 for pixel in left_column_ghost_pixels if pixel != (3, 12, 8))
    left_column_ghost_leak = sum(1 for r, g, b in left_column_ghost_pixels if r > 190 and g > 190 and b > 190)
    bottom_mask_miss = sum(1 for pixel in bottom_ghost_pixels if pixel != (52, 24, 17))
    bottom_ghost_leak = sum(1 for r, g, b in bottom_ghost_pixels if r > 190 and g > 190 and b > 190)
    leaked_source_header = sum(1 for r, g, b in source_header_pixels if r > 225 and g > 225 and b > 225)
    middle_changed = sum(1 for pixel in middle_pixels if pixel != background)
    dark_bottom = sum(1 for r, g, b in bottom_pixels if r < 45 and g < 35 and b < 35)

    assert left_source_leak / max(1, len(left_source_pixels)) < 0.02
    assert left_column_mask_miss == 0
    assert left_column_ghost_leak / max(1, len(left_column_ghost_pixels)) < 0.02
    assert bottom_mask_miss == 0
    assert bottom_ghost_leak / max(1, len(bottom_ghost_pixels)) < 0.02
    assert leaked_source_header / max(1, len(source_header_pixels)) < 0.02
    assert middle_changed / max(1, len(middle_pixels)) > 0.12
    assert dark_bottom / max(1, len(bottom_pixels)) < 0.45


def test_triveni_shared_price_reference_overlay_uses_bottom_deal_band(tmp_path):
    from PIL import Image

    background = (82, 42, 30)
    source = tmp_path / "background.png"
    target = tmp_path / "triveni-shared-price.png"
    Image.new("RGB", (1080, 1350), background).save(source)

    apply_critical_text_overlay(
        _triveni_shared_price_reference_project(),
        source,
        target,
        size=(1080, 1350),
        output_format="concept_preview",
    )

    with Image.open(target).convert("RGB") as img:
        width, height = img.size
        deal_band = img.crop((40, int(height * 0.745), width - 40, int(height * 0.905)))
        pixels = list(deal_band.getdata())

    premium_red = sum(1 for r, g, b in pixels if r > 95 and g < 72 and b < 72)
    premium_gold = sum(1 for r, g, b in pixels if r > 185 and 120 < g < 220 and b < 110)
    changed = sum(1 for pixel in pixels if pixel != background)

    assert changed / max(1, len(pixels)) > 0.70
    assert premium_red / max(1, len(pixels)) > 0.18
    assert premium_gold / max(1, len(pixels)) > 0.01


def test_triveni_shared_price_reference_overlay_has_branded_masthead_energy(tmp_path):
    from PIL import Image

    background = (82, 42, 30)
    source = tmp_path / "background.png"
    target = tmp_path / "triveni-shared-price.png"
    Image.new("RGB", (1080, 1350), background).save(source)

    apply_critical_text_overlay(
        _triveni_shared_price_reference_project(),
        source,
        target,
        size=(1080, 1350),
        output_format="concept_preview",
    )

    with Image.open(target).convert("RGB") as img:
        width, height = img.size
        masthead = img.crop((int(width * 0.16), int(height * 0.025), int(width * 0.84), int(height * 0.105)))
        top_sides = img.crop((0, int(height * 0.02), width, int(height * 0.18)))
        masthead_pixels = list(masthead.getdata())
        side_pixels = list(top_sides.getdata())

    ivory = sum(1 for r, g, b in masthead_pixels if r > 205 and g > 190 and b > 155)
    red_energy = sum(1 for r, g, b in side_pixels if r > 125 and g < 70 and b < 60)
    gold_energy = sum(1 for r, g, b in side_pixels if r > 190 and 120 < g < 225 and b < 115)

    assert ivory / max(1, len(masthead_pixels)) > 0.18
    assert red_energy / max(1, len(side_pixels)) > 0.004
    assert gold_energy / max(1, len(side_pixels)) > 0.006


def test_collect_text_facts_uses_locked_reference_items_before_raw_request():
    project = _complete_project().model_copy(update={
        "raw_request": "Create a flyer from this attached menu.",
        "fields": FlyerRequestFields(
            event_or_business_name="Menu Specials",
            contact_info="+1 904 555 0123",
            notes="Create a flyer from this attached menu.",
        ),
        "locked_facts": [
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Idly", source="reference_vision"),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$7", source="reference_vision"),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Dosa", source="reference_vision"),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$8", source="reference_vision"),
        ],
    })

    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}
    payload = _menu_overlay_payload(project)

    assert facts["detail_001"] == "Idly $7"
    assert facts["detail_002"] == "Dosa $8"
    assert payload["items"] == ["Idly $7", "Dosa $8"]


def test_collect_text_facts_preserves_multi_offer_celebration_contract():
    fields = FlyerRequestFields(
        event_or_business_name="One Year Grand Celebration",
        event_date="2026-05-30",
        venue_or_location="90 Brybar Dr St Johns FL",
        contact_info="+17329837841",
        notes=(
            "Create a one year grand celebration flyer, which must include "
            "grand sale 30% of all dine-In orders and 20% on all Take Away orders. "
            "All Biryani's Buy one get one free. Special Lunch Thali for $12.99. "
            "Dates both May 30 and 31st"
        ),
    )
    project = _complete_project().model_copy(update={"fields": fields})

    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}
    rendered_text = "\n".join(facts.values())

    assert facts["date"] == "May 30 and May 31, 2026"
    assert "grand sale 30% of all dine-In orders and 20% on all Take Away orders" in rendered_text
    assert "All Biryani's Buy one get one free" in rendered_text
    assert "Special Lunch Thali for $12.99" in rendered_text


def test_image_prompt_preserves_multi_offer_celebration_contract():
    fields = FlyerRequestFields(
        event_or_business_name="One Year Grand Celebration",
        event_date="2026-05-30",
        venue_or_location="90 Brybar Dr St Johns FL",
        contact_info="+17329837841",
        notes=(
            "Create a one year grand celebration flyer, which must include "
            "grand sale 30% of all dine-In orders and 20% on all Take Away orders. "
            "All Biryani's Buy one get one free. Special Lunch Thali for $12.99. "
            "Dates both May 30 and 31st"
        ),
    )
    project = _complete_project().model_copy(update={"fields": fields})

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Date: May 30 and May 31, 2026" in prompt
    assert "grand sale 30% of all dine-In orders and 20% on all Take Away orders" in prompt
    assert "All Biryani's Buy one get one free" in prompt
    assert "Special Lunch Thali - $12.99" in prompt


def test_collect_text_facts_suppresses_old_phone_from_notes_after_revision():
    project = _complete_project()
    fields = FlyerRequestFields(
        event_or_business_name="Dosa Night",
        venue_or_location="Lakshmi's Kitchen",
        contact_info="+1 980 200 5022",
        notes="Phone: +1 904 555 0123; Non-veg combo $16.99",
    )
    project = project.model_copy(update={"fields": fields})

    rendered_text = "\n".join(fact.text for fact in collect_text_facts(project))

    assert "+1 980 200 5022" in rendered_text
    assert "+1 904 555 0123" not in rendered_text


def test_collect_text_facts_accepts_ten_item_menu_and_daily_schedule():
    fields = FlyerRequestFields(
        event_or_business_name="Daily Lunch Specials",
        contact_info="+1 704 324 3322",
        notes="Daily lunch buffet 11 AM-3 PM. " + "; ".join(
            f"Item {idx} ${idx}.99" for idx in range(1, 11)
        ),
    )
    project = _complete_project().model_copy(update={"fields": fields})
    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}

    assert facts["schedule"] == "Daily | 11 AM-3 PM"
    assert facts["detail_010"] == "Item 10 $10.99"


def test_render_concept_previews_accepts_ten_item_menu(tmp_path):
    fields = FlyerRequestFields(
        event_or_business_name="Daily Lunch Specials",
        contact_info="+1 704 324 3322",
        notes="Daily lunch buffet 11 AM-3 PM. " + "; ".join(
            f"Item {idx} ${idx}.99" for idx in range(1, 11)
        ),
    )
    project = _complete_project().model_copy(update={"fields": fields})

    specs = render_concept_previews(project, tmp_path)

    assert specs[0].path.exists()
    assert validate_text_manifest_file(specs[0].path, project_id=project.project_id, project_version=project.version).ok


def test_exact_dessert_graduation_long_menu_renders_integrated_catalog_and_finals(tmp_path, monkeypatch):
    """Production regression 2026-06-07 23:53 UTC.

    The router clarified Flyer vs Catering and the fact extractor locked all 14
    suffix-priced dessert rows, but the renderer failed closed before sending
    because the old menu cap only allowed about ten rows. Customer-supplied
    itemized price lists should use the integrated catalog poster path with
    all item facts in the controlled prompt, not the low-quality background
    overlay fallback and not invented package prices.
    """
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setattr(render_module, "_openrouter_image_bytes", lambda *a, **k: _png_bytes())
    project = _dessert_graduation_project()

    assert render_module._integrated_poster_eligible(project) is True
    assert render_module._background_only_eligible(project) is False
    facts = collect_text_facts(project)
    detail_texts = [fact.text for fact in facts if fact.fact_id.startswith("detail_")]

    assert len(detail_texts) == 14
    assert "Mango tresleches - half tray $75" in detail_texts
    assert "Khalakhandh - 100 count $100" in detail_texts

    preview = render_concept_previews(
        project,
        tmp_path,
        model="google/gemini-2.5-flash-image",
    )[0]

    assert inspect_rendered_asset(
        preview.path,
        expected_width=1080,
        expected_height=1350,
        mime_type="image/png",
    ).ok is True
    assert validate_text_manifest_file(
        preview.path,
        project_id=project.project_id,
        project_version=project.version,
        output_format="concept_preview",
    ).ok is True
    manifest = json.loads(Path(f"{preview.path}.text.json").read_text(encoding="utf-8"))
    expected_text = "\n".join(fact["text"] for fact in manifest["expected_facts"])
    assert "Mango tresleches - half tray $75" in expected_text
    assert "Khalakhandh - 100 count $100" in expected_text

    selected = project.model_copy(update={
        "status": "awaiting_final_approval",
        "selected_concept_id": "C1",
        "concepts": [FlyerConcept(
            concept_id="C1",
            title="Best",
            style_summary="Generated",
            preview_asset_id="A0001",
            prompt="",
            created_at=project.created_at,
        )],
        "assets": [FlyerAsset(
            asset_id="A0001",
            kind="concept_preview",
            source="rendered",
            path=str(preview.path),
            mime_type="image/png",
            sha256="a" * 64,
            original_message_id=project.original_message_id,
            received_at=project.created_at,
        )],
    })
    finals = render_final_package(selected, tmp_path / "finals")

    assert {spec.output_format for spec in finals} == {
        "whatsapp_image",
        "instagram_post",
        "instagram_story",
        "printable_pdf",
    }
    square = next(spec for spec in finals if spec.output_format == "instagram_post")
    assert inspect_rendered_asset(
        square.path,
        expected_width=1080,
        expected_height=1080,
        mime_type="image/png",
    ).ok is True


def test_exact_dessert_graduation_long_menu_uses_integrated_catalog_prompt(monkeypatch):
    """Dense grounded customer menus need a designed poster path, not text-overlay fallback."""
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    project = _dessert_graduation_project()

    assert render_module._compact_menu_overlay_allowed(project) is True
    assert render_module._integrated_poster_eligible(project) is True
    assert render_module._background_only_eligible(project) is False

    prompt = render_module._image_prompt(
        project,
        concept_id="C1",
        output_format="concept_preview",
        size=(1080, 1350),
    )

    assert "Menu items to feature - exactly 14 items" in prompt
    assert "Create exactly 14 menu item cards" in prompt
    assert "Each listed item must appear once and only once" in prompt
    assert "full restaurant/menu poster" in prompt
    assert "item cards with food imagery and prices" in prompt
    assert "decorative BACKGROUND image only" not in prompt


def test_menu_overlay_fails_closed_when_items_overflow_the_card_panel(tmp_path, monkeypatch):
    """Under background-only the overlay is the SOLE source of item facts, so a
    menu too large to fit the card panel must FAIL CLOSED (→ manual review), not
    silently drop items. Normal flow already caps items at MAX_DETAIL_FACTS (and
    `_detail_clauses` raises beyond that); this verifies the overlay's own backstop
    if that cap is ever bypassed.
    """
    from PIL import Image
    import pytest as _pytest

    project = _english_project()
    payload = dict(render_module._menu_overlay_payload(project))
    payload["items"] = [f"Famous Item {i} - $9.99" for i in range(1, 25)]
    monkeypatch.setattr(render_module, "_menu_overlay_payload", lambda _p: payload)
    source = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1350), (30, 90, 120)).save(source)
    with _pytest.raises(FlyerRenderError, match="menu overlay cannot fit all"):
        apply_critical_text_overlay(
            project, source, tmp_path / "out.png", size=(1080, 1350), output_format="concept_preview",
        )


def test_renderer_fails_if_title_cannot_fit_without_truncation(tmp_path):
    fields = _complete_project().fields.model_copy(update={
        "event_or_business_name": " ".join(["VeryLongFestivalName"] * 16),
    })
    project = _complete_project().model_copy(update={"fields": fields})

    try:
        render_concept_previews(project, tmp_path)
    except FlyerRenderError as e:
        assert "critical text facts do not fit" in str(e)
    else:
        raise AssertionError("expected long title to fail instead of silently truncating")


def test_inspect_rendered_asset_rejects_blank_or_wrong_size_png(tmp_path):
    from PIL import Image

    good = tmp_path / "good.png"
    render_concept_previews(_complete_project(), tmp_path)
    good = tmp_path / "F0001-C1-preview.png"
    result = inspect_rendered_asset(good, expected_width=1080, expected_height=1350, mime_type="image/png")
    assert result.ok is True

    blank = tmp_path / "blank.png"
    Image.new("RGB", (1080, 1350), (255, 255, 255)).save(blank)
    blank_result = inspect_rendered_asset(blank, expected_width=1080, expected_height=1350, mime_type="image/png")
    assert blank_result.ok is False
    assert any("blank" in item or "variance" in item for item in blank_result.blockers)

    wrong = tmp_path / "wrong.png"
    Image.new("RGB", (200, 200), (255, 0, 0)).save(wrong)
    wrong_result = inspect_rendered_asset(wrong, expected_width=1080, expected_height=1350, mime_type="image/png")
    assert wrong_result.ok is False
    assert any("dimensions" in item for item in wrong_result.blockers)


def test_menu_overlay_payload_surfaces_offer_and_promo_as_extras():
    """Offers / promotion_end are required visible facts that the menu item cards
    don't render — they must reach the title card via `extras` so the
    deterministic overlay (and therefore visual QA) covers them, not just items.
    Regression for the F0112/F0113 `missing required visible fact: offer:0` class.
    """
    locked = [
        FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen",
                        source="customer_text", required=True, confidence=1.0, source_message_id="wamid.x"),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Dosa Special Night",
                        source="customer_text", required=True, confidence=1.0, source_message_id="wamid.x"),
        FlyerLockedFact(fact_id="offer:0", label="Offer", value="Pick Any 4 Dosa for $20",
                        source="customer_text", required=True, confidence=1.0, source_message_id="wamid.x"),
    ]
    for i, (n, p) in enumerate([("Ghee Karam Dosa", "$6.99"), ("Benne Dosa", "$7.49")]):
        locked.append(FlyerLockedFact(fact_id=f"item:{i}:name", label="Item", value=n,
                                      source="customer_text", required=True, confidence=1.0, source_message_id="wamid.x"))
        locked.append(FlyerLockedFact(fact_id=f"item:{i}:price", label="Price", value=p,
                                      source="customer_text", required=True, confidence=1.0, source_message_id="wamid.x"))
    project = _complete_project().model_copy(update={"locked_facts": locked})
    payload = _menu_overlay_payload(project)

    assert payload["items"], "menu path should engage with locked item facts"
    assert payload["business"] == "Lakshmi's Kitchen"
    assert "Pick Any 4 Dosa for $20" in payload["extras"]
    # Items are shown as cards, not duplicated into the title-card extras.
    assert not any("Ghee Karam Dosa" in str(e) for e in payload["extras"])


def test_concept_preview_model_branch_applies_critical_text_overlay(tmp_path, monkeypatch):
    """New-flyer concept generation must composite the deterministic critical-text
    overlay (title/items/prices) at the CONCEPT stage so visual QA runs on
    deterministic text, not the image model's garbled rendering. Regression for the
    100% `visual_qa_failed` incident (F0113 = missing campaign_title/offer/items).
    """
    project = _complete_project()  # non-integrated → critical overlay applies
    monkeypatch.setattr(render_module, "_openrouter_image_bytes", lambda *a, **k: _png_bytes())
    overlay_targets: list[str] = []
    real_overlay = render_module._apply_critical_text_overlay

    def _spy(proj, source, target, *, size, output_format):
        overlay_targets.append(str(target))
        return real_overlay(proj, source, target, size=size, output_format=output_format)

    monkeypatch.setattr(render_module, "_apply_critical_text_overlay", _spy)
    specs = render_concept_previews(project, tmp_path, model="google/gemini-2.5-flash-image")
    assert overlay_targets, "concept model-branch must apply the deterministic critical-text overlay"
    assert str(specs[0].path) in overlay_targets
    assert specs[0].path.exists()
    assert inspect_rendered_asset(
        specs[0].path, expected_width=1080, expected_height=1350, mime_type="image/png"
    ).ok is True


def test_render_model_pdf_fallback_applies_critical_overlay(tmp_path, monkeypatch):
    """The PDF branch (size=None) of the model fallback must composite the
    deterministic overlay too — otherwise the slice-2 background-only prompt
    ships a printable PDF with no copy/prices/contact.
    """
    project = _complete_project()  # non-integrated → overlay composited
    monkeypatch.setattr(render_module, "_openrouter_image_bytes", lambda *a, **k: _png_bytes())
    overlay_targets: list[str] = []
    real_overlay = render_module._apply_critical_text_overlay

    def _spy(proj, source, target, *, size, output_format):
        overlay_targets.append(str(target))
        return real_overlay(proj, source, target, size=size, output_format=output_format)

    monkeypatch.setattr(render_module, "_apply_critical_text_overlay", _spy)
    pdf_path = tmp_path / "F0001-printable_pdf.pdf"
    render_module._render_model(
        project, pdf_path, concept_id="C1", output_format="printable_pdf",
        size=None, model="google/gemini-2.5-flash-image", quality="medium",
    )
    assert overlay_targets, "PDF model fallback must apply the deterministic critical overlay"
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 1000


def test_apply_critical_text_overlay_changes_model_background_pixels(tmp_path):
    from PIL import Image, ImageChops

    source = tmp_path / "model.png"
    target = tmp_path / "overlay.png"
    Image.new("RGB", (1080, 1350), (30, 90, 120)).save(source)

    apply_critical_text_overlay(
        _complete_project(),
        source,
        target,
        size=(1080, 1350),
        output_format="concept_preview",
    )

    before = Image.open(source).convert("RGB")
    after = Image.open(target).convert("RGB")
    diff = ImageChops.difference(before, after)
    assert diff.getbbox() is not None
    assert inspect_rendered_asset(target, expected_width=1080, expected_height=1350, mime_type="image/png").ok is True


def test_render_final_package_creates_expected_formats(tmp_path):
    project = _complete_project()
    specs = render_final_package(project, tmp_path)
    by_format = {spec.output_format: spec for spec in specs}
    assert set(by_format) == {
        "whatsapp_image",
        "instagram_post",
        "instagram_story",
        "printable_pdf",
    }
    assert by_format["whatsapp_image"].path.suffix == ".png"
    assert by_format["printable_pdf"].path.suffix == ".pdf"
    for spec in specs:
        assert spec.path.exists()
        assert spec.path.stat().st_size > 1000
        assert validate_text_manifest_file(
            spec.path,
            project_id=project.project_id,
            project_version=project.version,
            output_format=spec.output_format,
        ).ok is True


def test_background_only_final_package_reapplies_overlay_per_format(tmp_path, monkeypatch):
    """A freshly-generated background-only preview (raw + overlay within seconds)
    must have the critical overlay RE-APPLIED from the raw at each output size,
    not be cropped as a direct poster — under the no-text contract the overlay is
    the sole text source, so cropping square/story would drop required facts.
    """
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(render_module, "_openrouter_image_bytes", lambda *a, **k: _png_bytes())
    project = _complete_project()
    specs = render_concept_previews(project, tmp_path, model="google/gemini-2.5-flash-image")
    preview_path = specs[0].path
    asset = FlyerAsset(
        asset_id="A0001", kind="concept_preview", source="rendered", path=str(preview_path),
        mime_type="image/png", sha256="c" * 64, original_message_id="m1",
        received_at=datetime.now(timezone.utc),
    )
    selected = project.model_copy(update={
        "selected_concept_id": "C1",
        "concepts": [FlyerConcept(
            concept_id="C1", title="Best Design", style_summary="Generated",
            preview_asset_id="A0001", prompt="", created_at=datetime.now(timezone.utc),
        )],
        "assets": [asset],
    })

    overlay_formats: list[str] = []
    real_overlay = render_module._apply_critical_text_overlay

    def _spy(proj, source, target, *, size, output_format):
        overlay_formats.append(output_format)
        return real_overlay(proj, source, target, size=size, output_format=output_format)

    monkeypatch.setattr(render_module, "_apply_critical_text_overlay", _spy)
    render_final_package(selected, tmp_path / "finals")
    # Re-applied for the non-4:5 formats (story/pdf) at their own sizes → not cropped.
    assert "instagram_story" in overlay_formats
    assert "printable_pdf" in overlay_formats


def test_background_only_final_honors_edited_preview_over_stale_raw(tmp_path, monkeypatch):
    """Even for a background-only-eligible project, a preview edited/regenerated
    well after its raw (stale raw) must be honored directly, not rebuilt from the
    stale raw — the tight freshness window distinguishes this from a sub-second
    generated composite."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "F0001-C1-preview.png"
    stale_raw = tmp_path / "F0001-C1-preview.raw.png"
    approved.write_bytes(_png_bytes(color=(20, 120, 40)))
    stale_raw.write_bytes(_png_bytes(color=(140, 20, 20)))
    import os
    old = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc).timestamp()
    new = datetime(2026, 5, 17, 13, 0, tzinfo=timezone.utc).timestamp()  # 1h newer → edited
    os.utime(stale_raw, (old, old))
    os.utime(approved, (new, new))
    project = _complete_project().model_copy(update={
        "assets": [FlyerAsset(asset_id="A0001", kind="concept_preview", source="rendered",
                              path=str(approved), mime_type="image/png", sha256="a" * 64,
                              original_message_id="m1", received_at=datetime.now(timezone.utc))],
        "concepts": [FlyerConcept(concept_id="C1", title="Best", style_summary="g",
                                  preview_asset_id="A0001", prompt="", created_at=datetime.now(timezone.utc))],
        "selected_concept_id": "C1",
    })
    assert render_module._background_only_eligible(project) is True
    specs = render_final_package(project, tmp_path / "finals", model="openai/gpt-5.4-image-2", quality="high")
    whatsapp = next(s for s in specs if s.output_format == "whatsapp_image")
    from PIL import Image
    with Image.open(whatsapp.path) as final_img, Image.open(approved) as appr, Image.open(stale_raw) as raw:
        assert final_img.getpixel((20, 20)) == appr.getpixel((20, 20))
        assert final_img.getpixel((20, 20)) != raw.getpixel((20, 20))


def test_renderer_blocks_missing_required_fields(tmp_path):
    project = _complete_project().model_copy(
        update={"fields": FlyerRequestFields(event_or_business_name="Bathukamma")}
    )
    try:
        render_concept_previews(project, tmp_path)
    except FlyerRenderError as e:
        assert "event_date" in str(e)
    else:
        raise AssertionError("expected FlyerRenderError")


def test_image_prompt_uses_schedule_instead_of_blank_date_for_recurring_offer():
    project = _complete_project()
    fields = project.fields.model_copy(update={
        "event_date": None,
        "notes": "Starts from 8 AM on both Saturday and Sunday.",
    })
    project = project.model_copy(update={"fields": fields})
    prompt = _image_prompt(project, concept_id="C1", output_format="whatsapp_image", size=(1080, 1350))
    assert "Schedule: Starts from 8 AM on both Saturday and Sunday" in prompt
    assert "Saturday and Sunday" in prompt
    assert "Date: " not in prompt


def test_image_prompt_extracts_through_day_range_schedule_for_recurring_offer():
    project = _complete_project()
    fields = project.fields.model_copy(update={
        "event_date": None,
        "event_time": None,
        "notes": (
            "I\u2019d like you to help me with evening snacks flier from 4 PM to 7 PM. "
            "Include 5 top South Indian snack items. Its Wednesday through Saturday event"
        ),
    })
    project = project.model_copy(update={"fields": fields})

    prompt = _image_prompt(project, concept_id="C1", output_format="whatsapp_image", size=(1080, 1350))

    assert "Schedule: Wednesday Through Saturday | 4 PM TO 7 PM" in prompt
    assert "Date: " not in prompt


def test_image_prompt_extracts_single_day_every_week_schedule():
    raw_request = (
        "Create a Special Biryani's Flyer using golden background. "
        "This promotion runs on Thursday of every week. Use address and phone number stored."
    )
    project = _complete_project().model_copy(update={
        "raw_request": raw_request,
        "fields": FlyerRequestFields(
            event_or_business_name="Special Biryani's",
            event_date=None,
            event_time=None,
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes=raw_request,
        ),
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="whatsapp_image", size=(1080, 1350))
    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}

    assert facts["schedule"] == "Thursday every week"
    assert "Schedule: Thursday every week" in prompt
    assert "Do not invent delivery, catering, payment, ordering-channel, or service-availability claims" in prompt
    assert "Date: " not in prompt


def test_image_prompt_uses_locked_schedule_as_source_of_truth():
    project = _complete_project().model_copy(update={
        "raw_request": "Create a flyer with stored details.",
        "fields": FlyerRequestFields(
            event_or_business_name="Special Biryani's",
            event_date=None,
            event_time=None,
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes="Create a flyer with stored details.",
        ),
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile"),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text"),
            FlyerLockedFact(fact_id="schedule", label="Schedule", value="Thursday every week", source="customer_text"),
        ],
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="whatsapp_image", size=(1080, 1350))
    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}

    assert facts["schedule"] == "Thursday every week"
    assert "Schedule: Thursday every week" in prompt


def test_image_prompt_uses_clean_biryani_copy_without_price_instruction_leak():
    raw_request = (
        "Create a Special Biryani's Flyer with all famous south indian biryani's included, "
        "add Price as $16.99 for chicken and $18.99 for goat. "
        "This promotion runs on Wednesday and Thursday of every week. "
        "Use address and phone number stored."
    )
    project = _complete_project().model_copy(update={
        "raw_request": raw_request,
        "fields": FlyerRequestFields(
            event_or_business_name="Special Biryani's",
            event_date=None,
            event_time=None,
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes=raw_request,
        ),
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile"),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text"),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile"),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile"),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Chicken Biryani", source="customer_text"),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$16.99", source="customer_text"),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Goat Biryani", source="customer_text"),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$18.99", source="customer_text"),
        ],
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="whatsapp_image", size=(1080, 1350))
    facts = {fact.fact_id: fact.text for fact in collect_text_facts(project)}

    assert facts["schedule"] == "Wednesday and Thursday every week"
    assert "Title is the campaign/product/service headline; Business/brand is the account identity." in prompt
    assert "Schedule: Wednesday and Thursday every week" in prompt
    assert "Chicken Biryani - $16.99" in prompt
    assert "Goat Biryani - $18.99" in prompt
    assert "add Price as" not in prompt
    assert "all famous south indian" not in prompt
    assert not any("add price" in text.lower() for text in facts.values())


def test_image_prompt_for_indochinese_menu_uses_structured_item_cards_not_raw_request():
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    names = [
        "Veg Manchurian",
        "Gobi Manchurian",
        "Chili Paneer",
        "Hakka Noodles",
        "Schezwan Fried Rice",
        "Chili Garlic Noodles",
        "Manchow Soup",
        "Spring Rolls",
    ]
    locked = [
        FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Indo-Chinese Specials", source="customer_text", required=True),
        FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="schedule", label="Schedule", value="Wednesday", source="customer_text", required=True),
    ]
    for idx, name in enumerate(names):
        locked.append(FlyerLockedFact(fact_id=f"item:{idx}:name", label="Item", value=name, source="customer_text", required=True))
        locked.append(FlyerLockedFact(fact_id=f"item:{idx}:price", label="Price", value="$9.99", source="customer_text", required=True))
    raw = (
        "Create a flyer for Indo-Chinese specials on Wednesday. Include 8 famous "
        "Indo-Chinese items. Any item priced at $9.99. Use Address and phone number stored."
    )
    project = FlyerProject(
        project_id="F0120",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-indochinese",
        raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="Indo-Chinese Specials",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes=raw,
            style_preference="professional local food menu flyer",
        ),
        locked_facts=locked,
    )

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Menu items to feature - exactly 8 items:" in prompt
    assert "Create exactly 8 menu item cards" in prompt
    assert "- Veg Manchurian - $9.99" in prompt
    assert "- Spring Rolls - $9.99" in prompt
    assert "Campaign scene direction (menu product close-up)" in prompt
    assert "family discovery" not in prompt.lower()
    assert "Use product-specific close-up food imagery based on the listed menu items" in prompt
    assert "Avoid generic buffet, dining-family, or unrelated stock-food scenes" in prompt
    assert raw not in prompt


def test_image_prompt_for_south_indian_snacks_rejects_family_scene_collision():
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    raw = (
        "Create a flyer for south indian snacks.Include these items. "
        "Gavvalu 1 Lb $8.99, Chekkalu 1 lb $8.99 and Arisalu 1 Lb $10.99"
    )
    project = FlyerProject(
        project_id="F0122",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-snacks",
        raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="South Indian Snacks",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes=raw,
            style_preference=(
                "professional local food menu flyer with appetizing photography, "
                "strong price readability, and brand-forward retail design"
            ),
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="South Indian Snacks", source="customer_text", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Chekkalu 1 lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:name", label="Item", value="Arisalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:price", label="Price", value="$10.99", source="customer_text", required=True),
        ],
    )

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Campaign scene direction (menu product close-up)" in prompt
    assert "Gavvalu 1 Lb - $8.99" in prompt
    assert "Chekkalu 1 lb - $8.99" in prompt
    assert "Arisalu 1 Lb - $10.99" in prompt
    assert "generic family" in prompt
    assert "family discovery" not in prompt.lower()
    assert "happy local family or community" not in prompt.lower()


def test_menu_overlay_payload_drops_aggregate_raw_item_sentence():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    raw = (
        "Create a flyer for south indian snacks.Include these items. "
        "Gavvalu 1 Lb $8.99, Chekkalu 1 lb $8.99 and Arisalu 1 Lb $10.99"
    )
    project = FlyerProject(
        project_id="F0123",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-snacks",
        raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="South Indian Snacks",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes=raw,
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="South Indian Snacks", source="customer_text", required=True),
            FlyerLockedFact(
                fact_id="offer:0",
                label="Offer",
                value="Gavvalu 1 Lb $8.99, Chekkalu 1 lb $8.99, Arisalu 1 Lb $10.99",
                source="customer_text",
                required=True,
            ),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Chekkalu 1 lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:name", label="Item", value="Arisalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:price", label="Price", value="$10.99", source="customer_text", required=True),
        ],
    )

    payload = _menu_overlay_payload(project)

    assert payload["items"] == [
        "Gavvalu 1 Lb $8.99",
        "Chekkalu 1 lb $8.99",
        "Arisalu 1 Lb $10.99",
    ]
    assert payload["extras"] == []


def test_menu_overlay_payload_drops_same_price_aggregate_item_sentence():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    raw = "Veg Manchurian $9.99, Spring Rolls $9.99, Hakka Noodles $9.99"
    project = FlyerProject(
        project_id="F0125",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-same-price",
        raw_request=raw,
        fields=FlyerRequestFields(event_or_business_name="Indo-Chinese Snacks", notes=raw),
        locked_facts=[
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Indo-Chinese Snacks", source="customer_text", required=True),
            FlyerLockedFact(fact_id="offer:0", label="Offer", value=raw, source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Veg Manchurian", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$9.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Spring Rolls", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$9.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:name", label="Item", value="Hakka Noodles", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:price", label="Price", value="$9.99", source="customer_text", required=True),
        ],
    )

    payload = _menu_overlay_payload(project)

    assert payload["items"] == [
        "Veg Manchurian $9.99",
        "Spring Rolls $9.99",
        "Hakka Noodles $9.99",
    ]
    assert payload["extras"] == []


def test_menu_overlay_fits_ten_item_phone_preview(tmp_path):
    from PIL import Image

    source = tmp_path / "background.png"
    target = tmp_path / "overlay.png"
    Image.new("RGB", (1080, 1350), (120, 70, 40)).save(source)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    locked = [
        FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Indo-Chinese Specials", source="customer_text", required=True),
    ]
    for idx in range(10):
        locked.append(FlyerLockedFact(fact_id=f"item:{idx}:name", label="Item", value=f"Item {idx + 1}", source="customer_text", required=True))
        locked.append(FlyerLockedFact(fact_id=f"item:{idx}:price", label="Price", value="$9.99", source="customer_text", required=True))
    project = FlyerProject(
        project_id="F0126",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-ten-items",
        raw_request="Create a flyer with 10 snacks, all $9.99",
        fields=FlyerRequestFields(event_or_business_name="Indo-Chinese Specials", contact_info="+17329837841"),
        locked_facts=locked,
    )

    apply_critical_text_overlay(project, source, target, size=(1080, 1350), output_format="concept_preview")

    assert inspect_rendered_asset(target, expected_width=1080, expected_height=1350, mime_type="image/png").ok is True


def test_menu_overlay_fits_many_item_final_formats(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(render_module, "_openrouter_image_bytes", lambda *a, **k: _png_bytes())
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    locked = [
        FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Snack Specials", source="customer_text", required=True),
    ]
    for idx in range(8):
        locked.append(FlyerLockedFact(fact_id=f"item:{idx}:name", label="Item", value=f"Snack {idx + 1}", source="customer_text", required=True))
        locked.append(FlyerLockedFact(fact_id=f"item:{idx}:price", label="Price", value=f"${idx + 5}.99", source="customer_text", required=True))
    project = FlyerProject(
        project_id="F0127",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-eight-items",
        raw_request="Create a flyer with eight snack specials",
        fields=FlyerRequestFields(event_or_business_name="Snack Specials", contact_info="+17329837841"),
        locked_facts=locked,
    )
    preview = render_concept_previews(project, tmp_path, model="google/gemini-2.5-flash-image")[0]
    selected = project.model_copy(update={
        "status": "awaiting_final_approval",
        "selected_concept_id": "C1",
        "concepts": [FlyerConcept(
            concept_id="C1",
            title="Best",
            style_summary="Generated",
            preview_asset_id="A0001",
            prompt="",
            created_at=now,
        )],
        "assets": [FlyerAsset(
            asset_id="A0001",
            kind="concept_preview",
            source="rendered",
            path=str(preview.path),
            mime_type="image/png",
            sha256="a" * 64,
            original_message_id="m-eight-items",
            received_at=now,
        )],
    })

    specs = render_final_package(selected, tmp_path / "finals")

    assert {spec.output_format for spec in specs} == {
        "whatsapp_image",
        "instagram_post",
        "instagram_story",
        "printable_pdf",
    }


def test_system_overlay_fallback_contains_menu_card_renderer():
    src = Path("src/agents/flyer/render.py").read_text(encoding="utf-8")

    assert '"menu_payload": _menu_overlay_payload(project)' in src
    assert 'menu=spec.get("menu_payload") or {}' in src
    assert 'if menu.get("items"):' in src
    assert 'menu.get("schedule")' in src
    assert 'fill=(255,253,244,245)' in src
    assert 'menu overlay cannot fit all' in src


def test_system_overlay_fallback_draws_menu_schedule(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "background.png"
    target = tmp_path / "overlay.png"
    Image.new("RGB", (1080, 1350), (120, 70, 40)).save(source)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0132",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-breakfast",
        raw_request=(
            "Create a weekend breakfast specials flyer for Lakshmi's Kitchen. "
            "Include Idlie, Medhu Vada. Any item price is at $8.99. "
            "Only available on Saturday and Sunday from 8 AM to 11 AM."
        ),
        fields=FlyerRequestFields(
            event_or_business_name="Weekend Breakfast Specials",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="en",
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Weekend Breakfast Specials", source="customer_text", required=True),
            FlyerLockedFact(fact_id="schedule", label="Schedule", value="Saturday and Sunday from 8 AM to 11 AM", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Idlie", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Medhu Vada", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$8.99", source="customer_text", required=True),
        ],
    )

    real_exists = render_module.Path.exists
    real_run = render_module.subprocess.run

    def fake_exists(path):
        if path.as_posix().endswith("/usr/bin/python3"):
            return True
        return real_exists(path)

    def run_with_current_python(args, **kwargs):
        if args[:2] == ["/usr/bin/python3", "-c"]:
            args = [sys.executable, "-c", args[2], args[3]]
        return real_run(args, **kwargs)

    monkeypatch.setattr(render_module, "_load_pillow", lambda: None)
    monkeypatch.setattr(render_module.Path, "exists", fake_exists)
    monkeypatch.setattr(render_module.subprocess, "run", run_with_current_python)

    render_module._apply_critical_text_overlay(
        project,
        source,
        target,
        size=(1080, 1350),
        output_format="whatsapp_image",
    )

    with Image.open(target).convert("RGB") as img:
        schedule_region = img.crop((55, 350, 540, 392))
        pixels = schedule_region.load()
        region_w, region_h = schedule_region.size
        dark_pixels = sum(
            1
            for y in range(region_h)
            for x in range(region_w)
            for r, g, b in [pixels[x, y]]
            if r < 170 and g < 95 and b < 105
        )

    assert dark_pixels > 80


def test_system_overlay_fallback_shared_price_reference_uses_poster_layout(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    source = tmp_path / "background.png"
    target = tmp_path / "overlay.png"
    with Image.new("RGB", (1080, 1350), (82, 42, 30)) as src_img:
        ImageDraw.Draw(src_img).rectangle((540, 80, 1040, 280), fill=(255, 255, 255))
        src_img.save(source)

    real_exists = render_module.Path.exists
    real_run = render_module.subprocess.run

    def fake_exists(path):
        if path.as_posix().endswith("/usr/bin/python3"):
            return True
        return real_exists(path)

    def run_with_current_python(args, **kwargs):
        if args[:2] == ["/usr/bin/python3", "-c"]:
            args = [sys.executable, "-c", args[2], args[3]]
        return real_run(args, **kwargs)

    monkeypatch.setattr(render_module, "_load_pillow", lambda: None)
    monkeypatch.setattr(render_module.Path, "exists", fake_exists)
    monkeypatch.setattr(render_module.subprocess, "run", run_with_current_python)

    render_module._apply_critical_text_overlay(
        _triveni_shared_price_reference_project(),
        source,
        target,
        size=(1080, 1350),
        output_format="whatsapp_image",
    )

    with Image.open(target).convert("RGB") as img:
        width, height = img.size
        title_region = img.crop((40, 30, int(width * 0.60), int(height * 0.24)))
        masthead = img.crop((int(width * 0.16), int(height * 0.025), int(width * 0.84), int(height * 0.105)))
        top_sides = img.crop((0, int(height * 0.02), width, int(height * 0.18)))
        source_header_region = img.crop((int(width * 0.50), int(height * 0.06), width - 40, int(height * 0.24)))
        item_region = img.crop((40, int(height * 0.42), width - 40, height - 70))
        middle_region = img.crop((40, int(height * 0.46), width - 40, int(height * 0.68)))
        bottom_strip = img.crop((0, int(height * 0.82), width, height))
        deal_band = img.crop((40, int(height * 0.745), width - 40, int(height * 0.905)))
        title_pixels = list(title_region.getdata())
        masthead_pixels = list(masthead.getdata())
        side_pixels = list(top_sides.getdata())
        source_header_pixels = list(source_header_region.getdata())
        pixels = list(item_region.getdata())
        middle_pixels = list(middle_region.getdata())
        bottom_pixels = list(bottom_strip.getdata())
        deal_pixels = list(deal_band.getdata())
    title_near_white = sum(1 for r, g, b in title_pixels if r > 225 and g > 218 and b > 195)
    ivory = sum(1 for r, g, b in masthead_pixels if r > 205 and g > 190 and b > 155)
    red_energy = sum(1 for r, g, b in side_pixels if r > 125 and g < 70 and b < 60)
    masthead_gold_energy = sum(1 for r, g, b in side_pixels if r > 190 and 120 < g < 225 and b < 115)
    leaked_source_header = sum(1 for r, g, b in source_header_pixels if r > 225 and g > 225 and b > 225)
    near_white = sum(1 for r, g, b in pixels if r > 225 and g > 218 and b > 195)
    gold_outline = sum(1 for r, g, b in pixels if r > 210 and 140 < g < 230 and b < 120)
    middle_changed = sum(1 for pixel in middle_pixels if pixel != (82, 42, 30))
    dark_bottom = sum(1 for r, g, b in bottom_pixels if r < 45 and g < 35 and b < 35)
    premium_red = sum(1 for r, g, b in deal_pixels if r > 95 and g < 72 and b < 72)
    premium_gold = sum(1 for r, g, b in deal_pixels if r > 185 and 120 < g < 220 and b < 110)
    deal_changed = sum(1 for pixel in deal_pixels if pixel != (82, 42, 30))

    assert title_near_white / max(1, len(title_pixels)) < 0.30
    assert ivory / max(1, len(masthead_pixels)) > 0.18
    assert red_energy / max(1, len(side_pixels)) > 0.004
    assert masthead_gold_energy / max(1, len(side_pixels)) > 0.006
    assert leaked_source_header / max(1, len(source_header_pixels)) < 0.02
    assert near_white / max(1, len(pixels)) < 0.45
    assert gold_outline / max(1, len(pixels)) < 0.12
    assert middle_changed / max(1, len(middle_pixels)) > 0.12
    assert dark_bottom / max(1, len(bottom_pixels)) < 0.45
    assert deal_changed / max(1, len(deal_pixels)) > 0.70
    assert premium_red / max(1, len(deal_pixels)) > 0.18
    assert premium_gold / max(1, len(deal_pixels)) > 0.01


def test_menu_overlay_uses_large_lightweight_poster_panels(tmp_path):
    from PIL import Image

    source = tmp_path / "background.png"
    target = tmp_path / "overlay.png"
    Image.new("RGB", (1080, 1350), (120, 70, 40)).save(source)
    project = _english_project().model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="South Indian Snacks",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="en",
            notes="Gavvalu 1 Lb $8.99, Chekkalu 1 lb $8.99 and Arisalu 1 Lb $10.99",
            style_preference="professional local food menu flyer",
        ),
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="South Indian Snacks", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Chekkalu 1 lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:name", label="Item", value="Arisalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:price", label="Price", value="$10.99", source="customer_text", required=True),
        ],
    })

    apply_critical_text_overlay(project, source, target, size=(1080, 1350), output_format="concept_preview")

    img = Image.open(target).convert("RGB")
    bottom_panel_sample = img.getpixel((540, 1080))
    assert sum(bottom_panel_sample) > 420
    assert inspect_rendered_asset(target, expected_width=1080, expected_height=1350, mime_type="image/png").ok is True


def test_image_prompt_preserves_explicit_family_festival_menu_scene():
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    raw = "Create a Diwali family festival flyer for South Indian snacks. Include Gavvalu 1 Lb $8.99."
    project = FlyerProject(
        project_id="F0123",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-festival-snacks",
        raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="Diwali Family Snacks",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes=raw,
            style_preference="Diwali festival theme for families with appetizing snacks",
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Diwali Family Snacks", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
        ],
    )

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Campaign scene direction (family discovery)" in prompt
    assert "happy local family or community" in prompt
    assert "Campaign scene direction (menu product close-up)" not in prompt
    assert "Gavvalu 1 Lb - $8.99" in prompt


def test_image_prompt_service_menu_does_not_get_food_product_scene():
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    raw = "Create a flyer for Chloe Hair Studio service menu. Haircut $20, Perms $80."
    project = FlyerProject(
        project_id="F0124",
        status="generating_concepts",
        customer_phone="+19045550123",
        created_at=now,
        updated_at=now,
        original_message_id="m-service-menu",
        raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="Chloe Hair Studio",
            contact_info="+1 904 555 0123",
            notes=raw,
            style_preference="modern salon service menu with clean premium styling",
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Chloe Hair Studio", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Service Menu", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Haircut", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$20", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Perms", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$80", source="customer_text", required=True),
        ],
    )

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "modern US salon" in prompt
    assert "Campaign scene direction (menu product close-up)" not in prompt
    assert "food/snacks as the hero background" not in prompt
    assert "premium restaurant ambiance" not in prompt
    assert "polished, category-appropriate service imagery" in prompt


def test_image_prompt_skips_blank_optional_fields_for_price_list():
    project = _complete_project()
    fields = FlyerRequestFields(
        event_or_business_name="Lakshmi's Kitchen",
        contact_info="+1 9802005022",
        notes="Items: Bobbatlu $2/piece; Murukulu $12/lb",
    )
    project = project.model_copy(update={"fields": fields})
    prompt = _image_prompt(project, concept_id="C1", output_format="whatsapp_image", size=(1080, 1350))
    assert "Contact: +1 9802005022" in prompt
    assert "Bobbatlu - $2" in prompt
    assert "Date: " not in prompt
    assert "Time: " not in prompt
    assert "Venue: " not in prompt


def test_image_prompt_enforces_explicit_english_only_language_constraint():
    project = _complete_project().model_copy(update={
        "raw_request": (
            "Create a Ganesh festival flyer. Language: English only. "
            "Do NOT use Telugu, Hindi, or any regional Indian language."
        ),
        "fields": FlyerRequestFields(
            event_or_business_name="Ganesh Festival",
            venue_or_location="Community Hall",
            contact_info="+17329837841",
            preferred_language="en",
            notes=(
                "Language: English only. Do NOT use Telugu, Hindi, or any regional Indian language."
            ),
        ),
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    # English-only + background-only eligible: the overlay draws the English facts
    # and the model renders NO text/script at all, so non-English text is
    # structurally impossible (stronger than the old "use English only" hint).
    assert "Do not render any text, script, or words in the background" in prompt
    assert "do NOT render flyer text" in prompt


def test_image_prompt_does_not_turn_weekend_special_badge_into_schedule():
    project = _complete_project().model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Lakshmis Kitchn",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            notes=(
                "Headline: Family Combo Feast. Include badges Fresh, Homemade, Weekend Special. "
                "Use green, gold, and warm rustic textures."
            ),
        ),
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Schedule: Weekend Special" not in prompt
    assert "Weekend Special. Use green" not in prompt


def test_image_prompt_sanitizes_exact_customer_facts_from_model_context():
    project = _complete_project()
    fields = project.fields.model_copy(update={
        "notes": "Non-veg combo $14.99. Call +1 904 555 0123 on 2026-10-10.",
    })
    project = project.model_copy(update={"fields": fields})
    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    assert "Non-veg combo - $14.99" in prompt
    assert "Contact: +1 904 555 0123" in prompt
    assert "Date: 2026-10-10" in prompt
    assert "Call +1 904 555 0123 on 2026-10-10" not in prompt


def test_fresh_generation_prompt_does_not_carry_stale_revision_history():
    project = _complete_project().model_copy(update={
        "status": "intake_started",
        "raw_request": "Create a premium chicken flyer for Fresh Meats.",
        "fields": FlyerRequestFields(
            event_or_business_name="Fresh Meats",
            venue_or_location="9551 Baymeadows Rd Suite 18",
            contact_info="+19048626362",
            notes="Premium Clean Chicken. Clean bird. Strong life.",
            style_preference="premium organic-style chicken grocery flyer",
        ),
        "revisions": [
            FlyerRevision(
                revision_id="R001",
                message_id="old-1",
                requested_at=datetime.now(timezone.utc),
                request_text="Use the old dosa night layout with orange festival colors.",
            ),
            FlyerRevision(
                revision_id="R002",
                message_id="old-2",
                requested_at=datetime.now(timezone.utc),
                request_text="Make the stale biryani flyer darker and keep the old template.",
            ),
        ],
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Fresh Meats" in prompt
    assert "old dosa night layout" not in prompt
    assert "stale biryani flyer" not in prompt


def test_revision_generation_prompt_keeps_current_revision_notes():
    project = _complete_project().model_copy(update={
        "status": "revising_design",
        "revisions": [
            FlyerRevision(
                revision_id="R001",
                message_id="rev-1",
                requested_at=datetime.now(timezone.utc),
                request_text="Make the logo larger and brighten the hero food photo.",
            ),
        ],
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Make the logo larger" in prompt
    assert "brighten the hero food photo" in prompt


def test_image_prompt_sanitizes_style_and_brand_asset_notes(tmp_path, monkeypatch):
    customers_path = tmp_path / "customers.json"
    logo = tmp_path / "brand_assets" / "CUST0001" / "B0001.png"
    logo.parent.mkdir(parents=True)
    logo.write_bytes(b"logo bytes")
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "next_brand_asset_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Triveni",
            "business_address": "300 S Polk St",
            "public_phone": "+17043243322",
            "business_whatsapp_number": "+17043243322",
            "authorized_request_numbers": ["+19045550123"],
            "business_category": "restaurant",
            "preferred_language": "en",
            "plan_id": "starter",
            "status": "active",
            "created_at": "2026-05-15T00:00:00Z",
            "updated_at": "2026-05-15T00:00:00Z",
            "billing_provider": "manual",
            "payment_checkout_url": "",
            "notes": "",
            "brand_assets": [{
                "asset_id": "B0001",
                "kind": "logo",
                "path": str(logo),
                "mime_type": "image/png",
                "sha256": "a" * 64,
                "original_message_id": "logo1",
                "received_at": "2026-05-15T00:00:00Z",
                "active": True,
                "notes": "old price $14.99 phone +1 222 333 4444 date 2026-10-10"
            }]
        }],
        "onboarding_sessions": []
    }), encoding="utf-8")
    monkeypatch.setattr("agents.flyer.render.CUSTOMERS_PATH", customers_path)
    project = _complete_project()
    project = project.model_copy(update={
        "fields": project.fields.model_copy(update={
            "style_preference": "premium $14.99 +1 222 333 4444 2026-10-10",
        })
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "$14.99" not in prompt
    assert "+1 222 333 4444" not in prompt
    assert "premium [price] [phone]" in prompt
    assert "Date: 2026-10-10" in prompt


def test_image_prompt_includes_customer_brand_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = tmp_path / "customers.json"
    logo = tmp_path / "brand_assets" / "CUST0001" / "B0001.png"
    logo.parent.mkdir(parents=True)
    logo.write_bytes(b"logo bytes")
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "next_brand_asset_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Triveni",
            "business_address": "300 S Polk St",
            "public_phone": "+17043243322",
            "business_whatsapp_number": "+17043243322",
            "authorized_request_numbers": ["+19045550123"],
            "business_category": "restaurant",
            "preferred_language": "en",
            "plan_id": "starter",
            "status": "active",
            "created_at": "2026-05-15T00:00:00Z",
            "updated_at": "2026-05-15T00:00:00Z",
            "billing_provider": "manual",
            "payment_checkout_url": "",
            "notes": "",
            "brand_assets": [{
                "asset_id": "B0001",
                "kind": "logo",
                "path": str(logo),
                "mime_type": "image/png",
                "sha256": "a" * 64,
                "original_message_id": "logo1",
                "received_at": "2026-05-15T00:00:00Z",
                "active": True,
                "notes": "logo"
            }]
        }],
        "onboarding_sessions": []
    }), encoding="utf-8")
    monkeypatch.setattr("agents.flyer.render.CUSTOMERS_PATH", customers_path)

    prompt = _image_prompt(_complete_project(), concept_id="C1", output_format="whatsapp_image", size=(1080, 1350))

    assert "Customer brand assets to honor" in prompt
    assert "saved logo reference" in prompt
    assert "B0001" not in prompt
    assert "B0001.png" not in prompt


def test_render_control_fact_disables_saved_customer_brand_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    customers_path = tmp_path / "customers.json"
    logo = tmp_path / "brand_assets" / "CUST0001" / "B0001.png"
    logo.parent.mkdir(parents=True)
    logo.write_bytes(b"logo bytes")
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "next_brand_asset_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Triveni",
            "business_address": "300 S Polk St",
            "public_phone": "+17043243322",
            "business_whatsapp_number": "+17043243322",
            "authorized_request_numbers": ["+19045550123"],
            "business_category": "restaurant",
            "preferred_language": "en",
            "plan_id": "starter",
            "status": "active",
            "created_at": "2026-05-15T00:00:00Z",
            "updated_at": "2026-05-15T00:00:00Z",
            "billing_provider": "manual",
            "payment_checkout_url": "",
            "notes": "",
            "brand_assets": [{
                "asset_id": "B0001",
                "kind": "logo",
                "path": str(logo),
                "mime_type": "image/png",
                "sha256": "a" * 64,
                "original_message_id": "logo1",
                "received_at": "2026-05-15T00:00:00Z",
                "active": True,
                "notes": "logo"
            }]
        }],
        "onboarding_sessions": []
    }), encoding="utf-8")
    monkeypatch.setattr("agents.flyer.render.CUSTOMERS_PATH", customers_path)
    project = _complete_project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(
                fact_id="render:disable_brand_assets",
                label="Render Control",
                value="true",
                source="system",
            ),
        ]
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="whatsapp_image", size=(1080, 1350))
    message = _image_message_content(project, concept_id="C1", output_format="whatsapp_image", size=(1080, 1350))

    assert "logo: B0001" not in prompt
    assert isinstance(message, str)


def test_project_reference_image_is_sent_to_image_model(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    ref = tmp_path / "assets" / "F0001-reference.png"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(b"reference image bytes")
    project = _complete_project()
    asset = FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path=str(ref),
        mime_type="image/png",
        sha256="b" * 64,
        original_message_id="template1",
        received_at=datetime.now(timezone.utc),
    )
    project = project.model_copy(update={"assets": [asset]})

    content = _image_message_content(project, concept_id="C1", output_format="whatsapp_image", size=(1080, 1350))

    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert "uploaded reference image" in content[0]["text"]
    assert "A0001" not in content[0]["text"]
    assert "do not redesign from scratch" in content[0]["text"].lower()


def test_render_concept_previews_can_still_render_three_when_configured(tmp_path):
    project = _complete_project()
    specs = render_concept_previews(project, tmp_path, concept_count=3)
    assert [s.concept_id for s in specs] == ["C1", "C2", "C3"]


def test_asset_manifest_hashes_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project()
    specs = render_concept_previews(project, tmp_path)
    assets = build_asset_manifest(
        specs,
        first_asset_number=1,
        source="rendered",
        original_message_id="wamid.flyer.1",
    )
    assert [asset.asset_id for asset in assets] == ["A0001"]
    assert all(len(asset.sha256) == 64 for asset in assets)


def test_openrouter_image_renderer_posts_modalities_and_writes_data_url(tmp_path, monkeypatch):
    project = _complete_project()
    project = project.model_copy(
        update={
            "fields": project.fields.model_copy(
                update={
                    "notes": "Menu items: Idly (3 PCS) - $7.99; Sambar Idly (2 Pcs) - $7.99. Starts from 8 AM Saturday and Sunday."
                }
            )
        }
    )
    requests = []

    class _Resp:
        def __enter__(self):
            png = base64.b64encode(_png_bytes()).decode("ascii")
            body = {"choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{png}"}}]}}]}
            self._body = json.dumps(body).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    def _fake_urlopen(req, timeout):
        requests.append((req, timeout, json.loads(req.data.decode("utf-8"))))
        return _Resp()

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _fake_urlopen)
    specs = render_concept_previews(
        project,
        tmp_path,
        model="openai/gpt-5-image",
        quality="medium",
    )
    assert len(requests) == 1
    assert requests[0][2]["modalities"] == ["image", "text"]
    assert requests[0][2]["max_tokens"] == 4096
    assert requests[0][2]["image_config"]["aspect_ratio"] == "4:5"
    prompt = requests[0][2]["messages"][0]["content"]
    assert "Controlled customer copy" in prompt
    assert "$7.99" in prompt
    assert "recurring schedule" in prompt
    assert specs[0].path.read_bytes().startswith(b"\x89PNG")


def test_openrouter_image_renderer_retries_incomplete_chunk_read(tmp_path, monkeypatch):
    project = _complete_project()
    calls = {"count": 0}

    class _Resp:
        def __init__(self, fail: bool):
            self.fail = fail

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            if self.fail:
                raise http.client.IncompleteRead(b'{"choices":')
            png = base64.b64encode(_png_bytes()).decode("ascii")
            return json.dumps({
                "choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{png}"}}]}}]
            }).encode("utf-8")

    def _fake_urlopen(_req, timeout):
        calls["count"] += 1
        return _Resp(fail=calls["count"] == 1)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _fake_urlopen)

    specs = render_concept_previews(
        project,
        tmp_path,
        model="openai/gpt-5-image",
        quality="medium",
    )

    assert calls["count"] == 2
    assert specs[0].path.read_bytes().startswith(b"\x89PNG")


def test_telugu_poster_prompt_is_background_only_overlay_owns_text():
    project = _complete_project().model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Weekend Breakfast Specials",
            event_time="08:00",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            preferred_language="te",
            notes=(
                'Create a breakfast flyer with these items "Poori with Chicken $14.99, '
                'Kheema Dosa $12.99, Pesarattu with Upma $11.99, Vada with Sambar $12.99, '
                'Mysore Masala Dosa $11.99". Timings 8 AM to 11 AM. Thursday to Sunday. Use Telugu language.'
            ),
        ),
        "raw_request": (
            'Create a breakfast flyer with these items "Poori with Chicken $14.99, '
            'Kheema Dosa $12.99, Pesarattu with Upma $11.99, Vada with Sambar $12.99, '
            'Mysore Masala Dosa $11.99". Timings 8 AM to 11 AM. Thursday to Sunday. Use Telugu language.'
        ),
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Create a complete, finished customer-ready poster flyer" in prompt
    # Telugu is now background-only eligible: the overlay owns ALL text (it renders
    # facts in their own script via _font), and the model garbles non-English — so
    # the model produces a TEXTLESS background and the deterministic overlay draws
    # the text, instead of the model painting garbled/hallucinated Telugu.
    assert "decorative BACKGROUND image only" in prompt
    assert "do NOT render them as text" in prompt
    # The exact facts are still present as imagery context.
    assert "Weekend Breakfast Specials" in prompt
    assert "Poori with Chicken - $14.99" in prompt
    assert "Kheema Dosa - $12.99" in prompt
    assert "+17329837841" in prompt
    # The language hint is now IMAGERY-only under background-only — it must NOT
    # instruct the model to render Telugu text (which it garbles).
    assert "Reflect Telugu / South-Indian cultural styling in the imagery" in prompt
    assert "Use Telugu as the primary flyer language" not in prompt


def test_background_only_contract_stays_for_non_integrated_paths(tmp_path, monkeypatch):
    """Background-only stays for localized/non-menu cases and for style-only
    references. Integrated typed menus are opt-in only after eval.
    """
    from PIL import Image as _Image
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    english_service = _complete_project().model_copy(update={"fields": FlyerRequestFields(
        event_or_business_name="Lakshmi's Kitchen", contact_info="+1 732 983 7841",
        preferred_language="en", notes="Grand opening this week")})
    p_en = _image_prompt(english_service, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    assert "decorative BACKGROUND image only" in p_en
    assert "do NOT render them as text" in p_en
    assert render_module._background_only_eligible(english_service) is True

    english_menu = _english_project()
    assert render_module._integrated_poster_eligible(english_menu) is False
    assert render_module._background_only_eligible(english_menu) is True

    # Language no longer gates: the overlay renders facts in their own script
    # (Telugu via _font), and the model garbles non-English, so Telugu is ALSO
    # background-only eligible (the model stops painting garbled/hallucinated text).
    telugu = english_menu.model_copy(update={
        "raw_request": english_menu.raw_request + " Use Telugu language.",
        "fields": english_menu.fields.model_copy(update={
            "preferred_language": "te",
            "notes": english_menu.fields.notes + "; Use Telugu language.",
        }),
    })
    p_te = _image_prompt(telugu, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    assert "decorative BACKGROUND image only" in p_te
    assert render_module._background_only_eligible(telugu) is True

    img = tmp_path / "assets" / "ref.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    _Image.new("RGB", (10, 10), (1, 2, 3)).save(img)

    def _asset(kind: str) -> FlyerAsset:
        return FlyerAsset(asset_id="A0001", kind=kind, source="whatsapp", path=str(img),
                          mime_type="image/png", sha256="b" * 64, original_message_id="m1",
                          received_at=datetime.now(timezone.utc))

    # A logo/brand asset alone stays eligible (it's visual identity, not text).
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    assert render_module._integrated_poster_eligible(
        english_menu.model_copy(update={"assets": [_asset("logo")]})) is True
    monkeypatch.delenv("FLYER_ALLOW_INTEGRATED_POSTER")
    # A reference IMAGE attached only as a STYLE template (the request does not ask
    # to read items out of it; copy is in fields) stays eligible — the overlay
    # owns the known text.
    assert render_module._background_only_eligible(
        english_menu.model_copy(update={"assets": [_asset("reference_image")]})) is True
    # A reference IMAGE the request asks to EXTRACT items from → not eligible
    # (those items live in the image, only the model can read them).
    extract_req = english_menu.model_copy(update={
        "assets": [_asset("reference_image")],
        "raw_request": "Create a flyer and extract items and prices from this sample flyer.",
    })
    assert render_module._background_only_eligible(extract_req) is False


def test_simple_english_typed_menu_defaults_to_background_overlay():
    project = _english_project()

    assert render_module._integrated_poster_eligible(project) is False

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "decorative BACKGROUND image only" in prompt
    assert "Do NOT draw any text" in prompt
    assert "Build a full restaurant/menu poster" not in prompt


def test_shared_price_reference_background_prompt_matches_premium_overlay_zones():
    project = _triveni_shared_price_reference_project()

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "decorative BACKGROUND image only" in prompt
    assert "premium Indian street-snack poster BACKGROUND" in prompt
    assert "top third dark and text-free" in prompt
    assert "left-middle calm enough for a snack list overlay" in prompt
    assert "bottom fifth calm enough for a red/gold combo-price band overlay" in prompt
    assert "strongest food hero cluster in the center/right and lower-right" in prompt


def test_simple_english_typed_menu_can_opt_into_integrated_poster_prompt(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    project = _english_project()

    assert render_module._integrated_poster_eligible(project) is True

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "complete, finished customer-ready poster flyer" in prompt
    assert "Build a full restaurant/menu poster" in prompt
    assert "Item cards must look like designed menu tiles" in prompt
    assert "keep every footer character at least 6% of the canvas height above the bottom edge" in prompt
    assert "Render the following text exactly" in prompt
    assert "Do not add secondary brand names" in prompt
    assert "freshness/availability claims" in prompt
    assert "Dosa - $6.99" in prompt
    assert "Idli - $5.99" in prompt
    assert "decorative BACKGROUND image only" not in prompt
    assert "do NOT render them as text" not in prompt


def test_english_combo_offer_can_opt_into_integrated_poster_prompt(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    now = datetime(2026, 6, 5, tzinfo=timezone.utc)
    raw = (
        "Can we do meal combo flyer for veg and non veg with prices 49.99 for non veg combo "
        "includes 2 non veg curries, 1 chicken pulav or chicken Biryani and 1 dessert. "
        "And a veg combo 39.99 includes 2 veg curries, 1 dessert on the occasion of "
        "Memorial Day weekend"
    )
    project = FlyerProject(
        project_id="F0067",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="wamid.combo",
        raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="Veg And Non Veg",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="en",
            notes=raw,
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Memorial Day Weekend Meal Combos", source="customer_text", required=True),
            FlyerLockedFact(
                fact_id="offer:0",
                label="Offer",
                value="Non Veg Combo: $49.99 includes 2 non veg curries, 1 chicken pulav or chicken Biryani, and 1 dessert",
                source="customer_text",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="offer:1",
                label="Offer",
                value="Veg Combo: $39.99 includes 2 veg curries and 1 dessert",
                source="customer_text",
                required=True,
            ),
        ],
    )

    assert render_module._integrated_poster_eligible(project) is True
    assert render_module._background_only_eligible(project) is False

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Build a complete finished poster flyer" in prompt
    assert "keep every footer character at least 6% of the canvas height above the bottom edge" in prompt
    assert "Render the following text exactly" in prompt
    assert "Do not add secondary brand names" in prompt
    assert "freshness/availability claims" in prompt
    assert "Memorial Day Weekend Meal Combos" in prompt
    assert "Non Veg Combo: $49.99" in prompt
    assert "Veg Combo: $39.99" in prompt
    assert "decorative BACKGROUND image only" not in prompt
    assert "do NOT render them as text" not in prompt


def test_integrated_poster_is_not_used_for_telugu_or_reference_extraction(tmp_path, monkeypatch):
    from PIL import Image as _Image
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    english = _english_project()

    telugu = english.model_copy(update={
        "raw_request": english.raw_request + " Use Telugu language.",
        "fields": english.fields.model_copy(update={
            "preferred_language": "te",
            "notes": english.fields.notes + "; Use Telugu language.",
        }),
    })
    assert render_module._integrated_poster_eligible(telugu) is False
    assert render_module._background_only_eligible(telugu) is True

    img = tmp_path / "assets" / "ref.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    _Image.new("RGB", (10, 10), (1, 2, 3)).save(img)
    asset = FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path=str(img),
        mime_type="image/png",
        sha256="b" * 64,
        original_message_id="m1",
        received_at=datetime.now(timezone.utc),
    )
    extract_req = english.model_copy(update={
        "assets": [asset],
        "raw_request": "Create a flyer and extract items and prices from this sample flyer.",
    })
    assert render_module._integrated_poster_eligible(extract_req) is False
    assert render_module._background_only_eligible(extract_req) is False


def test_integrated_poster_allows_english_typed_menu_even_with_localized_profile_language(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    project = _english_project().model_copy(update={
        "fields": _english_project().fields.model_copy(update={"preferred_language": "te"})
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert render_module._integrated_poster_eligible(project) is True
    assert "Build a full restaurant/menu poster" in prompt
    assert "Use English text only for this typed menu poster" in prompt
    assert "Reflect Telugu / South-Indian cultural styling in the imagery" not in prompt


def test_integrated_poster_concept_keeps_model_image_without_overlay(tmp_path, monkeypatch):
    raw_png = _png_bytes(size=(1792, 2240), color=(24, 70, 38))

    class _Resp:
        def __enter__(self):
            png = base64.b64encode(raw_png).decode("ascii")
            body = {"choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{png}"}}]}}]}
            self._body = json.dumps(body).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", lambda *_args, **_kwargs: _Resp())

    spec = render_concept_previews(
        _english_project(),
        tmp_path,
        model="openai/gpt-5.4-image-2",
        quality="high",
    )[0]

    raw_path = spec.path.with_name(f"{spec.path.stem}.raw.png")
    assert not raw_path.exists()
    assert inspect_rendered_asset(spec.path, expected_width=1080, expected_height=1350, mime_type="image/png").ok is True


def test_direct_poster_prompt_does_not_make_request_sentence_flyer_copy(monkeypatch):
    monkeypatch.setattr("agents.flyer.render._project_reference_assets", lambda project: project.assets)
    project = _complete_project().model_copy(update={
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path="/opt/shift-agent/state/flyer/assets/reference.jpg",
                mime_type="image/jpeg",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
        "fields": FlyerRequestFields(
            event_or_business_name="Weekend Breakfast Specials",
            event_time="08:00",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            preferred_language="te",
            notes=(
                "Hey! Create a breakfast flyer from 8 AM to 11 AM, its Thursday to Sunday. "
                "Items to include in the flyer(Attaching a flyer, take items from breakfast section for the new flyer)."
            ),
        ),
        "raw_request": (
            "Hey! Create a breakfast flyer from 8 AM to 11 AM, its Thursday to Sunday. "
            "Items to include in the flyer(Attaching a flyer, take items from breakfast section for the new flyer)."
        ),
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Hey! Create a breakfast flyer" not in prompt
    assert "take items from breakfast section" in prompt
    assert "Do not render the request wording itself" in prompt
    assert "read the attached reference image" in prompt


def test_direct_poster_prompt_extracts_items_and_prices_from_sample_reference(monkeypatch):
    monkeypatch.setattr("agents.flyer.render._project_reference_assets", lambda project: project.assets)
    project = _complete_project().model_copy(update={
        "assets": [
            FlyerAsset(
                asset_id="A0002",
                kind="reference_image",
                source="whatsapp",
                path="/opt/shift-agent/state/flyer/assets/sample-grocery.jpg",
                mime_type="image/jpeg",
                sha256="b" * 64,
                original_message_id="wamid.sample",
                received_at=datetime.now(timezone.utc),
            )
        ],
        "fields": FlyerRequestFields(
            event_or_business_name="Diwali Grocery Sale",
            event_date="2026-05-22",
            event_time="May 22 to May 25",
            venue_or_location="90 Brybar Saint Johns FL",
            contact_info="+17329837841",
            preferred_language="te",
            notes="Extract items and prices from the sample flyer attached.",
        ),
        "raw_request": "Diwali Grocery Sale. Extract items and prices from the sample flyer attached.",
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "extract items and prices" in prompt.lower()
    assert "read the attached reference image" in prompt
    assert "visible item names and prices" in prompt
    assert "Do not replace them with generic grocery categories" in prompt


def test_reference_with_materialized_facts_uses_textless_overlay_not_model_text(monkeypatch):
    monkeypatch.setattr("agents.flyer.render._project_reference_assets", lambda project: project.assets)
    base = _triveni_shared_price_reference_project()
    reference_asset = FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path="/opt/shift-agent/state/flyer/assets/triveni-reference.jpg",
        mime_type="image/jpeg",
        sha256="a" * 64,
        original_message_id="wamid.reference",
        received_at=base.created_at,
    )
    project = base.model_copy(update={
        "assets": [reference_asset],
        "raw_request": "Extract items and prices from this sample flyer and use Lakshmi's Kitchen theme.",
        "reference_extractions": [
            FlyerReferenceExtraction(
                asset_id="A0001",
                role="menu_reference",
                provider="test_vision",
                status="ok",
                extracted_facts=[
                    fact for fact in base.locked_facts
                    if fact.source == "reference_vision"
                ],
                extracted_at=base.created_at,
            )
        ],
    })

    assert render_module._needs_reference_extraction(project) is False
    assert render_module._background_only_eligible(project) is True

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "decorative BACKGROUND image only" in prompt
    assert "Use the attached reference image for visual style only" in prompt
    assert "Render the following text exactly" not in prompt


def test_style_only_reference_with_materialized_facts_stays_background_overlay(monkeypatch):
    monkeypatch.setattr("agents.flyer.render._project_reference_assets", lambda project: project.assets)
    base = _triveni_shared_price_reference_project()
    reference_asset = FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path="/opt/shift-agent/state/flyer/assets/triveni-reference.jpg",
        mime_type="image/jpeg",
        sha256="a" * 64,
        original_message_id="wamid.reference",
        received_at=base.created_at,
    )
    project = base.model_copy(update={
        "assets": [reference_asset],
        "raw_request": "Use as reference. Same flyer for Lakshmi's Kitchen, same content, Lakshmi's Kitchen theme.",
        "reference_extractions": [
            FlyerReferenceExtraction(
                asset_id="A0001",
                role="menu_reference",
                provider="test_vision",
                status="ok",
                extracted_facts=[
                    fact for fact in base.locked_facts
                    if fact.source == "reference_vision"
                ],
                extracted_at=base.created_at,
            )
        ],
    })

    assert render_module._needs_reference_extraction(project) is False
    assert render_module._integrated_poster_eligible(project) is False
    assert render_module._background_only_eligible(project) is True

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "decorative BACKGROUND image only" in prompt
    assert "Render the following text exactly" not in prompt


def test_style_only_reference_image_is_sent_to_model_for_art_direction(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr("agents.flyer.render._project_reference_assets", lambda project: project.assets)
    base = _triveni_shared_price_reference_project()
    reference = tmp_path / "assets" / "street-snack-reference.png"
    reference.parent.mkdir()
    reference.write_bytes(_png_bytes())
    reference_asset = FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path=str(reference),
        mime_type="image/png",
        sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
        original_message_id="wamid.reference",
        received_at=base.created_at,
    )
    project = base.model_copy(update={
        "assets": [reference_asset],
        "raw_request": "Use as reference. Same flyer for Lakshmi's Kitchen, same content, Lakshmi's Kitchen theme.",
        "reference_extractions": [
            FlyerReferenceExtraction(
                asset_id="A0001",
                role="menu_reference",
                provider="test_vision",
                status="ok",
                extracted_facts=[
                    fact for fact in base.locked_facts
                    if fact.source == "reference_vision"
                ],
                extracted_at=base.created_at,
            )
        ],
    })

    content = _image_message_content(
        project,
        concept_id="C1",
        output_format="concept_preview",
        size=(1080, 1350),
    )

    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "For this reference-only request" in content[0]["text"]
    image_parts = [part for part in content if part.get("type") == "image_url"]
    assert len(image_parts) == 1
    data_url = image_parts[0]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")
    sent_bytes = base64.b64decode(data_url.split(",", 1)[1])
    assert sent_bytes != reference.read_bytes()
    from PIL import Image
    with Image.open(io.BytesIO(sent_bytes)) as proxied:
        assert proxied.size[0] <= 192
        assert proxied.size[1] < 320


def test_source_edit_preview_calls_openai_edit_api_with_reference_image(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    reference.write_bytes(_png_bytes())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00. Add Any Item for $9.99.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })
    requests = []

    class _Resp:
        def __enter__(self):
            png = base64.b64encode(_png_bytes(color=(40, 90, 50))).decode("ascii")
            self._body = json.dumps({"data": [{"b64_json": png}]}).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    def _fake_urlopen(req, timeout):
        requests.append((req, timeout, req.data))
        return _Resp()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _fake_urlopen)

    spec = render_source_edit_preview(
        project,
        tmp_path,
        provider="openai",
        model="gpt-image-1",
        quality="medium",
    )

    assert spec.kind == "concept_preview"
    assert spec.path.read_bytes().startswith(b"\x89PNG")
    assert len(requests) == 1
    req, timeout, body = requests[0]
    assert req.full_url.endswith("/v1/images/edits")
    assert timeout == 180
    assert req.headers["Authorization"] == "Bearer sk-test"
    assert b'name="model"\r\n\r\ngpt-image-1' in body
    assert b'name="input_fidelity"\r\n\r\nhigh' in body
    assert b'name="prompt"' in body
    assert b"Remove extra 08:00" in body
    assert b'name="image"; filename="reference.png"' in body
    manifest = json.loads(Path(f"{spec.path}.text.json").read_text(encoding="utf-8"))
    assert manifest["verification_mode"] == "source_edit_overlay_recomposed"
    assert "re-composed" in " ".join(manifest["warnings"])
    qa = validate_text_manifest_file(
        spec.path,
        project_id=project.project_id,
        project_version=project.version,
        output_format="concept_preview",
    )
    assert qa.ok is True
    assert any("re-composed" in warning for warning in qa.warnings)


def test_source_edit_preview_applies_deterministic_overlay(tmp_path, monkeypatch):
    """Render-side robustness: source-edit output must carry the deterministic
    critical-text overlay (new-flyer parity), so the generative-edit model's
    omissions/garbles don't ship to QA. The raw model edit is preserved separately
    so the final package can re-apply the overlay per format."""
    reference = tmp_path / "reference.png"
    reference.write_bytes(_png_bytes())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })

    overlay_calls = []

    def _record_overlay(proj, source, target, *, size, output_format):
        overlay_calls.append({"size": size, "output_format": output_format})
        Path(target).write_bytes(Path(source).read_bytes())  # passthrough so inspect finds a file

    monkeypatch.setattr(render_module, "apply_critical_text_overlay", _record_overlay)

    class _Resp:
        def __enter__(self):
            png = base64.b64encode(_png_bytes(color=(40, 90, 50))).decode("ascii")
            self._body = json.dumps({"data": [{"b64_json": png}]}).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", lambda req, timeout: _Resp())

    spec = render_source_edit_preview(project, tmp_path, provider="openai", model="gpt-image-1", quality="medium")

    assert overlay_calls, "source-edit preview must apply the deterministic critical-text overlay"
    assert overlay_calls[0]["output_format"] == "concept_preview"
    assert overlay_calls[0]["size"] == (1080, 1350)
    # raw model edit preserved separately for final-package re-overlay
    assert render_module._raw_background_path(spec.path).exists()
    assert spec.path.read_bytes().startswith(b"\x89PNG")
    # #5 manifest truthfulness: the output is re-composed (deterministic overlay),
    # so the manifest DECLARES the facts and must NOT claim pixel-preserving
    # "source_edit_integrity_only".
    manifest = json.loads(Path(f"{spec.path}.text.json").read_text(encoding="utf-8"))
    assert manifest["verification_mode"] == "source_edit_overlay_recomposed"
    assert manifest["expected_facts"], "source-edit manifest must declare the overlaid facts, not empty"


def test_source_edit_integrity_manifest_allows_long_edit_instruction(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    long_instruction = (
        "Edit uploaded flyer/source artwork. Customer requested: Apply these changes to the existing flyer: "
        "change the background to rich golden color and keep the pictures of 2 male and 2 female celebrities "
        "with different hairstyles each and keep prices as $40, $60, $80, $100"
    )
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": long_instruction,
        "fields": FlyerRequestFields(
            event_or_business_name="Chloe hair studio",
            venue_or_location="11111 Gainsborough Ct, Fairfax, VA, 22030",
            contact_info="+19803826497",
            preferred_language="en",
            notes=long_instruction,
        ),
    })
    artifact = tmp_path / "F0001-C1-preview.png"
    artifact.write_bytes(b"edited image bytes")

    manifest_path = write_text_manifest(
        project,
        artifact,
        output_format="concept_preview",
        selected_concept_id="C1",
        source_path=artifact,
        verification_mode="source_edit_integrity_only",
        warnings=["inspect visually"],
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["verification_mode"] == "source_edit_integrity_only"
    qa = validate_text_manifest_file(
        artifact,
        project_id=project.project_id,
        project_version=project.version,
        output_format="concept_preview",
    )
    assert qa.ok is True


def test_source_edit_preview_calls_openrouter_with_reference_image(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    reference.write_bytes(_png_bytes())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00. Add Any Item for $9.99.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })
    requests = []

    class _Resp:
        def __enter__(self):
            data_url = "data:image/png;base64," + base64.b64encode(_png_bytes(color=(40, 90, 50))).decode("ascii")
            self._body = json.dumps({
                "choices": [{
                    "message": {
                        "images": [{"image_url": {"url": data_url}}],
                    },
                }],
            }).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    def _fake_urlopen(req, timeout):
        requests.append((req, timeout, json.loads(req.data.decode("utf-8"))))
        return _Resp()

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _fake_urlopen)

    spec = render_source_edit_preview(
        project,
        tmp_path,
        provider="openrouter",
        model="openai/gpt-5.4-image-2",
        quality="high",
    )

    assert spec.kind == "concept_preview"
    assert spec.path.read_bytes().startswith(b"\x89PNG")
    assert len(requests) == 1
    req, timeout, payload = requests[0]
    assert "openrouter.ai" in req.full_url
    assert timeout == 180
    assert req.headers["Authorization"] == "Bearer sk-or-test"
    assert payload["model"] == "openai/gpt-5.4-image-2"
    assert payload["modalities"] == ["image", "text"]
    assert payload["max_tokens"] == 4096
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert "Remove extra 08:00" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    manifest = json.loads(Path(f"{spec.path}.text.json").read_text(encoding="utf-8"))
    assert manifest["verification_mode"] == "source_edit_overlay_recomposed"


def test_openrouter_source_edit_retries_incomplete_chunk_read(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    reference.write_bytes(_png_bytes())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })
    calls = {"count": 0}

    class _Resp:
        def __init__(self, fail: bool):
            self.fail = fail

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            if self.fail:
                raise http.client.IncompleteRead(b'{"choices":')
            data_url = "data:image/png;base64," + base64.b64encode(_png_bytes(color=(40, 90, 50))).decode("ascii")
            return json.dumps({
                "choices": [{"message": {"images": [{"image_url": {"url": data_url}}]}}],
            }).encode("utf-8")

    def _fake_urlopen(_req, timeout):
        calls["count"] += 1
        return _Resp(fail=calls["count"] == 1)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setattr("agents.flyer.render.time.sleep", lambda _seconds: None)

    spec = render_source_edit_preview(
        project,
        tmp_path,
        provider="openrouter",
        model="openai/gpt-5.4-image-2",
        quality="high",
    )

    assert calls["count"] == 2
    assert spec.path.read_bytes().startswith(b"\x89PNG")


def test_openrouter_source_edit_retries_connection_error(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    reference.write_bytes(_png_bytes())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })
    calls = {"count": 0}

    class _Resp:
        def __enter__(self):
            data_url = "data:image/png;base64," + base64.b64encode(_png_bytes(color=(40, 90, 50))).decode("ascii")
            self._body = json.dumps({
                "choices": [{"message": {"images": [{"image_url": {"url": data_url}}]}}],
            }).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    def _fake_urlopen(_req, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.URLError("temporary DNS failure")
        return _Resp()

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setattr("agents.flyer.render.time.sleep", lambda _seconds: None)

    spec = render_source_edit_preview(
        project,
        tmp_path,
        provider="openrouter",
        model="openai/gpt-5.4-image-2",
        quality="high",
    )

    assert calls["count"] == 2
    assert spec.path.read_bytes().startswith(b"\x89PNG")


def test_openrouter_source_edit_does_not_retry_http_400(tmp_path, monkeypatch):
    import pytest as _pytest
    reference = tmp_path / "reference.png"
    reference.write_bytes(_png_bytes())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })
    calls = {"count": 0}

    def _fake_urlopen(_req, timeout):
        calls["count"] += 1
        raise urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            400,
            "bad request",
            {},
            io.BytesIO(b"invalid payload"),
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _fake_urlopen)

    with _pytest.raises(FlyerRenderError, match="OpenRouter source edit HTTP 400"):
        render_source_edit_preview(
            project,
            tmp_path,
            provider="openrouter",
            model="openai/gpt-5.4-image-2",
            quality="high",
        )

    assert calls["count"] == 1


def test_source_edit_preview_omitted_provider_fails_closed_to_manual_review(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    reference.write_bytes(_png_bytes())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Change date.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })
    urls = []

    class _Resp:
        def __enter__(self):
            data_url = "data:image/png;base64," + base64.b64encode(_png_bytes()).decode("ascii")
            self._body = json.dumps({
                "choices": [{
                    "message": {"images": [{"image_url": {"url": data_url}}]},
                }],
            }).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", lambda req, timeout=None: urls.append(req.full_url) or _Resp())

    import pytest as _pytest
    with _pytest.raises(FlyerRenderError, match="manual review"):
        render_source_edit_preview(project, tmp_path, model="openai/gpt-5.4-image-2", quality="high")

    assert urls == []


def test_openrouter_source_edit_fails_closed_on_remote_url_response(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    reference.write_bytes(_png_bytes())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Change date.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })

    class _Resp:
        def __enter__(self):
            self._body = json.dumps({
                "choices": [{
                    "message": {
                        "images": [{"image_url": {"url": "https://example.com/generated.png"}}],
                    },
                }],
            }).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", lambda _req, timeout=None: _Resp())

    import pytest as _pytest
    with _pytest.raises(FlyerRenderError, match="base64 image data"):
        render_source_edit_preview(
            project,
            tmp_path,
            provider="openrouter",
            model="openai/gpt-5.4-image-2",
            quality="high",
        )


def test_openrouter_source_edit_fails_closed_on_malformed_json_shape(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    reference.write_bytes(_png_bytes())
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Change date.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })

    class _Resp:
        def __enter__(self):
            self._body = json.dumps({
                "choices": [{
                    "message": {
                        "images": ["not-a-dict"],
                    },
                }],
            }).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", lambda _req, timeout=None: _Resp())

    import pytest as _pytest
    with _pytest.raises(FlyerRenderError, match="invalid image shape"):
        render_source_edit_preview(
            project,
            tmp_path,
            provider="openrouter",
            model="openai/gpt-5.4-image-2",
            quality="high",
        )


def test_openrouter_source_edit_fails_closed_on_placeholder_key(tmp_path, monkeypatch):
    import agents.flyer.render as render_mod

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    reference = tmp_path / "reference.png"
    reference.write_bytes(_png_bytes())
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Change date.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })

    def tripwire(*_args, **_kwargs):
        raise AssertionError("network request issued with PLACEHOLDER OpenRouter key")

    monkeypatch.setenv("OPENROUTER_API_KEY", "PLACEHOLDER-key")
    monkeypatch.setattr(render_mod.urllib.request, "urlopen", tripwire)

    import pytest as _pytest
    with _pytest.raises(FlyerRenderError, match="OPENROUTER_API_KEY"):
        render_source_edit_preview(
            project,
            tmp_path,
            provider="openrouter",
            model="openai/gpt-5.4-image-2",
            quality="high",
        )


def test_openrouter_image_bytes_fails_closed_on_placeholder_key(monkeypatch):
    def tripwire(*_args, **_kwargs):
        raise AssertionError("network request issued with placeholder OpenRouter key")

    monkeypatch.setattr(render_module, "_read_env_value", lambda _name: "PLACEHOLDER-key")
    monkeypatch.setattr(render_module.urllib.request, "urlopen", tripwire)

    import pytest as _pytest
    with _pytest.raises(FlyerRenderError, match="placeholder"):
        render_module._openrouter_image_bytes(
            _english_project(),
            concept_id="C1",
            output_format="concept_preview",
            size=(1080, 1350),
            model="openrouter/test",
            quality="medium",
        )


def test_openai_source_edit_bytes_fails_closed_on_lowercase_placeholder_key(tmp_path, monkeypatch):
    import agents.flyer.render as render_mod

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    ref_path = tmp_path / "F9102-ref.png"
    ref_path.write_bytes(b"fake")

    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    project = FlyerProject.model_validate({
        "project_id": "F9102",
        "status": "manual_edit_required",
        "customer_phone": "+17329837841",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "m-1",
        "raw_request": "Edit uploaded flyer/source artwork. Change date.",
        "fields": {"event_or_business_name": "Lakshmis", "contact_info": "+17329837841"},
        "assets": [{
            "asset_id": "A0001",
            "kind": "reference_image",
            "source": "whatsapp",
            "path": str(ref_path),
            "mime_type": "image/png",
            "sha256": "a" * 64,
            "received_at": now,
        }],
    })

    def tripwire(*_args, **_kwargs):
        raise AssertionError("network request issued with lowercase placeholder OpenAI key")

    monkeypatch.setattr(render_mod.urllib.request, "urlopen", tripwire)
    monkeypatch.setattr(render_mod, "_read_env_value", lambda _name: "placeholder-key")

    import pytest as _pytest
    with _pytest.raises(FlyerRenderError, match="placeholder"):
        render_mod._openai_source_edit_bytes(project, size=(1080, 1350), model="gpt-image-1", quality="medium")


def test_openai_source_edit_bytes_fails_closed_on_malformed_json_shape(tmp_path, monkeypatch):
    import agents.flyer.render as render_mod

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    ref_path = tmp_path / "F9103-ref.png"
    ref_path.write_bytes(b"fake")

    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    project = FlyerProject.model_validate({
        "project_id": "F9103",
        "status": "manual_edit_required",
        "customer_phone": "+17329837841",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "m-1",
        "raw_request": "Edit uploaded flyer/source artwork. Change date.",
        "fields": {"event_or_business_name": "Lakshmis", "contact_info": "+17329837841"},
        "assets": [{
            "asset_id": "A0001",
            "kind": "reference_image",
            "source": "whatsapp",
            "path": str(ref_path),
            "mime_type": "image/png",
            "sha256": "a" * 64,
            "received_at": now,
        }],
    })

    class _Resp:
        def __enter__(self):
            self._body = json.dumps({"data": ["not-a-dict"]}).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    monkeypatch.setattr(render_mod, "_read_env_value", lambda _name: "sk-test")
    monkeypatch.setattr(render_mod.urllib.request, "urlopen", lambda _req, timeout=None: _Resp())

    import pytest as _pytest
    with _pytest.raises(FlyerRenderError, match="invalid item shape"):
        render_mod._openai_source_edit_bytes(project, size=(1080, 1350), model="gpt-image-1", quality="medium")


def test_source_edit_rejects_pdf_reference_before_provider_call(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    reference = tmp_path / "reference.pdf"
    reference.write_bytes(b"%PDF-1.4")
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Change date.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(reference),
                mime_type="application/pdf",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
    })

    def _fail_urlopen(*_args, **_kwargs):
        raise AssertionError("PDF references must not reach image edit provider")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _fail_urlopen)

    try:
        render_source_edit_preview(project, tmp_path, provider="openai", model="gpt-image-1", quality="medium")
    except FlyerRenderError as e:
        assert "must be an image" in str(e)
    else:
        raise AssertionError("expected FlyerRenderError")


def test_source_edit_reference_uses_latest_reference_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    first = tmp_path / "old.png"
    latest = tmp_path / "latest.png"
    first.write_bytes(_png_bytes(color=(10, 20, 30)))
    latest.write_bytes(_png_bytes(color=(40, 90, 50)))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Change date.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path=str(first),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="old",
                received_at=datetime.now(timezone.utc),
            ),
            FlyerAsset(
                asset_id="A0002",
                kind="reference_image",
                source="whatsapp",
                path=str(latest),
                mime_type="image/png",
                sha256="b" * 64,
                original_message_id="latest",
                received_at=datetime.now(timezone.utc),
            ),
        ],
    })
    requests = []

    class _Resp:
        def __enter__(self):
            png = base64.b64encode(_png_bytes()).decode("ascii")
            self._body = json.dumps({"data": [{"b64_json": png}]}).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", lambda req, timeout: requests.append(req.data) or _Resp())

    render_source_edit_preview(project, tmp_path, provider="openai", model="gpt-image-1", quality="medium")

    assert b'filename="latest.png"' in requests[0]
    assert b'filename="old.png"' not in requests[0]


def test_direct_poster_prompt_uses_registered_business_name_not_reference_brand(tmp_path, monkeypatch):
    customers_path = tmp_path / "customers.json"
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Lakshmis Kitchn",
            "business_address": "90 Brybar Dr St Johns FL",
            "primary_chat_id": "201975216009469@lid",
            "onboarded_by_phone": "+19045550104",
            "public_phone": "+17329837841",
            "business_whatsapp_number": "+17329837841",
            "authorized_request_numbers": ["+17329837841", "+19045550104"],
            "business_category": "Indian restaurant",
            "preferred_language": "te",
            "plan_id": "trial",
            "status": "trial",
            "created_at": datetime(2026, 5, 17, tzinfo=timezone.utc).isoformat(),
            "updated_at": datetime(2026, 5, 17, tzinfo=timezone.utc).isoformat(),
            "activated_at": datetime(2026, 5, 17, tzinfo=timezone.utc).isoformat(),
            "monthly_flyers_used": 0,
            "billing_provider": "manual",
            "payment_currency": "USD",
        }],
        "onboarding_sessions": [],
    }), encoding="utf-8")
    monkeypatch.setenv("FLYER_CUSTOMERS_PATH", str(customers_path))
    monkeypatch.setattr("agents.flyer.render._project_reference_assets", lambda project: project.assets)
    project = _complete_project().model_copy(update={
        "customer_phone": "+19045550104",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="reference_image",
                source="whatsapp",
                path="/opt/shift-agent/state/flyer/assets/reference.jpg",
                mime_type="image/jpeg",
                sha256="a" * 64,
                original_message_id="wamid.reference",
                received_at=datetime.now(timezone.utc),
            )
        ],
        "fields": FlyerRequestFields(
            event_or_business_name="Weekend Breakfast Specials",
            event_time="08:00",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            preferred_language="te",
            notes="Take items from breakfast section in the attached reference flyer.",
        ),
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Business/brand: Lakshmis Kitchn" in prompt
    assert "Do not copy business names or logos from the reference" in prompt


def test_explicit_business_override_reaches_direct_and_source_edit_prompts(tmp_path, monkeypatch):
    import agents.flyer.render as render_mod

    customers_path = tmp_path / "customers.json"
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 2,
        "customers": [{
            "customer_id": "CUST0001",
            "business_name": "Old Brand",
            "business_address": "90 Brybar Dr St Johns FL",
            "primary_chat_id": "201975216009469@lid",
            "onboarded_by_phone": "+19045550104",
            "public_phone": "+17329837841",
            "business_whatsapp_number": "+17329837841",
            "authorized_request_numbers": ["+17329837841", "+19045550104"],
            "business_category": "Indian restaurant",
            "preferred_language": "te",
            "plan_id": "trial",
            "status": "trial",
            "created_at": datetime(2026, 5, 17, tzinfo=timezone.utc).isoformat(),
            "updated_at": datetime(2026, 5, 17, tzinfo=timezone.utc).isoformat(),
            "activated_at": datetime(2026, 5, 17, tzinfo=timezone.utc).isoformat(),
            "monthly_flyers_used": 0,
            "billing_provider": "manual",
            "payment_currency": "USD",
        }],
        "onboarding_sessions": [],
    }), encoding="utf-8")
    monkeypatch.setenv("FLYER_CUSTOMERS_PATH", str(customers_path))

    project = _complete_project().model_copy(update={
        "customer_phone": "+19045550104",
        "raw_request": "Create flyer. Business name is New Brand and headline is Evening Snacks.",
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="New Brand", source="customer_text"),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Evening Snacks", source="customer_text"),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile"),
        ],
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    source_prompt = render_mod._source_edit_prompt(project)

    assert "Business/brand: New Brand" in prompt
    assert "Business/brand: Old Brand" not in prompt
    assert "Business/brand to preserve: New Brand" in source_prompt
    assert "Business/brand to preserve: Old Brand" not in source_prompt


def test_real_image_model_concept_applies_deterministic_text_overlay(tmp_path, monkeypatch):
    raw_png = _png_bytes(color=(19, 83, 43))

    class _Resp:
        def __enter__(self):
            png = base64.b64encode(raw_png).decode("ascii")
            body = {"choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{png}"}}]}}]}
            self._body = json.dumps(body).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", lambda *_args, **_kwargs: _Resp())

    specs = render_concept_previews(
        _complete_project(),
        tmp_path,
        model="openai/gpt-5.4-image-2",
        quality="high",
    )

    assert inspect_rendered_asset(specs[0].path, expected_width=1080, expected_height=1350, mime_type="image/png").ok is True
    raw_path = specs[0].path.with_name(f"{specs[0].path.stem}.raw.png")
    assert raw_path.exists()
    assert specs[0].path.read_bytes() != raw_path.read_bytes()

    from PIL import Image, ImageChops
    with Image.open(specs[0].path).convert("RGB") as preview, Image.open(raw_path).convert("RGB") as raw:
        # The deterministic critical-text overlay composites exact text over the
        # model background (replacing the old identity-only overlay); the
        # non-menu project draws its critical panel near the bottom.
        assert ImageChops.difference(preview, raw).getbbox() is not None
        assert preview.getpixel((540, 1200)) != raw.getpixel((540, 1200))


def test_exact_identity_overlay_reserves_contact_with_long_schedule_and_address(tmp_path):
    source = tmp_path / "raw.png"
    target = tmp_path / "preview.png"
    source.write_bytes(_png_bytes(size=(1080, 1350), color=(19, 83, 43)))
    project = _complete_project().model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Special Biryani's",
            venue_or_location="12345 Very Long Commercial Plaza Suite 200 Near Market District Saint Johns Florida 32259",
            contact_info="+17329837841",
            notes="Create a flyer.",
        ),
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile"),
            FlyerLockedFact(
                fact_id="schedule",
                label="Schedule",
                value="Wednesday and Thursday every week",
                source="customer_text",
            ),
            FlyerLockedFact(
                fact_id="location",
                label="Location",
                value="12345 Very Long Commercial Plaza Suite 200 Near Market District Saint Johns Florida 32259",
                source="customer_profile",
            ),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile"),
        ],
    })

    apply_exact_identity_overlay(project, source, target, size=(1080, 1350))
    manifest = write_text_manifest(project, target, output_format="concept_preview")
    text = manifest.read_text(encoding="utf-8")

    assert "Wednesday and Thursday every week" in text
    assert "+17329837841" in text
    assert target.exists()


def test_exact_identity_overlay_falls_back_to_system_pillow(tmp_path, monkeypatch):
    source = tmp_path / "raw.png"
    target = tmp_path / "preview.png"
    source.write_bytes(_png_bytes(size=(1080, 1350), color=(19, 83, 43)))
    project = _complete_project()

    real_exists = render_module.Path.exists

    def fake_exists(path):
        if path.as_posix().endswith("/usr/bin/python3"):
            return True
        return real_exists(path)

    def fake_run(args, **_kwargs):
        payload = json.loads(Path(args[-1]).read_text(encoding="utf-8"))
        Path(payload["target"]).write_bytes(b"system-pillow-rendered")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(render_module, "_load_pillow", lambda: None)
    monkeypatch.setattr(render_module.Path, "exists", fake_exists)
    monkeypatch.setattr(render_module.subprocess, "run", fake_run)

    apply_exact_identity_overlay(project, source, target, size=(1080, 1350))

    assert target.read_bytes() == b"system-pillow-rendered"


def test_exact_identity_overlay_fails_closed_when_no_pillow_path(tmp_path, monkeypatch):
    source = tmp_path / "raw.png"
    target = tmp_path / "preview.png"
    source.write_bytes(_png_bytes(size=(1080, 1350), color=(19, 83, 43)))
    project = _complete_project()

    real_exists = render_module.Path.exists

    def fake_exists(path):
        if path.as_posix().endswith("/usr/bin/python3"):
            return False
        return real_exists(path)

    monkeypatch.setattr(render_module, "_load_pillow", lambda: None)
    monkeypatch.setattr(render_module.Path, "exists", fake_exists)

    try:
        apply_exact_identity_overlay(project, source, target, size=(1080, 1350))
    except FlyerRenderError as exc:
        assert "Pillow is unavailable for exact identity overlay" in str(exc)
    else:
        raise AssertionError("expected FlyerRenderError")


def test_real_image_model_direct_poster_is_resized_to_requested_format(tmp_path, monkeypatch):
    raw_png = _png_bytes(size=(1792, 2240), color=(24, 70, 38))

    class _Resp:
        def __enter__(self):
            png = base64.b64encode(raw_png).decode("ascii")
            body = {"choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{png}"}}]}}]}
            self._body = json.dumps(body).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", lambda *_args, **_kwargs: _Resp())

    specs = render_concept_previews(
        _complete_project(),
        tmp_path,
        model="openai/gpt-5.4-image-2",
        quality="high",
    )

    report = inspect_rendered_asset(specs[0].path, expected_width=1080, expected_height=1350, mime_type="image/png")
    assert report.ok is True
    assert (report.width, report.height) == (1080, 1350)


def test_non_eligible_final_package_uses_preview_directly_not_raw(tmp_path, monkeypatch):
    """For NON-eligible projects (reference-extraction / source-edit) the preview
    is the authoritative artifact (its text is model/operator-produced) — finals
    use it directly and are NOT rebuilt from the raw background + overlay."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "F0001-C1-preview.png"
    stale_raw = tmp_path / "F0001-C1-preview.raw.png"
    ref = tmp_path / "ref.png"
    approved.write_bytes(_png_bytes(color=(20, 120, 40)))
    stale_raw.write_bytes(_png_bytes(color=(140, 20, 20)))
    ref.write_bytes(_png_bytes(color=(10, 10, 10)))
    project = _complete_project().model_copy(update={
        # Reference-extraction request → NOT background-only eligible → direct.
        "raw_request": "Create a flyer; extract items and prices from this sample flyer.",
        "assets": [
            FlyerAsset(
                asset_id="A0001",
                kind="concept_preview",
                source="rendered",
                path=str(approved),
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.flyer.1",
                received_at=datetime.now(timezone.utc),
            ),
            FlyerAsset(
                asset_id="A0002",
                kind="reference_image",
                source="whatsapp",
                path=str(ref),
                mime_type="image/png",
                sha256="b" * 64,
                original_message_id="wamid.flyer.1",
                received_at=datetime.now(timezone.utc),
            ),
        ],
        "concepts": [
            FlyerConcept(
                concept_id="C1",
                title="Best Design",
                style_summary="Generated concept",
                preview_asset_id="A0001",
                prompt="",
                created_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    specs = render_final_package(project, tmp_path / "finals", model="openai/gpt-5.4-image-2", quality="high")

    whatsapp = next(spec for spec in specs if spec.output_format == "whatsapp_image")
    from PIL import Image
    with Image.open(whatsapp.path) as final_img, Image.open(approved) as approved_img, Image.open(stale_raw) as raw_img:
        assert final_img.getpixel((20, 20)) == approved_img.getpixel((20, 20))
        assert final_img.getpixel((20, 20)) != raw_img.getpixel((20, 20))


def test_source_edit_final_package_reapplies_overlay_per_format(tmp_path, monkeypatch):
    """Render-side robustness (item 2): a SOURCE-EDIT final package re-applies the
    deterministic overlay PER FORMAT from the raw model edit (contained, preserve
    aspect) — new-flyer parity — instead of resizing one 4:5 composite. The raw is
    written alongside the overlaid preview by render_source_edit_preview."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "F0001-C1-preview.png"
    raw_bg = tmp_path / "F0001-C1-preview.raw.png"  # _raw_background_path(approved)
    ref = tmp_path / "ref.png"
    approved.write_bytes(_png_bytes(color=(20, 120, 40)))
    raw_bg.write_bytes(_png_bytes(color=(140, 20, 20)))
    ref.write_bytes(_png_bytes(color=(10, 10, 10)))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "assets": [
            FlyerAsset(
                asset_id="A0001", kind="concept_preview", source="rendered", path=str(approved),
                mime_type="image/png", sha256="a" * 64, original_message_id="wamid.flyer.1",
                received_at=datetime.now(timezone.utc),
            ),
            FlyerAsset(
                asset_id="A0002", kind="reference_image", source="whatsapp", path=str(ref),
                mime_type="image/png", sha256="b" * 64, original_message_id="wamid.flyer.1",
                received_at=datetime.now(timezone.utc),
            ),
        ],
        "concepts": [
            FlyerConcept(
                concept_id="C1", title="Edited", style_summary="Source edit",
                preview_asset_id="A0001", prompt="", created_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    overlay_calls = []

    def _record_overlay(proj, source, target, *, size, output_format):
        overlay_calls.append({"output_format": output_format, "size": size})
        Path(target).write_bytes(Path(source).read_bytes())  # passthrough

    monkeypatch.setattr(render_module, "apply_critical_text_overlay", _record_overlay)

    render_final_package(project, tmp_path / "finals", model="openai/gpt-5.4-image-2", quality="high")

    formats_overlaid = {c["output_format"] for c in overlay_calls}
    assert {"whatsapp_image", "instagram_post", "instagram_story", "printable_pdf"} <= formats_overlaid, (
        f"source-edit final package must re-apply the overlay per format; got {formats_overlaid}"
    )


def test_source_edit_final_package_ignores_stale_raw_guard_and_reapplies_overlay(tmp_path, monkeypatch):
    """Source-edit finals must not resize the 4:5 approved preview just because the
    raw sidecar mtime looks old. If raw exists, the source-edit contract is to
    re-apply the deterministic overlay per output format."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "F0001-C1-preview.png"
    raw_bg = tmp_path / "F0001-C1-preview.raw.png"
    ref = tmp_path / "ref.png"
    approved.write_bytes(_png_bytes(color=(20, 120, 40)))
    raw_bg.write_bytes(_png_bytes(color=(140, 20, 20)))
    ref.write_bytes(_png_bytes(color=(10, 10, 10)))
    stale = datetime.now().timestamp() - 120
    current = datetime.now().timestamp()
    import os
    os.utime(raw_bg, (stale, stale))
    os.utime(approved, (current, current))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "assets": [
            FlyerAsset(
                asset_id="A0001", kind="concept_preview", source="rendered", path=str(approved),
                mime_type="image/png", sha256="a" * 64, original_message_id="wamid.flyer.1",
                received_at=datetime.now(timezone.utc),
            ),
            FlyerAsset(
                asset_id="A0002", kind="reference_image", source="whatsapp", path=str(ref),
                mime_type="image/png", sha256="b" * 64, original_message_id="wamid.flyer.1",
                received_at=datetime.now(timezone.utc),
            ),
        ],
        "concepts": [
            FlyerConcept(
                concept_id="C1", title="Edited", style_summary="Source edit",
                preview_asset_id="A0001", prompt="", created_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    overlay_calls = []
    contained_sources = []

    def _record_contained(source, target, *, size):
        contained_sources.append(str(source))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        render_module._export_from_source_image(source, target, size=size)

    def _record_overlay(proj, source, target, *, size, output_format):
        overlay_calls.append({"output_format": output_format, "source": str(source)})
        Path(target).write_bytes(Path(source).read_bytes())

    monkeypatch.setattr(render_module, "_export_from_source_image_contained", _record_contained)
    monkeypatch.setattr(render_module, "apply_critical_text_overlay", _record_overlay)

    render_final_package(project, tmp_path / "finals", model="openai/gpt-5.4-image-2", quality="high")

    formats_overlaid = {c["output_format"] for c in overlay_calls}
    assert {"whatsapp_image", "instagram_post", "instagram_story", "printable_pdf"} <= formats_overlaid
    assert contained_sources == [str(raw_bg), str(raw_bg), str(raw_bg), str(raw_bg)]


def test_source_edit_final_package_without_selected_preview_fails_closed(tmp_path, monkeypatch):
    import pytest as _pytest
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes(color=(10, 10, 10)))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "assets": [
            FlyerAsset(
                asset_id="A0002", kind="reference_image", source="whatsapp", path=str(ref),
                mime_type="image/png", sha256="b" * 64, original_message_id="wamid.flyer.1",
                received_at=datetime.now(timezone.utc),
            ),
        ],
        "concepts": [],
        "selected_concept_id": "C1",
    })

    with _pytest.raises(FlyerRenderError, match="source edit final package requires an approved preview"):
        render_final_package(project, tmp_path / "finals", model="openai/gpt-5.4-image-2", quality="high")


def test_not_background_eligible_source_edit_final_still_reapplies_overlay(tmp_path, monkeypatch):
    """A source-edit that ALSO requests reference extraction is NOT
    background-only-eligible, so pre-fix it took the direct/no-overlay branch.
    It must still re-apply the deterministic overlay per format (item 2 / Edit A)."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "F0001-C1-preview.png"
    raw_bg = tmp_path / "F0001-C1-preview.raw.png"
    ref = tmp_path / "ref.png"
    approved.write_bytes(_png_bytes(color=(20, 120, 40)))
    raw_bg.write_bytes(_png_bytes(color=(140, 20, 20)))
    ref.write_bytes(_png_bytes(color=(10, 10, 10)))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        # source-edit marker AND an extract request -> NOT background-only eligible.
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: extract the items and prices from the attached sample and refresh them.",
        "assets": [
            FlyerAsset(
                asset_id="A0001", kind="concept_preview", source="rendered", path=str(approved),
                mime_type="image/png", sha256="a" * 64, original_message_id="wamid.flyer.1",
                received_at=datetime.now(timezone.utc),
            ),
            FlyerAsset(
                asset_id="A0002", kind="reference_image", source="whatsapp", path=str(ref),
                mime_type="image/png", sha256="b" * 64, original_message_id="wamid.flyer.1",
                received_at=datetime.now(timezone.utc),
            ),
        ],
        "concepts": [
            FlyerConcept(
                concept_id="C1", title="Edited", style_summary="Source edit",
                preview_asset_id="A0001", prompt="", created_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    overlay_calls = []

    def _record_overlay(proj, source, target, *, size, output_format):
        overlay_calls.append(output_format)
        Path(target).write_bytes(Path(source).read_bytes())

    monkeypatch.setattr(render_module, "apply_critical_text_overlay", _record_overlay)
    render_final_package(project, tmp_path / "finals", model="openai/gpt-5.4-image-2", quality="high")

    assert {"whatsapp_image", "instagram_post", "instagram_story", "printable_pdf"} <= set(overlay_calls), (
        f"not-eligible source-edit final must still re-apply overlay per format; got {set(overlay_calls)}"
    )


def test_breakfast_menu_facts_are_customer_flyer_copy_not_raw_prompt():
    project = _complete_project().model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Weekend Breakfast Specials",
            event_time="08:00",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
            preferred_language="te",
            notes=(
                'Create a breakfast flyer with these items "Poori with Chicken $14.99, '
                'Kheema Dosa $12.99, Pesarattu with Upma $11.99, Vada with Sambar $12.99, '
                'Mysore Masala Dosa $11.99". Timings 8 AM to 11 AM. Thursday to Sunday.'
            ),
        ),
        "raw_request": (
            'Create a breakfast flyer with these items "Poori with Chicken $14.99, '
            'Kheema Dosa $12.99, Pesarattu with Upma $11.99, Vada with Sambar $12.99, '
            'Mysore Masala Dosa $11.99". Timings 8 AM to 11 AM. Thursday to Sunday.'
        ),
    })

    facts = collect_text_facts(project)
    payload = _menu_overlay_payload(project)

    assert facts[0].text == "Weekend Breakfast Specials"
    assert "Thursday To Sunday | 8 AM TO 11 AM" in [fact.text for fact in facts]
    assert payload["items"] == [
        "Poori with Chicken $14.99",
        "Kheema Dosa $12.99",
        "Pesarattu with Upma $11.99",
        "Vada with Sambar $12.99",
        "Mysore Masala Dosa $11.99",
    ]
    assert all("Create a breakfast flyer" not in fact.text for fact in facts)


def test_chloe_salon_prompt_is_not_food_or_festival_themed():
    project = _complete_project().model_copy(update={
        "raw_request": "Create flyer for Chloe Hair Studio promoting the $20 men haircut, $80 perms, and other hair services",
        "fields": FlyerRequestFields(
            event_or_business_name="Chloe Hair Studio",
            venue_or_location="11111 Gainsborough Ct, Fairfax, VA 22030",
            contact_info="+19803826497",
            preferred_language="en",
            style_preference="modern salon and beauty studio promotion",
            notes="Men haircut $20; Perms $80; Other hair services.",
        ),
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "salon" in prompt.lower()
    assert "service offer cards" in prompt.lower()
    assert "Other hair services available" in prompt
    assert "ethnic grocery" not in prompt.lower()
    assert "south indian" not in prompt.lower()
    assert "marigold" not in prompt.lower()
    assert "mango-leaf" not in prompt.lower()
    assert "appetizing food" not in prompt.lower()
    assert "festival warmth" not in prompt.lower()
    assert "restaurant/menu poster" not in prompt.lower()


def test_text_manifest_blocks_customer_instruction_leak(tmp_path):
    source = tmp_path / "bad.png"
    source.write_bytes(_png_bytes())
    project = _complete_project().model_copy(update={
        "raw_request": "Create flyer for chloe hair studio promoting the $20 men haircut",
        "fields": FlyerRequestFields(
            event_or_business_name="chloe hair studio promoting the $20 men haircut",
            venue_or_location="11111 Gainsborough Ct, Fairfax, VA 22030",
            contact_info="+19803826497",
            notes="Create flyer for chloe hair studio promoting the $20",
        ),
    })

    try:
        write_text_manifest(project, source, output_format="concept_preview")
    except FlyerRenderError as e:
        assert "instruction text leaked into flyer copy" in str(e)
    else:
        raise AssertionError("expected instruction leakage to fail text QA")


def test_final_package_exports_from_selected_generated_concept_without_new_model_call(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project()
    concept_specs = render_concept_previews(project, tmp_path)
    assets = build_asset_manifest(
        concept_specs,
        first_asset_number=1,
        source="rendered",
        original_message_id="wamid.flyer.1",
    )
    concepts = [
        FlyerConcept(
            concept_id="C1",
            title="Concept 1",
            style_summary="Generated concept",
            preview_asset_id="A0001",
            prompt=project.raw_request,
            created_at=datetime.now(timezone.utc),
            selected_at=datetime.now(timezone.utc),
        )
    ]
    project = project.model_copy(update={
        "assets": assets,
        "concepts": concepts,
        "selected_concept_id": "C1",
    })

    def _fail_urlopen(*_args, **_kwargs):
        raise AssertionError("final export should reuse the selected concept image")

    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _fail_urlopen)
    specs = render_final_package(project, tmp_path / "finals", model="openai/gpt-5.4-image-2", quality="high")
    assert {s.output_format for s in specs} == {"whatsapp_image", "instagram_post", "instagram_story", "printable_pdf"}
    assert all(s.path.exists() and s.path.stat().st_size > 1000 for s in specs)


def test_final_package_reuses_selected_concept_with_deterministic_model(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "F0001-C1-preview.png"
    approved.write_bytes(_png_bytes(size=(1080, 1350), color=(60, 20, 160)))
    asset = FlyerAsset(
        asset_id="A0001",
        kind="concept_preview",
        source="rendered",
        path=str(approved),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="wamid.flyer.1",
        received_at=datetime.now(timezone.utc),
    )
    project = _complete_project().model_copy(update={
        "assets": [asset],
        "concepts": [
            FlyerConcept(
                concept_id="C1",
                title="Approved Flyer",
                style_summary="Approved preview",
                preview_asset_id="A0001",
                prompt="",
                created_at=datetime.now(timezone.utc),
                selected_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    specs = render_final_package(project, tmp_path / "finals", model="deterministic-renderer", quality="high")

    from PIL import Image
    whatsapp = next(spec for spec in specs if spec.output_format == "whatsapp_image")
    with Image.open(whatsapp.path) as final_img, Image.open(approved) as approved_img:
        assert final_img.getpixel((20, 20)) == approved_img.getpixel((20, 20))


def test_final_package_with_selected_concept_missing_preview_fails_closed(tmp_path, monkeypatch):
    import pytest as _pytest
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "awaiting_final_approval",
        "assets": [],
        "concepts": [
            FlyerConcept(
                concept_id="C1",
                title="Approved Flyer",
                style_summary="Approved preview",
                preview_asset_id="A0001",
                prompt="",
                created_at=datetime.now(timezone.utc),
                selected_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    with _pytest.raises(FlyerRenderError, match=r"^final package requires an approved preview$"):
        render_final_package(project, tmp_path / "finals", model="deterministic-renderer", quality="medium")

    assert not (tmp_path / "finals" / "F0001-whatsapp_image.png").exists()


def test_final_package_with_selected_concept_invalid_preview_fails_closed(tmp_path, monkeypatch):
    import pytest as _pytest
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    invalid_preview = tmp_path / "F0001-C1-preview.png"
    invalid_preview.write_bytes(_png_bytes(size=(100, 100), color=(60, 20, 160)))
    asset = FlyerAsset(
        asset_id="A0001",
        kind="concept_preview",
        source="rendered",
        path=str(invalid_preview),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="wamid.flyer.1",
        received_at=datetime.now(timezone.utc),
    )
    project = _complete_project().model_copy(update={
        "status": "awaiting_final_approval",
        "assets": [asset],
        "concepts": [
            FlyerConcept(
                concept_id="C1",
                title="Approved Flyer",
                style_summary="Approved preview",
                preview_asset_id="A0001",
                prompt="",
                created_at=datetime.now(timezone.utc),
                selected_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    with _pytest.raises(FlyerRenderError, match=r"^final package requires an approved preview$"):
        render_final_package(project, tmp_path / "finals", model="deterministic-renderer", quality="medium")


def test_source_edit_final_package_without_selection_fails_closed(tmp_path, monkeypatch):
    import pytest as _pytest
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _complete_project().model_copy(update={
        "status": "manual_edit_required",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "assets": [],
        "concepts": [],
        "selected_concept_id": None,
    })

    with _pytest.raises(FlyerRenderError, match=r"^source edit final package requires an approved preview$"):
        render_final_package(project, tmp_path / "finals", model="deterministic-renderer", quality="medium")


def test_source_edit_final_package_without_raw_sidecar_fails_closed(tmp_path, monkeypatch):
    import pytest as _pytest
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "F0001-C1-preview.png"
    approved.write_bytes(_png_bytes(size=(1080, 1350), color=(20, 120, 40)))
    asset = FlyerAsset(
        asset_id="A0001",
        kind="concept_preview",
        source="rendered",
        path=str(approved),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="wamid.flyer.1",
        received_at=datetime.now(timezone.utc),
    )
    project = _complete_project().model_copy(update={
        "status": "awaiting_final_approval",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "assets": [asset],
        "concepts": [
            FlyerConcept(
                concept_id="C1",
                title="Edited Flyer",
                style_summary="Source-preserving edit",
                preview_asset_id="A0001",
                prompt="",
                created_at=datetime.now(timezone.utc),
                selected_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    with _pytest.raises(FlyerRenderError, match="source edit final package requires raw edited background sidecar"):
        render_final_package(project, tmp_path / "finals", model="deterministic-renderer", quality="medium")


def test_manual_completed_source_edit_final_package_uses_operator_preview_without_raw_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "manual" / "F0001" / "F0001-A0001.png"
    approved.parent.mkdir(parents=True)
    approved.write_bytes(_png_bytes(size=(1080, 1350), color=(20, 120, 40)))
    asset = FlyerAsset(
        asset_id="A0001",
        kind="concept_preview",
        source="uploaded",
        path=str(approved),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="wamid.flyer.1",
        received_at=datetime.now(timezone.utc),
    )
    project = _complete_project().model_copy(update={
        "status": "awaiting_final_approval",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "manual_review": FlyerManualReview(
            status="completed",
            reason="operator completed source edit",
            reason_code="source_edit_provider_unavailable",
            completed_at=datetime.now(timezone.utc),
            operator_asset_ids=["A0001"],
        ),
        "assets": [asset],
        "concepts": [
            FlyerConcept(
                concept_id="C1",
                title="Designer Approved",
                style_summary="Operator-approved manual review asset",
                preview_asset_id="A0001",
                prompt="",
                created_at=datetime.now(timezone.utc),
                selected_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    specs = render_final_package(project, tmp_path / "finals", model="deterministic-renderer", quality="medium")

    assert {s.output_format for s in specs} == {"whatsapp_image", "instagram_post", "instagram_story", "printable_pdf"}
    whatsapp = next(spec for spec in specs if spec.output_format == "whatsapp_image")
    manifest = json.loads(Path(f"{whatsapp.path}.text.json").read_text(encoding="utf-8"))
    assert manifest["verification_mode"] == "source_edit_integrity_only"
    assert manifest["expected_facts"] == []
    assert manifest["source_sha256"] == hashlib.sha256(approved.read_bytes()).hexdigest()


def test_manual_completed_source_edit_requires_selected_operator_asset_id_for_raw_sidecar_bypass(tmp_path, monkeypatch):
    import pytest as _pytest
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "manual" / "F0001" / "F0001-A0001.png"
    approved.parent.mkdir(parents=True)
    approved.write_bytes(_png_bytes(size=(1080, 1350), color=(20, 120, 40)))
    asset = FlyerAsset(
        asset_id="A0001",
        kind="concept_preview",
        source="uploaded",
        path=str(approved),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="wamid.flyer.1",
        received_at=datetime.now(timezone.utc),
    )
    project = _complete_project().model_copy(update={
        "status": "awaiting_final_approval",
        "raw_request": "Edit uploaded flyer/source artwork. Customer requested: Remove extra 08:00.",
        "manual_review": FlyerManualReview(
            status="completed",
            reason="operator completed source edit",
            reason_code="source_edit_provider_unavailable",
            completed_at=datetime.now(timezone.utc),
            operator_asset_ids=[],
        ),
        "assets": [asset],
        "concepts": [
            FlyerConcept(
                concept_id="C1",
                title="Designer Approved",
                style_summary="Operator-approved manual review asset",
                preview_asset_id="A0001",
                prompt="",
                created_at=datetime.now(timezone.utc),
                selected_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    with _pytest.raises(FlyerRenderError, match="source edit final package requires raw edited background sidecar"):
        render_final_package(project, tmp_path / "finals", model="deterministic-renderer", quality="medium")


def test_authorized_source_artwork_update_is_treated_as_source_edit_for_finals(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "F0001-C1-preview.png"
    raw_bg = tmp_path / "F0001-C1-preview.raw.png"
    approved.write_bytes(_png_bytes(size=(1080, 1350), color=(40, 30, 150)))
    raw_bg.write_bytes(_png_bytes(size=(1080, 1350), color=(90, 90, 90)))
    asset = FlyerAsset(
        asset_id="A0001",
        kind="concept_preview",
        source="rendered",
        path=str(approved),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="wamid.flyer.1",
        received_at=datetime.now(timezone.utc),
    )
    project = _complete_project().model_copy(update={
        "status": "awaiting_final_approval",
        "raw_request": "Authorized flyer/source artwork update. Change the date to May 22.",
        "assets": [asset],
        "concepts": [
            FlyerConcept(
                concept_id="C1",
                title="Edited Flyer",
                style_summary="Source-preserving edit",
                preview_asset_id="A0001",
                prompt="",
                created_at=datetime.now(timezone.utc),
                selected_at=datetime.now(timezone.utc),
            )
        ],
        "selected_concept_id": "C1",
    })

    specs = render_final_package(project, tmp_path / "finals", model="deterministic-renderer", quality="medium")

    whatsapp = next(spec for spec in specs if spec.output_format == "whatsapp_image")
    manifest = json.loads(Path(f"{whatsapp.path}.text.json").read_text(encoding="utf-8"))
    assert manifest["verification_mode"] == "source_edit_overlay_recomposed"


def test_is_source_edit_project_requires_marker_or_reference_image(tmp_path, monkeypatch):
    """Fix D: `_is_source_edit_project` must not return True for every
    manual_edit_required project. It needs either the explicit raw_request
    marker OR a reference_image asset alongside manual_edit_required.
    Missing_required_facts projects (no marker, no reference image) at
    manual_edit_required must NOT be classified as source-edit."""
    from agents.flyer.render import _is_source_edit_project

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    base = {
        "project_id": "F9100",
        "customer_phone": "+17329837841",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "m-1",
        "raw_request": "Make a flyer please.",
        "fields": {"event_or_business_name": "Bare", "contact_info": "+17329837841"},
    }

    # 1) manual_edit_required + no marker + no reference_image -> NOT source-edit.
    bare = FlyerProject.model_validate({**base, "status": "manual_edit_required", "assets": []})
    assert _is_source_edit_project(bare) is False

    # 2) Explicit marker in raw_request -> source-edit (regardless of status / assets).
    marked = FlyerProject.model_validate({
        **base,
        "status": "intake_started",
        "raw_request": "Edit uploaded flyer/source artwork. Change date.",
        "assets": [],
    })
    assert _is_source_edit_project(marked) is True

    # 3) manual_edit_required + reference_image asset -> source-edit.
    ref_path = tmp_path / "F9100-ref.png"
    ref_path.write_bytes(b"fake")
    with_ref = FlyerProject.model_validate({
        **base,
        "status": "manual_edit_required",
        "assets": [{
            "asset_id": "A0001",
            "kind": "reference_image",
            "source": "whatsapp",
            "path": str(ref_path),
            "mime_type": "image/png",
            "sha256": "a" * 64,
            "received_at": now,
        }],
    })
    assert _is_source_edit_project(with_ref) is True


def test_openai_source_edit_bytes_fails_closed_on_placeholder_key(tmp_path, monkeypatch):
    """Fix F: a PLACEHOLDER OPENAI key must NOT make a network request.
    Mirror visual_qa / workflow defense-in-depth: missing OR placeholder
    fails closed before urlopen is called, raising FlyerRenderError that
    generate-flyer-concepts catches and classifies as
    source_edit_provider_unavailable."""
    import agents.flyer.render as render_mod

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    ref_path = tmp_path / "F9101-ref.png"
    ref_path.write_bytes(b"fake")

    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    project = FlyerProject.model_validate({
        "project_id": "F9101",
        "status": "manual_edit_required",
        "customer_phone": "+17329837841",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "m-1",
        "raw_request": "Edit uploaded flyer/source artwork. Change date.",
        "fields": {"event_or_business_name": "Lakshmis", "contact_info": "+17329837841"},
        "assets": [{
            "asset_id": "A0001",
            "kind": "reference_image",
            "source": "whatsapp",
            "path": str(ref_path),
            "mime_type": "image/png",
            "sha256": "a" * 64,
            "received_at": now,
        }],
    })

    # If a network request were attempted with a PLACEHOLDER key, urlopen would
    # be called. Patch it to a tripwire so any call fails the test.
    def tripwire(*_args, **_kwargs):
        raise AssertionError("network request issued with PLACEHOLDER key — defense-in-depth violated")

    monkeypatch.setattr(render_mod.urllib.request, "urlopen", tripwire)
    monkeypatch.setattr(render_mod, "_read_env_value", lambda _name: "PLACEHOLDER-key")

    import pytest as _pytest
    with _pytest.raises(FlyerRenderError, match="placeholder"):
        render_mod._openai_source_edit_bytes(project, size=(1080, 1350), model="gpt-image-1", quality="medium")


def test_render_customer_facing_footer_has_no_hermes_brand():
    """BUG-FLYER-QA-004: customer-facing footer text in both render paths
    must read 'Flyer Studio', not 'Hermes Flyer Studio'. The Hermes name is
    internal-only; leaking it to customer flyers conflicts with the rule
    that Hermes stays internal."""
    render_py = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "render.py"
    src = render_py.read_text(encoding="utf-8")
    # In-process Pillow path (_draw_flyer_pil) — use single-quoted needles to
    # match the Python source literal exactly.
    assert 'footer = "Send APPROVE to finalize - Flyer Studio"' in src, \
        "Pillow renderer footer not updated"
    # Subprocess renderer template (SUBPROCESS_RENDERER constant)
    assert 'footer="Send APPROVE to finalize - Flyer Studio"' in src, \
        "Subprocess renderer footer not updated"
    # Regression guard: the legacy customer-facing footer must NOT remain in
    # either renderer.
    customer_facing_legacy = "Send APPROVE to finalize - Hermes Flyer Studio"
    assert customer_facing_legacy not in src, \
        "Legacy 'Hermes Flyer Studio' footer string still present in render.py"


def test_deterministic_render_paths_use_campaign_title_not_business_name():
    render_py = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "render.py"
    src = render_py.read_text(encoding="utf-8")
    assert "title_text = _display_title(project)" in src
    assert '"title": _display_title(project),' in src
    assert '"title": fact_value(project, "business_name", fallback=project.fields.event_or_business_name)' not in src


# ─── Task 7: word-boundary _context_has + brand/branding edit semantics ──


def test_context_has_word_boundary_does_not_match_substring():
    """Pre-fix, `spa` matched inside `space`, `transparent`, `Hispanic`.
    Post-fix the helper uses word boundaries for single-word terms."""
    import agents.flyer.render as render_mod

    assert render_mod._context_has("modern spa retreat", {"spa"}) is True
    assert render_mod._context_has("clean space for address", {"spa"}) is False
    assert render_mod._context_has("transparent design", {"spa"}) is False
    assert render_mod._context_has("Hispanic restaurant", {"spa"}) is False


def test_context_has_multi_word_term_keeps_substring_semantics():
    import agents.flyer.render as render_mod

    # Multi-word terms (containing spaces or hyphens) still match by substring,
    # because regex word boundaries don't help with embedded punctuation.
    assert render_mod._context_has("our deep clean service", {"deep clean"}) is True
    assert render_mod._context_has("hand-made noodles", {"hand-made"}) is True


def test_twelve_item_menu_fails_closed_not_silently_dropped():
    """Oracle F0140 regression: a 12-item menu exceeds one flyer's legible capacity
    (the binding 1080x1080 square holds ~MAX_DETAIL_FACTS rows). The menu helpers must
    return ALL parsed items (no truncation) and `_detail_clauses` must FAIL CLOSED →
    route to manual, never silently drop items past the cap. A small menu still renders."""
    import pytest as _pytest
    from agents.flyer.render import _detail_clauses, _locked_menu_item_lines

    now = datetime(2026, 6, 2, tzinfo=timezone.utc)

    def _project(n):
        facts = [FlyerLockedFact(fact_id="pricing_structure", label="Pricing", value="Any item $8.99", source="customer_text")]
        for i in range(n):
            facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label="Item", value=f"Dish Number {i + 1}", source="hermes_inferred"))
            facts.append(FlyerLockedFact(fact_id=f"item:{i}:price", label="Price", value="$8.99", source="customer_text"))
        return FlyerProject(
            project_id="F0140", status="awaiting_final_approval", customer_phone="+10000000000",
            created_at=now, updated_at=now, original_message_id="m",
            raw_request="include 12 South Indian items, any item at $8.99", locked_facts=facts,
            fields=FlyerRequestFields(event_or_business_name="Lakshmis Kitchen"),
        )

    # Helper returns ALL 12 items — overflow is never hidden by truncation.
    assert len(_locked_menu_item_lines(_project(12))) == 12
    # 12 items > capacity → fail closed (manual), not a partial render.
    with _pytest.raises(FlyerRenderError, match="do not fit"):
        _detail_clauses(_project(12))
    # A small menu still renders all its items (no false overflow).
    clauses = _detail_clauses(_project(6))
    assert sum(1 for c in clauses if "Dish Number" in c) == 6


def test_multiline_promo_brief_keeps_discount_line_separate_from_long_service_copy():
    """A short discount plus long descriptive service copy is a valid promo flyer.

    Regression for MK Kitchen 2026 graduation-party request: newlines were compacted
    before clause splitting, so "10% off..." and the long catering sentence became
    one required critical fact and failed before image generation.
    """
    from agents.flyer.render import _detail_clauses, _poster_copy_plan

    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    raw = (
        "Flyer theme to reflect the graduation and include the below\n\n"
        "2026 graduation parties\n"
        "10% off on entire order\n"
        "We cater both veg and anin-veg items with delicious dessert to make your celebration effortless"
    )
    project = FlyerProject(
        project_id="F0066",
        status="generating_concepts",
        customer_phone="+15713830763",
        created_at=now,
        updated_at=now,
        original_message_id="wamid.66",
        raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="MK kitchen",
            contact_info="+15713830763",
            venue_or_location="23596 prosperity ridge pl Ashburn Va 20148",
            notes=raw,
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="MK kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+15713830763", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="23596 prosperity ridge pl Ashburn Va 20148", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="2026 Graduation Parties", source="customer_text", required=True),
            FlyerLockedFact(fact_id="pricing_structure", label="Pricing", value="10% off on entire order", source="customer_text", required=True),
        ],
    )

    clauses = _detail_clauses(project)

    assert clauses == [
        "10% off on entire order",
        "We cater both veg and anin-veg items with delicious dessert to make your celebration effortless",
    ]
    plan = _poster_copy_plan(project)
    assert plan.title == "2026 Graduation Parties"
    assert plan.detail_lines == [
        "10% off on entire order",
        "We cater both veg and anin-veg items with delicious dessert to make your celebration effortless",
    ]


def test_flattened_runon_request_does_not_fail_render_graduation_live_2026_06_06():
    """Production-faithful graduation regression (live fail-close 2026-06-06 22:41).

    `_extract_fields` newline-flattens the request into `fields.notes`, so a run-on request
    with NO sentence-ending periods reaches `_detail_clauses` as ONE clause that exceeds
    `_clean_fact_text`'s per-clause limit. That raised "critical text facts do not fit" and
    failed the whole render BEFORE image generation (the model never ran). The prior MK-kitchen
    test above masked this by keeping newlines in `notes`; production flattens them.

    `fields.notes` below is the EXACT flattened shape the live extractor emitted (verified on
    the box). The offer/price are already locked as structured facts, so the over-long
    *supplementary* run-on clause is skipped and the render proceeds with structured facts
    intact — no crash, no duplicate offer line, no invented/truncated prose.
    """
    from agents.flyer.render import _detail_clauses, _poster_copy_plan

    now = datetime(2026, 6, 6, tzinfo=timezone.utc)
    raw = (
        "Create a flyer with theme to reflect the graduation and include the below\n\n"
        "2026 graduation party special: get 10 percent off on your entire catering order "
        "when you book before June 30 2026 we cater veg and non-veg"
    )
    flattened_notes = (
        "Create a flyer with theme to reflect the graduation and include the below "
        "2026 graduation party special: get 10 percent off on your entire catering order "
        "when you book before June 30 2026 we cater veg and non-veg"
    )
    assert "\n" not in flattened_notes  # the production shape that defeats the newline split
    assert len(flattened_notes) > 180   # one run-on clause exceeds the per-clause limit

    project = FlyerProject(
        project_id="F0145", status="generating_concepts", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="wamid.145",
        raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="MK kitchen",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr",
            notes=flattened_notes,
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="MK kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="2026 Graduation Party Special", source="customer_text", required=True),
            FlyerLockedFact(fact_id="pricing_structure", label="Pricing", value="10% off entire catering order", source="customer_text", required=True),
            FlyerLockedFact(fact_id="offer:0", label="Offer", value="Get 10% off on your entire catering order when you book before June 30", source="customer_text", required=True),
        ],
    )

    # Must NOT raise — the over-long supplementary run-on clause is skipped, not fatal.
    clauses = _detail_clauses(project)
    # The structured offer/pricing facts still render (the real content is preserved)...
    assert "10% off entire catering order" in clauses
    assert any("Get 10% off" in c for c in clauses)
    # ...the over-long run-on instruction/offer prose is NOT added as a detail line...
    assert all(len(c) <= 180 for c in clauses)
    assert not any("reflect the graduation" in c for c in clauses)
    # ...and the next layer up (image-prompt copy plan) also no longer raises.
    plan = _poster_copy_plan(project)
    assert plan.title == "2026 Graduation Party Special"


# --- Slice 2 Task 1: generic OpenRouter image-edit helper ---------------------


def test_openrouter_image_edit_bytes_posts_base_image_and_returns_decoded_bytes(tmp_path, monkeypatch):
    """The extracted generic helper base64-encodes the supplied base image, sends
    the prompt as the text part, and decodes the returned data-URL to bytes."""
    base = tmp_path / "base.png"
    base_bytes = _png_bytes(color=(10, 20, 30))
    base.write_bytes(base_bytes)
    out_bytes = _png_bytes(color=(40, 90, 50))
    requests = []

    class _Resp:
        def __enter__(self):
            data_url = "data:image/png;base64," + base64.b64encode(out_bytes).decode("ascii")
            self._body = json.dumps({
                "choices": [{"message": {"images": [{"image_url": {"url": data_url}}]}}],
            }).encode("utf-8")
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    def _fake_urlopen(req, timeout):
        requests.append((req, timeout, json.loads(req.data.decode("utf-8"))))
        return _Resp()

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _fake_urlopen)

    result = render_module._openrouter_image_edit_bytes(
        base_image_path=base,
        mime="image/png",
        prompt="Change ONLY the spelling.",
        size=(1080, 1350),
        model="google/gemini-3.1-flash-image-preview",
        quality="high",
    )

    assert result == out_bytes
    assert len(requests) == 1
    req, timeout, payload = requests[0]
    assert "openrouter.ai" in req.full_url
    assert timeout == 180
    assert req.headers["Authorization"] == "Bearer sk-or-test"
    assert payload["model"] == "google/gemini-3.1-flash-image-preview"
    assert payload["modalities"] == ["image", "text"]
    assert payload["image_config"]["image_size"] == "2K"
    # The prompt is the text part; the base image is the image_url part.
    content = payload["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Change ONLY the spelling."}
    expected_data_url = "data:image/png;base64," + base64.b64encode(base_bytes).decode("ascii")
    assert content[1] == {"type": "image_url", "image_url": {"url": expected_data_url}}


def test_openrouter_image_edit_bytes_missing_key_raises(tmp_path, monkeypatch):
    base = tmp_path / "base.png"
    base.write_bytes(_png_bytes())
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(render_module, "_read_env_value", lambda _name: "")
    with pytest.raises(FlyerRenderError):
        render_module._openrouter_image_edit_bytes(
            base_image_path=base,
            mime="image/png",
            prompt="x",
            size=(1080, 1350),
            model="m",
            quality="high",
        )


def test_openrouter_image_edit_bytes_http_error_raises_render_error(tmp_path, monkeypatch):
    base = tmp_path / "base.png"
    base.write_bytes(_png_bytes())

    def _boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b"server error"))

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("agents.flyer.render.urllib.request.urlopen", _boom)
    with pytest.raises(FlyerRenderError) as exc:
        render_module._openrouter_image_edit_bytes(
            base_image_path=base,
            mime="image/png",
            prompt="x",
            size=(1080, 1350),
            model="m",
            quality="high",
        )
    assert "500" in str(exc.value)


# --- Slice 2 Task 2: repair-edit render mode + flag ---------------------------


def test_premium_repair_enabled_flag_gating(monkeypatch):
    project = _english_project()
    # OFF by default.
    monkeypatch.delenv("FLYER_PREMIUM_REPAIR", raising=False)
    monkeypatch.delenv("FLYER_PREMIUM_REPAIR_ALLOWLIST", raising=False)
    assert render_module._premium_repair_enabled(project) is False
    # Anything other than exactly "1" is OFF.
    monkeypatch.setenv("FLYER_PREMIUM_REPAIR", "true")
    assert render_module._premium_repair_enabled(project) is False
    # Flag == "1" with NO allowlist → global ON.
    monkeypatch.setenv("FLYER_PREMIUM_REPAIR", "1")
    assert render_module._premium_repair_enabled(project) is True


def test_premium_repair_enabled_allowlist_scopes_by_customer_phone(monkeypatch):
    project = _english_project().model_copy(update={"customer_phone": "+17329837841"})
    monkeypatch.setenv("FLYER_PREMIUM_REPAIR", "1")
    # Allowlist set but does NOT contain this sender → OFF.
    monkeypatch.setenv("FLYER_PREMIUM_REPAIR_ALLOWLIST", "+19998887777")
    assert render_module._premium_repair_enabled(project) is False
    # Allowlist contains this sender (format-variant tolerant) → ON.
    monkeypatch.setenv("FLYER_PREMIUM_REPAIR_ALLOWLIST", "1 732-983-7841, +19998887777")
    assert render_module._premium_repair_enabled(project) is True


def _repair_project() -> FlyerProject:
    return _complete_project().model_copy(update={
        "customer_phone": "+17329837841",
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Street Snack Specials", source="customer_text", required=True),
        ],
    })


def test_render_repair_edit_writes_edit_bytes_without_overlay(tmp_path, monkeypatch):
    """render_repair_edit ships the model's premium text verbatim — the written
    preview is the model edit (NO deterministic overlay re-composed) and NO raw
    background sidecar is produced (the preview IS the authoritative artifact)."""
    project = _repair_project()
    base_png = tmp_path / "F0001-C1-base.png"
    base_png.write_bytes(_png_bytes(color=(12, 34, 56)))
    # A distinctly-coloured edit so we can assert the preview derives from the
    # edit bytes (not the base or a deterministic overlay composite).
    edit_bytes = _png_bytes(color=(70, 180, 90))

    edit_calls = []

    def _fake_edit(*, base_image_path, mime, prompt, size, model, quality):
        edit_calls.append({"base": Path(base_image_path), "mime": mime, "prompt": prompt, "size": size, "model": model, "quality": quality})
        return edit_bytes

    overlay_calls = []
    monkeypatch.setattr(render_module, "_openrouter_image_edit_bytes", _fake_edit)
    monkeypatch.setattr(render_module, "_apply_critical_text_overlay", lambda *a, **k: overlay_calls.append(1))
    monkeypatch.setattr(render_module, "apply_critical_text_overlay", lambda *a, **k: overlay_calls.append(1))

    spec = render_module.render_repair_edit(
        project,
        base_png,
        tmp_path,
        repair_instruction="Edit this exact flyer. Change ONLY: fix the spelling.",
        model="google/gemini-3.1-flash-image-preview",
        quality="high",
    )

    assert spec.kind == "concept_preview"
    assert spec.concept_id == "C1"
    assert spec.path == tmp_path / "F0001-C1-preview.png"
    # NO deterministic overlay was applied (the model's premium text is preserved).
    assert overlay_calls == []
    # The preview is the model edit (1080x1350, derived from the green edit bytes,
    # NOT a deterministic-overlay composite). Compare the dominant top-strip hue
    # of the written preview to the edit bytes.
    from PIL import Image
    with Image.open(spec.path) as written, Image.open(io.BytesIO(edit_bytes)) as expected:
        assert written.size == (1080, 1350)
        wp = written.convert("RGB").resize((8, 8)).getpixel((4, 4))
        ep = expected.convert("RGB").resize((8, 8)).getpixel((4, 4))
        assert max(abs(a - b) for a, b in zip(wp, ep)) <= 12
    # No raw-background sidecar (the repair preview is authoritative; unlike
    # source-edit there is no overlay to re-apply per final format).
    assert not render_module._raw_background_path(spec.path).exists()
    # The base image (prior premium render) was the edit base, not the reference.
    assert len(edit_calls) == 1
    assert edit_calls[0]["base"] == base_png
    assert edit_calls[0]["prompt"].startswith("Edit this exact flyer")
    assert edit_calls[0]["size"] == (1080, 1350)
    assert edit_calls[0]["model"] == "google/gemini-3.1-flash-image-preview"
    # A text manifest was written alongside.
    assert Path(f"{spec.path}.text.json").exists()


def test_render_repair_edit_quality_failure_raises_and_cleans_up(tmp_path, monkeypatch):
    project = _repair_project()
    base_png = tmp_path / "F0001-C1-base.png"
    base_png.write_bytes(_png_bytes())

    # Valid PNG bytes (so the contained-write succeeds), but the quality check is
    # forced to fail → render_repair_edit must fail-closed and leave no orphan.
    monkeypatch.setattr(render_module, "_openrouter_image_edit_bytes", lambda **k: _png_bytes(size=(64, 64)))
    monkeypatch.setattr(
        render_module,
        "inspect_rendered_asset",
        lambda *a, **k: render_module.RenderedAssetQuality(False, ["blank or low-variance image"], []),
    )

    with pytest.raises(FlyerRenderError):
        render_module.render_repair_edit(
            project,
            base_png,
            tmp_path,
            repair_instruction="x",
            model="m",
            quality="high",
        )
    # No orphan preview / manifest left behind.
    assert not (tmp_path / "F0001-C1-preview.png").exists()
    assert not Path(str(tmp_path / "F0001-C1-preview.png") + ".text.json").exists()


def test_deterministic_recovery_enabled_respects_flag_and_allowlist(monkeypatch):
    from agents.flyer import render as r
    from schemas import FlyerProject
    from datetime import datetime, timezone
    proj = FlyerProject(
        project_id="F0174", status="intake_started", customer_phone="+17329837841",
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        original_message_id="m", raw_request="x", locked_facts=[],
    )
    monkeypatch.delenv("FLYER_DETERMINISTIC_RECOVERY", raising=False)
    assert r._deterministic_recovery_enabled(proj) is False
    monkeypatch.setenv("FLYER_DETERMINISTIC_RECOVERY", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    assert r._deterministic_recovery_enabled(proj) is True
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+19999999999")
    assert r._deterministic_recovery_enabled(proj) is False


def test_force_background_only_uses_overlay_for_integrated_eligible(monkeypatch, tmp_path):
    from agents.flyer import render as r
    from schemas import FlyerProject, FlyerLockedFact
    from datetime import datetime, timezone
    import pathlib
    proj = FlyerProject(
        project_id="F0174", status="intake_started", customer_phone="+17329837841",
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        original_message_id="m", raw_request="Any item $7.99",
        locked_facts=[FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile")],
    )
    monkeypatch.setattr(r, "_integrated_poster_eligible", lambda p: True)
    monkeypatch.setattr(r, "_openrouter_image_bytes", lambda *a, **k: b"fakebgbytes")
    monkeypatch.setattr(r, "_write_generated_image", lambda raw, path, *, size: pathlib.Path(path).write_bytes(raw))
    calls = {"overlay": 0}
    def fake_overlay(project, source, target, *, size, output_format):
        calls["overlay"] += 1
        pathlib.Path(target).write_bytes(b"overlaid")
    monkeypatch.setattr(r, "_apply_critical_text_overlay", fake_overlay)
    target = tmp_path / "F0174-C1.png"
    r._render_model(proj, target, concept_id="C1", output_format="concept_preview",
                    size=(1080, 1350), model="google/gemini-3.1-flash-image-preview",
                    quality="high", force_background_only=True)
    assert calls["overlay"] == 1


def test_build_image_generation_prompt_force_background_only_emits_textless_contract(monkeypatch):
    """BLOCKER 1: for an integrated-eligible project, build_image_generation_prompt(
    force_background_only=True) must emit the textless background contract
    ('Do NOT draw any text') and must NOT contain 'full restaurant/menu poster'.
    Before the fix, _poster_layout_requirements() was called without
    force_background_only, so an integrated-eligible project (where
    _background_only_eligible() is False) fell into the full-poster branch and
    baked garbled text into the supposedly-textless background.
    """
    from agents.flyer import render as r
    from agents.flyer.render import build_image_generation_prompt
    from schemas import FlyerProject, FlyerLockedFact, FlyerRequestFields
    from datetime import datetime, timezone

    proj = FlyerProject(
        project_id="F0174",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        original_message_id="m-F0174",
        raw_request="Weekend Specials. Any item $7.99. Idli, Dosa, Vada.",
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen",
            venue_or_location="90 Brybar Dr St Johns FL",
            contact_info="+17329837841",
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile"),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Idli", source="customer_text"),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$7.99", source="customer_text"),
        ],
    )
    # Force this project into the integrated-eligible branch
    # (_background_only_eligible will be False for it, which is the bug trigger)
    monkeypatch.setattr(r, "_integrated_poster_eligible", lambda p: True)
    monkeypatch.setattr(r, "_background_only_eligible", lambda p: False)

    prompt = build_image_generation_prompt(proj, concept_id="C1", output_format="concept_preview", size=(1080, 1350), force_background_only=True)

    assert "Do NOT draw any text" in prompt or "decorative BACKGROUND image only" in prompt, (
        "force_background_only=True must emit the textless contract in the prompt"
    )
    assert "full restaurant/menu poster" not in prompt, (
        "force_background_only=True must NOT emit the full-poster instruction"
    )


# ---------------------------------------------------------------------------
# Context-var gate tests (Codex round 2 — prompt-leak closure)
# ---------------------------------------------------------------------------

def _f0174_integrated_project():
    from schemas import FlyerProject, FlyerLockedFact
    from datetime import datetime, timezone
    facts = [
        FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile"),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Weekend Specials", source="customer_text"),
        FlyerLockedFact(fact_id="pricing_structure", label="Pricing", value="Any item $7.99", source="customer_text"),
        FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile"),
        FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr", source="customer_profile"),
    ]
    for i, nm in enumerate(["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar"]):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label=f"Item{i}", value=nm, source="customer_text"))
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:price", label=f"Price{i}", value="$7.99", source="customer_text"))
    return FlyerProject(
        project_id="F0174", status="intake_started", customer_phone="+17329837841",
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc), updated_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        original_message_id="m", raw_request="Weekend Specials. Any item $7.99. Idli, Dosa, Vada, Uttapam, Pongal, Sambar.",
        locked_facts=facts,
    )


def test_force_background_only_prompt_has_no_text_leak(monkeypatch):
    import re
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    p = _f0174_integrated_project()
    assert r._integrated_poster_eligible(p) is True  # integrated-eligible baseline
    tok = r._FORCE_BACKGROUND_ONLY.set(True)
    try:
        prompt = r.build_image_generation_prompt(p, concept_id="C1", output_format="concept_preview", size=(1080, 1350), force_background_only=True)
    finally:
        r._FORCE_BACKGROUND_ONLY.reset(tok)
    # "Create exactly" is the affirmative card-directive leak (not the prohibition in text_contract_line)
    # "menu item cards" in the text_contract_line is correct ("do NOT render...menu item cards") — we
    # check for the affirmative creation form instead: "Create exactly N menu item cards"
    for leak in ["Create exactly", "full restaurant/menu poster", "complete integrated poster layout", "Item cards must look"]:
        assert leak not in prompt, f"text leak under force: {leak!r}"
    # The affirmative card-creation directive must not appear (the prohibition in text_contract_line is correct)
    assert "Create exactly" not in prompt, "affirmative item-card directive leaked under force"
    assert not re.search(r"-\s+\w[\w &'-]* - \$", prompt), "priced item row leaked under force"
    assert ("do NOT render them as text" in prompt) or ("decorative BACKGROUND" in prompt)


def test_genuine_background_only_prompt_unchanged(monkeypatch):
    # force OFF + cvar unset: a genuine background-only project must KEEP the
    # existing item-card directive (byte-identical — we did not touch bg-only).
    from agents.flyer import render as r
    monkeypatch.setattr(r, "_integrated_poster_eligible", lambda proj: False)  # => background_only_eligible True
    p = _f0174_integrated_project()
    assert r._FORCE_BACKGROUND_ONLY.get() is False
    prompt = r.build_image_generation_prompt(p, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    assert "menu item cards" in prompt  # bg-only directive preserved


# ---------------------------------------------------------------------------
# MAJOR-1: deterministic_recovery flag keeps draft+final on overlay path
# MAJOR-2: build_image_generation_prompt force param is self-sufficient
# ---------------------------------------------------------------------------

def test_deterministic_recovery_flag_disables_integrated_eligibility(monkeypatch):
    """MAJOR-1: a project with deterministic_recovery=True must be ineligible for
    integrated-poster mode AND eligible for background-only, EVEN when
    FLYER_ALLOW_INTEGRATED_POSTER=1 and the cvar is unset.
    This proves the persisted flag drives both draft and final export."""
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    # Base: no flag set — integrated eligible
    p = _f0174_integrated_project()
    assert r._FORCE_BACKGROUND_ONLY.get() is False
    assert r._integrated_poster_eligible(p) is True
    assert r._background_only_eligible(p) is False
    # Set the persistent flag
    p_recovered = p.model_copy(update={"deterministic_recovery": True})
    # Must NOT be integrated-eligible, cvar still unset
    assert r._FORCE_BACKGROUND_ONLY.get() is False
    assert r._integrated_poster_eligible(p_recovered) is False
    # Must be background-only eligible (overlay owns all text)
    assert r._background_only_eligible(p_recovered) is True


def test_build_image_generation_prompt_force_param_self_sufficient(monkeypatch):
    """MAJOR-2: force_background_only=True on build_image_generation_prompt must
    be sufficient to suppress integrated-poster text even WITHOUT the caller
    manually setting _FORCE_BACKGROUND_ONLY.

    Before the fix, _campaign_scene_block_for_project called _integrated_poster_eligible
    which reads _FORCE_BACKGROUND_ONLY directly — so if the cvar was not set,
    an integrated-eligible project routed to a non-family scene would emit
    'complete integrated poster layout' even though force_background_only=True
    was passed as a parameter.

    We force a non-family scene context via monkeypatching _visual_prompt_context
    so the storefront_service branch (where the bug manifests) is always selected,
    independent of the project's default scene selection.
    """
    from agents.flyer import render as r
    from agents.flyer.render import build_image_generation_prompt
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    p = _f0174_integrated_project()
    # Confirm the project is integrated-eligible and cvar is NOT set by us
    assert r._integrated_poster_eligible(p) is True
    assert r._FORCE_BACKGROUND_ONLY.get() is False
    # Force a non-family scene (storefront_service) so the bug path is exercised.
    # Without this the _f0174 visual context happens to produce family_discovery which
    # short-circuits the reserved_zone selection (and hides the bug).
    monkeypatch.setattr(r, "_visual_prompt_context", lambda proj: "taco tuesday special items menu")
    # Call WITHOUT manually setting the cvar — the param alone must suffice
    prompt = build_image_generation_prompt(
        p, concept_id="C1", output_format="concept_preview",
        size=(1080, 1350), force_background_only=True,
    )
    assert "complete integrated poster layout" not in prompt, (
        "force_background_only=True must suppress 'complete integrated poster layout' "
        "even without the caller setting the cvar"
    )
    assert "Create exactly" not in prompt, (
        "force_background_only=True must suppress the affirmative card-creation directive"
    )
    # And the textless contract IS present
    assert ("do NOT render" in prompt) or ("decorative BACKGROUND" in prompt), (
        "textless background contract must be present when force_background_only=True"
    )


def test_deterministic_recovery_default_false_byte_identical(monkeypatch):
    """A fresh integrated-eligible project (no deterministic_recovery field) must
    still be integrated-eligible — proves default=False changes nothing."""
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    p = _f0174_integrated_project()
    # Default: deterministic_recovery is False (or absent)
    assert getattr(p, "deterministic_recovery", False) is False
    assert r._integrated_poster_eligible(p) is True
    assert r._background_only_eligible(p) is False


def test_deterministic_recovery_project_prompt_is_textless_without_force(monkeypatch):
    """A persisted-recovered project (deterministic_recovery=True) rendered WITHOUT
    force_background_only / cvar (a subsequent render/revision) must STILL produce a
    textless prompt — no 'Create exactly N menu item cards' / priced rows."""
    import re
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    p = _f0174_integrated_project().model_copy(update={"deterministic_recovery": True})
    assert r._FORCE_BACKGROUND_ONLY.get() is False  # no transient force in play
    prompt = r.build_image_generation_prompt(
        p, concept_id="C1", output_format="concept_preview", size=(1080, 1350)
    )
    assert "Create exactly" not in prompt
    assert not re.search(r"-\s+\w[\w &'-]* - \$", prompt), "priced item row leaked for recovered project"
    assert ("do NOT render them as text" in prompt) or ("decorative BACKGROUND" in prompt)


def test_background_only_prompt_requests_food_hero_no_people(monkeypatch):
    # SCOPED: the food-hero directive appears only when the premium overlay is
    # enabled (FLYER_PREMIUM_OVERLAY). Here the flag is on (empty allowlist ⇒ global).
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    p = _f0174_integrated_project()  # existing helper in this file
    tok = r._FORCE_BACKGROUND_ONLY.set(True)
    try:
        prompt = r.build_image_generation_prompt(p, concept_id="C1", output_format="concept_preview", size=(1080, 1350), force_background_only=True)
    finally:
        r._FORCE_BACKGROUND_ONLY.reset(tok)
    low = prompt.lower()
    assert "no people" in low and ("no faces" in low or "no hands" in low)
    assert "close-up" in low or "hero" in low
    assert ("do not draw any text" in low) or ("do not render" in low)  # text guarantee retained


def test_background_only_food_hero_directive_absent_when_premium_overlay_off(monkeypatch):
    # Flag-off byte-identical: with FLYER_PREMIUM_OVERLAY unset, the background-only
    # prompt must NOT gain the food-hero directive (the v2 scoping contract).
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY", raising=False)
    p = _f0174_integrated_project()
    tok = r._FORCE_BACKGROUND_ONLY.set(True)
    try:
        prompt = r.build_image_generation_prompt(p, concept_id="C1", output_format="concept_preview", size=(1080, 1350), force_background_only=True)
    finally:
        r._FORCE_BACKGROUND_ONLY.reset(tok)
    low = prompt.lower()
    assert "food-hero" not in low and "the food itself is the hero" not in low
    # the original background contract is still present (byte-identical path)
    assert ("do not draw any text" in low) or ("do not render" in low)


# ---------------------------------------------------------------------------
# Fix C v2.1 — W1 Restaurant-Promo single-hero background directive
# ---------------------------------------------------------------------------

def test_w1_scoped_prompt_is_restaurant_promo_single_hero(monkeypatch):
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    p = _f0174_integrated_project()
    tok = r._FORCE_BACKGROUND_ONLY.set(True)
    try:
        prompt = r.build_image_generation_prompt(p, concept_id="C1", output_format="concept_preview", size=(1080, 1350), force_background_only=True)
    finally:
        r._FORCE_BACKGROUND_ONLY.reset(tok)
    low = prompt.lower()
    assert "one single" in low and "hero dish" in low
    assert "cinematic" in low and ("warm" in low or "golden" in low)
    assert "dominates the frame" in low
    assert "spread of many separate dishes" in low
    assert "vignette" in low
    assert "no people" in low
    assert ("do not draw any text" in low) or ("do not render" in low)
    assert "close-up of the dish(es)" not in low
    assert "reserve visually calm" not in low
    # text-leak fix: hard wordless directive present; no "advertisement" cue
    assert "absolutely no text" in low
    assert "do not imitate an advertisement" in low


def test_w1_flagoff_prompt_byte_identical(monkeypatch):
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY", raising=False)
    p = _f0174_integrated_project()
    tok = r._FORCE_BACKGROUND_ONLY.set(True)
    try:
        prompt = r.build_image_generation_prompt(p, concept_id="C1", output_format="concept_preview", size=(1080, 1350), force_background_only=True)
    finally:
        r._FORCE_BACKGROUND_ONLY.reset(tok)
    low = prompt.lower()
    assert "reserve visually calm" in low
    assert "restaurant-promo" not in low and "hero dish" not in low and "vignette" not in low
    assert ("do not draw any text" in low) or ("do not render" in low)


def test_premium_overlay_outcome_contextvar_consume_and_alert():
    from agents.flyer import render as r
    assert r.consume_premium_overlay_outcome() is None
    out = r.PremiumOverlayOutcome(
        status="premium_overlay_failed_unexpected", reason_class="subprocess_failure",
        reason_detail="RuntimeError: boom", render_path="none", output_format="concept_preview",
    )
    r._PREMIUM_OVERLAY_OUTCOME.set(out)
    got = r.consume_premium_overlay_outcome()
    assert got is out
    assert r.consume_premium_overlay_outcome() is None  # consume resets
    assert r.premium_outcome_should_alert(out) is True
    assert r.premium_outcome_should_alert(
        r.PremiumOverlayOutcome("premium_overlay_delivered", "none", "", "subprocess", "concept_preview")
    ) is False
    assert r.premium_outcome_should_alert(
        r.PremiumOverlayOutcome("premium_overlay_degraded_to_flat", "fit", "", "none", "concept_preview")
    ) is False
    assert r.premium_outcome_should_alert(None) is False


def test_premium_overlay_renderer_string_and_classifier():
    from agents.flyer import render as r
    src = r.PREMIUM_OVERLAY_RENDERER
    assert "model_validate_json" in src
    assert "render_premium_overlay" in src
    assert "sys_path" in src
    assert "sys.exit(3" in src
    assert "sys.exit(1" in src
    assert r._classify_fail_closed_reason("required fact missing: schedule") == "coverage"
    assert r._classify_fail_closed_reason("offer seal overflow") == "overflow"
    assert r._classify_fail_closed_reason("text cannot fit the panel") == "fit"
