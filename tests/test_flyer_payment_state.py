from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agents.flyer.payment_state import activation_event_state


def test_activation_event_manual_accepts_reference_without_amount():
    state = activation_event_state(
        provider="manual",
        payment_reference="cash-1",
        amount_cents=None,
        currency="",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_confirmed"


def test_activation_event_non_manual_requires_amount():
    state = activation_event_state(
        provider="stripe",
        payment_reference="pi_1",
        amount_cents=None,
        currency="USD",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_pending"


def test_activation_event_non_manual_requires_currency():
    state = activation_event_state(
        provider="stripe",
        payment_reference="pi_1",
        amount_cents=4999,
        currency="",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_pending"


def test_activation_event_rejects_unknown_provider():
    state = activation_event_state(
        provider="gateway_x",
        payment_reference="pi_1",
        amount_cents=4999,
        currency="USD",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_pending"


def test_activation_event_normalizes_provider_case_and_space():
    state = activation_event_state(
        provider=" Stripe ",
        payment_reference="pi_1",
        amount_cents=4999,
        currency="usd",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_confirmed"


def test_activation_event_rejects_currency_mismatch():
    state = activation_event_state(
        provider="razorpay",
        payment_reference="rzp_1",
        amount_cents=4999,
        currency="INR",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_pending"
