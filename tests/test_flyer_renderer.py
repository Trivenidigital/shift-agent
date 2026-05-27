"""Renderer tests for production Flyer Studio assets."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import base64
import http.client
import io
import json
import subprocess
import sys

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
from schemas import FlyerAsset, FlyerConcept, FlyerLockedFact, FlyerProject, FlyerRequestFields, FlyerRevision  # noqa: E402


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


def _png_bytes(size=(1080, 1350), color=(240, 120, 40)) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size[0], size[1] // 3), fill=(color[2], color[0], color[1]))
    draw.rectangle((0, size[1] * 2 // 3, size[0], size[1]), fill=(color[1] // 2, color[2], color[0] // 2))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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
    project = _complete_project().model_copy(update={"selected_concept_id": "C1"})
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
    assert "Do not add delivery, catering, payment, ordering-channel, or service-availability claims" in prompt
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
    assert "Title is the campaign/product/service headline; Business/brand is the account identity or footer brand." in prompt
    assert "Schedule: Wednesday and Thursday every week" in prompt
    assert "Chicken Biryani - $16.99" in prompt
    assert "Goat Biryani - $18.99" in prompt
    assert "add Price as" not in prompt
    assert "all famous south indian" not in prompt
    assert not any("add price" in text.lower() for text in facts.values())


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

    assert "Use English only" in prompt
    assert "Do not use Telugu, Hindi, or any regional Indian language" in prompt


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
    assert "logo: B0001" in prompt


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
    assert "reference_image: A0001" in content[0]["text"]
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


def test_direct_poster_prompt_includes_exact_menu_copy_and_allows_integrated_text():
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

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "Create a complete, finished customer-ready poster flyer" in prompt
    assert "Render the following text exactly" in prompt
    assert "Weekend Breakfast Specials" in prompt
    assert "Thursday To Sunday | 8 AM TO 11 AM" in prompt
    assert "Poori with Chicken - $14.99" in prompt
    assert "Kheema Dosa - $12.99" in prompt
    assert "+17329837841" in prompt
    assert "brand masthead" in prompt
    assert "item cards" in prompt
    assert "Telugu as the primary flyer language" in prompt
    assert "Do not output an all-English flyer" in prompt
    assert "Do not render readable words" not in prompt
    assert "Leave a premium lower-third area" not in prompt


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
    assert manifest["verification_mode"] == "source_edit_integrity_only"
    assert "inspect the preview visually" in " ".join(manifest["warnings"])
    qa = validate_text_manifest_file(
        spec.path,
        project_id=project.project_id,
        project_version=project.version,
        output_format="concept_preview",
    )
    assert qa.ok is True
    assert any("integrity only" in warning for warning in qa.warnings)



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
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert "Remove extra 08:00" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    manifest = json.loads(Path(f"{spec.path}.text.json").read_text(encoding="utf-8"))
    assert manifest["verification_mode"] == "source_edit_integrity_only"


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


def test_real_image_model_concept_applies_exact_identity_overlay(tmp_path, monkeypatch):
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

    from PIL import Image
    with Image.open(specs[0].path) as preview, Image.open(raw_path) as raw:
        assert preview.getpixel((540, 55)) != raw.getpixel((540, 55))
        assert preview.getpixel((540, 1290)) != raw.getpixel((540, 1290))


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


def test_final_package_ignores_stale_raw_background_when_preview_is_newer(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "F0001-C1-preview.png"
    stale_raw = tmp_path / "F0001-C1-preview.raw.png"
    approved.write_bytes(_png_bytes(color=(20, 120, 40)))
    stale_raw.write_bytes(_png_bytes(color=(140, 20, 20)))
    old_time = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc).timestamp()
    new_time = datetime(2026, 5, 17, 13, 0, tzinfo=timezone.utc).timestamp()
    import os
    os.utime(stale_raw, (old_time, old_time))
    os.utime(approved, (new_time, new_time))
    project = _complete_project().model_copy(update={
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
            )
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
    assert "without a price, show it as a service label" in prompt
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


def test_source_edit_final_package_reuses_approved_preview_even_with_deterministic_model(tmp_path, monkeypatch):
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

    specs = render_final_package(project, tmp_path / "finals", model="deterministic-renderer", quality="medium")

    from PIL import Image
    whatsapp = next(spec for spec in specs if spec.output_format == "whatsapp_image")
    with Image.open(whatsapp.path) as img:
        assert img.getpixel((540, 675)) == (20, 120, 40)


def test_authorized_source_artwork_update_is_treated_as_source_edit_for_finals(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    approved = tmp_path / "F0001-C1-preview.png"
    approved.write_bytes(_png_bytes(size=(1080, 1350), color=(40, 30, 150)))
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
    assert manifest["verification_mode"] == "source_edit_integrity_only"


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
