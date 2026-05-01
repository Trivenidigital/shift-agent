"""MockQBOClient contract conformance + parametrized error injection.

Pure-function tests — Windows + Linux. Reviewer-d MED M2: parametrize over
all 6 error_class values, exercise both retryable + non-retryable branches.
"""
from __future__ import annotations

import json
import os
os.environ.setdefault("EXPENSE_RECEIPTS_DIR", "/tmp/test/")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

import pytest

from schemas import ExpenseLead
from qbo_client import (
    MockQBOClient,
    RealQBOClient,
    QBOPushError,
    QBOPushResult,
    RETRYABLE_ERROR_CLASSES,
    redact_qbo_error,
    make_qbo_client,
)


@pytest.fixture
def lead():
    return ExpenseLead.model_validate({
        "expense_id": "E0042", "original_message_id": "wa_xyz",
        "sender_phone": "+19045550000",
        "received_at": "2026-04-29T12:00:00+00:00",
        "image_path": "/tmp/test/E0042.jpg",
        "image_phash": "a3f2c19d8b5e4067",
        "image_byte_hash": "a" * 64,
        "owner_confirmed_total_cents": 23450,
    })


def test_mock_push_happy_path(lead):
    client = MockQBOClient(timezone="America/New_York")
    result = client.push_expense(lead)
    assert isinstance(result, QBOPushResult)
    assert result.transaction_id == "MOCK-E0042-1"
    assert result.amount_cents == 23450
    # ISO8601 string
    assert "T" in result.pushed_at


def test_mock_push_seq_increments(lead):
    client = MockQBOClient()
    r1 = client.push_expense(lead)
    r2 = client.push_expense(lead)
    assert r1.transaction_id != r2.transaction_id
    assert r2.transaction_id == "MOCK-E0042-2"


def test_mock_push_requires_owner_confirmed_total(lead):
    lead.owner_confirmed_total_cents = None
    client = MockQBOClient()
    with pytest.raises(QBOPushError) as exc:
        client.push_expense(lead)
    assert exc.value.error_class == "invalid_request"


@pytest.mark.parametrize("error_class", [
    "token_expired", "rate_limit", "bad_account",
    "server", "network", "invalid_request",
])
def test_mock_push_fail_modes(lead, error_class):
    client = MockQBOClient(fail_mode=error_class)
    with pytest.raises(QBOPushError) as exc:
        client.push_expense(lead)
    assert exc.value.error_class == error_class


def test_retryable_classes_exact():
    """Ensures RETRYABLE_ERROR_CLASSES doesn't drift."""
    assert RETRYABLE_ERROR_CLASSES == frozenset({"rate_limit", "server", "network"})


def test_mock_void_happy_path(lead):
    client = MockQBOClient()
    r = client.push_expense(lead)
    client.void_transaction(r.transaction_id)


def test_mock_void_unknown_transaction(lead):
    client = MockQBOClient()
    with pytest.raises(QBOPushError) as exc:
        client.void_transaction("UNKNOWN-TX")
    assert exc.value.error_class == "invalid_request"


def test_mock_void_already_voided(lead):
    client = MockQBOClient()
    r = client.push_expense(lead)
    client.void_transaction(r.transaction_id)
    with pytest.raises(QBOPushError) as exc:
        client.void_transaction(r.transaction_id)
    assert exc.value.error_class == "invalid_request"


def test_mock_health_check():
    assert MockQBOClient().health_check() is True
    assert MockQBOClient(fail_mode="network").health_check() is False
    assert MockQBOClient(fail_mode="rate_limit").health_check() is True


def test_redact_strips_access_token():
    err = QBOPushError(
        "server",
        "GET /v3/company/123/?access_token=AB123CDEF456789xyz failed (500)",
    )
    redacted = redact_qbo_error(err)
    assert "access_token" not in redacted, f"leaked: {redacted}"
    assert "<REDACTED>" in redacted
    assert redacted.startswith("[server]")


def test_redact_strips_bearer():
    err = QBOPushError(
        "token_expired",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.SomeToken refused",
    )
    redacted = redact_qbo_error(err)
    assert "Bearer" not in redacted or "<REDACTED>" in redacted, f"leaked: {redacted}"


def test_redact_truncates():
    err = QBOPushError("server", "x" * 1000)
    redacted = redact_qbo_error(err, max_chars=50)
    assert len(redacted) <= 60  # [server] prefix + max_chars + ellipsis


