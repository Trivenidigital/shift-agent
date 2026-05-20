"""Pin the customer-facing source-edit acknowledgement copy.

WhatsApp gets outcome-only copy. Audit/Cockpit keep the details. This file
locks the body of `send_flyer_manual_edit_ack` to exactly the operator-
approved string and asserts the absence of workflow-internal terms that
previously leaked into the customer reply (source-preserving, edit queue,
operator/provider language, requested-edit echo, project ID).

The function still accepts request_text/project_id/reason on its signature
for caller compatibility (7 call sites in cf-router/hooks.py); the test
asserts these arguments do NOT reach the WhatsApp body regardless of their
content.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ACTIONS_PATH = REPO_ROOT / "src" / "plugins" / "cf-router" / "actions.py"

EXPECTED_BODY = (
    "Flyer Studio\n"
    "------------\n"
    "Got it. This needs a careful flyer edit. "
    "I'll send the updated flyer here once it's ready."
)

# Workflow internals that previously leaked into the customer ack. Any
# regression that re-introduces these strings into the WhatsApp body must
# fail loudly here so the next reviewer catches it.
FORBIDDEN_TERMS = (
    "source-preserving",
    "source preserving",
    "edit queue",
    "operator edit",
    "operator queue",
    "queued project",
    "queued for a source",
    "Requested edit:",
    "preserve the existing design",
    "generating a new flyer from scratch",
    "needs the source-preserving workflow",
    "provider",
)


def _load_actions_module():
    name = "cf_router_actions_for_manual_edit_ack_copy_test"
    sys.modules.pop(name, None)
    loader = importlib.machinery.SourceFileLoader(name, str(ACTIONS_PATH))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


@pytest.fixture
def captured_message(monkeypatch):
    """Intercept the safe_io.bridge_post call and capture its message arg.

    `send_flyer_manual_edit_ack` performs a function-local
    `from safe_io import bridge_post`, which would normally pick up the real
    `src/platform/safe_io.py`. That module imports `fcntl` at top level
    (Linux-only) so a real import fails on Windows dev machines. Inject a
    fake `safe_io` module into sys.modules BEFORE the function is called;
    the function-local import then resolves to the fake, on both Linux and
    Windows.
    """
    actions = _load_actions_module()

    captured: dict[str, object] = {}

    def fake_bridge_post(chat_id, message):
        captured["chat_id"] = chat_id
        captured["message"] = message
        return True, "test-message-id", "", "sent"

    fake_safe_io = type(sys)("safe_io")
    fake_safe_io.bridge_post = fake_bridge_post  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)

    return actions, captured


def test_manual_edit_ack_body_is_exactly_the_operator_approved_copy(captured_message):
    """The exact body string is the contract. No project ID, no echoed
    request, no source-preserving wording, no queue/operator/provider terms.
    A reviewer who wants to change this copy must update both the function
    body and this expected string in the same diff."""
    actions, captured = captured_message

    ok, mid, err = actions.send_flyer_manual_edit_ack(
        chat_id="555555555555@s.whatsapp.net",
        project_id="F0099",
        request_text="Replace Triveni Express with Lakshmi's Kitchen branding.",
        reason="source_edit_provider_unavailable",
    )

    assert ok is True
    assert mid == "test-message-id"
    assert err == ""
    assert captured["message"] == EXPECTED_BODY


def test_manual_edit_ack_body_does_not_leak_internal_terms(captured_message):
    """Negative assertion: workflow internals MUST NOT reach WhatsApp.

    Pass a request_text that contains the actual jargon to verify the function
    body cannot accidentally echo input back out (a regression that re-added
    `Requested edit: {body}` would echo the raw customer message — that was
    the F0063 drift surface)."""
    actions, captured = captured_message

    actions.send_flyer_manual_edit_ack(
        chat_id="555555555555@s.whatsapp.net",
        project_id="F0099",
        request_text=(
            "I want a source-preserving edit queued for a source-edit provider "
            "with project F0099. Please add this to the operator edit queue."
        ),
        reason="source_edit_provider_unavailable",
    )

    body = captured["message"]
    leaks = [term for term in FORBIDDEN_TERMS if term.lower() in body.lower()]
    assert leaks == [], (
        f"customer-facing ack must not contain workflow-internal terms; leaked: {leaks}"
    )
    # And the long request snippet must not echo into the body either —
    # that was the F0063 drift surface (Requested edit: {body} echo).
    assert "Lakshmi" not in body, "raw customer business name must not echo"
    assert "F0099" not in body, "project ID must not echo into customer body"


def test_manual_edit_ack_body_is_independent_of_caller_arguments(captured_message):
    """request_text / project_id / reason variations MUST NOT change the body.
    The signature is preserved for caller compatibility (7 hooks.py sites)
    but the body is independent of these inputs."""
    actions, captured = captured_message

    actions.send_flyer_manual_edit_ack(
        chat_id="111@s.whatsapp.net",
        project_id="F0001",
        request_text="",
        reason="",
    )
    body_minimal = captured["message"]

    actions.send_flyer_manual_edit_ack(
        chat_id="222@s.whatsapp.net",
        project_id="F0999",
        request_text="A very different request with lots of words. " * 10,
        reason="some_other_reason_string_with_underscores",
    )
    body_maximal = captured["message"]

    assert body_minimal == body_maximal == EXPECTED_BODY
