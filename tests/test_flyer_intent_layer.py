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

    # Default regime (shadow LLM OFF): ceiling stays 250ms.
    monkeypatch.delenv("FLYER_INTENT_SHADOW_LLM", raising=False)
    assert actions._flyer_classifier_timeout_ms() == 250

    # Shadow-LLM regime: ceiling relaxes to 4000ms so a real network call fits.
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM", "1")
    assert actions._flyer_classifier_timeout_ms() == 4000

    # Still hard-capped in the shadow-LLM regime (larger value clamps to 4000).
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER_TIMEOUT_MS", "99999")
    assert actions._flyer_classifier_timeout_ms() == 4000

    # A value under the ceiling passes through unchanged.
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER_TIMEOUT_MS", "1500")
    assert actions._flyer_classifier_timeout_ms() == 1500


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
    # intent.py stays fully provider-free, and so does the cf-router
    # routing/finalize glue (the shadow-context section). B1 (2026-07) adds a
    # plugin-local OpenRouter *shadow* classifier confined to the explicitly
    # delimited "Flyer intent shadow LLM classifier (B1)" section — the ONLY
    # place a provider client is permitted. The routing path never embeds one.
    forbidden = ("openai", "openrouter", "urllib.request", "requests.", "api_key")
    intent_source = (REPO / "src" / "agents" / "flyer" / "intent.py").read_text(encoding="utf-8").lower()
    actions_source = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8").lower()
    routing_glue = actions_source[
        actions_source.index("# === flyer hermes intent shadow context ===") :
        actions_source.index("# === flyer intent shadow llm classifier")
    ]
    for name, source in (("intent.py", intent_source), ("actions routing glue", routing_glue)):
        for term in forbidden:
            assert term not in source, f"{name} must not contain provider client term {term!r}"


# --- B1: plugin-local OpenRouter shadow classifier (strictly shadow) ---------


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_args) -> bool:
        return False


def _openrouter_body(content: object) -> bytes:
    text = content if isinstance(content, str) else json.dumps(content)
    return json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8")


def test_intent_llm_classifier_factory_returns_none_without_key(monkeypatch):
    actions = _load_actions()
    monkeypatch.setattr(actions, "_resolve_openrouter_key_for_flyer_intent", lambda: "")
    assert actions._build_flyer_intent_llm_classifier() is None
    monkeypatch.setattr(
        actions, "_resolve_openrouter_key_for_flyer_intent", lambda: "sk-or-PLACEHOLDER-000"
    )
    assert actions._build_flyer_intent_llm_classifier() is None


def test_intent_llm_classifier_returns_dict_on_200(monkeypatch):
    import urllib.request

    actions = _load_actions()
    monkeypatch.setattr(actions, "_resolve_openrouter_key_for_flyer_intent", lambda: "sk-or-test")
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["auth"] = req.headers.get("Authorization")
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp(
            _openrouter_body(
                {"intent": "new_flyer", "action": "create_project", "confidence": 0.9}
            )
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    classifier = actions._build_flyer_intent_llm_classifier()
    assert classifier is not None
    request = FlyerClassifierRequest(text="make me a flyer", actual_action="new_project")
    out = classifier(request)

    assert isinstance(out, dict)
    assert out["intent"] == "new_flyer"
    # The shadow parse adapter always stamps the future-gateway source.
    assert parse_classifier_payload(out).decision_source == "hermes_gateway_future"
    assert "openrouter.ai" in captured["url"]
    assert captured["timeout"] == 30
    assert captured["auth"] == "Bearer sk-or-test"
    assert captured["payload"]["temperature"] == 0.0
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["model"] == "openai/gpt-4o-mini"


def test_intent_llm_classifier_model_from_env(monkeypatch):
    import urllib.request

    actions = _load_actions()
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM_MODEL", "anthropic/claude-shadow")
    monkeypatch.setattr(actions, "_resolve_openrouter_key_for_flyer_intent", lambda: "sk-or-test")
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp(_openrouter_body({"intent": "unknown", "action": "observe"}))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    actions._build_flyer_intent_llm_classifier()(FlyerClassifierRequest(text="hi"))
    assert captured["payload"]["model"] == "anthropic/claude-shadow"


def test_intent_llm_classifier_raises_on_non_json(monkeypatch):
    import urllib.request

    actions = _load_actions()
    monkeypatch.setattr(actions, "_resolve_openrouter_key_for_flyer_intent", lambda: "sk-or-test")
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=None: _FakeResp(b"<html>502 bad gateway</html>")
    )

    classifier = actions._build_flyer_intent_llm_classifier()
    with pytest.raises(Exception):
        classifier(FlyerClassifierRequest(text="hello"))


# --- B1: allowlist-gated resolution in _flyer_classifier_callable_from_gateway --


def _stub_local_classifier(_request):
    return {"intent": "unknown", "action": "observe"}


