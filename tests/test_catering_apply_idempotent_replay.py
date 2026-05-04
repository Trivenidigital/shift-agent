"""PR-D2 commit 4: retry-state-machine + idempotent_replay short-circuit.

Static checks pin design v2 §4.6 behavior, updated for PR-CF1's
CUSTOMER_FINALIZED status addition (the customer-finalize flow):

- Approve-decision matcher accepts AWAITING_OWNER_APPROVAL,
  CUSTOMER_FINALIZED (PR-CF1), and OWNER_APPROVED (retry self-heal).
- OWNER_EDITED NOT in the approve matcher (R2-H1 money-moving fix).
- Reject/edit matcher accepts AWAITING_OWNER_APPROVAL +
  CUSTOMER_FINALIZED only — NO OWNER_APPROVED (retry-reject after
  approve is undefined; correctly EXIT_NOT_FOUND).
- Tail-scan for catering_quote_sent fires inside the FIRST LEADS_LOCK.
- Idempotent_replay short-circuit: if quote_sent exists, advance status
  if still OWNER_APPROVED + emit recovery row + return EXIT_OK.

Source-text checks normalize whitespace so multi-line matcher formatting
(deployed style) and single-line form both pass. We pin SEMANTIC
invariants (which statuses are/aren't accepted) rather than exact
formatting, which would drift on every refactor.
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest


_APPLY_SCRIPT = (Path(__file__).resolve().parent.parent
                 / "src" / "agents" / "catering" / "scripts"
                 / "apply-catering-owner-decision")


@pytest.fixture(scope="module")
def script_text() -> str:
    return _APPLY_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script_normalized(script_text: str) -> str:
    """Whitespace-normalized source: collapse runs of whitespace + comments
    so multi-line matchers compare against single-line patterns."""
    # Strip trailing # comments line-by-line, then collapse all whitespace
    lines = []
    for line in script_text.splitlines():
        # Remove trailing '# ...' comment but preserve content before it
        m = re.match(r"^(.*?)(?:\s+#.*)?$", line)
        lines.append(m.group(1) if m else line)
    joined = " ".join(lines)
    return re.sub(r"\s+", " ", joined)


def _approve_matcher(text: str) -> str:
    """Extract the approve-branch matcher tuple as a normalized string."""
    # Find the 'if args.decision == "approve":' block and grab its `l.status in (...)` tuple
    m = re.search(
        r'if args\.decision == "approve":.*?l\.status in \((.*?)\)\]',
        text, re.DOTALL,
    )
    assert m is not None, "approve matcher not found in script"
    return re.sub(r"\s+", " ", m.group(1)).strip().rstrip(",").strip()


def _reject_edit_matcher(text: str) -> str:
    """Extract the non-approve (else) branch matcher tuple."""
    # Find the 'else:' that pairs with the approve-decision check
    m = re.search(
        r'if args\.decision == "approve":.*?else:.*?l\.status in \((.*?)\)\]',
        text, re.DOTALL,
    )
    assert m is not None, "reject/edit matcher not found in script"
    return re.sub(r"\s+", " ", m.group(1)).strip().rstrip(",").strip()


def test_approve_matcher_includes_owner_approved(script_text: str):
    """Approve matcher MUST accept OWNER_APPROVED (row 3+4: retry / self-heal)."""
    matcher = _approve_matcher(script_text)
    assert '"OWNER_APPROVED"' in matcher, f"OWNER_APPROVED missing from approve matcher: {matcher}"


def test_approve_matcher_includes_awaiting_owner_approval(script_text: str):
    """Approve matcher MUST accept AWAITING_OWNER_APPROVAL (legacy / --skip-finalize)."""
    matcher = _approve_matcher(script_text)
    assert '"AWAITING_OWNER_APPROVAL"' in matcher, f"AWAITING_OWNER_APPROVAL missing: {matcher}"


def test_approve_matcher_includes_customer_finalized(script_text: str):
    """PR-CF1: approve matcher MUST accept CUSTOMER_FINALIZED (the new
    'ready to approve' state from the customer-finalize flow)."""
    matcher = _approve_matcher(script_text)
    assert '"CUSTOMER_FINALIZED"' in matcher, f"CUSTOMER_FINALIZED missing: {matcher}"


def test_owner_edited_not_in_approve_matcher(script_text: str):
    """R2-H1 money-moving correctness: OWNER_EDITED must NOT be in the
    approve-retry matcher. Owner sent edit, retry-approve with same code
    would ship un-edited quote to customer."""
    matcher = _approve_matcher(script_text)
    assert '"OWNER_EDITED"' not in matcher, \
        f"OWNER_EDITED leaked into approve matcher (R2-H1 regression): {matcher}"


def test_no_owner_edited_self_heal_branch(script_text: str):
    """Per design v2 §9.2 R2-H1: there must be no row-4-style self-heal
    branch keyed on OWNER_EDITED. Self-heal applies only to OWNER_APPROVED."""
    # No code path checks `lead.status == "OWNER_EDITED"` for retry resume
    assert 'lead.status == "OWNER_EDITED"' not in script_text
    assert 'lead.status in ("OWNER_EDITED"' not in script_text


def test_reject_edit_matcher_excludes_owner_approved(script_text: str):
    """Reject/edit decisions must NOT accept OWNER_APPROVED. Retry-reject
    after approve = undefined; the apply script should EXIT_NOT_FOUND."""
    matcher = _reject_edit_matcher(script_text)
    assert '"OWNER_APPROVED"' not in matcher, \
        f"OWNER_APPROVED leaked into reject/edit matcher (semantic regression): {matcher}"


def test_reject_edit_matcher_includes_awaiting_owner_approval(script_text: str):
    """Reject/edit MUST still accept AWAITING_OWNER_APPROVAL (the canonical
    pre-approval state)."""
    matcher = _reject_edit_matcher(script_text)
    assert '"AWAITING_OWNER_APPROVAL"' in matcher, \
        f"AWAITING_OWNER_APPROVAL missing from reject/edit matcher: {matcher}"


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
