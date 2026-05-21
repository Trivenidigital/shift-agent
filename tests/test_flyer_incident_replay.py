from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
PLATFORM = REPO / "src" / "platform"
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
CREATE_SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "create-flyer-project"
FIXTURE_PATH = REPO / "tests" / "fixtures" / "flyer_incident_replay" / "flyer_incidents.json"

sys.path.insert(0, str(SRC))
from agents.flyer.customer_copy_policy import classify_initial_ack, scan_customer_text


class _NoopFileLock:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _load_plugin_modules():
    pkg_name = "cf_router_flyer_replay_pkg"
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


def _load_create_script(monkeypatch: pytest.MonkeyPatch):
    fake_safe_io = sys.modules.get("safe_io") or types.ModuleType("safe_io")
    fake_safe_io.FileLock = _NoopFileLock
    fake_safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    sys.path.insert(0, str(PLATFORM))
    sys.path.insert(0, str(SRC))
    module_name = "create_flyer_project_for_replay"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(CREATE_SCRIPT))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _fixtures() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _event(fixture: dict) -> SimpleNamespace:
    source = SimpleNamespace(chat_id=fixture["chat_id"], message_id=f"msg-{fixture['id']}")
    return SimpleNamespace(
        text=fixture["text"],
        body=fixture["text"],
        message=fixture["text"],
        chat_id=fixture["chat_id"],
        message_id=f"msg-{fixture['id']}",
        source=source,
        media_path=fixture.get("media_path") or None,
    )


def _write_customer_state(path: Path, customer: dict) -> None:
    phone = str(customer.get("phone") or customer.get("public_phone") or customer.get("business_whatsapp_number") or "")
    row = {
        "customer_id": customer.get("customer_id") or "CUST0001",
        "business_name": customer.get("business_name") or "Lakshmis Kitchn",
        "business_address": customer.get("business_address") or "90 Brybar Dr St Johns FL",
        "primary_chat_id": customer.get("primary_chat_id") or "",
        "onboarded_by_phone": phone,
        "public_phone": phone,
        "business_whatsapp_number": phone,
        "authorized_request_numbers": [phone] if phone else [],
        "business_category": customer.get("business_category") or "Indian Restaurant",
        "preferred_language": "en",
        "plan_id": "trial",
        "status": customer.get("status") or "trial",
        "created_at": "2026-05-18T00:00:00+00:00",
        "updated_at": "2026-05-18T00:00:00+00:00",
        "activated_at": "2026-05-18T00:00:00+00:00",
        "monthly_flyers_used": 0,
        "billing_provider": "manual",
        "payment_currency": "USD",
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "next_customer_sequence": 2,
                "customers": [row],
                "onboarding_sessions": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _real_create_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture: dict) -> dict:
    module = _load_create_script(monkeypatch)
    projects_path = tmp_path / f"{fixture['id']}-projects.json"
    customers_path = tmp_path / f"{fixture['id']}-customers.json"
    customer = dict(fixture.get("customer") or {})
    _write_customer_state(customers_path, customer)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create-flyer-project",
            "--customer-phone", fixture["resolved_identity"]["phone"],
            "--chat-id", fixture["chat_id"],
            "--message-id", f"msg-{fixture['id']}",
            "--raw-request", fixture["text"],
            "--state-path", str(projects_path),
            "--customer-state-path", str(customers_path),
            "--asset-dir", str(tmp_path / f"{fixture['id']}-assets"),
        ],
    )
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert module.main() == 0
    return json.loads(stdout.getvalue())


