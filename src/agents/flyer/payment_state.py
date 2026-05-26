"""Provider-neutral Flyer Studio payment state machine.

Stripe/Razorpay/MCP connectors can create checkout links or webhook events, but
Flyer itself owns the state transitions and fail-closed activation rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from schemas import FlyerCustomerProfile, FlyerGuestOrder, FlyerPlanTier


FlyerPaymentState = Literal[
    "none",
    "checkout_missing",
    "checkout_ready",
    "payment_pending",
    "payment_confirmed",
    "activated",
]


@dataclass(frozen=True)
class PlanPaymentRequest:
    state: FlyerPaymentState
    plan_id: str
    checkout_url: str
    amount_cents: int
    currency: str
    provider: str


def build_plan_payment_request(
    *,
    plan_id: str,
    checkout_url: str,
    provider: str,
    tiers: list[FlyerPlanTier],
) -> PlanPaymentRequest:
    tier = next((row for row in tiers if row.plan_id == plan_id), None)
    if tier is None:
        raise ValueError("unknown plan")
    state: FlyerPaymentState = "checkout_ready" if checkout_url else "checkout_missing"
    return PlanPaymentRequest(
        state=state,
        plan_id=plan_id,
        checkout_url=checkout_url,
        amount_cents=tier.price_cents(),
        currency=tier.currency,
        provider=provider if provider in {"manual", "stripe", "razorpay", "other"} else "manual",
    )


def customer_payment_state(customer: FlyerCustomerProfile) -> FlyerPaymentState:
    if customer.status == "payment_pending":
        if customer.payment_reference:
            return "payment_confirmed"
        return "payment_pending" if customer.payment_checkout_url else "checkout_missing"
    if customer.status == "active" and customer.pending_plan_id:
        explicit = str(getattr(customer, "pending_plan_payment_state", "") or "")
        if explicit in {"checkout_missing", "checkout_ready", "payment_pending", "payment_confirmed"}:
            return explicit  # type: ignore[return-value]
        return "checkout_ready" if customer.pending_plan_checkout_url else "checkout_missing"
    if customer.status in {"active", "trial"}:
        return "activated"
    return "none"


def guest_order_payment_state(order: FlyerGuestOrder) -> FlyerPaymentState:
    if order.status == "pending_payment":
        return "payment_pending" if order.payment_checkout_url else "checkout_missing"
    if order.status in {"paid", "reserved", "used"}:
        return "payment_confirmed"
    return "none"


def activation_event_state(
    *,
    provider: str,
    payment_reference: str,
    amount_cents: Optional[int],
    currency: str,
    expected_amount_cents: int,
    expected_currency: str,
) -> FlyerPaymentState:
    provider_normalized = str(provider or "").strip().lower()
    if provider_normalized not in {"manual", "stripe", "razorpay", "other"}:
        return "payment_pending"
    if not payment_reference.strip():
        return "none"
    normalized_currency = str(currency or "").strip().upper()
    normalized_expected_currency = str(expected_currency or "").strip().upper()
    if provider_normalized != "manual" and amount_cents is None:
        return "payment_pending"
    if provider_normalized != "manual" and not normalized_currency:
        return "payment_pending"
    compare_currency = normalized_currency or normalized_expected_currency
    if compare_currency != normalized_expected_currency:
        return "payment_pending"
    if amount_cents is not None and amount_cents != expected_amount_cents:
        return "payment_pending"
    return "payment_confirmed"


def payment_state_timestamp(now: datetime) -> datetime:
    return now
