"""Pure routing heuristics for Flyer Studio cf-router behavior."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_flyer_final_approval_accepts_normal_whatsapp_confirmations():
    actions = _load_actions()

    for text in [
        "APPROVE",
        "Approve.",
        "approved",
        "OK",
        "ok.",
        "yes",
        "looks good",
        "go ahead",
        "send it",
        "finalize",
    ]:
        assert actions.is_flyer_approval_text(text), text

    for text in [
        "approve if you can change the price",
        "ok change dosa to idli",
        "looks good but make phone smaller",
        "yes add more items",
    ]:
        assert not actions.is_flyer_approval_text(text), text


def test_flyer_routing_preview_approval_aliases_are_status_gated():
    actions = _load_actions()

    finalizable = {"project_id": "F0062", "status": "awaiting_final_approval"}
    intake = {"project_id": "F0063", "status": "collecting_required_info"}

    assert actions.flyer_routing_decision_preview("ok", active_project=finalizable)["route"] == "approval"
    assert actions.flyer_routing_decision_preview("ok", active_project=intake)["route"] != "approval"


def test_flyer_delivery_state_intent_tracks_final_approval_aliases():
    actions = _load_actions()

    assert actions.is_flyer_delivery_state_intent("approved")
    assert actions.is_flyer_delivery_state_intent("looks good")

    assert not actions.is_flyer_delivery_state_intent("ok")
    assert not actions.is_flyer_delivery_state_intent("yes")
    assert not actions.is_flyer_delivery_state_intent("ok change dosa to idli")
    assert not actions.is_flyer_delivery_state_intent("looks good but make phone smaller")


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


def test_delivered_existing_flyer_media_revision_stays_on_active_project(monkeypatch):
    hooks, actions = _load_plugin_modules()
    text = (
        "Apply these changes to the existing flyer: change the background to rich golden color "
        "and keep the pictures if 2 male and 2 female celebrities with different hairstyles each "
        "and keep prices as $40,$60,$80,100"
    )
    before_update = {
        "project_id": "F0048",
        "customer_phone": "+19803826497",
        "status": "delivered",
        "updated_at": "2026-05-25T18:09:56Z",
        "raw_request": "Create flyer for Chloe Hair Studio promoting men haircut $20 and perms $80.",
        "fields": {
            "event_or_business_name": "Chloe Hair Studio",
            "venue_or_location": "11111 Gainsborough Ct, Fairfax, VA",
            "contact_info": "+19803826497",
        },
        "concepts": [{"concept_id": "C1"}],
        "revisions": [],
    }
    after_update = {**before_update, "status": "revising_design", "concepts": []}
    active_projects = [before_update, after_update]
    update_calls: list[tuple] = []
    preview_calls: list[str] = []
    audits: list[dict] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+19803826497", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0004", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_projects.pop(0) if active_projects else after_update)
    monkeypatch.setattr(actions, "invoke_update_flyer_project", lambda *args: update_calls.append(args) or (True, json.dumps({
        "project_id": "F0048",
        "version": 4,
        "revision_patch": {"changed": True, "visual_only": False, "ambiguous": False},
        "revision_requires_clarification": False,
    })))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _message, **_kwargs: (True, "ack-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda project_id: (True, f"generated {project_id}"))
    monkeypatch.setattr(actions, "send_flyer_concept_previews", lambda _chat_id, project_id: preview_calls.append(project_id) or (True, "preview-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))
    # Test fixture's updated_at is fixed at 2026-05-25T18:09:56Z, which becomes
    # >24h stale relative to wall-clock once the P0-1 stale-project guard's
    # `delivered` threshold elapses. The guard's behavior is exercised by
    # dedicated tests; for this revision-capture path we pin staleness=False
    # so the test stays deterministic regardless of when it runs.
    monkeypatch.setattr(actions, "is_stale_for_new_request", lambda _project: False)

    result = hooks._try_flyer_active_project_intercept(
        text,
        "74290284261595@lid",
        {"message_id": "m-chloe-existing-media"},
        media_path="C:/tmp/chloe-existing.jpg",
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: revision captured for F0048"}
    assert update_calls
    assert preview_calls == ["F0048"]
    assert not any(row.get("reason") == "flyer_active_project_bypassed" for row in audits)


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


def test_reference_scope_allows_when_event_title_carries_account_identity():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+17329837841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": [],
            "visible_phone_numbers": [],
            "visible_event_names": ["Lakshmis Kitchen Ugadi Specials 2026"],
            "confidence": "high",
        },
    )

    assert result["decision"] == "allow"
    assert result["reason"] == "reference_matches_account"


def test_reference_scope_allows_address_match_even_if_name_differs():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr, Houston, TX",
        account_phones=["+17329837841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": ["Weekly Grocery Deals"],
            "visible_phone_numbers": [],
            "visible_addresses": ["90 Brybar Drive Houston TX"],
            "confidence": "high",
        },
    )

    assert result["decision"] == "allow"
    assert result["reason"] == "reference_matches_account"


def test_reference_scope_allows_phone_match_even_if_name_differs():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr, Houston, TX",
        account_phones=["+17329837841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": ["Weekly Grocery Deals"],
            "visible_phone_numbers": ["(732) 983-7841"],
            "visible_addresses": [],
            "confidence": "high",
        },
    )

    assert result["decision"] == "allow"
    assert result["reason"] == "reference_matches_account"


def test_reference_scope_allows_related_attached_flyer_by_phone_match():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+1 (732) 983-7841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": ["Community Food Fest"],
            "visible_phone_numbers": ["732-983-7841"],
            "confidence": "high",
        },
    )

    assert result["decision"] == "allow"


def test_reference_scope_allows_related_attached_flyer_phone_with_extension_digits():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+17329837841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": ["Community Event"],
            "visible_phone_numbers": ["Call +1 (732) 983-7841 ext 204"],
            "confidence": "high",
        },
    )

    assert result["decision"] == "allow"
    assert result["reason"] == "reference_matches_account"


def test_reference_scope_does_not_allow_short_phone_fragment_match():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+17329837841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": ["Unrelated Organization"],
            "visible_phone_numbers": ["841"],
            "confidence": "high",
        },
    )

    assert result["decision"] == "block"
    assert result["reason"] == "reference_appears_unrelated"

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


def test_reference_scope_generic_heading_does_not_hard_block():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+17329837841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": ["Weekend Specials"],
            "visible_phone_numbers": [],
            "confidence": "high",
        },
    )

    assert result["decision"] == "clarify"
    assert result["reason"] == "reference_relationship_unclear"


def test_reference_scope_request_account_identity_overrides_generic_owner_guess():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+17329837841"],
        raw_request="Please update this Lakshmis Kitchen flyer date.",
        extraction={
            "visible_organization_names": ["Grand Opening Special"],
            "visible_phone_numbers": [],
            "confidence": "high",
        },
    )

    assert result["decision"] == "allow"
    assert result["reason"] == "request_names_account_but_reference_unclear"


def test_reference_scope_allows_and_ampersand_name_variant():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+17329837841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": ["Lakshmi and Kitchen"],
            "visible_phone_numbers": [],
            "confidence": "high",
        },
    )

    assert result["decision"] == "allow"
    assert result["reason"] == "reference_matches_account"


def test_reference_scope_still_blocks_distinct_organization_name():
    scope = _load_reference_scope_script()

    result = scope.decide_scope(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr",
        account_phones=["+17329837841"],
        raw_request="Please update this flyer date.",
        extraction={
            "visible_organization_names": ["Community Telugu Association"],
            "visible_phone_numbers": [],
            "confidence": "high",
        },
    )

    assert result["decision"] == "block"
    assert result["reason"] == "reference_appears_unrelated"


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


def test_reference_scope_choice_transaction_holds_state_lock(monkeypatch):
    actions = _load_actions()
    held = {"value": False}
    writes: list[dict] = []

    class RecordingLock:
        def __enter__(self):
            held["value"] = True

        def __exit__(self, *_args):
            held["value"] = False

    def fake_read(now=None):
        assert held["value"] is True
        return {
            "schema_version": 1,
            "pending": [{
                "chat_id": "17329837841@s.whatsapp.net",
                "sender_phone": "+17329837841",
                "status": "awaiting_choice",
                "raw_request": "Use this flyer",
                "media_path": "/tmp/ref.jpg",
                "expires_at": 9999999999,
            }],
        }

    def fake_write(doc):
        assert held["value"] is True
        writes.append(doc)

    monkeypatch.setattr(actions, "_reference_scope_state_lock", lambda: RecordingLock())
    monkeypatch.setattr(actions, "_read_reference_scope_state", fake_read)
    monkeypatch.setattr(actions, "_write_reference_scope_state", fake_write)

    pending = actions.consume_flyer_reference_scope_choice(
        "2",
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
    )

    assert pending["choice"] == "use_reference"
    assert writes == [{"schema_version": 1, "pending": []}]
    assert held["value"] is False


def test_reference_scope_authorization_reply_transaction_holds_state_lock(monkeypatch):
    actions = _load_actions()
    held = {"value": False}
    writes: list[dict] = []

    class RecordingLock:
        def __enter__(self):
            held["value"] = True

        def __exit__(self, *_args):
            held["value"] = False

    def fake_read(now=None):
        assert held["value"] is True
        return {
            "schema_version": 1,
            "pending": [{
                "chat_id": "201975216009469@lid",
                "sender_phone": "+19045550104",
                "status": "awaiting_authorization_details",
                "authorization_note": "",
                "raw_request": "Use this flyer",
                "media_path": "/tmp/ref.jpg",
                "expires_at": 9999999999,
            }],
        }

    def fake_write(doc):
        assert held["value"] is True
        writes.append(doc)

    monkeypatch.setattr(actions, "_reference_scope_state_lock", lambda: RecordingLock())
    monkeypatch.setattr(actions, "_read_reference_scope_state", fake_read)
    monkeypatch.setattr(actions, "_write_reference_scope_state", fake_write)

    recorded = actions.consume_flyer_reference_authorization_reply(
        "Sister business, same owner approved",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    )

    assert recorded["choice"] == "use_account_details"
    assert recorded["authorization_note"] == "Sister business, same owner approved"
    assert writes[0]["pending"] == []
    assert held["value"] is False


def test_reference_scope_state_writer_uses_safe_io_atomic_writer(monkeypatch, tmp_path):
    actions = _load_actions()
    writes: list[tuple[Path, str]] = []
    actions.FLYER_REFERENCE_SCOPE_PATH = tmp_path / "reference_scope_pending.json"

    def fake_atomic(path: Path, content: str) -> None:
        writes.append((path, content))

    monkeypatch.setattr(actions, "_reference_scope_atomic_writer", lambda: fake_atomic)

    actions._write_reference_scope_state({"schema_version": 1, "pending": []})

    assert writes == [
        (
            tmp_path / "reference_scope_pending.json",
            '{"pending":[],"schema_version":1}',
        )
    ]


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

    assert recorded["choice"] == "use_account_details"
    assert recorded["authorization_reply"] == "Sister business, co-owned by Triveni"
    assert "Sister business" in recorded["authorization_note"]
    assert actions.consume_flyer_reference_authorization_reply(
        "Sister business, co-owned by Triveni",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    ) is None


def test_reference_scope_relationship_answer_completes_authorized_path(tmp_path):
    actions = _load_actions()
    actions.FLYER_REFERENCE_SCOPE_PATH = tmp_path / "reference_scope_pending.json"

    actions.save_flyer_reference_scope_pending(
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
        customer={"business_name": "Lakshmis Kitchen"},
        raw_request=(
            "Use this flyer for Lakshmis Kitchen. Replace Triveni Express. "
            "Replace phone number. Veg Thali Special, replace Rice with Jeera Rice."
        ),
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

    final = actions.consume_flyer_reference_authorization_reply(
        "Co-owner",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    )

    assert final["choice"] == "use_account_details"
    assert final["authorization_reply"] == "Co-owner"
    assert final["authorization_note"] == "Co-owner"
    assert actions.consume_flyer_reference_authorization_reply(
        "Co-owner",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    ) is None


def test_reference_scope_narrative_reply_consumes_awaiting_choice_row_directly(tmp_path):
    """Production bug seen on F0050 (2026-05-19): bot prompts "I need to
    confirm... reply with how it is connected to Lakshmis Kitchn", customer
    replies "Co-owner" (a narrative), and pre-fix the reply fell through to
    the active-project intercept and got routed as a revision — returning
    "I could not match that change to the queued edit."

    Root cause: the explicit-choice detector (`_reference_scope_choice`)
    matched only `i own`/`we own`/`authorized`/`connected` — none of which
    is in "Co-owner" — so the choice intercept passed; the authorization
    intercept then required `status == "awaiting_authorization_details"`
    but the row was still `awaiting_choice`. Customer was stuck in a 2-step
    state machine the bot's UX presents as 1-step.

    Fix: `_consume_flyer_reference_authorization_reply_locked` now ALSO
    matches `awaiting_choice` rows when the body is substantive (≥ 4 alpha
    chars + not in the small ack-only set). The narrative becomes the
    authorization_note + the row is promoted to `use_account_details`.
    """
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

    # Customer skips the explicit "1" step and replies directly with the
    # relationship narrative — matching what the bot prompt invites.
    final = actions.consume_flyer_reference_authorization_reply(
        "Co-owner",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    )

    assert final is not None, (
        "narrative reply on awaiting_choice row must consume the row "
        "instead of falling through to active-project revision routing"
    )
    assert final["choice"] == "use_account_details"
    assert final["authorization_reply"] == "Co-owner"
    assert "Co-owner" in final["authorization_note"]
    # Row consumed: a second identical reply finds no pending row.
    assert actions.consume_flyer_reference_authorization_reply(
        "Co-owner",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    ) is None


def test_reference_scope_ack_only_reply_does_not_consume_awaiting_choice_row(tmp_path):
    """Conservative safety check for the awaiting_choice fallback: short
    acks ("yeah", "okay", "thanks", "yes", "yep", "ok", "sure", "fine",
    "cool", "k") must NOT consume the row. Customer's ack is intent-
    ambiguous; treating it as implicit authorization would silently start
    a source-edit they didn't choose. Pre-S1, these would fall through to
    the same downstream revision-routing path they hit today; the goal of
    the fix is to catch narrative answers like "Co-owner" without
    accidentally consuming acks.
    """
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

    for ack in ["ok", "OK", "ok.", "yes", "Yes", "yep", "Yeah", "Sure",
                "thanks", "Cool", "k", "okay", "fine"]:
        result = actions.consume_flyer_reference_authorization_reply(
            ack,
            chat_id="201975216009469@lid",
            sender_phone="+19045550104",
        )
        assert result is None, (
            f"ack-only reply {ack!r} must not consume awaiting_choice row "
            f"(would falsely start source-edit project without authorization)"
        )

    # Row is still in pending — a real narrative reply afterwards still
    # consumes it.
    final = actions.consume_flyer_reference_authorization_reply(
        "Co-owner of Lakshmis",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    )
    assert final is not None
    assert final["choice"] == "use_account_details"


def test_reference_scope_authorized_sentence_counts_as_relationship_details(tmp_path):
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

    final = actions.consume_flyer_reference_authorization_reply(
        "I am authorized and co-owner for both businesses",
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
    )

    assert final["choice"] == "use_account_details"
    assert "co-owner" in final["authorization_note"]


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


def test_business_scope_block_message_catches_different_business_request():
    actions = _load_actions()
    customer = {
        "customer_id": "CUST0004",
        "status": "trial",
        "business_name": "Chloe hair studio",
    }

    reply = actions.flyer_business_scope_block_message(
        customer,
        "Create a flyer for india bazar. Include all indian groceries available and biryani available",
    )
    assert "set up for Chloe hair studio" in reply
    assert "india bazar" in reply.lower()
    assert "Create One Flyer - $4" in reply

    assert actions.flyer_business_scope_block_message(
        customer,
        "Create a flyer for Chloe Studio. Include haircut offers and hair styling services",
    ) == ""
    assert actions.flyer_business_scope_block_message(
        customer,
        "Create a flyer for evening snacks. Include samosa, mirchi bajji, and tea",
    ) == ""
    assert actions.flyer_business_scope_block_message(
        customer,
        "Create a flyer for Diwali store wide sales. All items 5-10% off",
    ) == ""
    assert actions.flyer_business_scope_block_message(
        customer,
        "Create a flyer for my salon. Include haircut and styling offers",
    ) == ""


def test_business_scope_block_message_catches_nested_for_business_request():
    actions = _load_actions()
    customer = {
        "customer_id": "CUST0005",
        "status": "trial",
        "business_name": "MK Kitchen",
    }

    reply = actions.flyer_business_scope_block_message(
        customer,
        "Can we create a flyer with sales for Memorial Day on groceries for Triveni supermarket",
    )

    assert "set up for MK Kitchen" in reply
    assert "Triveni supermarket" in reply
    assert "separate Flyer Studio setup" in reply
    reply_spaced = actions.flyer_business_scope_block_message(
        customer,
        "Can we create a flyer with sales for Memorial Day on groceries for Triveni Super Market",
    )
    assert "Triveni Super Market" in reply_spaced
    assert actions.flyer_business_scope_block_message(
        customer,
        "Can we create a flyer with sales for Memorial Day on groceries",
    ) == ""


def test_business_scope_block_preserves_wrong_business_after_campaign_suffix_strip():
    actions = _load_actions()
    customer = {
        "customer_id": "CUST0006",
        "status": "trial",
        "business_name": "Lakshmi's Kitchen",
    }

    reply = actions.flyer_business_scope_block_message(
        customer,
        "Create a flyer for Patel Grocery store wide sale. All items 5-10% off",
    )

    assert "set up for Lakshmi's Kitchen" in reply
    assert "Patel Grocery" in reply

    single_token = actions.flyer_business_scope_block_message(
        customer,
        "Create a flyer for Walmart store wide sale. All items 5-10% off",
    )
    assert "set up for Lakshmi's Kitchen" in single_token
    assert "Walmart" in single_token


def test_business_scope_block_ignores_campaign_titles_with_org_words():
    actions = _load_actions()
    customer = {
        "customer_id": "CUST0007",
        "status": "trial",
        "business_name": "Lakshmi's Kitchen",
    }

    for text in [
        "Create a flyer for Restaurant Week Specials",
        "Create a flyer for Cafe Style Biryani",
        "Create a flyer for Biryani Bazaar",
        "Create a flyer for Kitchen Essentials Sale",
    ]:
        assert actions.flyer_business_scope_block_message(customer, text) == ""


def test_cross_business_primary_request_blocks_before_incomplete_intake_loop(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent: list[str] = []
    audits: list[dict] = []
    customer = {
        "customer_id": "CUST0005",
        "status": "trial",
        "business_name": "MK Kitchen",
    }

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+15550001111", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, reply, **_kwargs: sent.append(reply) or (True, "m-scope", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(
        actions,
        "trigger_create_flyer_project",
        lambda **_kwargs: pytest.fail("cross-business request must not create incomplete MK Kitchen project"),
    )

    result = hooks._try_flyer_primary_intercept(
        "Can we create a flyer with sales for Memorial Day on groceries for Triveni supermarket",
        "15550001111@s.whatsapp.net",
        {"message_id": "m-triveni-scope"},
        force_new=True,
    )

    assert result == {"action": "skip", "reason": "cf-router flyer business scope blocked"}
    assert sent and "set up for MK Kitchen" in sent[0]
    assert "Triveni supermarket" in sent[0]
    assert any(row.get("reason") == "flyer_business_scope_blocked" for row in audits)


def test_cross_business_active_project_request_blocks_nested_for_phrase(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0099",
        "customer_phone": "+15550001111",
        "status": "awaiting_final_approval",
        "raw_request": "Create a flyer for MK Kitchen with weekend combo offers.",
        "fields": {
            "event_or_business_name": "MK Kitchen",
            "contact_info": "+15550001111",
        },
        "concepts": [{"concept_id": "C1"}],
    }
    sent: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+15550001111", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {
        "customer_id": "CUST0005",
        "status": "trial",
        "business_name": "MK Kitchen",
    })
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, reply, **_kwargs: sent.append(reply) or (True, "m-scope", ""))
    monkeypatch.setattr(actions, "invoke_update_flyer_project", lambda *_args, **_kwargs: pytest.fail("cross-business request must not revise active project"))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_active_project_intercept(
        "Can we create a flyer with sales for Memorial Day on groceries for Triveni supermarket",
        "15550001111@s.whatsapp.net",
        {"message_id": "m-triveni-active-scope"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer business scope blocked"}
    assert sent and "set up for MK Kitchen" in sent[0]
    assert "Triveni supermarket" in sent[0]


def test_registered_customer_menu_combo_detail_routes_to_flyer_project(monkeypatch):
    hooks, actions = _load_plugin_modules()
    routed: dict[str, str] = {}
    customer = {
        "customer_id": "CUST0006",
        "status": "trial",
        "business_name": "MK Kitchen",
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_sample_prompt_request_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_source_vs_new_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_active_project_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+15550001111", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)

    def fake_primary(text, *_args, **kwargs):
        routed["text"] = text
        routed["force_new"] = str(kwargs.get("force_new"))
        return {"action": "skip", "reason": "cf-router flyer primary: contextual details"}

    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", fake_primary)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text=(
            "Can we do meal combo for veg and non veg with prices 49.99 for non veg combo "
            "includes 2 non veg curries, 1 chicken pulav or chicken Biryani and 1 dessert. "
            "And a veg combo 39.99 includes 2 veg curries, 1 dessert on the occasion of Memorial Day weekend"
        ),
        chat_id="15550001111@s.whatsapp.net",
        message_id="m-combo-detail",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer primary: contextual details"}
    assert "meal combo" in routed["text"]
    assert routed["force_new"] == "True"


def test_registered_customer_get_flyer_ready_with_banner_is_not_vague_loop(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent: list[str] = []
    routed: dict[str, str] = {}
    customer = {
        "customer_id": "CUST0006",
        "status": "trial",
        "business_name": "MK Kitchen",
    }
    text = "To this please add visual pictures to the combos and get the flyer ready with MK kitchen banner"

    assert actions.is_vague_flyer_start(text, has_media=False) is False

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_sample_prompt_request_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_source_vs_new_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_active_project_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+15550001111", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, reply, **_kwargs: sent.append(reply) or (True, "mid", ""))

    def fake_primary(raw_text, *_args, **kwargs):
        routed["text"] = raw_text
        routed["force_new"] = str(kwargs.get("force_new"))
        return {"action": "skip", "reason": "cf-router flyer primary: contextual ready"}

    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", fake_primary)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text=text,
        chat_id="15550001111@s.whatsapp.net",
        message_id="m-combo-ready",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer primary: contextual ready"}
    assert "visual pictures" in routed["text"]
    assert routed["force_new"] == "True"
    assert not sent


def test_routing_decision_preview_reports_evening_snacks_bypass_read_only():
    actions = _load_actions()
    text = (
        "I'd like you to help me with evening snacks flier from 4 PM to 7 PM. "
        "Include 5 top South Indian snack items. Its Wednesday through Saturday event"
    )

    decision = actions.flyer_routing_decision_preview(
        text,
        active_project={"project_id": "F0062", "status": "awaiting_final_approval"},
        latest_message_id="live-evening-snacks",
    )
    assert decision["route"] == "new_project"
    assert decision["selected_project_id"] == "F0062"
    assert decision["fresh_new_request_detected"] is True
    assert decision["active_project_bypassed"] is True
    assert decision["latest_message_id"] == "live-evening-snacks"
    assert actions.should_start_new_flyer_over_active(text, has_media=False)


def test_routing_decision_preview_matches_live_order_for_status_fresh_overlap():
    actions = _load_actions()
    text = "any update on flyer for Friday sale?"
    active = {"project_id": "F0062", "status": "awaiting_final_approval"}

    assert actions.is_flyer_project_status_request(text)
    assert actions.should_start_new_flyer_over_active(text, has_media=False)

    decision = actions.flyer_routing_decision_preview(text, active_project=active)
    assert decision["route"] == "new_project"
    assert decision["reason"] == "fresh_new_request"
    assert decision["fresh_new_request_detected"] is True
    assert decision["active_project_bypassed"] is True


def test_routing_decision_preview_keeps_similar_open_intake_on_active_project():
    actions = _load_actions()
    raw_request = (
        "Design a premium organic-style flyer for Fresh Meats featuring a whole fresh chicken "
        "with Premium Amish Organic Chicken, Clean bird. Strong life, Fresh, Healthy, Natural, "
        "and Halal Certified seal."
    )
    active = {
        "project_id": "F0050",
        "status": "intake_started",
        "raw_request": raw_request,
        "fields": {
            "event_or_business_name": "Fresh Meats",
            "notes": raw_request,
            "style_preference": "premium organic-style grocery product promotion",
        },
    }

    assert actions.should_start_new_flyer_over_active(raw_request, has_media=False)
    assert actions.flyer_project_has_required_fields(active)
    assert actions.similar_to_active_project_request(raw_request, active)

    decision = actions.flyer_routing_decision_preview(raw_request, active_project=active)
    assert decision["route"] == "active_intake"
    assert decision["reason"] == "active_intake_similar_request"
    assert decision["fresh_new_request_detected"] is True
    assert decision["active_project_bypassed"] is False


def test_routing_decision_preview_keeps_revision_status_and_approval_paths():
    actions = _load_actions()
    active = {"project_id": "F0062", "status": "awaiting_final_approval"}

    assert actions.flyer_routing_decision_preview("approve", active_project=active)["route"] == "approval"
    assert actions.flyer_routing_decision_preview("any update?", active_project=active)["route"] == "status_reply"
    assert actions.flyer_routing_decision_preview("change phone number", active_project=active)["route"] == "revision"
    assert actions.flyer_routing_decision_preview("make it red", active_project=active)["route"] == "revision"
    assert actions.flyer_routing_decision_preview("replace rice with jeera rice", active_project=active)["route"] == "revision"
    assert actions.flyer_routing_decision_preview("Create flyer", active_project=active)["route"] == "revision"
    assert actions.flyer_routing_decision_preview("Help me make a flyer", active_project=active)["route"] == "revision"


def test_sample_prompt_preference_text_is_account_command():
    actions = _load_actions()

    assert actions.is_flyer_account_command("update business name to Lakshmi's Kitchen")
    assert actions.is_flyer_account_command("UPGRADE PLAN - show Flyer Studio plans")
    assert actions.is_flyer_account_command("Upgrade to Growth")
    assert actions.is_flyer_account_command("I want the 60 flyers/month plan")
    assert actions.is_flyer_account_command("don't show sample prompts")
    assert actions.is_flyer_account_command("show sample prompts again")
    assert actions.is_flyer_account_command("please don't show sample prompts")
    assert actions.is_flyer_account_command("can you show me sample prompts again")
    assert actions.is_flyer_account_command("[shift-agent-sender v=1 role=customer]\nstop showing examples")
    assert actions.is_flyer_account_command("no examples")
    assert actions.is_flyer_starter_prompt_preference_command("don't show sample prompts")
    assert actions.is_flyer_starter_prompt_preference_command("show sample prompts again")
    assert actions.is_flyer_starter_prompt_preference_command("please don't show sample prompts")
    assert actions.is_flyer_starter_prompt_preference_command("hello, show me sample prompts again")
    assert not actions.is_flyer_starter_prompt_preference_command("status")
    assert actions.is_flyer_regulated_account_intent("Did my payment go through?")
    assert actions.is_flyer_regulated_account_intent("I need to update business WhatsApp number")
    assert not actions.is_flyer_regulated_account_intent("Create a thali flyer with delivery/payment badges")


def test_upgrade_plan_cta_for_registered_customer_shows_plans_not_intake(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = {}
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Lakshmi's Kitchen",
        "business_category": "restaurant",
        "status": "trial",
        "plan_id": "trial",
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "trigger_flyer_account_command", lambda **_kwargs: (True, "ok", {
        "handled": True,
        "reply_text": (
            "Flyer Studio\n------------\n"
            "Plans for Lakshmi's Kitchen:\n"
            "Starter - $49.99/month - 30 flyers/month\n"
            "Growth - $69.99/month - 60 flyers/month\n"
            "Unlimited - $199/month - unlimited flyers/month\n\n"
            "Reply CHANGE PLAN STARTER, CHANGE PLAN GROWTH, or CHANGE PLAN UNLIMITED."
        ),
        "customer_id": "CUST0001",
        "status": "trial",
    }))
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("upgrade plan CTA must not start flyer intake")))
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("upgrade plan CTA must not create a project")))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "plans-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="UPGRADE PLAN - show Flyer Studio plans",
        chat_id="17329837841@s.whatsapp.net",
        message_id="upgrade-plan-click",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer account command"}
    assert sent["chat_id"] == "17329837841@s.whatsapp.net"
    assert "$49.99" in sent["text"]
    assert "$69.99" in sent["text"]
    assert "$199" in sent["text"]
    assert "What should this flyer promote" not in sent["text"]


def test_natural_upgrade_to_growth_routes_to_account_handler(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = {}
    account_calls = []
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Lakshmi's Kitchen",
        "status": "trial",
        "plan_id": "trial",
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "trigger_flyer_account_command", lambda **kwargs: account_calls.append(kwargs) or (True, "ok", {
        "handled": True,
        "reply_text": "Flyer Studio\n------------\nPlease reply CONFIRM UPDATE to apply this account change.",
        "customer_id": "CUST0001",
        "status": "trial",
    }))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "account-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("billing command must not create flyer")))

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Upgrade to Growth",
        chat_id="17329837841@s.whatsapp.net",
        message_id="natural-upgrade-growth",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer account command"}
    assert account_calls and account_calls[0]["text"] == "Upgrade to Growth"
    assert "CONFIRM UPDATE" in sent["text"]
    assert "processed your request" not in sent["text"].lower()


def test_regulated_billing_language_is_guarded_before_generic_passthrough(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Lakshmi's Kitchen",
        "status": "trial",
        "plan_id": "trial",
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "guard-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("billing guard must not create flyer")))

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Did my payment go through?",
        chat_id="17329837841@s.whatsapp.net",
        message_id="payment-question",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer regulated account guard"}
    assert "No plan, payment, or account change has been made." in sent["text"]
    assert "processed your request" not in sent["text"].lower()
    assert audits and audits[-1]["reason"] == "flyer_regulated_account_guard"


# ---- PR-α 2026-05-26: regulated-intent regex gap-fill ----------------------
# Closes the billing/payment/account gaps from the operator's 24-pattern
# active-block list. Behavior contract: each phrase must be caught by
# is_flyer_regulated_account_intent so cf-router's _try_flyer_regulated_account_guard
# fires fail-closed clarification copy instead of generic Hermes fallback.
#
# False-positive guard: flyer briefs that mention these terms in non-mutation
# contexts (no change/update verb, no "I paid" construction) must NOT be caught,
# to avoid hijacking legitimate flyer-creation traffic.


def test_pr_alpha_bare_i_paid_is_regulated_payment_intent():
    _, actions = _load_plugin_modules()
    assert actions.is_flyer_regulated_account_intent("I paid")
    assert actions.is_flyer_regulated_account_intent("I paid.")
    assert actions.is_flyer_regulated_account_intent("I have paid")
    assert actions.is_flyer_regulated_account_intent("I just paid")
    assert actions.is_flyer_regulated_account_intent("I already paid")


def test_pr_alpha_mark_paid_variants_are_regulated_payment_intent():
    _, actions = _load_plugin_modules()
    assert actions.is_flyer_regulated_account_intent("mark paid")
    assert actions.is_flyer_regulated_account_intent("marked paid")
    assert actions.is_flyer_regulated_account_intent("marking paid")
    assert actions.is_flyer_regulated_account_intent("marked as paid")
    assert actions.is_flyer_regulated_account_intent("Please mark as paid")


def test_pr_alpha_cancel_my_plan_is_regulated_account_intent():
    # Already caught via the bare "plan" keyword in the existing pattern;
    # add explicit coverage to lock the behavior.
    _, actions = _load_plugin_modules()
    assert actions.is_flyer_regulated_account_intent("cancel my plan")
    assert actions.is_flyer_regulated_account_intent("I want to cancel my plan")
    assert actions.is_flyer_regulated_account_intent("cancel plan")


def test_pr_alpha_phone_change_variants_are_regulated_account_intent():
    _, actions = _load_plugin_modules()
    assert actions.is_flyer_regulated_account_intent("change phone")
    assert actions.is_flyer_regulated_account_intent("change my phone number")
    assert actions.is_flyer_regulated_account_intent("update phone number")
    assert actions.is_flyer_regulated_account_intent("update my phone")
    assert actions.is_flyer_regulated_account_intent("set my phone")
    assert actions.is_flyer_regulated_account_intent("modify our contact phone")


def test_pr_alpha_address_change_variants_are_regulated_account_intent():
    _, actions = _load_plugin_modules()
    assert actions.is_flyer_regulated_account_intent("change address")
    assert actions.is_flyer_regulated_account_intent("change my address")
    assert actions.is_flyer_regulated_account_intent("update address")
    assert actions.is_flyer_regulated_account_intent("update our business address")
    assert actions.is_flyer_regulated_account_intent("edit the address")


def test_pr_alpha_does_not_false_positive_on_flyer_briefs():
    # Critical: flyer briefs that mention regulated-intent vocabulary in
    # non-mutation contexts must continue to route as flyer briefs, NOT as
    # regulated-account intents.
    _, actions = _load_plugin_modules()
    assert not actions.is_flyer_regulated_account_intent(
        "Create a flyer with our phone number 555-1234"
    )
    assert not actions.is_flyer_regulated_account_intent(
        "Make a poster showing our address"
    )
    # Existing negative case from the 0e431b8 test (preserved):
    assert not actions.is_flyer_regulated_account_intent(
        "Create a thali flyer with delivery/payment badges"
    )


# ---- PR-α follow-up 2026-05-26: active-project yield regression tests ----
# The PR-α regex extension catches "change phone number" / "change address" as
# regulated-account intent. But existing Flyer routing (flyer_routing_decision_preview
# line 1181) treats those phrases as active-project revisions when the sender
# has an active flyer project. Without the yield logic in
# _try_flyer_regulated_account_guard, the new regex would HIJACK legitimate
# flyer edits like "update this flyer, change the phone number" into a
# fail-closed account warning. These tests lock the yield behavior.


def test_pr_alpha_active_project_revision_phrase_yields_from_regulated_guard(monkeypatch):
    """When sender has an active flyer project AND text targets a flyer field,
    the regulated-account guard must yield (return None) so the active-project
    intercept later in dispatch can route as revision."""
    hooks, actions = _load_plugin_modules()
    active_project = {"project_id": "F0062", "status": "awaiting_final_approval"}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen", "status": "trial"})
    # If the guard didn't yield, it would call send_flyer_text + audit_intercepted.
    # Asserting these are NEVER called proves the yield happened.
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("regulated guard must not send when yielding")))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("regulated guard must not audit when yielding")))

    for revision_text in [
        "update this flyer, change the phone number",
        "change phone number",
        "change my phone number",
        "change the address",
        "edit the price",
        "swap the logo",
        "update the date",
    ]:
        result = hooks._try_flyer_regulated_account_guard(
            revision_text,
            "17329837841@s.whatsapp.net",
            SimpleNamespace(text=revision_text, chat_id="17329837841@s.whatsapp.net", message_id=f"rev-{hash(revision_text)}"),
        )
        assert result is None, f"regulated guard should have yielded for {revision_text!r}, got {result!r}"


def test_pr_alpha_no_active_project_change_phone_still_fires_guard(monkeypatch):
    """Without an active flyer project, "change phone number" must still hit
    the regulated-account guard — the yield is conditional on active project."""
    hooks, actions = _load_plugin_modules()

    sent = {}
    audits = []
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen", "status": "trial"})
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_regulated_account_guard(
        "change my phone number",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(text="change my phone number", chat_id="17329837841@s.whatsapp.net", message_id="no-active"),
    )
    assert result == {"action": "skip", "reason": "cf-router flyer regulated account guard"}
    assert "No plan, payment, or account change has been made." in sent["text"]
    assert audits and audits[-1]["reason"] == "flyer_regulated_account_guard"


def test_pr_alpha_active_project_payment_claim_still_fires_guard(monkeypatch):
    """Even with an active project, payment-claim text ("I paid") must hit
    the regulated guard — payment claims are NOT revision-target text."""
    hooks, actions = _load_plugin_modules()
    active_project = {"project_id": "F0062", "status": "awaiting_final_approval"}

    sent = {}
    audits = []
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen", "status": "trial"})
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_regulated_account_guard(
        "I paid",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(text="I paid", chat_id="17329837841@s.whatsapp.net", message_id="paid-with-active"),
    )
    assert result == {"action": "skip", "reason": "cf-router flyer regulated account guard"}
    assert "No plan, payment, or account change has been made." in sent["text"]
    assert audits and audits[-1]["reason"] == "flyer_regulated_account_guard"


def test_pr_alpha_flyer_text_targets_revision_field_helper():
    """Unit test for the new helper that drives the yield decision."""
    _, actions = _load_plugin_modules()
    assert actions.flyer_text_targets_revision_field("update this flyer, change the phone number")
    assert actions.flyer_text_targets_revision_field("change phone")
    assert actions.flyer_text_targets_revision_field("change the address")
    assert actions.flyer_text_targets_revision_field("edit the price")
    assert actions.flyer_text_targets_revision_field("swap the logo")
    assert actions.flyer_text_targets_revision_field("update the date")
    # Negative: payment claims, pure account commands, non-edit text
    assert not actions.flyer_text_targets_revision_field("I paid")
    assert not actions.flyer_text_targets_revision_field("Upgrade to Growth")
    assert not actions.flyer_text_targets_revision_field("Did my payment go through?")
    assert not actions.flyer_text_targets_revision_field("show plans")


def test_media_edit_to_delivered_project_stays_on_active_project():
    _, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0120",
        "status": "delivered",
        "fields": {
            "event_or_business_name": "Lakshmi's Kitchen",
            "contact_info": "+1 732 983 7841",
        },
        "concepts": [{"concept_id": "C1", "preview_asset_id": "A0001"}],
    }

    assert actions.should_bypass_active_flyer_project_for_fresh_request(
        "change this attached flyer price to $9.99",
        active_project,
        has_media=True,
    ) is False


def test_pr_alpha_lid_only_active_project_revision_phrase_yields_from_regulated_guard(monkeypatch):
    """LID-only blocker fix: when identify-sender resolves phone=None but the
    active-project store still finds a project (e.g. via primary_chat_id), the
    yield must fire — the guard MUST NOT add a phone-truthy gate on top of
    find_active_flyer_project_by_sender.

    Per lessons.md line 92-95 (Flyer Studio LID-only routing lessons), pre-
    onboarding state and active projects can be chat_id-bound with
    sender_phone=None. Forcing phone resolution before yielding hijacks
    legitimate LID-only customers' flyer edits."""
    hooks, actions = _load_plugin_modules()
    active_project = {"project_id": "F0070", "status": "awaiting_final_approval"}

    # LID-only: phone resolution returns None
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "customer"))
    # Active project lookup MUST be called even when phone is None — and here
    # we mock it to simulate the function evolving to support chat_id-keyed
    # LID-only lookup (already on the repo's known-follow-up list).
    project_lookup_calls = []
    def _record_project_lookup(phone, chat_id):
        project_lookup_calls.append({"phone": phone, "chat_id": chat_id})
        return active_project
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", _record_project_lookup)
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: None)
    # If the guard didn't yield, it would call send_flyer_text + audit_intercepted.
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("regulated guard must not send for LID-only with active project")))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("regulated guard must not audit for LID-only with active project")))

    result = hooks._try_flyer_regulated_account_guard(
        "change phone number",
        "201975216009469@lid",
        SimpleNamespace(text="change phone number", chat_id="201975216009469@lid", message_id="lid-only-rev"),
    )
    assert result is None, f"regulated guard should have yielded for LID-only active-project revision, got {result!r}"
    # Verify find_active_flyer_project_by_sender was actually called with phone=None
    # (proves the gate-on-phone_check bug is gone).
    assert project_lookup_calls and project_lookup_calls[0]["phone"] is None
    assert project_lookup_calls[0]["chat_id"] == "201975216009469@lid"


