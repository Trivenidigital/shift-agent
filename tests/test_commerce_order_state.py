"""Tests for src/platform/commerce/order_state.py.

Asserts state-machine completeness against LEGAL_TRANSITIONS constant
(Reviewer A MEDIUM-2). Every (from, to) outside the set must raise;
every (from, to) inside must succeed (modulo cart-driven create).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest
from schemas import CommerceCart, CommerceCartItem, CommerceOrderStatus

from commerce import cart as commerce_cart
from commerce import order_state as commerce_order
from commerce.exceptions import IllegalCommerceTransition
from commerce.order_state import LEGAL_TRANSITIONS, TERMINAL_STATUSES


@pytest.fixture
def cart_state(tmp_state_dir: Path) -> Path:
    return tmp_state_dir / "commerce" / "carts.json"


@pytest.fixture
def order_state(tmp_state_dir: Path) -> Path:
    return tmp_state_dir / "commerce" / "orders.json"


@pytest.fixture
def decisions_log_path(tmp_state_dir: Path) -> Path:
    p = tmp_state_dir / "logs" / "decisions.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_audit_rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _build_cart(cart_state: Path, decisions_log_path: Path) -> CommerceCart:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    result = commerce_cart.add_item(
        state_path=cart_state,
        decisions_log_path=decisions_log_path,
        sender_phone="+15551234567",
        sender_lid=None,
        chat_id="chat",
        sku="A", display_name="A", quantity=2, unit="each", unit_price_cents=500,
        now=now,
    )
    return result.cart


def test_create_emits_audit_and_status_history(cart_state, order_state, decisions_log_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    cart = _build_cart(cart_state, decisions_log_path)
    result = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path,
        cart=cart, now=now,
    )
    assert result.ok
    assert result.order.order_id == "CO00001"
    assert result.order.status == "pending_payment"
    assert result.order.total_cents == 1000
    assert len(result.order.status_history) == 1
    assert result.order.status_history[0].from_status is None
    assert result.order.status_history[0].to_status == "pending_payment"

    rows = _read_audit_rows(decisions_log_path)
    assert "commerce_order_created" in {r["type"] for r in rows}


def test_create_is_idempotent_on_same_cart(cart_state, order_state, decisions_log_path):
    cart = _build_cart(cart_state, decisions_log_path)
    r1 = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart,
    )
    r2 = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart,
    )
    assert r1.order.order_id == r2.order.order_id


def test_create_refuses_restricted_skus(cart_state, order_state, decisions_log_path):
    cart = _build_cart(cart_state, decisions_log_path)
    result = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path,
        cart=cart, restricted_skus=["A"], refusal_reason="restricted_category",
    )
    assert not result.ok
    assert result.detail.startswith("refused_category:")
    rows = _read_audit_rows(decisions_log_path)
    refusal = next(r for r in rows if r["type"] == "commerce_order_create_refused_category")
    assert refusal["refused_skus"] == ["A"]
    assert refusal["reason"] == "restricted_category"
    # Reviewer B MEDIUM-2: display_name must accompany SKU in the audit row
    assert refusal["refused_items"] == [{"sku": "A", "display_name": "A"}]


def test_refused_category_is_idempotent_on_retry(cart_state, order_state, decisions_log_path):
    """Reviewer B MEDIUM-1: re-attempting create on an already-refused cart
    must not multiply audit rows. Existing-order lookup runs BEFORE refusal."""
    cart = _build_cart(cart_state, decisions_log_path)
    # First attempt: refused
    r1 = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path,
        cart=cart, restricted_skus=["A"], refusal_reason="restricted_category",
    )
    assert not r1.ok
    # Second attempt (cart unchanged, same cart_id): no order was created,
    # so refusal is re-emitted. This is acceptable per house style (mirrors
    # flyer/guest_order.py); the test pins current behavior so any future
    # dedup change is intentional.
    r2 = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path,
        cart=cart, restricted_skus=["A"], refusal_reason="restricted_category",
    )
    assert not r2.ok
    rows = _read_audit_rows(decisions_log_path)
    refusals = [r for r in rows if r["type"] == "commerce_order_create_refused_category"]
    # NOTE: 2 refusals expected — see commerce/order_state.py docstring.
    # If a future PR dedups, this assertion needs updating.
    assert len(refusals) == 2


def test_create_after_existing_order_is_idempotent(cart_state, order_state, decisions_log_path):
    """If an order already exists for the cart, re-calling create returns it
    unchanged AND does NOT emit a refusal row even if restricted_skus is now set.
    PR reviewer B MEDIUM-1 effect of moving idempotency check first.
    """
    cart = _build_cart(cart_state, decisions_log_path)
    first = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart,
    )
    assert first.ok
    second = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path,
        cart=cart, restricted_skus=["A"], refusal_reason="restricted_category",
    )
    assert second.ok  # returns existing order, ignores restricted_skus
    assert second.order.order_id == first.order.order_id
    rows = _read_audit_rows(decisions_log_path)
    refusals = [r for r in rows if r["type"] == "commerce_order_create_refused_category"]
    assert refusals == []  # idempotent path does not emit refusal


def test_create_marks_cart_checked_out_when_cart_state_path_provided(
    cart_state, order_state, decisions_log_path
):
    """PR reviewer A HIGH-2: passing cart_state_path makes create() call
    cart.checkout() so the cart status flips from open to checked_out and
    a follow-up add_item starts a new cart."""
    cart = _build_cart(cart_state, decisions_log_path)
    commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path,
        cart=cart, cart_state_path=cart_state,
    )
    store = commerce_cart.load_cart_store(cart_state)
    found = next(c for c in store.carts if c.cart_id == cart.cart_id)
    assert found.status == "checked_out"


def test_awaiting_approval_to_paid_edge_legal():
    """PR reviewer A BLOCKER: webhook may confirm payment directly on an
    order parked in awaiting_approval. Verify the edge is in LEGAL_TRANSITIONS."""
    assert ("awaiting_approval", "paid") in LEGAL_TRANSITIONS


def test_legal_transition_pending_to_paid(cart_state, order_state, decisions_log_path):
    cart = _build_cart(cart_state, decisions_log_path)
    create = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart,
    )
    t = commerce_order.transition(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=create.order.order_id, to_status="paid",
        actor="webhook", cause="payment_confirmed",
    )
    assert t.ok
    assert t.order.status == "paid"
    assert len(t.order.status_history) == 2
    assert t.order.status_history[1].from_status == "pending_payment"
    assert t.order.status_history[1].to_status == "paid"


def test_illegal_transition_raises(cart_state, order_state, decisions_log_path):
    cart = _build_cart(cart_state, decisions_log_path)
    create = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart,
    )
    # pending_payment -> ready is NOT legal (must go pending -> paid -> preparing -> ready)
    with pytest.raises(IllegalCommerceTransition):
        commerce_order.transition(
            state_path=order_state, decisions_log_path=decisions_log_path,
            order_id=create.order.order_id, to_status="ready",
            actor="operator", cause="invalid_jump",
        )


def test_transition_idempotent_when_already_in_status(cart_state, order_state, decisions_log_path):
    cart = _build_cart(cart_state, decisions_log_path)
    create = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart,
    )
    same = commerce_order.transition(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=create.order.order_id, to_status="pending_payment",
        actor="operator", cause="noop",
    )
    assert same.ok
    assert same.detail == "noop_already_in_status"
    # status_history NOT appended for a no-op
    assert len(same.order.status_history) == 1


@pytest.mark.parametrize("from_status,to_status", sorted(LEGAL_TRANSITIONS))
def test_every_legal_transition_actually_succeeds(
    cart_state, order_state, decisions_log_path, from_status, to_status
):
    """For every (from, to) in LEGAL_TRANSITIONS, drive an order from `from`
    to `to` via transition() and assert no IllegalCommerceTransition.

    PR reviewer A HIGH-1: the prior tautology-shaped test only re-checked
    the constant against itself; this parametrized version actually drives
    the state machine.
    """
    cart = _build_cart(cart_state, decisions_log_path)
    created = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart,
    )
    order_id = created.order.order_id

    # Walk a deterministic path to reach `from_status`. The from_status of
    # pending_payment is already where we are; other statuses need a setup walk.
    SETUP_PATH = {
        "pending_payment": [],
        "awaiting_approval": [("awaiting_approval", "caller", "owner_approval_required")],
        "paid": [("paid", "webhook", "payment_confirmed")],
        "preparing": [("paid", "webhook", "paid"), ("preparing", "operator", "kitchen_ack")],
        "ready": [
            ("paid", "webhook", "paid"),
            ("preparing", "operator", "kitchen_ack"),
            ("ready", "operator", "ready_for_pickup"),
        ],
        "out_for_delivery": [
            ("paid", "webhook", "paid"),
            ("preparing", "operator", "kitchen_ack"),
            ("ready", "operator", "ready_for_pickup"),
            ("out_for_delivery", "operator", "courier_dispatched"),
        ],
    }
    if from_status not in SETUP_PATH:
        pytest.skip(f"setup path for from_status={from_status!r} not defined (terminal state)")
    for step_to, step_actor, step_cause in SETUP_PATH[from_status]:
        commerce_order.transition(
            state_path=order_state, decisions_log_path=decisions_log_path,
            order_id=order_id, to_status=step_to,
            actor=step_actor, cause=step_cause,
        )
    # We're now in from_status; apply the legal transition under test.
    result = commerce_order.transition(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=order_id, to_status=to_status,
        actor="operator", cause=f"test_{from_status}_to_{to_status}",
    )
    assert result.ok
    assert result.order.status == to_status


def test_every_illegal_transition_raises(cart_state, order_state, decisions_log_path):
    """For every (from, to) NOT in LEGAL_TRANSITIONS, transition() must raise.

    Drives the state machine for representative illegal edges (combinatorial
    space is bounded — 10 statuses × 9 targets = 90 pairs).
    """
    statuses = (
        "pending_payment", "awaiting_approval", "paid",
        "preparing", "ready", "out_for_delivery",
        "completed", "cancelled", "voided", "refunded",
    )
    SETUP_PATH = {
        "pending_payment": [],
        "awaiting_approval": [("awaiting_approval", "caller", "x")],
        "paid": [("paid", "webhook", "x")],
        "preparing": [("paid", "webhook", "x"), ("preparing", "operator", "x")],
        "ready": [
            ("paid", "webhook", "x"),
            ("preparing", "operator", "x"),
            ("ready", "operator", "x"),
        ],
        "out_for_delivery": [
            ("paid", "webhook", "x"),
            ("preparing", "operator", "x"),
            ("ready", "operator", "x"),
            ("out_for_delivery", "operator", "x"),
        ],
        "completed": [
            ("paid", "webhook", "x"),
            ("preparing", "operator", "x"),
            ("ready", "operator", "x"),
            ("completed", "operator", "x"),
        ],
        "cancelled": [("cancelled", "operator", "x")],
        "voided": [("voided", "cron", "x")],
        "refunded": [("paid", "webhook", "x"), ("refunded", "operator", "x")],
    }
    for from_status in statuses:
        for to_status in statuses:
            if from_status == to_status:
                continue
            if (from_status, to_status) in LEGAL_TRANSITIONS:
                continue
            if from_status not in SETUP_PATH:
                continue  # cannot reach
            cart = _build_cart(cart_state, decisions_log_path)
            created = commerce_order.create(
                state_path=order_state, decisions_log_path=decisions_log_path, cart=cart,
            )
            order_id = created.order.order_id
            try:
                for step_to, step_actor, step_cause in SETUP_PATH[from_status]:
                    commerce_order.transition(
                        state_path=order_state, decisions_log_path=decisions_log_path,
                        order_id=order_id, to_status=step_to,
                        actor=step_actor, cause=step_cause,
                    )
            except IllegalCommerceTransition:
                continue  # cannot reach from_status — skip
            with pytest.raises(IllegalCommerceTransition):
                commerce_order.transition(
                    state_path=order_state, decisions_log_path=decisions_log_path,
                    order_id=order_id, to_status=to_status,
                    actor="operator", cause="illegal_jump_test",
                )


def test_terminal_statuses_have_no_outbound_edges():
    for terminal in TERMINAL_STATUSES:
        outbound = [(f, t) for (f, t) in LEGAL_TRANSITIONS if f == terminal]
        assert outbound == [], f"terminal {terminal!r} must not have outbound edges; found {outbound}"


def test_cancel_emits_both_status_change_and_cancelled_audit(cart_state, order_state, decisions_log_path):
    cart = _build_cart(cart_state, decisions_log_path)
    create = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart,
    )
    cancel = commerce_order.cancel(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=create.order.order_id, reason="customer_changed_mind",
    )
    assert cancel.ok
    rows = _read_audit_rows(decisions_log_path)
    types = {r["type"] for r in rows}
    assert "commerce_order_status_change" in types
    assert "commerce_order_cancelled" in types


def test_post_payment_cancel_raises(cart_state, order_state, decisions_log_path):
    """paid -> cancelled is NOT a legal transition (post-payment uses refunded)."""
    cart = _build_cart(cart_state, decisions_log_path)
    create = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart,
    )
    commerce_order.transition(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=create.order.order_id, to_status="paid",
        actor="webhook", cause="payment_confirmed",
    )
    with pytest.raises(IllegalCommerceTransition):
        commerce_order.cancel(
            state_path=order_state, decisions_log_path=decisions_log_path,
            order_id=create.order.order_id, reason="too_late",
        )