def _install_common_replay_mocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture: dict):
    hooks, actions = _load_plugin_modules()
    calls: list[str] = []
    audits: list[dict] = []
    sent: list[str] = []
    identity_calls: list[str] = []
    fake_safe_io = types.ModuleType("safe_io")

    def bridge_post(_chat_id: str, message: str):
        calls.append("bridge_post")
        sent.append(message)
        return True, f"mid-{len(sent)}", "", 200

    def bridge_send_media(_chat_id: str, _path: str, *, caption: str = ""):
        calls.append("bridge_send_media")
        if caption:
            sent.append(caption)
        return True, f"media-{len(sent)}", "", 200

    fake_safe_io.bridge_post = bridge_post
    fake_safe_io.bridge_send_media = bridge_send_media
    fake_safe_io.FileLock = _NoopFileLock
    fake_safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)

    def fail_call(name: str):
        def _fail(*_args, **_kwargs):
            raise AssertionError(f"unregistered live surface called: {name}")
        return _fail

    monkeypatch.setattr(actions.subprocess, "run", fail_call("subprocess.run"))
    for attr in (
        "CONFIG_PATH", "LEADS_PATH", "PROPOSALS_PATH", "MENU_PENDING_PATH",
        "FLYER_PROJECTS_PATH", "FLYER_CUSTOMERS_PATH", "FLYER_GUEST_ORDERS_PATH",
        "FLYER_REFERENCE_SCOPE_PATH", "ROSTER_PATH", "LOG_PATH", "THROTTLE_PATH",
    ):
        monkeypatch.setattr(actions, attr, tmp_path / f"{attr.lower()}.json")

    monkeypatch.setattr(actions, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions, "flyer_campaign_cta_text", lambda _text: "")
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda *_a, **_kw: None)
    monkeypatch.setattr(actions, "find_reserved_flyer_guest_order", lambda *_a, **_kw: None)
    monkeypatch.setattr(actions, "find_latest_flyer_project_for_status_by_sender", lambda *_a, **_kw: None)
    monkeypatch.setattr(actions, "find_flyer_project_by_id_for_sender", lambda *_a, **_kw: None)
    monkeypatch.setattr(actions, "consume_flyer_reference_scope_choice", lambda *_a, **_kw: None)
    monkeypatch.setattr(actions, "peek_flyer_source_vs_new_pending", lambda **_kw: None)
    monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", lambda *_a, **_kw: None)
    monkeypatch.setattr(actions, "consume_flyer_reference_authorization_reply", lambda *_a, **_kw: None)
    monkeypatch.setattr(actions, "flyer_starter_prompts_enabled", lambda *_a, **_kw: False)
    monkeypatch.setattr(actions, "flyer_starter_prompt_already_sent", lambda *_a, **_kw: True)
    monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", lambda *_a, **_kw: None)
    monkeypatch.setattr(hooks, "_try_flyer_account_intercept", lambda *_a, **_kw: None)
    monkeypatch.setattr(hooks, "_try_flyer_existing_onboarding_intercept", lambda *_a, **_kw: None)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kw: calls.append(f"audit:{kw.get('reason')}") or audits.append(kw))
    if hasattr(actions, "audit_source_vs_new"):
        monkeypatch.setattr(actions, "audit_source_vs_new", lambda **kw: calls.append(f"source_audit:{kw.get('choice')}"))

    def resolve_identity(chat_id: str):
        identity_calls.append(chat_id)
        resolved = fixture["resolved_identity"]
        return resolved.get("phone"), resolved.get("role", "customer")

    def send_text(_chat_id: str, text: str):
        calls.append("send_flyer_text")
        sent.append(text)
        return True, f"mid-{len(sent)}", ""

    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", resolve_identity)
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda *_a, **_kw: fixture.get("customer"))
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda *_a, **_kw: fixture.get("active_project"))
    monkeypatch.setattr(actions, "send_flyer_text", send_text)
    monkeypatch.setattr(actions, "send_flyer_concept_previews", lambda *_a, **_kw: calls.append("send_previews") or (True, "preview-mid", ""))
    monkeypatch.setattr(hooks, "_reserve_flyer_access_or_reply", lambda *_a, **_kw: ("quota:CUST0001", None))
    monkeypatch.setattr(hooks, "_release_flyer_access", lambda *_a, **_kw: (True, "released"))
    monkeypatch.setattr(hooks, "_send_preview_then_finalize_access", lambda *_a, **_kw: calls.append("send_preview_then_finalize_access") or (True, "preview-mid", ""))

    return hooks, actions, calls, audits, sent, identity_calls


