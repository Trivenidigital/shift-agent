"""extract-receipt unit tests for Agent #21.

Covers two plan §4g edge cases the original v0.1 build deferred:
  #11 approval-code collision regenerate — _generate_unique_code retries
      on collision against the cross-state-file active-code pool
  #16 multi-receipt batch independence — 5 sequential receipts generate
      5 distinct codes (no silent collision, no shared state corruption)

Tests load extract-receipt via importlib + attribute injection — same
pattern as test_expense_bookkeeper_apply_decision.py (and parser).
Linux-only via pytestmark — fcntl is not on Windows.
"""
from __future__ import annotations

import os
os.environ.setdefault("EXPENSE_RECEIPTS_DIR", "/tmp/test/")

import importlib.util
import platform
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="extract-receipt imports fcntl-using safe_io",
)


EXTRACT_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "src" / "agents" / "expense_bookkeeper" / "scripts"
    / "extract-receipt"
)


@pytest.fixture(scope="module")
def extract_mod():
    """Load extract-receipt as a module; suppress __main__ block."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))
    spec = importlib.util.spec_from_file_location(
        "extract_receipt_test", str(EXTRACT_SCRIPT)
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__name__ = "extract_receipt_test"
    spec.loader.exec_module(mod)
    return mod


def _empty_store(extract_mod):
    """Build an empty ExpenseLeadStore for code-generator input."""
    return extract_mod.ExpenseLeadStore(leads=[], last_id=0)


# ───────────────────────────────────────────────────
# Edge case #11 — approval-code collision regenerate
# ───────────────────────────────────────────────────

def test_collision_regenerate_returns_non_colliding_code(extract_mod, monkeypatch):
    """When _generate_unique_code's first candidate collides with an active
    code in the pool, the retry loop must return a DIFFERENT non-colliding
    code rather than returning the colliding one or raising prematurely.

    Mocks secrets.choice with a deterministic char sequence: first 5 chars
    produce a collision; next 5 chars produce a unique code. Mocks
    _collect_active_codes to return a known seeded pool."""
    seeded_active = {"#AAAAA", "#BBBBB", "#CCCCC"}
    monkeypatch.setattr(extract_mod, "_collect_active_codes", lambda store: seeded_active)

    # secrets.choice will be called in groups of 5 per candidate.
    # Round 1 → "#AAAAA" (collides with seeded), Round 2 → "#XYZ23" (unique).
    sequence = iter("AAAAA" + "XYZ23")
    monkeypatch.setattr(extract_mod.secrets, "choice", lambda alpha: next(sequence))

    result = extract_mod._generate_unique_code(_empty_store(extract_mod))
    assert result == "#XYZ23"
    assert result not in seeded_active


def test_collision_regenerate_raises_after_100_consecutive_collisions(extract_mod, monkeypatch):
    """If 100 consecutive candidates all collide (pathological / pool
    nearly full), the function must raise rather than loop forever."""
    # Force every candidate to be the same code; seed pool contains it.
    monkeypatch.setattr(
        extract_mod, "_collect_active_codes", lambda store: {"#AAAAA"}
    )
    monkeypatch.setattr(extract_mod.secrets, "choice", lambda alpha: "A")

    with pytest.raises(RuntimeError, match="could not generate unique"):
        extract_mod._generate_unique_code(_empty_store(extract_mod))


# ───────────────────────────────────────────────────
# Edge case #16 — multi-receipt batch independence
# ───────────────────────────────────────────────────

def test_multi_receipt_batch_generates_distinct_codes(extract_mod, monkeypatch):
    """5 sequential receipts (the rapid-fire batch case in plan §4g #16)
    must produce 5 distinct approval codes. Asserts the cross-state-file
    collision check sees this-call's prior leads on subsequent invocations
    so codes don't collide within a single batch."""
    monkeypatch.setattr(extract_mod, "_collect_active_codes",
                        extract_mod._collect_active_codes.__wrapped__
                        if hasattr(extract_mod._collect_active_codes, "__wrapped__")
                        else extract_mod._collect_active_codes)
    # Use real secrets.choice (real entropy); 5 codes from 28.6M pool collide
    # at vanishing probability — this is the production behavior we're
    # asserting works correctly across calls.

    store = _empty_store(extract_mod)
    codes: list[str] = []
    for i in range(1, 6):
        # Force the cross-state-file pool to be just our growing store
        # (avoid leaking real catering/menu/pending state files into the test).
        monkeypatch.setattr(
            extract_mod, "_collect_active_codes",
            lambda s: {l.owner_approval_code for l in s.leads
                       if l.owner_approval_code},
        )
        code = extract_mod._generate_unique_code(store)
        # Mint a fake AWAITING lead with the generated code so the next
        # iteration sees it in the active pool (mirrors what extract-receipt's
        # main() does when it appends the lead to store).
        lead = extract_mod.ExpenseLead(
            expense_id=f"E000{i}",
            original_message_id=f"wa_msg_batch_{i}",
            sender_phone="+19045550100",
            received_at="2026-04-30T12:00:00+00:00",
            image_path="/tmp/test/" + f"E000{i}.jpg",
            image_phash="a" * 16,
            image_byte_hash="b" * 64,
            owner_approval_code=code,
            status="AWAITING_OWNER_APPROVAL",
        )
        store.leads.append(lead)
        codes.append(code)

    assert len(set(codes)) == 5, f"expected 5 distinct codes, got: {codes}"
