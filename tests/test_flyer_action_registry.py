"""PR-δ 2026-05-26 — tests for `mutation_class` field on FlyerActionDefinition.

Locks the schema-completeness + classification correctness invariants. PR-δ
ships the FIELD only; rollback handler wiring + audit-fail-closed behavior
are later (PR-ζ + a follow-up).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

# Module imports both for "flat" deploy layout and source-tree layout.
try:
    from agents.flyer import action_registry as registry  # type: ignore
except Exception:
    sys.path.insert(0, str(SRC / "agents" / "flyer"))
    import action_registry as registry  # type: ignore


VALID_MUTATION_CLASSES = {"local_reversible", "external_irreversible"}


def test_pr_zeta_1b_flyer_action_definition_mutation_class_optional():
    """PR-ζ.1b 2026-05-26 — mutation_class is now Optional (default None) so
    registry entries without a concrete rollback consumer (project actions,
    informational fallback replies) can omit it without making a downstream-
    consumed semantic claim. Constructing without mutation_class no longer
    raises; the field defaults to None.

    Supersedes the original PR-δ "mutation_class is required" invariant.
    Existing ACCOUNT_ACTIONS entries that explicitly declare mutation_class
    continue to do so — see test_pr_delta_mutating_account_actions_classified
    below for the entries-must-classify invariant on actions that DO mutate.
    """
    definition = registry.FlyerActionDefinition(
        action_id="flyer.test.noop",
        command="noop",
        domain="account",
        effect="read",
        # mutation_class deliberately omitted — must default to None.
    )
    assert definition.mutation_class is None


def test_pr_delta_flyer_action_definition_accepts_both_mutation_classes():
    """Both valid Literal values must construct without error."""
    for value in VALID_MUTATION_CLASSES:
        definition = registry.FlyerActionDefinition(
            action_id="flyer.test.classified",
            command="classified",
            domain="account",
            effect="read",
            mutation_class=value,  # type: ignore[arg-type]
        )
        assert definition.mutation_class == value


def test_pr_delta_mutating_account_actions_classified():
    """PR-ζ.1b 2026-05-26 — refines PR-δ invariant: mutation_class is now
    Optional (None default), but ACCOUNT_ACTIONS entries with effect="write"
    or effect="payment_request" MUST still declare a valid Literal value.
    Read-only entries (informational fallback replies, status displays) MAY
    omit mutation_class because they don't mutate state.

    This preserves the original PR-δ classification discipline for the
    actions that actually matter for rollback semantics, without forcing
    informational entries to make hollow claims.
    """
    assert registry.ACCOUNT_ACTIONS, "ACCOUNT_ACTIONS must not be empty"
    for command, definition in registry.ACCOUNT_ACTIONS.items():
        assert hasattr(definition, "mutation_class"), f"{command} missing mutation_class attribute"
        if definition.effect in ("write", "payment_request"):
            assert definition.mutation_class in VALID_MUTATION_CLASSES, (
                f"{command} (effect={definition.effect!r}) MUST declare a "
                f"mutation_class; got {definition.mutation_class!r}"
            )
        else:
            # effect="read" — informational; mutation_class optional.
            assert (
                definition.mutation_class is None
                or definition.mutation_class in VALID_MUTATION_CLASSES
            ), (
                f"{command} has invalid mutation_class={definition.mutation_class!r}"
            )


def test_pr_delta_read_only_actions_are_local_reversible():
    """Read-only actions (status / help / plan_menu) are trivially
    local_reversible — no state mutation happens, so rollback is a no-op."""
    for command in ("status", "help", "plan_menu"):
        definition = registry.ACCOUNT_ACTIONS[command]
        assert definition.effect == "read", f"{command} expected effect=read, got {definition.effect}"
        assert definition.mutation_class == "local_reversible", (
            f"{command} read-only action must be local_reversible, got {definition.mutation_class}"
        )


def test_pr_delta_file_write_actions_are_local_reversible():
    """File-write account/preference actions (starter_prompt_mode, business
    name/phone/whatsapp/authorized-requester updates) mutate JSON state only.
    They are reversible by re-update — no external service is involved."""
    file_write_actions = (
        "starter_prompt_mode",
        "update_business_name",
        "add_authorized",
        "remove_authorized",
        "update_phone",
        "update_whatsapp",
    )
    for command in file_write_actions:
        definition = registry.ACCOUNT_ACTIONS[command]
        assert definition.effect == "write", f"{command} expected effect=write, got {definition.effect}"
        assert definition.mutation_class == "local_reversible", (
            f"{command} file-write action must be local_reversible, got {definition.mutation_class}"
        )


def test_pr_delta_change_plan_is_external_irreversible():
    """change_plan triggers a payment request (Stripe/Razorpay/manual).
    Once the provider commits the charge, local state rollback alone cannot
    undo the customer's payment — would need a refund flow. PR-ζ +
    audit-fail-closed wiring uses this classification to route customer copy
    to "under operator review" instead of "no change has been made"."""
    definition = registry.ACCOUNT_ACTIONS["change_plan"]
    assert definition.effect == "payment_request"
    assert definition.requires_payment is True
    assert definition.mutation_class == "external_irreversible"


def test_pr_delta_only_change_plan_is_external_irreversible():
    """Lock the classification census: exactly one action
    (change_plan) is external_irreversible. If a future action becomes
    external_irreversible, this test fails until it's updated — forcing the
    operator to consciously add the new rollback semantics."""
    external_irreversible_actions = {
        command for command, definition in registry.ACCOUNT_ACTIONS.items()
        if definition.mutation_class == "external_irreversible"
    }
    assert external_irreversible_actions == {"change_plan"}, (
        f"Expected only change_plan to be external_irreversible, got {external_irreversible_actions}"
    )


def test_pr_delta_mutation_class_literal_values_match_module_type():
    """The FlyerActionMutationClass Literal type must equal the canonical set
    of valid values. Locks the type-export contract for future PR-ζ readers."""
    # This validates the Literal type at runtime by checking its __args__
    # (a Python typing implementation detail but the only stable way to
    # introspect a Literal at runtime).
    from typing import get_args
    declared = set(get_args(registry.FlyerActionMutationClass))
    assert declared == VALID_MUTATION_CLASSES
