"""Pin customer-facing manual-review/source-edit acknowledgement copy.

The customer WhatsApp message must be outcome-only. Project IDs, raw source
edit requests, relationship notes, provider/reason details, and queue wording
belong in audit/Cockpit/manual queue state, not in the outbound body.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ACTIONS_PATH = REPO_ROOT / "src" / "plugins" / "cf-router" / "actions.py"
PLATFORM = REPO_ROOT / "src" / "platform"

MANUAL_REVIEW_EXPECTED_BODY = (
    "Flyer Studio\n"
    "------------\n"
    "I couldn't finish this automatically. I'll review it and send an update here."
)

EDIT_PROCESSING_EXPECTED_BODY = (
    "Flyer Studio\n"
    "------------\n"
    "Got it. I'm updating your flyer now and will send the revised version here when it's ready."
)

F0063_STYLE_REQUEST = (
    "Please use the uploaded Triveni source flyer. Exact source-edit request: "
    "replace Triveni Express with Lakshmis Kitchen, remove the extra 08:00, "
    "add weekend special biryani for $9.99, keep the same layout, colors, "
    "logo placement, food photos, footer, and WhatsApp number. "
    "Authorized relationship: co-owners."
)

F0063_STYLE_REASON = (
    "provider unavailable; reason_code=source_edit_provider_unavailable; "
    "manual_edit_required; source-preserving workflow queued for operator"
)

FORBIDDEN_CUSTOMER_TERMS = (
    "queued project",
    "Project F",
    "Requested edit:",
    "Original customer request",
    "Authorized relationship",
    "source-preserving workflow",
    "source-preserving",
    "operator",
    "manual_edit_required",
    "provider",
    "reason_code",
    "manual review",
    "Request:",
    "Reason:",
    "F0063",
    "co-owners",
    "Lakshmis Kitchen",
    "08:00",
    "careful flyer edit",
    "updated flyer",
    "free trial",
    "trial active",
    "quota",
    "credits",
    "samples remaining",
)


def _load_actions_module():
    name = "cf_router_actions_for_manual_review_ack_copy_test"
    sys.modules.pop(name, None)
    loader = importlib.machinery.SourceFileLoader(name, str(ACTIONS_PATH))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


def _action_context():
    sys.path.insert(0, str(PLATFORM))
    from schemas import ActionExecutionContext

    return ActionExecutionContext(
        action_id="test.flyer.manual_review_ack",
        is_regulated_action=False,
        verified_action_result=False,
    )


@pytest.fixture
def captured_message(monkeypatch):
    actions = _load_actions_module()
    captured: dict[str, object] = {}

    def fake_bridge_post(chat_id, message, **_kwargs):
        captured["chat_id"] = chat_id
        captured["message"] = message
        return True, "test-message-id", "", "sent"

    fake_safe_io = type(sys)("safe_io")
    fake_safe_io.bridge_post = fake_bridge_post  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)

    return actions, captured


def _assert_no_customer_leaks(body: str) -> None:
    leaks = [term for term in FORBIDDEN_CUSTOMER_TERMS if term.lower() in body.lower()]
    assert leaks == [], f"customer-facing ack leaked internal/source-edit terms: {leaks}"
    assert len(body) < 180


def test_manual_review_ack_is_short_outcome_only_copy_for_f0063_style_input(captured_message):
    actions, captured = captured_message

    ok, mid, err = actions.send_flyer_manual_review_ack(
        chat_id="555555555555@s.whatsapp.net",
        project_id="F0063",
        request_text=F0063_STYLE_REQUEST,
        reason=F0063_STYLE_REASON,
        action_context=_action_context(),
    )

    assert ok is True
    assert mid == "test-message-id"
    assert err == ""
    assert captured["message"] == MANUAL_REVIEW_EXPECTED_BODY
    _assert_no_customer_leaks(str(captured["message"]))


def test_source_edit_processing_ack_is_short_active_edit_copy(captured_message):
    actions, captured = captured_message

    ok, mid, err = actions.send_flyer_edit_processing_ack(
        chat_id="555555555555@s.whatsapp.net",
        project_id="F0063",
        action_context=_action_context(),
    )

    assert ok is True
    assert mid == "test-message-id"
    assert err == ""
    assert captured["message"] == EDIT_PROCESSING_EXPECTED_BODY
    _assert_no_customer_leaks(str(captured["message"]))
