"""IN-3 (Flyer Studio E2E adversarial audit 2026-07-13).

Generation failure after a processing-ack must still send ONE plain-language
closure to the customer when the failure reason is UNCLASSIFIED (not a
manual-review reason). Previously the `proc_ok` branch of
``_send_generation_failure_customer_update`` returned ``True, "", ""`` silently,
so a customer who had already received the "creating your flyer now" ack got NO
failure/closure message when generate-flyer-concepts exited non-zero for an
unrecognized reason (e.g. the concurrency guard, an unclassified crash) — only
an audit row + the reactive "any update?" path existed.

These tests exercise ``_send_generation_failure_customer_update`` directly with
the outbound send functions monkeypatched, so no real bridge send occurs.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"


def _load_plugin_modules():
    pkg_name = "cf_router_flyer_in3_pkg_under_test"
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


def _wire(monkeypatch, actions, *, manual_review):
    """Monkeypatch every outbound path; record which one fired. No real send."""
    sent_text: list[str] = []
    intake_calls: list[bool] = []
    manual_calls: list[bool] = []
    monkeypatch.setattr(
        actions, "flyer_generation_queued_manual_review",
        lambda _detail: manual_review,
    )
    monkeypatch.setattr(
        actions, "send_flyer_text",
        lambda _chat_id, message, **_kw: sent_text.append(message) or (True, "closure-mid", ""),
    )
    monkeypatch.setattr(
        actions, "send_flyer_intake_ack",
        lambda _chat_id, _project_id, **_kw: intake_calls.append(True) or (True, "intake-mid", ""),
    )
    monkeypatch.setattr(
        actions, "send_flyer_manual_review_ack",
        lambda _chat_id, _project_id, _request_text, **_kw: manual_calls.append(True) or (True, "manual-mid", ""),
    )
    return sent_text, intake_calls, manual_calls


def test_unclassified_failure_after_processing_ack_sends_closure(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent_text, intake_calls, manual_calls = _wire(monkeypatch, actions, manual_review=False)

    ok, mid, err = hooks._send_generation_failure_customer_update(
        "chat-1",
        "F0001",
        "make me a diwali flyer",
        "exit=137 concurrency guard tripped; provider timeout",
        proc_ok=True,
        action_context=None,
    )

    # The bug: this branch used to return silently. Now exactly one plain-language
    # closure must go out.
    assert len(sent_text) == 1, "expected exactly one closure message to be sent"
    closure = sent_text[0]
    assert closure.strip(), "closure message must be non-empty"
    # Customer-facing plain copy: no internal reason code / operator jargon leak.
    lower = closure.lower()
    assert "exit=" not in closure
    assert "concurrency" not in lower
    assert "provider" not in lower
    # Must NOT fall through to the no-prior-ack intake path or the manual-review path.
    assert not intake_calls
    assert not manual_calls
    # Return contract preserved: (ok, mid, err) forwarded from the send.
    assert (ok, mid, err) == (True, "closure-mid", "")


def test_classified_manual_review_failure_unchanged(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent_text, intake_calls, manual_calls = _wire(monkeypatch, actions, manual_review=True)

    ok, mid, err = hooks._send_generation_failure_customer_update(
        "chat-1",
        "F0001",
        "make me a flyer",
        "queued_manual_review",
        proc_ok=True,
        action_context=None,
    )

    # Classified path is unchanged: routes to the manual-review ack, no plain closure.
    assert manual_calls == [True]
    assert not sent_text
    assert not intake_calls
    assert (ok, mid, err) == (True, "manual-mid", "")


def test_unclassified_failure_without_prior_ack_sends_intake(monkeypatch):
    hooks, actions = _load_plugin_modules()
    sent_text, intake_calls, manual_calls = _wire(monkeypatch, actions, manual_review=False)

    ok, mid, err = hooks._send_generation_failure_customer_update(
        "chat-1",
        "F0001",
        "make me a flyer",
        "exit=1 unclassified crash",
        proc_ok=False,
        action_context=None,
    )

    # No prior processing-ack is unchanged: still routes to the intake ack.
    assert intake_calls == [True]
    assert not sent_text
    assert not manual_calls
    assert (ok, mid, err) == (True, "intake-mid", "")
