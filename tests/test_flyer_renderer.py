"""Renderer tests for production Flyer Studio assets."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import base64
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.render import (  # noqa: E402
    FlyerRenderError,
    _image_message_content,
    _image_prompt,
    build_asset_manifest,
    render_concept_previews,
    render_final_package,
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
    assert "Date: " not in prompt
    assert "Time: " not in prompt
    assert "Venue: " not in prompt


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
            png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")
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
    assert "Menu/details to include when relevant" in prompt
    assert "Idly (3 PCS) - $7.99" in prompt
    assert "recurring schedule" in prompt
    assert specs[0].path.read_bytes().startswith(b"\x89PNG")


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
