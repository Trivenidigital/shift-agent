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


def _load_plugin_modules():
    pkg_name = "cf_router_flyer_pkg_under_test"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]

    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_name] = pkg_mod

    actions_full = f"{pkg_name}.actions"
    actions_loader = importlib.machinery.SourceFileLoader(actions_full, str(PLUGIN_DIR / "actions.py"))
    actions_spec = importlib.util.spec_from_loader(actions_full, actions_loader)
    actions_mod = importlib.util.module_from_spec(actions_spec)
    sys.modules[actions_full] = actions_mod
    actions_loader.exec_module(actions_mod)

    hooks_full = f"{pkg_name}.hooks"
    hooks_loader = importlib.machinery.SourceFileLoader(hooks_full, str(PLUGIN_DIR / "hooks.py"))
    hooks_spec = importlib.util.spec_from_loader(hooks_full, hooks_loader)
    hooks_mod = importlib.util.module_from_spec(hooks_spec)
    sys.modules[hooks_full] = hooks_mod
    hooks_loader.exec_module(hooks_mod)
    return hooks_mod, actions_mod


def _load_reference_scope_script():
    module_name = "flyer_reference_scope_under_test"
    sys.modules.pop(module_name, None)
    script = REPO / "src" / "agents" / "flyer" / "scripts" / "check-flyer-reference-scope"
    loader = importlib.machinery.SourceFileLoader(module_name, str(script))
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
    assert actions.should_start_new_flyer_over_active(
        "I'd like you to update this flyer. Change Sunil Pantra name to Srini Yalavarthi. "
        "Change date from May 16 to May 22.",
        has_media=True,
    )
    assert actions.should_start_new_flyer_over_active(
        "Please change the date and venue on the attached image.",
        has_media=True,
    )
    assert not actions.should_start_new_flyer_over_active("replace logo", has_media=True)


def test_media_exact_reference_edit_is_not_new_poster_generation():
    actions = _load_actions()

    assert actions.is_exact_reference_edit_request(
        "I'd like you to Remove that extra 08:00. Add Any Item for $9.99.",
        has_media=True,
    )
    assert actions.is_exact_reference_edit_request(
        "Please update this flyer. Change the date from May 16 to May 22.",
        has_media=True,
    )
    assert actions.is_exact_reference_edit_request(
        "Remove extra 08:00 from this image.",
        has_media=True,
    )
    assert actions.is_exact_reference_edit_request(
        "Make this say Grand Opening.",
        has_media=True,
    )
    assert actions.is_exact_reference_edit_request(
        "Make this flyer say Sunday.",
        has_media=True,
    )
    assert actions.is_exact_reference_edit_request(
        "Set the date to May 22.",
        has_media=True,
    )
    assert actions.is_exact_reference_edit_request(
        "Put $9.99 here.",
        has_media=True,
    )
    assert actions.should_start_new_flyer_over_active(
        "Remove extra 08:00 from this image.",
        has_media=True,
    )
    assert not actions.is_exact_reference_edit_request(
        "Diwali Grocery Sale. Use items in this flyer and create one for Lakshmis Kitchen.",
        has_media=True,
    )


def test_reference_scope_blocks_unrelated_attached_flyer_with_useful_copy():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+17329837841"],
        raw_request="I'd like you to update this flyer. Change date from May 16 to May 22.",
        extraction={
            "visible_organization_names": ["Telugu Association of North America"],
            "visible_phone_numbers": [],
            "confidence": "high",
        },
    )

    assert result["decision"] == "block"
    assert "not appear to be related to Lakshmis Kitchen" in result["reply_text"]
    assert "Create One Flyer - $4" in result["reply_text"]
    assert "If you own or are authorized to use this flyer" in result["reply_text"]
    assert "If this is only a reference" in result["reply_text"]
    assert "new original Lakshmis Kitchen flyer" in result["reply_text"]
    assert "without copying Telugu Association of North America branding/layout exactly" in result["reply_text"]


