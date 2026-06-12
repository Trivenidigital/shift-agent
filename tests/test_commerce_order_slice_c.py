"""Slice-C primitive tests for src/platform/commerce/order_state.py.

Covers the two write-safety additions:
  1. `expected_from_status` optimistic-concurrency guard (transition + cancel).
  2. The shared FileLock around create()/transition() read-modify-write —
     a lost-update regression test using real threads (Linux only; the lock is
     a deliberate no-op on Windows dev/test where fcntl is unavailable).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from schemas import CommerceCart

from commerce import cart as commerce_cart
from commerce import order_state as commerce_order
from commerce.exceptions import IllegalCommerceTransition

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


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


def _read_audit_types(p: Path) -> list[str]:
    if not p.exists():
        return []
    return [json.loads(line)["type"]
            for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _make_cart(cart_state: Path, decisions_log_path: Path, *, phone: str, sku: str) -> CommerceCart:
    return commerce_cart.add_item(
        state_path=cart_state, decisions_log_path=decisions_log_path,
        sender_phone=phone, sender_lid=None, chat_id=phone,
        sku=sku, display_name=sku, quantity=1, unit="each", unit_price_cents=500,
        now=NOW,
    ).cart


def _seed_paid_order(cart_state, order_state, decisions_log_path) -> str:
    """Create an order and advance it to `paid` so progress transitions apply."""
    cart = _make_cart(cart_state, decisions_log_path, phone="+15551230000", sku="A")
    oid = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart, now=NOW,
    ).order.order_id
    commerce_order.transition(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=oid, to_status="paid", actor="webhook", cause="paid", now=NOW,
    )
    return oid


# ── expected_from_status optimistic-concurrency guard ───────────────────────

def test_transition_with_matching_expected_status_succeeds(cart_state, order_state, decisions_log_path):
    oid = _seed_paid_order(cart_state, order_state, decisions_log_path)
    res = commerce_order.transition(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=oid, to_status="preparing", actor="operator", cause="go",
        expected_from_status="paid", now=NOW,
    )
    assert res.ok and res.order.status == "preparing"


def test_transition_with_stale_expected_status_refuses_without_write(cart_state, order_state, decisions_log_path):
    oid = _seed_paid_order(cart_state, order_state, decisions_log_path)
    before = order_state.read_bytes()
    res = commerce_order.transition(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=oid, to_status="preparing", actor="operator", cause="go",
        expected_from_status="pending_payment",  # stale — order is actually paid
        now=NOW,
    )
    assert not res.ok
    assert res.detail == "stale_expected_status"
    assert res.order.status == "paid"  # unchanged
    assert order_state.read_bytes() == before  # NO write happened


def test_transition_without_expected_status_keeps_legacy_behavior(cart_state, order_state, decisions_log_path):
    oid = _seed_paid_order(cart_state, order_state, decisions_log_path)
    res = commerce_order.transition(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=oid, to_status="preparing", actor="operator", cause="go", now=NOW,
    )
    assert res.ok and res.order.status == "preparing"


def test_cancel_forwards_expected_from_status(cart_state, order_state, decisions_log_path):
    cart = _make_cart(cart_state, decisions_log_path, phone="+15551231111", sku="A")
    oid = commerce_order.create(
        state_path=order_state, decisions_log_path=decisions_log_path, cart=cart, now=NOW,
    ).order.order_id  # status pending_payment
    # Stale expected → refused, no cancel.
    stale = commerce_order.cancel(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=oid, reason="changed mind", actor="operator",
        expected_from_status="awaiting_approval", now=NOW,
    )
    assert not stale.ok and stale.detail == "stale_expected_status"
    assert commerce_order.get(order_state, oid).status == "pending_payment"
    # Correct expected → cancels + emits commerce_order_cancelled.
    ok = commerce_order.cancel(
        state_path=order_state, decisions_log_path=decisions_log_path,
        order_id=oid, reason="changed mind", actor="operator",
        expected_from_status="pending_payment", now=NOW,
    )
    assert ok.ok and commerce_order.get(order_state, oid).status == "cancelled"
    assert "commerce_order_cancelled" in _read_audit_types(decisions_log_path)


def test_illegal_transition_still_raises_under_lock(cart_state, order_state, decisions_log_path):
    oid = _seed_paid_order(cart_state, order_state, decisions_log_path)
    with pytest.raises(IllegalCommerceTransition):
        commerce_order.transition(
            state_path=order_state, decisions_log_path=decisions_log_path,
            order_id=oid, to_status="completed", actor="operator", cause="x",
            expected_from_status="paid", now=NOW,
        )


# ── shared FileLock: lost-update regression (real concurrency) ──────────────

@pytest.mark.skipif(
    os.name == "nt",
    reason="file_lock is a deliberate no-op on Windows dev/test (fcntl is Unix-only); "
           "real serialization is only exercised on the Linux CI runner / VPS.",
)
def test_concurrent_creates_do_not_lost_update(cart_state, order_state, decisions_log_path):
    """Two concurrent create() calls on the SAME orders store must BOTH persist.

    Without the shared FileLock both threads load the same store, append, and
    write — last-writer-wins drops one order. With the lock they serialize."""
    carts = [
        _make_cart(cart_state, decisions_log_path, phone=f"+1555000{i:04d}", sku=f"S{i}")
        for i in range(12)
    ]
    barrier = threading.Barrier(len(carts))
    errors: list[BaseException] = []

    def worker(cart: CommerceCart) -> None:
        try:
            barrier.wait()
            commerce_order.create(
                state_path=order_state, decisions_log_path=decisions_log_path,
                cart=cart, now=NOW,
            )
        except BaseException as e:  # noqa: BLE001 — surface any thread error
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(c,)) for c in carts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"worker errors: {errors}"
    store = commerce_order.load_order_store(order_state)
    # Every distinct cart must have produced exactly one persisted order.
    assert len(store.orders) == len(carts)
    assert len({o.order_id for o in store.orders}) == len(carts)
    assert {o.cart_id for o in store.orders} == {c.cart_id for c in carts}


@pytest.mark.skipif(os.name == "nt", reason="no-op lock on Windows (see above)")
def test_concurrent_transitions_preserve_status_history(cart_state, order_state, decisions_log_path):
    """Racing progress transitions on one order must not drop status_history
    entries: exactly one of two competing transitions applies, the loser sees
    the post-lock state (idempotent noop or a fresh legal step)."""
    oid = _seed_paid_order(cart_state, order_state, decisions_log_path)
    results: list[str] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        r = commerce_order.transition(
            state_path=order_state, decisions_log_path=decisions_log_path,
            order_id=oid, to_status="preparing", actor="operator", cause="go",
            expected_from_status="paid", now=NOW,
        )
        results.append(r.detail or ("ok" if r.ok else "fail"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    order = commerce_order.get(order_state, oid)
    assert order.status == "preparing"
    # One real transition (paid→preparing) + the create/seed history; no dup
    # preparing events from a lost update.
    preparing_events = [e for e in order.status_history if e.to_status == "preparing"]
    assert len(preparing_events) == 1
