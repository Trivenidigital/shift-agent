"""Pure routing heuristics for Flyer Studio cf-router behavior."""
from __future__ import annotations

import importlib.machinery
import importlib.util
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


def test_sample_prompt_preference_text_is_account_command():
    actions = _load_actions()

    assert actions.is_flyer_account_command("don't show sample prompts")
    assert actions.is_flyer_account_command("show sample prompts again")
    assert actions.is_flyer_account_command("[shift-agent-sender v=1 role=customer]\nstop showing examples")
    assert actions.is_flyer_account_command("no examples")
    assert actions.is_flyer_starter_prompt_preference_command("don't show sample prompts")
    assert actions.is_flyer_starter_prompt_preference_command("show sample prompts again")
    assert not actions.is_flyer_starter_prompt_preference_command("status")


def test_vague_flyer_start_for_active_customer_sends_starter_brief(monkeypatch):
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
    monkeypatch.setattr(actions, "trigger_create_flyer_project", lambda **_kwargs: created.update(called=True) or (True, "", {}))
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text: sent.update({"chat_id": chat_id, "text": text}) or (True, "starter-mid", ""))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks.pre_gateway_dispatch(SimpleNamespace(
        text="Create flyer",
        chat_id="17329837841@s.whatsapp.net",
        message_id="m1",
    ))

    assert result == {"action": "skip", "reason": "cf-router flyer starter brief sent"}
    assert created["called"] is False
    assert sent["chat_id"] == "17329837841@s.whatsapp.net"
    assert "Here is a starter flyer request" in sent["text"]
    assert "Grow Your Business with Modern Marketing" in sent["text"]


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
        monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))

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
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
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
    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create project")))
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
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
    monkeypatch.setattr(hooks, "_try_flyer_primary_intercept", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create project")))
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: customer)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
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
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda _chat_id, _project_id: (True, "processing-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda _project_id: (True, "generated"))
    monkeypatch.setattr(actions, "send_flyer_concept_previews", lambda _chat_id, _project_id: (True, "preview-mid", ""))
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text: (False, "", "bridge down"))
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text: (False, "", "bridge down"))
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
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
    monkeypatch.setattr(actions, "send_flyer_processing_ack", lambda _chat_id, _project_id: (True, "processing-mid", ""))
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda _project_id: (True, "generated"))

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
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args: (_ for _ in ()).throw(AssertionError("ready service project must not repeat missing-info prompt")))
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
    monkeypatch.setattr(
        actions,
        "invoke_update_flyer_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("status check must not be parsed as an edit")),
    )
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text: sent.append((chat_id, text)) or (True, "status-mid", ""))
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


def test_flyer_project_status_request_classifier_keeps_edits_separate():
    actions = _load_actions()

    for text in [
        "any update",
        "Any updates?",
        "what's the status",
        "is the flyer ready",
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text: (True, "mid-1", ""))
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
    def fake_send_ready(_chat_id, text):
        sent["message"] = text
        return True, "mid-1", ""

    monkeypatch.setattr(actions, "send_flyer_text", fake_send_ready)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)

    result = hooks._try_flyer_campaign_cta_intercept(
        "Start Free Trial",
        "74290284261595@lid",
        {"message_id": "retry-cta"},
    )

    assert result == {"action": "skip", "reason": "cf-router flyer active customer ready"}
    assert "already set up for Chloe hair studio" in sent["message"]
    assert "Send your flyer request" in sent["message"]


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
    def fake_send_ready(_chat_id, text):
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda *_args: (_ for _ in ()).throw(AssertionError("flyer brief must not get ready-only reply")))

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
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text: (sent.update({"chat_id": chat_id, "text": text}), (True, "out-mid", ""))[1])
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)

    result = hooks._try_flyer_active_project_intercept(
        "any update?",
        "201975216009469@lid",
        {"message_id": "m-update"},
    )
    assert result is not None and result.get("action") == "skip"
    assert "F0058" in sent["text"], f"expected status reply about F0058, got: {sent['text']!r}"
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
    monkeypatch.setattr(actions, "send_flyer_text", lambda chat_id, text: (sent.update({"text": text}), (True, "out-mid", ""))[1])
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kw: None)

    result = hooks._try_flyer_active_project_intercept(
        "any update on F0028?",
        "201975216009469@lid",
        {"message_id": "m-explicit"},
    )
    assert result is not None
    assert "F0028" in sent["text"], f"expected reply about F0028 (explicit mention), got: {sent['text']!r}"
    assert "F0058" not in sent["text"]


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
