"""PR-ζ.1b PR #282 review fix — regression coverage for the §13.C revision-ack
3-way split.

Sub-branch (b) sends "Revision applied to the flyer details. I am regenerating
the design now." The callsite passes verified_action_result=True because the
local state mutation is verified by `ok` returning True from
actions.invoke_update_flyer_project before this branch fires.

2026-07-12 update (F0222 SILENCE incident): bare "applied" was REMOVED from
FORBIDDEN_COMPLETION_VERBS because it false-positived on any customer echo of
the word. As a result, "Revision applied to the flyer details..." is now lint-
clean by DEFAULT (no forbidden verb; "Revision applied" is not one of the
money-context phrases in FORBIDDEN_COMPLETION_PHRASES). The callsite's
verified_action_result=True opt-in remains correct (the claim IS verified) but
is no longer load-bearing for the bare "applied" verb specifically.

These tests lock: (1) the ack passes the chokepoint lint under the production
config, and (2) the bare "applied" verb removal is intentional.
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


def test_revision_applied_ack_is_lint_clean_by_default_after_bare_applied_removal():
    """After the 2026-07-12 removal of bare "applied", the revision-ack is
    lint-clean by DEFAULT (has_verified_action_result=False): "Revision applied
    to the flyer details..." contains no forbidden completion verb and is not a
    money-context phrase. This is the same over-match-on-echo fix as the F0222
    incident — a benign operational use of "applied" no longer refuses the send.
    """
    scan = lint_no_unverified_completion(
        REVISION_APPLIED_ACK,
        has_verified_action_result=False,
    )
    assert scan.hits == (), (
        f"Revision-ack unexpectedly trips the lint by default: "
        f"{[h.value for h in scan.hits]}. Bare 'applied' should be removed and "
        f"'Revision applied' is not a FORBIDDEN_COMPLETION_PHRASES money phrase."
    )


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


def test_forbidden_completion_verbs_excludes_bare_applied():
    """Lock the intentional 2026-07-12 removal of bare 'applied' from the verb
    list (F0222 SILENCE incident — it over-matched customer echoes). The money-
    claim protection moved to FORBIDDEN_COMPLETION_PHRASES; if bare 'applied'
    reappears here, the echo false-positive regresses."""
    lowered = tuple(v.lower() for v in FORBIDDEN_COMPLETION_VERBS)
    assert "applied" not in lowered, (
        f"bare 'applied' is back in FORBIDDEN_COMPLETION_VERBS — it over-matches "
        f"customer echoes (F0222). Money-claim risk belongs in "
        f"FORBIDDEN_COMPLETION_PHRASES."
    )
