"""Background generation must not receive competing finished-poster copy."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agents.flyer import render
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields


def project():
    now = datetime.now(timezone.utc)
    return FlyerProject(project_id="F9401", status="generating_concepts",
        customer_phone="+15550100001", created_at=now, updated_at=now,
        original_message_id="synthetic", raw_request="Lunch Buffet $12.99",
        fields=FlyerRequestFields(style_preference="Paint SECRET STYLE COPY"),
        locked_facts=[FlyerLockedFact(fact_id=k, label=k, value=v,
            source="customer_text", required=True) for k, v in [
            ("business_name", "Synthetic Kitchen"), ("campaign_title", "Lunch Buffet"),
            ("pricing_structure", "$12.99"), ("contact_phone", "+15550100001")]])


@pytest.mark.parametrize("forced", [False, True])
def test_background_excludes_finished_poster_inputs(monkeypatch, forced):
    monkeypatch.setattr(render, "_background_only_eligible", lambda _: not forced)
    monkeypatch.setattr(render, "_premium_overlay_enabled", lambda _: True)
    monkeypatch.setattr(render, "_scene_block_from_visual_direction", lambda _: "PAINT SCENE COPY")
    prompt = render._image_prompt(project(), concept_id="C1", output_format="concept_preview",
        size=(1080, 1350), scene_direction=object(), force_background_only=forced,
        repair_instruction="Paint REPAIR COPY")
    for forbidden in ["complete, finished", "Synthetic Kitchen", "Lunch Buffet", "$12.99",
                      "+15550100001", "SECRET STYLE COPY", "PAINT SCENE COPY", "REPAIR COPY",
                      "Customer brand assets to honor", "Controlled customer copy"]:
        assert forbidden not in prompt
    assert "BACKGROUND" in prompt
    assert "buffet" in prompt


def test_background_retains_reference_photo_but_not_brand_templates(tmp_path, monkeypatch):
    logo = tmp_path / "logo.png"
    photo = tmp_path / "photo.png"
    logo.write_bytes(b"logo-with-text")
    photo.write_bytes(b"food-photo")
    monkeypatch.setattr(render, "_background_only_eligible", lambda _: True)
    monkeypatch.setattr(render, "_premium_overlay_enabled", lambda _: True)
    monkeypatch.setattr(render, "_generation_brand_assets", lambda _: [
        SimpleNamespace(path=str(logo), mime_type="image/png", kind="logo")])
    monkeypatch.setattr(render, "_project_reference_assets", lambda _: [
        SimpleNamespace(path=str(photo), mime_type="image/png", kind="reference_image")])
    content = render._image_message_content(project(), concept_id="C1",
        output_format="concept_preview", size=(1080, 1350))
    assert len(content) == 2
    assert content[1]["image_url"]["url"].endswith("Zm9vZC1waG90bw==")


def test_background_preserves_structured_palette_and_motifs(monkeypatch):
    monkeypatch.setattr(render, "_background_only_eligible", lambda _: True)
    monkeypatch.setattr(render, "_premium_overlay_enabled", lambda _: True)
    prompt = render._image_prompt(project(), concept_id="C1", output_format="concept_preview",
        size=(1080, 1350), scene_direction=SimpleNamespace(palette=["emerald", "gold"], motifs=["subtle leaves"]))
    assert "emerald, gold" in prompt
    assert "subtle leaves" in prompt


def test_background_preserves_confirmed_cuisine_from_structured_scene(monkeypatch):
    from agents.flyer.flyer_brief import VisualDirection
    monkeypatch.setattr(render, "_background_only_eligible", lambda _: True)
    monkeypatch.setattr(render, "_premium_overlay_enabled", lambda _: True)
    scene = VisualDirection(theme_family="South Indian lunch buffet",
                            visual_subjects=["South Indian vegetarian buffet"])
    prompt = render._image_prompt(project(), concept_id="C1", output_format="concept_preview",
        size=(1080, 1350), scene_direction=scene)
    assert "South Indian lunch buffet" in prompt
    assert "South Indian vegetarian buffet" in prompt
    assert "Synthetic Kitchen" not in prompt
    assert "complete, finished" not in prompt


def test_integrated_poster_still_receives_customer_copy(monkeypatch):
    monkeypatch.setattr(render, "_background_only_eligible", lambda _: False)
    monkeypatch.setattr(render, "_premium_overlay_enabled", lambda _: True)
    prompt = render._image_prompt(project(), concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    assert "complete, finished" in prompt
    assert "Synthetic Kitchen" in prompt
    assert "$12.99" in prompt
