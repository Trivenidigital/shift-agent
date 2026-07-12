"""Regression coverage for the outbound regulated-send lint SILENCE incident.

Prod log 2026-07-12 19:34 (customer MK kitchen CUST0007, project F0222): a
customer replied to an awaiting-approval flyer with a REVISION —

    "Can you highlight veg thali $16.99 similar to what is applied for
     Non-veg thali $20.99"

cf-router routed it correctly (revision=true, binding_source=quoted_message)
and COMPOSED a clarification reply that echoed the customer's word "applied".
Bare "applied" was in FORBIDDEN_COMPLETION_VERBS, so the regulated-send lint
tripped (verb_hits=["applied"]) and safe_io refused the send (rc=2) → the
message was DROPPED → the customer got SILENCE.

Defect 1 (this file): bare "applied" over-matches any echo of the word. Its
only genuine money-claim risk ("discount applied" / "credit applied" /
"coupon applied") is retained as multiword phrases in
FORBIDDEN_COMPLETION_PHRASES, so removing the bare verb fixes the echo
false-positive WITHOUT weakening money safety at the regulated-send lint.

Defect 2 (chokepoint fallback-not-silence) is fcntl-bound and lives in
tests/test_safe_io_bridge_post.py (Linux only). These tests are pure
customer_copy_policy checks and run on every platform.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agents.flyer import customer_copy_policy as policy  # noqa: E402


# Verbatim customer message from the prod incident.
INCIDENT_CUSTOMER_BODY = (
    "Can you highlight veg thali $16.99 similar to what is applied for "
    "Non-veg thali $20.99"
)

# The clarification reply cf-router composes on the revision-requires-
# clarification branch. Mirrors src/plugins/cf-router/hooks.py:4550-4552
# (the "I need one clarification before regenerating:" template + the
# "I saw: <excerpt>" echo of the customer body). If that template diverges,
# test_incident_clarification_template_still_present catches the drift.
INCIDENT_CLARIFICATION_REPLY = (
    "I need one clarification before regenerating: I could not match that "
    "change to the current flyer details. Please send the exact item or "
    "text to change.\n\nI saw: " + INCIDENT_CUSTOMER_BODY
)


# ── Defect 1: bare "applied" removed; echo passes; money phrases still block ──

def test_bare_applied_removed_from_completion_verbs():
    """Bare 'applied' must NOT be in the completion-VERB list — it false-
    positived on any echo. Its money-claim risk moved to the PHRASES list."""
    lowered = {v.lower() for v in policy.FORBIDDEN_COMPLETION_VERBS}
    assert "applied" not in lowered, (
        "bare 'applied' is back in FORBIDDEN_COMPLETION_VERBS — it over-matches "
        "customer echoes (the F0222 SILENCE incident). Money-claim protection "
        "belongs in FORBIDDEN_COMPLETION_PHRASES."
    )


def test_incident_echo_of_applied_passes_lint():
    """The exact customer echo 'what is applied for' must PASS the lint."""
    scan = policy.lint_no_unverified_completion(INCIDENT_CUSTOMER_BODY)
    assert scan.hits == (), (
        f"incident echo still trips the lint: {[h.value for h in scan.hits]}"
    )


def test_money_claim_discount_applied_still_blocks():
    """Genuine money completion claim must STILL block at the regulated lint —
    money safety is not weakened by removing the bare verb."""
    scan = policy.lint_no_unverified_completion("Your 20% discount applied.")
    assert scan.hits, "money claim 'discount applied' no longer blocks"
    assert any("applied" in h.value.lower() for h in scan.hits)


def test_money_claim_credit_applied_still_blocks():
    scan = policy.lint_no_unverified_completion("A $10 credit applied to your account.")
    assert scan.hits, "money claim 'credit applied' no longer blocks"


def test_money_claim_coupon_applied_still_blocks():
    scan = policy.lint_no_unverified_completion("Coupon applied at checkout.")
    assert scan.hits, "money claim 'coupon applied' no longer blocks"


def test_money_claim_refund_processed_still_blocks():
    """'processed' is KEPT (genuine standalone money-completion claim)."""
    scan = policy.lint_no_unverified_completion("Your refund has been processed.")
    assert any(h.value.lower() == "processed" for h in scan.hits)


def test_forbidden_completion_phrases_present_and_money_specific():
    """The retained money-claim phrases are multiword (so they only match a
    genuine money context, never a bare echo)."""
    lowered = {p.lower() for p in policy.FORBIDDEN_COMPLETION_PHRASES}
    assert {"discount applied", "credit applied", "coupon applied"} <= lowered
    for phrase in policy.FORBIDDEN_COMPLETION_PHRASES:
        assert " " in phrase, f"phrase {phrase!r} must be multiword"


def test_kept_verbs_catch_genuine_standalone_claims():
    """Sibling bare verbs KEPT because each catches a genuine standalone
    invented completion claim whose risk is NOT covered by a money phrase."""
    kept = {
        "processed": "Your order has been processed.",
        "sent": "Your flyer has been sent.",
        "scheduled": "Your delivery is scheduled.",
        "posted": "Your payment has been posted.",
        "cancelled": "Your order has been cancelled.",
        "confirmed": "Your booking is confirmed.",
        "booked": "Your slot has been booked.",
        "paid": "Your invoice has been paid.",
        "refunded": "You have been refunded.",
    }
    for verb, text in kept.items():
        scan = policy.lint_no_unverified_completion(text)
        found = {h.value.lower() for h in scan.hits}
        assert verb in found, f"KEPT verb {verb!r} must still block {text!r}"


def test_verified_action_result_suppresses_verbs_and_phrases():
    """has_verified_action_result=True is the evidence-backed escape hatch: it
    exempts BOTH the bare verbs AND the retained money phrases."""
    text = "Your refund has been processed and the discount applied."
    assert policy.lint_no_unverified_completion(
        text, has_verified_action_result=True
    ).hits == ()


# ── Incident end-to-end (Defect 1): the composed clarification reply passes ──

def test_incident_clarification_reply_passes_lint_post_fix():
    """The reconstructed clarification reply the revision path composes for the
    incident message must PASS the lint post-fix (no forbidden verb/phrase),
    so the customer receives it instead of SILENCE."""
    scan = policy.lint_no_unverified_completion(INCIDENT_CLARIFICATION_REPLY)
    assert scan.hits == (), (
        f"incident clarification reply still trips the lint: "
        f"{[h.value for h in scan.hits]}"
    )


def test_incident_clarification_template_still_present():
    """Source-drift guard: the clarification template this test reconstructs
    must still exist in hooks.py (read as text — cf-router is fcntl-bound)."""
    hooks_text = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(
        encoding="utf-8"
    )
    assert "I need one clarification before regenerating:" in hooks_text, (
        "clarification template drifted from hooks.py:4550 — update "
        "INCIDENT_CLARIFICATION_REPLY to match."
    )
    assert 'PROJECT_ACTIONS, "clarification.request"' in hooks_text


def test_all_clarification_request_sends_are_non_regulated():
    """ROOT-FIX invariant (F0222): a clarification is a QUESTION, never a
    completion claim, so EVERY `clarification.request` send must be built with
    `is_regulated_action=False` — the real question then always delivers (never
    lint-refused, never fallback-substituted into acknowledged-limbo). All 9
    sends (not the 1 the review first spotted) are pinned here so a future
    clarification.request that omits the flag regresses the incident class.

    Source-scan (cf-router is fcntl-bound; read as text). Counts every
    `clarification.request` call and asserts each is immediately followed by
    `is_regulated_action=False`."""
    import re

    hooks_text = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(
        encoding="utf-8"
    )
    total = len(re.findall(r'PROJECT_ACTIONS,\s*"clarification\.request"', hooks_text))
    non_regulated = len(
        re.findall(
            r'PROJECT_ACTIONS,\s*"clarification\.request",\s*is_regulated_action=False',
            hooks_text,
        )
    )
    assert total >= 9, f"expected >=9 clarification.request sends, found {total}"
    assert non_regulated == total, (
        f"{total - non_regulated} of {total} clarification.request sends are NOT "
        f"is_regulated_action=False — a clarification QUESTION must never be a "
        f"regulated completion claim (F0222 acknowledged-limbo risk)."
    )
