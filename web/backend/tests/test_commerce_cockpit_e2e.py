"""Commerce Order Cockpit full-lifecycle E2E (dry-run, fake state).

Integration coverage tying Slice A (fulfillment fields) + Slice B (read-only
inbox/detail/audit) + Slice C (owner status transitions) together. The per-route
tests (test_commerce_orders.py, test_commerce_transition.py) cover each surface
in isolation; this drives a real order through its whole lifecycle via the
transition ROUTE and asserts the READ API (inbox totals/open_count,
payment_status inference, detail status_history, decisions.log audit join)
reflects every write.

Maps to the operator's post-cockpit-deploy visual-verification checklist:
empty state renders cleanly; owner actions advance status; terminal orders are
inert; commerce stays dormant (no provider/POS/customer-send anywhere).

All against a tmp state dir — no deploy, no live bridge, no prod mutation.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

pytest.importorskip("fastapi")


class _Req:
    """Minimal Request stand-in (client_ip/client_ua only)."""
    def __init__(self) -> None:
        self.client = type("C", (), {"host": "127.0.0.1"})()
        self.headers = {"user-agent": "pytest-e2e"}


def _set_paths(tmp_path: Path):
    from app import audit as audit_mod
    from app.routers import commerce
    s = commerce.get_settings()
    s.state_dir = tmp_path / "state"
    s.decisions_path = tmp_path / "logs" / "decisions.log"
    s.config_path = tmp_path / "config.yaml"
    s.cockpit_audit_log = tmp_path / "logs" / "cockpit-audit.log"
    audit_mod.settings.cockpit_audit_log = s.cockpit_audit_log
    return commerce, s


def _seed_paid(commerce, s, *, phone: str, fulfillment_type: str) -> str:
    """Create an order via primitives and advance it to `paid` (the precondition
    for cockpit fulfillment actions — `->paid` itself is provider-only and not
    exposed by the cockpit). `actor="webhook"` mirrors provider confirmation."""
    from commerce import cart as commerce_cart
    from commerce import order_state
    cart = commerce_cart.add_item(
        state_path=s.state_dir / "commerce" / "carts.json",
        decisions_log_path=s.decisions_path,
        sender_phone=phone, sender_lid=None, chat_id=phone,
        sku="samosa", display_name="Samosa", quantity=2, unit="each",
        unit_price_cents=350,
    ).cart
    oid = order_state.create(
        state_path=commerce._orders_path(), decisions_log_path=s.decisions_path,
        cart=cart,
    ).order.order_id
    # set fulfillment metadata (Slice A fields) directly on the stored order
    store_path = commerce._orders_path()
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    for o in raw["orders"]:
        if o["order_id"] == oid:
            o["fulfillment_type"] = fulfillment_type
            if fulfillment_type == "delivery":
                o["delivery_address"] = "123 Main St"
    store_path.write_text(json.dumps(raw), encoding="utf-8")
    order_state.transition(
        state_path=store_path, decisions_log_path=s.decisions_path,
        order_id=oid, to_status="paid", actor="webhook", cause="provider_confirmed",
    )
    return oid


def _list(commerce) -> dict:
    return asyncio.run(commerce.list_orders(_=None))


def _detail(commerce, oid: str) -> dict:
    return asyncio.run(commerce.get_order(oid, _=None))


def _transition(commerce, oid: str, to_status: str, expected_from: str, cause: str = "") -> dict:
    return asyncio.run(commerce.transition_order(
        oid, commerce.OrderTransitionBody(
            to_status=to_status, expected_from_status=expected_from, cause=cause),
        _Req(), _=None))


def _row(listing: dict, oid: str) -> dict:
    return next(r for r in listing["orders"] if r["order_id"] == oid)


# ── empty state renders cleanly (dormant Commerce, no orders) ───────────────

def test_empty_inbox_renders_cleanly(tmp_path):
    commerce, _ = _set_paths(tmp_path)
    listing = _list(commerce)
    assert listing["orders"] == []
    assert listing["totals"] == {"order_count": 0, "open_count": 0, "gross_cents_by_currency": {}}
    assert listing["degraded"] is None
    # owner action on a non-existent order is harmless: 404, no crash
    with pytest.raises(HTTPException) as ei:
        _transition(commerce, "CO00001", "preparing", "paid")
    assert ei.value.status_code == 404


# ── full PICKUP lifecycle: read API reflects every write ────────────────────

def test_full_pickup_lifecycle_read_reflects_writes(tmp_path):
    commerce, s = _set_paths(tmp_path)
    oid = _seed_paid(commerce, s, phone="+15551110001", fulfillment_type="pickup")

    # paid: appears in inbox, open, payment_status=paid
    row = _row(_list(commerce), oid)
    assert row["status"] == "paid" and row["payment_status"] == "paid"
    assert row["fulfillment_type"] == "pickup"
    assert _list(commerce)["totals"]["open_count"] == 1

    # drive paid -> preparing -> ready -> completed via the cockpit route
    chain = [("preparing", "paid"), ("ready", "preparing"), ("completed", "ready")]
    for to_status, expected_from in chain:
        res = _transition(commerce, oid, to_status, expected_from)
        assert res["order"]["status"] == to_status
        # READ inbox reflects the new status immediately
        assert _row(_list(commerce), oid)["status"] == to_status
        # READ detail: status_history grew with this transition + audit join has it
        detail = _detail(commerce, oid)
        hist = detail["order"]["status_history"]
        assert hist[-1]["from_status"] == expected_from and hist[-1]["to_status"] == to_status
        assert hist[-1]["actor"] == "operator"
        assert any(a.get("type") == "commerce_order_status_change"
                   and a.get("next_status") == to_status for a in detail["audit"])

    # terminal: completed leaves open_count 0 and is inert to further actions
    assert _list(commerce)["totals"]["open_count"] == 0
    with pytest.raises(HTTPException) as ei:
        _transition(commerce, oid, "preparing", "completed")  # not allowlisted from terminal
    assert ei.value.status_code == 409


# ── full DELIVERY lifecycle (adds out_for_delivery leg) ─────────────────────

def test_full_delivery_lifecycle(tmp_path):
    commerce, s = _set_paths(tmp_path)
    oid = _seed_paid(commerce, s, phone="+15552220002", fulfillment_type="delivery")
    assert _detail(commerce, oid)["order"]["delivery_address"] == "123 Main St"

    chain = [("preparing", "paid"), ("ready", "preparing"),
             ("out_for_delivery", "ready"), ("completed", "out_for_delivery")]
    for to_status, expected_from in chain:
        res = _transition(commerce, oid, to_status, expected_from)
        assert res["order"]["status"] == to_status
        assert _row(_list(commerce), oid)["status"] == to_status

    assert _detail(commerce, oid)["order"]["status"] == "completed"
    assert _list(commerce)["totals"]["open_count"] == 0


# ── pre-payment cancel E2E (the only cancel the cockpit exposes) ─────────────

def test_pre_payment_cancel_lifecycle(tmp_path):
    commerce, s = _set_paths(tmp_path)
    # seed an order left at pending_payment (no provider confirmation)
    from commerce import cart as commerce_cart
    from commerce import order_state
    cart = commerce_cart.add_item(
        state_path=s.state_dir / "commerce" / "carts.json",
        decisions_log_path=s.decisions_path,
        sender_phone="+15553330003", sender_lid=None, chat_id="c",
        sku="dosa", display_name="Dosa", quantity=1, unit="each", unit_price_cents=500,
    ).cart
    oid = order_state.create(
        state_path=commerce._orders_path(), decisions_log_path=s.decisions_path, cart=cart,
    ).order.order_id

    assert _row(_list(commerce), oid)["status"] == "pending_payment"
    assert _list(commerce)["totals"]["open_count"] == 1

    res = _transition(commerce, oid, "cancelled", "pending_payment", cause="customer changed mind")
    assert res["order"]["status"] == "cancelled"

    # READ reflects cancel: terminal, dropped from open_count, audit has both rows
    assert _row(_list(commerce), oid)["status"] == "cancelled"
    assert _list(commerce)["totals"]["open_count"] == 0
    audit_types = {a.get("type") for a in _detail(commerce, oid)["audit"]}
    assert "commerce_order_cancelled" in audit_types
    assert _row(_list(commerce), oid)["payment_status"] == "none"


# ── dormant-safe boundary: cockpit refuses money/post-payment transitions ──

def test_cockpit_refuses_money_and_postpayment_transitions(tmp_path):
    """The cockpit must NOT expose ->paid (money claim), ->refunded (money move),
    or preparing->cancelled (post-payment cancel w/o refund path). Each is legal
    globally but excluded from the Slice-C allowlist; the route refuses with 409
    + an audited commerce_order_action_refused(reason=not_allowed_in_slice_c).
    Asserting this inside the full E2E harness (not just per-route) guards the
    central money/send-safety boundary."""
    from commerce import cart as commerce_cart
    from commerce import order_state
    commerce, s = _set_paths(tmp_path)

    # pending_payment order (for the manual mark-paid attempt)
    cart = commerce_cart.add_item(
        state_path=s.state_dir / "commerce" / "carts.json",
        decisions_log_path=s.decisions_path,
        sender_phone="+15556660006", sender_lid=None, chat_id="c",
        sku="idli", display_name="Idli", quantity=1, unit="each", unit_price_cents=400,
    ).cart
    pending_oid = order_state.create(
        state_path=commerce._orders_path(), decisions_log_path=s.decisions_path, cart=cart,
    ).order.order_id

    # paid order (for the refund attempt) + preparing order (for post-pay cancel)
    paid_oid = _seed_paid(commerce, s, phone="+15556660007", fulfillment_type="pickup")
    prep_oid = _seed_paid(commerce, s, phone="+15556660008", fulfillment_type="pickup")
    _transition(commerce, prep_oid, "preparing", "paid")  # advance to preparing

    forbidden = [
        (pending_oid, "paid", "pending_payment"),     # manual mark-paid — money claim
        (paid_oid, "refunded", "paid"),               # refund — money move
        (prep_oid, "cancelled", "preparing"),         # post-payment cancel, no refund path
    ]
    for oid, to_status, expected_from in forbidden:
        with pytest.raises(HTTPException) as ei:
            _transition(commerce, oid, to_status, expected_from, cause="should be refused")
        assert ei.value.status_code == 409, f"{expected_from}->{to_status} should be 409"

    # every refusal audited as not_allowed_in_slice_c, and NO order changed status
    refused = [json.loads(line) for line in s.decisions_path.read_text().splitlines()
               if "commerce_order_action_refused" in line]
    for oid, to_status, _expected in forbidden:
        rows = [r for r in refused if r["order_id"] == oid and r["attempted_to_status"] == to_status]
        assert rows and rows[-1]["reason"] == "not_allowed_in_slice_c", \
            f"missing refusal audit for {oid} -> {to_status}"
    assert order_state.get(commerce._orders_path(), pending_oid).status == "pending_payment"
    assert order_state.get(commerce._orders_path(), paid_oid).status == "paid"
    assert order_state.get(commerce._orders_path(), prep_oid).status == "preparing"


# ── stale-guard across the read/write boundary (no silent clobber) ──────────

def test_stale_action_after_concurrent_advance_is_refused(tmp_path):
    commerce, s = _set_paths(tmp_path)
    oid = _seed_paid(commerce, s, phone="+15554440004", fulfillment_type="pickup")
    # operator A renders at "paid", then operator B advances it to "preparing"
    _transition(commerce, oid, "preparing", "paid")
    # operator A's stale "paid->preparing" click must be refused (409), no clobber
    with pytest.raises(HTTPException) as ei:
        _transition(commerce, oid, "preparing", "paid")
    assert ei.value.status_code == 409
    # the order is unchanged at preparing; refusal audited
    assert _detail(commerce, oid)["order"]["status"] == "preparing"
    refused = [json.loads(line) for line in s.decisions_path.read_text().splitlines()
               if "commerce_order_action_refused" in line]
    assert refused[-1]["reason"] == "stale_expected_status"


# ── read API never mutates state (Slice B invariant holds through E2E) ──────

def test_reads_do_not_mutate_state(tmp_path):
    commerce, s = _set_paths(tmp_path)
    oid = _seed_paid(commerce, s, phone="+15555550005", fulfillment_type="pickup")
    orders_file = commerce._orders_path()
    before = orders_file.read_bytes()
    _list(commerce)
    _detail(commerce, oid)
    assert orders_file.read_bytes() == before