def test_real_client_refuses_v01():
    with pytest.raises(NotImplementedError, match="v0.2"):
        RealQBOClient()


def test_factory_returns_mock_when_mode_mock():
    class FakeCfg:
        qbo_client_mode = "mock"
    client = make_qbo_client(FakeCfg(), customer_timezone="America/New_York")
    assert isinstance(client, MockQBOClient)


def test_factory_real_raises_v01():
    class FakeCfg:
        qbo_client_mode = "real"
    with pytest.raises(NotImplementedError):
        make_qbo_client(FakeCfg())


# ─────────────────────────────────────────────────────────────────
# F2 (E2E Layer B fix 2026-05-01): cross-process state persistence.
# Without state_path, MockQBOClient._pushed is in-memory and a fresh
# instance can't void a transaction pushed by a prior instance — exactly
# what blocked the live undo flow on the VPS during E2E Layer B testing.
# ─────────────────────────────────────────────────────────────────


def test_mock_void_works_across_process_boundaries(lead, tmp_path):
    """Two separate MockQBOClient instances sharing a state_path file:
    process 1 pushes, process 2 (fresh instance, no in-memory state) voids
    successfully. Without persistence the void raises 'unknown
    transaction_id' — that's the bug this fix addresses."""
    state = tmp_path / "mock-qbo.json"

    # Process 1: push
    client1 = MockQBOClient(state_path=state)
    result = client1.push_expense(lead)
    assert result.transaction_id == "MOCK-E0042-1"
    assert state.exists(), "state file must be written after push"

    # Process 2: completely fresh instance; same state_path → void succeeds
    client2 = MockQBOClient(state_path=state)
    client2.void_transaction("MOCK-E0042-1")  # must NOT raise

    # Process 3: same transaction now gone from ledger → re-void raises
    client3 = MockQBOClient(state_path=state)
    with pytest.raises(QBOPushError, match="unknown transaction_id"):
        client3.void_transaction("MOCK-E0042-1")


def test_mock_state_path_none_preserves_in_memory_behaviour(lead):
    """state_path=None (default) means no file persistence — preserves the
    pre-fix in-memory behaviour that all existing unit tests depend on."""
    client = MockQBOClient()  # no state_path
    assert client._state_path is None
    result = client.push_expense(lead)
    # Same-instance void works (in-memory)
    client.void_transaction(result.transaction_id)
    # Fresh instance with no state_path has no shared state — void raises
    fresh = MockQBOClient()
    with pytest.raises(QBOPushError, match="unknown transaction_id"):
        fresh.void_transaction(result.transaction_id)


def test_mock_state_seq_persists_across_instances(lead, tmp_path):
    """seq counter must survive process boundary so transaction IDs don't
    collide on retry-after-crash scenarios."""
    state = tmp_path / "mock-qbo.json"
    client1 = MockQBOClient(state_path=state)
    r1 = client1.push_expense(lead)
    assert r1.transaction_id == "MOCK-E0042-1"

    # Different lead (different expense_id), fresh client, same state file
    other_lead = ExpenseLead.model_validate({
        **lead.model_dump(),
        "expense_id": "E0099",
        "original_message_id": "wa_other",
    })
    client2 = MockQBOClient(state_path=state)
    r2 = client2.push_expense(other_lead)
    # seq must continue from where client1 left off (1 → 2), not reset to 1
    assert r2.transaction_id == "MOCK-E0099-2"


def test_mock_state_corrupted_file_raises_loudly(tmp_path):
    """A corrupted state file must NOT silently lose transaction state.
    Fail loudly so operator notices and triages."""
    state = tmp_path / "mock-qbo.json"
    state.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        MockQBOClient(state_path=state)


def test_mock_state_unsupported_schema_version_raises(tmp_path):
    """Future schema versions on disk must NOT be silently accepted —
    forces explicit migration tooling when v2 lands."""
    state = tmp_path / "mock-qbo.json"
    state.write_text(
        '{"schema_version": 99, "seq": 0, "transactions": {}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema_version"):
        MockQBOClient(state_path=state)


def test_factory_forwards_state_path(tmp_path):
    """make_qbo_client must forward state_path to MockQBOClient so the
    apply-expense-decision integration actually gets persistence."""
    class FakeCfg:
        qbo_client_mode = "mock"
    state = tmp_path / "mock-qbo-factory.json"
    client = make_qbo_client(FakeCfg(), customer_timezone="UTC", state_path=state)
    assert isinstance(client, MockQBOClient)
    assert client._state_path == state
