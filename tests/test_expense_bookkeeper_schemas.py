"""Pydantic validation + defaults for Agent #21 Expense Bookkeeper.

Pure-function tests — runs on Windows + Linux (no fcntl, no subprocess).
"""
from __future__ import annotations

import os
import pytest

@pytest.fixture(autouse=True)
def _isolate_receipts_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPENSE_RECEIPTS_DIR", "/tmp/test/")


# Allow image_path validator to accept test paths
os.environ.setdefault("EXPENSE_RECEIPTS_DIR", "/tmp/test/")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from schemas import (
    Config,
    ExpenseBookkeeperConfig,
    ExpenseLead,
    ExpenseLeadStore,
    ExpenseLineItem,
    ExpenseClassification,
    ReceiptExtraction,
    EXPENSE_TRANSITIONS,
    EXPENSE_TERMINAL_STATUSES,
    is_expense_transition_allowed,
    ExpenseReceiptReceived,
    ExpenseDuplicateDetected,
    ExpenseExtractionCompleted,
    ExpenseClassificationProposed,
    ExpenseOwnerApprovalRequested,
    ExpenseOwnerDecision,
    ExpenseLeadStatusChange,
    ExpensePushAttempted,
    ExpensePushed,
    ExpensePushFailed,
    ExpenseReversalRequested,
    ExpenseReversed,
    ExpenseReceiptPruned,
    ExpenseNonOwnerUndoDeclined,
    ExpenseOrphanDetected,
)


def test_config_defaults():
    cfg = ExpenseBookkeeperConfig()
    assert cfg.enabled is False
    assert cfg.cockpit_threshold_cents == 5000
    assert cfg.auto_categorize_threshold == 0.85
    assert cfg.require_owner_approval_for_personal_flag is True
    assert cfg.reversibility_window_hours == 24
    assert cfg.dedup_hash_distance_threshold == 4
    assert cfg.receipt_retention_days == 90
    assert cfg.proposal_ttl_hours == 72
    assert cfg.qbo_client_mode == "mock"


def test_config_in_full_config_object():
    cfg = Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "l",
                     "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    })
    assert cfg.expense_bookkeeper.enabled is False


def test_config_extra_forbidden():
    with pytest.raises(Exception):
        ExpenseBookkeeperConfig(unknown_field=1)  # type: ignore[call-arg]


def test_config_threshold_must_be_positive():
    with pytest.raises(Exception):
        ExpenseBookkeeperConfig(cockpit_threshold_cents=0)


@pytest.fixture
def base_lead_dict():
    return {
        "expense_id": "E0001",
        "original_message_id": "wa_msg_xyz123",
        "sender_phone": "+19045550000",
        "received_at": "2026-04-29T12:00:00+00:00",
        "image_path": "/tmp/test/E0001.jpg",
        "image_phash": "a3f2c19d8b5e4067",
        "image_byte_hash": "a" * 64,
    }


def test_lead_validates(base_lead_dict):
    lead = ExpenseLead.model_validate(base_lead_dict)
    assert lead.status == "EXTRACTING"
    assert lead.reconcile_required is False
    assert lead.duplicate_of is None


def test_lead_path_traversal_rejected(base_lead_dict):
    base_lead_dict["image_path"] = "/tmp/test/../../etc/passwd"
    with pytest.raises(Exception, match="invalid image_path"):
        ExpenseLead.model_validate(base_lead_dict)


def test_lead_outside_managed_dir_rejected(base_lead_dict):
    base_lead_dict["image_path"] = "/etc/passwd"
    with pytest.raises(Exception, match="must be under"):
        ExpenseLead.model_validate(base_lead_dict)


def test_lead_phash_length_enforced(base_lead_dict):
    base_lead_dict["image_phash"] = "abc"
    with pytest.raises(Exception):
        ExpenseLead.model_validate(base_lead_dict)


def test_lead_byte_hash_length_enforced(base_lead_dict):
    base_lead_dict["image_byte_hash"] = "abc"
    with pytest.raises(Exception):
        ExpenseLead.model_validate(base_lead_dict)


def test_lead_id_pattern_enforced(base_lead_dict):
    base_lead_dict["expense_id"] = "X1"
    with pytest.raises(Exception):
        ExpenseLead.model_validate(base_lead_dict)


def test_extraction_extra_ignored():
    """LLM-output shape: extra='ignore' tolerates future fields per Hermes-alignment Part 1."""
    obj = ReceiptExtraction.model_validate({
        "vendor_name": "Costco",
        "total_cents": 23450,
        "line_items": [],
        "extraction_confidence": 0.92,
        "_some_future_field": "future-value",  # ignored, no error
    })
    assert obj.vendor_name == "Costco"
    assert not hasattr(obj, "_some_future_field")


def test_extraction_total_optional():
    """Reviewer-b HIGH B2: total_cents is ADVISORY ONLY; nullable, owner-confirmed
    total is the source of truth."""
    obj = ReceiptExtraction.model_validate({
        "vendor_name": "Acme",
        "line_items": [],
        "extraction_confidence": 0.5,
    })
    assert obj.total_cents is None


def test_extraction_raw_text_capped():
    huge = "x" * 10000
    with pytest.raises(Exception):
        ReceiptExtraction(
            vendor_name="X", line_items=[], extraction_confidence=0.5,
            raw_text_for_audit=huge,
        )


def test_classification_validates():
    c = ExpenseClassification(
        is_business=True, confidence=0.9,
        rationale="grocery for restaurant kitchen",
        qbo_account="COGS - Groceries",
    )
    assert c.is_business is True


