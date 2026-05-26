from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
sys.path.insert(0, str(PLATFORM_DIR))


def test_operational_bridge_copy_allows_forbidden_words_without_action_context():
    from customer_copy_policy import lint_customer_copy

    result = lint_customer_copy("2 scheduled shifts. Quotes sent to customers: 3")

    assert result.allowed
    assert result.verbs_found == ["sent", "scheduled"]


def test_forbidden_completion_verbs_rejected_for_unverified_regulated_action():
    from customer_copy_policy import ActionExecutionContext, lint_customer_copy

    context = ActionExecutionContext(
        is_regulated_action=True,
        verified_action_result=False,
        action_id="flyer.plan_change",
    )

    result = lint_customer_copy("I processed your upgrade to Growth.", action_context=context)

    assert not result.allowed
    assert result.verbs_found == ["processed"]


def test_forbidden_completion_verbs_allowed_with_verified_action_context():
    from customer_copy_policy import ActionExecutionContext, lint_customer_copy

    context = ActionExecutionContext(
        is_regulated_action=True,
        verified_action_result=True,
        action_id="flyer.plan_change.confirmed",
    )

    result = lint_customer_copy("Plan change completed.", action_context=context)

    assert result.allowed
    assert result.verbs_found == ["completed"]


def test_safe_clarification_copy_allows_no_action_context():
    from customer_copy_policy import lint_customer_copy

    result = lint_customer_copy("No plan, payment, or account change has been made.")

    assert result.allowed
    assert result.verbs_found == []


def test_safe_io_bridge_helpers_call_customer_copy_lint_before_network_post():
    safe_io_path = PLATFORM_DIR / "safe_io.py"
    text = safe_io_path.read_text(encoding="utf-8")

    for func_name in ("bridge_post", "bridge_send_media", "bridge_send_cta"):
        start = text.index(f"def {func_name}(")
        next_def = text.find("\ndef ", start + 1)
        body = text[start: next_def if next_def != -1 else len(text)]
        lint_idx = body.find("_lint_bridge_customer_copy(")
        post_idx = body.find("urllib.request.urlopen")

        assert lint_idx != -1, f"{func_name} does not lint customer copy"
        assert post_idx != -1, f"{func_name} has no HTTP POST call in static check"
        assert lint_idx < post_idx, f"{func_name} lints after posting"
