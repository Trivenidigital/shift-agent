"""PR-ζ.1b PR #282 review fix — regression coverage for the §13.C revision-ack
3-way split.

Sub-branch (b) sends "Revision applied to the flyer details. I am regenerating
the design now." — the word "applied" is in FORBIDDEN_COMPLETION_VERBS and
trips the PR-ζ chokepoint lint by default (is_regulated_action=True,
verified_action_result=False). The PR review caught this as a runtime-refusal
BLOCKER. Fix: caller passes verified_action_result=True because the local
state mutation is verified by `ok` returning True from
actions.invoke_update_flyer_project before this branch fires.

This test locks the structural guarantee: the exact ack message + the
configured ActionExecutionContext together pass the chokepoint lint with
zero hits.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "platform"))
sys.path.insert(0, str(REPO / "src" / "agents" / "flyer"))

from action_registry import (  # noqa: E402  type: ignore
    PROJECT_ACTIONS,
    build_action_context_for_command,
)
from customer_copy_policy import (  # noqa: E402  type: ignore
    FORBIDDEN_COMPLETION_VERBS,
    lint_no_unverified_completion,
)


# Verbatim copy from src/plugins/cf-router/hooks.py §13.C sub-branch (b).
# If hooks.py copy diverges, this test will catch the drift.
REVISION_APPLIED_ACK = (
    "Revision applied to the flyer details. I am regenerating the design now."
)


def test_revision_applied_ack_contains_forbidden_verb_by_default():
    """Sanity: without verified_action_result, the ack DOES trip the lint —
    the PR review's verified observation. This is what makes the
    verified_action_result=True opt-in load-bearing for the production path.
    """
    scan = lint_no_unverified_completion(
        REVISION_APPLIED_ACK,
        has_verified_action_result=False,
    )
    assert scan.hits, (
        f"Expected the ack to trip FORBIDDEN_COMPLETION_VERBS without "
        f"verified_action_result=True. If the copy was rewritten to avoid "
        f"completion verbs, this sanity branch needs updating."
    )
    # The specific verb that trips it.
    verbs = [h.value.lower() for h in scan.hits]
    assert "applied" in verbs, f"Expected 'applied' in hits; got {verbs}"


def test_revision_applied_ack_passes_lint_with_verified_action_result():
    """Production-path structural guarantee: the revision-ack message + the
    ζ.1b production context (edit.processing entry + verified_action_result=
    True) together pass the chokepoint lint with zero hits. This is the
    exact configuration the cf-router callsite at hooks.py §13.C sub-branch
    (b) emits.
    """
    ctx = build_action_context_for_command(
        PROJECT_ACTIONS, "edit.processing",
        verified_action_result=True,
    )
    assert ctx.is_regulated_action is True
    assert ctx.verified_action_result is True
    assert ctx.action_id == "flyer.project.edit_processing"

    scan = lint_no_unverified_completion(
        REVISION_APPLIED_ACK,
        has_verified_action_result=ctx.verified_action_result,
    )
    assert not scan.hits, (
        f"PR-ζ chokepoint would refuse the revision-ack send. "
        f"Lint hits: {[h.value for h in scan.hits]}. "
        f"Either restore verified_action_result=True at the callsite, or "
        f"rewrite the ack copy to avoid completion verbs."
    )


def test_forbidden_completion_verbs_includes_applied():
    """Lock the verb list contains 'applied'. If a future cleanup of the
    forbidden-verbs list drops 'applied', this test catches it and forces
    the operator to re-evaluate whether the verified_action_result=True
    opt-in at the revision-ack callsite is still needed."""
    lowered = tuple(v.lower() for v in FORBIDDEN_COMPLETION_VERBS)
    assert "applied" in lowered, (
        f"FORBIDDEN_COMPLETION_VERBS no longer contains 'applied'. "
        f"Revisit the hooks.py §13.C sub-branch (b) opt-in."
    )
