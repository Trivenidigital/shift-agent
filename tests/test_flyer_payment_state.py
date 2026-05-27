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
