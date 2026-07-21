"""Regression tests for cf-router live catering lead routing.

These tests intentionally avoid safe_io/audit imports so they can run on
Windows too; the Linux-heavy cf-router suite covers the broader plugin paths.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"


def _load_plugin_modules():
    pkg_name = "cf_router_new_inquiry_regression"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]

    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_name] = pkg_mod

    def load(name: str):
        full = f"{pkg_name}.{name}"
        loader = importlib.machinery.SourceFileLoader(
            full, str(PLUGIN_DIR / f"{name}.py"),
        )
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
        return mod

    actions_mod = load("actions")
    hooks_mod = load("hooks")
    return hooks_mod, actions_mod


def test_strong_new_inquiry_after_customer_finalized_creates_new_lead(tmp_path):
    hooks_mod, actions_mod = _load_plugin_modules()
    state = tmp_path / "state"
    state.mkdir()
    actions_mod.LEADS_PATH = state / "catering-leads.json"
    actions_mod.PROPOSALS_PATH = state / "catering-proposals.json"
    actions_mod.MENU_PENDING_PATH = state / "catering-menu-pending.json"
    actions_mod.CONFIG_PATH = tmp_path / "config.yaml"
    actions_mod.ROSTER_PATH = tmp_path / "roster.json"
    actions_mod.LOG_PATH = tmp_path / "decisions.log"
    actions_mod.THROTTLE_PATH = state / "cf-router-throttle.json"
    actions_mod.LEADS_PATH.write_text(json.dumps({
        "leads": [{
            "lead_id": "L0014",
            "owner_approval_code": "#K6VPD",
            "status": "CUSTOMER_FINALIZED",
            "customer_phone": "+19045550104",
            "customer_lid": None,
            "created_at": "2026-05-13T11:49:00-04:00",
        }],
        "next_lead_seq": 15,
    }), encoding="utf-8")
    actions_mod.PROPOSALS_PATH.write_text(
        json.dumps({"sets": [], "next_sequence": 1}), encoding="utf-8",
    )

    calls: list[tuple[str, object]] = []
    actions_mod.is_owner_chat = lambda _chat_id: False
    actions_mod.is_employee_chat = lambda _chat_id: False
    actions_mod.lid_to_phone_via_identify_sender = (
        lambda _chat_id: ("+19045550104", "customer")
    )
    actions_mod.trigger_create_catering_lead = (
        lambda **kw: calls.append(("create", kw)) or (True, "lead_created")
    )
    actions_mod.invoke_create_catering_proposals = (
        lambda *args: calls.append(("proposal", args)) or 0
    )
    actions_mod.send_canonical_followup_reply = (
        lambda *args: calls.append(("reply", args)) or True
    )
    actions_mod.audit_intercepted = lambda **kw: calls.append(("audit", kw))

    result = hooks_mod.pre_gateway_dispatch(SimpleNamespace(
        text="Need catering for 80 people next Friday. Mix veg and non-veg options.",
        chat_id="201975216009469@lid",
        message_id="msg-new-event-after-finalized",
    ))

    assert result == {
        "action": "skip",
        "reason": "cf-router F7 primary: catering inquiry routed deterministically",
    }
    create_calls = [payload for kind, payload in calls if kind == "create"]
    assert len(create_calls) == 1
    assert create_calls[0]["customer_phone"] == "+19045550104"
    assert create_calls[0]["customer_name"] == ""
    assert create_calls[0]["raw_inquiry"].startswith("Need catering for 80")
    assert create_calls[0]["extracted_fields"]["headcount"] == 80
    assert set(create_calls[0]["extracted_fields"]["dietary_restrictions"]) == {"veg", "non-veg"}
    assert not any(kind == "proposal" for kind, _payload in calls)


def test_birthday_catering_inquiry_preserves_date_and_veg_split(tmp_path):
    hooks_mod, actions_mod = _load_plugin_modules()
    state = tmp_path / "state"
    state.mkdir()
    actions_mod.LEADS_PATH = state / "catering-leads.json"
    actions_mod.PROPOSALS_PATH = state / "catering-proposals.json"
    actions_mod.MENU_PENDING_PATH = state / "catering-menu-pending.json"
    actions_mod.CONFIG_PATH = tmp_path / "config.yaml"
    actions_mod.ROSTER_PATH = tmp_path / "roster.json"
    actions_mod.LOG_PATH = tmp_path / "decisions.log"
    actions_mod.THROTTLE_PATH = state / "cf-router-throttle.json"
    actions_mod.LEADS_PATH.write_text(
        json.dumps({"leads": [], "next_lead_seq": 16}),
        encoding="utf-8",
    )
    actions_mod.PROPOSALS_PATH.write_text(
        json.dumps({"sets": [], "next_sequence": 1}), encoding="utf-8",
    )

    calls: list[tuple[str, object]] = []
    actions_mod.is_owner_chat = lambda _chat_id: False
    actions_mod.is_employee_chat = lambda _chat_id: False
    actions_mod.lid_to_phone_via_identify_sender = (
        lambda _chat_id: ("+17329837841", "customer")
    )
    actions_mod.get_revenue_route_clarification = lambda _chat_id: None
    actions_mod.trigger_create_catering_lead = (
        lambda **kw: calls.append(("create", kw)) or (True, '{"lead_id":"L0016"}')
    )
    actions_mod.invoke_create_catering_proposals = (
        lambda *args: calls.append(("proposal", args)) or 0
    )
    actions_mod.audit_intercepted = lambda **kw: calls.append(("audit", kw))

    text = (
        "Bro, I have my daughter birthday coming up on July 12, I'd like you "
        "to help me with catering for 80 people. 60 people non vegetarians "
        "and 20 vegetarians, please suggest me 3 sample combinations menus "
        "to choose from. I hope you could give me best rate."
    )
    result = hooks_mod.pre_gateway_dispatch(SimpleNamespace(
        text=text,
        chat_id="201975216009469@lid",
        message_id="msg-birthday-catering",
    ))

    assert result == {
        "action": "skip",
        "reason": "cf-router F7 primary: catering inquiry routed deterministically",
    }
    create_calls = [payload for kind, payload in calls if kind == "create"]
    assert len(create_calls) == 1
    fields = create_calls[0]["extracted_fields"]
    assert fields["headcount"] == 80
    assert fields["event_date"].endswith("-07-12")
    assert set(fields["dietary_restrictions"]) == {"veg", "non-veg"}
    assert "60 non-veg" in fields["notes"]
    assert "20 veg" in fields["notes"]
    assert "3 sample" in fields["notes"]
    proposal_calls = [payload for kind, payload in calls if kind == "proposal"]
    assert len(proposal_calls) == 1
    assert proposal_calls[0][0] == "L0016"
    assert proposal_calls[0][3] == text


def test_active_lead_sample_menu_request_escapes_to_dispatcher(tmp_path):
    hooks_mod, actions_mod = _load_plugin_modules()
    state = tmp_path / "state"
    state.mkdir()
    actions_mod.LEADS_PATH = state / "catering-leads.json"
    actions_mod.PROPOSALS_PATH = state / "catering-proposals.json"
    actions_mod.MENU_PENDING_PATH = state / "catering-menu-pending.json"
    actions_mod.CONFIG_PATH = tmp_path / "config.yaml"
    actions_mod.ROSTER_PATH = tmp_path / "roster.json"
    actions_mod.LOG_PATH = tmp_path / "decisions.log"
    actions_mod.THROTTLE_PATH = state / "cf-router-throttle.json"
    actions_mod.LEADS_PATH.write_text(json.dumps({
        "leads": [{
            "lead_id": "L0016",
            "owner_approval_code": "#GWXSR",
            "status": "AWAITING_OWNER_APPROVAL",
            "customer_phone": "+17329837841",
            "customer_lid": None,
            "created_at": "2026-06-08T08:24:09-04:00",
        }],
        "next_lead_seq": 17,
    }), encoding="utf-8")
    actions_mod.PROPOSALS_PATH.write_text(
        json.dumps({"sets": [], "next_sequence": 1}), encoding="utf-8",
    )

    calls: list[tuple[str, object]] = []
    actions_mod.is_owner_chat = lambda _chat_id: False
    actions_mod.is_employee_chat = lambda _chat_id: False
    actions_mod.lid_to_phone_via_identify_sender = (
        lambda _chat_id: ("+17329837841", "customer")
    )
    actions_mod.get_revenue_route_clarification = lambda _chat_id: None
    actions_mod.invoke_create_catering_proposals = (
        lambda *args: calls.append(("proposal", args)) or 0
    )
    actions_mod.send_canonical_followup_reply = (
        lambda *args: calls.append(("reply", args)) or True
    )
    actions_mod.audit_intercepted = lambda **kw: calls.append(("audit", kw))

    text = "Can you create two sample menus mix n match."
    assert actions_mod.is_proposal_request(text) is True
    result = hooks_mod.pre_gateway_dispatch(SimpleNamespace(
        text=text,
        chat_id="201975216009469@lid",
        message_id="msg-sample-menus",
    ))

    # PR-A: proposal request against an active lead escapes to the Hermes dispatcher
    # (return None) instead of cf-router invoking create-catering-proposal-options.
    assert result is None
    assert not any(kind == "proposal" for kind, _payload in calls)
    assert not any(kind == "reply" for kind, _payload in calls)
    audit_reasons = [payload.get("reason") for kind, payload in calls if kind == "audit"]
    assert "f7_proposal_request_escaped_to_dispatcher" in audit_reasons


def test_active_lead_menu_constraints_escape_not_owner_wait_reply(tmp_path):
    hooks_mod, actions_mod = _load_plugin_modules()
    state = tmp_path / "state"
    state.mkdir()
    actions_mod.LEADS_PATH = state / "catering-leads.json"
    actions_mod.PROPOSALS_PATH = state / "catering-proposals.json"
    actions_mod.MENU_PENDING_PATH = state / "catering-menu-pending.json"
    actions_mod.CONFIG_PATH = tmp_path / "config.yaml"
    actions_mod.ROSTER_PATH = tmp_path / "roster.json"
    actions_mod.LOG_PATH = tmp_path / "decisions.log"
    actions_mod.THROTTLE_PATH = state / "cf-router-throttle.json"
    actions_mod.LEADS_PATH.write_text(json.dumps({
        "leads": [{
            "lead_id": "L0016",
            "owner_approval_code": "#GWXSR",
            "status": "AWAITING_OWNER_APPROVAL",
            "customer_phone": "+17329837841",
            "customer_lid": None,
            "created_at": "2026-06-08T08:24:09-04:00",
        }],
        "next_lead_seq": 17,
    }), encoding="utf-8")
    actions_mod.PROPOSALS_PATH.write_text(
        json.dumps({"sets": [], "next_sequence": 1}), encoding="utf-8",
    )

    calls: list[tuple[str, object]] = []
    actions_mod.is_owner_chat = lambda _chat_id: False
    actions_mod.is_employee_chat = lambda _chat_id: False
    actions_mod.lid_to_phone_via_identify_sender = (
        lambda _chat_id: ("+17329837841", "customer")
    )
    actions_mod.get_revenue_route_clarification = lambda _chat_id: None
    actions_mod.invoke_create_catering_proposals = (
        lambda *args: calls.append(("proposal", args)) or 0
    )
    actions_mod.send_canonical_followup_reply = (
        lambda *args: calls.append(("reply", args)) or True
    )
    actions_mod.audit_intercepted = lambda **kw: calls.append(("audit", kw))

    text = (
        "Menu should not contain beef and pork. Menu should contain both veg "
        "and non-veg options. Add more appetizers, mains."
    )
    assert actions_mod.is_proposal_request(text) is True
    result = hooks_mod.pre_gateway_dispatch(SimpleNamespace(
        text=text,
        chat_id="201975216009469@lid",
        message_id="msg-menu-constraints",
    ))

    # PR-A: escapes to the Hermes dispatcher; cf-router no longer regenerates
    # proposals itself, and no owner-wait canonical reply is sent.
    assert result is None
    assert not any(kind == "proposal" for kind, _payload in calls)
    assert not any(kind == "reply" for kind, _payload in calls)
    audit_reasons = [payload.get("reason") for kind, payload in calls if kind == "audit"]
    assert "f7_proposal_request_escaped_to_dispatcher" in audit_reasons
