"""Pure routing heuristics for Flyer Studio cf-router behavior."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"


def _load_actions():
    module_name = "cf_router_flyer_actions_under_test"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(PLUGIN_DIR / "actions.py"))
    spec = importlib.util.spec_from_loader(module_name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    loader.exec_module(mod)
    return mod


def test_explicit_new_flyer_request_should_not_attach_to_active_project():
    actions = _load_actions()

    assert actions.should_start_new_flyer_over_active(
        "Creare flyer for customer Lakshmi's Kitchen. Items Bobbatlu $2/piece.",
        has_media=False,
    )
    assert actions.should_start_new_flyer_over_active(
        "Create a breakfast menu for tomorrow from 8 AM to 10 AM. "
        "Items to include in the flyer Idli - $4.99, Onion Dosa $6.99.",
        has_media=False,
    )


def test_media_price_change_is_new_template_work_not_logo_upload():
    actions = _load_actions()

    assert actions.should_start_new_flyer_over_active(
        "I'd like you to change non-veg combo price from $14.99 to $16.99",
        has_media=True,
    )
    assert not actions.should_start_new_flyer_over_active("replace logo", has_media=True)


def test_wrong_flyer_correction_starts_new_work_instead_of_mutating_stale_project():
    actions = _load_actions()

    assert actions.should_start_new_flyer_over_active(
        "The one you generated looks completely different to what I had provided. "
        "I had sent you Thursday Dosa night special. You have responded with weekend breakfast flyer.",
        has_media=False,
    )


def test_price_list_and_reference_projects_are_ready_with_minimal_fields():
    actions = _load_actions()

    assert actions.flyer_project_has_required_fields({
        "raw_request": "Items: Bobbatlu $2/piece. Phone: +1 9802005022",
        "fields": {
            "event_or_business_name": "Lakshmi's Kitchen",
            "contact_info": "+1 9802005022",
            "notes": "Items: Bobbatlu $2/piece",
        },
    })
    assert actions.flyer_project_has_required_fields({
        "raw_request": "Create flyer from uploaded template/reference. Customer requested: change price",
        "fields": {
            "event_or_business_name": "Uploaded Flyer Template",
            "notes": "Create flyer from uploaded template/reference. Customer requested: change price",
        },
        "assets": [{"kind": "reference_image"}],
    })


def test_flyer_customer_lookup_matches_owned_account_numbers(tmp_path):
    actions = _load_actions()
    path = tmp_path / "customers.json"
    actions.FLYER_CUSTOMERS_PATH = path
    path.write_text(
        """
{
  "schema_version": 1,
  "next_customer_sequence": 2,
  "next_brand_asset_sequence": 1,
  "customers": [
    {
      "customer_id": "CUST0001",
      "business_name": "Lakshmis Kitchn",
      "business_address": "90 Brybar Dr St Johns FL",
      "primary_chat_id": "17329837841@s.whatsapp.net",
      "onboarded_by_phone": "+17329837841",
      "public_phone": "+19045550100",
      "business_whatsapp_number": "+19045550101",
      "authorized_request_numbers": ["+19045550102"],
      "business_category": "Indian Restaurant",
      "preferred_language": "te",
      "plan_id": "trial",
      "status": "trial",
      "created_at": "2026-05-17T03:06:00Z",
      "updated_at": "2026-05-17T03:06:00Z"
    }
  ],
  "onboarding_sessions": []
}
""".strip(),
        encoding="utf-8",
    )

    assert actions.find_flyer_customer_by_sender("+17329837841", "")["customer_id"] == "CUST0001"
    assert actions.find_flyer_customer_by_sender("+19045550100", "")["customer_id"] == "CUST0001"
    assert actions.find_flyer_customer_by_sender("+19045550101", "")["customer_id"] == "CUST0001"
    assert actions.find_flyer_customer_by_sender("+19045550102", "")["customer_id"] == "CUST0001"


def test_account_commands_are_detected_before_revision_routing_static_contract():
    actions = _load_actions()
    hooks = (PLUGIN_DIR / "hooks.py").read_text(encoding="utf-8")

    assert actions.is_flyer_account_command("STATUS")
    assert actions.is_flyer_account_command("ADD AUTHORIZED NUMBER +19045550199")
    assert "def _try_flyer_account_intercept" in hooks
    assert hooks.index("_try_flyer_account_intercept") < hooks.index("_try_flyer_active_project_intercept")
    assert "trigger_flyer_reserve_quota" in hooks
    assert "trigger_flyer_finalize_usage" in hooks
    assert "trigger_flyer_release_quota" in hooks


def test_free_trial_phrase_starts_flyer_onboarding():
    actions = _load_actions()

    assert actions.is_flyer_onboarding_intent("START FREE TRIAL - I want to try Flyer Studio") is True


def test_act_now_campaign_phrase_starts_flyer_onboarding():
    actions = _load_actions()

    assert actions.is_flyer_onboarding_intent("ACT NOW - Save Time and Money") is True
    assert actions.is_flyer_onboarding_intent("I want to set up Flyer Studio for my business") is True
    assert actions.is_flyer_onboarding_intent("Help me create a beautiful flyer for my business") is True


def test_campaign_cta_labels_are_detected_before_project_creation():
    actions = _load_actions()

    for text in [
        "Start Free Trial",
        "Start Free Trail",
        "Act Now! Save Time and Money",
        "Help me create a beautiful flyer for my business",
        "I want to set up Flyer Studio for my business",
    ]:
        assert actions.is_flyer_campaign_cta(text)
        assert not actions.should_start_new_flyer_over_active(text, has_media=False)


def test_campaign_cta_detection_handles_live_sender_block_wrapper():
    actions = _load_actions()
    text = (
        '[shift-agent-sender v=1 platform=whatsapp phone=null '
        'lid="201975216009469@lid" fromMe=false chat_id="201975216009469@lid"]\n'
        "Help me create a beautiful flyer for my business"
    )

    assert actions.flyer_campaign_cta_text(text) == "Help me create a beautiful flyer for my business"
    assert actions.is_flyer_campaign_cta(text)
    assert not actions.should_start_new_flyer_over_active(text, has_media=False)


def test_extract_flyer_request_after_compound_confirm():
    actions = _load_actions()

    assert actions.extract_flyer_request_after_confirm(
        "CONFIRM. Create a breakfast menu for tomorrow from 8 AM to 10 AM."
    ) == "Create a breakfast menu for tomorrow from 8 AM to 10 AM."
    assert actions.extract_flyer_request_after_confirm("CONFIRM") == ""