def test_incident_replay_fixture_set_is_large_enough_and_unique():
    fixtures = _fixtures()
    ids = [item["id"] for item in fixtures]
    assert len(fixtures) >= 8
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda item: item["id"])
def test_flyer_incident_replay_fixture(fixture, tmp_path, monkeypatch):
    hooks, actions, calls, audits, sent, identity_calls = _install_common_replay_mocks(monkeypatch, tmp_path, fixture)
    expect = fixture["expect"]

    def fake_create(**kwargs):
        calls.append("trigger_create_flyer_project")
        assert kwargs["chat_id"] == fixture["chat_id"]
        assert kwargs["customer_phone"] == fixture["resolved_identity"]["phone"]
        if fixture["mode"] == "fresh_new_over_active" and fixture["id"].startswith("F0065"):
            project = _real_create_project(monkeypatch, tmp_path, fixture)
            facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}
            assert facts["business_name"]["value"] == expect["business_name"]
            assert facts["business_name"]["source"] == "customer_profile"
            assert facts["campaign_title"]["value"] == expect["campaign_title"]
            assert facts["campaign_title"]["source"] == "customer_text"
        else:
            project = {
                "project_id": "F9001",
                "status": "intake_started",
                "fields": {"event_or_business_name": "Replay Campaign", "contact_info": fixture["resolved_identity"]["phone"]},
                "locked_facts": [],
                "concepts": [],
            }
        return True, json.dumps(project), project

    monkeypatch.setattr(actions, "trigger_create_flyer_project", fake_create)
    update_calls: list[tuple] = []

    def fake_update(*args, **_kwargs):
        calls.append("invoke_update_flyer_project")
        update_calls.append(args)
        return True, json.dumps({"revision_requires_clarification": False, "revision_patch": {}})

    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda *_a, **_kw: calls.append("trigger_generate_flyer_concepts") or (False, "visual_qa_failed: missing required visible fact: business_name"))
    monkeypatch.setattr(actions, "invoke_update_flyer_project", fake_update)
    monkeypatch.setattr(actions, "finalize_and_send_flyer", lambda *_a, **_kw: calls.append("finalize_and_send_flyer") or (False, "visual_qa_failed: missing required visible fact: business_name"))
    monkeypatch.setattr(actions, "flyer_source_edit_preflight", lambda *_a, **_kw: (False, "provider unavailable", "source_edit_provider_unavailable"))

    mode = fixture["mode"]
    if mode == "reference_scope_choice":
        monkeypatch.setattr(actions, "consume_flyer_reference_scope_choice", lambda *_a, **_kw: fixture["pending"])
    elif mode == "source_new_status_check":
        monkeypatch.setattr(actions, "parse_source_vs_new_followup", lambda _text: ("", ""))
        monkeypatch.setattr(actions, "peek_flyer_source_vs_new_pending", lambda **_kw: fixture["pending"])
    elif mode == "source_choice":
        monkeypatch.setattr(actions, "parse_source_vs_new_followup", lambda _text: ("source", ""))
        monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", lambda *_a, **_kw: fixture["pending"])
        monkeypatch.setattr(actions, "flyer_project_has_manual_review_queued", lambda _p: True)

    result = hooks.pre_gateway_dispatch(_event(fixture))

    assert identity_calls and identity_calls[0] == fixture["chat_id"]
    _assert_expected_route(fixture, expect, result, calls, audits, sent, update_calls)
    for forbidden in expect.get("forbidden_calls", []):
        assert forbidden not in calls
    for required in expect.get("must_call", []):
        assert required in calls
    audit_reasons = [row.get("reason") for row in audits]
    for required_audit in expect.get("must_audit", []):
        assert required_audit in audit_reasons, audits
    for snippet in expect.get("must_send_contains", []):
        assert any(snippet in text for text in sent), sent
    for text in sent:
        assert not scan_customer_text(text, raw_request=fixture["text"]).hits, text
    if expect.get("duplicate_initial_ack") is False:
        markers = [classify_initial_ack(text) for text in sent]
        assert not any("processing" in marker for marker in markers) or not any("intake" in marker for marker in markers)