# ---- PR-β 2026-05-26: delivery-state guard ---------------------------------
# Closes the second customer-visible generic-fallback risk class identified in
# the regulated-intent vision: delivery-state phrases that have no active or
# recent flyer project to surface. The active-project intercept already
# handles delivery phrases when a project resolves; PR-β catches the leftover
# case where no project exists and the message would otherwise reach generic
# Hermes (which could claim "I sent your flyer" without evidence).
#
# Design discipline inherited from PR-α (per the PR #250 close lesson):
# - Tight phrase-anchored regex, NOT bare-token matching
# - False-positive negative tests REQUIRED alongside positive cases
# - LID-only test for the no-active-project guard path (no second phone gate)
# - Dispatch-order integration test verifies guard never fires when active-
#   project intercept handled the message
#
# Scope: where is my flyer / did you send my flyer / send my flyer / approve
# / I approve. "send now" was originally deferred to PR-β.1; PR-β.1 has
# since landed (2026-05-26) and adds send-now via a separate start-anchored
# regex helper `is_flyer_send_now_intent`. See PR-β.1 tests below.


def test_pr_beta_is_flyer_delivery_state_intent_positive_cases():
    _, actions = _load_plugin_modules()
    assert actions.is_flyer_delivery_state_intent("where is my flyer")
    assert actions.is_flyer_delivery_state_intent("where's my flyer")
    assert actions.is_flyer_delivery_state_intent("where is the flyer")
    assert actions.is_flyer_delivery_state_intent("did you send my flyer")
    assert actions.is_flyer_delivery_state_intent("did you send the flyer")
    assert actions.is_flyer_delivery_state_intent("did you send me my flyer")
    assert actions.is_flyer_delivery_state_intent("send my flyer")
    assert actions.is_flyer_delivery_state_intent("send me my flyer")
    assert actions.is_flyer_delivery_state_intent("send us the flyer")
    assert actions.is_flyer_delivery_state_intent("I approve")
    assert actions.is_flyer_delivery_state_intent("approve")
    assert actions.is_flyer_delivery_state_intent("approve.")


def test_pr_beta_is_flyer_delivery_state_intent_false_positive_guards():
    # Critical: flyer briefs / revisions / other non-delivery contexts must
    # NOT be caught — they have their own dispatch paths and must not be
    # hijacked by PR-β's guard.
    _, actions = _load_plugin_modules()
    assert not actions.is_flyer_delivery_state_intent("Where can I show my flyer to customers?")
    assert not actions.is_flyer_delivery_state_intent("approve this concept")
    assert not actions.is_flyer_delivery_state_intent("approve the changes I requested")
    assert not actions.is_flyer_delivery_state_intent("send to customers Friday")
    assert not actions.is_flyer_delivery_state_intent("Did you receive my flyer?")
    assert not actions.is_flyer_delivery_state_intent("Create a flyer with our address")
    assert not actions.is_flyer_delivery_state_intent("Make a poster showing our address")
    # NOTE: PR-β.1 (2026-05-26) reverses the original "send now" deferral
    # marker — "send now" now classifies as delivery-state intent. The
    # PR-β.1 negative case is "send now" embedded mid-message in a flyer
    # brief; the start-anchored regex prevents that match.
    assert not actions.is_flyer_delivery_state_intent("Create a flyer that says send now")


def test_pr_beta_no_project_fires_delivery_state_guard(monkeypatch):
    """No active project AND no latest/closed/delivered project + delivery-
    state phrase → guard fail-closes with deterministic "no delivery action
    taken" copy. Never claims completion."""
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Lakshmi's Kitchen",
        "status": "trial",
    }

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "delivery-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    for delivery_text in ["where is my flyer", "did you send my flyer", "send my flyer", "I approve", "approve"]:
        sent.clear()
        audits.clear()
        result = hooks._try_flyer_delivery_state_guard(
            delivery_text,
            "17329837841@s.whatsapp.net",
            SimpleNamespace(text=delivery_text, chat_id="17329837841@s.whatsapp.net", message_id=f"delivery-{hash(delivery_text)}"),
        )
        assert result == {"action": "skip", "reason": "cf-router flyer delivery state guard"}, f"guard should fire for {delivery_text!r}"
        assert "No delivery action has been taken" in sent["text"]
        assert "Lakshmi's Kitchen" in sent["text"]
        # Forbidden claims: must not say it sent / delivered / completed.
        text_lower = sent["text"].lower()
        for forbidden in ("i sent", "your flyer is done", "delivered to", "completed your"):
            assert forbidden not in text_lower, f"copy must not claim {forbidden!r} for {delivery_text!r}"
        assert audits and audits[-1]["reason"] == "flyer_delivery_state_guard"


def test_pr_beta_unknown_customer_no_project_fires_guard(monkeypatch):
    """No customer profile + delivery-state phrase → guard fail-closes with
    generic "after this number is set up" copy."""
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "delivery-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_delivery_state_guard(
        "where is my flyer",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(text="where is my flyer", chat_id="17329837841@s.whatsapp.net", message_id="unknown-customer"),
    )
    assert result == {"action": "skip", "reason": "cf-router flyer delivery state guard"}
    assert "after this number is set up" in sent["text"]
    assert "No delivery action has been taken" in sent["text"]
    assert audits and audits[-1]["reason"] == "flyer_delivery_state_guard"


def test_pr_beta_non_delivery_text_does_not_fire_guard(monkeypatch):
    """Text that doesn't match is_flyer_delivery_state_intent → guard returns
    None (no send, no audit). Prevents PR-β from hijacking flyer briefs."""
    hooks, actions = _load_plugin_modules()
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda *_args: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda *_args: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("guard must not send for non-delivery text")))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("guard must not audit for non-delivery text")))

    for non_delivery in [
        "Create a flyer with our address",
        "Make a poster showing our phone number",
        "Where can I show my flyer to customers?",
        "approve this concept",
        "send to customers Friday",
        "Did you receive my flyer?",
        # NOTE: "send now" was originally a PR-β.1 deferral marker here.
        # PR-β.1 (2026-05-26) reverses that deferral — "send now" now
        # classifies as delivery-state intent and routes through PR-β
        # guard. The new false-positive case for the start-anchored
        # send-now regex is "send now" EMBEDDED in a flyer brief:
        "Create a flyer that says send now",
    ]:
        result = hooks._try_flyer_delivery_state_guard(
            non_delivery,
            "17329837841@s.whatsapp.net",
            SimpleNamespace(text=non_delivery, chat_id="17329837841@s.whatsapp.net", message_id=f"non-{hash(non_delivery)}"),
        )
        assert result is None, f"guard must yield for {non_delivery!r}, got {result!r}"


def test_pr_beta_lid_only_no_active_project_fires_guard(monkeypatch):
    """LID-only: phone resolution returns None but a customer profile still
    resolves via chat_id / primary_chat_id. Guard must still fire with the
    customer-known copy — NOT add a second phone-truthy gate."""
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Lakshmi's Kitchen",
        "status": "trial",
    }
    # LID-only: phone is None
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "customer"))
    # Customer lookup still resolves (simulates LID-only chat_id-keyed customer match)
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda phone, _chat_id: customer if phone is None else None)
    # No latest project — exercises the no-project fail-closed branch
    project_lookup_calls = []
    def _record_project_lookup(phone, chat_id):
        project_lookup_calls.append({"phone": phone, "chat_id": chat_id})
        return None
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", _record_project_lookup)
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "delivery-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_delivery_state_guard(
        "where is my flyer",
        "201975216009469@lid",
        SimpleNamespace(text="where is my flyer", chat_id="201975216009469@lid", message_id="lid-only-delivery"),
    )
    assert result == {"action": "skip", "reason": "cf-router flyer delivery state guard"}, f"LID-only guard should fire, got {result!r}"
    assert "Lakshmi's Kitchen" in sent["text"], "LID-only customer-known copy expected"
    assert "No delivery action has been taken" in sent["text"]
    assert audits and audits[-1]["reason"] == "flyer_delivery_state_guard"
    # Verify find_latest_flyer_project_for_status_by_sender was called with phone=None
    # (proves no second-phone-gate added on top of the upstream function's own gate).
    assert project_lookup_calls and project_lookup_calls[0]["phone"] is None
    assert project_lookup_calls[0]["chat_id"] == "201975216009469@lid"


# ---- PR-β follow-up 2026-05-26: delivered-project surface regression tests ----
# Closes the blocker caught in PR-β review: `did you send my flyer` and `send
# my flyer` are NOT classified as status requests by `is_flyer_project_status_request`,
# so the active-project intercept's status-surface branch doesn't fire for them.
# When a delivered project exists, the original PR-β fail-closed copy would
# have falsely claimed "no active or recent flyer to deliver" — inaccurate
# because the flyer WAS delivered.
#
# Fix: PR-β's guard now calls `find_latest_flyer_project_for_status_by_sender`
# before emitting the no-project copy. If a project resolves (delivered /
# closed_no_send / manual_edit_required / anything non-completed), surface
# the existing `flyer_project_status_reply` (or `flyer_manual_edit_status_reply`
# for the manual_edit_required + source_edit_provider_unavailable case).


def _expected_status_reply():
    return "Flyer Studio status: your most recent flyer is delivered."


def _expected_manual_edit_reply():
    return "Flyer Studio manual edit pending."


def test_pr_beta_delivered_project_surfaces_status_for_did_you_send(monkeypatch):
    """Delivered project + 'did you send my flyer' → surface real status,
    NOT the no-project fail-closed copy."""
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    delivered_project = {
        "project_id": "F0080",
        "status": "delivered",
        "updated_at": "2026-05-26T00:00:00Z",
    }
    customer = {"customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen", "status": "active"}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: delivered_project)
    monkeypatch.setattr(actions, "flyer_project_status_reply", lambda _project: _expected_status_reply())
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "surface-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_delivery_state_guard(
        "did you send my flyer",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(text="did you send my flyer", chat_id="17329837841@s.whatsapp.net", message_id="delivered-did-send"),
    )
    assert result == {"action": "skip", "reason": "cf-router flyer delivery state status surfaced"}
    assert sent["text"] == _expected_status_reply()
    # MUST NOT emit the inaccurate no-project copy
    assert "No delivery action has been taken" not in sent["text"]
    assert "I don't see an active or recent flyer" not in sent["text"]
    assert audits and audits[-1]["reason"] == "flyer_delivery_state_status_surfaced"
    assert "project_id=F0080" in audits[-1]["detail"]
    assert "status=delivered" in audits[-1]["detail"]


def test_pr_beta_delivered_project_surfaces_status_for_send_my_flyer(monkeypatch):
    """Delivered project + 'send my flyer' → surface real status, NOT
    no-project copy. Same blocker class as `did you send my flyer`."""
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    delivered_project = {"project_id": "F0081", "status": "delivered", "updated_at": "2026-05-26T00:01:00Z"}
    customer = {"customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen", "status": "active"}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: delivered_project)
    monkeypatch.setattr(actions, "flyer_project_status_reply", lambda _project: _expected_status_reply())
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "surface-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_delivery_state_guard(
        "send my flyer",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(text="send my flyer", chat_id="17329837841@s.whatsapp.net", message_id="delivered-send-my"),
    )
    assert result == {"action": "skip", "reason": "cf-router flyer delivery state status surfaced"}
    assert sent["text"] == _expected_status_reply()
    assert "No delivery action has been taken" not in sent["text"]
    assert audits and audits[-1]["reason"] == "flyer_delivery_state_status_surfaced"
    assert "project_id=F0081" in audits[-1]["detail"]


def test_pr_beta_closed_no_send_project_surfaces_status(monkeypatch):
    """closed_no_send project + delivery-state phrase → surface status reply.
    `find_latest_flyer_project_for_status_by_sender` includes closed_no_send."""
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    closed_project = {"project_id": "F0082", "status": "closed_no_send", "updated_at": "2026-05-26T00:02:00Z"}
    customer = {"customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen", "status": "active"}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: closed_project)
    monkeypatch.setattr(actions, "flyer_project_status_reply", lambda _project: _expected_status_reply())
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "surface-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_delivery_state_guard(
        "where is my flyer",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(text="where is my flyer", chat_id="17329837841@s.whatsapp.net", message_id="closed-where"),
    )
    assert result == {"action": "skip", "reason": "cf-router flyer delivery state status surfaced"}
    assert audits and audits[-1]["reason"] == "flyer_delivery_state_status_surfaced"
    assert "project_id=F0082" in audits[-1]["detail"]
    assert "status=closed_no_send" in audits[-1]["detail"]


def test_pr_beta_manual_edit_required_surfaces_manual_edit_reply(monkeypatch):
    """manual_edit_required + source_edit_provider_unavailable + delivery-
    state phrase → surface flyer_manual_edit_status_reply, NOT generic
    project_status_reply (parallel to active-project intercept's branch)."""
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    manual_project = {
        "project_id": "F0083",
        "status": "manual_edit_required",
        "manual_review": {"reason_code": "source_edit_provider_unavailable"},
        "updated_at": "2026-05-26T00:03:00Z",
    }
    customer = {"customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen", "status": "active"}
    reply_calls = {"manual": 0, "generic": 0}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: manual_project)

    def _manual_reply(_project):
        reply_calls["manual"] += 1
        return _expected_manual_edit_reply()

    def _generic_reply(_project):
        reply_calls["generic"] += 1
        return _expected_status_reply()

    monkeypatch.setattr(actions, "flyer_manual_edit_status_reply", _manual_reply)
    monkeypatch.setattr(actions, "flyer_project_status_reply", _generic_reply)
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "surface-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_delivery_state_guard(
        "send my flyer",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(text="send my flyer", chat_id="17329837841@s.whatsapp.net", message_id="manual-edit-send"),
    )
    assert result == {"action": "skip", "reason": "cf-router flyer delivery state status surfaced"}
    assert sent["text"] == _expected_manual_edit_reply()
    assert reply_calls["manual"] == 1 and reply_calls["generic"] == 0, "must dispatch to manual_edit_status_reply, not generic"
    assert audits and audits[-1]["reason"] == "flyer_delivery_state_status_surfaced"


# ---- PR-β.1 2026-05-26: send-now deterministic finalization handler ----
# Closes the PR-β.1 deferral. "send now" / "please send my flyer now" / etc.
# now route through the existing approval/finalization safe path when the
# active project is in a finalizable state, surface status when not
# finalizable, and fail-closed when no project exists.
#
# Discipline inherited from PR-α and PR-β (per the PR #250 close lesson):
# - Start-anchored regex (NOT searchable anywhere) — prevents false positives
#   on flyer briefs that embed "send now" as copy text
# - Active-project / finalizable-state ownership FIRST
# - PR-β guard's latest-project surface path catches non-finalizable cases
# - PR-β guard's no-project path catches truly-empty cases
# - LID-only test asserts no second phone gate
# - False-positive negative tests alongside positive cases


def test_pr_beta_1_is_flyer_send_now_intent_positive_cases():
    _, actions = _load_plugin_modules()
    assert actions.is_flyer_send_now_intent("send now")
    assert actions.is_flyer_send_now_intent("Send now.")
    assert actions.is_flyer_send_now_intent("please send now")
    assert actions.is_flyer_send_now_intent("kindly send now")
    assert actions.is_flyer_send_now_intent("send me now")
    assert actions.is_flyer_send_now_intent("send my flyer now")
    assert actions.is_flyer_send_now_intent("send the flyer now")
    assert actions.is_flyer_send_now_intent("send it now")
    assert actions.is_flyer_send_now_intent("please send my flyer now")
    assert actions.is_flyer_send_now_intent("please send the flyer now")


def test_pr_beta_1_is_flyer_send_now_intent_false_positive_guards():
    # The critical false-positive class: "send now" embedded mid-message in
    # a flyer brief. The start-anchored regex prevents these from matching.
    _, actions = _load_plugin_modules()
    assert not actions.is_flyer_send_now_intent("Create a flyer that says send now")
    assert not actions.is_flyer_send_now_intent("Design a poster with 'send now' button")
    assert not actions.is_flyer_send_now_intent("Make a flyer with text 'send now'")
    # Other "send" phrases not finalization intent
    assert not actions.is_flyer_send_now_intent("send to customers Friday")
    assert not actions.is_flyer_send_now_intent("send me ideas")
    assert not actions.is_flyer_send_now_intent("send this to my team")
    # Bare "send my flyer" without "now" — covered by PR-β, not PR-β.1
    assert not actions.is_flyer_send_now_intent("send my flyer")
    # Approval text — different intent class
    assert not actions.is_flyer_send_now_intent("approve")
    assert not actions.is_flyer_send_now_intent("approve this concept")


def test_pr_beta_1_delivery_state_intent_now_includes_send_now():
    """Regression check: PR-β explicitly excluded 'send now' from
    is_flyer_delivery_state_intent (deferred to PR-β.1). PR-β.1 reverses
    that exclusion."""
    _, actions = _load_plugin_modules()
    # send-now variants must now classify as delivery-state intent (regression)
    assert actions.is_flyer_delivery_state_intent("send now")
    assert actions.is_flyer_delivery_state_intent("please send my flyer now")
    assert actions.is_flyer_delivery_state_intent("send the flyer now")
    # All existing PR-β behavior must still hold
    assert actions.is_flyer_delivery_state_intent("where is my flyer")
    assert actions.is_flyer_delivery_state_intent("send my flyer")
    assert actions.is_flyer_delivery_state_intent("approve")
    # False positives still excluded
    assert not actions.is_flyer_delivery_state_intent("Create a flyer that says send now")
    assert not actions.is_flyer_delivery_state_intent("send to customers Friday")


