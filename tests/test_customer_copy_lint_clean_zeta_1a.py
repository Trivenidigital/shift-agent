"""PR-ζ.1a — customer-copy lint-clean invariants.

The PR-ζ chokepoint lint refuses sends that contain forbidden completion
verbs when context is regulated + unverified. After PR-ζ.1b removes
actions.py + hooks.py from SAFE_IO_NULL_CONTEXT_ALLOWLIST, the lint will
run on these customer-facing replies. ζ.1a closes the live exposure today
(cancelled customers receive the forbidden verb `cancelled` in their
reply); this test pins the invariant.

Schema-driven via typing.get_args() on the actual FlyerCustomerProfile
status Literal — if a future PR adds a new status value, this test
auto-discovers it and verifies the reply is lint-clean. If the new status
happens to itself be a forbidden completion verb (e.g. someone adds
`refunded`), the test fails until `flyer_customer_not_active_reply` is
updated with an explicit lint-clean branch.
"""
from __future__ import annotations

import platform
import re
import sys
from pathlib import Path
from typing import get_args

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
FLYER_DIR = REPO / "src" / "agents" / "flyer"
CF_ROUTER_DIR = REPO / "src" / "plugins" / "cf-router"
sys.path.insert(0, str(PLATFORM_DIR))
sys.path.insert(0, str(FLYER_DIR))
sys.path.insert(0, str(CF_ROUTER_DIR))

# cf-router imports use fcntl-bound modules through the safe_io chain on
# Linux; mirror the existing test convention.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="cf-router imports use fcntl-bound modules (Linux only)",
)


def _flyer_status_literal_values() -> tuple[str, ...]:
    """Schema-driven enumeration of every reachable FlyerCustomerProfile
    status. Drives the lint test parametrize so a future schema addition
    fails fast if it would emit a forbidden-verb customer reply. Mirrors
    the get_args precedent at schemas.py:616 + tests/test_flyer_intent_layer.py."""
    from schemas import FlyerCustomerProfile
    annotation = FlyerCustomerProfile.model_fields["status"].annotation
    return get_args(annotation)


_STATUS_VALUES = _flyer_status_literal_values()


@pytest.mark.parametrize("status", _STATUS_VALUES)
def test_flyer_customer_not_active_reply_lint_clean(status: str) -> None:
    """For every reachable status value, the reply must contain no
    forbidden completion verbs under has_verified_action_result=False."""
    from customer_copy_policy import lint_no_unverified_completion
    from actions import flyer_customer_not_active_reply
    reply = flyer_customer_not_active_reply({"status": status})
    scan = lint_no_unverified_completion(reply, has_verified_action_result=False)
    assert scan.hits == (), (
        f"status={status!r} produced forbidden completion verbs: "
        f"{[h.value for h in scan.hits]}; reply={reply!r}"
    )


def test_flyer_customer_not_active_reply_default_branch_lint_clean() -> None:
    """The default branch (status missing or unexpected) must be lint-clean.
    Defends against legacy customer-dict shapes or future schema additions
    where the status name might itself be a forbidden completion verb."""
    from customer_copy_policy import lint_no_unverified_completion
    from actions import flyer_customer_not_active_reply
    for synthetic in [
        {},
        {"status": ""},
        {"status": "not_active"},
        {"status": "trial_ended"},  # not in Literal today; tests forward-compat
        {"status": "refunded"},     # IS a forbidden verb; tests defensive fallback
        {"status": "completed"},    # IS a forbidden verb
        {"status": "cancelled"},    # IS in Literal; tests cancelled branch
    ]:
        reply = flyer_customer_not_active_reply(synthetic)
        scan = lint_no_unverified_completion(reply, has_verified_action_result=False)
        assert scan.hits == (), (
            f"customer={synthetic!r} produced forbidden completion verbs: "
            f"{[h.value for h in scan.hits]}; reply={reply!r}"
        )


def test_payment_pending_custom_branch_preserved() -> None:
    """The payment_pending branch was always lint-clean and carries
    customer-specific copy. PR-ζ.1a must not regress it to the generic
    fallback. Pins the specific phrase that distinguishes the custom
    branch from the generic one."""
    from actions import flyer_customer_not_active_reply
    reply = flyer_customer_not_active_reply({"status": "payment_pending"})
    assert "waiting for payment confirmation" in reply, (
        "payment_pending custom branch was lost — regression to generic fallback. "
        f"reply={reply!r}"
    )


