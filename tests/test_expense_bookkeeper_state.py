"""State-machine + transition-table exhaustive tests for Agent #21.

Per drift rules (Part 1 testing pattern), these are pure-function tests —
runs on Windows + Linux. Reviewer-d HIGH D4: enumerate every valid transition
+ every invalid transition + terminal-state empty check.
"""
from __future__ import annotations

import os
os.environ.setdefault("EXPENSE_RECEIPTS_DIR", "/tmp/test/")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

import pytest

from schemas import (
    EXPENSE_TRANSITIONS,
    EXPENSE_TERMINAL_STATUSES,
    is_expense_transition_allowed,
)


ALL_STATES = list(EXPENSE_TRANSITIONS.keys())


def _all_pairs():
    """Cartesian product of (src, tgt) for all 8x8 = 64 state pairs."""
    return [(s, t) for s in ALL_STATES for t in ALL_STATES]


def _valid_pairs():
    return [(s, t) for s in ALL_STATES for t in EXPENSE_TRANSITIONS[s]]


def _invalid_pairs():
    valid = set(_valid_pairs())
    return [p for p in _all_pairs() if p not in valid]


@pytest.mark.parametrize("src,tgt", _valid_pairs())
def test_valid_transitions_succeed(src, tgt):
    assert is_expense_transition_allowed(src, tgt), (
        f"valid transition {src} → {tgt} rejected"
    )


@pytest.mark.parametrize("src,tgt", _invalid_pairs())
def test_invalid_transitions_rejected(src, tgt):
    assert not is_expense_transition_allowed(src, tgt), (
        f"invalid transition {src} → {tgt} allowed"
    )


@pytest.mark.parametrize("terminal", list(EXPENSE_TERMINAL_STATUSES))
def test_terminal_states_have_no_outbound(terminal):
    assert EXPENSE_TRANSITIONS[terminal] == frozenset(), (
        f"terminal {terminal} should have empty transitions, "
        f"got {EXPENSE_TRANSITIONS[terminal]}"
    )


def test_known_valid_transition_count():
    """Sanity: 11 valid forward transitions.

    Breakdown:
      EXTRACTING → AWAITING_OWNER_APPROVAL, REJECTED, EXPIRED (3)
      AWAITING_OWNER_APPROVAL → APPROVED_PENDING_PUSH, REJECTED, EXPIRED (3)
      APPROVED_PENDING_PUSH → PUSHED, PUSH_FAILED (2)
      PUSH_FAILED → APPROVED_PENDING_PUSH, REJECTED (2)
      PUSHED → REVERSED (1)
      REVERSED, REJECTED, EXPIRED → (0)
    """
    valid = _valid_pairs()
    assert len(valid) == 11, f"expected 11 valid transitions, got {len(valid)}"


def test_known_terminal_states():
    """STRICT terminal = no outbound transitions. PUSHED is NOT here
    because `undo` to REVERSED is still possible within the window."""
    expected = {"REVERSED", "REJECTED", "EXPIRED"}
    assert EXPENSE_TERMINAL_STATUSES == frozenset(expected)


def test_all_states_covered():
    """Every state in the type appears in transition table."""
    expected = {
        "EXTRACTING", "AWAITING_OWNER_APPROVAL", "APPROVED_PENDING_PUSH",
        "PUSHED", "PUSH_FAILED", "REVERSED", "REJECTED", "EXPIRED",
    }
    assert set(EXPENSE_TRANSITIONS.keys()) == expected
