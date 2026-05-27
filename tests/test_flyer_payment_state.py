from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.payment_state import activation_event_state  # noqa: E402


def test_activation_event_confirms_when_reference_amount_and_currency_match():
    state = activation_event_state(
        provider="stripe",
        payment_reference="pi_123",
        amount_cents=4999,
        currency="usd",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_confirmed"


def test_activation_event_rejects_unknown_provider_fail_closed():
    state = activation_event_state(
        provider="paypal",
        payment_reference="pi_123",
        amount_cents=4999,
        currency="USD",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_pending"


def test_activation_event_normalizes_provider_case_and_whitespace():
    state = activation_event_state(
        provider="  STRIPE  ",
        payment_reference="pi_123",
        amount_cents=4999,
        currency="USD",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_confirmed"


def test_activation_event_requires_nonblank_currency_for_non_manual_provider():
    state = activation_event_state(
        provider="stripe",
        payment_reference="pi_123",
        amount_cents=4999,
        currency="  ",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_pending"


def test_activation_event_fails_closed_when_expected_currency_missing():
    state = activation_event_state(
        provider="stripe",
        payment_reference="pi_123",
        amount_cents=4999,
        currency="USD",
        expected_amount_cents=4999,
        expected_currency="",
    )
    assert state == "payment_pending"
