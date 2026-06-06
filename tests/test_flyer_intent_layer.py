from __future__ import annotations

import re
import sys
import time
import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
PLATFORM = SRC / "platform"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(PLATFORM))

from agents.flyer.intent import (  # noqa: E402
    FlyerClassifierRequest,
    FlyerIntentContext,
    FlyerIntentDecision,
    FlyerIntentMode,
    build_training_example,
    classifier_setting_from_env,
    deterministic_baseline_decision,
    mode_from_value,
    normalize_actual_action,
    parse_classifier_payload,
    run_classifier_shadow,
    validate_flyer_intent_decision,
)
from schemas import CfRouterIntercepted, FlyerHermesIntentDecision, LogEntry  # noqa: E402


def _load_actions():
    import importlib.util

    path = REPO / "src" / "plugins" / "cf-router" / "actions.py"
    spec = importlib.util.spec_from_file_location("cf_router_actions_intent_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_intent_decision_rejects_extra_fields():
    with pytest.raises(ValidationError):
        FlyerIntentDecision.model_validate(
            {
                "schema_version": 1,
                "decision_source": "fixture",
                "intent": "new_flyer",
                "action": "create_project",
                "confidence": 0.91,
                "unexpected": "nope",
            }
        )


def test_validator_rejects_customer_copy_policy_leak():
    decision = FlyerIntentDecision(
        decision_source="fixture",
        intent="status_check",
        action="clarify",
        confidence=0.9,
        customer_reply="Request processing. I created flyer project F0065.",
    )

    result = validate_flyer_intent_decision(
        decision,
        FlyerIntentContext(mode=FlyerIntentMode.SHADOW, raw_request="evening snacks"),
    )

    assert not result.ok
    assert "customer_copy_policy_violation" in result.reasons


def test_validator_rejects_low_confidence_mutation():
    decision = FlyerIntentDecision(
        decision_source="fixture",
        intent="new_flyer",
        action="create_project",
        confidence=0.72,
    )

    result = validate_flyer_intent_decision(
        decision,
        FlyerIntentContext(mode=FlyerIntentMode.SHADOW),
    )

    assert not result.ok
    assert "low_confidence_mutation" in result.reasons
    assert result.would_mutate is True


def test_validator_rejects_source_edit_automation_in_pr1():
    decision = FlyerIntentDecision(
        decision_source="fixture",
        intent="source_edit",
        action="create_project",
        confidence=0.96,
    )

    result = validate_flyer_intent_decision(
        decision,
        FlyerIntentContext(mode=FlyerIntentMode.SHADOW, allow_source_edit_automation=False),
    )

    assert not result.ok
    assert "source_edit_automation_not_enabled" in result.reasons


@pytest.mark.parametrize("value", ["low_risk_active", "nonsense"])
def test_unsupported_active_modes_are_mechanically_inert(value):
    mode = mode_from_value(value)
    assert mode == FlyerIntentMode.UNSUPPORTED_ACTIVE_MODE


def test_normalize_actual_action_groups_route_families():
    assert normalize_actual_action("flyer_primary_project_created", "created") == "new_project"
    assert normalize_actual_action("flyer_project_status", "") == "status"
    assert normalize_actual_action("flyer_account_command", "") == "account_update"
    assert normalize_actual_action("llm_passthrough", "") == "passthrough"


def test_training_example_is_pii_light_and_hash_based():
    decision = FlyerIntentDecision(
        decision_source="fixture",
        intent="new_flyer",
        action="create_project",
        confidence=0.92,
        customer_reply="Got it. I can create that flyer.",
        clarifying_question="What date should I use?",
        target_project_id="F0065",
        reason="customer asked for Weekend Breakfast Specials",
        evidence=["has flyer request"],
    )
    validation = validate_flyer_intent_decision(decision, FlyerIntentContext(mode=FlyerIntentMode.SHADOW))

    example = build_training_example(
        decision=decision,
        validation=validation,
        message_id_hash="abc123",
        chat_key_hash="def456",
        actual_action="new_project",
    )

    assert example["message_id_hash"] == "abc123"
    assert example["chat_key_hash"] == "def456"
    assert "raw_request" not in example
    assert example["intent"] == "new_flyer"
    assert example["action"] == "create_project"
    assert "decision" not in example
    assert "customer_reply" not in json.dumps(example)
    assert "clarifying_question" not in json.dumps(example)
    assert "target_project_id" not in json.dumps(example)
    assert "Weekend Breakfast" not in json.dumps(example)


def test_classifier_payload_parser_marks_hermes_gateway_source():
    decision = parse_classifier_payload(
        {
            "schema_version": 1,
            "decision_source": "fixture",
            "intent": "new_flyer",
            "action": "create_project",
            "confidence": 0.93,
        }
    )

    assert decision.decision_source == "hermes_gateway_future"
    assert decision.intent == "new_flyer"


def test_classifier_setting_only_enables_shadow():
    assert classifier_setting_from_env("shadow") == "shadow"
    assert classifier_setting_from_env("active") == "active"
    assert classifier_setting_from_env("off") == "off"
    assert classifier_setting_from_env("") == "off"


def test_active_mode_is_supported_but_still_registry_gated():
    assert mode_from_value("active") == FlyerIntentMode.ACTIVE
    request = FlyerClassifierRequest(text="I want the 60 flyers per month plan")

    decision = deterministic_baseline_decision(request)
    validation = validate_flyer_intent_decision(
        decision,
        FlyerIntentContext(mode=FlyerIntentMode.ACTIVE, raw_request=request.text, risk_scope="active_customer"),
    )

    assert decision.decision_source == "deterministic_baseline"
    assert decision.intent == "account_update"
    assert decision.action == "account_update"
    assert validation.ok is True


def test_classifier_shadow_reports_invalid_and_timeout_without_throwing():
    request = FlyerClassifierRequest(
        text="Create flyer for lunch",
        has_media=False,
        actual_route="flyer_primary_project_created",
        actual_action="new_project",
    )

    invalid = run_classifier_shadow(lambda _request: {"unexpected": "shape"}, request, timeout_ms=50)
    assert invalid.status == "invalid"
    assert invalid.decision.decision_source == "none"

    def slow(_request):
        time.sleep(0.2)
        return {"intent": "new_flyer", "action": "create_project", "confidence": 0.95}

    timeout = run_classifier_shadow(slow, request, timeout_ms=10)
    assert timeout.status == "timeout"
    assert timeout.decision.decision_source == "none"
    assert timeout.latency_ms >= 0


def test_classifier_shadow_success_validates_decision():
    request = FlyerClassifierRequest(
        text="Approve",
        has_media=False,
        actual_route="flyer_project_finalized",
        actual_action="approval",
    )

    result = run_classifier_shadow(
        lambda _request: {"intent": "approve_final", "action": "approve_project", "confidence": 0.96},
        request,
        timeout_ms=100,
    )

    assert result.status == "success"
    assert result.decision.decision_source == "hermes_gateway_future"
    assert result.decision.intent == "approve_final"


def test_cf_router_flyer_reason_literals_match_schema():
    source = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")
    reasons = {
        match.group(1)
        for pattern in (
            r'reason\s*=\s*"((?:flyer_)[^"]+)"',
            r'reason\s*=\s*\(\s*"((?:flyer_)[^"]+)"',
            # ternary `reason=("X" if c else "Y")` — the else-branch literal would
            # otherwise be missed (2026-06-06: flyer_bare_brief_generation_failed).
            r'\belse\s+"((?:flyer_)[^"]+)"',
        )
        for match in re.finditer(pattern, source)
    }
    allowed = set(get_args(CfRouterIntercepted.model_fields["reason"].annotation))
    missing = sorted(reasons - allowed)
    assert missing == []


def test_flyer_hermes_intent_decision_schema_round_trips():
    row = {
        "type": "flyer_hermes_intent_decision",
        "ts": "2026-05-22T00:00:00Z",
        "schema_version": 1,
        "mode": "shadow",
        "decision_source": "fixture",
        "classifier_status": "success",
        "classifier_latency_ms": 12,
        "classifier_error_kind": "",
        "classifier_error_detail": "",
        "message_id_hash": "mhash",
        "chat_key_hash": "chash",
        "has_media": False,
        "validator_ok": True,
        "validator_reasons": [],
        "advisory_intent": "new_flyer",
        "advisory_action": "create_project",
        "confidence": 0.91,
        "would_mutate": True,
        "actual_route": "flyer_primary_project_created",
        "actual_reason": "project_created",
        "actual_action": "new_project",
        "route_sequence": ["flyer_active_project_bypassed", "flyer_primary_project_created"],
        "route_terminal": True,
        "subprocess_rc": 0,
        "branch_return_reason": "cf-router flyer primary created",
        "selected_project_id": "F0065",
        "prior_active_project_id": "F0062",
        "project_status": "awaiting_final_approval",
        "customer_status": "trial",
        "intake_status": "",
        "preview_source": "actual",
        "live_route_changed": False,
        "active_customer_risk": True,
        "risk_scope": "active_project",
    }

    parsed = FlyerHermesIntentDecision.model_validate(row)
    assert parsed.actual_action == "new_project"
    adapter = __import__("pydantic").TypeAdapter(LogEntry)
    assert adapter.validate_python(row).type == "flyer_hermes_intent_decision"


def test_shadow_context_emits_terminal_route_not_intermediate_bypass(monkeypatch):
    actions = _load_actions()
    emitted: list[dict] = []
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER", "shadow")
    monkeypatch.setattr(actions, "audit_flyer_hermes_intent_decision", lambda **kw: emitted.append(kw))

    token = actions.begin_flyer_intent_shadow(
        text="Create flyer for evening snacks",
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.test",
        has_media=False,
    )
    try:
        actions.record_flyer_intent_route_event(
            reason="flyer_active_project_bypassed",
            subprocess_rc=0,
            detail="project_id=F0062; fresh_flyer_intent=true",
        )
        actions.record_flyer_intent_route_event(
            reason="flyer_primary_project_created",
            subprocess_rc=0,
            detail="project_id=F0065",
        )
        actions.finalize_flyer_intent_shadow(
            hook_result={"action": "skip", "reason": "cf-router flyer primary created"}
        )
    finally:
        actions.reset_flyer_intent_shadow(token)

    assert emitted
    assert emitted[0]["actual_route"] == "flyer_primary_project_created"
    assert emitted[0]["route_sequence"] == ["flyer_active_project_bypassed", "flyer_primary_project_created"]
    assert emitted[0]["message_id_hash"] != "wamid.test"
    assert emitted[0]["classifier_status"] == "skipped_no_gateway"


def test_shadow_context_uses_injected_gateway_classifier_after_route(monkeypatch):
    actions = _load_actions()
    emitted: list[dict] = []
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER", "shadow")
    monkeypatch.setattr(actions, "audit_flyer_hermes_intent_decision", lambda **kw: emitted.append(kw))

    class FakeGateway:
        def flyer_intent_classifier(self, request):
            return {
                "schema_version": 1,
                "intent": "new_flyer",
                "action": "create_project",
                "confidence": 0.95,
            }

    token = actions.begin_flyer_intent_shadow(
        text="Create flyer for evening snacks",
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.test",
        has_media=False,
    )
    try:
        actions.record_flyer_intent_route_event(
            reason="flyer_primary_project_created",
            subprocess_rc=0,
            detail="project_id=F0065",
        )
        actions.finalize_flyer_intent_shadow(
            hook_result={"action": "skip", "reason": "cf-router flyer primary created"},
            gateway=FakeGateway(),
        )
    finally:
        actions.reset_flyer_intent_shadow(token)

    _wait_for(lambda: bool(emitted))
    assert emitted[0]["classifier_status"] == "success"
    assert emitted[0]["decision"].decision_source == "hermes_gateway_future"
    assert emitted[0]["validation"].ok is True


def test_active_context_uses_deterministic_baseline_without_gateway(monkeypatch):
    actions = _load_actions()
    emitted: list[dict] = []
    monkeypatch.setenv("FLYER_HERMES_INTENT_MODE", "active")
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER", "active")
    monkeypatch.setattr(actions, "audit_flyer_hermes_intent_decision", lambda **kw: emitted.append(kw))

    token = actions.begin_flyer_intent_shadow(
        text="I want the 60 flyers per month plan",
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.plan",
        has_media=False,
    )
    try:
        actions.record_flyer_intent_route_event(
            reason="flyer_account_command",
            subprocess_rc=0,
            detail="customer_id=CUST0001; status=trial",
        )
        actions.finalize_flyer_intent_shadow(
            hook_result={"action": "skip", "reason": "cf-router flyer account command"}
        )
    finally:
        actions.reset_flyer_intent_shadow(token)

    assert emitted
    assert emitted[0]["mode"] == "active"
    assert emitted[0]["classifier_status"] == "success"
    assert emitted[0]["decision"].decision_source == "deterministic_baseline"
    assert emitted[0]["decision"].intent == "account_update"


def test_shadow_context_classifier_runs_after_finalizer_returns(monkeypatch):
    actions = _load_actions()
    emitted: list[dict] = []
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER", "shadow")
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER_TIMEOUT_MS", "250")
    monkeypatch.setattr(actions, "audit_flyer_hermes_intent_decision", lambda **kw: emitted.append(kw))

    class SlowGateway:
        def flyer_intent_classifier(self, request):
            time.sleep(0.2)
            return {"intent": "new_flyer", "action": "create_project", "confidence": 0.95}

    token = actions.begin_flyer_intent_shadow(
        text="Create flyer for evening snacks",
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.test",
        has_media=False,
    )
    try:
        actions.record_flyer_intent_route_event(
            reason="flyer_primary_project_created",
            subprocess_rc=0,
            detail="project_id=F0065",
        )
        start = time.monotonic()
        actions.finalize_flyer_intent_shadow(
            hook_result={"action": "skip", "reason": "cf-router flyer primary created"},
            gateway=SlowGateway(),
        )
        elapsed_ms = (time.monotonic() - start) * 1000
    finally:
        actions.reset_flyer_intent_shadow(token)

    assert elapsed_ms < 50
    _wait_for(lambda: bool(emitted), timeout=1.0)
    assert emitted[0]["classifier_status"] == "success"


def test_shadow_context_does_not_call_classifier_for_passthrough_candidate(monkeypatch):
    actions = _load_actions()
    emitted: list[dict] = []
    called = False
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER", "shadow")
    monkeypatch.setattr(actions, "audit_flyer_hermes_intent_decision", lambda **kw: emitted.append(kw))

    class FakeGateway:
        def flyer_intent_classifier(self, request):
            nonlocal called
            called = True
            return {"intent": "new_flyer", "action": "create_project", "confidence": 0.95}

    token = actions.begin_flyer_intent_shadow(
        text="Create flyer maybe later",
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.test",
        has_media=False,
    )
    try:
        actions.finalize_flyer_intent_shadow(hook_result=None, gateway=FakeGateway())
    finally:
        actions.reset_flyer_intent_shadow(token)

    assert called is False
    assert emitted[0]["classifier_status"] == "skipped_passthrough"


def test_shadow_context_contains_gateway_classifier_failures(monkeypatch):
    actions = _load_actions()
    emitted: list[dict] = []
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER", "shadow")
    monkeypatch.setattr(actions, "audit_flyer_hermes_intent_decision", lambda **kw: emitted.append(kw))

    class BadGateway:
        @property
        def flyer_intent_classifier(self):
            raise RuntimeError("gateway property exploded")

    token = actions.begin_flyer_intent_shadow(
        text="Create flyer for evening snacks",
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.test",
        has_media=False,
    )
    try:
        actions.record_flyer_intent_route_event(
            reason="flyer_primary_project_created",
            subprocess_rc=0,
            detail="project_id=F0065",
        )
        actions.finalize_flyer_intent_shadow(
            hook_result={"action": "skip", "reason": "cf-router flyer primary created"},
            gateway=BadGateway(),
        )
    finally:
        actions.reset_flyer_intent_shadow(token)

    assert emitted[0]["classifier_status"] == "error"
    assert emitted[0]["classifier_error_kind"] == "RuntimeError"


def test_shadow_classifier_timeout_is_hard_capped(monkeypatch):
    actions = _load_actions()
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER_TIMEOUT_MS", "5000")

    assert actions._flyer_classifier_timeout_ms() == 250


def test_shadow_context_off_mode_emits_nothing(monkeypatch):
    actions = _load_actions()
    emitted: list[dict] = []
    monkeypatch.setenv("FLYER_HERMES_INTENT_MODE", "off")
    monkeypatch.setattr(actions, "audit_flyer_hermes_intent_decision", lambda **kw: emitted.append(kw))

    token = actions.begin_flyer_intent_shadow(
        text="Create flyer for evening snacks",
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.test",
    )
    actions.finalize_flyer_intent_shadow(hook_result=None)

    assert token is None
    assert emitted == []


def _wait_for(predicate, *, timeout: float = 0.5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_static_no_provider_client_in_intent_or_cf_router_classifier_glue():
    forbidden = ("openai", "openrouter", "urllib.request", "requests.", "api_key")
    intent_source = (REPO / "src" / "agents" / "flyer" / "intent.py").read_text(encoding="utf-8").lower()
    actions_source = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8").lower()
    classifier_glue = actions_source[
        actions_source.index("# === flyer hermes intent shadow context ===") :
        actions_source.index("# === audit ===")
    ]
    for name, source in (("intent.py", intent_source), ("actions classifier glue", classifier_glue)):
        for term in forbidden:
            assert term not in source, f"{name} must not contain provider client term {term!r}"
