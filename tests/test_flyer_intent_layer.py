from __future__ import annotations

import re
import sys
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
    FlyerIntentContext,
    FlyerIntentDecision,
    FlyerIntentMode,
    build_training_example,
    mode_from_value,
    normalize_actual_action,
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


@pytest.mark.parametrize("value", ["active", "low_risk_active", "nonsense"])
def test_active_modes_are_mechanically_inert_for_pr1(value):
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
    assert example["decision"]["intent"] == "new_flyer"


def test_cf_router_flyer_reason_literals_match_schema():
    source = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")
    reasons = {
        match.group(1)
        for pattern in (
            r'reason\s*=\s*"((?:flyer_)[^"]+)"',
            r'reason\s*=\s*\(\s*"((?:flyer_)[^"]+)"',
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
