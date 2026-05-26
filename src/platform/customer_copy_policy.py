"""Customer-visible copy safety checks for regulated actions."""
from __future__ import annotations

import re
from typing import Iterable

from pydantic import BaseModel, ConfigDict


FORBIDDEN_COMPLETION_VERBS: tuple[str, ...] = (
    "processed",
    "completed",
    "upgraded",
    "downgraded",
    "changed",
    "confirmed",
    "sent",
    "approved",
    "paid",
    "posted",
    "pushed",
    "applied",
    "scheduled",
    "booked",
    "cancelled",
    "canceled",
    "refunded",
)


class ActionExecutionContext(BaseModel):
    """Evidence context for customer-visible regulated-action copy."""

    model_config = ConfigDict(extra="forbid")

    is_regulated_action: bool = False
    verified_action_result: bool = False
    action_id: str = ""


class CopyLintResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    verbs_found: list[str] = []
    reason: str = ""


def _normalise_action_context(action_context: object | None) -> ActionExecutionContext | None:
    if action_context is None:
        return None
    if isinstance(action_context, ActionExecutionContext):
        return action_context
    if isinstance(action_context, dict):
        return ActionExecutionContext.model_validate(action_context)
    return ActionExecutionContext.model_validate({
        "is_regulated_action": bool(getattr(action_context, "is_regulated_action", False)),
        "verified_action_result": bool(getattr(action_context, "verified_action_result", False)),
        "action_id": str(getattr(action_context, "action_id", "") or ""),
    })


def _find_forbidden_verbs(text: str) -> list[str]:
    found: list[str] = []
    for verb in FORBIDDEN_COMPLETION_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", text or "", flags=re.IGNORECASE):
            found.append(verb)
    return found


def lint_customer_copy(
    text: str | Iterable[str],
    action_context: object | None = None,
) -> CopyLintResult:
    """Reject completion claims unless the action has verified evidence."""

    if isinstance(text, str):
        combined = text
    else:
        combined = "\n".join(str(part) for part in text if str(part).strip())
    verbs = _find_forbidden_verbs(combined)
    if not verbs:
        return CopyLintResult(allowed=True)

    context = _normalise_action_context(action_context)
    if context is None or not context.is_regulated_action:
        return CopyLintResult(allowed=True, verbs_found=verbs)
    if context is not None and context.is_regulated_action and context.verified_action_result:
        return CopyLintResult(allowed=True, verbs_found=verbs)

    return CopyLintResult(
        allowed=False,
        verbs_found=verbs,
        reason="forbidden_completion_verb_without_verified_action_result",
    )