def test_reference_scope_allows_related_attached_flyer():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+17329837841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": ["Lakshmi's Kitchen"],
            "visible_phone_numbers": [],
            "confidence": "high",
        },
    )

    assert result["decision"] == "allow"


def test_reference_scope_clarifies_when_reference_owner_is_unreadable():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+17329837841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": [],
            "visible_phone_numbers": [],
            "confidence": "low",
        },
    )

    assert result["decision"] == "clarify"
    assert "could not confirm" in result["reply_text"]


def test_reference_scope_pending_choice_consumes_option_two(tmp_path):
    actions = _load_actions()
    actions.FLYER_REFERENCE_SCOPE_PATH = tmp_path / "reference_scope_pending.json"

    actions.save_flyer_reference_scope_pending(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        customer={"business_name": "Lakshmis Kitchen"},
        raw_request="Use this flyer for Lakshmis Kitchen. Replace Triveni Express.",
        media_path="/opt/shift-agent/.hermes/image_cache/triveni.jpg",
        scope={"visible_organization_names": ["Triveni Express"]},
        ttl_sec=600,
    )

    pending = actions.consume_flyer_reference_scope_choice(
        "2",
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
    )

    assert pending is not None
    assert pending["choice"] == "use_reference"
    assert pending["raw_request"].startswith("Use this flyer")
    assert pending["media_path"].endswith("triveni.jpg")
    assert pending["source_organization"] == "Triveni Express"
    assert actions.consume_flyer_reference_scope_choice(
        "2",
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
    ) is None


def test_reference_scope_pending_choice_ignores_unrelated_short_reply(tmp_path):
    actions = _load_actions()
    actions.FLYER_REFERENCE_SCOPE_PATH = tmp_path / "reference_scope_pending.json"

    actions.save_flyer_reference_scope_pending(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        customer={"business_name": "Lakshmis Kitchen"},
        raw_request="Use this flyer for Lakshmis Kitchen.",
        media_path="/tmp/ref.jpg",
        scope={"visible_organization_names": ["Triveni Express"]},
        ttl_sec=600,
    )

    assert actions.consume_flyer_reference_scope_choice(
        "change rice to jeera rice",
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
    ) is None
    assert actions.consume_flyer_reference_scope_choice(
        "option 1",
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
    )["choice"] == "authorized"


def test_reference_scope_authorized_path_records_relationship_followup(tmp_path):
    actions = _load_actions()
    actions.FLYER_REFERENCE_SCOPE_PATH = tmp_path / "reference_scope_pending.json"

    actions.save_flyer_reference_scope_pending(
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
        customer={"business_name": "Lakshmis Kitchen"},
        raw_request="Use this flyer for Lakshmis Kitchen. Replace Triveni Express.",
        media_path="/opt/shift-agent/.hermes/image_cache/triveni.jpg",
        scope={"visible_organization_names": ["Triveni Express"]},
        ttl_sec=600,
    )
    pending = actions.consume_flyer_reference_scope_choice(
        "1",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    )
    actions.save_flyer_reference_authorization_pending(pending)

    recorded = actions.consume_flyer_reference_authorization_reply(
        "Sister business, co-owned by Triveni",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    )

    assert recorded["choice"] == "authorization_note_recorded"
    assert recorded["authorization_reply"] == "Sister business, co-owned by Triveni"
    final = actions.consume_flyer_reference_authorization_reply(
        "use account details",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    )
    assert final["choice"] == "use_account_details"
    assert "Sister business" in final["authorization_note"]


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


def test_attached_sample_brief_without_media_is_not_ready_to_render():
    actions = _load_actions()
    project = {
        "project_id": "F0024",
        "raw_request": "Create a professional flyer. Promotion: Diwali Grocery Sale. "
        "Items/offers/prices/key message: Extract items and prices from the sample flyer attached.",
        "fields": {
            "event_or_business_name": "Diwali Grocery Sale",
            "contact_info": "+17329837841",
            "notes": "Items/offers/prices/key message: Extract items and prices from the sample flyer attached.",
        },
        "assets": [],
    }

    assert not actions.flyer_project_has_required_fields(project)
    assert "attach" in actions.flyer_project_missing_info_reply(project).lower()

    project["assets"] = [{"kind": "reference_image"}]
    assert actions.flyer_project_has_required_fields(project)


