"""Recorded F0229 content must survive the actual overlay prompt assembly."""
from datetime import datetime, timezone

import pytest

from agents.flyer import render
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields


def project():
    raw = ("Hi, I need a flyer. Business: Lakshmi's Kitchen. "
           "Offer: Lunch Buffet $12.99 per person. "
           "Valid weekdays 11am to 2pm through November 30.")
    now = datetime.now(timezone.utc)
    return FlyerProject(
        project_id="F9229", customer_phone="+15550100001",
        status="generating_concepts", created_at=now, updated_at=now,
        original_message_id="test-f0229", raw_request=raw,
        fields=FlyerRequestFields(event_or_business_name="I need a", notes=raw),
        locked_facts=[FlyerLockedFact(fact_id=k, label=k, value=v,
                                     source="customer_text", required=True)
                      for k, v in [
                          ("business_name", "Lakshmi's Kitchen"),
                          ("pricing_structure", "Lunch Buffet $12.99 per person."),
                          ("schedule", "Valid weekdays 11am to 2pm through November 30."),
                      ]],
    )


def test_pricing_fact_and_labeled_raw_copy_are_not_two_offers():
    details = render._detail_clauses(project())
    assert sum("12.99" in line for line in details) == 1, details


def test_distinct_offer_at_same_price_is_preserved():
    p = project()
    p.fields.notes += " Kids dinner $12.99 on Saturdays."
    details = render._detail_clauses(p)
    assert any("Kids dinner" in line for line in details)


def test_model_separating_campaign_and_price_does_not_duplicate_the_offer():
    p = project()
    p.locked_facts = [f.model_copy(update={"value": "$12.99 per person"})
                      if f.fact_id == "pricing_structure" else f for f in p.locked_facts]
    p.locked_facts.append(FlyerLockedFact(fact_id="campaign_title", label="Title",
                                         value="Lunch Buffet", source="customer_text"))
    details = render._detail_clauses(p)
    assert sum("12.99" in line for line in details) == 1, details


@pytest.mark.parametrize("fragment", ["I need a", "Please make me a", "Can you create a"])
def test_legacy_instruction_fragment_cannot_become_render_title(fragment):
    p = project()
    p.fields.event_or_business_name = fragment
    assert render._display_title(p) != fragment


def test_locked_actual_campaign_remains_the_title():
    p = project()
    p.locked_facts.append(FlyerLockedFact(fact_id="campaign_title", label="Title",
                                         value="Lunch Buffet", source="customer_text"))
    assert render._display_title(p) == "Lunch Buffet"


def test_buffet_offer_does_not_receive_single_dish_no_buffet_instruction(monkeypatch):
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+15550100001")
    prompt = render._poster_layout_requirements(project(), force_background_only=True).lower()
    assert "buffet" in prompt
    assert "no buffet" not in prompt
    assert "one single" not in prompt
    assert "absolutely no text" in prompt


def test_sparse_offer_prompt_does_not_reserve_empty_menu_band(monkeypatch):
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+15550100001")
    prompt = render._image_prompt(project(), concept_id="C1", output_format="concept_preview",
                                  size=(1080, 1350), force_background_only=True).lower()
    assert "bottom ~32%" not in prompt
    assert "top ~" not in prompt
    assert "bottom ~" not in prompt
    assert "reserved overlay zones" not in prompt
    assert "overlay panels" not in prompt
    assert "single exposure" in prompt
    assert "no blurred letterbox panels" in prompt
