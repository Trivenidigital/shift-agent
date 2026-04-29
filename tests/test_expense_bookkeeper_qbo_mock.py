"""MockQBOClient contract conformance + parametrized error injection.

Pure-function tests — Windows + Linux. Reviewer-d MED M2: parametrize over
all 6 error_class values, exercise both retryable + non-retryable branches.
"""
from __future__ import annotations

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