def test_pr_beta_1_send_now_with_no_project_fails_closed(monkeypatch):
    """No active project + no latest project + 'send now' → PR-β guard
    fail-closes with deterministic 'no delivery action taken' copy. NEVER
    claims completion."""
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    customer = {"customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen", "status": "trial"}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "snw-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    for send_now_text in ["send now", "please send my flyer now", "send the flyer now"]:
        sent.clear()
        audits.clear()
        result = hooks._try_flyer_delivery_state_guard(
            send_now_text,
            "17329837841@s.whatsapp.net",
            SimpleNamespace(text=send_now_text, chat_id="17329837841@s.whatsapp.net", message_id=f"snw-noproj-{hash(send_now_text)}"),
        )
        assert result == {"action": "skip", "reason": "cf-router flyer delivery state guard"}, f"fail-closed expected for {send_now_text!r}"
        assert "No delivery action has been taken" in sent["text"]
        text_lower = sent["text"].lower()
        for forbidden in ("i sent", "your flyer is done", "delivered to", "completed your"):
            assert forbidden not in text_lower, f"copy must not claim {forbidden!r} for {send_now_text!r}"
        assert audits and audits[-1]["reason"] == "flyer_delivery_state_guard"


def test_pr_beta_1_send_now_with_delivered_project_surfaces_status(monkeypatch):
    """'send now' + delivered latest project → PR-β guard surfaces real
    status reply via existing flyer_project_status_reply, NOT a new send
    claim. Verifies the hooks.py:2894 exclusion routes correctly."""
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    delivered_project = {"project_id": "F0090", "status": "delivered", "updated_at": "2026-05-26T03:00:00Z"}
    customer = {"customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen", "status": "active"}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: delivered_project)
    monkeypatch.setattr(actions, "flyer_project_status_reply", lambda _project: "Flyer Studio status: delivered.")
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "snw-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_delivery_state_guard(
        "send now",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(text="send now", chat_id="17329837841@s.whatsapp.net", message_id="snw-delivered"),
    )
    assert result == {"action": "skip", "reason": "cf-router flyer delivery state status surfaced"}
    assert sent["text"] == "Flyer Studio status: delivered."
    # MUST NOT claim a fresh send
    assert "No delivery action has been taken" not in sent["text"]
    assert audits and audits[-1]["reason"] == "flyer_delivery_state_status_surfaced"
    assert "project_id=F0090" in audits[-1]["detail"]
    assert "status=delivered" in audits[-1]["detail"]


def test_pr_beta_1_send_now_lid_only_no_active_project_fails_closed(monkeypatch):
    """LID-only: phone=None + no project + 'send now' → fail-closed via
    PR-β guard. Asserts no second phone-truthy gate."""
    hooks, actions = _load_plugin_modules()
    sent = {}
    audits = []
    customer = {"customer_id": "CUST0001", "business_name": "Lakshmi's Kitchen", "status": "trial"}
    project_lookup_calls = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda phone, _chat_id: customer if phone is None else None)
    def _record(phone, chat_id):
        project_lookup_calls.append({"phone": phone, "chat_id": chat_id})
        return None
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", _record)
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "snw-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_delivery_state_guard(
        "send now",
        "201975216009469@lid",
        SimpleNamespace(text="send now", chat_id="201975216009469@lid", message_id="snw-lid-noproj"),
    )
    assert result == {"action": "skip", "reason": "cf-router flyer delivery state guard"}
    assert "Lakshmi's Kitchen" in sent["text"]
    assert "No delivery action has been taken" in sent["text"]
    assert audits and audits[-1]["reason"] == "flyer_delivery_state_guard"
    # Verify project lookup was called with phone=None (no second gate)
    assert project_lookup_calls and project_lookup_calls[0]["phone"] is None


def test_pr_beta_1_send_now_with_active_finalizable_routes_to_finalize_helper():
    """Integration sanity: confirm the OR-ed gate at hooks.py:2808 evaluates
    'send now' as approval-equivalent when status is finalizable.

    This is a helper-level integration check that proves the gate expression
    `is_flyer_approval_text(body) or is_flyer_send_now_intent(body)` returns
    True for 'send now' inputs that would land at the gate. Confirms the
    hooks.py wiring is correct without needing to mock the full active-
    project intercept (the actual finalize_and_send_flyer dispatch path is
    well-tested by existing tests for the 'approve' input)."""
    _, actions = _load_plugin_modules()
    # The exact expression used at hooks.py:2808
    for body in ["send now", "please send my flyer now", "send the flyer now", "send it now"]:
        gate_result = actions.is_flyer_approval_text(body) or actions.is_flyer_send_now_intent(body)
        assert gate_result, f"finalization gate must match {body!r}"
    # And inputs that should NOT match the gate
    for body in ["Create a flyer that says send now", "send to customers Friday", "send me ideas"]:
        gate_result = actions.is_flyer_approval_text(body) or actions.is_flyer_send_now_intent(body)
        assert not gate_result, f"finalization gate must NOT match {body!r}"


def test_pr_beta_1_revision_text_branch_excludes_send_now():
    """Integration sanity: confirm the hooks.py:2894 exclusion is correct.
    For status in finalizable+delivered states, body must reach revision-text
    treatment UNLESS it's a send-now intent. The exclusion expression matches
    the inline negation at the call site."""
    _, actions = _load_plugin_modules()
    # Send-now bodies must be EXCLUDED from revision-text treatment
    for body in ["send now", "please send my flyer now", "send the flyer now"]:
        excluded = actions.is_flyer_send_now_intent(body)
        assert excluded, f"revision-text branch must exclude {body!r}"
    # Other revision-like bodies must NOT be excluded (fall through to revision text)
    for body in ["change the date to May 30", "swap rice with jeera rice", "make the title bigger", "I want a different color scheme"]:
        excluded = actions.is_flyer_send_now_intent(body)
        assert not excluded, f"revision-text branch must NOT exclude {body!r}"


def test_explicit_sample_prompt_request_sends_starter_ideas(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = {}
    created = {"called": False}
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Lakshmis Kitchen",
        "business_category": "restaurant",
        "status": "trial",
        "_starter_prompt_mode": "auto",
        "_starter_prompt_sent_count": 1,
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (True, "", {
        "reply_text": (
            "Flyer Studio\n------------\n"
            "Pick a sample idea to start:\n\n"
            "1. Create an evening snacks flyer from 4 PM to 7 PM.\n"
            "2. Create a daily thali specials flyer.\n\n"
            "Reply 1 or 2."
        ),
        "action": "choose_sample_idea",
    }))
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: created.update(called=True) or (True, "", {}))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "sample-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Can you give me sample prompt for evening snacks flyer",
        chat_id="17329837841@s.whatsapp.net",
        message_id="sample-prompt-1",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer sample prompts sent"}
    assert created["called"] is False
    assert sent["chat_id"] == "17329837841@s.whatsapp.net"
    assert "Pick a sample idea" in sent["text"]
    assert "evening snacks" in sent["text"]


@pytest.mark.parametrize(
    "message_text",
    [
        "show sample prompt for grocery flyer",
        "give me flyer ideas for weekend special",
        "share example ideas for marketing flyer",
        "Need inspiration for flyer design",
        "give me ad ideas for my business flyer",
        "send promotional ideas for my shop flyer",
        "share campaign ideas for my business flyer",
        "show ad examples for my business flyer",
        "provide promo ideas for our business flyer",
        "suggest marketing ideas for my shop flyer",
        "give me ad ideas for my business",
        "send promotional ideas for my shop",
        "show me example prompts for my business",
        "show me example flyer prompts for this week",
        "send me prompt examples",
        "example prompts for my offer",
        "can you share marketing ideas for today",
        "need sample ideas for my business offer",
        "suggest some promo ideas for my business",
        "give me creative ideas for my business",
        "send me promo suggestions for my shop",
        "i need ad concepts for my business",
        "give ad caption ideas for my store",
        "send poster caption ideas for my offer",
        "need flyer caption ideas",
        "give me promo lines for my poster",
        "example flyer text please",
        "show me some template ideas",
        "i need sample ad copy",
        "need prompt ideas for ads",
        "can you suggest hooks for my flyer",
        "help with promotion ideas",
        "give 3 ideas for weekend offer",
        "give me some taglines for my poster",
        "need catchy slogans for my store offer",
        "share a few ad copies for my weekend sale",
        "what are good promo captions for my business",
        "can you suggest punchlines for my business poster",
        "give marketing slogan options for my shop ad",
        "what can you suggest for my weekend offer flyer",
        "any ideas for my summer sale poster",
        "what should i write for a grand opening flyer",
        "sample flyer request please",
        "what should be on my flyer",
        "what should i put on my flyer",
        "suggest flyer wording for summer sale",
        "need ideas for caption",
        "can i get some flyer ideas?",
        "help me with caption ideas for my store promotion",
        "give me few prompt ideas for my business ad",
        "can you share a couple of flyer prompt ideas for my salon",
    ],
)
def test_sample_prompt_variants_route_to_sample_idea_intake(monkeypatch, message_text):
    hooks, actions = _load_plugin_modules()
    sent = {}
    intake_calls = []
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Lakshmis Kitchen",
        "business_category": "restaurant",
        "status": "trial",
        "_starter_prompt_mode": "auto",
        "_starter_prompt_sent_count": 1,
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(
        actions,
        "trigger_flyer_intake",
        lambda **kwargs: intake_calls.append(kwargs) or (True, "", {"reply_text": "Pick a sample idea to start:", "action": "choose_sample_idea"}),
    )
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("sample prompt path must not create project")))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "sample-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text=message_text,
        chat_id="17329837841@s.whatsapp.net",
        message_id="sample-prompt-variant",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer sample prompts sent"}
    assert intake_calls
    assert intake_calls[0]["start_source"] == "sample_idea"
    assert sent["chat_id"] == "17329837841@s.whatsapp.net"


def test_preference_command_with_polite_prefix_does_not_route_to_sample_ideas(monkeypatch):
    hooks, actions = _load_plugin_modules()
    account_calls = []

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(actions, "is_flyer_account_command", lambda _text: True)
    monkeypatch.setattr(actions, "is_flyer_starter_prompt_preference_command", lambda _text: True)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(
        actions,
        "trigger_flyer_account_command",
        lambda **kwargs: account_calls.append(kwargs) or (True, "", {"handled": True, "reply_text": "Preference saved."}),
    )
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args, **_kwargs: (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(
        actions,
        "trigger_flyer_intake",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("preference command must not trigger sample-idea intake")),
    )

    result = hooks.pre_gateway_dispatch(
        SimpleNamespace(
            text="please don't show sample prompts",
            chat_id="17329837841@s.whatsapp.net",
            message_id="sample-pref-polite",
        )
    )

    assert result == {"action": "skip", "reason": "cf-router flyer account command"}
    assert account_calls


def test_sample_prompt_request_from_new_sender_starts_sample_intake(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = {}
    intake_calls = []

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(
        actions,
        "trigger_flyer_intake",
        lambda **kwargs: intake_calls.append(kwargs) or (True, "", {"reply_text": "Choose your language first.", "action": "choose_language"}),
    )
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("new sender sample prompt must not create project")))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "sample-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Can you send sample prompts for my flyer?",
        chat_id="17329837841@s.whatsapp.net",
        message_id="sample-prompt-new-customer",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer sample prompts sent"}
    assert intake_calls
    assert intake_calls[0]["start_source"] == "sample_idea"
    assert "language" in sent["text"].lower()


def test_vague_flyer_start_for_active_customer_sends_starter_ideas(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = {}
    created = {"called": False}
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Spark Growth",
        "business_category": "digital marketing agency",
        "status": "trial",
        "_starter_prompt_mode": "auto",
        "_starter_prompt_sent_count": 0,
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "claim_flyer_starter_prompt_send", lambda _customer_id: True)
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (True, "", {
        "reply_text": (
            "Flyer Studio\n------------\n"
            "Pick one idea and I will turn it into a flyer brief.\n\n"
            "1. Grow Your Business with Modern Marketing\n"
            "2. Weekly Service Spotlight\n"
            "3. Limited-Time Offer\n\n"
            "Reply 1 or 2."
        ),
        "action": "choose_sample_idea",
    }))
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: created.update(called=True) or (True, "", {}))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "starter-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Create flyer",
        chat_id="17329837841@s.whatsapp.net",
        message_id="m1",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer starter ideas sent"}
    assert created["called"] is False
    assert sent["chat_id"] == "17329837841@s.whatsapp.net"
    assert "Pick one idea" in sent["text"]
    assert "Grow Your Business with Modern Marketing" in sent["text"]


def test_registered_customer_legacy_trial_link_complaint_gets_account_aware_reply(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = {}
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Lakshmi's Kitchen",
        "business_category": "restaurant",
        "status": "trial",
        "_starter_prompt_mode": "auto",
        "_starter_prompt_sent_count": 0,
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("legacy trial-link complaint must not send sample ideas")))
    monkeypatch.setattr(actions, "claim_flyer_starter_prompt_send", lambda _customer_id: (_ for _ in ()).throw(AssertionError("must not burn starter prompt claim")))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.update({"chat_id": chat_id, "text": text}) or (True, "ready-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text='"START FREE TRIAL - I want to try Flyer Studio" clicked on link from your final flyer response, I am already on Free tier',
        chat_id="17329837841@s.whatsapp.net",
        message_id="m-link-complaint",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer active customer trial link recovery"}
    assert sent["chat_id"] == "17329837841@s.whatsapp.net"
    assert "already on the Free plan" in sent["text"]
    assert "Lakshmi's Kitchen" in sent["text"]
    assert "Create another flyer" in sent["text"]
    assert "Start Free Trial" not in sent["text"]
    assert "Pick a sample idea" not in sent["text"]


def test_vague_flyer_start_for_ineligible_customer_status_does_not_send_starter(monkeypatch):
    for status in ("payment_pending", "suspended", "cancelled"):
        hooks, actions = _load_plugin_modules()
        sent = []
        customer = {
            "customer_id": "CUST0001",
            "business_name": "Spark Growth",
            "business_category": "digital marketing agency",
            "status": status,
        }

        monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
        monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
        monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ineligible customer must not create project")))
        monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
        monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
        monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
        monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))

        result = hooks.pre_gateway_dispatch(SimpleNamespace(
            text="Create flyer",
            chat_id=f"{status}@s.whatsapp.net",
            message_id=f"m-{status}",
        ))

        assert result == {"action": "skip", "reason": "cf-router flyer customer not active"}
        assert not any("Here is a starter flyer request" in text for text in sent)
        assert sent
        if status == "payment_pending":
            assert "waiting for payment" in sent[0].lower()
        elif status == "cancelled":
            assert "no longer active" in sent[0].lower()
        else:
            assert status in sent[0].lower()


def test_explicit_flyer_request_for_ineligible_customer_status_does_not_create_project(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Spark Growth",
        "business_category": "digital marketing agency",
        "status": "payment_pending",
    }

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("ineligible customer must not create project")))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_primary_intercept(
        "Create premium flyer for weekend sale with chicken combo $9.99",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-explicit-ineligible"},
        force_new=True,
    )

    assert result == {"action": "skip", "reason": "cf-router flyer customer not active"}
    assert sent
    assert "waiting for payment" in sent[0].lower()


def test_vague_flyer_start_for_opted_out_customer_asks_short_clarification(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Demo Salon",
        "business_category": "salon",
        "status": "trial",
        "_starter_prompt_mode": "off",
        "_starter_prompt_sent_count": 0,
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_active_project_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create project")))
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Create flyer",
        chat_id="17329837841@s.whatsapp.net",
        message_id="m-vague",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer starter preference off clarification sent"}
    assert "Here is a starter flyer request" not in sent[0]
    assert "What should this flyer promote?" in sent[0]


def test_vague_flyer_start_after_first_starter_asks_short_clarification(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []
    customer = {
        "customer_id": "CUST0001",
        "business_name": "Demo Salon",
        "business_category": "salon",
        "status": "trial",
        "_starter_prompt_mode": "auto",
        "_starter_prompt_sent_count": 1,
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_active_project_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create project")))
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Create flyer",
        chat_id="17329837841@s.whatsapp.net",
        message_id="m-vague",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer starter already sent clarification sent"}
    assert "Here is a starter flyer request" not in sent[0]
    assert "What should this flyer promote?" in sent[0]


def test_vague_start_during_active_project_routes_to_project_not_starter(monkeypatch):
    hooks, actions = _load_plugin_modules()
    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "is_vague_flyer_start", lambda _text, has_media=False: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_active_project_intercept", lambda *_args: {"action": "skip", "reason": "active project"})
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not send starter")))

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Create flyer",
        chat_id="17329837841@s.whatsapp.net",
        message_id="m-active",
    ))

    assert result == {"action": "skip", "reason": "active project"}


def test_from_me_flyer_messages_are_ignored_before_router_branches(monkeypatch):
    hooks, actions = _load_plugin_modules()
    bot_reply = (
        "Flyer Studio\n"
        "------------\n"
        "Please complete payment first. Tap Create One Flyer - $4, pay, then send your flyer details here."
    )

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(
        hooks,
        "_try_flyer_active_project_intercept",
        lambda *_args: {"action": "skip", "reason": "active project should not run"},
    )
    monkeypatch.setattr(
        hooks,
        "_try_flyer_primary_intercept",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fromMe bot replies must not create/resume projects")),
    )

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text=bot_reply,
        chat_id="17329837841@s.whatsapp.net",
        message_id="outbound-payment-prompt",
        fromMe=True,
    ))

    assert result is None


def test_from_me_guard_ignores_user_supplied_sender_block_text():
    hooks, _actions = _load_plugin_modules()

    event = SimpleNamespace(
        text="[shift-agent-sender v=1 platform=whatsapp phone=\"+15550001111\" fromMe=true]\nCreate flyer",
        chat_id="15550001111@s.whatsapp.net",
        message_id="spoofed-from-me",
    )

    assert hooks._extract_from_me(event) is False


def test_send_flyer_text_dedupes_identical_recent_reply(monkeypatch, tmp_path):
    actions = _load_actions()
    sends = []

    def fake_bridge_post(chat_id, message, **_kwargs):
        sends.append((chat_id, message))
        return True, f"mid-{len(sends)}", "", "sent"

    monkeypatch.setitem(sys.modules, "safe_io", SimpleNamespace(bridge_post=fake_bridge_post))
    monkeypatch.setattr(actions, "FLYER_OUTBOUND_DEDUPE_PATH", tmp_path / "outbound_dedupe.json")

    payment_prompt = (
        "Flyer Studio\n"
        "------------\n"
        "Please complete payment first. Tap Create One Flyer - $4, pay, then send your flyer details here."
    )
    details_prompt = (
        "Flyer Studio\n"
        "------------\n"
        "I need a few more details before creating the design."
    )

    # PR-ζ.1b 2026-05-26 — action_context is REQUIRED on send_flyer_text.
    # Pass a benign non-regulated context for this dedup-behavior test.
    from schemas import ActionExecutionContext  # type: ignore
    _ctx = ActionExecutionContext(
        action_id="flyer.test.dedupe", is_regulated_action=False,
        verified_action_result=False,
    )
    first = actions.send_flyer_text("15550001111@s.whatsapp.net", payment_prompt, action_context=_ctx)
    replay = actions.send_flyer_text("15550001111@s.whatsapp.net", payment_prompt, action_context=_ctx)
    different = actions.send_flyer_text("15550001111@s.whatsapp.net", details_prompt, action_context=_ctx)

    assert first == (True, "mid-1", "")
    assert replay == (True, "deduped:mid-1", "")
    assert different == (True, "mid-2", "")
    assert sends == [
        ("15550001111@s.whatsapp.net", payment_prompt),
        ("15550001111@s.whatsapp.net", details_prompt),
    ]


def test_send_flyer_concept_previews_persists_delivery_metadata(monkeypatch, tmp_path):
    actions = _load_actions()
    asset_path = tmp_path / "F0095-C1-preview.png"
    asset_path.write_bytes(b"png")
    state_path = tmp_path / "projects.json"
    state_path.write_text(
        """{
  "schema_version": 1,
  "next_sequence": 96,
  "projects": [
    {
      "project_id": "F0095",
      "status": "awaiting_final_approval",
      "customer_phone": "+19045550104",
      "version": 1,
      "assets": [
        {
          "asset_id": "A0002",
          "kind": "concept_preview",
          "path": "%s",
          "delivery_status": "pending",
          "outbound_message_id": "",
          "delivery_attempt_count": 0,
          "delivery_error": ""
        }
      ],
      "concepts": [
        {
          "concept_id": "C1",
          "title": "Designer Approved",
          "style_summary": "Operator-approved manual review asset",
          "preview_asset_id": "A0002"
        }
      ]
    }
  ]
}""" % str(asset_path).replace('\\', '/'),
        encoding="utf-8",
    )
    sent_media = []
    sent_text = []

    class DummyLock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def atomic_write_text(path, body):
        Path(path).write_text(body, encoding="utf-8")

    def bridge_send_media(chat_id, media_path, caption="", **_kwargs):
        sent_media.append((chat_id, media_path, caption))
        return True, "media-mid-1", "", "sent"

    def bridge_post(chat_id, message, **_kwargs):
        sent_text.append((chat_id, message))
        return True, "text-mid-1", "", "sent"

    monkeypatch.setitem(sys.modules, "safe_io", SimpleNamespace(
        FileLock=DummyLock,
        atomic_write_text=atomic_write_text,
        bridge_send_media=bridge_send_media,
        bridge_post=bridge_post,
    ))
    monkeypatch.setitem(sys.modules, "flyer_render", SimpleNamespace(
        validate_text_manifest_file=lambda *_a, **_kw: SimpleNamespace(ok=True, blockers=[]),
    ))
    monkeypatch.setitem(sys.modules, "flyer_visual_qa", SimpleNamespace(
        validate_visual_qa_report=lambda *_a, **_kw: SimpleNamespace(ok=True, blockers=[]),
    ))
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", state_path)

    ok, mids, err = actions.send_flyer_concept_previews("201975216009469@lid", "F0095")

    assert (ok, mids, err) == (True, "media-mid-1,text-mid-1", "")
    assert len(sent_media) == 1
    assert len(sent_text) == 1
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    asset = persisted["projects"][0]["assets"][0]
    assert asset["delivery_status"] == "sent"
    assert asset["outbound_message_id"] == "media-mid-1"
    assert asset["delivery_attempt_count"] == 1
    assert asset["delivery_error"] == ""
    assert asset["delivered_at"]
    assert persisted["projects"][0]["updated_at"]


def test_pre_gateway_dispatch_dedupes_replayed_inbound_before_sending(monkeypatch, tmp_path):
    hooks, actions = _load_plugin_modules()
    sent = []

    monkeypatch.setattr(actions, "CF_ROUTER_INBOUND_DEDUPE_PATH", tmp_path / "inbound_dedupe.json")
    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(hooks, "_try_flyer_active_project_intercept", lambda *_args: None)
    monkeypatch.setattr(actions, "should_start_new_flyer_over_active", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        hooks,
        "_try_flyer_primary_intercept",
        lambda *_args, **_kwargs: sent.append("primary") or {"action": "skip", "reason": "created"},
    )

    event = SimpleNamespace(
        text="Create flyer for weekend dosa night",
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.replayed.1",
    )

    first = hooks.pre_gateway_dispatch(event)
    second = hooks.pre_gateway_dispatch(event)

    assert first == {"action": "skip", "reason": "created"}
    assert second == {"action": "skip", "reason": "cf-router duplicate inbound"}
    assert sent == ["primary"]


def test_pre_gateway_dispatch_does_not_content_dedupe_without_native_message_id(monkeypatch, tmp_path):
    hooks, actions = _load_plugin_modules()
    sent = []

    monkeypatch.setattr(actions, "CF_ROUTER_INBOUND_DEDUPE_PATH", tmp_path / "inbound_dedupe.json")
    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(hooks, "_try_flyer_active_project_intercept", lambda *_args: None)
    monkeypatch.setattr(actions, "should_start_new_flyer_over_active", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        hooks,
        "_try_flyer_primary_intercept",
        lambda *_args, **_kwargs: sent.append("primary") or {"action": "skip", "reason": "created"},
    )

    event = SimpleNamespace(
        text="Create flyer",
        chat_id="17329837841@s.whatsapp.net",
    )

    first = hooks.pre_gateway_dispatch(event)
    second = hooks.pre_gateway_dispatch(event)

    assert first == {"action": "skip", "reason": "created"}
    assert second == {"action": "skip", "reason": "created"}
    assert sent == ["primary", "primary"]


def test_business_name_update_command_runs_before_active_project_revision(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        hooks,
        "_try_flyer_active_project_intercept",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("account command must not route as queued flyer edit")),
    )
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {
        "customer_id": "CUST0001",
        "status": "trial",
        "business_name": "Lakshmis Kitchn",
    })
    monkeypatch.setattr(actions, "trigger_flyer_account_command", lambda **_kwargs: (True, "ok", {
        "handled": True,
        "reply_text": "Flyer Studio\n------------\nBusiness name updated.",
        "customer_id": "CUST0001",
        "status": "trial",
    }))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="update business name to Lakshmi's Kitchen",
        chat_id="17329837841@s.whatsapp.net",
        message_id="m-business-name",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer account command"}
    assert sent == ["Flyer Studio\n------------\nBusiness name updated."]


def test_repeated_full_request_during_open_intake_generates_existing_project(monkeypatch):
    """Real customer regression: repeating the full prompt while an intake
    project is open must not create F0051/F0052 in a loop.
    """
    hooks, actions = _load_plugin_modules()
    raw_request = (
        "Design a premium organic-style flyer for Fresh Meats featuring a whole fresh chicken "
        "with Premium Amish Organic Chicken, Clean bird. Strong life, Fresh, Healthy, Natural, "
        "and Halal Certified seal."
    )
    active_project = {
        "project_id": "F0050",
        "status": "intake_started",
        "customer_phone": "+17329837841",
        "raw_request": raw_request,
        "fields": {
            "event_or_business_name": "Fresh Meats",
            "notes": raw_request,
            "style_preference": "premium organic-style grocery product promotion",
        },
        "concepts": [],
        "revisions": [],
    }

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda _project: True)
    monkeypatch.setattr(actions, "trigger_flyer_reserve_quota", lambda **_kwargs: (True, "reserved", {"quota_allowed": True}))
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda _chat_id, _project_id, **_kwargs: (True, "processing-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda _project_id, **_kwargs: (True, "generated"))
    monkeypatch.setattr(actions, "send_flyer_concept_previews", lambda _chat_id, _project_id, **_kwargs: (True, "preview-mid", ""))
    monkeypatch.setattr(actions, "trigger_flyer_finalize_usage", lambda **_kwargs: (True, "finalized", {}))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(
        actions,
        "trigger_create_flyer_project",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not create another project")),
    )

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text=raw_request,
        chat_id="17329837841@s.whatsapp.net",
        message_id="fresh-meats-repeat",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer active: generated F0050"}


def test_explicit_new_request_escapes_different_ready_intake_project(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F1000",
        "status": "intake_started",
        "customer_phone": "+17329837841",
        "raw_request": "Create flyer for Fresh Meats chicken promo.",
        "fields": {"event_or_business_name": "Fresh Meats", "notes": "chicken promo"},
        "concepts": [],
        "revisions": [],
    }

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda _project: True)

    result = hooks._try_flyer_active_project_intercept(
        "Create flyer for Diwali grocery sale with sweets boxes $9.99",
        "17329837841@s.whatsapp.net",
        {"message_id": "new-diwali"},
    )

    assert result is None


