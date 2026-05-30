"""Read-only Commerce Order Cockpit router tests (Slice B).

Mirrors the flyer-admin test pattern: mutate the cached Settings paths to a
tmp dir, write state JSON, and call the async handlers directly via
asyncio.run(..., _=None) (bypassing the auth Depends). Covers empty-state,
populated list/detail, totals, payment-status inference, audit join,
404, and graceful missing/malformed state. The router is read-only — no test
asserts (or causes) any file mutation.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException


def _set_paths(tmp_path: Path):
    from app.routers import commerce
    s = commerce.get_settings()
    s.state_dir = tmp_path / "state"
    s.decisions_path = tmp_path / "logs" / "decisions.log"
    s.config_path = tmp_path / "config.yaml"
    return commerce, s


def _write_orders(state_dir: Path, orders: list[dict]):
    p = state_dir / "commerce" / "orders.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"orders": orders}, indent=2), encoding="utf-8")


def _order(order_id="CO00001", **over):
    base = {
        "order_id": order_id,
        "sender_phone": "+19045550123",
        "chat_id": "19045550123@s.whatsapp.net",
        "cart_id": "CC00001",
        "line_items": [{"sku": "samosa", "display_name": "Samosa", "quantity": 2,
                        "unit": "each", "unit_price_cents": 300, "line_total_cents": 600,
                        "added_at": "2026-05-30T00:00:00+00:00"}],
        "subtotal_cents": 600, "tax_cents": 0, "fee_cents": 0, "total_cents": 600,
        "currency": "USD", "status": "pending_payment",
        "payment_intent_id": "", "payment_reference": "",
        "status_history": [], "created_at": "2026-05-30T12:00:00+00:00",
        "updated_at": "2026-05-30T12:00:00+00:00",
        "fulfillment_type": "pickup", "customer_name": "Asha",
        "delivery_address": None, "requested_time": "2026-05-30T18:30:00+00:00",
        "order_notes": "no onions", "pos_sync_status": "not_synced",
    }
    base.update(over)
    return base


# ── empty state (Commerce dormant) ──────────────────────────────────────────

def test_list_empty_when_no_orders_file(tmp_path):
    commerce, _ = _set_paths(tmp_path)
    res = asyncio.run(commerce.list_orders(_=None))
    assert res["orders"] == []
    assert res["totals"] == {"order_count": 0, "open_count": 0, "gross_cents_by_currency": {}}
    assert res["degraded"] is None


def test_list_empty_when_state_dir_missing(tmp_path):
    commerce, _ = _set_paths(tmp_path)  # state_dir never created
    res = asyncio.run(commerce.list_orders(_=None))
    assert res["orders"] == [] and res["degraded"] is None


# ── populated list + totals + row shape ─────────────────────────────────────

def test_list_returns_rows_newest_first_with_totals(tmp_path):
    commerce, s = _set_paths(tmp_path)
    _write_orders(s.state_dir, [
        _order("CO00001", created_at="2026-05-30T10:00:00+00:00", status="paid",
               currency="USD", total_cents=600),
        _order("CO00002", created_at="2026-05-30T12:00:00+00:00", status="pending_payment",
               currency="USD", total_cents=1500, fulfillment_type="delivery",
               delivery_address="123 Main St"),
    ])
    res = asyncio.run(commerce.list_orders(_=None))
    assert [r["order_id"] for r in res["orders"]] == ["CO00002", "CO00001"]  # newest first
    assert res["totals"]["order_count"] == 2
    assert res["totals"]["open_count"] == 2  # paid + pending_payment both open
    assert res["totals"]["gross_cents_by_currency"] == {"USD": 2100}
    row = next(r for r in res["orders"] if r["order_id"] == "CO00002")
    assert row["customer_name"] == "Asha"
    assert row["fulfillment_type"] == "delivery"
    assert row["payment_status"] == "unpaid"  # pending_payment + no intent
    assert row["pos_sync_status"] == "not_synced"
    assert row["item_count"] == 1
    assert row["requested_time"] == "2026-05-30T18:30:00+00:00"


def test_totals_split_by_currency(tmp_path):
    commerce, s = _set_paths(tmp_path)
    _write_orders(s.state_dir, [
        _order("CO00001", currency="USD", total_cents=600, status="completed"),
        _order("CO00002", currency="INR", total_cents=9900, status="cancelled"),
    ])
    res = asyncio.run(commerce.list_orders(_=None))
    assert res["totals"]["gross_cents_by_currency"] == {"USD": 600, "INR": 9900}
    assert res["totals"]["open_count"] == 0  # completed + cancelled are terminal


# ── payment-status inference ────────────────────────────────────────────────

@pytest.mark.parametrize("status,intent,expected", [
    ("paid", "", "paid"),
    ("preparing", "", "paid"),
    ("out_for_delivery", "", "paid"),
    ("completed", "", "paid"),
    ("refunded", "", "refunded"),
    ("cancelled", "", "none"),
    ("voided", "", "none"),
    ("pending_payment", "pi_123", "pending"),
    ("pending_payment", "", "unpaid"),
    ("awaiting_approval", "", "unpaid"),
])
def test_payment_status_inference(tmp_path, status, intent, expected):
    commerce, s = _set_paths(tmp_path)
    _write_orders(s.state_dir, [_order("CO00001", status=status, payment_intent_id=intent)])
    res = asyncio.run(commerce.list_orders(_=None))
    assert res["orders"][0]["payment_status"] == expected


# ── detail + audit join ─────────────────────────────────────────────────────

def test_detail_returns_order_and_audit_join(tmp_path):
    commerce, s = _set_paths(tmp_path)
    _write_orders(s.state_dir, [_order("CO00007", order_notes="ring doorbell")])
    s.decisions_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "commerce_order_created", "ts": "2026-05-30T12:00:00+00:00", "order_id": "CO00007"},
        {"type": "flyer_something", "ts": "2026-05-30T12:01:00+00:00", "order_id": "CO00007"},  # excluded (not commerce_)
        {"type": "commerce_order_status_changed", "ts": "2026-05-30T12:05:00+00:00", "event_ref": "CO00007"},
        {"type": "commerce_order_created", "ts": "2026-05-30T12:00:00+00:00", "order_id": "CO99999"},  # other order
    ]
    s.decisions_path.write_text(
        "\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n", encoding="utf-8")
    res = asyncio.run(commerce.get_order("CO00007", _=None))
    assert res["order"]["order_notes"] == "ring doorbell"
    assert res["payment_status"] == "unpaid"
    types = [e["type"] for e in res["audit"]]
    assert types == ["commerce_order_created", "commerce_order_status_changed"]  # commerce-only, this order, oldest-first


def test_detail_unknown_order_404(tmp_path):
    commerce, s = _set_paths(tmp_path)
    _write_orders(s.state_dir, [_order("CO00001")])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(commerce.get_order("CO_NOPE", _=None))
    assert ei.value.status_code == 404


def test_detail_when_no_orders_file_404(tmp_path):
    commerce, _ = _set_paths(tmp_path)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(commerce.get_order("CO00001", _=None))
    assert ei.value.status_code == 404


# ── graceful malformed state ────────────────────────────────────────────────

def test_malformed_json_is_degraded_not_crash(tmp_path):
    commerce, s = _set_paths(tmp_path)
    p = s.state_dir / "commerce" / "orders.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not json", encoding="utf-8")
    res = asyncio.run(commerce.list_orders(_=None))
    assert res["orders"] == []
    assert res["degraded"] and "unreadable" in res["degraded"]


def test_json_without_orders_list_is_degraded(tmp_path):
    commerce, s = _set_paths(tmp_path)
    p = s.state_dir / "commerce" / "orders.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"unexpected": "shape"}), encoding="utf-8")
    res = asyncio.run(commerce.list_orders(_=None))
    assert res["orders"] == []
    assert res["degraded"] and "malformed" in res["degraded"]


def test_router_does_not_write_files(tmp_path):
    """Read-only invariant: listing/detail must not create or modify state."""
    commerce, s = _set_paths(tmp_path)
    _write_orders(s.state_dir, [_order("CO00001")])
    orders_file = s.state_dir / "commerce" / "orders.json"
    before = orders_file.read_bytes()
    asyncio.run(commerce.list_orders(_=None))
    asyncio.run(commerce.get_order("CO00001", _=None))
    assert orders_file.read_bytes() == before
    # no stray files created in commerce dir beyond orders.json
    assert sorted(p.name for p in (s.state_dir / "commerce").iterdir()) == ["orders.json"]
