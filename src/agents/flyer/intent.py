"""Flyer Studio Hermes intent contract and safety validator.

This module is intentionally side-effect free. Hermes can become the messy
language "brain" behind this schema, but Flyer code remains the deterministic
contract and safety harness.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import time
from enum import StrEnum
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from agents.flyer.customer_copy_policy import scan_customer_text
except Exception:  # pragma: no cover - deployed flat-module fallback
    from flyer_customer_copy_policy import scan_customer_text  # type: ignore


class FlyerIntentMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"
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
ClassifierStatus = Literal[
    "off",
    "skipped_not_candidate",
    "skipped_passthrough",
    "skipped_no_gateway",
    "skipped_budget",
    "success",
    "timeout",
    "invalid",
    "error",
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


class FlyerClassifierRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    text: str = Field(default="", max_length=4000)
    has_media: bool = False
    actual_route: str = Field(default="", max_length=120)
    actual_action: ActualAction = "unknown"
    route_sequence: list[str] = Field(default_factory=list, max_length=20)
    branch_return_reason: str = Field(default="", max_length=300)
    customer_status: str = Field(default="", max_length=80)
    project_status: str = Field(default="", max_length=80)
    intake_status: str = Field(default="", max_length=80)
    risk_scope: RiskScope = "none"


class FlyerClassifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ClassifierStatus
    decision: FlyerIntentDecision = Field(default_factory=FlyerIntentDecision)
    error_kind: str = Field(default="", max_length=80)
    error_detail: str = Field(default="", max_length=300)
    latency_ms: int = Field(default=0, ge=0)


def mode_from_value(value: str | None) -> FlyerIntentMode:
    normalized = str(value or "").strip().lower()
    if normalized == "off":
        return FlyerIntentMode.OFF
    if normalized == "active":
        return FlyerIntentMode.ACTIVE
    if normalized in {"", "shadow"}:
        return FlyerIntentMode.SHADOW
    return FlyerIntentMode.UNSUPPORTED_ACTIVE_MODE


def classifier_setting_from_env(value: str | None) -> Literal["off", "shadow", "active"]:
    normalized = str(value or "").strip().lower()
    if normalized == "active":
        return "active"
    return "shadow" if normalized == "shadow" else "off"


def parse_classifier_payload(payload: Any) -> FlyerIntentDecision:
    if isinstance(payload, FlyerIntentDecision):
        data = payload.model_dump()
    elif isinstance(payload, str):
        data = json.loads(payload)
    elif isinstance(payload, dict):
        data = dict(payload)
    else:
        raise TypeError(f"unsupported classifier payload: {type(payload).__name__}")
    data["decision_source"] = "hermes_gateway_future"
    return FlyerIntentDecision.model_validate(data)


def _classifier_result(
    status: ClassifierStatus,
    *,
    start: float,
    decision: FlyerIntentDecision | None = None,
    error_kind: str = "",
    error_detail: str = "",
) -> FlyerClassifierResult:
    latency_ms = max(0, int((time.monotonic() - start) * 1000))
    return FlyerClassifierResult(
        status=status,
        decision=decision or FlyerIntentDecision(decision_source="none"),
        error_kind=error_kind[:80],
        error_detail=error_detail[:300],
        latency_ms=latency_ms,
    )


def run_classifier_shadow(
    classifier: Callable[[FlyerClassifierRequest], Any] | None,
    request: FlyerClassifierRequest,
    *,
    timeout_ms: int = 250,
) -> FlyerClassifierResult:
    start = time.monotonic()
    if classifier is None:
        return _classifier_result("skipped_no_gateway", start=start)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(classifier, request)
    try:
        payload = future.result(timeout=max(0.001, timeout_ms / 1000.0))
    except concurrent.futures.TimeoutError:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        return _classifier_result("timeout", start=start, error_kind="timeout")
    except Exception as exc:
        executor.shutdown(wait=False, cancel_futures=True)
        return _classifier_result("error", start=start, error_kind=type(exc).__name__)
    executor.shutdown(wait=False, cancel_futures=False)

    try:
        decision = parse_classifier_payload(payload)
    except Exception as exc:
        return _classifier_result("invalid", start=start, error_kind=type(exc).__name__)
    return _classifier_result("success", start=start, decision=decision)


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


def deterministic_baseline_decision(request: FlyerClassifierRequest) -> FlyerIntentDecision:
    """Return a local semantic classification when no Hermes classifier exists.

    This is intentionally conservative. It can identify account/billing/status
    intents for deterministic handlers, but it does not authorize mutation by
    itself; the action registry and account/payment state machine still decide.
    """
    text = " ".join((request.text or "").split())
    lower = text.lower()
    try:
        from agents.flyer.action_registry import normalize_account_command_text
    except Exception:  # pragma: no cover - deployed flat-module fallback
        try:
            from flyer_action_registry import normalize_account_command_text  # type: ignore
        except Exception:
            normalize_account_command_text = None  # type: ignore

    if normalize_account_command_text is not None and normalize_account_command_text(text):
        return FlyerIntentDecision(
            decision_source="deterministic_baseline",
            intent="account_update",
            action="account_update",
            confidence=0.9,
            reason="semantic account or billing command matched action registry",
        )
    if re.search(r"\b(?:payment|checkout|invoice|card|refund|stripe|razorpay|billing)\b", lower):
        return FlyerIntentDecision(
            decision_source="deterministic_baseline",
            intent="account_update",
            action="clarify",
            confidence=0.82,
            needs_clarification=True,
            reason="regulated billing language without executable command",
        )
    if lower.strip(" .!,:;") in {"status", "where is my flyer", "is my flyer ready"}:
        return FlyerIntentDecision(
            decision_source="deterministic_baseline",
            intent="status_check",
            action="route_current",
            confidence=0.82,
            reason="status phrasing",
        )
    if lower.strip(" .!,:;") == "approve":
        return FlyerIntentDecision(
            decision_source="deterministic_baseline",
            intent="approve_final",
            action="approve_project",
            confidence=0.92,
            reason="exact approval token",
        )
    if re.search(r"\b(?:revise|change|replace|fix|edit|update)\b.*\b(?:flyer|poster|banner|image|logo|text|price)\b", lower):
        return FlyerIntentDecision(
            decision_source="deterministic_baseline",
            intent="revise_flyer",
            action="revise_project",
            confidence=0.78,
            reason="revision language",
        )
    if re.search(r"\b(?:create|make|design|generate|need|want)\b.*\b(?:flyer|flier|poster|banner|marketing material)\b", lower):
        return FlyerIntentDecision(
            decision_source="deterministic_baseline",
            intent="new_flyer",
            action="create_project",
            confidence=0.8,
            reason="new flyer language",
        )
    return FlyerIntentDecision(decision_source="deterministic_baseline", intent="unknown", action="observe", confidence=0.0)


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
        "intent": decision.intent,
        "action": decision.action,
        "decision_source": decision.decision_source,
        "confidence_bucket": _confidence_bucket(decision.confidence),
        "validator_ok": validation.ok,
        "validator_reasons": list(validation.reasons),
        "would_mutate": validation.would_mutate,
        "risk_scope": validation.risk_scope,
        "actual_action": actual_action,
        "route_label": actual_action,
        "outcome_label": "accepted" if validation.ok else "validator_rejected",
    }


def _confidence_bucket(confidence: float) -> Literal["low", "medium", "high"]:
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.55:
        return "medium"
    return "low"