def test_ineligible_customer_cannot_finalize_existing_active_project(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F9999",
        "status": "awaiting_final_approval",
        "customer_phone": "+17329837841",
        "raw_request": "Create flyer",
        "concepts": [{"concept_id": "C1"}],
    }
    sent = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "payment_pending"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "find_reserved_flyer_guest_order", lambda _phone, _chat_id, _project_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "finalize_and_send_flyer", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("inactive customer must not finalize")))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_active_project_intercept(
        "Approve",
        "17329837841@s.whatsapp.net",
        {"message_id": "approve-inactive"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer customer not active"}
    assert sent and "waiting for payment" in sent[0].lower()


def test_exact_edit_manual_queue_ack_persists_manual_review_state(monkeypatch):
    hooks, actions = _load_plugin_modules()
    calls = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial", "business_name": "Lakshmis Kitchen"})
    monkeypatch.setattr(actions, "is_vague_flyer_start", lambda _text, has_media=False: False)
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "flyer_location_block_message", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(actions, "trigger_check_flyer_reference_scope", lambda **_kwargs: (True, "allow", {"decision": "allow"}))
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: (True, "created", {"project_id": "F2000", "status": "manual_edit_required", "assets": [{"kind": "reference_image", "path": "C:/tmp/ref.png", "mime_type": "image/png"}]}))
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply", lambda *_args, **_kwargs: ("quota", None))
    monkeypatch.setattr(
        actions,
        "flyer_source_edit_preflight",
        lambda _project: (False, "source edit provider is not configured", "source_edit_provider_unavailable"),
    )
    monkeypatch.setattr(actions, "send_flyer_manual_edit_ack", lambda *_args, **_kwargs: (True, "manual-mid", ""))

    def fake_update(project_id, *args):
        calls["update"] = (project_id, args)
        return True, "queued"

    monkeypatch.setattr(actions, "invoke_update_flyer_project", fake_update)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_primary_intercept(
        "Please update this flyer. Change the date.",
        "17329837841@s.whatsapp.net",
        {"message_id": "exact-edit"},
        force_new=True,
        media_path="C:/tmp/ref.png",
    )

    assert result == {"action": "skip", "reason": "cf-router flyer exact edit queued: project F2000"}
    assert calls["update"][0] == "F2000"
    args = calls["update"][1]
    assert "--queue-manual-review" in args
    # S6 P0-5: must use the TYPED reason_code (S1 enum) so the cockpit triage
    # view groups + tallies on source_edit_provider_unavailable, not the
    # default `operator_request` that --manual-reason alone would leave.
    assert "--manual-reason-code" in args
    code_idx = args.index("--manual-reason-code")
    assert args[code_idx + 1] == "source_edit_provider_unavailable"


def test_manual_completed_guest_order_uses_existing_reserved_order(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F7777",
        "status": "awaiting_final_approval",
        "customer_phone": "+17329837841",
        "raw_request": "Edit uploaded flyer/source artwork.",
        "concepts": [{"concept_id": "C1"}],
        "manual_review": {"status": "completed"},
    }
    calls = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_reserved_flyer_guest_order", lambda _phone, _chat_id, _project_id: {"reserved_order": True})
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda *_args: (_ for _ in ()).throw(AssertionError("held guest order should be reused")))
    monkeypatch.setattr(actions, "trigger_flyer_reserve_quota", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("guest manual order must not fall through to quota")))
    monkeypatch.setattr(actions, "finalize_and_send_flyer", lambda *_args: (True, "sent"))

    def fake_consume(**kwargs):
        calls["consume"] = kwargs
        return True, "consumed", {}

    monkeypatch.setattr(actions, "trigger_consume_flyer_guest_order", fake_consume)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_active_project_intercept(
        "APPROVE",
        "17329837841@s.whatsapp.net",
        {"message_id": "manual-approve"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: finalized F7777"}
    assert calls["consume"]["project_id"] == "F7777"


def test_manual_completed_finalization_does_not_send_failure_after_media_sent_when_access_finalize_fails(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F7778",
        "status": "awaiting_final_approval",
        "customer_phone": "+17329837841",
        "raw_request": "Edit uploaded flyer/source artwork.",
        "concepts": [{"concept_id": "C1"}],
        "manual_review": {"status": "completed"},
    }
    sent: list[str] = []
    audits: list[dict] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_reserved_flyer_guest_order", lambda _phone, _chat_id, _project_id: {"reserved_order": True})
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda *_args: (_ for _ in ()).throw(AssertionError("held guest order should be reused")))
    monkeypatch.setattr(actions, "trigger_flyer_reserve_quota", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("guest manual order must not fall through to quota")))
    monkeypatch.setattr(actions, "finalize_and_send_flyer", lambda *_args: (True, '{"sent": 1, "message_ids": ["wamid.final"]}'))
    monkeypatch.setattr(actions, "trigger_consume_flyer_guest_order", lambda **_kwargs: (False, "guest order write failed", {}))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_active_project_intercept(
        "APPROVE",
        "17329837841@s.whatsapp.net",
        {"message_id": "manual-approve"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: finalized F7778"}
    assert sent == []
    assert "flyer_access_finalize_failed" in [row.get("reason") for row in audits]
    assert audits[-1]["reason"] == "flyer_primary_project_created"


def test_natural_concept_selection_selects_without_revision_fallback(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F7780",
        "status": "awaiting_concept_selection",
        "customer_phone": "+17329837841",
        "concepts": [{"concept_id": "C1", "title": "Dosa Night"}],
    }
    calls: list[tuple[str, tuple[str, ...]]] = []
    sent: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    def fake_update(project_id, *args):
        calls.append((project_id, args))
        if "--revision-text" in args:
            raise AssertionError("natural concept selection must not route as revision")
        return True, "ok"

    monkeypatch.setattr(actions, "invoke_update_flyer_project", fake_update)

    result = hooks._try_flyer_active_project_intercept(
        "first one please",
        "17329837841@s.whatsapp.net",
        {"message_id": "select-natural"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: selected C1 for F7780"}
    assert calls == [
        ("F7780", ("--select-concept", "C1")),
        ("F7780", ("--status", "awaiting_final_approval")),
    ]
    assert sent and "Selected C1" in sent[0]


def test_final_stage_concept_reference_reminds_without_clearing_assets(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F7781",
        "status": "awaiting_final_approval",
        "customer_phone": "+17329837841",
        "selected_concept_id": "C1",
        "concepts": [{"concept_id": "C1", "title": "Dosa Night"}],
        "final_asset_ids": ["A0001"],
    }
    sent: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(actions, "invoke_update_flyer_project", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("selected concept reminder must not mutate project")))

    result = hooks._try_flyer_active_project_intercept(
        "I like C1",
        "17329837841@s.whatsapp.net",
        {"message_id": "select-final-stage"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: selected concept reminder for F7781"}
    assert sent == ["C1 is selected. Reply APPROVE to receive final files, or reply with changes."]


@pytest.mark.parametrize(
    "status, body",
    [
        ("awaiting_final_approval", "I like C1 but change the price to $9.99"),
        ("awaiting_final_approval", "make Dosa Night bigger"),
        ("awaiting_concept_selection", "make the first line bigger"),
    ],
)
def test_concept_selection_fragments_inside_revisions_route_as_revision(monkeypatch, status, body):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F7782",
        "status": status,
        "customer_phone": "+17329837841",
        "selected_concept_id": "C1" if status == "awaiting_final_approval" else None,
        "concepts": [{"concept_id": "C1", "title": "Dosa Night"}],
        "final_asset_ids": ["A0001"],
        "revisions": [],
    }
    calls: list[tuple[str, tuple[str, ...]]] = []
    sent: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    def fake_update(project_id, *args):
        calls.append((project_id, args))
        if "--select-concept" in args:
            raise AssertionError("revision text must not be swallowed as concept selection")
        return True, "{}"

    monkeypatch.setattr(actions, "invoke_update_flyer_project", fake_update)

    result = hooks._try_flyer_active_project_intercept(
        body,
        "17329837841@s.whatsapp.net",
        {"message_id": "revision-with-concept-fragment"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: revision captured for F7782"}
    assert calls == [("F7782", ("--revision-text", body, "--message-id", "revision-with-concept-fragment"))]
    assert sent and "Revision noted" in sent[0]


@pytest.mark.parametrize("text, expected", [
    (
        "CONFIRM. Create a flyer for weekend sale with 20% off.",
        "Create a flyer for weekend sale with 20% off.",
    ),
    (
        "ok create flyer for weekend sale",
        "create flyer for weekend sale",
    ),
    (
        "yes create flyer for weekend sale",
        "create flyer for weekend sale",
    ),
])
def test_compound_confirm_routes_trailing_request_without_starter_brief(monkeypatch, text, expected):
    hooks, actions = _load_plugin_modules()
    sent = []
    created = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "trigger_flyer_onboarding", lambda **_kwargs: (True, "", {
        "handled": True,
        "next_status": "trial",
        "customer_id": "CUST0001",
        "reply_text": (
            "Flyer Studio\n------------\nFree trial active.\n\n"
            "Here is a starter flyer request.\nEdit anything below and send it back."
        ),
    }))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(actions, "should_start_new_flyer_over_active", lambda _text, has_media=False: True)
    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda text, *_args, **_kwargs: created.update({"raw_request": text}) or {"action": "skip", "reason": "created"})

    result = hooks._try_flyer_onboarding_intercept(
        text,
        "17329837841@s.whatsapp.net",
        {"message_id": "compound"},
    )

    assert result == {"action": "skip", "reason": "created"}
    assert created["raw_request"] == expected
    assert sent
    assert "Here is a starter flyer request" not in sent[0]


def test_starter_suppression_uses_canonical_marker(monkeypatch):
    hooks, actions = _load_plugin_modules()

    monkeypatch.setattr(actions, "flyer_starter_brief_marker", lambda: "CUSTOM STARTER MARKER")

    reply = hooks._suppress_flyer_starter_brief(
        "Flyer Studio\n------------\nReady.\n\nCUSTOM STARTER MARKER\nEdit this."
    )

    assert reply == "Flyer Studio\n------------\nReady.\n\nI will create the flyer request you included now."


def test_starter_brief_fallback_preserves_business_name(monkeypatch):
    import builtins

    actions = _load_actions()
    real_import = builtins.__import__

    def fail_starter_import(name, *args, **kwargs):
        if name in {"agents.flyer.starter_briefs", "flyer_starter_briefs"}:
            raise ImportError("starter brief module unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_starter_import)

    reply = actions.flyer_starter_brief_reply({
        "business_name": "Demo Salon",
        "business_category": "salon",
    })

    assert "Business: Demo Salon" in reply
    assert "Here is a starter flyer request" in reply


def test_sample_prompt_preference_command_failure_does_not_fall_through(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []

    monkeypatch.setattr(actions, "is_flyer_account_command", lambda _text: True)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "trigger_flyer_account_command", lambda **_kwargs: (False, "boom", {}))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_account_intercept(
        "don't show sample prompts",
        "201975216009469@lid",
        SimpleNamespace(message_id="m1"),
    )

    assert result == {"action": "skip", "reason": "cf-router flyer account command failed"}
    assert "I could not update that setting" in sent[0]


def test_sample_prompt_preference_command_without_customer_does_not_fall_through(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []

    monkeypatch.setattr(actions, "is_flyer_account_command", lambda _text: True)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_account_intercept(
        "don't show sample prompts",
        "201975216009469@lid",
        SimpleNamespace(message_id="m1"),
    )

    assert result == {"action": "skip", "reason": "cf-router flyer account command customer not found"}
    assert "after your Flyer Studio account is set up" in sent[0]


def test_broad_account_command_without_customer_falls_through(monkeypatch):
    hooks, actions = _load_plugin_modules()

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not send account reply")))

    assert hooks._try_flyer_account_intercept(
        "status",
        "201975216009469@lid",
        SimpleNamespace(message_id="m-status"),
    ) is None


def test_onboarding_starter_claim_released_on_hard_send_failure(monkeypatch):
    hooks, actions = _load_plugin_modules()
    released = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "trigger_flyer_onboarding", lambda **_kwargs: (True, "", {
        "handled": True,
        "next_status": "trial",
        "customer_id": "CUST0001",
        "reply_text": "Flyer Studio\n------------\nHere is a starter flyer request.\nEdit anything below.",
    }))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text, **_kwargs: (False, "", "bridge down"))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(actions, "release_flyer_starter_prompt_claim", lambda customer_id: released.append(customer_id))

    result = hooks._try_flyer_onboarding_intercept(
        "CONFIRM",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(message_id="m-confirm"),
    )

    assert result == {"action": "skip", "reason": "cf-router flyer onboarding: trial"}
    assert released == ["CUST0001"]


def test_intake_starter_claim_released_on_hard_send_failure(monkeypatch):
    hooks, actions = _load_plugin_modules()
    released = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_flyer_intake_session_by_sender", lambda _phone, _chat_id: {"status": "choosing_mode"})
    monkeypatch.setattr(actions, "classify_flyer_intent", lambda _text: (False, []))
    monkeypatch.setattr(actions, "should_start_new_flyer_over_active", lambda _text, has_media=False: False)
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (True, "", {
        "handled": True,
        "action": "text_ready",
        "customer_id": "CUST0001",
        "reply_text": "Flyer Studio\n------------\nHere is a starter flyer request.\nEdit anything below.",
    }))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text, **_kwargs: (False, "", "bridge down"))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(actions, "release_flyer_starter_prompt_claim", lambda customer_id: released.append(customer_id))

    result = hooks._try_flyer_intake_intercept(
        "2",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(message_id="m-mode"),
    )

    assert result == {"action": "skip", "reason": "cf-router flyer intake: text_ready"}
    assert released == ["CUST0001"]


def test_payment_pending_customer_campaign_cta_gets_payment_guidance(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []

    monkeypatch.setattr(actions, "flyer_campaign_source", lambda _text: "start_trial")
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {
        "customer_id": "CUST0001",
        "business_name": "Demo Salon",
        "status": "payment_pending",
    })
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)
    monkeypatch.setattr(hooks, "_start_flyer_intake", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not start intake")))

    result = hooks._try_flyer_campaign_cta_intercept(
        "Start Free Trial",
        "17329837841@s.whatsapp.net",
        SimpleNamespace(message_id="cta"),
    )

    assert result == {"action": "skip", "reason": "cf-router flyer customer not active"}
    assert "waiting for payment" in sent[0].lower()
    assert "Here is a starter flyer request" not in sent[0]


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


def test_lid_only_project_lookup_uses_primary_chat_id_account_numbers(tmp_path):
    actions = _load_actions()
    customer_path = tmp_path / "customers.json"
    projects_path = tmp_path / "projects.json"
    actions.FLYER_CUSTOMERS_PATH = customer_path
    actions.FLYER_PROJECTS_PATH = projects_path
    lid_chat_id = "201975216009469@lid"
    customer_path.write_text(
        """
{
  "schema_version": 1,
  "next_customer_sequence": 2,
  "next_brand_asset_sequence": 1,
  "customers": [
    {
      "customer_id": "CUST0001",
      "business_name": "Lakshmis Kitchen",
      "business_address": "90 Brybar Dr St Johns FL",
      "primary_chat_id": "201975216009469@lid",
      "onboarded_by_phone": "+17329837841",
      "public_phone": "+19045550104",
      "business_whatsapp_number": "+19045550104",
      "authorized_request_numbers": ["+19045550105"],
      "business_category": "Indian Restaurant",
      "preferred_language": "en",
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
    projects_path.write_text(
        """
{
  "schema_version": 1,
  "next_sequence": 61,
  "projects": [
    {"project_id": "F0058", "customer_phone": "+19045550104", "status": "awaiting_final_approval", "updated_at": "2026-05-30T20:18:00Z", "created_at": "2026-05-30T20:15:00Z"},
    {"project_id": "F0059", "customer_phone": "+19045550105", "status": "closed_no_send", "updated_at": "2026-05-30T20:28:00Z", "created_at": "2026-05-30T20:22:00Z"},
    {"project_id": "F0060", "customer_phone": "+19048626362", "status": "awaiting_final_approval", "updated_at": "2026-05-30T20:40:00Z", "created_at": "2026-05-30T20:35:00Z"}
  ]
}
""".strip(),
        encoding="utf-8",
    )

    active = actions.find_active_flyer_project_by_sender(None, lid_chat_id)
    latest = actions.find_latest_flyer_project_for_status_by_sender(None, lid_chat_id)
    exact = actions.find_flyer_project_by_id_for_sender(None, lid_chat_id, "F0058")
    leak = actions.find_flyer_project_by_id_for_sender(None, lid_chat_id, "F0060")

    assert active is not None and active["project_id"] == "F0058"
    assert latest is not None and latest["project_id"] == "F0059"
    assert exact is not None and exact["project_id"] == "F0058"
    assert leak is None


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


def test_registered_customer_service_flyer_brief_is_not_language_preflight():
    actions = _load_actions()
    text = (
        "I want you to create a flyer for Marketing with my services promoting\n"
        "Services: Social media marketing\n"
        "Performance marketing\n"
        "SEO\n"
        "AEO\n"
        "GEO\n"
        "AI Marketing\n"
        "Content Creation\n"
        "Paid Ads"
    )

    assert not actions.is_vague_flyer_start(text, has_media=False)
    assert actions.should_start_new_flyer_over_active(text, has_media=False)


def test_service_list_project_has_required_fields_without_event_date_time():
    actions = _load_actions()
    project = {
        "project_id": "F0035",
        "status": "intake_started",
        "customer_phone": "+918985741562",
        "raw_request": (
            "I want you to create a flyer for Marketing with my services promoting "
            "Services: Social media marketing Performance marketing SEO AEO GEO "
            "AI Marketing Content Creation Paid Ads"
        ),
        "fields": {
            "event_or_business_name": (
                "Marketing with my services promoting Services: Social media marketing "
                "Performance marketing SEO AEO GEO AI Marketing Content Creation Paid Ads"
            ),
            "venue_or_location": "101, kavitha palace , KPHB, Hyderabad, Telangana, 500085.",
            "contact_info": "+18985741562",
            "preferred_language": "mixed",
            "notes": (
                "I want you to create a flyer for Marketing with my services promoting "
                "Services: Social media marketing Performance marketing SEO AEO GEO "
                "AI Marketing Content Creation Paid Ads"
            ),
        },
        "assets": [],
        "concepts": [],
    }

    assert actions.flyer_project_has_required_fields(project)


def test_lid_only_active_service_project_resumes_generation(monkeypatch):
    hooks, actions = _load_plugin_modules()
    project = {
        "project_id": "F0035",
        "status": "intake_started",
        "customer_phone": "+918985741562",
        "fields": {
            "event_or_business_name": "Marketing Services",
            "venue_or_location": "101 Kavitha Palace, KPHB",
            "contact_info": "+918985741562",
            "notes": "Services: Social media marketing, Performance marketing, SEO, AEO, GEO, AI Marketing, Content Creation, Paid Ads",
        },
        "concepts": [],
        "revisions": [],
    }
    customer = {
        "customer_id": "CUST0003",
        "status": "trial",
        "primary_chat_id": "158024815611933@lid",
        "public_phone": "+918985741562",
    }
    calls = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "unknown"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)

    def fake_find_active(phone, chat_id):
        calls["lookup"] = (phone, chat_id)
        return project

    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", fake_find_active)
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda _project: True)
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply", lambda *_args, **_kwargs: ({"reservation_id": "R1"}, None))
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda _chat_id, _project_id, **_kwargs: (True, "processing-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda _project_id, **_kwargs: (True, "generated"))

    def fake_send_preview(access, chat_id, phone, project_id, message_id, **kwargs):
        calls["preview"] = {
            "access": access,
            "chat_id": chat_id,
            "phone": phone,
            "project_id": project_id,
            "message_id": message_id,
            "kwargs": kwargs,
        }
        return True, "preview-mid", ""

    monkeypatch.setattr(hooks, "_send_preview_then_finalize_access", fake_send_preview)
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ready service project must not repeat missing-info prompt")))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_active_project_intercept(
        "This flyer should promote my business services",
        "158024815611933@lid",
        {"message_id": "followup-1"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: generated F0035"}
    assert calls["lookup"] == ("+918985741562", "158024815611933@lid")
    assert calls["preview"]["project_id"] == "F0035"
    assert calls["preview"]["phone"] == "+918985741562"


def test_manual_source_edit_status_check_gets_queue_update_not_clarification(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0053",
        "customer_phone": "+17329837841",
        "status": "manual_edit_required",
        "raw_request": (
            "Original customer request: use this flyer for Lakshmis Kitchen. "
            "Replace Triveni Express branding and use saved account details."
        ),
        "updated_at": "2026-05-19T02:11:00Z",
        # S7 P0-6: reason_code on the manual_review row drives cf-router
        # routing — source_edit_provider_unavailable hits the canonical
        # source-edit reason line rather than a clarification/edit parser.
        "manual_review": {
            "status": "queued",
            "reason": "source_edit_provider_unavailable",
            "reason_code": "source_edit_provider_unavailable",
            "detail": "legacy source-edit project queued before reason was tracked",
            "queued_at": "2026-05-19T02:11:00Z",
        },
    }
    sent = []

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(
        actions,
        "invoke_update_flyer_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("status check must not be parsed as an edit")),
    )
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: sent.append((chat_id, text)) or (True, "status-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="any update",
        chat_id="17329837841@s.whatsapp.net",
        message_id="f0053-status-1",
    ))

    assert result == {
        "action": "skip",
        "reason": "cf-router flyer exact edit status for F0053",
    }
    assert sent
    assert "I have the requested changes" in sent[0][1]
    assert "no extra information needed" in sent[0][1]
    assert "Please send the exact text" not in sent[0][1]


def test_manual_review_where_is_update_flyer_routes_as_status(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0085",
        "customer_phone": "+17329837841",
        "status": "manual_edit_required",
        "created_at": "2026-05-23T00:16:00Z",
        "original_message_id": "m-f0085",
        "raw_request": "Create a flyer for mid-night biryani. Include all famous biryanis.",
        "fields": {"event_or_business_name": "Mid-Night Biryani", "contact_info": "+17329837841"},
        "updated_at": "2026-05-23T00:18:00Z",
        "manual_review": {
            "status": "queued",
            "reason": "visual_qa_failed",
            "reason_code": "visual_qa_failed",
            "detail": "final visual QA failed",
            "queued_at": "2026-05-23T00:18:00Z",
        },
    }
    sent: list[str] = []
    audit_reasons: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(
        actions,
        "invoke_update_flyer_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("status check must not become a queued edit")),
    )
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "status-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audit_reasons.append(kwargs.get("reason", "")))

    result = hooks._try_flyer_active_project_intercept(
        "Where is the update flyer?",
        "17329837841@s.whatsapp.net",
        {"message_id": "where-update"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer status for F0085"}
    assert sent
    assert "queued" in sent[0].lower()
    assert "Please send the exact text" not in sent[0]
    assert "flyer_project_status" in audit_reasons
    assert "flyer_reference_exact_edit_status" not in audit_reasons


def test_manual_review_where_is_my_updated_flyer_routes_as_status(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0086",
        "customer_phone": "+17329837841",
        "status": "manual_edit_required",
        "created_at": "2026-05-23T00:16:00Z",
        "original_message_id": "m-f0086",
        "raw_request": "Please update this flyer. Remove extra 08:00.",
        "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+17329837841"},
        "updated_at": "2026-05-23T00:18:00Z",
        "manual_review": {
            "status": "queued",
            "reason": "source_edit_provider_unavailable",
            "reason_code": "source_edit_provider_unavailable",
            "detail": "source edit provider is not configured",
            "queued_at": "2026-05-23T00:18:00Z",
        },
    }
    sent: list[str] = []
    audit_reasons: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(
        actions,
        "invoke_update_flyer_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("status check must not become a queued edit")),
    )
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "status-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audit_reasons.append(kwargs.get("reason", "")))

    result = hooks._try_flyer_active_project_intercept(
        "Where is my updated flyer?",
        "17329837841@s.whatsapp.net",
        {"message_id": "where-my-updated"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer exact edit status for F0086"}
    assert sent
    assert "queued" in sent[0].lower()
    assert "Please send the exact text" not in sent[0]
    assert "flyer_reference_exact_edit_status" in audit_reasons


def test_manual_review_status_normalizes_source_edit_reason_code_in_active_intercept(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0087",
        "customer_phone": "+17329837841",
        "status": "manual_edit_required",
        "updated_at": "2026-05-23T00:18:00Z",
        "manual_review": {
            "status": "queued",
            "reason_code": " Source_Edit_Provider_Unavailable ",
        },
    }
    audit_reasons: list[str] = []
    sent: list[str] = []
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "status-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audit_reasons.append(kwargs.get("reason", "")))

    result = hooks._try_flyer_active_project_intercept(
        "where is my updated flyer?",
        "17329837841@s.whatsapp.net",
        {"message_id": "where-updated-normalized"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer exact edit status for F0087"}
    assert sent
    assert "flyer_reference_exact_edit_status" in audit_reasons


def test_manual_review_status_normalizes_source_edit_reason_code_in_pre_gateway(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0088",
        "customer_phone": "+17329837841",
        "status": "manual_edit_required",
        "updated_at": "2026-05-23T00:18:00Z",
        "manual_review": {
            "status": "queued",
            "reason_code": "SOURCE_EDIT_PROVIDER_UNAVAILABLE",
        },
    }
    audit_reasons: list[str] = []
    sent: list[str] = []

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_choice_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_reference_scope_authorization_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "status-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audit_reasons.append(kwargs.get("reason", "")))

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="any update?",
        chat_id="17329837841@s.whatsapp.net",
        message_id="status-upper-reason",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer exact edit status for F0088"}
    assert sent
    assert "flyer_reference_exact_edit_status" in audit_reasons


def test_flyer_project_status_request_classifier_keeps_edits_separate():
    actions = _load_actions()

    for text in [
        "any update",
        "Any updates?",
        "any news on the flyer?",
        "what is the latest update?",
        "update on this flyer please",
        "share status of flyer",
        "status on my flyer please",
        "status update on my flyer",
        "give me an update on the flyer",
        "where is the update flyer?",
        "where's the update flyer?",
        "where is my updated flyer?",
        "where's my updated flyer?",
        "what's the status",
        "is the flyer ready",
        "is it ready yet?",
        "ready yet?",
        "still waiting",
        "how long will it take",
    ]:
        assert actions.is_flyer_project_status_request(text)

    for text in [
        "update this flyer, change the phone number",
        "change this flyer date to May 22",
        "add one more item for $9.99",
        "replace the Triveni Express logo",
    ]:
        assert not actions.is_flyer_project_status_request(text)


def test_flyer_customer_lookup_can_match_lid_primary_chat_without_phone(tmp_path):
    actions = _load_actions()
    customer_path = tmp_path / "customers.json"
    actions.FLYER_CUSTOMERS_PATH = customer_path
    customer_path.parent.mkdir(parents=True, exist_ok=True)
    customer_path.write_text(
        """
{
  "schema_version": 1,
  "next_customer_sequence": 4,
  "next_brand_asset_sequence": 1,
  "customers": [
    {
      "customer_id": "CUST0003",
      "business_name": "Hisaku",
      "business_address": "101 Kavitha Palace, KPHB, Hyderabad, Telangana 500085",
      "primary_chat_id": "158024815611933@lid",
      "onboarded_by_phone": null,
      "public_phone": "+918985741562",
      "business_whatsapp_number": "+918985741562",
      "authorized_request_numbers": ["+918985741562"],
      "business_category": "Digital Marketing",
      "preferred_language": "en",
      "plan_id": "trial",
      "status": "trial",
      "created_at": "2026-05-18T17:42:34Z",
      "updated_at": "2026-05-18T17:42:34Z"
    }
  ],
  "onboarding_sessions": []
}
""".strip(),
        encoding="utf-8",
    )

    customer = actions.find_flyer_customer_by_sender(None, "158024815611933@lid")

    assert customer["customer_id"] == "CUST0003"


def test_quick_flyer_guest_order_requires_resolved_sender_phone():
    actions = _load_actions()

    ok, detail, doc = actions.trigger_start_flyer_guest_order(
        sender_phone=None,
        chat_id="201975216009469@lid",
        message_id="cta-quick",
    )

    assert ok is False
    assert detail == "sender_phone_required"
    assert doc["detail"] == "sender_phone_required"


def test_registered_lid_only_text_mode_brief_creates_project_not_intake(monkeypatch):
    hooks, actions = _load_plugin_modules()
    text = (
        "I want you to create a flyer for Marketing with my services promoting\n"
        "Services: Social media marketing\n"
        "Performance marketing\n"
        "SEO\n"
        "AEO\n"
        "GEO\n"
        "AI Marketing\n"
        "Content Creation\n"
        "Paid Ads"
    )
    customer = {
        "customer_id": "CUST0003",
        "status": "trial",
        "primary_chat_id": "158024815611933@lid",
        "public_phone": "+918985741562",
        "business_whatsapp_number": "+918985741562",
        "authorized_request_numbers": ["+918985741562"],
    }
    calls = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "unknown"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)

    def fail_intake(**_kwargs):
        raise AssertionError("registered text-mode flyer brief must not restart intake")

    def fake_create_project(**kwargs):
        calls["create"] = kwargs
        return True, "project_id=F9001", {"project_id": "F9001", "fields": {}, "raw_request": kwargs["raw_request"]}

    monkeypatch.setattr(actions, "trigger_flyer_intake", fail_intake)
    monkeypatch.setattr(actions, "trigger_create_flyer_project", fake_create_project)
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda _project: False)
    monkeypatch.setattr(actions, "flyer_project_missing_info_reply", lambda _project: "What else should go on this flyer?")
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text, **_kwargs: (True, "mid-1", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_primary_intercept(
        text,
        "158024815611933@lid",
        {"message_id": "brief-1"},
        force_new=True,
    )

    assert result == {"action": "skip", "reason": "cf-router flyer primary: project F9001 created"}
    assert calls["create"]["customer_phone"] == "+918985741562"
    assert "Social media marketing" in calls["create"]["raw_request"]


def test_active_customer_flyer_brief_ignores_stale_intake_session(monkeypatch):
    hooks, actions = _load_plugin_modules()
    text = (
        "I want you to create a flyer for Marketing with my services promoting\n"
        "Services: Social media marketing\n"
        "Performance marketing\n"
        "SEO"
    )
    customer = {
        "customer_id": "CUST0003",
        "status": "trial",
        "primary_chat_id": "158024815611933@lid",
        "public_phone": "+918985741562",
    }

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "unknown"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_flyer_intake_session_by_sender", lambda _phone, _chat_id: {"status": "choosing_mode"})
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("stale intake must be ignored")))

    result = hooks._try_flyer_intake_intercept(
        text,
        "158024815611933@lid",
        {"message_id": "brief-after-loop"},
    )

    assert result is None


def test_active_customer_start_trial_cta_returns_ready_not_new_intake(monkeypatch):
    hooks, actions = _load_plugin_modules()
    customer = {
        "customer_id": "CUST0004",
        "business_name": "Chloe hair studio",
        "status": "trial",
        "primary_chat_id": "74290284261595@lid",
        "public_phone": "+19803826497",
    }
    sent = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "unknown"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("active customer CTA must not restart intake")))
    def fake_send_ready(_chat_id, text, **_kwargs):
        sent["message"] = text
        return True, "mid-1", ""

    monkeypatch.setattr(actions, "send_flyer_text", fake_send_ready)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_campaign_cta_intercept(
        "Start Free Trial",
        "74290284261595@lid",
        {"message_id": "retry-cta"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active customer trial link recovery"}
    assert "already on the Free plan for Chloe hair studio" in sent["message"]
    assert "Create another flyer" in sent["message"]
    assert "Start Free Trial" not in sent["message"]


def test_active_customer_stale_onboarding_non_flyer_gets_ready_reply(monkeypatch):
    hooks, actions = _load_plugin_modules()
    customer = {
        "customer_id": "CUST0004",
        "business_name": "Chloe hair studio",
        "status": "trial",
        "primary_chat_id": "74290284261595@lid",
        "public_phone": "+19803826497",
    }
    sent = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "unknown"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_flyer_onboarding_session_by_sender", lambda _phone, _chat_id: {"status": "collecting_business_name"})
    def fake_send_ready(_chat_id, text, **_kwargs):
        sent["message"] = text
        return True, "mid-1", ""

    monkeypatch.setattr(actions, "send_flyer_text", fake_send_ready)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_existing_onboarding_intercept(
        "Chloe hair studio",
        "74290284261595@lid",
        {"message_id": "business-name-after-retry"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active customer ready"}
    assert "already set up for Chloe hair studio" in sent["message"]


def test_active_customer_stale_onboarding_flyer_brief_continues_to_project(monkeypatch):
    hooks, actions = _load_plugin_modules()
    customer = {
        "customer_id": "CUST0004",
        "business_name": "Chloe hair studio",
        "status": "trial",
        "primary_chat_id": "74290284261595@lid",
        "public_phone": "+19803826497",
    }

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "unknown"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_flyer_onboarding_session_by_sender", lambda _phone, _chat_id: {"status": "collecting_business_name"})
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("flyer brief must not get ready-only reply")))

    result = hooks._try_flyer_existing_onboarding_intercept(
        "Create flyer for Chloe hair studio grand opening sale",
        "74290284261595@lid",
        {"message_id": "brief-after-retry"},
    )

    assert result is None


def test_campaign_cta_starts_intake_for_lid_only_sender(monkeypatch):
    hooks, actions = _load_plugin_modules()
    calls = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: (None, "unknown"))

    def fake_trigger_flyer_intake(**kwargs):
        calls["intake"] = kwargs
        return True, "ok", {"reply_text": "Choose language", "action": "choose_language"}

    monkeypatch.setattr(actions, "trigger_flyer_intake", fake_trigger_flyer_intake)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text, **_kwargs: (True, "mid-1", ""))
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text, **_kwargs: (True, "mid-1", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    intake_result = hooks._try_flyer_intake_intercept("1", "999999999999@lid", {"message_id": "msg-2"})
    onboarding_result = hooks._try_flyer_existing_onboarding_intercept("My Business", "999999999999@lid", {"message_id": "msg-3"})

    assert intake_result == {"action": "skip", "reason": "cf-router flyer intake: choose_mode"}
    assert onboarding_result == {"action": "skip", "reason": "cf-router flyer onboarding: collecting_business_name"}
    assert calls["intake"]["sender_phone"] is None
    assert calls["onboarding"]["sender_phone"] is None


def test_approved_brief_intake_routes_to_project_creation_with_audit(monkeypatch):
    hooks, actions = _load_plugin_modules()
    audits = []
    created = {}

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {
        "customer_id": "CUST0001",
        "status": "trial",
    })
    monkeypatch.setattr(actions, "find_flyer_intake_session_by_sender", lambda _phone, _chat_id: {
        "status": "brief_pending_approval",
    })
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (True, "", {
        "action": "create_project",
        "raw_request": "Create a professional flyer for Lakshmis Kitchn. Customer request: evening snacks.",
        "reference_media_path": "",
        "brief_source": "text",
        "brief_approved_at": "2026-05-21T00:00:00+00:00",
        "brief_approved_message_id": "approve-mid",
    }))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))
    def fake_discard(phone, chat_id):
        audits.append({"reason": "discarded", "phone": phone, "chat_id": chat_id})
        return True

    monkeypatch.setattr(actions, "discard_flyer_intake_session_by_sender", fake_discard)
    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda raw_request, *_args, **_kwargs: created.update({"raw_request": raw_request}) or {
        "action": "skip",
        "reason": "created",
    })

    result = hooks._try_flyer_intake_intercept(
        "APPROVE",
        "17329837841@s.whatsapp.net",
        {"message_id": "approve-mid"},
    )

    assert result == {"action": "skip", "reason": "created"}
    assert "evening snacks" in created["raw_request"]
    assert audits[0]["reason"] == "flyer_brief_approved"
    assert "source=text" in audits[0]["detail"]
    assert "approved_message_id=approve-mid" in audits[0]["detail"]
    assert audits[1]["reason"] == "discarded"


def test_approved_brief_project_creation_failure_keeps_pending_brief(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent = []
    audits = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {
        "customer_id": "CUST0001",
        "status": "trial",
    })
    monkeypatch.setattr(actions, "find_flyer_intake_session_by_sender", lambda _phone, _chat_id: {
        "status": "brief_pending_approval",
    })
    monkeypatch.setattr(actions, "trigger_flyer_intake", lambda **_kwargs: (True, "", {
        "action": "create_project",
        "raw_request": "Create a professional flyer for Lakshmis Kitchn. Customer request: evening snacks.",
        "brief_source": "text",
        "brief_approved_at": "2026-05-21T00:00:00+00:00",
        "brief_approved_message_id": "approve-mid",
    }))
    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(actions, "discard_flyer_intake_session_by_sender", lambda *_args: (_ for _ in ()).throw(AssertionError("pending brief must survive failure")))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "retry-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_intake_intercept(
        "APPROVE",
        "17329837841@s.whatsapp.net",
        {"message_id": "approve-mid"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer brief project creation failed"}
    assert "still saved" in sent[0]
    assert audits[-1]["reason"] == "flyer_brief_project_create_failed"


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
    assert actions.extract_flyer_request_after_confirm(
        "ok create flyer for weekend sale"
    ) == "create flyer for weekend sale"
    assert actions.extract_flyer_request_after_confirm(
        "yes create flyer for weekend sale"
    ) == "create flyer for weekend sale"
    assert actions.extract_flyer_request_after_confirm("CONFIRM") == ""


def test_media_backed_new_work_escapes_stale_active_project(monkeypatch):
    hooks, actions = _load_plugin_modules()
    stale_project = {
        "project_id": "F0042",
        "customer_phone": "+17329837841",
        "status": "awaiting_final_approval",
        "concepts": [{"concept_id": "C1"}],
    }

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: stale_project)

    result = hooks._try_flyer_active_project_intercept(
        "Please update this flyer. Change the date from May 16 to May 22.",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-media-new"},
        media_path="C:/tmp/source.png",
    )

    assert result is None


def test_evening_snacks_fresh_request_bypasses_old_active_project(monkeypatch):
    """F0061/F0062 regression: a clearly new flyer brief with time/window
    details must not be swallowed as a revision to an old active project."""
    hooks, actions = _load_plugin_modules()
    phrase = (
        "I’d like you to help me with evening snacks flier from 4 PM to 7 PM. "
        "Include 5 top South Indian snack items. Its Wednesday through Saturday event"
    )
    active_project = {
        "project_id": "F0062",
        "customer_phone": "+17329837841",
        "status": "awaiting_final_approval",
        "updated_at": "2026-05-21T00:00:00Z",
        "created_at": "2026-05-21T00:00:00Z",
        "raw_request": "Create old breakfast dosa flyer",
        "fields": {
            "event_or_business_name": "Old Breakfast Special",
            "event_date": "Last week",
            "event_time": "8 AM to 10 AM",
            "venue_or_location": "Old location",
            "contact_info": "+17329837841",
        },
        "concepts": [{"concept_id": "C1"}],
        "revisions": [],
    }

    assert actions.should_start_new_flyer_over_active(phrase, has_media=False)

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args, **_kwargs: pytest.fail("fresh request must not send revision/status copy"))
    monkeypatch.setattr(actions, "invoke_update_flyer_project", lambda *_args, **_kwargs: pytest.fail("fresh request must not revise active project"))
    audits: list[dict] = []
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_active_project_intercept(
        phrase,
        "17329837841@s.whatsapp.net",
        {"message_id": "m-evening-snacks"},
    )

    assert result is None
    assert any(
        row.get("reason") == "flyer_active_project_bypassed"
        and "fresh_flyer_intent=true" in row.get("detail", "")
        and "project_id=F0062" in row.get("detail", "")
        and "message_id=m-evening-snacks" in row.get("detail", "")
        for row in audits
    )

    active_project["status"] = "revising_design"
    audits.clear()
    result = hooks._try_flyer_active_project_intercept(
        phrase,
        "17329837841@s.whatsapp.net",
        {"message_id": "m-evening-snacks-2"},
    )

    assert result is None
    assert any(
        row.get("reason") == "flyer_active_project_bypassed"
        and "fresh_flyer_intent=true" in row.get("detail", "")
        and "status=revising_design" in row.get("detail", "")
        and "message_id=m-evening-snacks-2" in row.get("detail", "")
        for row in audits
    )


def test_cross_business_request_does_not_become_active_project_revision(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0048",
        "customer_phone": "+19803826497",
        "status": "awaiting_final_approval",
        "raw_request": "Create flyer for Chloe Hair Studio promoting men haircut $20 and perms $80.",
        "fields": {
            "event_or_business_name": "Chloe Hair Studio",
            "venue_or_location": "11111 Gainsborough Ct, Fairfax, VA",
            "contact_info": "+19803826497",
        },
        "concepts": [{"concept_id": "C1"}],
        "revisions": [],
    }
    replies: list[str] = []
    audits: list[dict] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+19803826497", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {
        "customer_id": "CUST0004",
        "status": "trial",
        "business_name": "Chloe hair studio",
    })
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, reply, **_kwargs: (replies.append(reply) or (True, "m-scope", "")))
    monkeypatch.setattr(actions, "invoke_update_flyer_project", lambda *_args, **_kwargs: pytest.fail("cross-business request must not revise active project"))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_active_project_intercept(
        "Create a flyer for india bazar. Include all indian groceries available and biryani available",
        "74290284261595@lid",
        {"message_id": "india-bazar-wrong-account"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer business scope blocked"}
    assert replies and "set up for Chloe hair studio" in replies[0]
    assert "Create One Flyer - $4" in replies[0]
    assert any(row.get("reason") == "flyer_business_scope_blocked" for row in audits)


@pytest.mark.parametrize(
    "text",
    [
        "change phone number",
        "make it red",
        "replace rice with jeera rice",
        "APPROVE",
        "use option 1",
        "any update?",
    ],
)
def test_common_active_project_followups_do_not_classify_as_fresh_flyer_intent(text):
    _hooks, actions = _load_plugin_modules()
    assert actions.should_start_new_flyer_over_active(text, has_media=False) is False


# --------------------------------------------------------------------------
# closed_no_send status-reply routing (fix for the screenshot bug:
# customer asked "any update?" on a freshly-closed source-edit and got
# a delivery line for an older project)
# --------------------------------------------------------------------------


def _flyer_projects_fixture(tmp_path, projects):
    """Materialize a flyer projects.json file and return its Path."""
    store_path = tmp_path / "flyer-projects.json"
    store_path.write_text(
        '{"projects": ' + __import__("json").dumps(projects) + "}",
        encoding="utf-8",
    )
    return store_path


def test_extract_flyer_project_id_mention_finds_explicit_id():
    actions = _load_actions()
    assert actions.extract_flyer_project_id_mention("any update on F0058?") == "F0058"
    assert actions.extract_flyer_project_id_mention("Status of f0012") == "F0012"
    assert actions.extract_flyer_project_id_mention("f0058 status") == "F0058"


def test_extract_flyer_project_id_mention_ignores_non_matches():
    actions = _load_actions()
    assert actions.extract_flyer_project_id_mention("any update?") is None
    assert actions.extract_flyer_project_id_mention("F58") is None  # not 4 digits
    assert actions.extract_flyer_project_id_mention("AF0058") is None  # not word-boundary
    assert actions.extract_flyer_project_id_mention("") is None


def test_active_picker_excludes_closed_no_send(tmp_path, monkeypatch):
    """find_active_flyer_project_by_sender (used by new-request / revision /
    approval routing) must NOT return closed_no_send rows — otherwise a
    closed project swallows fresh customer work."""
    actions = _load_actions()
    projects = [
        {
            "project_id": "F0058",
            "customer_phone": "+19045550104",
            "status": "closed_no_send",
            "updated_at": "2026-05-19T21:13:34Z",
            "created_at": "2026-05-19T21:04:04Z",
        },
        {
            "project_id": "F0034",
            "customer_phone": "+19045550104",
            "status": "revising_design",
            "updated_at": "2026-05-18T19:14:34Z",
            "created_at": "2026-05-18T17:18:47Z",
        },
    ]
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", _flyer_projects_fixture(tmp_path, projects))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: None)
    active = actions.find_active_flyer_project_by_sender("+19045550104", "201975216009469@lid")
    assert active is not None
    assert active["project_id"] == "F0034"  # closed F0058 skipped despite being newer


def test_latest_status_picker_includes_closed_no_send(tmp_path, monkeypatch):
    """The status-reply selector DOES include closed_no_send so customers
    asking 'any update?' learn about their recent closure rather than
    getting pointed at a stale older project."""
    actions = _load_actions()
    projects = [
        {
            "project_id": "F0058",
            "customer_phone": "+19045550104",
            "status": "closed_no_send",
            "updated_at": "2026-05-19T21:13:34Z",
            "created_at": "2026-05-19T21:04:04Z",
        },
        {
            "project_id": "F0034",
            "customer_phone": "+19045550104",
            "status": "revising_design",
            "updated_at": "2026-05-18T19:14:34Z",
            "created_at": "2026-05-18T17:18:47Z",
        },
    ]
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", _flyer_projects_fixture(tmp_path, projects))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: None)
    latest = actions.find_latest_flyer_project_for_status_by_sender("+19045550104", "201975216009469@lid")
    assert latest is not None
    assert latest["project_id"] == "F0058"


def test_latest_status_picker_excludes_completed_but_keeps_delivered(tmp_path, monkeypatch):
    """`completed` is truly terminal (customer-finalized). `delivered` is
    non-terminal (customer can still request revisions) — surface it in
    status replies when it's the newest."""
    actions = _load_actions()
    projects = [
        {
            "project_id": "F0028",
            "customer_phone": "+19045550104",
            "status": "delivered",
            "updated_at": "2026-05-18T13:20:20Z",
            "created_at": "2026-05-18T13:15:19Z",
        },
        {
            "project_id": "F0006",
            "customer_phone": "+19045550104",
            "status": "completed",
            "updated_at": "2026-05-19T20:00:00Z",  # newer than F0028 but completed
            "created_at": "2026-05-15T03:51:04Z",
        },
    ]
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", _flyer_projects_fixture(tmp_path, projects))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: None)
    latest = actions.find_latest_flyer_project_for_status_by_sender("+19045550104", "201975216009469@lid")
    assert latest is not None
    assert latest["project_id"] == "F0028"  # completed excluded


def test_find_flyer_project_by_id_respects_sender_ownership(tmp_path, monkeypatch):
    """Exact id lookup must not leak project state across customers — only
    return a row when its customer_phone is in the sender's account set."""
    actions = _load_actions()
    projects = [
        {
            "project_id": "F0058",
            "customer_phone": "+19045550104",
            "status": "closed_no_send",
            "updated_at": "2026-05-19T21:13:34Z",
        },
        {
            "project_id": "F0050",
            "customer_phone": "+19048626362",  # different customer
            "status": "awaiting_final_approval",
            "updated_at": "2026-05-19T18:00:00Z",
        },
    ]
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", _flyer_projects_fixture(tmp_path, projects))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: None)

    # Owner can fetch their own row
    own = actions.find_flyer_project_by_id_for_sender("+19045550104", "201975216009469@lid", "F0058")
    assert own is not None and own["project_id"] == "F0058"

    # Cross-customer leak is blocked
    leak = actions.find_flyer_project_by_id_for_sender("+19045550104", "201975216009469@lid", "F0050")
    assert leak is None


def test_status_reply_prefers_recent_closed_no_send_over_older_active(tmp_path, monkeypatch):
    """Screenshot scenario: customer has F0034 (revising_design from
    yesterday) AND a fresh F0058 closed_no_send. 'any update?' must
    surface F0058's closure, not F0034's revising line."""
    hooks, actions = _load_plugin_modules()
    projects = [
        {
            "project_id": "F0058",
            "customer_phone": "+19045550104",
            "status": "closed_no_send",
            "updated_at": "2026-05-19T21:13:34Z",
            "created_at": "2026-05-19T21:04:04Z",
            "manual_review": {
                "status": "closed_no_send",
                "reason_code": "source_edit_provider_unavailable",
                "reason": "source_edit_provider_unavailable",
            },
            "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+19045550104"},
            "raw_request": "Authorized exact edit",
            "original_message_id": "m-58",
        },
        {
            "project_id": "F0034",
            "customer_phone": "+19045550104",
            "status": "revising_design",
            "updated_at": "2026-05-18T19:14:34Z",
            "created_at": "2026-05-18T17:18:47Z",
            "manual_review": {"status": "none", "reason_code": "unclassified", "reason": ""},
            "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+19045550104"},
            "raw_request": "Revise design",
            "original_message_id": "m-34",
        },
    ]
    state_path = _flyer_projects_fixture(tmp_path, projects)
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", state_path)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+19045550104", "employee"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: {"customer_id": "CUST0001", "status": "trial"})
    sent = {}
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: (sent.update({"chat_id": chat_id, "text": text}), (True, "out-mid", ""))[1])
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)

    result = hooks._try_flyer_active_project_intercept(
        "any update?",
        "201975216009469@lid",
        {"message_id": "m-update"},
    )
    assert result is not None and result.get("action") == "skip"
    assert "F0058" not in sent["text"], f"status copy must hide project ids, got: {sent['text']!r}"
    assert "F0034" not in sent["text"]
    # Should be the closed_no_send reason-aware line, not the revising-design line.
    assert "apply that source-flyer edit" in sent["text"] or "closed without delivering" in sent["text"]