def test_shadow_llm_allowlist_semantics(monkeypatch):
    actions = _load_actions()
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM", "1")
    monkeypatch.setattr(
        actions, "_build_flyer_intent_llm_classifier", lambda: _stub_local_classifier
    )
    chat = "17329837841@s.whatsapp.net"

    # empty/unset allowlist ⇒ disabled-for-all (never global-on), even armed.
    monkeypatch.delenv("FLYER_INTENT_SHADOW_LLM_CHATS", raising=False)
    assert actions._flyer_classifier_callable_from_gateway(None, chat_id=chat) is None

    # member (normalized across +/punctuation/JID-suffix) ⇒ armed.
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM_CHATS", "+1 (732) 983-7841, 55501")
    assert actions._flyer_classifier_callable_from_gateway(None, chat_id=chat) is _stub_local_classifier

    # non-member ⇒ off.
    assert (
        actions._flyer_classifier_callable_from_gateway(
            None, chat_id="15550009999@s.whatsapp.net"
        )
        is None
    )

    # flag off ⇒ off even for an allowlisted chat.
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM", "0")
    assert actions._flyer_classifier_callable_from_gateway(None, chat_id=chat) is None


def test_gateway_classifier_precedes_local_shadow_llm(monkeypatch):
    actions = _load_actions()
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM", "1")
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM_CHATS", "17329837841")
    monkeypatch.setattr(
        actions, "_build_flyer_intent_llm_classifier", lambda: _stub_local_classifier
    )

    class Gateway:
        def flyer_intent_classifier(self, request):
            return {}

    gw = Gateway()
    got = actions._flyer_classifier_callable_from_gateway(
        gw, chat_id="17329837841@s.whatsapp.net"
    )
    assert got == gw.flyer_intent_classifier  # gateway attr wins over the local LLM


def test_shadow_llm_not_armed_when_flag_unset(monkeypatch):
    actions = _load_actions()
    monkeypatch.delenv("FLYER_INTENT_SHADOW_LLM", raising=False)
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM_CHATS", "17329837841")
    monkeypatch.setattr(
        actions, "_build_flyer_intent_llm_classifier", lambda: _stub_local_classifier
    )
    assert (
        actions._flyer_classifier_callable_from_gateway(
            None, chat_id="17329837841@s.whatsapp.net"
        )
        is None
    )


# --- B1: per-UTC-day budget cap (skipped_budget) -----------------------------


def _metered_stub(counter: dict):
    def classifier(_request):
        counter["n"] = counter.get("n", 0) + 1
        return {"intent": "approve_final", "action": "approve_project", "confidence": 0.99}

    classifier._flyer_shadow_llm_metered = True  # type: ignore[attr-defined]
    return classifier


def test_shadow_llm_budget_reserve_increments_and_resets(monkeypatch, tmp_path):
    actions = _load_actions()
    monkeypatch.setattr(actions, "FLYER_INTENT_SHADOW_LLM_BUDGET_PATH", tmp_path / "budget.json")
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM_DAILY_CAP", "2")

    assert actions._flyer_intent_shadow_llm_reserve_budget() is True   # count -> 1
    assert actions._flyer_intent_shadow_llm_reserve_budget() is True   # count -> 2
    assert actions._flyer_intent_shadow_llm_reserve_budget() is False  # cap exhausted
    assert json.loads((tmp_path / "budget.json").read_text())["count"] == 2

    # A stale prior-day counter (even one over cap) resets on the new UTC day.
    (tmp_path / "budget.json").write_text(json.dumps({"utc_day": "2000-01-01", "count": 999}))
    assert actions._flyer_intent_shadow_llm_reserve_budget() is True


def test_shadow_llm_over_budget_records_skipped_budget(monkeypatch, tmp_path):
    actions = _load_actions()
    emitted: list[dict] = []
    counter: dict = {}
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER", "shadow")
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM", "1")
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM_CHATS", "17329837841")
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM_DAILY_CAP", "0")  # no budget today
    monkeypatch.setattr(actions, "FLYER_INTENT_SHADOW_LLM_BUDGET_PATH", tmp_path / "budget.json")
    monkeypatch.setattr(actions, "audit_flyer_hermes_intent_decision", lambda **kw: emitted.append(kw))
    monkeypatch.setattr(actions, "_build_flyer_intent_llm_classifier", lambda: _metered_stub(counter))

    token = actions.begin_flyer_intent_shadow(
        text="approve",
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.budget",
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
            gateway=None,
        )
    finally:
        actions.reset_flyer_intent_shadow(token)

    assert emitted, "budget-skip must still emit a synchronous audit row"
    assert emitted[0]["classifier_status"] == "skipped_budget"
    assert counter.get("n", 0) == 0  # the LLM call was skipped, not merely dropped


