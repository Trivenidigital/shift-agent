"""Tests for the Flyer Studio campaign-scene prompt template library."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.campaign_scene_prompts import (  # noqa: E402
    CAMPAIGN_SCENE_TEMPLATES,
    CampaignSceneTemplate,
    campaign_scene_prompt_block,
    render_campaign_scene_block,
    select_campaign_scene,
)
from agents.flyer.render import _image_prompt  # noqa: E402
from schemas import FlyerProject, FlyerRequestFields  # noqa: E402


def _project(raw_request: str, *, business: str = "Test Biz") -> FlyerProject:
    return FlyerProject(
        project_id="F0001",
        status="generating_concepts",
        customer_phone="+19045550123",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="wamid.1",
        raw_request=raw_request,
        fields=FlyerRequestFields(event_or_business_name=business),
    )


def test_registry_completeness():
    keys = [t.key for t in CAMPAIGN_SCENE_TEMPLATES]
    assert "human_billboard" in keys
    assert "family_discovery" in keys
    # a storefront/service-oriented fallback is present
    assert any("storefront" in k or "service" in k for k in keys)
    # keys unique; every entry is a frozen template with a non-empty scene block
    assert len(keys) == len(set(keys))
    for t in CAMPAIGN_SCENE_TEMPLATES:
        assert isinstance(t, CampaignSceneTemplate)
        assert t.scene_block.strip()


def test_variable_substitution_leaves_no_unresolved_placeholders():
    for t in CAMPAIGN_SCENE_TEMPLATES:
        block = render_campaign_scene_block(t, business="Lakshmi's Kitchen", offer="Any dosa $6.99", audience="families")
        assert "{" not in block and "}" not in block
        assert "Lakshmi's Kitchen" in block
    # empty variables still resolve to safe defaults — never leave placeholders
    block = render_campaign_scene_block(CAMPAIGN_SCENE_TEMPLATES[0], business="", offer="", audience="")
    assert "{" not in block and "}" not in block


def test_selection_is_deterministic_and_maps_expected_templates():
    festival = "diwali festival dinner for families at our restaurant"
    sale = "grand opening sale 50% off at our salon, limited time"
    plain = "we provide professional help"
    assert select_campaign_scene(festival).key == "family_discovery"
    assert select_campaign_scene(sale).key == "human_billboard"
    assert select_campaign_scene(plain).key == "storefront_service"
    # deterministic: same input → same output, repeatedly
    assert {select_campaign_scene(festival).key for _ in range(5)} == {"family_discovery"}
    assert {select_campaign_scene(plain).key for _ in range(5)} == {"storefront_service"}


def test_image_prompt_includes_selected_campaign_scene_block(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))  # no customers.json → category=""
    festival = _project("diwali festival dinner for families at our restaurant", business="Triveni Restaurant")
    prompt = _image_prompt(festival, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    assert "Campaign scene direction" in prompt
    assert "family discovery" in prompt.lower()

    sale = _project("grand opening sale at our salon, limited time only", business="Chloe Hair Studio")
    sale_prompt = _image_prompt(sale, concept_id="C1", output_format="concept_preview", size=(1080, 1350))
    assert "human billboard" in sale_prompt.lower()