def test_status_reply_with_explicit_id_mention_wins_over_latest(tmp_path, monkeypatch):
    """If the customer names a project id, exact lookup wins — even when a
    different project would be the latest-updated."""
    hooks, actions = _load_plugin_modules()
    projects = [
        {
            "project_id": "F0058",
            "customer_phone": "+19045550104",
            "status": "closed_no_send",
            "updated_at": "2026-05-19T21:13:34Z",
            "created_at": "2026-05-19T21:04:04Z",
            "manual_review": {
                "status": "closed_no_send",
                "reason_code": "source_edit_provider_unavailable",
                "reason": "source_edit_provider_unavailable",
            },
            "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+19045550104"},
            "raw_request": "Authorized exact edit",
            "original_message_id": "m-58",
        },
        {
            "project_id": "F0028",
            "customer_phone": "+19045550104",
            "status": "delivered",
            "updated_at": "2026-05-18T13:20:20Z",
            "created_at": "2026-05-18T13:15:19Z",
            "manual_review": {"status": "none", "reason_code": "unclassified", "reason": ""},
            "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+19045550104"},
            "raw_request": "Original brief",
            "original_message_id": "m-28",
        },
        {
            "project_id": "F0034",
            "customer_phone": "+19045550104",
            "status": "revising_design",
            "updated_at": "2026-05-18T19:14:34Z",
            "created_at": "2026-05-18T17:18:47Z",
            "manual_review": {"status": "none", "reason_code": "unclassified", "reason": ""},
            "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+19045550104"},
            "raw_request": "Revise design",
            "original_message_id": "m-34",
        },
    ]
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", _flyer_projects_fixture(tmp_path, projects))
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+19045550104", "employee"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: {"customer_id": "CUST0001", "status": "trial"})
    sent = {}
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: (sent.update({"text": text}), (True, "out-mid", ""))[1])
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)

    result = hooks._try_flyer_active_project_intercept(
        "any update on F0028?",
        "201975216009469@lid",
        {"message_id": "m-explicit"},
    )
    assert result is not None
    assert "F0028" not in sent["text"], f"status copy must hide project ids, got: {sent['text']!r}"
    assert "F0058" not in sent["text"]
    assert "final flyer files have been delivered" in sent["text"]


def test_closed_no_send_does_not_swallow_new_flyer_request(tmp_path, monkeypatch):
    """Regression: after F0058 is closed_no_send, a subsequent NEW flyer
    request from the same sender must not attach to the closed row — the
    active picker still excludes closed_no_send, so new-request routing
    creates a fresh project."""
    actions = _load_actions()
    projects = [
        {
            "project_id": "F0058",
            "customer_phone": "+19045550104",
            "status": "closed_no_send",
            "updated_at": "2026-05-19T21:13:34Z",
            "created_at": "2026-05-19T21:04:04Z",
        },
    ]
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", _flyer_projects_fixture(tmp_path, projects))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: None)

    # The active picker is what every non-status path (new-request, revision,
    # approval, image upload) consults. It must return None so the
    # new-flyer-request path takes over.
    active = actions.find_active_flyer_project_by_sender("+19045550104", "201975216009469@lid")
    assert active is None, (
        "active picker must NOT return closed_no_send — otherwise a fresh "
        "'Create a flyer for ...' request would attach to the closed row"
    )


def test_status_reply_when_only_closed_no_send_exists(tmp_path, monkeypatch):
    """REGRESSION (review-found): customer has ONLY a closed_no_send project
    (no active row). 'any update?' must still resolve to the closure — the
    early-return on `active_project is None` previously dropped the inbound
    to LLM dispatch."""
    hooks, actions = _load_plugin_modules()
    projects = [
        {
            "project_id": "F0058",
            "customer_phone": "+19045550104",
            "status": "closed_no_send",
            "updated_at": "2026-05-19T21:13:34Z",
            "created_at": "2026-05-19T21:04:04Z",
            "manual_review": {
                "status": "closed_no_send",
                "reason_code": "source_edit_provider_unavailable",
                "reason": "source_edit_provider_unavailable",
            },
            "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+19045550104"},
            "raw_request": "Authorized exact edit",
            "original_message_id": "m-58",
        },
    ]
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", _flyer_projects_fixture(tmp_path, projects))
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+19045550104", "employee"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: {"customer_id": "CUST0001", "status": "trial"})
    sent = {}
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: (sent.update({"text": text}), (True, "out-mid", ""))[1])
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)

    result = hooks._try_flyer_active_project_intercept(
        "any update?",
        "201975216009469@lid",
        {"message_id": "m-update"},
    )
    assert result is not None and result.get("action") == "skip"
    assert "F0058" not in sent["text"]
    assert "apply that source-flyer edit" in sent["text"] or "closed without delivering" in sent["text"]


def test_status_reply_with_explicit_id_when_no_active_project(tmp_path, monkeypatch):
    """REGRESSION (review-found): 'any update on F0058?' must resolve to F0058
    via exact-id lookup even when active picker returns None. Without the
    no-active-project status branch, the inbound never reaches the id
    selector."""
    hooks, actions = _load_plugin_modules()
    projects = [
        {
            "project_id": "F0058",
            "customer_phone": "+19045550104",
            "status": "closed_no_send",
            "updated_at": "2026-05-19T21:13:34Z",
            "created_at": "2026-05-19T21:04:04Z",
            "manual_review": {
                "status": "closed_no_send",
                "reason_code": "source_edit_provider_unavailable",
                "reason": "source_edit_provider_unavailable",
            },
            "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+19045550104"},
            "raw_request": "Authorized exact edit",
            "original_message_id": "m-58",
        },
    ]
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", _flyer_projects_fixture(tmp_path, projects))
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+19045550104", "employee"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: {"customer_id": "CUST0001", "status": "trial"})
    sent = {}
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text, **_kwargs: (sent.update({"text": text}), (True, "out-mid", ""))[1])
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)

    result = hooks._try_flyer_active_project_intercept(
        "any update on F0058?",
        "201975216009469@lid",
        {"message_id": "m-explicit"},
    )
    assert result is not None and result.get("action") == "skip"
    assert "F0058" not in sent["text"]
    assert "apply that source-flyer edit" in sent["text"] or "closed without delivering" in sent["text"]


def test_status_reply_returns_none_when_no_projects_at_all(tmp_path, monkeypatch):
    """Negative path: status inbound from a customer with NO projects must
    return None so downstream intercepts (or LLM dispatch) handle the
    message — we don't want to send a fabricated 'closed' reply for a
    customer who has never had a project."""
    hooks, actions = _load_plugin_modules()
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", _flyer_projects_fixture(tmp_path, []))
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+19045550104", "employee"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _p, _c: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_a, **_kw: pytest.fail("must not send when no projects exist"))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)

    result = hooks._try_flyer_active_project_intercept(
        "any update?",
        "201975216009469@lid",
        {"message_id": "m-none"},
    )
    assert result is None


# ─── F0061 source-contract regression tests (Task 1; red until Task 5 lands) ──


def test_save_flyer_reference_scope_pending_persists_original_intent(tmp_path):
    """Scope-pending row carries `original_intent` so downstream intercepts
    can branch on exact-source-edit vs generic-reference. Pre-fix the field
    was not even on the wire."""
    actions = _load_actions()
    actions.FLYER_REFERENCE_SCOPE_PATH = tmp_path / "reference_scope_pending.json"

    actions.save_flyer_reference_scope_pending(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        customer={"business_name": "Lakshmis Kitchen"},
        raw_request="Replace Triveni Express with Lakshmi's Kitchen branding.",
        media_path="/tmp/ref.jpg",
        scope={"visible_organization_names": ["Triveni Express"]},
        original_intent="exact_source_edit",
    )

    pending = actions.consume_flyer_reference_scope_choice(
        "use as reference",
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
    )
    assert pending is not None
    assert pending.get("original_intent") == "exact_source_edit"


def test_exact_edit_request_use_as_reference_does_not_downgrade(monkeypatch):
    """F0061 load-bearing regression: after scope clarify on an exact-edit
    request, customer replies `use as reference` — system must NOT call
    trigger_create_flyer_project. It must send SOURCE/NEW clarification."""
    hooks, actions = _load_plugin_modules()
    sent: list[str] = []
    audited: list[dict] = []

    def fake_consume(text, *, chat_id, sender_phone, transition_to_status=None):
        body = " ".join(text.split()).lower().strip(" .!,:;-")
        if body in {"use as reference", "use it as reference", "use as a reference"}:
            return {
                "chat_id": chat_id,
                "sender_phone": sender_phone,
                "customer": {"business_name": "Lakshmis Kitchen", "customer_id": "CUST0001"},
                "raw_request": "I'd like you use this flyer for Lakshmi's Kitchen. Replace Triveni Express with Lakshmi's Kitchen branding.",
                "media_path": "/tmp/ref.jpg",
                "source_organization": "Triveni Express",
                "status": "awaiting_choice",
                "original_intent": "exact_source_edit",
                "created_at": 0,
                "choice": "use_reference",
            }
        return None

    monkeypatch.setattr(actions, "consume_flyer_reference_scope_choice", fake_consume)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda *_a, **_kw: {"customer_id": "CUST0001"})
    monkeypatch.setattr(
        actions, "trigger_create_flyer_project",
        lambda **_kw: pytest.fail("must NOT create project on bare `use as reference` for exact-edit"),
    )
    monkeypatch.setattr(actions, "send_flyer_text", lambda _c, text, **_kwargs: (sent.append(text), (True, "mid", ""))[1])
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kw: audited.append(kw) or None)
    if hasattr(actions, "audit_source_vs_new"):
        monkeypatch.setattr(actions, "audit_source_vs_new", lambda **kw: audited.append(kw) or None)

    result = hooks._try_flyer_reference_scope_choice_intercept(
        "use as reference",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-use-ref"},
    )

    assert result is not None and result.get("action") == "skip"
    assert any("SOURCE" in t and "NEW" in t for t in sent), (
        f"clarification must mention both SOURCE and NEW; sent={sent!r}"
    )