def test_vague_flyer_start_enters_adaptive_intake_but_complete_request_does_not():
    actions = _load_actions()

    assert actions.is_vague_flyer_start("Create flyer", has_media=False)
    assert actions.is_vague_flyer_start("Help me make a flyer", has_media=False)
    assert not actions.should_start_new_flyer_over_active("Create flyer", has_media=False)
    assert not actions.should_start_new_flyer_over_active("Create a flyer", has_media=False)
    assert not actions.should_start_new_flyer_over_active("Help me make a flyer", has_media=False)
    assert not actions.is_vague_flyer_start(
        "Create a breakfast flyer with Idli $4.99 and Dosa $8.99 Saturday 8 AM to 11 AM",
        has_media=False,
    )
    assert not actions.is_vague_flyer_start("Create flyer using this attached sample", has_media=True)


def test_unlimited_location_gate_blocks_other_location_copy():
    actions = _load_actions()
    customer = {
        "plan_id": "unlimited",
        "allowed_location_labels": ["Pineville"],
        "location_restriction_enabled": True,
        "business_address": "Pineville, NC",
    }

    block = actions.flyer_location_block_message(
        customer,
        "Create breakfast flyer for Virginia location tomorrow",
    )
    assert "set up for Pineville" in block
    assert "Virginia" in block
    assert "Contact Support" in block
    assert actions.flyer_location_block_message(
        customer,
        "Create breakfast flyer for Pineville location tomorrow",
    ) == ""


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


def test_active_project_lookup_uses_latest_project_across_account_numbers(tmp_path):
    actions = _load_actions()
    customer_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    actions.FLYER_CUSTOMERS_PATH = customer_path
    actions.FLYER_PROJECTS_PATH = projects_path
    customer_path.write_text(
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
      "public_phone": "+17329837841",
      "business_whatsapp_number": "+17329837841",
      "authorized_request_numbers": ["+17329837841", "+19045550104"],
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
    projects_path.parent.mkdir(parents=True, exist_ok=True)
    projects_path.write_text(
        """
{
  "schema_version": 1,
  "next_sequence": 20,
  "projects": [
    {"project_id": "F0013", "customer_phone": "+17329837841", "status": "awaiting_final_approval", "updated_at": "2026-05-17T16:04:52Z"},
    {"project_id": "F0019", "customer_phone": "+19045550104", "status": "delivered", "updated_at": "2026-05-17T19:23:10Z"}
  ]
}
""".strip(),
        encoding="utf-8",
    )

    active = actions.find_active_flyer_project_by_sender("+17329837841", "17329837841@s.whatsapp.net")

    assert active["project_id"] == "F0019"
    assert actions.is_flyer_revision_intent(
        "Design looks great, but remove Tatte Idly and swap with Ghee Karam Idly."
    )


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
        "Create One Flyer - $4",
        "Create one flyer for $4",
        "Act Now! Save Time and Money",
        "Help me create a beautiful flyer for my business",
        "I want to set up Flyer Studio for my business",
    ]:
        assert actions.is_flyer_campaign_cta(text)
        assert not actions.should_start_new_flyer_over_active(text, has_media=False)
    assert actions.is_quick_flyer_campaign_cta("Create One Flyer - $4")


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


def test_campaign_cta_detection_handles_whatsapp_card_reply_text():
    actions = _load_actions()

    assert actions.flyer_campaign_cta_text(
        "Create beautiful marketing material for your business.\n"
        "Flyer Studio\n"
        "Start Free Trial"
    ) == "Start Free Trial"
    assert actions.flyer_campaign_source(
        "Create beautiful marketing material for your business.\n"
        "Flyer Studio\n"
        "Create One Flyer - $4"
    ) == "quick_flyer"
    assert actions.flyer_campaign_source(
        "Create beautiful marketing material for your business.\n"
        "Flyer Studio\n"
        "Act Now! Save Time and Money"
    ) == "act_now"


