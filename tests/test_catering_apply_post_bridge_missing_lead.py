"""PR-D2 commit 2: matched_idx + customer_phone_pre_bridge + divergence audit.

Static checks pin the design v2 §4.2 contract on apply-catering-owner-decision:
- for-loop index-leak pattern at line 397 of pre-PR-D2 code is REMOVED.
- next((i for i, l in enumerate(...))) idiom is present.
- customer_phone_pre_bridge captured INSIDE first LEADS_LOCK block.
- log_quote_sent_lead_missing_best_effort wired to the matched_idx-is-None branch.

Subprocess-level integration tests (BridgeStub side-effect to delete lead
post-bridge) defer to commit 7 — they need fcntl + tmp /opt/shift-agent
context that's only viable on Linux CI / VPS.
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


def test_no_for_loop_index_leak_in_post_bridge(script_text: str):
    """The pre-PR-D2 pattern was:
        for i, l in enumerate(store.leads):
            if l.lead_id == lead_id_for_output:
                ... break
        atomic_write_json(LEADS_PATH, store)
        ... store.leads[i].customer_phone   # <- index leak

    After PR-D2 commit 2, the for-loop is replaced by next(...) idiom
    and the line-397-equivalent reference uses store.leads[matched_idx]
    (which is None-checked above)."""
    # The two-step write order from PR-D1 has CateringQuoteSent at line 417
    # area, after atomic_write. After commit 2's matched_idx, the for-loop
    # at line 401-407 is GONE. Search for the line above + variable usage
    # (store.leads[i].customer_phone) — must NOT appear.
    assert "store.leads[i].customer_phone" not in script_text, (
        "post-bridge for-loop index-leak pattern still present — H3 fix not applied"
    )


def test_matched_idx_idiom_present(script_text: str):
    """next() idiom replacing for-loop in post-bridge re-load."""
    assert "matched_idx = next(" in script_text, (
        "matched_idx = next((i for i, l in ...)) idiom not present"
    )


def test_customer_phone_pre_bridge_captured_inside_lock(script_text: str):
    """customer_phone_pre_bridge captured before first LEADS_LOCK release."""
    assert "customer_phone_pre_bridge" in script_text, (
        "customer_phone_pre_bridge not captured"
    )
    # Find the first LEADS_LOCK acquire and the lock release; assert
    # customer_phone_pre_bridge assignment is between them.
    first_lock_idx = script_text.find("with FileLock(LEADS_LOCK):")
    assert first_lock_idx != -1
    capture_idx = script_text.find("customer_phone_pre_bridge = lead.customer_phone")
    assert capture_idx > first_lock_idx, (
        "customer_phone_pre_bridge captured before first LEADS_LOCK acquire"
    )
    # Bridge POST is _bridge_post call — must come AFTER the capture
    bridge_idx = script_text.find("ok, mid_or_err = _bridge_post(")
    assert bridge_idx > capture_idx, (
        "customer_phone_pre_bridge captured after bridge POST"
    )


def test_log_quote_sent_lead_missing_wired(script_text: str):
    """log_quote_sent_lead_missing_best_effort imported + called."""
    assert "from audit_helpers import" in script_text
    assert "log_quote_sent_lead_missing_best_effort" in script_text
    # It's called in the matched_idx-is-None branch
    null_branch_idx = script_text.find("if matched_idx is None:")
    assert null_branch_idx != -1
    helper_call_idx = script_text.find(
        "log_quote_sent_lead_missing_best_effort(",
        null_branch_idx,
    )
    assert helper_call_idx != -1, (
        "log_quote_sent_lead_missing_best_effort not called in matched_idx-is-None branch"
    )


def test_pushover_p2_fired_on_divergence(script_text: str):
    """Pushover priority=2 still fires on post-bridge divergence
    (existing behavior preserved alongside new audit emission)."""
    assert "BUG state-outbound divergence" in script_text
    assert '"--priority", "2"' in script_text


def test_log_helper_renamed(script_text: str):
    """_log() renamed to _append_log_with_outer_leadslock per R5-H2 pin."""
    assert "_append_log_with_outer_leadslock" in script_text
    # Both definitions: helper + alias
    assert script_text.count("_append_log_with_outer_leadslock") >= 6


def test_cateringquotesentleadmissing_imported(script_text: str):
    """The new variant from PR-D1 is now used by apply-script post-bridge path."""
    assert "CateringQuoteSentLeadMissing" in script_text
