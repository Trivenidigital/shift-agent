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
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields  # noqa: E402


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
    restaurant_menu = "indian restaurant south indian snacks professional local food menu flyer"
    assert select_campaign_scene(festival).key == "family_discovery"
    assert select_campaign_scene(sale).key == "human_billboard"
    assert select_campaign_scene(plain).key == "storefront_service"
    assert select_campaign_scene(restaurant_menu).key != "family_discovery"
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


def test_image_prompt_scene_uses_locked_facts_over_negative_raw_instruction_terms(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))  # no customers.json
    raw_request = (
        "Create a modern professional flyer for my digital marketing agency. "
        "No food or festival visuals unless I ask for them."
    )
    project = _project(raw_request, business="Growth Marketing Services").model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Growth Marketing Services",
            style_preference="clean premium agency style",
        ),
        "locked_facts": [
            FlyerLockedFact(
                fact_id="business_name",
                label="Business",
                value="Chloe Growth Studio",
                source="customer_profile",
            ),
            FlyerLockedFact(
                fact_id="campaign_title",
                label="Campaign",
                value="Grow Your Business with Digital Marketing",
                source="customer_text",
            ),
        ],
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "modern digital marketing services flyer" in prompt
    assert "family discovery" not in prompt.lower()
    assert "no food or festival visuals" not in prompt.lower()


def test_image_prompt_scene_keeps_positive_style_preference_with_locked_facts(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))  # no customers.json
    project = _project(
        "Create a flyer for the weekend growth plan.",
        business="Apex Digital Studio",
    ).model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Apex Digital Studio",
            style_preference="festive Diwali theme with families enjoying biryani",
        ),
        "locked_facts": [
            FlyerLockedFact(
                fact_id="business_name",
                label="Business",
                value="Apex Digital Studio",
                source="customer_profile",
            ),
            FlyerLockedFact(
                fact_id="campaign_title",
                label="Campaign",
                value="Weekend Growth Plan",
                source="customer_text",
            ),
        ],
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "family discovery" in prompt.lower()


def test_image_prompt_scene_ignores_negated_style_preference_terms(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))  # no customers.json
    project = _project(
        "Create a flyer for the weekend growth plan.",
        business="Apex Digital Studio",
    ).model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Apex Digital Studio",
            style_preference="clean modern design; no food or festival visuals",
        ),
        "locked_facts": [
            FlyerLockedFact(
                fact_id="business_name",
                label="Business",
                value="Apex Digital Studio",
                source="customer_profile",
            ),
            FlyerLockedFact(
                fact_id="campaign_title",
                label="Campaign",
                value="Weekend Growth Plan",
                source="customer_text",
            ),
        ],
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "family discovery" not in prompt.lower()
    assert "no food or festival visuals" in prompt.lower()


def test_image_prompt_scene_preserves_positive_style_after_negated_subclause(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))  # no customers.json
    project = _project(
        "Create a flyer for the weekend growth plan.",
        business="Apex Digital Studio",
    ).model_copy(update={
        "fields": FlyerRequestFields(
            event_or_business_name="Apex Digital Studio",
            style_preference="clean modern design, no clutter, festive Diwali theme with families enjoying biryani",
        ),
        "locked_facts": [
            FlyerLockedFact(
                fact_id="business_name",
                label="Business",
                value="Apex Digital Studio",
                source="customer_profile",
            ),
            FlyerLockedFact(
                fact_id="campaign_title",
                label="Campaign",
                value="Weekend Growth Plan",
                source="customer_text",
            ),
        ],
    })

    prompt = _image_prompt(project, concept_id="C1", output_format="concept_preview", size=(1080, 1350))

    assert "family discovery" in prompt.lower()