def test_cancelled_branch_has_specific_copy() -> None:
    """Reviewer MINOR fix: the cancelled branch must carry the
    customer-specific "restart setup" instruction rather than the generic
    "Contact Support" fallback. A future refactor that collapses the
    cancelled branch into the generic fallback would pass lint but
    silently degrade customer guidance — this test catches that."""
    from actions import flyer_customer_not_active_reply
    reply = flyer_customer_not_active_reply({"status": "cancelled"})
    assert "restart setup" in reply, (
        "cancelled branch lost its customer-specific instruction. "
        f"reply={reply!r}"
    )
    # AND the rewritten body uses the lint-clean phrasing:
    assert "no longer active" in reply, (
        "cancelled branch did not emit the lint-clean rewrite. "
        f"reply={reply!r}"
    )


def test_change_plan_fallback_in_hooks_py_is_lint_clean() -> None:
    """Reviewer MAJOR fix: read hooks.py source rather than pinning a
    literal string in the test. A regression in hooks.py:1793 (someone
    re-introducing `processed` or a different forbidden verb) is now
    caught here instead of by the chokepoint at runtime."""
    from customer_copy_policy import lint_no_unverified_completion
    hooks_text = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")

    # Locate the change_plan refusal fallback string. Look for the
    # surrounding "We've logged it for operator follow-up" sentinel which is
    # unique to this site, then extract the surrounding string concatenation.
    sentinel = "We've logged it for operator follow-up"
    assert sentinel in hooks_text, (
        f"change_plan fallback sentinel missing from hooks.py — has the "
        f"fallback been removed? Re-read hooks.py around line 1793."
    )

    # Confirm the old forbidden-verb phrasing is gone:
    assert "couldn't be processed" not in hooks_text, (
        "regression: hooks.py reintroduced the forbidden 'couldn't be "
        "processed' phrasing in the change_plan fallback. Use lint-clean "
        "alternative."
    )

    # Confirm the new lint-clean phrasing is present:
    assert "weren't able to set up your plan change" in hooks_text, (
        "PR-ζ.1a rewrite missing from hooks.py — the change_plan fallback "
        "must use 'weren't able to set up' phrasing."
    )

    # Bonus: lint the new phrasing literal to confirm word-by-word cleanness.
    # If the phrase is ever edited, this assertion runs the lint over the
    # actual edited form (still source-driven via the regex extraction below).
    pattern = re.compile(
        r'"(Flyer Studio\\n[-]+\\n[^"]*' + re.escape(sentinel) + r'[^"]*?)"',
        re.DOTALL,
    )
    # Note: the fallback is a multi-string concatenation in hooks.py source,
    # not a single literal. The above pattern may not capture the full
    # concatenated body. The sentinel + absence + presence checks above
    # provide the primary guarantee; the regex-extract is best-effort.
    matches = pattern.findall(hooks_text)
    for body in matches:
        scan = lint_no_unverified_completion(body, has_verified_action_result=False)
        assert scan.hits == (), (
            f"change_plan fallback body in hooks.py source contains forbidden "
            f"verbs: {[h.value for h in scan.hits]}; body={body!r}"
        )


def test_status_literal_schema_drift_detection() -> None:
    """Asserts FlyerCustomerProfile.status Literal still matches the set
    this PR was designed against. If a future PR adds a new status value,
    this test fails and forces the developer to add an explicit branch in
    flyer_customer_not_active_reply (or verify the defensive fallback
    produces lint-clean copy for the new value)."""
    expected = {"payment_pending", "trial", "active", "suspended", "cancelled"}
    actual = set(_STATUS_VALUES)
    assert actual == expected, (
        f"FlyerCustomerProfile.status Literal drifted from PR-ζ.1a's tested "
        f"set. Expected: {expected}. Actual: {actual}. Update "
        f"flyer_customer_not_active_reply if any new status would emit a "
        f"forbidden completion verb."
    )