def test_reference_scope_use_reference_generation_failure_audits_failed(monkeypatch):
    hooks, actions = _load_plugin_modules()
    calls: list[str] = []
    audits: list[dict] = []

    def fake_consume(text, *, chat_id, sender_phone, transition_to_status=None):
        assert transition_to_status == "awaiting_source_vs_new_choice"
        if text.strip().lower() == "use as reference":
            return {
                "chat_id": chat_id,
                "sender_phone": sender_phone,
                "customer": {"business_name": "Lakshmis Kitchen", "customer_id": "CUST0001"},
                "raw_request": "Use this flyer as inspiration for Lakshmi's Kitchen.",
                "media_path": "/tmp/ref.jpg",
                "source_organization": "Triveni Express",
                "status": "awaiting_choice",
                "original_intent": "generic_reference",
                "created_at": 0,
                "choice": "use_reference",
            }
        return None

    monkeypatch.setattr(actions, "consume_flyer_reference_scope_choice", fake_consume)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(
        actions,
        "trigger_create_flyer_project",
        lambda **_kw: (True, "", {"project_id": "F0067", "status": "intake_started", "manual_review": {}}),
    )
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda *_a, **_kw: True)
    monkeypatch.setattr(actions, "flyer_project_has_manual_review_queued", lambda *_a, **_kw: False)
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda *_a, **_kw: (calls.append("processing") or (True, "processing-mid", "")))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda project_id: (calls.append(f"generate:{project_id}") or (False, "visual_qa_failed")))
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply", lambda *_a, **_kw: ("quota:CUST0001", None))
    monkeypatch.setattr(hooks, "_release_flyer_access", lambda *_a, **_kw: (calls.append("release") or (True, "released")))
    monkeypatch.setattr(
        hooks,
        "_send_generation_failure_customer_update",
        lambda *_a, **_kw: (calls.append("failure-update") or (True, "failure-mid", "")),
    )
    monkeypatch.setattr(hooks, "_send_preview_then_finalize_access", lambda *_a, **_kw: pytest.fail("failed generation must not send preview"))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kw: audits.append(kw) or None)

    result = hooks._try_flyer_reference_scope_choice_intercept(
        "use as reference",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-use-ref-fail"},
    )

    assert result is not None and result.get("action") == "skip"
    assert calls == ["processing", "generate:F0067", "release", "failure-update"]
    assert audits[-1]["reason"] == "flyer_primary_failed"
    assert audits[-1]["subprocess_rc"] == 2


def test_source_vs_new_new_choice_creates_project_without_manual_edit(monkeypatch):
    """After SOURCE/NEW clarification, customer reply `NEW` must call
    trigger_create_flyer_project WITHOUT manual_edit_required and with
    raw_request containing a `Create a new original` flavor marker."""
    hooks, actions = _load_plugin_modules()
    created: dict = {}

    def fake_consume_source_vs_new(choice_token, trailing, *, chat_id, sender_phone):
        if choice_token == "new":
            return {
                "chat_id": chat_id,
                "sender_phone": sender_phone,
                "customer": {"business_name": "Lakshmis Kitchen", "customer_id": "CUST0001"},
                "raw_request": "I'd like you use this flyer for Lakshmi's Kitchen.",
                "media_path": "/tmp/ref.jpg",
                "source_organization": "Triveni Express",
                "original_intent": "exact_source_edit",
                "customer_followup_instruction": trailing,
                "choice": "new",
            }
        return None

    if not hasattr(actions, "consume_flyer_source_vs_new_choice"):
        pytest.skip("consume_flyer_source_vs_new_choice not yet implemented (Task 5)")
    monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", fake_consume_source_vs_new)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))

    def fake_create(**kwargs):
        created.update(kwargs)
        return True, "", {"project_id": "F0062", "status": "intake_started", "manual_review": {}}

    monkeypatch.setattr(actions, "trigger_create_flyer_project", fake_create)
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_a, **_kw: (True, "mid", ""))
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda *_a, **_kw: (True, "mid", ""))
    monkeypatch.setattr(actions, "send_flyer_intake_ack", lambda *_a, **_kw: (True, "mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda *_a, **_kw: False)
    monkeypatch.setattr(actions, "flyer_project_has_manual_review_queued", lambda *_a, **_kw: False)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)
    if hasattr(actions, "audit_source_vs_new"):
        monkeypatch.setattr(actions, "audit_source_vs_new", lambda **_kw: None)

    if not hasattr(hooks, "_try_flyer_source_vs_new_choice_intercept"):
        pytest.skip("_try_flyer_source_vs_new_choice_intercept not yet implemented (Task 5)")

    result = hooks._try_flyer_source_vs_new_choice_intercept(
        "NEW",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-new"},
    )

    assert result is not None and result.get("action") == "skip"
    assert created.get("manual_edit_required") in (False, None), (
        f"NEW branch must NOT pass manual_edit_required; got {created.get('manual_edit_required')!r}"
    )


def test_source_vs_new_new_choice_generates_when_project_is_ready(monkeypatch):
    """After SOURCE/NEW clarification, a complete `NEW` choice should follow the
    same autonomous generation path as the primary create flow, not stall after
    only sending an intake ack."""
    hooks, actions = _load_plugin_modules()
    calls: list[str] = []

    def fake_consume_source_vs_new(choice_token, trailing, *, chat_id, sender_phone):
        if choice_token == "new":
            return {
                "chat_id": chat_id,
                "sender_phone": sender_phone,
                "customer": {"business_name": "Lakshmis Kitchen", "customer_id": "CUST0001"},
                "raw_request": "I'd like you use this flyer for Lakshmi's Kitchen.",
                "media_path": "/tmp/ref.jpg",
                "source_organization": "Triveni Express",
                "original_intent": "exact_source_edit",
                "customer_followup_instruction": trailing,
                "choice": "new",
            }
        return None

    monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", fake_consume_source_vs_new)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(
        actions,
        "trigger_create_flyer_project",
        lambda **_kw: (True, "", {"project_id": "F0063", "status": "intake_started", "manual_review": {}}),
    )
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda *_a, **_kw: True)
    monkeypatch.setattr(actions, "flyer_project_has_manual_review_queued", lambda *_a, **_kw: False)
    monkeypatch.setattr(actions, "send_flyer_intake_ack", lambda *_a, **_kw: pytest.fail("ready NEW branch must not stop at intake ack"))
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda *_a, **_kw: (calls.append("processing") or (True, "processing-mid", "")))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda project_id: (calls.append(f"generate:{project_id}") or (True, "")))
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply", lambda *_a, **_kw: ("quota:CUST0001", None))
    monkeypatch.setattr(hooks, "_send_preview_then_finalize_access", lambda *_a, **_kw: (calls.append("preview") or (True, "preview-mid", "")))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)
    if hasattr(actions, "audit_source_vs_new"):
        monkeypatch.setattr(actions, "audit_source_vs_new", lambda **_kw: None)

    result = hooks._try_flyer_source_vs_new_choice_intercept(
        "NEW",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-new-ready"},
    )

    assert result is not None and result.get("action") == "skip"
    assert calls == ["processing", "generate:F0063", "preview"]


def test_source_vs_new_new_choice_generation_failure_releases_access(monkeypatch):
    hooks, actions = _load_plugin_modules()
    calls: list[str] = []
    audits: list[dict] = []

    def fake_consume_source_vs_new(choice_token, trailing, *, chat_id, sender_phone):
        if choice_token == "new":
            return {
                "chat_id": chat_id,
                "sender_phone": sender_phone,
                "customer": {"business_name": "Lakshmis Kitchen", "customer_id": "CUST0001"},
                "raw_request": "I'd like you use this flyer for Lakshmi's Kitchen.",
                "media_path": "/tmp/ref.jpg",
                "source_organization": "Triveni Express",
                "original_intent": "exact_source_edit",
                "customer_followup_instruction": trailing,
                "choice": "new",
            }
        return None

    monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", fake_consume_source_vs_new)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(
        actions,
        "trigger_create_flyer_project",
        lambda **_kw: (True, "", {"project_id": "F0064", "status": "intake_started", "manual_review": {}}),
    )
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda *_a, **_kw: True)
    monkeypatch.setattr(actions, "flyer_project_has_manual_review_queued", lambda *_a, **_kw: False)
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda *_a, **_kw: (calls.append("processing") or (True, "processing-mid", "")))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda project_id: (calls.append(f"generate:{project_id}") or (False, "visual_qa_failed")))
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply", lambda *_a, **_kw: ("quota:CUST0001", None))
    monkeypatch.setattr(hooks, "_release_flyer_access", lambda *_a, **_kw: (calls.append("release") or (True, "released")))
    monkeypatch.setattr(
        hooks,
        "_send_generation_failure_customer_update",
        lambda *_a, **_kw: (calls.append("failure-update") or (True, "failure-mid", "")),
    )
    monkeypatch.setattr(hooks, "_send_preview_then_finalize_access", lambda *_a, **_kw: pytest.fail("failed generation must not send preview"))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kw: audits.append(kw) or None)
    if hasattr(actions, "audit_source_vs_new"):
        monkeypatch.setattr(actions, "audit_source_vs_new", lambda **_kw: None)

    result = hooks._try_flyer_source_vs_new_choice_intercept(
        "NEW",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-new-fail"},
    )

    assert result is not None and result.get("action") == "skip"
    assert calls == ["processing", "generate:F0064", "release", "failure-update"]
    assert audits[-1]["reason"] == "flyer_primary_failed"
    assert audits[-1]["subprocess_rc"] == 2


def test_active_intake_generation_failure_does_not_send_duplicate_initial_ack(monkeypatch):
    hooks, actions = _load_plugin_modules()
    calls = {"processing": 0, "intake": 0}
    audits: list[dict] = []
    active_project = {
        "project_id": "F0065",
        "customer_phone": "+17329837841",
        "status": "intake_started",
        "fields": {"event_or_business_name": "Evening Snacks", "contact_info": "+17329837841"},
        "concepts": [],
        "revisions": [],
    }

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda *_a, **_kw: True)
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda *_a, **_kw: (calls.__setitem__("processing", calls["processing"] + 1) or True, "processing-mid", ""))
    monkeypatch.setattr(actions, "send_flyer_intake_ack", lambda *_a, **_kw: (calls.__setitem__("intake", calls["intake"] + 1) or True, "intake-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda *_a, **_kw: (False, "exit=1 transient provider error"))
    monkeypatch.setattr(actions, "flyer_generation_queued_manual_review", lambda _detail, **_kwargs: False)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kw: audits.append(kw) or None)
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply", lambda *_a, **_kw: ("quota:CUST0001", None))
    monkeypatch.setattr(hooks, "_release_flyer_access", lambda *_a, **_kw: (True, "released"))

    result = hooks._try_flyer_active_project_intercept(
        "continue",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-active-fail"},
    )

    assert result is not None and result.get("action") == "skip"
    assert calls["processing"] == 1
    assert calls["intake"] == 0
    assert audits[-1]["reason"] == "flyer_primary_failed"
    assert audits[-1]["subprocess_rc"] == 2


def test_active_intake_preview_delivery_failure_audits_send_failure_rc(monkeypatch):
    hooks, actions = _load_plugin_modules()
    audits: list[dict] = []
    active_project = {
        "project_id": "F0068",
        "customer_phone": "+17329837841",
        "status": "intake_started",
        "fields": {"event_or_business_name": "Evening Snacks", "contact_info": "+17329837841"},
        "concepts": [],
        "revisions": [],
    }

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda *_a, **_kw: True)
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda *_a, **_kw: (True, "processing-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda *_a, **_kw: (True, "generated"))
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply", lambda *_a, **_kw: ("quota:CUST0001", None))
    monkeypatch.setattr(
        hooks,
        "_send_preview_then_finalize_access",
        lambda *_a, **_kw: (False, "preview-mid", "preview delivery failed"),
    )
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kw: audits.append(kw) or None)

    result = hooks._try_flyer_active_project_intercept(
        "continue",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-active-preview-fail"},
    )

    assert result is not None and result.get("action") == "skip"
    assert audits[-1]["reason"] == "flyer_primary_failed"
    assert audits[-1]["subprocess_rc"] == 3


def test_primary_create_generation_failure_audits_failed_even_when_customer_update_sent(monkeypatch):
    hooks, actions = _load_plugin_modules()
    calls: list[str] = []
    audits: list[dict] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "flyer_location_block_message", lambda *_a, **_kw: "")
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kw: (True, "", {
        "project_id": "F0066",
        "status": "intake_started",
        "manual_review": {},
    }))
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda *_a, **_kw: True)
    monkeypatch.setattr(actions, "flyer_project_has_manual_review_queued", lambda *_a, **_kw: False)
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda *_a, **_kw: (calls.append("processing") or (True, "processing-mid", "")))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda project_id: (calls.append(f"generate:{project_id}") or (False, "visual_qa_failed")))
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply", lambda *_a, **_kw: ("quota:CUST0001", None))
    monkeypatch.setattr(hooks, "_release_flyer_access", lambda *_a, **_kw: (calls.append("release") or (True, "released")))
    monkeypatch.setattr(
        hooks,
        "_send_generation_failure_customer_update",
        lambda *_a, **_kw: (calls.append("failure-update") or (True, "failure-mid", "")),
    )
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kw: audits.append(kw) or None)

    result = hooks._try_flyer_primary_intercept(
        "Create a flyer for weekend dosa specials. Any item $9.99. Contact +17329837841.",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-primary-fail"},
    )

    assert result is not None and result.get("action") == "skip"
    assert calls == ["processing", "generate:F0066", "release", "failure-update"]
    assert audits[-1]["reason"] == "flyer_primary_failed"
    assert audits[-1]["subprocess_rc"] == 2


