"""Flyer Studio Hermes intent contract and safety validator.

This module is intentionally side-effect free. Hermes can become the messy
language "brain" behind this schema, but Flyer code remains the deterministic
contract and safety harness.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.flyer.customer_copy_policy import scan_customer_text


class FlyerIntentMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    UNSUPPORTED_ACTIVE_MODE = "unsupported_active_mode"


DecisionSource = Literal["none", "fixture", "deterministic_baseline", "hermes_gateway_future"]
FlyerIntent = Literal[
    "new_flyer",
    "revise_flyer",
    "approve_final",
    "status_check",
    "account_update",
    "onboarding_answer",
    "source_edit",
    "reference_use",
    "sample_prompt_choice",
    "unclear",
    "unknown",
]
FlyerIntentAction = Literal[
    "observe",
    "clarify",
    "route_current",
    "create_project",
    "revise_project",
    "approve_project",
    "account_update",
    "manual_review",
]
RiskScope = Literal[
    "active_project",
    "active_customer",
    "active_intake",
    "pre_project_customer_visible",
    "historical_audit",
    "none",
]
ActualAction = Literal[
    "new_project",
    "revision",
    "approval",
    "status",
    "manual_review",
    "account_update",
    "onboarding_or_intake",
    "passthrough",
    "failure",
    "unknown",
]

MUTATING_ACTIONS = {
    "create_project",
    "revise_project",
    "approve_project",
    "account_update",
    "manual_review",
}

HERMES_FLYER_INTENT_PROMPT = """You classify Flyer Studio WhatsApp messages.
Return strict JSON matching FlyerIntentDecision. Prefer clarify/observe over
mutation when confidence is low. Never include internal project ids, provider
names, reason codes, or raw request echoes in customer_reply."""


class FlyerIntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    decision_source: DecisionSource = "none"
    intent: FlyerIntent = "unknown"
    action: FlyerIntentAction = "observe"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarifying_question: str = Field(default="", max_length=500)
    customer_reply: str = Field(default="", max_length=1000)
    target_project_id: str = Field(default="", max_length=40)
    reason: str = Field(default="", max_length=500)
    evidence: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("clarifying_question", "customer_reply", "target_project_id", "reason", mode="before")
    @classmethod
    def _coerce_short_string(cls, value: Any) -> str:
        return "" if value is None else str(value)


class FlyerIntentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: FlyerIntentMode = FlyerIntentMode.SHADOW
    raw_request: str = ""
    known_project_ids: list[str] = Field(default_factory=list)
    risk_scope: RiskScope = "none"
    allow_source_edit_automation: bool = False


class FlyerIntentValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    reasons: tuple[str, ...] = ()
    would_mutate: bool = False
    risk_scope: RiskScope = "none"


def mode_from_value(value: str | None) -> FlyerIntentMode:
    normalized = str(value or "").strip().lower()
    if normalized == "off":
        return FlyerIntentMode.OFF
    if normalized in {"", "shadow"}:
        return FlyerIntentMode.SHADOW
    return FlyerIntentMode.UNSUPPORTED_ACTIVE_MODE


def validate_flyer_intent_decision(
    decision: FlyerIntentDecision,
    context: FlyerIntentContext,
    *,
    mutation_confidence_threshold: float = 0.85,
) -> FlyerIntentValidationResult:
    reasons: list[str] = []
    would_mutate = decision.action in MUTATING_ACTIONS

    if context.mode == FlyerIntentMode.UNSUPPORTED_ACTIVE_MODE and would_mutate:
        reasons.append("unsupported_active_mode")
    if would_mutate and decision.confidence < mutation_confidence_threshold:
        reasons.append("low_confidence_mutation")
    if decision.intent == "source_edit" and would_mutate and not context.allow_source_edit_automation:
        reasons.append("source_edit_automation_not_enabled")
    if decision.target_project_id and context.known_project_ids and decision.target_project_id not in context.known_project_ids:
        reasons.append("target_project_outside_context")
    if decision.customer_reply:
        scan = scan_customer_text(decision.customer_reply, raw_request=context.raw_request)
        if scan.hits:
            reasons.append("customer_copy_policy_violation")

    return FlyerIntentValidationResult(
        ok=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        would_mutate=would_mutate,
        risk_scope=context.risk_scope,
    )


def normalize_actual_action(actual_route: str, branch_return_reason: str = "") -> ActualAction:
    route = str(actual_route or "").lower()
    branch = str(branch_return_reason or "").lower()
    if route in {"llm_passthrough", "plugin_error_passthrough"}:
        return "passthrough"
    if "failed" in route:
        return "failure"
    if "status" in route:
        return "status"
    if "account" in route:
        return "account_update"
    if "manual_review" in route or "manual_edit" in route or "exact_edit_queued" in route:
        return "manual_review"
    if "approve" in branch or "finalized" in branch:
        return "approval"
    if "revision" in branch or "revision" in route:
        return "revision"
    if "intake" in route or "onboarding" in route or "starter" in route or "brief" in route:
        return "onboarding_or_intake"
    if "project_created" in route or "guest_order_started" in route or "brand_asset_saved" in route:
        return "new_project"
    return "unknown"


def build_training_example(
    *,
    decision: FlyerIntentDecision,
    validation: FlyerIntentValidationResult,
    message_id_hash: str,
    chat_key_hash: str,
    actual_action: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "message_id_hash": message_id_hash,
        "chat_key_hash": chat_key_hash,
        "decision": decision.model_dump(),
        "validation": validation.model_dump(),
        "actual_action": actual_action,
    }
