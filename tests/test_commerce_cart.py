"""Tests for src/platform/commerce/cart.py.

Pattern mirrors tests/test_catering_v02_scripts.py:
deterministic state-mutation + audit-row assertions, no mocking of safe_io.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from commerce import cart as commerce_cart
from commerce.cart import CART_IDLE_TTL


@pytest.fixture
def state_path(tmp_state_dir: Path) -> Path:
    return tmp_state_dir / "commerce" / "carts.json"


@pytest.fixture
def decisions_log_path(tmp_state_dir: Path) -> Path:
    p = tmp_state_dir / "logs" / "decisions.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_audit_rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_add_item_creates_cart_with_phone(state_path, decisions_log_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    result = commerce_cart.add_item(
        state_path=state_path,
        decisions_log_path=decisions_log_path,
        sender_phone="+15551234567",
        sender_lid=None,
        chat_id="+15551234567@s.whatsapp.net",
        sku="GROC-RICE-5LB",
        display_name="Basmati Rice 5lb",
        quantity=2,
        unit="each",
        unit_price_cents=1299,
        now=now,
    )
    assert result.ok
    assert result.cart is not None
    assert result.cart.cart_id == "CC00001"
    assert result.cart.sender_phone == "+15551234567"
    assert result.cart.sender_lid is None
    assert len(result.cart.items) == 1
    assert result.cart.items[0].quantity == 2
    assert result.cart.items[0].line_total_cents == 2598
    assert result.cart.subtotal_cents == 2598
    assert result.cart.expires_at == now + CART_IDLE_TTL

    rows = _read_audit_rows(decisions_log_path)
    assert {r["type"] for r in rows} == {"commerce_cart_started", "commerce_cart_updated"}


def test_add_item_creates_cart_with_lid_only(state_path, decisions_log_path):
    """LID-only sender (no phone) — Reviewer A LOW-1 fix.

    Verifies the (phone_or_lid, chat_id) key works without an E164 phone.
    """
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    result = commerce_cart.add_item(
        state_path=state_path,
        decisions_log_path=decisions_log_path,
        sender_phone=None,
        sender_lid="201975216009469@lid",
        chat_id="201975216009469@lid",
        sku="GROC-DAL-1KG",
        display_name="Toor Dal 1kg",
        quantity=1,
        unit="each",
        unit_price_cents=599,
        now=now,
    )
    assert result.ok
    assert result.cart.sender_phone is None
    assert result.cart.sender_lid == "201975216009469@lid"


def test_add_item_appends_to_existing_cart(state_path, decisions_log_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    commerce_cart.add_item(
        state_path=state_path,
        decisions_log_path=decisions_log_path,
        sender_phone="+15551234567",
        sender_lid=None,
        chat_id="chat",
        sku="A", display_name="A", quantity=1, unit="each", unit_price_cents=100,
        now=now,
    )
    later = now + timedelta(minutes=10)
    result = commerce_cart.add_item(
        state_path=state_path,
        decisions_log_path=decisions_log_path,
        sender_phone="+15551234567",
        sender_lid=None,
        chat_id="chat",
        sku="A", display_name="A", quantity=2, unit="each", unit_price_cents=100,
        now=later,
    )
    assert result.cart.items[0].quantity == 3
    assert result.cart.items[0].line_total_cents == 300
    assert result.cart.subtotal_cents == 300
    assert result.cart.expires_at == later + CART_IDLE_TTL


def test_add_item_currency_mismatch_refused(state_path, decisions_log_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    commerce_cart.add_item(
        state_path=state_path,
        decisions_log_path=decisions_log_path,
        sender_phone="+15551234567",
        sender_lid=None,
        chat_id="chat",
        sku="A", display_name="A", quantity=1, unit="each",
        unit_price_cents=100, currency="USD", now=now,
    )
    bad = commerce_cart.add_item(
        state_path=state_path,
        decisions_log_path=decisions_log_path,
        sender_phone="+15551234567",
        sender_lid=None,
        chat_id="chat",
        sku="B", display_name="B", quantity=1, unit="each",
        unit_price_cents=100, currency="INR", now=now,
    )
    assert not bad.ok
    assert bad.detail == "currency_mismatch"


def test_remove_item(state_path, decisions_log_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    r = commerce_cart.add_item(
        state_path=state_path, decisions_log_path=decisions_log_path,
        sender_phone="+15551234567", sender_lid=None, chat_id="chat",
        sku="A", display_name="A", quantity=2, unit="each", unit_price_cents=100, now=now,
    )
    rm = commerce_cart.remove_item(
        state_path=state_path, decisions_log_path=decisions_log_path,
        cart_id=r.cart.cart_id, sku="A", now=now,
    )
    assert rm.ok
    assert rm.cart.items == []
    assert rm.cart.subtotal_cents == 0


def test_update_qty_zero_triggers_remove(state_path, decisions_log_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    r = commerce_cart.add_item(
        state_path=state_path, decisions_log_path=decisions_log_path,
        sender_phone="+15551234567", sender_lid=None, chat_id="chat",
        sku="A", display_name="A", quantity=3, unit="each", unit_price_cents=100, now=now,
    )
    upd = commerce_cart.update_qty(
        state_path=state_path, decisions_log_path=decisions_log_path,
        cart_id=r.cart.cart_id, sku="A", quantity=0, now=now,
    )
    assert upd.ok
    assert upd.cart.items == []


def test_update_qty_negative_refused(state_path, decisions_log_path):
    upd = commerce_cart.update_qty(
        state_path=state_path, decisions_log_path=decisions_log_path,
        cart_id="CC00001", sku="A", quantity=-1,
    )
    assert not upd.ok
    assert upd.detail == "quantity_must_be_non_negative"


def test_clear(state_path, decisions_log_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    r = commerce_cart.add_item(
        state_path=state_path, decisions_log_path=decisions_log_path,
        sender_phone="+15551234567", sender_lid=None, chat_id="chat",
        sku="A", display_name="A", quantity=1, unit="each", unit_price_cents=100, now=now,
    )
    c = commerce_cart.clear(
        state_path=state_path, decisions_log_path=decisions_log_path,
        cart_id=r.cart.cart_id, now=now,
    )
    assert c.ok
    assert c.cart.status == "cleared"
    second = commerce_cart.clear(
        state_path=state_path, decisions_log_path=decisions_log_path,
        cart_id=r.cart.cart_id, now=now,
    )
    assert not second.ok
    assert second.detail == "cart_not_open"


def test_expire_idle_carts(state_path, decisions_log_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    commerce_cart.add_item(
        state_path=state_path, decisions_log_path=decisions_log_path,
        sender_phone="+15551234567", sender_lid=None, chat_id="chat",
        sku="A", display_name="A", quantity=1, unit="each", unit_price_cents=100, now=now,
    )
    past_ttl = now + CART_IDLE_TTL + timedelta(seconds=1)
    n = commerce_cart.expire_idle(
        state_path=state_path,
        decisions_log_path=decisions_log_path,
        now=past_ttl,
    )
    assert n == 1
    store = commerce_cart.load_cart_store(state_path)
    assert store.carts[0].status == "expired"


def test_checkout_marks_cart(state_path, decisions_log_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    r = commerce_cart.add_item(
        state_path=state_path, decisions_log_path=decisions_log_path,
        sender_phone="+15551234567", sender_lid=None, chat_id="chat",
        sku="A", display_name="A", quantity=1, unit="each", unit_price_cents=100, now=now,
    )
    co = commerce_cart.checkout(
        state_path=state_path, decisions_log_path=decisions_log_path,
        cart_id=r.cart.cart_id, order_id="CO00001", now=now,
    )
    assert co.ok
    assert co.cart.status == "checked_out"


def test_checkout_empty_cart_refused(state_path, decisions_log_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    r = commerce_cart.add_item(
        state_path=state_path, decisions_log_path=decisions_log_path,
        sender_phone="+15551234567", sender_lid=None, chat_id="chat",
        sku="A", display_name="A", quantity=1, unit="each", unit_price_cents=100, now=now,
    )
    commerce_cart.remove_item(
        state_path=state_path, decisions_log_path=decisions_log_path,
        cart_id=r.cart.cart_id, sku="A", now=now,
    )
    bad = commerce_cart.checkout(
        state_path=state_path, decisions_log_path=decisions_log_path,
        cart_id=r.cart.cart_id, order_id="CO00001", now=now,
    )
    assert not bad.ok
    assert bad.detail == "cart_empty"
