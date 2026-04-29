"""PR-D2 commit 4: retry-state-machine + idempotent_replay short-circuit.

Static checks pin design v2 §4.6 behavior:
- Matcher widened to AWAITING_OWNER_APPROVAL OR OWNER_APPROVED (approve only).
- OWNER_EDITED NOT in the matcher (R2-H1 money-moving correctness fix).
- Tail-scan for catering_quote_sent fires inside the FIRST LEADS_LOCK.
- Idempotent_replay short-circuit: if quote_sent exists, advance status
  if still OWNER_APPROVED + emit recovery row + return EXIT_OK.
- Reject/edit retry path keeps the deployed AWAITING_OWNER_APPROVAL matcher
  (no widening for non-approve decisions).
"""
from __future__ import annotations
from pathlib import Path

import pytest


_APPLY_SCRIPT = (Path(__file__).resolve().parent.parent
                 / "src" / "agents" / "catering" / "scripts"
                 / "apply-catering-owner-decision")


@pytest.fixture(scope="module")
def script_text() -> str:
    return _APPLY_SCRIPT.read_text(encoding="utf-8")


def test_approve_matcher_widened_to_owner_approved(script_text: str):
    """Approve-decision matcher accepts AWAITING_OWNER_APPROVAL OR OWNER_APPROVED.
    OWNER_APPROVED is the row 3+4 case (retry / self-heal)."""
    # Look for the widened matcher inside the approve branch
    assert 'l.status in ("AWAITING_OWNER_APPROVAL", "OWNER_APPROVED")' in script_text


def test_owner_edited_not_in_approve_matcher(script_text: str):
    """R2-H1 money-moving correctness: OWNER_EDITED must NOT be in the
    approve-retry matcher. Owner sent edit, retry-approve with same code
    would ship un-edited quote to customer.

    OWNER_EDITED IS expected to appear elsewhere in the file (e.g., in
    decision_map["edit"]); we only check it's not in any approve-matcher
    tuple. The matcher line must match exactly the design v2 §4.6 wording."""
    matcher_line = 'l.status in ("AWAITING_OWNER_APPROVAL", "OWNER_APPROVED")'
    assert matcher_line in script_text
    # Forbidden 3-tuple variant
    forbidden = 'l.status in ("AWAITING_OWNER_APPROVAL", "OWNER_APPROVED", "OWNER_EDITED")'
    assert forbidden not in script_text


def test_no_owner_edited_self_heal_branch(script_text: str):
    """Per design v2 §9.2 R2-H1: there must be no row-4-style self-heal
    branch keyed on OWNER_EDITED. Self-heal applies only to OWNER_APPROVED."""
    # No code path checks `lead.status == "OWNER_EDITED"` for retry resume
    assert 'lead.status == "OWNER_EDITED"' not in script_text
    assert 'lead.status in ("OWNER_EDITED"' not in script_text


def test_reject_edit_matcher_unchanged(script_text: str):
    """Reject/edit decisions retain the deployed AWAITING_OWNER_APPROVAL-only
    matcher. Retry-reject after approve = undefined; correctly EXIT_NOT_FOUND."""
    # The else branch for non-approve uses the narrow matcher
    assert 'l.status == "AWAITING_OWNER_APPROVAL"' in script_text


def test_quote_sent_tail_scan_inside_first_leadslock(script_text: str):
    """Tail-scan for quote_sent fires inside the first LEADS_LOCK block,
    before status mutation, per R2-H-2 lock-and-write contract."""
    first_lock_idx = script_text.find("with FileLock(LEADS_LOCK):")
    assert first_lock_idx != -1
    tail_scan_idx = script_text.find("_tail_scan_quote_sent(LOG_PATH, lead.lead_id)")
    assert tail_scan_idx != -1
    # Should be after first lock acquire and before the bridge POST call
    bridge_idx = script_text.find("ok, mid_or_err = _bridge_post(")
    assert first_lock_idx < tail_scan_idx < bridge_idx


def test_idempotent_replay_emits_recovered_status_change(script_text: str):
    """Row 1 idempotent_replay: if quote_sent exists + status=OWNER_APPROVED,
    advance to SENT_TO_CUSTOMER + emit CateringLeadStatusChange with
    reason='idempotent_replay_recovered'."""
    assert 'idempotent_replay_recovered' in script_text
    assert 'idempotent_replay": True' in script_text


def test_idempotent_replay_returns_exit_ok(script_text: str):
    """Row 1 short-circuits with EXIT_OK before any bridge POST."""
    # The idempotent_replay block should contain 'return EXIT_OK'
    replay_idx = script_text.find('"idempotent_replay": True')
    assert replay_idx != -1
    # EXIT_OK return must follow within the same block
    block = script_text[replay_idx:replay_idx + 500]
    assert "return EXIT_OK" in block


def test_tail_scan_quote_sent_called_with_lead_id(script_text: str):
    """The tail-scan invocation passes lead.lead_id (the resolved lead from
    the matcher), not a placeholder."""
    # Find the quote_sent_existing assignment in the active branch
    assert "_tail_scan_quote_sent(LOG_PATH, lead.lead_id)" in script_text
