"""P0-3a — the pure free-form outbound enforcement screen `enforce_free_form_text`.

Three deterministic classes (safe_io wires this into the bridge_post chokepoint):
  1. promise_ban — commitment/guarantee language the deterministic system alone
     may make (guarantee / promise / refund / "free delivery by" / "discount
     applied" …). Principled list derived from the 2026-05-27 lesson
     ("must not invent operational service promises: WhatsApp Delivery, catering
     availability, payment-method claims") + the plan's named examples.
  2. invented_operational_claim — reuses lint_no_unverified_completion (past-tense
     completion verbs about a regulated action).
  3. length_spam_cap — length + repetition sanity.

Verdict is deterministic and side-effect free (offline, like the rest of ccp).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.flyer.customer_copy_policy import (  # noqa: E402
    FREE_FORM_ENFORCEMENT_CLASSES,
    FREE_FORM_MAX_CHARS,
    PROMISE_BAN_VERBS,
    enforce_free_form_text,
)


# ── clean replies PASS ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "Happy to make that weekend flyer! What should it feature — the dishes, the date, and any offer?",
        "Sure — tell me the three menu items you'd like on the poster and I'll get a draft over.",
        "Thanks for reaching out! I can help with flyers, menus, and your account. What do you need?",
        "I'm sorry you're frustrated — I'm here to help. What are you trying to get done?",
    ],
)
def test_clean_composed_replies_pass(text):
    result = enforce_free_form_text(text)
    assert result.passed is True
    assert result.hit_classes == ()
    assert result.classes_checked == FREE_FORM_ENFORCEMENT_CLASSES


# ── promise-ban: red → green with realistic composed replies ────────────────

@pytest.mark.parametrize(
    "text,expected_term_substr",
    [
        ("We guarantee free delivery by Friday for your order.", "guarantee"),
        ("I promise your food will arrive hot.", "promise"),
        ("No problem, the 20% discount applied to your next order.", "discount applied"),
        ("We'll issue a full refund right away.", "refund"),
        ("Satisfaction guaranteed or your money back!", "money back"),
    ],
)
def test_promise_ban_red(text, expected_term_substr):
    result = enforce_free_form_text(text)
    assert result.passed is False
    assert "promise_ban" in result.hit_classes
    values = " ".join(h.value for h in result.hits).lower()
    assert expected_term_substr in values


def test_promise_ban_green_after_rewrite():
    # Same intent, reworded to avoid an operational commitment the system can't make.
    red = "We guarantee your flyer will be delivered by 5pm today."
    green = "I'll get started on your flyer now and send the draft here as soon as it's ready."
    assert enforce_free_form_text(red).passed is False
    assert enforce_free_form_text(green).passed is True


# ── invented-operational-claim class reuses the completion-verb lint ────────

def test_invented_operational_claim_red():
    result = enforce_free_form_text("Your table is booked for Saturday.")
    assert result.passed is False
    assert "invented_operational_claim" in result.hit_classes


def test_invented_operational_claim_suppressed_when_verified():
    # A verified action result (real audit-row evidence) exempts the claims class,
    # mirroring lint_no_unverified_completion semantics.
    text = "Your table is booked for Saturday."
    assert enforce_free_form_text(text).passed is False
    assert enforce_free_form_text(text, has_verified_action_result=True).passed is True


def test_verified_action_result_does_not_exempt_promise_ban():
    # Verified-action exemption is scoped to the claims class only; a forward
    # commitment (guarantee/promise) still fails even with the flag set.
    text = "We guarantee a full refund."
    result = enforce_free_form_text(text, has_verified_action_result=True)
    assert result.passed is False
    assert "promise_ban" in result.hit_classes


# ── length / spam sanity cap ────────────────────────────────────────────────

def test_length_cap_boundary():
    assert enforce_free_form_text("x" * FREE_FORM_MAX_CHARS).passed is True
    over = enforce_free_form_text("x" * (FREE_FORM_MAX_CHARS + 1))
    assert over.passed is False
    assert "length_spam_cap" in over.hit_classes


def test_excessive_line_repetition():
    spam = "\n".join(["BUY NOW BUY NOW"] * 8)
    result = enforce_free_form_text(spam)
    assert result.passed is False
    assert "length_spam_cap" in result.hit_classes


# ── structural guarantees ───────────────────────────────────────────────────

def test_classes_checked_is_stable_and_ordered():
    assert FREE_FORM_ENFORCEMENT_CLASSES == (
        "promise_ban",
        "invented_operational_claim",
        "length_spam_cap",
    )


def test_empty_text_passes():
    result = enforce_free_form_text("")
    assert result.passed is True
    assert result.hit_classes == ()


def test_promise_verbs_list_contains_core_commitments():
    lowered = tuple(v.lower() for v in PROMISE_BAN_VERBS)
    for core in ("guarantee", "promise", "refund"):
        assert core in lowered
