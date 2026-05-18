"""Renderer tests for production Flyer Studio assets."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import base64
import io
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.render import (  # noqa: E402
    FlyerRenderError,
    _image_message_content,
    _image_prompt,
    _menu_overlay_payload,
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
from schemas import FlyerAsset, FlyerConcept, FlyerProject, FlyerRequestFields  # noqa: E402


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
        render_source_edit_preview(project, tmp_path, model="gpt-image-1", quality="medium")
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

    render_source_edit_preview(project, tmp_path, model="gpt-image-1", quality="medium")

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


def test_real_image_model_concept_uses_direct_poster_output_without_overlay(tmp_path, monkeypatch):
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
    assert not specs[0].path.with_name(f"{specs[0].path.stem}.raw.png").exists()


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
