from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
PLATFORM = REPO / "src" / "platform"
SRC = REPO / "src"
ACTIONS = REPO / "src" / "plugins" / "cf-router" / "actions.py"
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"

sys.path.insert(0, str(SRC))
from agents.flyer.customer_copy_policy import CUSTOMER_COPY_FORBIDDEN_RE

FORBIDDEN_CUSTOMER_COPY = CUSTOMER_COPY_FORBIDDEN_RE


def _load_actions(monkeypatch: pytest.MonkeyPatch):
    sys.path.insert(0, str(PLATFORM))
    sys.path.insert(0, str(SRC))
    module_name = "cf_router_actions_for_customer_lifecycle_copy"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(ACTIONS))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _load_plugin_modules():
    pkg_name = "cf_router_customer_lifecycle_pkg"
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


def _action_context():
    sys.path.insert(0, str(PLATFORM))
    from schemas import ActionExecutionContext

    return ActionExecutionContext(
        action_id="test.flyer.copy_ack",
        is_regulated_action=False,
        verified_action_result=False,
    )


def _install_bridge_capture(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []
    fake_safe_io = types.ModuleType("safe_io")

    def bridge_post(_chat_id: str, message: str, **_kwargs):
        sent.append(message)
        return True, f"mid-{len(sent)}", "", 200

    fake_safe_io.bridge_post = bridge_post
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    return sent


def _assert_customer_safe(text: str) -> None:
    assert not FORBIDDEN_CUSTOMER_COPY.search(text), text


def test_initial_ack_helpers_are_outcome_only(monkeypatch):
    actions = _load_actions(monkeypatch)
    sent = _install_bridge_capture(monkeypatch)

    for helper in (
        actions.send_flyer_processing_ack,
        actions.send_flyer_intake_ack,
        actions.send_flyer_manual_review_ack,
        actions.send_flyer_manual_edit_ack,
    ):
        ok, _mid, err = helper(
            "17329837841@s.whatsapp.net",
            "F0065",
            action_context=_action_context(),
        )
        assert ok, err
        _assert_customer_safe(sent[-1])


def test_status_replies_hide_project_ids_and_internal_terms(monkeypatch):
    actions = _load_actions(monkeypatch)
    sys.path.insert(0, str(PLATFORM))
    from schemas import FlyerManualReview, FlyerProject, FlyerRequestFields
    from agents.flyer.workflow import build_project_status_reply

    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F9001",
        status="manual_edit_required",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-status",
        raw_request="Create flyer for evening snacks.",
        fields=FlyerRequestFields(event_or_business_name="Evening Snacks", contact_info="+17329837841"),
        manual_review=FlyerManualReview(
            status="queued",
            reason="source_edit_provider_unavailable",
            reason_code="source_edit_provider_unavailable",
            queued_at=now,
        ),
    )

    for reply in (
        build_project_status_reply(project),
        actions.flyer_project_status_reply(project.model_dump(mode="json")),
        actions.flyer_manual_edit_status_reply(project.model_dump(mode="json")),
    ):
        _assert_customer_safe(reply)


def test_status_reply_fallbacks_hide_project_ids(monkeypatch):
    actions = _load_actions(monkeypatch)
    monkeypatch.setattr(actions, "_ensure_platform_path", lambda: None)
    monkeypatch.setattr(actions, "_ensure_local_src_path", lambda: None)
    monkeypatch.setitem(sys.modules, "schemas", None)

    reply = actions.flyer_project_status_reply({"project_id": "F7777", "status": "intake_started"})
    _assert_customer_safe(reply)


def test_regeneration_failure_copy_hides_project_id(monkeypatch):
    hooks, actions = _load_plugin_modules()

    sent: list[str] = []
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text, **_kwargs: (sent.append(text) is None, "mid", ""))

    ok, _mid, err = hooks._send_flyer_regeneration_failed_ack(
        "17329837841@s.whatsapp.net",
        "F0065",
        action_context=_action_context(),
    )

    assert ok, err
    _assert_customer_safe(sent[-1])
