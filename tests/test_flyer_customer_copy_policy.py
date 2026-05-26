from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SELF_EVAL = REPO / "tools" / "flyer-self-evaluation.py"

sys.path.insert(0, str(SRC))

from agents.flyer import customer_copy_policy as policy


def load_self_eval():
    spec = importlib.util.spec_from_file_location("flyer_self_eval_policy_test", SELF_EVAL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_self_eval_and_tests_use_same_customer_copy_policy_constants():
    module = load_self_eval()

    assert module.INTERNAL_COPY_TERMS == policy.BANNED_CUSTOMER_COPY_TERMS
    assert module.STATIC_COPY_SCAN_FUNCTIONS == policy.STATIC_CUSTOMER_COPY_FUNCTIONS


def test_customer_copy_policy_detects_project_ids_internal_terms_and_raw_echo():
    result = policy.scan_customer_text(
        "Project F-0065 was queued with provider=openrouter. Original customer request: make it red",
        raw_request="make it red",
    )

    categories = {hit.category for hit in result.hits}
    assert {"project_id", "internal_term", "raw_request_echo"} <= categories


def test_static_send_literal_scan_catches_hook_local_customer_copy():
    source = """
def helper(actions, chat_id):
    actions.send_flyer_text(
        chat_id,
        "Flyer Studio\\n------------\\nProject F0065 provider reason_code leaked",
    )
"""

    scanned = policy.extract_send_call_literals(source)
    assert "Project F0065 provider reason_code leaked" in scanned
    assert policy.scan_customer_text(scanned).hits


def test_static_send_literal_scan_catches_dynamic_project_id_copy():
    source = """
def helper(actions, chat_id, project_id):
    actions.send_flyer_text(
        chat_id,
        f"Flyer Studio\\n------------\\nProject {project_id} is ready.",
    )
"""

    scanned = policy.extract_send_call_literals(source)
    assert "Project {project_id} is ready" in scanned
    hits = policy.scan_customer_text(scanned).hits
    assert any(hit.category == "project_id" for hit in hits)


# ---- PR-γ 2026-05-26: forbidden completion verbs lint (measure mode) -------
# Pure additive peer to scan_customer_text. Does NOT block any send (no
# chokepoint hookup yet — deferred to PR-ζ). Existing replay tests asserting
# `not scan_customer_text(text).hits` for legitimate Flyer copy continue to
# pass because the new lint function is separate, not a wrapper.


def test_pr_gamma_forbidden_completion_verbs_list_complete():
    """Lock the canonical PR-γ verb list (17 verbs covering completion claims
    for billing/payment/account/schedule/delivery)."""
    expected = {
        "processed", "completed", "upgraded", "downgraded", "changed",
        "confirmed", "sent", "approved", "paid", "posted", "pushed",
        "applied", "scheduled", "booked", "cancelled", "canceled", "refunded",
    }
    assert set(policy.FORBIDDEN_COMPLETION_VERBS) == expected
    # Tuple, not set — preserves declared order for stable test iteration.
    assert isinstance(policy.FORBIDDEN_COMPLETION_VERBS, tuple)


def test_pr_gamma_lint_no_unverified_completion_catches_each_verb():
    """Each forbidden verb in isolation triggers a hit when
    has_verified_action_result=False (the default)."""
    for verb in policy.FORBIDDEN_COMPLETION_VERBS:
        text = f"I have {verb} your request."
        scan = policy.lint_no_unverified_completion(text)
        verbs_found = {hit.value for hit in scan.hits if hit.category == "unverified_completion_verb"}
        assert verb in verbs_found, f"verb {verb!r} must be detected in {text!r}"


def test_pr_gamma_lint_verified_action_result_suppresses_all_hits():
    """When has_verified_action_result=True, lint returns empty scan even if
    the text contains EVERY forbidden verb."""
    text = "I have processed, completed, upgraded, downgraded, changed, confirmed, sent, approved, paid, posted, pushed, applied, scheduled, booked, cancelled, canceled, refunded everything."
    scan = policy.lint_no_unverified_completion(text, has_verified_action_result=True)
    assert scan.hits == ()


def test_pr_gamma_lint_case_insensitive_and_word_boundary():
    """Verb detection is case-insensitive and word-boundary-anchored.
    'approveable' / 'approving' / 'sender' must NOT match because they are
    not the bare verb."""
    # Case-insensitive positive cases
    assert policy.lint_no_unverified_completion("Your request was PROCESSED").hits
    assert policy.lint_no_unverified_completion("plan Confirmed").hits
    assert policy.lint_no_unverified_completion("SENT").hits
    # Word-boundary negative cases — embedded substrings must not trigger
    # because `\b` only matches between word and non-word chars (underscore
    # is a word char in default Python regex).
    assert not policy.lint_no_unverified_completion("This is approveable").hits
    assert not policy.lint_no_unverified_completion("sender").hits
    assert not policy.lint_no_unverified_completion("pushed_to_qa").hits
    assert not policy.lint_no_unverified_completion("approvedbyowner").hits


def test_pr_gamma_lint_deduplicates_per_verb():
    """Repeated verbs in one text return only one hit per distinct verb."""
    text = "We sent your flyer. Your flyer was sent. We sent it again."
    scan = policy.lint_no_unverified_completion(text)
    sent_hits = [hit for hit in scan.hits if hit.value == "sent"]
    assert len(sent_hits) == 1
    assert sent_hits[0].category == "unverified_completion_verb"


def test_pr_gamma_lint_multiple_distinct_verbs():
    """Multiple distinct verbs in one text return one hit per verb."""
    text = "Your plan upgraded and the payment processed successfully."
    scan = policy.lint_no_unverified_completion(text)
    verbs_found = {hit.value for hit in scan.hits if hit.category == "unverified_completion_verb"}
    assert verbs_found == {"upgraded", "processed"}


def test_pr_gamma_lint_empty_or_no_verb_text_returns_empty():
    """Text with no forbidden verbs returns empty CustomerCopyScan."""
    assert policy.lint_no_unverified_completion("").hits == ()
    assert policy.lint_no_unverified_completion(None).hits == ()
    assert policy.lint_no_unverified_completion("Please review the design").hits == ()
    assert policy.lint_no_unverified_completion("Your flyer is ready").hits == ()
    # Word-boundary protection — "approve" alone is in the verb list, but
    # text without the verb in word-boundary form returns no hits
    assert policy.lint_no_unverified_completion("Send your first flyer request now.").hits == ()


def test_pr_gamma_lint_does_not_modify_scan_customer_text_behavior():
    """Regression check: PR-γ adds a NEW function and does NOT change
    `scan_customer_text` behavior. Existing replay tests (in test_flyer_*)
    assert `not scan_customer_text(text).hits` for legitimate Flyer copy
    containing words like 'sent' / 'scheduled' / 'applied'; modifying
    scan_customer_text would break them. This test locks that scan_customer_text
    does NOT flag forbidden completion verbs."""
    text = "Your flyer was sent. Your plan was upgraded. Payment confirmed."
    # scan_customer_text only checks BANNED_CUSTOMER_COPY_TERMS (internal-term
    # leakage), project IDs, and raw_request_echo. It does NOT check forbidden
    # completion verbs.
    scan = policy.scan_customer_text(text)
    verb_hits = [hit for hit in scan.hits if hit.category == "unverified_completion_verb"]
    assert verb_hits == []
    # The new lint function DOES flag them
    lint_scan = policy.lint_no_unverified_completion(text)
    lint_verbs = {hit.value for hit in lint_scan.hits if hit.category == "unverified_completion_verb"}
    assert lint_verbs == {"sent", "upgraded", "confirmed"}


def test_pr_gamma_lint_returns_customer_copy_scan_dataclass():
    """Return type contract: lint_no_unverified_completion returns a
    CustomerCopyScan (same dataclass as scan_customer_text) so future
    callers can polymorphically consume both."""
    scan = policy.lint_no_unverified_completion("Your flyer sent successfully")
    assert isinstance(scan, policy.CustomerCopyScan)
    assert scan.text == "Your flyer sent successfully"
    assert all(isinstance(hit, policy.CustomerCopyHit) for hit in scan.hits)
    # matched_values property works on the returned scan
    assert "sent" in scan.matched_values