def test_shadow_llm_under_budget_fires_worker(monkeypatch, tmp_path):
    actions = _load_actions()
    emitted: list[dict] = []
    counter: dict = {}
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER", "shadow")
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM", "1")
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM_CHATS", "17329837841")
    monkeypatch.setenv("FLYER_INTENT_SHADOW_LLM_DAILY_CAP", "5")
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER_TIMEOUT_MS", "1000")
    monkeypatch.setattr(actions, "FLYER_INTENT_SHADOW_LLM_BUDGET_PATH", tmp_path / "budget.json")
    monkeypatch.setattr(actions, "audit_flyer_hermes_intent_decision", lambda **kw: emitted.append(kw))
    monkeypatch.setattr(actions, "_build_flyer_intent_llm_classifier", lambda: _metered_stub(counter))

    token = actions.begin_flyer_intent_shadow(
        text="approve",
        chat_id="17329837841@s.whatsapp.net",
        message_id="wamid.fire",
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
            gateway=None,
        )
    finally:
        actions.reset_flyer_intent_shadow(token)

    _wait_for(lambda: bool(emitted))
    assert emitted[0]["classifier_status"] == "success"
    assert counter["n"] == 1  # exactly one metered fire consumed
    assert json.loads((tmp_path / "budget.json").read_text())["count"] == 1


# --- B1: flag rename aliases (NEW name wins, legacy names still work) ---------


def _finalize_and_capture_status(actions, monkeypatch, *, text="Create flyer for evening snacks"):
    emitted: list[dict] = []
    monkeypatch.setattr(actions, "audit_flyer_hermes_intent_decision", lambda **kw: emitted.append(kw))
    token = actions.begin_flyer_intent_shadow(
        text=text, chat_id="15550000001@s.whatsapp.net", message_id="wamid.alias", has_media=False
    )
    try:
        actions.record_flyer_intent_route_event(
            reason="flyer_primary_project_created", subprocess_rc=0, detail="project_id=F0065"
        )
        actions.finalize_flyer_intent_shadow(
            hook_result={"action": "skip", "reason": "cf-router flyer primary created"}, gateway=None
        )
    finally:
        actions.reset_flyer_intent_shadow(token)
    return emitted, token


@pytest.mark.parametrize(
    "env",
    [
        {"FLYER_INTENT_SHADOW_MODE": "off"},                                  # new name
        {"FLYER_HERMES_INTENT_MODE": "off"},                                  # legacy name
        {"FLYER_INTENT_SHADOW_MODE": "off", "FLYER_HERMES_INTENT_MODE": "active"},  # new wins
    ],
)
def test_shadow_mode_alias_off(monkeypatch, env):
    actions = _load_actions()
    monkeypatch.delenv("FLYER_INTENT_SHADOW_MODE", raising=False)
    monkeypatch.delenv("FLYER_HERMES_INTENT_MODE", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    token = actions.begin_flyer_intent_shadow(
        text="Create flyer", chat_id="1@s.whatsapp.net", message_id="m"
    )
    assert token is None


def test_shadow_audit_setting_alias_enables_classifier_path(monkeypatch):
    actions = _load_actions()
    monkeypatch.delenv("FLYER_HERMES_INTENT_CLASSIFIER", raising=False)
    monkeypatch.setenv("FLYER_INTENT_SHADOW_AUDIT", "shadow")  # new name enables
    emitted, _ = _finalize_and_capture_status(actions, monkeypatch)
    assert emitted[0]["classifier_status"] == "skipped_no_gateway"


def test_shadow_audit_setting_legacy_name_still_works(monkeypatch):
    actions = _load_actions()
    monkeypatch.delenv("FLYER_INTENT_SHADOW_AUDIT", raising=False)
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER", "shadow")  # legacy name
    emitted, _ = _finalize_and_capture_status(actions, monkeypatch)
    assert emitted[0]["classifier_status"] == "skipped_no_gateway"


def test_shadow_audit_setting_new_name_wins(monkeypatch):
    actions = _load_actions()
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER", "shadow")  # legacy says on
    monkeypatch.setenv("FLYER_INTENT_SHADOW_AUDIT", "off")          # new says off -> wins
    emitted, _ = _finalize_and_capture_status(actions, monkeypatch)
    assert emitted[0]["classifier_status"] == "off"


def test_shadow_timeout_alias(monkeypatch):
    actions = _load_actions()
    monkeypatch.delenv("FLYER_INTENT_SHADOW_LLM", raising=False)  # default ceiling 250
    monkeypatch.delenv("FLYER_HERMES_INTENT_CLASSIFIER_TIMEOUT_MS", raising=False)
    monkeypatch.setenv("FLYER_INTENT_SHADOW_TIMEOUT_MS", "123")  # new name
    assert actions._flyer_classifier_timeout_ms() == 123

    monkeypatch.delenv("FLYER_INTENT_SHADOW_TIMEOUT_MS", raising=False)
    monkeypatch.setenv("FLYER_HERMES_INTENT_CLASSIFIER_TIMEOUT_MS", "77")  # legacy name
    assert actions._flyer_classifier_timeout_ms() == 77

    monkeypatch.setenv("FLYER_INTENT_SHADOW_TIMEOUT_MS", "88")  # new wins
    assert actions._flyer_classifier_timeout_ms() == 88