def _assert_expected_route(
    fixture: dict,
    expect: dict,
    result: dict | None,
    calls: list[str],
    audits: list[dict],
    sent: list[str],
    update_calls: list[tuple],
) -> None:
    route = expect.get("route")
    reason = str((result or {}).get("reason") or "")
    audit_reasons = [row.get("reason") for row in audits]
    audit_details = "\n".join(str(row.get("detail") or "") for row in audits)
    if route in {"new_project", "clarification", "source_new_clarification", "source_new_status", "manual_edit_queued", "revision", "status_reply"}:
        assert result is not None and result.get("action") == "skip", (fixture["id"], result, calls, audits, sent)
    if route == "new_project":
        assert "trigger_create_flyer_project" in calls
        assert "flyer_active_project_bypassed" in audit_reasons
        assert "flyer_primary_project_created" in audit_reasons
    elif route == "source_new_clarification":
        assert "source-vs-new clarification" in reason
    elif route == "source_new_status":
        assert "source-vs-new status" in reason
    elif route == "manual_edit_queued":
        assert "source-edit queued" in reason
        assert "flyer_reference_exact_edit_queued" in audit_reasons
        assert any("--manual-reason-code" in args and expect.get("manual_review_reason_code") in args for args in update_calls)
    elif route == "finalize_failed":
        assert result is None
        assert "finalize_and_send_flyer" in calls
        assert "flyer_primary_failed" in audit_reasons
        assert "approve=true" in audit_details
        assert "visual_qa_failed" in audit_details
    elif route == "clarification":
        assert sent
        assert "trigger_create_flyer_project" not in calls
    elif route == "revision":
        assert "invoke_update_flyer_project" in calls
        assert "trigger_create_flyer_project" not in calls
    elif route == "status_reply":
        assert "send_flyer_text" in calls
        assert "trigger_create_flyer_project" not in calls
        assert "invoke_update_flyer_project" not in calls
    else:
        raise AssertionError(f"unasserted expected route for {fixture['id']}: {route!r}")


def test_preview_approved_final_qa_replay_feeds_self_eval_active_risk(tmp_path, monkeypatch):
    fixture = next(item for item in _fixtures() if item["mode"] == "approve_final_qa_failure")
    hooks, actions, _calls, audits, _sent, _identity_calls = _install_common_replay_mocks(monkeypatch, tmp_path, fixture)
    monkeypatch.setattr(actions, "finalize_and_send_flyer", lambda *_a, **_kw: (False, "visual_qa_failed: missing required visible fact: business_name"))
    result = hooks.pre_gateway_dispatch(_event(fixture))
    assert result is None
    assert any(
        row.get("reason") == "flyer_primary_failed"
        and "approve=true" in str(row.get("detail") or "")
        and "visual_qa_failed" in str(row.get("detail") or "")
        for row in audits
    )

    spec = importlib.util.spec_from_file_location("flyer_self_eval_replay", REPO / "tools" / "flyer-self-evaluation.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    report = module.build_report(
        projects={"projects": [fixture["active_project"]]},
        decision_entries=[
            {"type": "cf_router_intercepted", "reason": "flyer_primary_project_created", "project_id": "F0065", "detail": "project_id=F0065; ack_message_id=preview-mid"},
            *audits,
        ],
        now=module.parse_utc("2026-05-21T08:00:00Z"),
    )

    incident = next(item for item in report["incidents"] if item["type"] == "preview_approved_final_qa_failed")
    assert incident["evidence_details"]["active_customer_risk"] is True
