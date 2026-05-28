"""Commerce payment-link primitive — slice 1 placeholder.

Slice 1 ships TEMPLATE SUBSTITUTION ONLY (no real Stripe/Razorpay call):
- Mint an intent with provider="placeholder"
- Substitute {order_id}/{amount_cents}/etc. into operator-configured template
- Track payment_reference immutability (cross-order dedup ledger)
- Hard fail-closed on empty template (callers MUST emit the
  "Payment link is not configured yet" reply)

Real provider integration + webhook receive land in slice 2 with their own
credential + compliance review per PRD v2 §6.

Invariants (binding, from PRD v2 §7):
1. Idempotency key = order_id (NOT (order_id, amount_cents))
2. Re-mint against same order_id returns existing live intent
3. Amount change requires explicit void + new intent
4. payment_reference once stored blocks cross-order reuse indefinitely
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from schemas import (
    CommercePaymentIntent,
    CommercePaymentIntentStore,
    CommercePaymentReferenceLedger,
)
from .cart import atomic_write_json  # reuse Windows-safe shim
from .audit import emit
from .exceptions import (
    CommerceCheckoutUrlUnrenderable,
    CommercePaymentReferenceReuse,
)


@dataclass(frozen=True)
class PaymentLinkResult:
    ok: bool
    intent: Optional[CommercePaymentIntent]
    detail: str = ""


def load_intent_store(path: Path) -> CommercePaymentIntentStore:
    if not path.exists():
        return CommercePaymentIntentStore()
    return CommercePaymentIntentStore.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def write_intent_store(path: Path, store: CommercePaymentIntentStore) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, store)


def load_reference_ledger(path: Path) -> CommercePaymentReferenceLedger:
    if not path.exists():
        return CommercePaymentReferenceLedger()
    return CommercePaymentReferenceLedger.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def write_reference_ledger(path: Path, ledger: CommercePaymentReferenceLedger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, ledger)


def _next_intent_id(store: CommercePaymentIntentStore) -> str:
    n = 1
    used = {i.intent_id for i in store.intents}
    while True:
        candidate = f"CPI{n:05d}"
        if candidate not in used:
            return candidate
        n += 1


def _render_checkout_url(
    template: str,
    *,
    order_id: str,
    intent_id: str,
    amount_cents: int,
    currency: str,
    chat_id: str,
) -> str:
    """Mirror flyer/guest_order.py:_checkout_url template approach."""
    if not template:
        return ""
    return template.format(
        order_id=order_id,
        intent_id=intent_id,
        amount_cents=amount_cents,
        amount_usd=f"{amount_cents / 100:.2f}",
        currency=currency,
        chat_id=chat_id,
    )


def assert_payment_url_renderable(url: str) -> None:
    """Hard guard: raise if the caller is about to render an empty / whitespace url.

    Per PRD v2 §7 + reconciliation invariant #2: callers MUST NOT ship a bare
    URL that points nowhere. They MUST surface the "Payment link is not
    configured yet" copy instead.
    """
    if not url or not url.strip():
        raise CommerceCheckoutUrlUnrenderable(
            "checkout_url is empty; caller must emit "
            "'Payment link is not configured yet' copy instead of rendering"
        )


def mint(
    *,
    intent_state_path: Path,
    decisions_log_path: Path,
    order_id: str,
    originating_message_id: str,
    amount_cents: int,
    currency: str,
    chat_id: str,
    checkout_url_template: str = "",
    now: Optional[datetime] = None,
) -> PaymentLinkResult:
    """Mint a new intent (slice 1: provider=placeholder only).

    Idempotent on order_id: re-minting returns the existing live intent
    (status != voided/refunded/chargeback) unchanged.
    """
    if amount_cents <= 0:
        return PaymentLinkResult(False, None, "amount_must_be_positive")
    now = now or datetime.now(timezone.utc)
    store = load_intent_store(intent_state_path)

    # Idempotency: one live intent per order_id
    live = [
        i for i in store.intents
        if i.order_id == order_id
        and i.status not in {"voided", "refunded", "chargeback"}
    ]
    if live:
        return PaymentLinkResult(True, live[0], "already_minted_idempotent")

    intent_id = _next_intent_id(store)
    checkout_url = _render_checkout_url(
        checkout_url_template,
        order_id=order_id,
        intent_id=intent_id,
        amount_cents=amount_cents,
        currency=currency,
        chat_id=chat_id,
    )
    intent = CommercePaymentIntent(
        intent_id=intent_id,
        order_id=order_id,
        originating_message_id=originating_message_id,
        amount_cents=amount_cents,
        currency=currency,
        provider="placeholder",
        checkout_url=checkout_url,
        status="minted",
        payment_reference="",
        created_at=now,
        updated_at=now,
    )
    store.intents.append(intent)
    write_intent_store(intent_state_path, store)
    emit(
        decisions_log_path,
        {
            "type": "commerce_payment_intent_minted",
            "ts": now.isoformat(),
            "intent_id": intent_id,
            "order_id": order_id,
            "originating_message_id": originating_message_id,
            "amount_cents": amount_cents,
            "currency": currency,
            "provider": "placeholder",
        },
    )
    return PaymentLinkResult(True, intent)


def mark_attempted(
    *,
    intent_state_path: Path,
    decisions_log_path: Path,
    intent_id: str,
    now: Optional[datetime] = None,
) -> PaymentLinkResult:
    """Emit the *_attempted row before a caller sends the link.

    Per Reviewer A MEDIUM-3 attempted/sent/failed triple. Slice 1 has no
    real network send but the attempted event preserves the audit shape for
    slice 2 swap-in.
    """
    now = now or datetime.now(timezone.utc)
    store = load_intent_store(intent_state_path)
    intent = next((i for i in store.intents if i.intent_id == intent_id), None)
    if intent is None:
        return PaymentLinkResult(False, None, "intent_not_found")
    emit(
        decisions_log_path,
        {
            "type": "commerce_payment_link_attempted",
            "ts": now.isoformat(),
            "intent_id": intent_id,
            "order_id": intent.order_id,
        },
    )
    return PaymentLinkResult(True, intent)


def mark_sent(
    *,
    intent_state_path: Path,
    decisions_log_path: Path,
    intent_id: str,
    now: Optional[datetime] = None,
) -> PaymentLinkResult:
    now = now or datetime.now(timezone.utc)
    store = load_intent_store(intent_state_path)
    intent = next((i for i in store.intents if i.intent_id == intent_id), None)
    if intent is None:
        return PaymentLinkResult(False, None, "intent_not_found")
    updated = intent.model_copy(update={"status": "sent", "updated_at": now})
    _replace(store, updated)
    write_intent_store(intent_state_path, store)
    emit(
        decisions_log_path,
        {
            "type": "commerce_payment_link_sent",
            "ts": now.isoformat(),
            "intent_id": intent_id,
            "order_id": intent.order_id,
        },
    )
    return PaymentLinkResult(True, updated)


def void(
    *,
    intent_state_path: Path,
    decisions_log_path: Path,
    intent_id: str,
    reason: str,
    actor: str = "operator",
    now: Optional[datetime] = None,
) -> PaymentLinkResult:
    now = now or datetime.now(timezone.utc)
    store = load_intent_store(intent_state_path)
    intent = next((i for i in store.intents if i.intent_id == intent_id), None)
    if intent is None:
        return PaymentLinkResult(False, None, "intent_not_found")
    if intent.status in {"confirmed", "refunded", "chargeback"}:
        return PaymentLinkResult(False, intent, f"cannot_void_{intent.status}")
    updated = intent.model_copy(
        update={"status": "voided", "voided_at": now, "updated_at": now}
    )
    _replace(store, updated)
    write_intent_store(intent_state_path, store)
    emit(
        decisions_log_path,
        {
            "type": "commerce_payment_intent_voided",
            "ts": now.isoformat(),
            "intent_id": intent_id,
            "order_id": intent.order_id,
            "reason": reason,
            "actor": actor,
        },
    )
    return PaymentLinkResult(True, updated)


def register_reference(
    *,
    reference_ledger_path: Path,
    decisions_log_path: Path,
    payment_reference: str,
    order_id: str,
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    """Append a payment_reference to the immutable ledger.

    Returns (ok, detail). On cross-order reuse attempt: returns
    (False, "dedup_blocked") AND emits commerce_payment_dedup_blocked.
    Raises CommercePaymentReferenceReuse only if caller uses the
    strict variant (register_reference_strict).
    """
    payment_reference = " ".join((payment_reference or "").split())
    if not payment_reference:
        return False, "payment_reference_required"
    now = now or datetime.now(timezone.utc)
    ledger = load_reference_ledger(reference_ledger_path)
    prior = ledger.references.get(payment_reference)
    if prior is not None and prior != order_id:
        emit(
            decisions_log_path,
            {
                "type": "commerce_payment_dedup_blocked",
                "ts": now.isoformat(),
                "reference": payment_reference,
                "attempted_order_id": order_id,
                "original_order_id": prior,
            },
        )
        return False, f"dedup_blocked:original_order_id={prior}"
    if prior == order_id:
        return True, "noop_same_order"
    ledger.references[payment_reference] = order_id
    write_reference_ledger(reference_ledger_path, ledger)
    return True, "registered"


def register_reference_strict(
    *,
    reference_ledger_path: Path,
    decisions_log_path: Path,
    payment_reference: str,
    order_id: str,
    now: Optional[datetime] = None,
) -> None:
    """Same as register_reference but raises on dedup block (for fail-closed callers)."""
    ok, detail = register_reference(
        reference_ledger_path=reference_ledger_path,
        decisions_log_path=decisions_log_path,
        payment_reference=payment_reference,
        order_id=order_id,
        now=now,
    )
    if not ok and detail.startswith("dedup_blocked:"):
        original = detail.split("original_order_id=", 1)[1]
        raise CommercePaymentReferenceReuse(payment_reference, original)
    if not ok:
        raise ValueError(detail)


def _replace(store: CommercePaymentIntentStore, intent: CommercePaymentIntent) -> None:
    store.intents = [intent if i.intent_id == intent.intent_id else i for i in store.intents]
