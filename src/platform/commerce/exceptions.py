"""Commerce primitive exceptions.

Fail-closed by design: any illegal transition or unconfigured-but-invoked
gated path raises rather than silently returning success. Mirrors the
2026-05-25 lesson on money-adjacent fail-closed posture.
"""
from __future__ import annotations


class CommerceError(Exception):
    """Base for commerce-primitive failures."""


class IllegalCommerceTransition(CommerceError):
    """Order state machine refused a transition not in LEGAL_TRANSITIONS."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(
            f"illegal commerce order transition: {from_status!r} -> {to_status!r}"
        )
        self.from_status = from_status
        self.to_status = to_status


class CommerceOwnerApprovalThresholdUnconfigured(CommerceError):
    """Caller invoked the approval-gated path without operator-set threshold.

    Per PRD v2 §7 (Reviewer B HIGH-3): the threshold default is
    None (UNCONFIGURED). Callers must NOT silently proceed when the
    operator has not configured a value.
    """


class CommercePaymentReferenceReuse(CommerceError):
    """Cross-order payment_reference re-use refused.

    The reference ledger is immutable across all orders (including
    cancelled/voided/refunded). Mirrors flyer/guest_order.py:108-113 +
    2026-05-25 lesson.
    """

    def __init__(self, reference: str, original_order_id: str) -> None:
        super().__init__(
            f"payment_reference {reference!r} already bound to order "
            f"{original_order_id!r}; cross-order reuse is permanently blocked"
        )
        self.reference = reference
        self.original_order_id = original_order_id


class CommerceCheckoutUrlUnrenderable(CommerceError):
    """Caller attempted to render an empty / malformed checkout URL.

    Per reconciliation invariant #2: an empty url MUST surface as the
    "Payment link is not configured yet" customer-visible reply, NOT a
    bare clickable string with no destination.
    """
