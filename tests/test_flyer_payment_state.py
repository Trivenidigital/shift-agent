from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.payment_state import activation_event_state


def test_activation_event_rejects_unknown_provider_fail_closed() -> None:
    state = activation_event_state(
        provider="unknown",
        payment_reference="pi_1",
        amount_cents=4999,
        currency="USD",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "none"


def test_activation_event_normalizes_provider_case_and_whitespace() -> None:
    state = activation_event_state(
        provider="  STRIPE  ",
        payment_reference="pi_1",
        amount_cents=4999,
        currency="USD",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_confirmed"


def test_activation_event_accepts_razorpay_and_padded_currency() -> None:
    state = activation_event_state(
        provider="razorpay",
        payment_reference="pay_1",
        amount_cents=4999,
        currency=" usd ",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_confirmed"


def test_activation_event_manual_confirms_without_amount_when_reference_and_currency_match() -> None:
    state = activation_event_state(
        provider="manual",
        payment_reference="owner-confirmed",
        amount_cents=None,
        currency="USD",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_confirmed"


def test_activation_event_manual_rejects_mismatched_amount() -> None:
    state = activation_event_state(
        provider="manual",
        payment_reference="owner-confirmed",
        amount_cents=1,
        currency="USD",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_pending"


def test_activation_event_requires_expected_currency() -> None:
    state = activation_event_state(
        provider="stripe",
        payment_reference="pi_1",
        amount_cents=4999,
        currency="USD",
        expected_amount_cents=4999,
        expected_currency="",
    )
    assert state == "payment_pending"


def test_activation_event_requires_non_manual_currency_input() -> None:
    state = activation_event_state(
        provider="stripe",
        payment_reference="pi_1",
        amount_cents=4999,
        currency="",
        expected_amount_cents=4999,
        expected_currency="USD",
    )
    assert state == "payment_pending"
