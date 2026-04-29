"""Owner-message parser unit tests for Agent #21.

Tests the strict two-anchor regex set from plan §4e + design v2 amendments.
Pure-function tests — runs on Windows + Linux (script imports may fail on
Windows due to safe_io's fcntl, so we import the script via importlib with
attribute-injection just for the parser function.)

Reviewer-c HIGH C1: parser must reject bare `force`/`reject`/empty.
Reviewer-c HIGH C2: edge-case nudges (single decimal, comma, $ prefix) covered.
"""
from __future__ import annotations

import os
os.environ.setdefault("EXPENSE_RECEIPTS_DIR", "/tmp/test/")

import importlib.util
import platform
import sys
from pathlib import Path

import pytest

# Subprocess-y bits of the script use fcntl; the parser is pure Python though.
# We load via importlib + suppress __main__ block.
APPLY_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "src" / "agents" / "expense_bookkeeper" / "scripts"
    / "apply-expense-decision"
)

# Skip on Windows because the import chain pulls in safe_io which uses fcntl.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="parser is in apply-expense-decision which imports fcntl-using safe_io",
)


@pytest.fixture(scope="module")
def parse_owner_message():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))
    spec = importlib.util.spec_from_file_location(
        "apply_expense_decision_test", str(APPLY_SCRIPT)
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__name__ = "apply_expense_decision_test"  # suppress __main__
    spec.loader.exec_module(mod)
    return mod.parse_owner_message


# ---- Approve forward (#CODE amount) ----

def test_approve_simple(parse_owner_message):
    r = parse_owner_message("#A47C2 234.50")
    assert r["verb"] == "approve"
    assert r["code"] == "#A47C2"
    assert r["amount_cents"] == 23450
    assert r["force"] is False


def test_approve_with_dollar_prefix(parse_owner_message):
    r = parse_owner_message("#A47C2 $234.50")
    assert r["verb"] == "approve"
    assert r["amount_cents"] == 23450


def test_approve_with_comma(parse_owner_message):
    r = parse_owner_message("#A47C2 1,234.50")
    assert r["amount_cents"] == 123450


def test_approve_with_force(parse_owner_message):
    r = parse_owner_message("#A47C2 234.50 force")
    assert r["verb"] == "approve"
    assert r["force"] is True


def test_approve_force_case_insensitive(parse_owner_message):
    r = parse_owner_message("#A47C2 234.50 FORCE")
    assert r["force"] is True


# ---- Approve reversed (amount #CODE) ----

def test_approve_reversed_order(parse_owner_message):
    r = parse_owner_message("234.50 #A47C2")
    assert r["verb"] == "approve"
    assert r["amount_cents"] == 23450


def test_approve_reversed_with_dollar(parse_owner_message):
    r = parse_owner_message("$234.50 #A47C2")
    assert r["amount_cents"] == 23450


# ---- Reject ----

def test_reject(parse_owner_message):
    r = parse_owner_message("#A47C2 reject")
    assert r["verb"] == "reject"
    assert r["code"] == "#A47C2"


def test_reject_via_approve_modifier(parse_owner_message):
    """Approve regex with reject modifier also classifies as reject."""
    r = parse_owner_message("#A47C2 234.50 reject")
    assert r["verb"] == "reject"


# ---- Undo ----

def test_undo(parse_owner_message):
    r = parse_owner_message("undo E0042")
    assert r["verb"] == "undo"
    assert r["eid"] == "E0042"
    assert r["force"] is False


def test_undo_force(parse_owner_message):
    r = parse_owner_message("undo E0042 force")
    assert r["verb"] == "undo"
    assert r["force"] is True


def test_undo_case_insensitive(parse_owner_message):
    r = parse_owner_message("UNDO E0042")
    assert r["verb"] == "undo"


# ---- Missing decimals nudge ----

def test_missing_decimals_no_dot(parse_owner_message):
    r = parse_owner_message("#A47C2 234")
    assert r["verb"] == "missing_decimals"
    assert r["code"] == "#A47C2"


def test_missing_decimals_one_decimal(parse_owner_message):
    r = parse_owner_message("#A47C2 234.5")
    assert r["verb"] == "missing_decimals"


# ---- Rejected-as-malformed ----

def test_bare_force_rejected(parse_owner_message):
    """Reviewer-c HIGH C1: bare `force` must NOT match."""
    assert parse_owner_message("force") is None


def test_bare_reject_rejected(parse_owner_message):
    assert parse_owner_message("reject") is None


def test_empty_string_rejected(parse_owner_message):
    assert parse_owner_message("") is None
    assert parse_owner_message("   ") is None


def test_bare_undo_rejected(parse_owner_message):
    """`undo` alone (no E####) is malformed."""
    assert parse_owner_message("undo") is None


def test_random_text_rejected(parse_owner_message):
    assert parse_owner_message("hello world") is None


def test_code_only_rejected(parse_owner_message):
    """`#A47C2` alone has no verb → malformed."""
    r = parse_owner_message("#A47C2")
    # Could match missing_decimals if regex is too permissive — verify it doesn't
    # The strict missing_decimals regex requires whitespace + digits.
    assert r is None


def test_invalid_code_alphabet_rejected(parse_owner_message):
    """`I` is not in the alphabet."""
    assert parse_owner_message("#IIIII 234.50") is None


def test_too_short_code_rejected(parse_owner_message):
    assert parse_owner_message("#A47 234.50") is None


def test_three_decimals_rejected(parse_owner_message):
    """3+ decimals shouldn't match either approve regex."""
    assert parse_owner_message("#A47C2 234.567") is None