def test_generic_marketing_flyer_request_is_adaptive_intake_not_project_ready():
    actions = _load_actions()

    text = "Hi I want to create a marketing flyer for my marketing business service"

    assert actions.is_vague_flyer_start(text, has_media=False)
    assert not actions.should_start_new_flyer_over_active(text, has_media=False)


def test_campaign_cta_starts_intake_for_lid_only_sender(monkeypatch):
    hooks, actions = _load_plugin_modules()
    calls = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "unknown"))

    def fake_trigger_flyer_intake(**kwargs):
        calls["intake"] = kwargs
        return True, "ok", {"reply_text": "Choose language", "action": "choose_language"}

    monkeypatch.setattr(actions, "trigger_flyer_intake", fake_trigger_flyer_intake)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text: (True, "mid-1", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_campaign_cta_intercept(
        "Start Free Trial",
        "999999999999@lid",
        {"message_id": "msg-1"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer intake started: start_trial"}
    assert calls["intake"]["sender_phone"] is None
    assert calls["intake"]["start_source"] == "start_trial"


def test_lid_only_sender_can_continue_intake_and_onboarding(monkeypatch):
    hooks, actions = _load_plugin_modules()
    calls = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "unknown"))
    monkeypatch.setattr(actions, "find_flyer_intake_session_by_sender", lambda phone, chat_id: {"chat_id": chat_id, "sender_phone": phone})
    monkeypatch.setattr(actions, "find_flyer_onboarding_session_by_sender", lambda phone, chat_id: {"chat_id": chat_id, "sender_phone": phone})
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda *_args: None)

    def fake_continue_intake(**kwargs):
        calls["intake"] = kwargs
        return True, "ok", {"reply_text": "Choose mode", "action": "choose_mode", "source": "start_trial"}

    def fake_continue_onboarding(**kwargs):
        calls["onboarding"] = kwargs
        return True, "ok", {"handled": True, "reply_text": "Business name?", "next_status": "collecting_business_name"}

    monkeypatch.setattr(actions, "trigger_flyer_intake", fake_continue_intake)
    monkeypatch.setattr(actions, "trigger_flyer_onboarding", fake_continue_onboarding)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text: (True, "mid-1", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    intake_result = hooks._try_flyer_intake_intercept("1", "999999999999@lid", {"message_id": "msg-2"})
    onboarding_result = hooks._try_flyer_existing_onboarding_intercept("My Business", "999999999999@lid", {"message_id": "msg-3"})

    assert intake_result == {"action": "skip", "reason": "cf-router flyer intake: choose_mode"}
    assert onboarding_result == {"action": "skip", "reason": "cf-router flyer onboarding: collecting_business_name"}
    assert calls["intake"]["sender_phone"] is None
    assert calls["onboarding"]["sender_phone"] is None


def test_flyer_approval_text_is_case_insensitive_and_sender_block_safe():
    actions = _load_actions()
    wrapped = (
        '[shift-agent-sender v=1 platform=whatsapp phone="+17329837841" '
        'lid="201975216009469@lid" fromMe=false chat_id="17329837841@s.whatsapp.net"]\n'
        "Approve"
    )

    assert actions.is_flyer_approval_text("APPROVE")
    assert actions.is_flyer_approval_text("Approve")
    assert actions.is_flyer_approval_text("approve.")
    assert actions.is_flyer_approval_text(wrapped)
    assert not actions.is_flyer_approval_text("approve after changing the phone")


def test_extract_flyer_request_after_compound_confirm():
    actions = _load_actions()

    assert actions.extract_flyer_request_after_confirm(
        "CONFIRM. Create a breakfast menu for tomorrow from 8 AM to 10 AM."
    ) == "Create a breakfast menu for tomorrow from 8 AM to 10 AM."
    assert actions.extract_flyer_request_after_confirm("CONFIRM") == ""
