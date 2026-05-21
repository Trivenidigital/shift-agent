"""Contracts for Flyer Studio business-category starter briefs."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agents.flyer import starter_briefs  # noqa: E402


def test_digital_marketing_category_gets_agency_brief():
    brief = starter_briefs.starter_brief_for_category("Digital marketing agency")

    assert brief.category_id == "digital_marketing_agency"
    assert "business growth" in brief.body.lower()
    assert "Social Media Marketing" in brief.body
    assert "no food or festival visuals unless I ask for them" in brief.body


def test_unknown_category_gets_local_business_brief():
    brief = starter_briefs.starter_brief_for_category("custom gifts and printing")

    assert brief.category_id == "local_business"
    assert "Edit anything below" in starter_briefs.starter_brief_message("custom gifts and printing")


def test_category_keywords_match_words_not_substrings():
    restore = starter_briefs.starter_brief_for_category("restore and refinish studio")
    cafeteria = starter_briefs.starter_brief_for_category("cafeteria consulting")

    assert restore.category_id == "local_business"
    assert cafeteria.category_id == "local_business"


def test_all_starter_briefs_are_whatsapp_sized_customer_editable_and_public_safe():
    forbidden = ("Hermes", "system prompt", "developer instruction")

    for brief in starter_briefs.all_starter_briefs():
        message = starter_briefs.starter_brief_message(brief.label, business_name="Demo Business")
        assert len(message) <= 1800
        assert message.startswith("Flyer Studio\n------------\nHere is a starter flyer request.")
        assert "Edit anything below" in message
        assert "Use my saved business name, address, phone, and logo." in message
        assert all(term not in message for term in forbidden)


def test_starter_message_can_include_account_wide_opt_out_hint():
    message = starter_briefs.starter_brief_message(
        "salon",
        business_name="Demo Salon",
        include_opt_out_hint=True,
    )

    assert 'reply "don\'t show sample prompts"' in message


def test_restaurant_starter_idea_choices_are_compact_and_safe():
    ideas = starter_briefs.starter_idea_choices(
        "restaurant",
        business_name="Lakshmi's Kitchen",
        language="en",
    )

    assert len(ideas) == 2
    joined = "\n".join(ideas).lower()
    assert "thali" in joined
    assert "evening snacks" in joined
    for blocked in ("project", "provider", "reason_code", "manual_edit_required", "operator"):
        assert blocked not in joined
    assert all(len(idea) <= 280 for idea in ideas)


def test_starter_idea_message_uses_selected_language_shell_with_numeric_replies():
    message = starter_briefs.starter_idea_choices_message(
        "restaurant",
        business_name="Lakshmi's Kitchen",
        language="te",
    )

    assert "Flyer Studio" in message
    assert "మీకు నచ్చిన ఐడియా" in message
    assert "Reply 1 or 2" in message
    assert "APPROVE" in message
    assert "Lakshmi's Kitchen" in message
    assert "for this business account" in message


def test_ai_powered_claim_only_appears_for_ai_marketing_categories():
    standard = starter_briefs.starter_brief_message("digital marketing agency")
    paid_ads = starter_briefs.starter_brief_message("paid ads agency")
    ai = starter_briefs.starter_brief_message("AI marketing agency")

    assert "AI-Powered" not in standard
    assert "AI-Powered" not in paid_ads
    assert "AI-Powered" in ai
