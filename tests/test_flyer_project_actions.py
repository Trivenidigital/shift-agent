"""Tests for the PR-ζ.1b PROJECT_ACTIONS registry + build_action_context helpers.

Covers commit 1 of PR-ζ.1b: action_registry.py additions.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "platform"))
sys.path.insert(0, str(ROOT / "src" / "agents" / "flyer"))

from action_registry import (  # noqa: E402  type: ignore
    ACCOUNT_ACTIONS,
    PROJECT_ACTIONS,
    FlyerActionDefinition,
    build_action_context,
    build_action_context_for_command,
)


def test_project_actions_keys_match_definition_command():
    """Registry self-consistency: dict key == entry.command."""
    for key, defn in PROJECT_ACTIONS.items():
        assert defn.command == key, f"key {key!r} != command {defn.command!r}"


def test_project_actions_action_ids_globally_unique():
    """No collision between PROJECT_ACTIONS and ACCOUNT_ACTIONS action_ids."""
    project_ids = {defn.action_id for defn in PROJECT_ACTIONS.values()}
    account_ids = {defn.action_id for defn in ACCOUNT_ACTIONS.values()}
    collisions = project_ids & account_ids
    assert not collisions, f"action_id collisions: {collisions}"


def test_account_actions_new_entries_present():
    """ζ.1b adds change_plan_fallback + command_reply + onboarding_progress
    + update_brand_asset to ACCOUNT_ACTIONS (per §13.A + §13.B)."""
    assert "change_plan_fallback" in ACCOUNT_ACTIONS
    assert "command_reply" in ACCOUNT_ACTIONS
    assert "onboarding_progress" in ACCOUNT_ACTIONS
    assert "update_brand_asset" in ACCOUNT_ACTIONS
    assert (
        ACCOUNT_ACTIONS["change_plan_fallback"].action_id
        == "flyer.billing.request_plan_change_fallback"
    )
    assert ACCOUNT_ACTIONS["command_reply"].action_id == "flyer.account.command_reply"
    assert (
        ACCOUNT_ACTIONS["onboarding_progress"].action_id
        == "flyer.account.onboarding_progress"
    )
    assert (
        ACCOUNT_ACTIONS["update_brand_asset"].action_id
        == "flyer.account.update_brand_asset"
    )


def test_project_actions_failure_ack_entries_present():
    """ζ.1b adds generation.failed_ack + finalization.failed_ack to
    PROJECT_ACTIONS (per §13.D). Callers MUST pass is_regulated_action=False
    via the helper; the registry entry exists for action_id attribution only.
    """
    assert "generation.failed_ack" in PROJECT_ACTIONS
    assert "finalization.failed_ack" in PROJECT_ACTIONS
    assert (
        PROJECT_ACTIONS["generation.failed_ack"].action_id
        == "flyer.generation.failed_ack"
    )
    assert (
        PROJECT_ACTIONS["finalization.failed_ack"].action_id
        == "flyer.finalization.failed_ack"
    )
    # Both are local_reversible — failure ack mutates no state.
    assert PROJECT_ACTIONS["generation.failed_ack"].mutation_class == "local_reversible"
    assert PROJECT_ACTIONS["finalization.failed_ack"].mutation_class == "local_reversible"


def test_build_action_context_for_command_is_regulated_action_param():
    """ζ.1b §13.G — build_action_context_for_command accepts is_regulated_action
    kwarg. Default True preserves regulated-action posture for account-command
    paths; explicit False supports queue-state / status / informational acks."""
    # Default: True
    default_ctx = build_action_context_for_command(PROJECT_ACTIONS, "intake.acknowledged")
    assert default_ctx.is_regulated_action is True
    # Explicit False: queue-state ack
    queue_ctx = build_action_context_for_command(
        PROJECT_ACTIONS, "manual_review.queued", is_regulated_action=False,
    )
    assert queue_ctx.is_regulated_action is False
    assert queue_ctx.action_id == "flyer.project.manual_review_queued"
    # Failed-ack entries: callers always pass False
    fail_ctx = build_action_context_for_command(
        PROJECT_ACTIONS, "generation.failed_ack", is_regulated_action=False,
    )
    assert fail_ctx.is_regulated_action is False
    assert fail_ctx.action_id == "flyer.generation.failed_ack"


def test_account_actions_mutation_class_optional_default_none():
    """ζ.1b makes mutation_class Optional on the dataclass; the 2 new
    entries omit it and resolve to None."""
    assert ACCOUNT_ACTIONS["change_plan_fallback"].mutation_class is None
    assert ACCOUNT_ACTIONS["command_reply"].mutation_class is None
    # Existing entry that explicitly set mutation_class still has it.
    assert ACCOUNT_ACTIONS["change_plan"].mutation_class == "external_irreversible"


def test_project_actions_mutation_class_pattern():
    """ζ.1b design REV 3 §3.2 + §13.D — PROJECT_ACTIONS entries default to
    mutation_class=None (no premature rollback claim) EXCEPT the failure-ack
    entries which explicitly declare local_reversible per operator §13.D
    decision (failure acks mutate no state; the explicit declaration captures
    the "no rollback needed" semantic for ζ.2 audit-fail-closed wiring).
    """
    explicit_local_reversible = {"generation.failed_ack", "finalization.failed_ack"}
    for key, defn in PROJECT_ACTIONS.items():
        if key in explicit_local_reversible:
            assert defn.mutation_class == "local_reversible", (
                f"PROJECT_ACTIONS[{key!r}] must declare local_reversible per §13.D; "
                f"got {defn.mutation_class!r}"
            )
        else:
            assert defn.mutation_class is None, (
                f"PROJECT_ACTIONS[{key!r}] declares mutation_class — "
                f"per ζ.1b design REV 3 §3.2 non-failure-ack entries should "
                f"be None until ζ.2 wires concrete rollback consumers."
            )


def test_build_action_context_flat_constructor():
    """Flat constructor round-trips named kwargs into ActionExecutionContext."""
    ctx = build_action_context(
        action_id="flyer.test.flat",
        is_regulated_action=False,
    )
    assert ctx.action_id == "flyer.test.flat"
    assert ctx.is_regulated_action is False
    assert ctx.verified_action_result is False  # default
    assert ctx.mutation_class is None  # default
    assert ctx.audit_row_id is None  # default


def test_build_action_context_for_command_derives_from_registry():
    """Helper pulls action_id + mutation_class from registry entry."""
    ctx = build_action_context_for_command(PROJECT_ACTIONS, "intake.acknowledged")
    assert ctx.action_id == "flyer.project.intake_acknowledged"
    assert ctx.is_regulated_action is True  # default for registry-backed
    assert ctx.verified_action_result is False  # default
    assert ctx.mutation_class is None  # PROJECT_ACTIONS entries omit it


def test_build_action_context_for_command_inherits_mutation_class():
    """For ACCOUNT_ACTIONS entries that DO declare mutation_class, the helper
    propagates it to the resulting ActionExecutionContext."""
    ctx = build_action_context_for_command(ACCOUNT_ACTIONS, "change_plan")
    assert ctx.action_id == "flyer.billing.request_plan_change"
    assert ctx.is_regulated_action is True
    assert ctx.mutation_class == "external_irreversible"


def test_build_action_context_for_command_raises_on_unknown():
    """Unknown command surfaces as KeyError — registry drift, not runtime
    contingency."""
    with pytest.raises(KeyError):
        build_action_context_for_command(PROJECT_ACTIONS, "nonexistent.command")


def test_build_action_context_default_verified_false():
    """Default safety posture: verified_action_result False unless caller
    explicitly flips True after action completion."""
    flat = build_action_context(action_id="x", is_regulated_action=True)
    assert flat.verified_action_result is False
    registry = build_action_context_for_command(PROJECT_ACTIONS, "intake.acknowledged")
    assert registry.verified_action_result is False
