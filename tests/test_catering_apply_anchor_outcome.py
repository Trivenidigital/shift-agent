"""PR-D2 commit 3: anchor BEFORE bridge POST + post-bridge write reorder + tail-scan helpers.

Static checks pin the design v2 §4.3-§4.5 + §9.1 B-1 contract:
- Anchor with bridge_post_outcome="unknown" written inside FIRST LEADS_LOCK
  block, BEFORE bridge POST.
- Failed-anchor (outcome="failed") written under LEADS_LOCK on bridge fail.
- Post-bridge write reorder: CateringQuoteSent FIRST → success-anchor SECOND
  → state mutation THIRD → status_change LAST.
- _tail_scan_anchor + _tail_scan_quote_sent helpers present with no
  max_age_hours parameter (R1-H-2 fix).

Subprocess-level integration tests defer to commit 7.
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


def test_anchor_unknown_written_in_first_leadslock(script_text: str):
    """First-anchor write happens inside the FIRST LEADS_LOCK block,
    after the existing CateringOwnerDecision audit but before lock release."""
    # First anchor write: outcome="unknown"
    first_anchor_idx = script_text.find('bridge_post_outcome="unknown"')
    assert first_anchor_idx != -1
    # Should appear between the first LEADS_LOCK and the bridge POST call
    first_lock = script_text.find("with FileLock(LEADS_LOCK):")
    bridge_call = script_text.find("ok, mid_or_err = _bridge_post(")
    assert first_lock < first_anchor_idx < bridge_call


def test_failed_anchor_written_on_bridge_fail(script_text: str):
    """Bridge POST fail path writes anchor with outcome='failed' so retry
    knows to re-attempt."""
    failed_anchor_idx = script_text.find('bridge_post_outcome="failed"')
    assert failed_anchor_idx != -1
    # Must be inside an `if not ok:` branch
    if_not_ok = script_text.find("if not ok:")
    assert if_not_ok != -1
    assert if_not_ok < failed_anchor_idx


def test_success_anchor_written_after_bridge(script_text: str):
    """Success-anchor (outcome='success') written in the post-bridge
    re-acquired LEADS_LOCK block."""
    success_idx = script_text.find('bridge_post_outcome="success"')
    assert success_idx != -1


def test_post_bridge_write_order_reordered(script_text: str):
    """Per design v2 §4.4 / B-1: in the post-bridge SECOND LEADS_LOCK block,
    write order is CateringQuoteSent FIRST → success-anchor → state mutation
    → status_change LAST."""
    # Find the SECOND LEADS_LOCK block (after the bridge POST)
    bridge_idx = script_text.find("ok, mid_or_err = _bridge_post(")
    assert bridge_idx != -1
    after_bridge = script_text[bridge_idx:]
    # Locate position of each row write within the post-bridge section
    quote_sent_idx = after_bridge.find('type="catering_quote_sent"')
    success_anchor_idx = after_bridge.find('bridge_post_outcome="success"')
    state_mutation_idx = after_bridge.find('"status": "SENT_TO_CUSTOMER",')
    status_change_idx = after_bridge.find('to_status="SENT_TO_CUSTOMER", actor="system"')
    # Order assertion: quote_sent → success_anchor → state mutation → status_change
    assert quote_sent_idx != -1
    assert success_anchor_idx != -1
    assert state_mutation_idx != -1
    assert status_change_idx != -1
    assert quote_sent_idx < success_anchor_idx < state_mutation_idx < status_change_idx, (
        f"Post-bridge write order incorrect: "
        f"quote_sent={quote_sent_idx} success_anchor={success_anchor_idx} "
        f"state_mutation={state_mutation_idx} status_change={status_change_idx}"
    )


def test_tail_scan_anchor_helper_present(script_text: str):
    """_tail_scan_anchor helper defined per design v2 §4.5."""
    assert "def _tail_scan_anchor(" in script_text
    # No max_age_hours parameter (R1-H-2 fix)
    func_def_idx = script_text.find("def _tail_scan_anchor(")
    func_def_end = script_text.find(") -> Optional[CateringQuoteAttempted]:", func_def_idx)
    func_def = script_text[func_def_idx:func_def_end]
    assert "max_age_hours" not in func_def, (
        "max_age_hours parameter should be removed per R1-H-2 (NTP skew avoidance)"
    )
    assert "max_lines: int = 5000" in func_def


def test_tail_scan_quote_sent_helper_present(script_text: str):
    """_tail_scan_quote_sent helper defined per design v2 §4.5."""
    assert "def _tail_scan_quote_sent(" in script_text
    func_def_idx = script_text.find("def _tail_scan_quote_sent(")
    func_def_end = script_text.find(") -> Optional[CateringQuoteSent]:", func_def_idx)
    func_def = script_text[func_def_idx:func_def_end]
    assert "max_age_hours" not in func_def


def test_tail_scan_emits_truncated_signal_on_cap_hit(script_text: str):
    """Per R2-H-1: stderr emission on max_lines cap-hit (NDJSON variant
    deferred to PR-D3, plain stderr line for PR-D2)."""
    assert 'tail_scan_truncated' in script_text


def test_cateringquoteattempted_imported(script_text: str):
    """CateringQuoteAttempted (with bridge_post_outcome from PR-D1) imported."""
    assert "CateringQuoteAttempted" in script_text


def test_no_empty_original_message_id_at_callsites(script_text: str):
    """R5 BLOCKER fix: CateringQuoteAttempted.original_message_id has
    min_length=1. Empty string passed to anchor writes would crash the
    apply-script with ValidationError on every approve. All 4 callsites
    must use a non-empty sentinel (e.g. _no_inbound_msg_<lead_id>) until
    the inbound Meta message_id is threaded through.

    Catch a regression where future code re-introduces empty strings
    BEFORE the script is invoked in production."""
    assert 'original_message_id=""' not in script_text, (
        "R5 BLOCKER regression: CateringQuoteAttempted callsite passes "
        "empty original_message_id; min_length=1 would raise ValidationError "
        "and crash apply-script on every approve"
    )
    # Defensive: also ensure the sentinel is the chosen pattern
    assert '_no_inbound_msg_' in script_text


def test_anchor_callsites_count(script_text: str):
    """4 anchor writes total: pre-bridge (line 522 area), failed-bridge
    (line 554), post-bridge missing-lead divergence path NOT a CateringQuoteAttempted
    (it's CateringQuoteSentLeadMissing), success-anchor (line 670). Plus the
    placeholder lookup_status anchor — let's count CateringQuoteAttempted
    callsites specifically."""
    # Count occurrences of CateringQuoteAttempted constructor invocation
    callsite_count = script_text.count("CateringQuoteAttempted(\n                ")
    callsite_count += script_text.count("CateringQuoteAttempted(\n                    ")
    callsite_count += script_text.count("CateringQuoteAttempted(\n                        ")
    # 3 callsites: anchor=unknown pre-bridge + anchor=failed + anchor=success
    assert callsite_count >= 3, (
        f"expected >= 3 CateringQuoteAttempted callsites, found {callsite_count}"
    )