def test_classification_rationale_capped():
    with pytest.raises(Exception):
        ExpenseClassification(
            is_business=True, confidence=0.9,
            rationale="x" * 1000,
            qbo_account="COGS",
        )


def test_lead_store_schema_version():
    s = ExpenseLeadStore()
    assert s.schema_version == 1
    assert s.last_id == 0
    assert s.leads == []


def test_terminal_statuses_have_no_outbound():
    """STRICT terminal = no outbound transitions. PUSHED is excluded —
    owner can still `undo` to REVERSED within the reversibility window."""
    for terminal in EXPENSE_TERMINAL_STATUSES:
        assert EXPENSE_TRANSITIONS[terminal] == frozenset(), (
            f"terminal {terminal} should have empty transitions"
        )
    # PUSHED is NOT a strict terminal
    assert "PUSHED" not in EXPENSE_TERMINAL_STATUSES
    assert EXPENSE_TRANSITIONS["PUSHED"] == frozenset({"REVERSED"})


def test_audit_entries_round_trip():
    """All 15 new entry types validate + round-trip via model_dump_json."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    entries = [
        ExpenseReceiptReceived(
            ts=now, type="expense_receipt_received",
            expense_id="E0001", sender_phone="+1", image_path="/p",
            image_phash="a"*16, original_message_id="m1",
        ),
        ExpenseDuplicateDetected(
            ts=now, type="expense_duplicate_detected",
            expense_id="E0002", matched_expense_id="E0001",
            phash_distance=2,
        ),
        ExpenseExtractionCompleted(
            ts=now, type="expense_extraction_completed",
            expense_id="E0001", extraction_confidence=0.9,
            line_item_count=3, extracted_total_cents=23450,
        ),
        ExpenseClassificationProposed(
            ts=now, type="expense_classification_proposed",
            expense_id="E0001", is_business=True,
            classification_confidence=0.9, qbo_account="COGS - Groceries",
        ),
        ExpenseOwnerApprovalRequested(
            ts=now, type="expense_owner_approval_requested",
            expense_id="E0001", owner_approval_code="#A47C2",
            extracted_total_cents=23450, routed_to="whatsapp",
        ),
        ExpenseOwnerDecision(
            ts=now, type="expense_owner_decision",
            expense_id="E0001", decision="approved",
            raw_message="#A47C2 234.50", code_matched=True,
            amount_matched=True, force_context="none",
        ),
        ExpenseLeadStatusChange(
            ts=now, type="expense_lead_status_change",
            expense_id="E0001", from_status="AWAITING_OWNER_APPROVAL",
            to_status="APPROVED_PENDING_PUSH", reason="owner approved",
        ),
        ExpensePushAttempted(
            ts=now, type="expense_push_attempted",
            expense_id="E0001", qbo_client_mode="mock",
            extracted_total_cents=23450, owner_confirmed_total_cents=23450,
            push_total_cents=23450,
        ),
        ExpensePushed(
            ts=now, type="expense_pushed",
            expense_id="E0001", qbo_transaction_id="MOCK-E0001-1",
            qbo_amount_cents=23450, push_attempt_no=1,
        ),
        ExpensePushFailed(
            ts=now, type="expense_push_failed",
            expense_id="E0001", error_class="rate_limit",
            error_message_redacted="[rate_limit] mock",
        ),
        ExpenseReversalRequested(
            ts=now, type="expense_reversal_requested",
            expense_id="E0001", requested_by_phone="+1",
            requested_by_role="owner", within_window=True,
            hours_since_push=2.5,
        ),
        ExpenseReversed(
            ts=now, type="expense_reversed",
            expense_id="E0001", qbo_transaction_id="MOCK-E0001-1",
            void_method="api_void",
        ),
        ExpenseReceiptPruned(
            ts=now, type="expense_receipt_pruned",
            expense_id="E0001", vendor_normalized="Costco",
            extracted_total_cents=23450, reason="retention_expired",
        ),
        ExpenseNonOwnerUndoDeclined(
            ts=now, type="expense_non_owner_undo_declined",
            expense_id="E0001", requested_by_phone="+1",
        ),
        ExpenseOrphanDetected(
            ts=now, type="expense_orphan_detected",
            expense_id="E0001", last_known_status="APPROVED_PENDING_PUSH",
            detected_by="startup_scan",
        ),
    ]
    for e in entries:
        s = e.model_dump_json()
        # Use TypeAdapter for the discriminated union for full round-trip
        from pydantic import TypeAdapter
        from schemas import LogEntry
        adapter = TypeAdapter(LogEntry)
        parsed = adapter.validate_json(s)
        assert parsed.type == e.type, f"type mismatch for {type(e).__name__}"


def test_owner_decision_raw_message_capped():
    """raw_message must be capped at 500 chars per security review B3."""
    from datetime import datetime, timezone
    with pytest.raises(Exception):
        ExpenseOwnerDecision(
            ts=datetime.now(timezone.utc),
            type="expense_owner_decision",
            expense_id="E0001", decision="approved",
            raw_message="x" * 1000, code_matched=True,
            amount_matched=True, force_context="none",
        )


def test_push_failed_message_capped():
    """error_message_redacted must be capped at 200 chars."""
    from datetime import datetime, timezone
    with pytest.raises(Exception):
        ExpensePushFailed(
            ts=datetime.now(timezone.utc),
            type="expense_push_failed",
            expense_id="E0001", error_class="server",
            error_message_redacted="x" * 500,
        )
