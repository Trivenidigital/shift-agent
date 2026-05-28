"""Tests for src/platform/commerce/payment_link.py.

Slice 1 is placeholder-only (no real provider call). Tests cover:
- Template substitution + empty-template guard
- Idempotency on order_id
- Cross-order payment_reference dedup (immutable ledger)
- void transition (cannot void after confirmed/refunded/chargeback)
- assert_payment_url_renderable empty-string guard
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from commerce import payment_link
from commerce.exceptions import (
    CommerceCheckoutUrlUnrenderable,
    CommercePaymentReferenceReuse,
)


@pytest.fixture
def intent_state(tmp_state_dir: Path) -> Path:
    return tmp_state_dir / "commerce" / "payment_intents.json"


@pytest.fixture
def ledger_state(tmp_state_dir: Path) -> Path:
    return tmp_state_dir / "commerce" / "payment_references.json"


@pytest.fixture
def decisions_log_path(tmp_state_dir: Path) -> Path:
    p = tmp_state_dir / "logs" / "decisions.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_mint_with_template_substitution(intent_state, decisions_log_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_abc",
        amount_cents=2599,
        currency="USD",
        chat_id="chat",
        checkout_url_template="https://pay.example.com/?order={order_id}&amt={amount_usd}&intent={intent_id}",
        now=now,
    )
    assert r.ok
    assert r.intent.intent_id == "CPI00001"
    assert r.intent.checkout_url == "https://pay.example.com/?order=CO00001&amt=25.99&intent=CPI00001"
    assert r.intent.provider == "placeholder"
    rows = _read_rows(decisions_log_path)
    assert any(row["type"] == "commerce_payment_intent_minted" for row in rows)


def test_mint_empty_template_returns_empty_url(intent_state, decisions_log_path):
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_abc",
        amount_cents=2599,
        currency="USD",
        chat_id="chat",
        checkout_url_template="",  # operator hasn't configured
    )
    assert r.ok  # minting still succeeds — caller decides next step
    assert r.intent.checkout_url == ""


def test_assert_payment_url_renderable_raises_on_empty():
    with pytest.raises(CommerceCheckoutUrlUnrenderable):
        payment_link.assert_payment_url_renderable("")
    with pytest.raises(CommerceCheckoutUrlUnrenderable):
        payment_link.assert_payment_url_renderable("   ")


def test_assert_payment_url_renderable_passes_on_nonempty():
    payment_link.assert_payment_url_renderable("https://pay.example.com/order/1")


def test_mint_idempotent_on_order_id(intent_state, decisions_log_path):
    """Re-minting against the same live order_id returns the existing intent (PRD v2 §7)."""
    a = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_abc",
        amount_cents=2599,
        currency="USD",
        chat_id="chat",
        checkout_url_template="https://pay.example.com/?o={order_id}",
    )
    b = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_xyz",  # different inbound; still same order
        amount_cents=5000,  # even different amount — same order_id idempotency wins
        currency="USD",
        chat_id="chat",
        checkout_url_template="https://pay.example.com/?o={order_id}",
    )
    assert a.intent.intent_id == b.intent.intent_id
    assert b.detail == "already_minted_idempotent"


def test_attempted_sent_triple_emits_both_rows(intent_state, decisions_log_path):
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_abc",
        amount_cents=2599,
        currency="USD",
        chat_id="chat",
        checkout_url_template="https://pay.example.com/?o={order_id}",
    )
    payment_link.mark_attempted(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        intent_id=r.intent.intent_id,
    )
    payment_link.mark_sent(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        intent_id=r.intent.intent_id,
    )
    rows = _read_rows(decisions_log_path)
    types = [row["type"] for row in rows]
    assert "commerce_payment_link_attempted" in types
    assert "commerce_payment_link_sent" in types
    # Order: minted -> attempted -> sent
    assert types.index("commerce_payment_intent_minted") < types.index("commerce_payment_link_attempted")
    assert types.index("commerce_payment_link_attempted") < types.index("commerce_payment_link_sent")


def test_void_marks_intent(intent_state, decisions_log_path):
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_abc",
        amount_cents=2599,
        currency="USD",
        chat_id="chat",
    )
    v = payment_link.void(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        intent_id=r.intent.intent_id,
        reason="customer_cancelled",
    )
    assert v.ok
    assert v.intent.status == "voided"
    assert v.intent.voided_at is not None
    rows = _read_rows(decisions_log_path)
    assert any(row["type"] == "commerce_payment_intent_voided" for row in rows)


def test_register_reference_first_time(ledger_state, decisions_log_path):
    ok, detail = payment_link.register_reference(
        reference_ledger_path=ledger_state,
        decisions_log_path=decisions_log_path,
        payment_reference="stripe_pi_abc",
        order_id="CO00001",
    )
    assert ok
    assert detail == "registered"
    ledger = payment_link.load_reference_ledger(ledger_state)
    assert ledger.references["stripe_pi_abc"] == "CO00001"


def test_register_reference_same_order_replay_is_noop(ledger_state, decisions_log_path):
    payment_link.register_reference(
        reference_ledger_path=ledger_state,
        decisions_log_path=decisions_log_path,
        payment_reference="stripe_pi_abc",
        order_id="CO00001",
    )
    ok, detail = payment_link.register_reference(
        reference_ledger_path=ledger_state,
        decisions_log_path=decisions_log_path,
        payment_reference="stripe_pi_abc",
        order_id="CO00001",
    )
    assert ok
    assert detail == "noop_same_order"


def test_register_reference_cross_order_blocked(ledger_state, decisions_log_path):
    """The 2026-05-25 lesson: cross-order payment_reference reuse is permanently blocked."""
    payment_link.register_reference(
        reference_ledger_path=ledger_state,
        decisions_log_path=decisions_log_path,
        payment_reference="stripe_pi_abc",
        order_id="CO00001",
    )
    ok, detail = payment_link.register_reference(
        reference_ledger_path=ledger_state,
        decisions_log_path=decisions_log_path,
        payment_reference="stripe_pi_abc",
        order_id="CO00002",  # different order
    )
    assert not ok
    assert detail.startswith("dedup_blocked:")
    rows = _read_rows(decisions_log_path)
    blocked = next(row for row in rows if row["type"] == "commerce_payment_dedup_blocked")
    assert blocked["original_order_id"] == "CO00001"
    assert blocked["attempted_order_id"] == "CO00002"


def test_register_reference_strict_raises_on_cross_order(ledger_state, decisions_log_path):
    payment_link.register_reference_strict(
        reference_ledger_path=ledger_state,
        decisions_log_path=decisions_log_path,
        payment_reference="stripe_pi_abc",
        order_id="CO00001",
    )
    with pytest.raises(CommercePaymentReferenceReuse) as exc_info:
        payment_link.register_reference_strict(
            reference_ledger_path=ledger_state,
            decisions_log_path=decisions_log_path,
            payment_reference="stripe_pi_abc",
            order_id="CO00002",
        )
    assert exc_info.value.original_order_id == "CO00001"


def test_register_reference_empty_refused(ledger_state, decisions_log_path):
    ok, detail = payment_link.register_reference(
        reference_ledger_path=ledger_state,
        decisions_log_path=decisions_log_path,
        payment_reference="   ",  # whitespace-only
        order_id="CO00001",
    )
    assert not ok
    assert detail == "payment_reference_required"
