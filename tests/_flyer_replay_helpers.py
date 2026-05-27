"""Shared Flyer replay harness helpers.

Extracted from ``tests/test_flyer_incident_replay.py`` so the same harness
is reused by ``tests/test_flyer_rollout_replay.py``. Behavior-preserving
move; the only intentional change is the addition of an ``onboarding``
route branch in ``_assert_expected_route`` (used by the LID-only rollout
scenario).

Files under ``tests/`` cannot import each other through the test runner's
default sys.path setup without one ``_``-prefix; this module's name keeps
it out of pytest collection while still being importable.
"""
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


class _NoopFileLock:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def load_plugin_modules():
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


def load_create_script(monkeypatch: pytest.MonkeyPatch):
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


def build_event(fixture: dict) -> SimpleNamespace:
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


def write_customer_state(path: Path, customer: dict) -> None:
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


def real_create_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture: dict) -> dict:
    module = load_create_script(monkeypatch)
    projects_path = tmp_path / f"{fixture['id']}-projects.json"
    customers_path = tmp_path / f"{fixture['id']}-customers.json"
    customer = dict(fixture.get("customer") or {})
    write_customer_state(customers_path, customer)
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


def install_common_replay_mocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture: dict):
    hooks, actions = load_plugin_modules()
    calls: list[str] = []
    audits: list[dict] = []
    sent: list[str] = []
    identity_calls: list[str] = []
    fake_safe_io = types.ModuleType("safe_io")

    def bridge_post(_chat_id: str, message: str, **_kwargs):
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

    def send_text(_chat_id: str, text: str, **_kwargs):
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


def assert_expected_route(
    fixture: dict,
    expect: dict,
    result: dict | None,
    calls: list[str],
    audits: list[dict],
    sent: list[str],
    update_calls: list[tuple],
) -> None:
    """Assert the cf-router dispatched the fixture's expected route.

    Routes:
      new_project | clarification | source_new_clarification | source_new_status
      manual_edit_queued | revision | status_reply | finalize_failed
      onboarding (added 2026-05-21 for LID-only rollout fixture)
    """
    route = expect.get("route")
    reason = str((result or {}).get("reason") or "")
    audit_reasons = [row.get("reason") for row in audits]
    audit_details = "\n".join(str(row.get("detail") or "") for row in audits)
    if route in {
        "new_project",
        "clarification",
        "source_new_clarification",
        "source_new_status",
        "manual_edit_queued",
        "revision",
        "status_reply",
        "finalize_failed",
    }:
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
        assert "finalization failed" in reason
        assert "finalize_and_send_flyer" in calls
        assert "flyer_primary_failed" in audit_reasons
        assert "approve=true" in audit_details
        assert "visual_qa_failed" in audit_details
        assert any("issue preparing the final files" in text for text in sent)
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
    elif route == "onboarding":
        # LID-only Start Free Trial -> onboarding intercept consumed
        # by cf-router. The intercept returns a non-None dispatch result;
        # no project create or revision call is made.
        assert result is not None
        assert "trigger_create_flyer_project" not in calls
        assert "invoke_update_flyer_project" not in calls
    elif route == "intake_consumed":
        # Brief-builder intake intercept consumes the message. cf-router
        # returns the intercept's dispatch dict and the project create CLI
        # (if any) is owned by the intake intercept itself, not cf-router.
        assert result is not None
        assert "trigger_create_flyer_project" not in calls
        assert "invoke_update_flyer_project" not in calls
    elif route == "account_intercept":
        # Account intercept consumes the message (duplicate-phone
        # recognition path). No project/revision side effects.
        assert result is not None
        assert "trigger_create_flyer_project" not in calls
        assert "invoke_update_flyer_project" not in calls
    else:
        raise AssertionError(f"unasserted expected route for {fixture['id']}: {route!r}")