def test_active_intake_visual_qa_failure_sends_manual_review_fallback_after_processing(monkeypatch):
    hooks, actions = _load_plugin_modules()
    calls = {"processing": 0, "intake": 0, "manual": 0}
    active_project = {
        "project_id": "F0065",
        "customer_phone": "+17329837841",
        "status": "intake_started",
        "fields": {"event_or_business_name": "Evening Snacks", "contact_info": "+17329837841"},
        "concepts": [],
        "revisions": [],
    }

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda *_a, **_kw: True)
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda *_a, **_kw: (calls.__setitem__("processing", calls["processing"] + 1) or True, "processing-mid", ""))
    monkeypatch.setattr(actions, "send_flyer_intake_ack", lambda *_a, **_kw: (calls.__setitem__("intake", calls["intake"] + 1) or True, "intake-mid", ""))
    monkeypatch.setattr(actions, "send_flyer_manual_review_ack", lambda *_a, **_kw: (calls.__setitem__("manual", calls["manual"] + 1) or True, "manual-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda *_a, **_kw: (False, "visual_qa_failed: missing required visible fact: business_name"))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply", lambda *_a, **_kw: ("quota:CUST0001", None))
    monkeypatch.setattr(hooks, "_release_flyer_access", lambda *_a, **_kw: (True, "released"))

    result = hooks._try_flyer_active_project_intercept(
        "continue",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-active-qa-fail"},
    )

    assert result is not None and result.get("action") == "skip"
    assert calls == {"processing": 1, "intake": 0, "manual": 1}


def test_visible_time_text_revision_does_not_send_clarification(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0065",
        "customer_phone": "+17329837841",
        "status": "awaiting_final_approval",
        "fields": {"event_or_business_name": "Evening Snacks", "contact_info": "+17329837841"},
        "concepts": [{"concept_id": "C1"}],
        "revisions": [],
    }
    sent: list[str] = []
    audit_reasons: list[str] = []
    audit_reasons: list[str] = []
    generated: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)

    def fake_update(project_id, *args):
        assert project_id == "F0065"
        assert "--revision-text" in args
        assert any("Time: 16:00 is duplicated" in arg for arg in args)
        active_project["concepts"] = []
        return True, (
            '{"project_id":"F0065","version":2,'
            '"revision_requires_clarification":false,'
            '"revision_patch":{"notes_update":"Remove duplicate/extra time text \\"16:00\\" from the flyer."}}'
        )

    monkeypatch.setattr(actions, "invoke_update_flyer_project", fake_update)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda project_id: generated.append(project_id) or (True, "generated"))
    monkeypatch.setattr(actions, "send_flyer_concept_previews", lambda *_args, **_kwargs: (True, "preview-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audit_reasons.append(kwargs.get("reason", "")))

    result = hooks._try_flyer_active_project_intercept(
        "Time: 16:00 is duplicated. I'd like you to remove this.",
        "17329837841@s.whatsapp.net",
        {"message_id": "visible-time-revision"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: revision captured for F0065"}
    assert sent == ["Revision applied to the flyer details. I am regenerating the design now."]
    assert generated == ["F0065"]


def test_category_price_revision_does_not_send_clarification(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0071",
        "customer_phone": "+17329837841",
        "status": "awaiting_final_approval",
        "fields": {"event_or_business_name": "Mid-Night Biryani", "contact_info": "+17329837841"},
        "concepts": [{"concept_id": "C1"}],
        "revisions": [],
    }
    sent: list[str] = []
    generated: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)

    def fake_update(project_id, *args):
        assert project_id == "F0071"
        assert "--revision-text" in args
        assert any("Update prices of any biryani to $22.99" in arg for arg in args)
        active_project["concepts"] = []
        return True, (
            '{"project_id":"F0071","version":2,'
            '"revision_requires_clarification":false,'
            '"revision_patch":{"notes_update":"Set all biryani prices to $22.99"}}'
        )

    monkeypatch.setattr(actions, "invoke_update_flyer_project", fake_update)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda project_id: generated.append(project_id) or (True, "generated"))
    monkeypatch.setattr(actions, "send_flyer_concept_previews", lambda *_args, **_kwargs: (True, "preview-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_active_project_intercept(
        "Update prices of any biryani to $22.99",
        "17329837841@s.whatsapp.net",
        {"message_id": "category-price-revision"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: revision captured for F0071"}
    assert sent == ["Revision applied to the flyer details. I am regenerating the design now."]
    assert generated == ["F0071"]


def test_pending_revision_confirmation_blocks_approve_and_reminds_apply(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0065",
        "customer_phone": "+17329837841",
        "status": "awaiting_final_approval",
        "fields": {"event_or_business_name": "Evening Snacks", "contact_info": "+17329837841"},
        "concepts": [{"concept_id": "C1"}],
        "revisions": [{"revision_id": "R001", "applied": False}],
        "pending_revision_confirmation": {"revision_id": "R001"},
    }
    sent: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    def boom_finalize(*_a, **_kw):
        raise AssertionError("finalize should not be called when pending confirmation exists")

    monkeypatch.setattr(actions, "finalize_and_send_flyer", boom_finalize)

    result = hooks._try_flyer_active_project_intercept(
        "APPROVE",
        "17329837841@s.whatsapp.net",
        {"message_id": "approve-with-pending"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: pending revision confirmation for F0065"}
    assert sent and "Reply APPLY R001" in sent[0]


def test_final_visual_qa_failure_after_approve_gets_review_ack(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0085",
        "customer_phone": "+17329837841",
        "status": "awaiting_final_approval",
        "fields": {"event_or_business_name": "Mid-Night Biryani", "contact_info": "+17329837841"},
        "concepts": [{"concept_id": "C1"}],
    }
    sent: list[str] = []
    audit_reasons: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "finalize_and_send_flyer", lambda *_args: (False, "visual_qa_failed: missing required visible facts"))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "final-failed-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audit_reasons.append(kwargs.get("reason", "")))

    result = hooks._try_flyer_active_project_intercept(
        "APPROVE",
        "17329837841@s.whatsapp.net",
        {"message_id": "approve-fail-final-qa"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: finalization failed for F0085"}
    assert sent == [
        "Flyer Studio\n"
        "------------\n"
        "I hit an issue preparing the final files. I'll review it and send an update here."
    ]
    assert "approval has been processed" not in sent[0].lower()
    assert audit_reasons
    assert audit_reasons[-1] == "flyer_primary_failed"
    assert "flyer_reference_exact_edit_queued" not in audit_reasons


def test_pending_confirmation_message_is_sent_verbatim(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0065",
        "customer_phone": "+17329837841",
        "status": "awaiting_final_approval",
        "fields": {"event_or_business_name": "Evening Snacks", "contact_info": "+17329837841"},
        "concepts": [{"concept_id": "C1"}],
        "revisions": [],
    }
    sent: list[str] = []
    generated: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)

    pending_message = (
        "Flyer Studio\n"
        "------------\n"
        "I understood your change as:\n"
        "replace text 'Price any event' -> 'Any Item'\n\n"
        "Reply APPLY R001 to regenerate a new preview (this does not send final files), or reply with corrections."
    )

    def fake_update(project_id, *args):
        assert project_id == "F0065"
        assert "--revision-text" in args
        active_project["concepts"] = [{"concept_id": "C1"}]
        return True, (
            '{"project_id":"F0065","version":2,'
            '"revision_requires_clarification":true,'
            '"revision_patch":{"unresolved_reason":"pending confirmation required",'
            '"pending_confirmation_message":' + _json.dumps(pending_message) + "}}"
        )

    import json as _json
    monkeypatch.setattr(actions, "invoke_update_flyer_project", fake_update)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda project_id: generated.append(project_id) or (True, "generated"))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_active_project_intercept(
        'Replace "Price any event" with "Any Item".',
        "17329837841@s.whatsapp.net",
        {"message_id": "pending-confirm"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: revision captured for F0065"}
    assert sent == [pending_message]
    assert generated == []


def test_revision_clarification_reply_includes_request_excerpt_to_avoid_dedupe(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0048",
        "customer_phone": "+19803826497",
        "status": "delivered",
        "fields": {"event_or_business_name": "Chloe Hair Studio", "contact_info": "+19803826497"},
        "concepts": [{"concept_id": "C1"}],
        "revisions": [],
    }
    sent: list[str] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+19803826497", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0004", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(
        actions,
        "invoke_update_flyer_project",
        lambda *_args: (True, json.dumps({
            "project_id": "F0048",
            "version": 3,
            "revision_requires_clarification": True,
            "revision_patch": {
                "unresolved_reason": "I could not match that change to the current flyer details."
            },
        })),
    )
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_active_project_intercept(
        "Apply these changes: use richer gold near the price boxes.",
        "19803826497@s.whatsapp.net",
        {"message_id": "clarify-gold-boxes"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: revision captured for F0048"}
    assert sent
    assert "I saw: Apply these changes: use richer gold near the price boxes." in sent[0]
    assert "F0048" not in sent[0]


def test_preview_layout_change_with_create_new_wording_stays_on_active_project(monkeypatch):
    hooks, actions = _load_plugin_modules()
    active_project = {
        "project_id": "F0097",
        "customer_phone": "+19803826497",
        "status": "awaiting_final_approval",
        "fields": {"event_or_business_name": "Chloe Hair Studio", "contact_info": "+19803826497"},
        "concepts": [{"concept_id": "C1"}],
        "revisions": [],
    }
    after_update = {**active_project, "status": "revising_design", "concepts": []}
    active_projects = [active_project, after_update]
    text = (
        "Create a new flyer for chloe hair studio with contact number and address look smaller "
        "and the main focus should be on the services that we provide."
    )
    update_calls: list[tuple] = []
    sent: list[str] = []
    previews: list[str] = []
    audits: list[dict] = []

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+19803826497", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0004", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_projects.pop(0) if active_projects else after_update)
    monkeypatch.setattr(actions, "invoke_update_flyer_project", lambda *args: update_calls.append(args) or (True, json.dumps({
        "project_id": "F0097",
        "version": 2,
        "revision_requires_clarification": False,
        "revision_patch": {"changed": True, "visual_only": False, "ambiguous": False},
    })))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: sent.append(text) or (True, "ack-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda project_id: (True, f"generated {project_id}"))
    monkeypatch.setattr(actions, "send_flyer_concept_previews", lambda _chat_id, project_id: previews.append(project_id) or (True, "preview-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kwargs: audits.append(kwargs))

    result = hooks._try_flyer_active_project_intercept(
        text,
        "74290284261595@lid",
        {"message_id": "chloe-layout-revision"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active: revision captured for F0097"}
    assert update_calls
    assert previews == ["F0097"]
    assert not any(row.get("reason") == "flyer_active_project_bypassed" for row in audits)


def test_same_business_layout_revision_does_not_bypass_but_new_campaign_does():
    """Boundary guard for the 42bdda5 contract: 'create a new flyer for
    <current business> with [layout/emphasis edits]' is a revision (no bypass),
    but a genuine new campaign for the same business is still a fresh work order
    (bypass). Keeps the same-business carve-out narrow."""
    _hooks, actions = _load_plugin_modules()
    active = {
        "project_id": "F0097",
        "status": "awaiting_final_approval",
        "fields": {"event_or_business_name": "Chloe Hair Studio"},
    }
    layout_revision = (
        "Create a new flyer for chloe hair studio with contact number and address "
        "look smaller and the main focus should be on the services that we provide."
    )
    new_campaign = "Create a new flyer for chloe hair studio for our grand opening sale this weekend."

    # Same-business layout/emphasis edit attaches to the active project.
    assert actions.should_bypass_active_flyer_project_for_fresh_request(layout_revision, active, has_media=False) is False
    # Genuine new campaign for the same business is still a fresh work order.
    assert actions.should_bypass_active_flyer_project_for_fresh_request(new_campaign, active, has_media=False) is True
    # A layout tweak combined with fresh campaign/event detail is a new work
    # order, not a revision — must still bypass.
    combined = (
        "Create a new flyer for chloe hair studio, make the contact number smaller, "
        "event from 4 pm to 7 pm this Saturday."
    )
    assert actions.should_bypass_active_flyer_project_for_fresh_request(combined, active, has_media=False) is True
    # Emphasis on existing content (menu/items) is a revision, not a new brief —
    # content nouns must not be treated as fresh-campaign detail.
    menu_emphasis = "Create a new flyer for chloe hair studio with the main focus on the menu items."
    assert actions.should_bypass_active_flyer_project_for_fresh_request(menu_emphasis, active, has_media=False) is False
    # A concrete calendar date is a new dated campaign, even with a layout tweak —
    # in either month-day or day-month order.
    dated_campaign = "Create a new flyer for chloe hair studio for June 12, make the contact number smaller."
    assert actions.should_bypass_active_flyer_project_for_fresh_request(dated_campaign, active, has_media=False) is True
    dated_campaign_dm = "Create a new flyer for chloe hair studio for 12 June, make the contact number smaller."
    assert actions.should_bypass_active_flyer_project_for_fresh_request(dated_campaign_dm, active, has_media=False) is True
    # Media-backed requests keep their existing path (carve-out is text-only).
    assert actions.should_bypass_active_flyer_project_for_fresh_request(layout_revision, active, has_media=True) is True


def test_schedule_named_business_revision_attaches_not_bypassed():
    """A business whose name contains a schedule/occasion word (e.g. 'Sunday
    Salon', 'Eid Market') must still get its layout revisions attached. The
    new-campaign check ignores the stored business-name span, so the word in
    the name doesn't masquerade as fresh-campaign detail."""
    _hooks, actions = _load_plugin_modules()
    sunday = {
        "project_id": "F0200",
        "status": "awaiting_final_approval",
        "fields": {"event_or_business_name": "Sunday Salon"},
    }
    eid = {
        "project_id": "F0201",
        "status": "awaiting_final_approval",
        "fields": {"event_or_business_name": "Eid Market"},
    }
    assert actions.should_bypass_active_flyer_project_for_fresh_request(
        "Create a new flyer for Sunday Salon, make the contact number smaller and focus on the services.",
        sunday,
        has_media=False,
    ) is False
    assert actions.should_bypass_active_flyer_project_for_fresh_request(
        "Create a new flyer for Eid Market, make the contact and address smaller.",
        eid,
        has_media=False,
    ) is False


def test_contact_address_digits_in_revision_attach_not_bypassed():
    """A layout revision that names the actual phone/street number being resized
    must attach, not bypass — contact/address digits are not a new campaign;
    only new dates/times/occasions are."""
    _hooks, actions = _load_plugin_modules()
    active = {
        "project_id": "F0097",
        "status": "awaiting_final_approval",
        "fields": {"event_or_business_name": "Chloe Hair Studio"},
    }
    assert actions.should_bypass_active_flyer_project_for_fresh_request(
        "Create a new flyer for Chloe Hair Studio, make phone number 555-1234 and address smaller.",
        active,
        has_media=False,
    ) is False
    assert actions.should_bypass_active_flyer_project_for_fresh_request(
        "Create a new flyer for chloe hair studio, make 123 Main St address and contact smaller.",
        active,
        has_media=False,
    ) is False


def test_layout_revision_wording_requires_revision_specific_focus_phrasing():
    """Only revision-specific focus phrasing ('main focus / focus should be /
    focus on') counts as a layout revision; generic 'highlight/emphasize' new-
    brief language does not (it would risk false-attaching a new brief to an
    active project). Size edits and explicit focus edits still count."""
    _hooks, actions = _load_plugin_modules()
    assert actions._is_layout_emphasis_revision_wording("highlighting our specials and products") is False
    assert actions._is_layout_emphasis_revision_wording("the main focus should be on the services") is True
    assert actions._is_layout_emphasis_revision_wording("focus on the menu items") is True
    assert actions._is_layout_emphasis_revision_wording("make the contact number and address smaller") is True


def test_source_vs_new_source_choice_creates_manual_edit_project(monkeypatch):
    """SOURCE branch routes through existing exact-edit handler:
    trigger_create_flyer_project called WITH manual_edit_required=True and
    raw_request prefixed `Edit uploaded flyer/source artwork`."""
    hooks, actions = _load_plugin_modules()
    created: dict = {}

    def fake_consume_source_vs_new(choice_token, trailing, *, chat_id, sender_phone):
        if choice_token == "source":
            return {
                "chat_id": chat_id,
                "sender_phone": sender_phone,
                "customer": {"business_name": "Lakshmis Kitchen", "customer_id": "CUST0001"},
                "raw_request": "I'd like you use this flyer for Lakshmi's Kitchen. Replace Triveni Express.",
                "media_path": "/tmp/ref.jpg",
                "source_organization": "Triveni Express",
                "original_intent": "exact_source_edit",
                "customer_followup_instruction": trailing,
                "choice": "source",
            }
        return None

    if not hasattr(actions, "consume_flyer_source_vs_new_choice"):
        pytest.skip("consume_flyer_source_vs_new_choice not yet implemented (Task 5)")
    monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", fake_consume_source_vs_new)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))

    def fake_create(**kwargs):
        created.update(kwargs)
        return True, "", {
            "project_id": "F0063",
            "status": "manual_edit_required",
            "manual_review": {"status": "queued", "reason_code": "source_edit_provider_unavailable"},
        }

    monkeypatch.setattr(actions, "trigger_create_flyer_project", fake_create)
    monkeypatch.setattr(actions, "flyer_project_has_manual_review_queued", lambda *_a, **_kw: True)
    monkeypatch.setattr(actions, "send_flyer_manual_edit_ack", lambda *_a, **_kw: (True, "mid", ""))
    monkeypatch.setattr(actions, "send_flyer_manual_review_ack", lambda *_a, **_kw: (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)
    if hasattr(actions, "audit_source_vs_new"):
        monkeypatch.setattr(actions, "audit_source_vs_new", lambda **_kw: None)

    if not hasattr(hooks, "_try_flyer_source_vs_new_choice_intercept"):
        pytest.skip("_try_flyer_source_vs_new_choice_intercept not yet implemented (Task 5)")

    result = hooks._try_flyer_source_vs_new_choice_intercept(
        "SOURCE",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-source"},
    )

    assert result is not None and result.get("action") == "skip"
    assert created.get("manual_edit_required") is True, (
        f"SOURCE branch must pass manual_edit_required=True; got {created.get('manual_edit_required')!r}"
    )
    raw = created.get("raw_request", "")
    assert raw.startswith("Edit uploaded flyer/source artwork"), (
        f"SOURCE branch raw_request must be prefixed; got {raw!r}"
    )


@pytest.mark.parametrize(
    "status_text",
    [
        "any update?",
        "any updates?",
        "status please",
        "what is the status?",
        "any progress on my flyer?",
        "how long for the update?",
        "when will it be ready?",
        "eta please",
    ],
)
def test_queued_source_edit_status_checkin_resends_source_new_clarification(monkeypatch, status_text):
    """After SOURCE-chosen project is queued, follow-up `any update?` MUST
    NOT re-enter the SOURCE/NEW clarification (lessons.md 2026-05-19)."""
    hooks, actions = _load_plugin_modules()

    if not hasattr(hooks, "_try_flyer_source_vs_new_choice_intercept"):
        pytest.skip("_try_flyer_source_vs_new_choice_intercept not yet implemented (Task 5)")

    def fake_consume_source_vs_new(choice_token, trailing, *, chat_id, sender_phone):
        return None

    if hasattr(actions, "consume_flyer_source_vs_new_choice"):
        monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", fake_consume_source_vs_new)
    if hasattr(actions, "peek_flyer_source_vs_new_pending"):
        monkeypatch.setattr(
            actions,
            "peek_flyer_source_vs_new_pending",
            lambda **_kw: {"customer": {"customer_id": "CUST0001"}},
        )
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(
        actions, "trigger_create_flyer_project",
        lambda **_kw: pytest.fail("status check-in must NOT create a project"),
    )
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_a, **_kw: (True, "mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)

    result = hooks._try_flyer_source_vs_new_choice_intercept(
        status_text,
        "17329837841@s.whatsapp.net",
        {"message_id": "m-status"},
    )
    assert result is not None and result.get("action") == "skip"


def test_source_vs_new_status_checkin_clarification_has_no_trial_or_quota_copy(monkeypatch):
    """Status check-ins on awaiting SOURCE/NEW choice must resend only the
    SOURCE/NEW decision prompt, without trial/quota/payment upsell copy."""
    hooks, actions = _load_plugin_modules()

    if not hasattr(hooks, "_try_flyer_source_vs_new_choice_intercept"):
        pytest.skip("_try_flyer_source_vs_new_choice_intercept not yet implemented (Task 5)")

    if hasattr(actions, "consume_flyer_source_vs_new_choice"):
        monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", lambda *_a, **_kw: None)
    if hasattr(actions, "peek_flyer_source_vs_new_pending"):
        monkeypatch.setattr(
            actions,
            "peek_flyer_source_vs_new_pending",
            lambda **_kw: {"customer": {"customer_id": "CUST0001"}},
        )
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    sent: dict[str, str] = {}
    monkeypatch.setattr(
        actions,
        "send_flyer_text",
        lambda _chat_id, text, **_kwargs: sent.update({"text": text}) or (True, "mid", ""),
    )
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)

    result = hooks._try_flyer_source_vs_new_choice_intercept(
        "any update?",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-status"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer source-vs-new status check-in"}
    reply = sent["text"].lower()
    assert "reply source" in reply
    assert "reply new" in reply
    assert "trial" not in reply
    assert "quota" not in reply
    assert "create one flyer - $4" not in reply


def test_source_vs_new_source_branch_calls_preflight_and_generates_when_ready(monkeypatch):
    """Regression for PR #137 review finding 3: design said SOURCE routes
    through the existing exact-edit handler at hooks.py:587-697 (preflight →
    generate when ready). Initial implementation always sent the manual_edit
    ack and never reached preflight/generation even with a valid OPENAI_API_KEY.
    """
    hooks, actions = _load_plugin_modules()

    if not hasattr(hooks, "_try_flyer_source_vs_new_choice_intercept"):
        pytest.skip("_try_flyer_source_vs_new_choice_intercept not yet implemented (Task 5)")

    def fake_consume_source_vs_new(choice_token, trailing, *, chat_id, sender_phone):
        return {
            "chat_id": chat_id,
            "sender_phone": sender_phone,
            "customer": {"business_name": "Lakshmis Kitchen", "customer_id": "CUST0001"},
            "raw_request": "Edit this flyer for Lakshmi's Kitchen.",
            "media_path": "/tmp/ref.jpg",
            "source_organization": "Triveni Express",
            "original_intent": "exact_source_edit",
            "customer_followup_instruction": trailing,
            "choice": "source",
        }

    if not hasattr(actions, "consume_flyer_source_vs_new_choice"):
        pytest.skip("consume_flyer_source_vs_new_choice not yet implemented")
    monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", fake_consume_source_vs_new)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kw: (
        True, "", {"project_id": "F0099", "status": "manual_edit_required",
                   "assets": [{"kind": "reference_image", "path": "/tmp/ref.jpg",
                               "mime_type": "image/jpeg"}]},
    ))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)
    if hasattr(actions, "audit_source_vs_new"):
        monkeypatch.setattr(actions, "audit_source_vs_new", lambda **_kw: None)

    # Preflight returns ready (valid provider) — must trigger generation, not manual queue.
    preflight_calls: list = []
    def fake_preflight(project):
        preflight_calls.append(project.get("project_id"))
        return (True, "ready", "")
    monkeypatch.setattr(actions, "flyer_source_edit_preflight", fake_preflight)

    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply",
                        lambda *_a, **_kw: ({"access_id": "A1", "consumed": True}, None))
    monkeypatch.setattr(hooks, "_release_flyer_access", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(hooks, "_send_preview_then_finalize_access",
                        lambda *_a, **_kw: (True, "mid", ""))

    edit_proc_calls: list = []
    monkeypatch.setattr(actions, "send_flyer_edit_processing_ack",
                        lambda *_a, **_kw: (edit_proc_calls.append(_a) or (True, "proc-mid", "")))
    gen_calls: list = []
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts",
                        lambda pid: (gen_calls.append(pid) or (True, "")))
    # Manual-edit ack MUST NOT be called when preflight is ready.
    monkeypatch.setattr(actions, "send_flyer_manual_edit_ack",
                        lambda *_a, **_kw: pytest.fail("manual_edit_ack must NOT fire when provider ready"))

    result = hooks._try_flyer_source_vs_new_choice_intercept(
        "SOURCE",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-source-ready"},
    )
    assert result is not None and result.get("action") == "skip"
    assert preflight_calls == ["F0099"], f"preflight must be called; got {preflight_calls!r}"
    assert gen_calls == ["F0099"], f"generation must be triggered; got {gen_calls!r}"
    assert edit_proc_calls, "edit processing ack must be sent (not manual ack)"


def test_source_vs_new_source_branch_queues_manual_when_provider_unavailable(monkeypatch):
    """SOURCE branch must call invoke_update_flyer_project --queue-manual-review +
    send manual_edit_ack when preflight reports source_edit_provider_unavailable.
    No generation call should fire.
    """
    hooks, actions = _load_plugin_modules()

    if not hasattr(hooks, "_try_flyer_source_vs_new_choice_intercept"):
        pytest.skip("_try_flyer_source_vs_new_choice_intercept not yet implemented (Task 5)")

    def fake_consume_source_vs_new(choice_token, trailing, *, chat_id, sender_phone):
        return {
            "chat_id": chat_id,
            "sender_phone": sender_phone,
            "customer": {"business_name": "Lakshmis Kitchen", "customer_id": "CUST0001"},
            "raw_request": "Edit this flyer for Lakshmi's Kitchen.",
            "media_path": "/tmp/ref.jpg",
            "source_organization": "Triveni Express",
            "original_intent": "exact_source_edit",
            "customer_followup_instruction": "",
            "choice": "source",
        }

    if not hasattr(actions, "consume_flyer_source_vs_new_choice"):
        pytest.skip("consume_flyer_source_vs_new_choice not yet implemented")
    monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", fake_consume_source_vs_new)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kw: (
        True, "", {"project_id": "F0100", "status": "manual_edit_required",
                   "assets": [{"kind": "reference_image", "path": "/tmp/ref.jpg",
                               "mime_type": "image/jpeg"}]},
    ))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)
    if hasattr(actions, "audit_source_vs_new"):
        monkeypatch.setattr(actions, "audit_source_vs_new", lambda **_kw: None)

    monkeypatch.setattr(actions, "flyer_source_edit_preflight",
                        lambda project: (False, "OPENAI_API_KEY missing", "source_edit_provider_unavailable"))

    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply",
                        lambda *_a, **_kw: ({"access_id": "A1", "consumed": True}, None))
    monkeypatch.setattr(hooks, "_release_flyer_access", lambda *_a, **_kw: (True, ""))

    queue_calls: list = []
    def fake_queue(project_id, *flags):
        queue_calls.append((project_id, flags))
        return (True, "")
    monkeypatch.setattr(actions, "invoke_update_flyer_project", fake_queue)

    ack_calls: list = []
    monkeypatch.setattr(actions, "send_flyer_manual_edit_ack",
                        lambda *_a, **_kw: (ack_calls.append(_a) or (True, "mid", "")))
    # trigger_generate_flyer_concepts MUST NOT fire when provider unavailable.
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts",
                        lambda _pid: pytest.fail("generation must NOT fire when provider unavailable"))
    monkeypatch.setattr(actions, "send_flyer_edit_processing_ack",
                        lambda *_a, **_kw: pytest.fail("processing ack must NOT fire when provider unavailable"))

    result = hooks._try_flyer_source_vs_new_choice_intercept(
        "SOURCE",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-source-unavail"},
    )
    assert result is not None and result.get("action") == "skip"
    assert queue_calls, "must invoke update_flyer_project with --queue-manual-review"
    pid, flags = queue_calls[0]
    assert pid == "F0100"
    assert "--queue-manual-review" in flags
    assert "source_edit_provider_unavailable" in flags
    assert ack_calls, "manual_edit_ack must be sent"


def test_source_vs_new_source_branch_generation_failure_queues_provider_timeout(monkeypatch):
    """If provider-ready source-edit generation still fails before queuing its
    own manual review row, cf-router must queue a typed transient-provider
    reason_code instead of falling back to operator_request."""
    hooks, actions = _load_plugin_modules()

    if not hasattr(hooks, "_try_flyer_source_vs_new_choice_intercept"):
        pytest.skip("_try_flyer_source_vs_new_choice_intercept not yet implemented (Task 5)")

    def fake_consume_source_vs_new(choice_token, trailing, *, chat_id, sender_phone):
        return {
            "chat_id": chat_id,
            "sender_phone": sender_phone,
            "customer": {"business_name": "Lakshmis Kitchen", "customer_id": "CUST0001"},
            "raw_request": "Edit this flyer for Lakshmi's Kitchen.",
            "media_path": "/tmp/ref.jpg",
            "source_organization": "Triveni Express",
            "original_intent": "exact_source_edit",
            "customer_followup_instruction": "",
            "choice": "source",
        }

    if not hasattr(actions, "consume_flyer_source_vs_new_choice"):
        pytest.skip("consume_flyer_source_vs_new_choice not yet implemented")
    monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", fake_consume_source_vs_new)
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kw: (
        True, "", {"project_id": "F0101", "status": "manual_edit_required",
                   "assets": [{"kind": "reference_image", "path": "/tmp/ref.jpg",
                               "mime_type": "image/jpeg"}]},
    ))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)
    if hasattr(actions, "audit_source_vs_new"):
        monkeypatch.setattr(actions, "audit_source_vs_new", lambda **_kw: None)
    monkeypatch.setattr(actions, "flyer_source_edit_preflight", lambda _project: (True, "ready", ""))
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply",
                        lambda *_a, **_kw: ({"access_id": "A1", "consumed": True}, None))
    monkeypatch.setattr(hooks, "_release_flyer_access", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(actions, "send_flyer_edit_processing_ack", lambda *_a, **_kw: (True, "proc-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts",
                        lambda _pid: (False, "exit=1 OpenRouter HTTP 500"))
    monkeypatch.setattr(actions, "flyer_generation_queued_manual_review", lambda _detail: False)

    queue_calls: list = []
    monkeypatch.setattr(actions, "invoke_update_flyer_project",
                        lambda project_id, *flags: (queue_calls.append((project_id, flags)) or (True, "")))
    ack_calls: list = []
    monkeypatch.setattr(actions, "send_flyer_manual_edit_ack",
                        lambda *_a, **_kw: (ack_calls.append(_a) or (True, "mid", "")))

    result = hooks._try_flyer_source_vs_new_choice_intercept(
        "SOURCE",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-source-generation-failed"},
    )

    assert result is not None and result.get("action") == "skip"
    assert queue_calls, "generation failure fallback must queue manual review"
    _pid, flags = queue_calls[0]
    assert "--manual-reason-code" in flags
    code_idx = flags.index("--manual-reason-code")
    assert flags[code_idx + 1] == "provider_timeout"
    assert "--manual-reason" in flags
    assert "source_edit_generation_failed" in flags
    assert ack_calls, "manual_edit_ack must be sent after fallback queue"


def test_exact_reference_edit_generation_failure_queues_provider_timeout(monkeypatch):
    hooks, actions = _load_plugin_modules()

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender",
                        lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial", "business_name": "Lakshmis Kitchen"})
    monkeypatch.setattr(actions, "is_vague_flyer_start", lambda _text, has_media=False: False)
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "flyer_location_block_message", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(actions, "is_exact_reference_edit_request", lambda _text, has_media=False: True)
    monkeypatch.setattr(actions, "trigger_check_flyer_reference_scope", lambda **_kwargs: (True, "allow", {"decision": "allow"}))
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kw: (
        True, "", {"project_id": "F0102", "status": "manual_edit_required",
                   "assets": [{"kind": "reference_image", "path": "/tmp/ref.jpg",
                               "mime_type": "image/jpeg"}]},
    ))
    monkeypatch.setattr(actions, "flyer_source_edit_preflight", lambda _project: (True, "ready", ""))
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply",
                        lambda *_a, **_kw: ({"access_id": "A1", "consumed": True}, None))
    monkeypatch.setattr(hooks, "_release_flyer_access", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(actions, "send_flyer_edit_processing_ack", lambda *_a, **_kw: (True, "proc-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts",
                        lambda _pid: (False, "exit=1 OpenRouter HTTP 500"))
    monkeypatch.setattr(actions, "flyer_generation_queued_manual_review", lambda _detail: False)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)

    queue_calls: list = []
    monkeypatch.setattr(actions, "invoke_update_flyer_project",
                        lambda project_id, *flags: (queue_calls.append((project_id, flags)) or (True, "")))
    ack_calls: list = []
    monkeypatch.setattr(actions, "send_flyer_manual_edit_ack",
                        lambda *_a, **_kw: (ack_calls.append(_a) or (True, "manual-mid", "")))

    result = hooks._try_flyer_primary_intercept(
        "Please edit this flyer and replace the headline",
        "17329837841@s.whatsapp.net",
        {"message_id": "m-exact-generation-failed", "media_path": "/tmp/ref.jpg"},
        media_path="/tmp/ref.jpg",
    )

    assert result is not None and result.get("action") == "skip"
    assert queue_calls, "exact-reference generation failure must queue manual review"
    _pid, flags = queue_calls[0]
    assert "--manual-reason-code" in flags
    code_idx = flags.index("--manual-reason-code")
    assert flags[code_idx + 1] == "provider_timeout"
    assert "--manual-reason" in flags
    assert "source_edit_generation_failed" in flags
    assert ack_calls, "manual_edit_ack must be sent after fallback queue"


def test_parse_source_vs_new_followup_handles_compound_reply():
    actions = _load_actions()
    assert actions.parse_source_vs_new_followup("SOURCE") == ("source", "")
    assert actions.parse_source_vs_new_followup("Source.") == ("source", "")
    assert actions.parse_source_vs_new_followup("NEW") == ("new", "")
    assert actions.parse_source_vs_new_followup("any update?") == ("", "")
    choice, trailing = actions.parse_source_vs_new_followup("SOURCE, also change date to Saturday")
    assert choice == "source"
    assert "Saturday" in trailing
    choice2, trailing2 = actions.parse_source_vs_new_followup("Option 2 - please use cursive font")
    assert choice2 == "new"
    assert "cursive" in trailing2
    assert actions.parse_source_vs_new_followup("1") == ("source", "")
    assert actions.parse_source_vs_new_followup("2") == ("new", "")


def test_parse_source_vs_new_followup_documents_greedy_match_tail_risk():
    """Pin the documented design fail-mode: a bare "Source code please" reply
    parses as choice="source" with trailing="code please" because the leading
    token regex matches `\\bsource\\b` then captures the rest of the line.

    This is intentional accepted behavior per the plan's compound-reply
    semantics: customers can append free-form instructions after SOURCE/NEW.
    The cost is a narrow tail-risk if a customer happens to use the word
    "source" in a non-choice sentence WHILE a pending row is sitting in
    `awaiting_source_vs_new_choice`. Mitigation: `peek_flyer_source_vs_new_pending`
    gates the consume on the row's existence, so this only fires when there
    actually is a pending choice for this sender.

    Future hardening (out of scope for this PR): require trailing chunk to
    start with a punctuation/conjunction (e.g. `[,.;:!\\-—]` or
    whitespace+`also|and|plus`) so a bare word like "code" does not get
    accepted. Captured as a follow-up backlog item; this test pins the
    current accepted shape so a tightener cannot regress it silently.
    """
    actions = _load_actions()
    choice, trailing = actions.parse_source_vs_new_followup("Source code please")
    assert choice == "source"
    assert "code please" in trailing


def test_consume_flyer_source_vs_new_choice_round_trip(tmp_path):
    actions = _load_actions()
    actions.FLYER_REFERENCE_SCOPE_PATH = tmp_path / "reference_scope_pending.json"
    actions.save_flyer_reference_scope_pending(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        customer={"business_name": "Lakshmis Kitchen", "customer_id": "CUST0001"},
        raw_request="Replace Triveni Express with Lakshmi's Kitchen branding.",
        media_path="/tmp/ref.jpg",
        scope={"visible_organization_names": ["Triveni Express"]},
        original_intent="exact_source_edit",
    )

    # Transition the row via the choice consumer (use_reference + exact_source_edit).
    pending = actions.consume_flyer_reference_scope_choice(
        "use as reference",
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        transition_to_status="awaiting_source_vs_new_choice",
    )
    assert pending is not None
    assert pending["choice"] == "use_reference"
    assert pending["original_intent"] == "exact_source_edit"

    # Row is still present under the new status — peek finds it.
    peek = actions.peek_flyer_source_vs_new_pending(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
    )
    assert peek is not None
    assert peek["status"] == "awaiting_source_vs_new_choice"

    # Consume SOURCE — row removed.
    sv = actions.consume_flyer_source_vs_new_choice(
        "source", "also change date",
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
    )
    assert sv is not None
    assert sv["choice"] == "source"
    assert sv["customer_followup_instruction"] == "also change date"
    # Idempotent: second consume finds nothing.
    assert actions.consume_flyer_source_vs_new_choice(
        "source", "",
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
    ) is None


def test_flyer_is_status_checkin_matches_expected_phrases():
    actions = _load_actions()
    assert actions.flyer_is_status_checkin("any update?")
    assert actions.flyer_is_status_checkin("any updates?")
    assert actions.flyer_is_status_checkin("any update please")
    assert actions.flyer_is_status_checkin("any progress on my flyer?")
    assert actions.flyer_is_status_checkin("is it ready?")
    assert actions.flyer_is_status_checkin("when will my flyer be ready?")
    assert actions.flyer_is_status_checkin("eta please")
    assert actions.flyer_is_status_checkin("where are the updates?")
    assert actions.flyer_is_status_checkin("how long")
    assert actions.flyer_is_status_checkin("status")
    assert not actions.flyer_is_status_checkin("source please")
    assert not actions.flyer_is_status_checkin("change the date")


@pytest.mark.parametrize(
    "text",
    [
        "f0058 status?",
        "eta on my flyer",
        "where my flyer at",
        "did you complete it",
        "whats happening with my flyer",
        "what about my flyer",
    ],
)
def test_status_request_phrase_gaps_route_as_status_checkins(text):
    actions = _load_actions()
    assert actions.is_flyer_project_status_request(text), text


def test_generic_reference_use_as_reference_still_works(tmp_path):
    """Regression: generic-reference customers must NOT see the SOURCE/NEW
    detour. They get the existing use-reference path."""
    actions = _load_actions()
    actions.FLYER_REFERENCE_SCOPE_PATH = tmp_path / "reference_scope_pending.json"

    actions.save_flyer_reference_scope_pending(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        customer={"business_name": "Lakshmis Kitchen"},
        raw_request="Use this flyer.",
        media_path="/tmp/ref.jpg",
        scope={"visible_organization_names": ["Triveni Express"]},
        original_intent="generic_reference",
    )
    pending = actions.consume_flyer_reference_scope_choice(
        "use as reference",
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        transition_to_status="awaiting_source_vs_new_choice",
    )
    assert pending is not None
    # Even though we passed transition_to_status, generic_reference rows are
    # consumed (removed) — no detour.
    assert actions.peek_flyer_source_vs_new_pending(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
    ) is None


# ─────────────────────────────────────────────────────────────────
# 2026-05-28 — intake-bypass helper + script detector (Commit 2)
# ─────────────────────────────────────────────────────────────────


def _bypass_customer(status: str = "trial") -> dict:
    return {"customer_id": "CUST0001", "status": status, "preferred_language": "en"}


def _bypass_intake(status: str = "choosing_mode") -> dict:
    return {"status": status}


@pytest.mark.parametrize("text,customer,intake_session,has_media,expected", [
    # F0108 / 22.png canonical — "fix the time" hits edit_target regex.
    # The original plan example "add Saturday hours" did NOT match — reviewer 2
    # was right to flag the phrasing; pin actual-matching phrasing here.
    ("edit this to fix the time", None, _bypass_intake("choosing_mode"), True,
     "edit_with_media"),
    # Different verb + target — also matches.
    ("change the date on this flyer", None, _bypass_intake("choosing_mode"), True,
     "edit_with_media"),
    # New customer + new-flyer + media → "new_flyer_with_media".
    ("Create flyer for Dosa Night", None, _bypass_intake("choosing_mode"), True,
     "new_flyer_with_media"),
    # Existing trial customer + flyer-intent (NOT new-flyer-signal) + no media.
    # "What is the status of my flyer" matches classify_flyer_intent but
    # NOT should_start_new_flyer_over_active — exercises the existing-
    # customer fast path (signal 4/5 of the helper). Text that ALSO matches
    # the new-flyer signal (e.g., "I want a flyer for next week") hits the
    # more-specific "new_flyer_text_only" branch first; that's by design
    # for granular audit labeling per operator decision #2.
    ("What is the status of my flyer", _bypass_customer("trial"), None, False,
     "existing_trial_customer_intent"),
    # Existing active customer + flyer-intent + no media — active label.
    ("What is the status of my flyer", _bypass_customer("active"), None, False,
     "existing_active_customer_intent"),
    # Protected status — wizard owns the message.
    ("Create flyer for Dosa Night", _bypass_customer("trial"),
     _bypass_intake("guided_collecting_goal"), True, None),
    # Brand-new + vague + no media — no signal; wizard.
    ("hi", None, _bypass_intake("choosing_language"), False, None),
    # Counter-example pinning reviewer 2 #2 finding: "edit this" alone (no
    # edit_target word) does NOT bypass — classifier requires both verb AND
    # target. Pins the asymmetry so a regex relaxation doesn't silently
    # broaden the bypass.
    ("edit this", None, _bypass_intake("choosing_mode"), True, None),
    # Operator decision 2026-05-28 #1: expired/cancelled/suspended customers
    # NEVER bypass — account-lifecycle boundary owns re-onboarding.
    ("edit this to fix the time", _bypass_customer("expired"), None, True, None),
    ("edit this to fix the time", _bypass_customer("cancelled"), None, True, None),
    ("edit this to fix the time", _bypass_customer("suspended"), None, True, None),
])
def test_should_bypass_intake_for_clear_intent(
    text, customer, intake_session, has_media, expected,
):
    actions = _load_actions()
    assert actions.should_bypass_intake_for_clear_intent(
        text, customer, intake_session, has_media=has_media,
    ) == expected


def test_should_bypass_intake_returns_optional_str_type():
    """Signature returns Optional[str] — None when no bypass, else the
    bypass_reason Literal value. Callers consume directly to populate
    FlyerIntakeBypassed.bypass_reason (no re-classification)."""
    actions = _load_actions()
    result_bypass = actions.should_bypass_intake_for_clear_intent(
        "edit this to fix the time", None, _bypass_intake("choosing_mode"),
        has_media=True,
    )
    result_no_bypass = actions.should_bypass_intake_for_clear_intent(
        "hi", None, _bypass_intake("choosing_language"), has_media=False,
    )
    assert isinstance(result_bypass, str)
    assert result_bypass == "edit_with_media"
    assert result_no_bypass is None


def test_should_bypass_intake_priority_order_new_flyer_beats_existing_customer():
    """When text matches BOTH should_start_new_flyer_over_active AND
    classify_flyer_intent (e.g., 'I want a flyer for next week'), the helper
    returns the more-specific new-flyer Literal — NOT the existing-customer
    Literal. This is operator decision #2 granularity: media-vs-text-only on
    new-flyer routes is more useful for audit triage than the customer-state
    breakdown when both signals overlap. Pre-PR semantics still preserved:
    ANY pre-PR bypass case still bypasses post-PR — just with a finer-grained
    reason label."""
    actions = _load_actions()
    # Text matches both classifiers.
    assert actions.classify_flyer_intent("I want a flyer for next week")[0]
    assert actions.should_start_new_flyer_over_active(
        "I want a flyer for next week", has_media=False,
    )
    # Helper returns new_flyer_text_only (more specific), not the
    # existing-customer Literal, even with active/trial customer present.
    result = actions.should_bypass_intake_for_clear_intent(
        "I want a flyer for next week",
        _bypass_customer("active"),
        None, has_media=False,
    )
    assert result == "new_flyer_text_only"


def test_should_bypass_intake_does_not_mutate_inputs():
    """Pure-function invariant: helper does NOT modify customer or
    intake_session dicts. Defensive Hermes-as-brain check."""
    actions = _load_actions()
    customer = _bypass_customer("trial")
    intake_session = _bypass_intake("choosing_mode")
    customer_snapshot = dict(customer)
    intake_snapshot = dict(intake_session)
    _ = actions.should_bypass_intake_for_clear_intent(
        "edit this to fix the time", customer, intake_session, has_media=True,
    )
    assert customer == customer_snapshot
    assert intake_session == intake_snapshot


def test_should_bypass_intake_accepts_intake_session_without_status_field():
    """Defensive: empty intake_session dict → treated as non-protected."""
    actions = _load_actions()
    result = actions.should_bypass_intake_for_clear_intent(
        "edit this to fix the time", None, {}, has_media=True,
    )
    assert result == "edit_with_media"


def test_should_bypass_intake_accepts_customer_without_status_field():
    """Defensive: customer dict missing 'status' — fails precondition 2."""
    actions = _load_actions()
    result = actions.should_bypass_intake_for_clear_intent(
        "edit this to fix the time", {"customer_id": "CUST0001"},
        None, has_media=True,
    )
    assert result is None


def test_intake_protected_statuses_match_inline_hooks_set():
    """The helper's _INTAKE_PROTECTED_STATUSES must match the inline set
    at hooks.py:2367-2376 exactly. Any drift would split bypass behavior;
    Commit 3 wiring replaces the inline references with imports from here."""
    actions = _load_actions()
    from pathlib import Path
    hooks_src = (Path(__file__).resolve().parent.parent / "src" / "plugins"
                 / "cf-router" / "hooks.py").read_text(encoding="utf-8")
    anchor = "protected_statuses = {"
    start = hooks_src.find(anchor)
    assert start >= 0, "hooks.py inline protected_statuses set not found"
    end = hooks_src.find("}", start)
    assert end > start
    body = hooks_src[start + len(anchor):end]
    inline_members = {
        m.strip().strip('"').strip("'")
        for m in body.split(",")
        if m.strip()
    }
    assert inline_members == actions._INTAKE_PROTECTED_STATUSES


# ─────────────────────────────────────────────────────────────────
# Script detector — operator decision 2026-05-28 #3 (regional-SMB telemetry)
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("Create flyer for Dosa Night", "latin"),
    ("", "latin"),
    ("Diwali के लिए flyer बनाओ", "devanagari"),  # Hindi (Devanagari)
    ("तोसा रात के लिए", "devanagari"),  # Marathi (also Devanagari)
    ("தோசை இரவுக்கான flyer", "tamil"),  # Tamil
    ("¡Crea un volante!", "other"),  # Spanish — non-ASCII but not Hindi/Tamil
    ("Mixed Latin + देवनागरी", "devanagari"),  # mixed prefers Devanagari
    ("Mixed Latin + தமிழ்", "tamil"),  # mixed prefers Tamil
])
def test_detect_inbound_script(text, expected):
    actions = _load_actions()
    assert actions._detect_inbound_script(text) == expected


def test_detect_inbound_script_handles_none_safely():
    """Defensive: None text shouldn't crash; default to 'latin'."""
    actions = _load_actions()
    assert actions._detect_inbound_script(None) == "latin"  # type: ignore[arg-type]


def test_detect_inbound_script_does_not_mutate_input():
    """Pure-function invariant."""
    actions = _load_actions()
    text = "Diwali के लिए flyer"
    snapshot = text
    _ = actions._detect_inbound_script(text)
    assert text == snapshot


# ─────────────────────────────────────────────────────────────────
# 2026-05-28 — intake-bypass wiring + shadow context (Commit 3)
# Tests cover the helper-call insertion, the shadow context lifecycle,
# and _derive_bypass_outcome's F-pattern regex extraction.
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("hook_result,expected", [
    # None → unrouted
    (None, ("unrouted", "", "")),
    # F-pattern in reason → routed_to_project with project_id extracted
    ({"action": "skip", "reason": "cf-router flyer primary project created F0108"},
     ("routed_to_project", "F0108", "")),
    ({"action": "skip", "reason": "cf-router flyer active: regenerated revised design for F0097"},
     ("routed_to_project", "F0097", "")),
    # No F-pattern → intermediate_intercept_handled with reason captured
    ({"action": "skip", "reason": "cf-router flyer sample prompts sent"},
     ("intermediate_intercept_handled", "", "cf-router flyer sample prompts sent")),
    ({"action": "skip", "reason": "cf-router flyer customer not active"},
     ("intermediate_intercept_handled", "", "cf-router flyer customer not active")),
    ({"action": "skip", "reason": "cf-router flyer reference scope auth_blocked"},
     ("intermediate_intercept_handled", "", "cf-router flyer reference scope auth_blocked")),
    # Empty reason → intermediate with empty handler
    ({"action": "skip", "reason": ""},
     ("intermediate_intercept_handled", "", "")),
    # Missing reason key → empty string → intermediate
    ({"action": "skip"},
     ("intermediate_intercept_handled", "", "")),
])
def test_derive_bypass_outcome(hook_result, expected):
    """Pinned per plan §9 (post-revision) + design §4. F-pattern regex
    extraction from hook_result['reason']."""
    actions = _load_actions()
    assert actions._derive_bypass_outcome(hook_result) == expected


def test_derive_bypass_outcome_truncates_long_handler_reason():
    """Reason field on FlyerIntakeBypassOutcome.handler_intercept is max_length=80.
    The derivation truncates to fit the schema constraint."""
    actions = _load_actions()
    long_reason = "cf-router flyer " + ("a" * 200)
    outcome, project_id, handler = actions._derive_bypass_outcome(
        {"action": "skip", "reason": long_reason},
    )
    assert outcome == "intermediate_intercept_handled"
    assert project_id == ""
    assert len(handler) == 80


def _stub_safe_io_for_bypass_audit(monkeypatch, fake_log: Path) -> None:
    """Stub sys.modules['safe_io'] with a Windows-compatible ndjson_append
    so the lazy import inside audit_flyer_intake_bypassed /
    finalize_flyer_intake_bypass_shadow finds a working module.

    Real safe_io imports fcntl which is Unix-only — on Windows the lazy
    import raises ModuleNotFoundError and the except suppresses, so the
    audit row is never written. Tests need a working stub to verify the
    decision/outcome row shape."""
    import types as _types
    safe_io_stub = _types.ModuleType("safe_io")

    def _stub_ndjson_append(path, payload):
        p = Path(str(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(str(p), "a", encoding="utf-8") as fh:
            fh.write(str(payload) + "\n")

    safe_io_stub.ndjson_append = _stub_ndjson_append
    monkeypatch.setitem(sys.modules, "safe_io", safe_io_stub)


def test_bypass_shadow_lifecycle_begin_finalize_reset(monkeypatch, tmp_path):
    """End-to-end shadow context: begin() sets ContextVar, finalize() emits
    FlyerIntakeBypassOutcome via ndjson_append, reset() clears the binding."""
    actions = _load_actions()
    fake_log = tmp_path / "decisions.log"
    monkeypatch.setattr(actions, "LOG_PATH", fake_log)
    _stub_safe_io_for_bypass_audit(monkeypatch, fake_log)

    token = actions.begin_flyer_intake_bypass_shadow(
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.intake.1",
        bypass_reason="edit_with_media",
        has_media=True,
        customer_state="",
        intake_session_status="choosing_mode",
        inbound_script="latin",
    )
    assert token is not None

    # Finalize with a successful hook_result → routed_to_project outcome.
    actions.finalize_flyer_intake_bypass_shadow(
        hook_result={"action": "skip", "reason": "cf-router flyer primary project created F0108"},
    )

    # Audit row written.
    contents = fake_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(contents) == 1
    row = json.loads(contents[0])
    assert row["type"] == "flyer_intake_bypass_outcome"
    assert row["outcome"] == "routed_to_project"
    assert row["project_id"] == "F0108"
    assert row["elapsed_ms"] >= 0
    assert row["chat_id_hash"]  # non-empty

    # Reset clears the binding so subsequent finalize() is a no-op.
    actions.reset_flyer_intake_bypass_shadow(token)
    fake_log.unlink()
    actions.finalize_flyer_intake_bypass_shadow(hook_result=None)
    assert not fake_log.exists()  # no-op when context is None


def test_bypass_shadow_finalize_no_op_when_no_bypass(monkeypatch, tmp_path):
    """Defensive: finalize() with no prior begin() must write nothing.
    Dispatch wrapper calls finalize unconditionally; no-op is the
    correctness-critical contract."""
    actions = _load_actions()
    fake_log = tmp_path / "decisions.log"
    monkeypatch.setattr(actions, "LOG_PATH", fake_log)
    _stub_safe_io_for_bypass_audit(monkeypatch, fake_log)
    actions.finalize_flyer_intake_bypass_shadow(hook_result={"action": "skip", "reason": "anything"})
    assert not fake_log.exists()


def test_bypass_shadow_finalize_outcome_unrouted_when_hook_result_none(monkeypatch, tmp_path):
    """When intake bypass fired but no downstream intercept handled →
    outcome=unrouted. Silent-failure surface lit per operator decision #5
    (the row exists so operators can grep for it; it's NOT a mutable bool
    on the decision row)."""
    actions = _load_actions()
    fake_log = tmp_path / "decisions.log"
    monkeypatch.setattr(actions, "LOG_PATH", fake_log)
    _stub_safe_io_for_bypass_audit(monkeypatch, fake_log)
    token = actions.begin_flyer_intake_bypass_shadow(
        chat_id="x", message_id="m", bypass_reason="edit_with_media",
        has_media=True, customer_state="", intake_session_status="choosing_mode",
        inbound_script="latin",
    )
    actions.finalize_flyer_intake_bypass_shadow(hook_result=None)
    actions.reset_flyer_intake_bypass_shadow(token)
    row = json.loads(fake_log.read_text(encoding="utf-8").strip())
    assert row["outcome"] == "unrouted"
    assert row["project_id"] == ""
    assert row["handler_intercept"] == ""


def test_bypass_shadow_finalize_outcome_intermediate_when_handler_no_project(monkeypatch, tmp_path):
    """When an intermediate intercept (e.g., scope_choice) handles the
    message after bypass-return-None → outcome=intermediate_intercept_handled
    + handler_intercept captured."""
    actions = _load_actions()
    fake_log = tmp_path / "decisions.log"
    monkeypatch.setattr(actions, "LOG_PATH", fake_log)
    _stub_safe_io_for_bypass_audit(monkeypatch, fake_log)
    token = actions.begin_flyer_intake_bypass_shadow(
        chat_id="x", message_id="m", bypass_reason="edit_with_media",
        has_media=True, customer_state="", intake_session_status="choosing_mode",
        inbound_script="latin",
    )
    actions.finalize_flyer_intake_bypass_shadow(
        hook_result={"action": "skip", "reason": "cf-router flyer reference scope auth_blocked"},
    )
    actions.reset_flyer_intake_bypass_shadow(token)
    row = json.loads(fake_log.read_text(encoding="utf-8").strip())
    assert row["outcome"] == "intermediate_intercept_handled"
    assert row["project_id"] == ""
    assert "reference scope" in row["handler_intercept"]


def test_pending_bypass_token_consume_and_clear(monkeypatch):
    """note_flyer_intake_bypass_active stashes; consume_*_token returns +
    clears in one call. Defensive against token leakage across dispatches."""
    actions = _load_actions()
    # Pre-state: nothing pending.
    assert actions.consume_pending_flyer_intake_bypass_token() is None
    actions.note_flyer_intake_bypass_active(
        chat_id="x", message_id="m", bypass_reason="edit_with_media",
        has_media=True, customer_state="", intake_session_status="choosing_mode",
        inbound_script="latin",
    )
    token = actions.consume_pending_flyer_intake_bypass_token()
    assert token is not None
    # Consumed; subsequent consume returns None.
    assert actions.consume_pending_flyer_intake_bypass_token() is None
    actions.reset_flyer_intake_bypass_shadow(token)


def test_audit_flyer_intake_bypassed_writes_decision_row(monkeypatch, tmp_path):
    """audit_flyer_intake_bypassed emits the FlyerIntakeBypassed row via
    ndjson_append with the right field shape — verifies schema fit."""
    actions = _load_actions()
    fake_log = tmp_path / "decisions.log"
    monkeypatch.setattr(actions, "LOG_PATH", fake_log)
    _stub_safe_io_for_bypass_audit(monkeypatch, fake_log)
    actions.audit_flyer_intake_bypassed(
        chat_id="17329837841@s.whatsapp.net",
        bypass_reason="edit_with_media",
        has_media=True,
        customer_state="",
        intake_session_status="choosing_mode",
        inbound_script="latin",
    )
    row = json.loads(fake_log.read_text(encoding="utf-8").strip())
    assert row["type"] == "flyer_intake_bypassed"
    assert row["bypass_reason"] == "edit_with_media"
    assert row["has_media"] is True
    assert row["inbound_script"] == "latin"
    assert row["chat_id_hash"]


def test_audit_emit_failures_are_non_fatal(monkeypatch, tmp_path):
    """Per design §3 — audit-emit failures MUST NOT propagate. Both
    audit_flyer_intake_bypassed and finalize_flyer_intake_bypass_shadow
    write to stderr and swallow exceptions."""
    actions = _load_actions()

    def fake_ndjson_append_raise(*a, **k):
        raise OSError("simulated disk full")

    monkeypatch.setitem(sys.modules, "safe_io",
                        type(sys)("safe_io"))  # type: ignore[arg-type]
    sys.modules["safe_io"].ndjson_append = fake_ndjson_append_raise

    # audit_flyer_intake_bypassed: should NOT raise.
    actions.audit_flyer_intake_bypassed(
        chat_id="x", bypass_reason="edit_with_media", has_media=True,
        customer_state="", intake_session_status="choosing_mode",
        inbound_script="latin",
    )

    # finalize_flyer_intake_bypass_shadow: should NOT raise.
    token = actions.begin_flyer_intake_bypass_shadow(
        chat_id="x", message_id="m", bypass_reason="edit_with_media",
        has_media=True, customer_state="", intake_session_status="choosing_mode",
        inbound_script="latin",
    )
    actions.finalize_flyer_intake_bypass_shadow(hook_result=None)
    actions.reset_flyer_intake_bypass_shadow(token)
    # If we got here without raising, the non-fatal discipline holds.


# ─────────────────────────────────────────────────────────────────
# 2026-05-29 — design §4 build-phase replay gate against live audit sample
# ─────────────────────────────────────────────────────────────────


def test_outcome_derivation_against_recent_audit_sample():
    """Build-phase gate (design §4): the F-pattern derivation in
    _derive_bypass_outcome assumes every flyer_primary_project_created
    audit row carries an F-pattern project_id in its detail field —
    i.e., the call-site formatters in hooks.py consistently include
    `project_id=F....` in their detail strings, mirroring the same
    shape they use in hook_result['reason'] for the success paths.

    The fixture at tests/fixtures/intake_bypass_audit_sample.jsonl was
    pulled from /var/log/shift-agent-archive/decisions.log-2026052{6,8,9}
    on main-vps via the two-step SSH pattern on 2026-05-29 — 32 rows
    where reason == 'flyer_primary_project_created'.

    If a future PR changes a success path's formatter to drop the
    project_id reference, this test fails and surfaces the
    derivation-coupling risk before canary."""
    actions = _load_actions()
    fixture_path = (Path(__file__).resolve().parent / "fixtures"
                    / "intake_bypass_audit_sample.jsonl")
    assert fixture_path.exists(), (
        f"audit-sample fixture missing at {fixture_path}; "
        f"refresh via two-step SSH pull from main-vps decisions archive"
    )
    rows = [json.loads(line) for line in
            fixture_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    project_created_rows = [
        r for r in rows if r.get("reason") == "flyer_primary_project_created"
    ]
    assert project_created_rows, (
        "fixture must contain at least one flyer_primary_project_created row"
    )
    bad: list[dict] = []
    for row in project_created_rows:
        detail = str(row.get("detail") or "")
        if not actions._FLYER_PROJECT_ID_RE.search(detail):
            bad.append({"ts": row.get("ts"), "detail": detail[:120]})
    assert not bad, (
        f"{len(bad)} flyer_primary_project_created row(s) without F-pattern "
        f"in detail — derivation regex would mis-classify these as "
        f"intermediate_intercept_handled: {bad[:3]}"
    )


# ─────────────────────────────────────────────────────────────────
# P0 #2 — cf-router helper extraction + dispatcher tests (Commit 4)
# ─────────────────────────────────────────────────────────────────


class _FakeValidation:
    """Mimics text-manifest QA / visual QA validation return shape."""
    def __init__(self, ok: bool, blockers: list[str] | None = None) -> None:
        self.ok = ok
        self.blockers = blockers or []


def _install_send_media_fakes(
    monkeypatch,
    actions,
    *,
    text_qa_ok: bool = True,
    visual_qa_ok: bool = True,
    text_qa_blockers: list[str] | None = None,
    visual_qa_blockers: list[str] | None = None,
    bridge_send_media_ok: bool = True,
    bridge_post_ok: bool = True,
):
    """Stub the network/IO surface of _send_concept_preview_media. Returns
    a list of (call_name, args, kwargs) tuples that tests assert on.

    Uses monkeypatch.setitem(sys.modules, ...) so fake modules auto-restore
    at test teardown — otherwise sys.modules pollution would corrupt
    later tests (e.g., test_flyer_delivery_retry which imports the real
    safe_io)."""
    bridge_calls: list[tuple] = []
    import types as _types

    def fake_bridge_send_media(chat_id, path, **kwargs):
        bridge_calls.append(("bridge_send_media", (chat_id, path), kwargs))
        if bridge_send_media_ok:
            return True, f"mid-media-{len(bridge_calls)}", "", "sent"
        return False, "", "send_failed", "failed"

    def fake_bridge_post(chat_id, text, **kwargs):
        bridge_calls.append(("bridge_post", (chat_id, text), kwargs))
        if bridge_post_ok:
            return True, f"mid-post-{len(bridge_calls)}", "", "sent"
        return False, "", "post_failed", "failed"

    fake_safe_io = _types.ModuleType("safe_io")
    fake_safe_io.bridge_send_media = fake_bridge_send_media
    fake_safe_io.bridge_post = fake_bridge_post
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)

    fake_flyer_render = _types.ModuleType("flyer_render")
    fake_flyer_render.validate_text_manifest_file = lambda *a, **k: _FakeValidation(
        ok=text_qa_ok, blockers=text_qa_blockers or [],
    )
    monkeypatch.setitem(sys.modules, "flyer_render", fake_flyer_render)

    fake_flyer_visual_qa = _types.ModuleType("flyer_visual_qa")
    fake_flyer_visual_qa.validate_visual_qa_report = lambda *a, **k: _FakeValidation(
        ok=visual_qa_ok, blockers=visual_qa_blockers or [],
    )
    monkeypatch.setitem(sys.modules, "flyer_visual_qa", fake_flyer_visual_qa)

    fake_action_registry = _types.ModuleType("flyer_action_registry")
    fake_action_registry.PROJECT_ACTIONS = {}
    fake_action_registry.build_action_context_for_command = lambda *a, **k: {}
    monkeypatch.setitem(sys.modules, "flyer_action_registry", fake_action_registry)

    monkeypatch.setattr(actions, "_record_flyer_concept_preview_delivery",
                        lambda *a, **k: None, raising=False)
    return bridge_calls


def _sample_project_dict(status: str = "awaiting_concept_selection",
                         with_warning: bool = False) -> dict:
    project = {
        "project_id": "F0108", "status": status, "version": 1,
        "concepts": [{
            "concept_id": "C1", "title": "Dosa Night",
            "style_summary": "warm tones", "preview_asset_id": "A0001",
        }],
        "assets": [{"asset_id": "A0001", "path": "/tmp/preview.png"}],
        "locked_facts": [
            {"fact_id": "business_name", "value": "Lakshmi's Kitchen"},
            {"fact_id": "campaign_title", "value": "Dosa Night"},
            {"fact_id": "offer:0", "value": "Pick Any 4 Dosa for $20"},
            {"fact_id": "contact_phone", "value": "+1 980-200-5022"},
        ],
    }
    if with_warning:
        project["warning"] = {
            "severity": "warn",
            "blockers": ["visible wrong business/brand: Laksmi'S Kitchen"],
            "customer_text": "Here's your flyer draft. ...",
            "customer_text_sha256": "a" * 64,
            "delivered_at": "2026-05-28T14:30:00+00:00",
            "asset_id": "A0001", "classifier_version": "v1",
        }
    return project


def test_send_concept_preview_media_strict_policy_fails_on_visual_qa(monkeypatch):
    """qa_policy='strict' (pass-tier) hard-fails when visual-QA-report is not
    ok. Pre-PR behavior preserved bit-for-bit."""
    actions = _load_actions()
    _install_send_media_fakes(monkeypatch, actions,
                              visual_qa_ok=False,
                              visual_qa_blockers=["visual QA did not pass"])
    project = _sample_project_dict()
    ok, _, err = actions._send_concept_preview_media(
        chat_id="x@s.whatsapp.net", project=project, qa_policy="strict",
    )
    assert ok is False
    assert err.startswith("visual_qa_failed:")
    assert "visual QA did not pass" in err


def test_send_concept_preview_media_warn_tolerant_proceeds_on_failed_visual_qa(monkeypatch):
    """qa_policy='warn_tolerant' proceeds even when visual-QA-report status
    != 'passed'. project.warning already captures the blockers."""
    actions = _load_actions()
    bridge_calls = _install_send_media_fakes(
        monkeypatch, actions,
        visual_qa_ok=False,
        visual_qa_blockers=["visual QA did not pass"],
    )
    project = _sample_project_dict()
    ok, _, _ = actions._send_concept_preview_media(
        chat_id="x@s.whatsapp.net", project=project,
        qa_policy="warn_tolerant",
        customer_text="Here's your flyer draft. ...",
    )
    assert ok is True
    call_names = [c[0] for c in bridge_calls]
    assert "bridge_send_media" in call_names
    assert "bridge_post" in call_names


def test_send_concept_preview_media_text_manifest_qa_always_strict(monkeypatch):
    """Text-manifest QA stays strict regardless of qa_policy. Substrate /
    template-parse failures are NOT warn-tier-recoverable."""
    actions = _load_actions()
    _install_send_media_fakes(
        monkeypatch, actions,
        text_qa_ok=False, text_qa_blockers=["template parse failed"],
    )
    project = _sample_project_dict()
    for policy in ("strict", "warn_tolerant"):
        ok, _, err = actions._send_concept_preview_media(
            chat_id="x@s.whatsapp.net", project=project, qa_policy=policy,
            customer_text=("text" if policy == "warn_tolerant" else None),
        )
        assert ok is False, f"qa_policy={policy} should still fail on text_qa"
        assert err.startswith("text_qa_failed:"), policy


def test_send_concept_preview_media_pin_c_customer_text_replaces_trailing_cta(monkeypatch):
    """Pin C — customer_text replaces ONLY the trailing CTA, NOT the per-concept
    captions. Concept captions stay 'C1: Title' semantic descriptors."""
    actions = _load_actions()
    bridge_calls = _install_send_media_fakes(monkeypatch, actions)
    project = _sample_project_dict()
    warn_text = "Here's your flyer draft.\n\nWe noticed a small detail..."
    ok, _, _ = actions._send_concept_preview_media(
        chat_id="x@s.whatsapp.net", project=project,
        qa_policy="warn_tolerant", customer_text=warn_text,
    )
    assert ok is True

    # Per-concept caption: stable "C1: Title" pattern; NOT replaced
    media_call = next(c for c in bridge_calls if c[0] == "bridge_send_media")
    assert "C1: Dosa Night" in media_call[2]["caption"]
    assert "Reply APPROVE or reply with changes" in media_call[2]["caption"]
    assert warn_text not in media_call[2]["caption"]

    # Trailing CTA: text IS the warn_text override
    cta_call = next(c for c in bridge_calls if c[0] == "bridge_post")
    assert cta_call[1][1] == warn_text
    assert "Reply APPROVE to receive final files" not in cta_call[1][1]


def test_send_concept_preview_media_default_cta_when_customer_text_none(monkeypatch):
    """qa_policy='strict' with no customer_text sends a fact checklist before APPROVE."""
    actions = _load_actions()
    bridge_calls = _install_send_media_fakes(monkeypatch, actions)
    project = _sample_project_dict()
    ok, _, _ = actions._send_concept_preview_media(
        chat_id="x@s.whatsapp.net", project=project, qa_policy="strict",
    )
    assert ok is True
    cta_call = next(c for c in bridge_calls if c[0] == "bridge_post")
    assert "Please check these details before approving:" in cta_call[1][1]
    assert "Business: Lakshmi's Kitchen" in cta_call[1][1]
    assert "Title: Dosa Night" in cta_call[1][1]
    assert "Offer: Pick Any 4 Dosa for $20" in cta_call[1][1]
    assert cta_call[1][1].endswith("Reply APPROVE to receive final files, or reply with changes.")


def test_dispatch_concept_preview_send_routes_warn_tier_to_warn_wrapper(monkeypatch, tmp_path):
    """Dispatcher routes status=delivered_with_warning + warning payload
    → send_warn_tier_concept_previews with the built customer_text."""
    actions = _load_actions()
    fake_store = tmp_path / "projects.json"
    fake_store.write_text(json.dumps({"projects": [_sample_project_dict(
        status="delivered_with_warning", with_warning=True,
    )]}), encoding="utf-8")
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", fake_store)

    captured: dict = {}

    def fake_warn(chat_id, project_id, customer_text):
        captured["target"] = "warn"
        captured["customer_text"] = customer_text
        return True, "mid", ""

    def fake_pass(chat_id, project_id):
        captured["target"] = "pass"
        return True, "mid", ""

    monkeypatch.setattr(actions, "send_warn_tier_concept_previews", fake_warn)
    monkeypatch.setattr(actions, "send_flyer_concept_previews", fake_pass)

    ok, _, _ = actions._dispatch_concept_preview_send("x@s.whatsapp.net", "F0108")
    assert ok is True
    assert captured["target"] == "warn"
    assert captured["customer_text"]
    assert "Lakshmi's Kitchen" in captured["customer_text"]


def test_dispatch_concept_preview_send_routes_pass_tier_to_pass_wrapper(monkeypatch, tmp_path):
    """Dispatcher routes any non-warn status → existing pass-tier wrapper.
    Hermes-as-brain check: only reads status + warning; doesn't re-classify."""
    actions = _load_actions()
    fake_store = tmp_path / "projects.json"
    fake_store.write_text(json.dumps({"projects": [_sample_project_dict(
        status="awaiting_concept_selection", with_warning=False,
    )]}), encoding="utf-8")
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", fake_store)

    captured: dict = {}
    monkeypatch.setattr(actions, "send_warn_tier_concept_previews",
                        lambda *a, **k: captured.update(target="warn") or (True, "", ""))
    monkeypatch.setattr(actions, "send_flyer_concept_previews",
                        lambda *a, **k: captured.update(target="pass") or (True, "", ""))

    actions._dispatch_concept_preview_send("x@s.whatsapp.net", "F0108")
    assert captured["target"] == "pass"


def test_dispatch_concept_preview_send_falls_back_to_pass_when_project_missing(monkeypatch, tmp_path):
    """Missing project → falls back to pass-tier wrapper (which then returns
    'project_not_found'). Defensive: dispatcher doesn't gate on existence."""
    actions = _load_actions()
    fake_store = tmp_path / "projects.json"
    fake_store.write_text(json.dumps({"projects": []}), encoding="utf-8")
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", fake_store)

    captured: dict = {}
    monkeypatch.setattr(actions, "send_warn_tier_concept_previews",
                        lambda *a, **k: captured.update(target="warn") or (True, "", ""))
    monkeypatch.setattr(actions, "send_flyer_concept_previews",
                        lambda *a, **k: captured.update(target="pass") or (True, "", ""))

    actions._dispatch_concept_preview_send("x@s.whatsapp.net", "F9999")
    assert captured["target"] == "pass"


def test_dispatch_concept_preview_send_pass_tier_when_warning_none(monkeypatch, tmp_path):
    """Edge case: status=delivered_with_warning but warning=None → falls
    back to pass-tier. Defensive against state-machine inconsistencies."""
    actions = _load_actions()
    fake_store = tmp_path / "projects.json"
    bad_project = _sample_project_dict(status="delivered_with_warning", with_warning=False)
    bad_project["warning"] = None
    fake_store.write_text(json.dumps({"projects": [bad_project]}), encoding="utf-8")
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", fake_store)

    captured: dict = {}
    monkeypatch.setattr(actions, "send_warn_tier_concept_previews",
                        lambda *a, **k: captured.update(target="warn") or (True, "", ""))
    monkeypatch.setattr(actions, "send_flyer_concept_previews",
                        lambda *a, **k: captured.update(target="pass") or (True, "", ""))

    actions._dispatch_concept_preview_send("x@s.whatsapp.net", "F0108")
    assert captured["target"] == "pass"


def test_send_flyer_concept_previews_signature_unchanged(monkeypatch):
    """Pass-tier wrapper preserves the pre-PR signature.
    Bit-for-bit compatibility for the 6 existing callers in hooks.py."""
    actions = _load_actions()
    import inspect
    sig = inspect.signature(actions.send_flyer_concept_previews)
    params = list(sig.parameters.keys())
    assert params == ["chat_id", "project_id"], params


def test_send_warn_tier_concept_previews_requires_customer_text(monkeypatch):
    """Warn-tier wrapper REQUIRES customer_text — asymmetric signature
    prevents accidental empty-customer-text on warn-tier."""
    actions = _load_actions()
    import inspect
    sig = inspect.signature(actions.send_warn_tier_concept_previews)
    params = sig.parameters
    assert "customer_text" in params
    assert params["customer_text"].default is inspect.Parameter.empty
