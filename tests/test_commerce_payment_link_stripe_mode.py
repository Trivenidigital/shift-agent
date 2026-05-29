"""Slice-3 PR-1: provider abstraction + Stripe mode + mark_confirmed helper.

Tests cover:
- CommerceConfig new fields (provider, provider_mode, webhook_subscription_name,
  send_payment_confirmation_reply, stripe_livemode_expected)
- commerce_payment_link.mint() with provider="placeholder" (backward-compat)
- commerce_payment_link.mint() with provider="stripe" (mocked SDK)
- _mint_via_stripe() idempotency_key + metadata cross-reference
- mark_confirmed() state transition + idempotency
- Failure modes: missing api_key, stripe-python not installed, Stripe API error,
  unsupported provider

The actual stripe-python SDK is NOT a hard dependency — it's lazy-imported
inside _mint_via_stripe(). Tests monkeypatch the import so they run without
the SDK installed (matches the slice-2 catering script test pattern).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from schemas import CommerceConfig
from commerce import payment_link


TS = datetime(2026, 5, 29, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def intent_state(tmp_state_dir: Path) -> Path:
    return tmp_state_dir / "commerce" / "payment_intents.json"


@pytest.fixture
def decisions_log_path(tmp_state_dir: Path) -> Path:
    p = tmp_state_dir / "logs" / "decisions.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# ─────────────────────────────────────────────────────────────────
# CommerceConfig new-field defaults
# ─────────────────────────────────────────────────────────────────

def test_commerce_config_provider_default_is_placeholder():
    cfg = CommerceConfig()
    assert cfg.provider == "placeholder"


def test_commerce_config_provider_mode_default_is_sdk():
    cfg = CommerceConfig()
    assert cfg.provider_mode == "sdk"


def test_commerce_config_webhook_subscription_name_default():
    cfg = CommerceConfig()
    assert cfg.webhook_subscription_name == "stripe-commerce-payments"


def test_commerce_config_send_confirmation_reply_default_true():
    cfg = CommerceConfig()
    assert cfg.send_payment_confirmation_reply is True


def test_commerce_config_stripe_livemode_expected_default_false():
    """Reviewer B-MEDIUM-1 fail-safe: default to test mode."""
    cfg = CommerceConfig()
    assert cfg.stripe_livemode_expected is False


def test_commerce_config_accepts_stripe_provider():
    cfg = CommerceConfig(provider="stripe", provider_mode="sdk")
    assert cfg.provider == "stripe"


def test_commerce_config_rejects_invalid_provider():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CommerceConfig(provider="not_a_provider")


def test_commerce_config_rejects_invalid_provider_mode():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CommerceConfig(provider_mode="not_a_mode")


# ─────────────────────────────────────────────────────────────────
# mint() — placeholder mode (backward-compat: slice-2 behavior unchanged)
# ─────────────────────────────────────────────────────────────────

def test_mint_placeholder_mode_unchanged(intent_state, decisions_log_path):
    """Calling mint() without provider= keeps slice-2 placeholder behavior."""
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=1500,
        currency="USD",
        chat_id="c",
        checkout_url_template="https://pay/?o={order_id}",
        now=TS,
    )
    assert r.ok
    assert r.intent.provider == "placeholder"
    assert r.intent.checkout_url == "https://pay/?o=CO00001"
    rows = _read_rows(decisions_log_path)
    minted = next(r for r in rows if r["type"] == "commerce_payment_intent_minted")
    assert minted["provider"] == "placeholder"


def test_mint_explicit_placeholder_provider(intent_state, decisions_log_path):
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=1500,
        currency="USD",
        chat_id="c",
        checkout_url_template="https://pay/?o={order_id}",
        provider="placeholder",
        now=TS,
    )
    assert r.ok
    assert r.intent.provider == "placeholder"


# ─────────────────────────────────────────────────────────────────
# mint() — stripe mode
# ─────────────────────────────────────────────────────────────────

class _MockStripeModule:
    """Minimal mock of the stripe-python module surface used by _mint_via_stripe."""
    api_key = None

    class PaymentLink:
        last_kwargs = None

        @classmethod
        def create(cls, **kwargs):
            cls.last_kwargs = kwargs
            return SimpleNamespace(url="https://buy.stripe.com/test_xxx")


def _install_mock_stripe(monkeypatch):
    """Inject a fake stripe module under sys.modules so the lazy import inside
    _mint_via_stripe picks it up."""
    mock = _MockStripeModule
    mock.api_key = None
    mock.PaymentLink.last_kwargs = None
    monkeypatch.setitem(sys.modules, "stripe", mock)
    return mock


def test_mint_stripe_mode_returns_stripe_url(intent_state, decisions_log_path, monkeypatch):
    mock_stripe = _install_mock_stripe(monkeypatch)
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=15000,
        currency="USD",
        chat_id="c",
        provider="stripe",
        stripe_api_key="sk_test_xxx",
        now=TS,
    )
    assert r.ok
    assert r.intent.provider == "stripe"
    assert r.intent.checkout_url == "https://buy.stripe.com/test_xxx"
    assert mock_stripe.api_key == "sk_test_xxx"


def test_mint_stripe_passes_idempotency_key(intent_state, decisions_log_path, monkeypatch):
    """Reviewer B-LOW-3: defense-in-depth via Stripe's own idempotency layer."""
    mock_stripe = _install_mock_stripe(monkeypatch)
    payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00042",
        originating_message_id="msg_x",
        amount_cents=15000,
        currency="USD",
        chat_id="c",
        provider="stripe",
        stripe_api_key="sk_test_xxx",
        now=TS,
    )
    assert mock_stripe.PaymentLink.last_kwargs["idempotency_key"] == "CO00042"


def test_mint_stripe_metadata_carries_cross_refs(intent_state, decisions_log_path, monkeypatch):
    """Reconciliation invariant #4: webhook reconciler needs commerce_order_id +
    commerce_intent_id in metadata to look up the lead."""
    mock_stripe = _install_mock_stripe(monkeypatch)
    payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00042",
        originating_message_id="msg_x",
        amount_cents=15000,
        currency="USD",
        chat_id="c",
        provider="stripe",
        stripe_api_key="sk_test_xxx",
        now=TS,
    )
    metadata = mock_stripe.PaymentLink.last_kwargs["metadata"]
    assert metadata["commerce_order_id"] == "CO00042"
    assert metadata["commerce_intent_id"].startswith("CPI")


def test_mint_stripe_currency_lowercased_for_stripe(intent_state, decisions_log_path, monkeypatch):
    """Stripe API requires lowercase ISO 4217 codes."""
    mock_stripe = _install_mock_stripe(monkeypatch)
    payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=15000,
        currency="USD",
        chat_id="c",
        provider="stripe",
        stripe_api_key="sk_test_xxx",
        now=TS,
    )
    line_item = mock_stripe.PaymentLink.last_kwargs["line_items"][0]
    assert line_item["price_data"]["currency"] == "usd"


def test_mint_stripe_persists_intent_with_provider_stripe(intent_state, decisions_log_path, monkeypatch):
    _install_mock_stripe(monkeypatch)
    payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=15000,
        currency="USD",
        chat_id="c",
        provider="stripe",
        stripe_api_key="sk_test_xxx",
        now=TS,
    )
    store = payment_link.load_intent_store(intent_state)
    assert store.intents[0].provider == "stripe"
    assert store.intents[0].checkout_url.startswith("https://buy.stripe.com/")
    rows = _read_rows(decisions_log_path)
    minted = next(r for r in rows if r["type"] == "commerce_payment_intent_minted")
    assert minted["provider"] == "stripe"


# ─────────────────────────────────────────────────────────────────
# mint() — failure modes
# ─────────────────────────────────────────────────────────────────

def test_mint_stripe_without_api_key_refuses(intent_state, decisions_log_path):
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=1500,
        currency="USD",
        chat_id="c",
        provider="stripe",
        stripe_api_key=None,
    )
    assert not r.ok
    assert r.detail == "stripe_api_key_required"


def test_mint_stripe_without_sdk_installed_fails_closed(intent_state, decisions_log_path, monkeypatch):
    """If stripe-python isn't installed, fail cleanly with documented detail."""
    # Sabotage the import so the lazy-import inside _mint_via_stripe raises
    monkeypatch.setitem(sys.modules, "stripe", None)
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=1500,
        currency="USD",
        chat_id="c",
        provider="stripe",
        stripe_api_key="sk_test_xxx",
    )
    assert not r.ok
    assert r.detail.startswith("stripe_sdk_not_installed")


def test_mint_stripe_api_error_caught(intent_state, decisions_log_path, monkeypatch):
    """Any Stripe SDK exception is wrapped as stripe_api_error."""
    class _ExplodingStripe:
        api_key = None
        class PaymentLink:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("simulated_stripe_api_outage")
    monkeypatch.setitem(sys.modules, "stripe", _ExplodingStripe)
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=1500,
        currency="USD",
        chat_id="c",
        provider="stripe",
        stripe_api_key="sk_test_xxx",
    )
    assert not r.ok
    assert r.detail.startswith("stripe_api_error")


def test_mint_unsupported_provider_rejected(intent_state, decisions_log_path):
    """Reserved-but-not-wired providers fail fast (operator typo defense)."""
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=1500,
        currency="USD",
        chat_id="c",
        provider="razorpay",  # reserved but not wired in PR-1
    )
    assert not r.ok
    assert r.detail == "unsupported_provider:razorpay"


def test_mint_idempotency_preserved_across_provider_modes(intent_state, decisions_log_path, monkeypatch):
    """Re-minting the same order_id returns the existing intent regardless of provider."""
    _install_mock_stripe(monkeypatch)
    r1 = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=15000,
        currency="USD",
        chat_id="c",
        provider="stripe",
        stripe_api_key="sk_test_xxx",
    )
    r2 = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x_again",
        amount_cents=15000,
        currency="USD",
        chat_id="c",
        provider="stripe",
        stripe_api_key="sk_test_xxx",
    )
    assert r1.intent.intent_id == r2.intent.intent_id
    assert r2.detail == "already_minted_idempotent"


# ─────────────────────────────────────────────────────────────────
# mark_confirmed() — slice-3 PR-2 reconciler's primary state mutator
# ─────────────────────────────────────────────────────────────────

def _mint_placeholder_intent(intent_state, decisions_log_path):
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=15000,
        currency="USD",
        chat_id="c",
        checkout_url_template="https://pay/?o={order_id}",
        now=TS,
    )
    return r.intent


def test_mark_confirmed_flips_status_and_records_reference(intent_state, decisions_log_path):
    intent = _mint_placeholder_intent(intent_state, decisions_log_path)
    r = payment_link.mark_confirmed(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        intent_id=intent.intent_id,
        payment_reference="pi_test_abc123",
    )
    assert r.ok
    assert r.intent.status == "confirmed"
    assert r.intent.payment_reference == "pi_test_abc123"


def test_mark_confirmed_idempotent_same_reference(intent_state, decisions_log_path):
    intent = _mint_placeholder_intent(intent_state, decisions_log_path)
    payment_link.mark_confirmed(
        intent_state_path=intent_state, decisions_log_path=decisions_log_path,
        intent_id=intent.intent_id, payment_reference="pi_test_abc123",
    )
    r2 = payment_link.mark_confirmed(
        intent_state_path=intent_state, decisions_log_path=decisions_log_path,
        intent_id=intent.intent_id, payment_reference="pi_test_abc123",
    )
    assert r2.ok
    assert r2.detail == "noop_already_confirmed"


def test_mark_confirmed_rejects_different_reference_on_confirmed(intent_state, decisions_log_path):
    intent = _mint_placeholder_intent(intent_state, decisions_log_path)
    payment_link.mark_confirmed(
        intent_state_path=intent_state, decisions_log_path=decisions_log_path,
        intent_id=intent.intent_id, payment_reference="pi_test_abc123",
    )
    r2 = payment_link.mark_confirmed(
        intent_state_path=intent_state, decisions_log_path=decisions_log_path,
        intent_id=intent.intent_id, payment_reference="pi_test_DIFFERENT",
    )
    assert not r2.ok
    assert "confirmed_with_different_reference" in r2.detail


def test_mark_confirmed_rejects_on_voided_intent(intent_state, decisions_log_path):
    intent = _mint_placeholder_intent(intent_state, decisions_log_path)
    payment_link.void(
        intent_state_path=intent_state, decisions_log_path=decisions_log_path,
        intent_id=intent.intent_id, reason="test_void",
    )
    r = payment_link.mark_confirmed(
        intent_state_path=intent_state, decisions_log_path=decisions_log_path,
        intent_id=intent.intent_id, payment_reference="pi_test_abc",
    )
    assert not r.ok
    assert r.detail == "cannot_confirm_voided"


def test_mark_confirmed_intent_not_found(intent_state, decisions_log_path):
    r = payment_link.mark_confirmed(
        intent_state_path=intent_state, decisions_log_path=decisions_log_path,
        intent_id="CPI99999", payment_reference="pi_x",
    )
    assert not r.ok
    assert r.detail == "intent_not_found"


def test_void_after_mark_confirmed_refused(intent_state, decisions_log_path):
    """PR-1 reviewer M2: confirmed intent cannot be voided (slice-1 invariant).
    Pin the composition: mark_confirmed → void must reject with
    cannot_void_confirmed."""
    intent = _mint_placeholder_intent(intent_state, decisions_log_path)
    payment_link.mark_confirmed(
        intent_state_path=intent_state, decisions_log_path=decisions_log_path,
        intent_id=intent.intent_id, payment_reference="pi_test_abc",
    )
    r = payment_link.void(
        intent_state_path=intent_state, decisions_log_path=decisions_log_path,
        intent_id=intent.intent_id, reason="too_late",
    )
    assert not r.ok
    assert r.detail == "cannot_void_confirmed"


def test_mint_currency_normalized_to_uppercase(intent_state, decisions_log_path):
    """PR-1 reviewer L1: lowercase caller currency must be normalized before
    persisting so CommercePaymentIntent.currency Literal validation passes."""
    r = payment_link.mint(
        intent_state_path=intent_state,
        decisions_log_path=decisions_log_path,
        order_id="CO00001",
        originating_message_id="msg_x",
        amount_cents=1500,
        currency="usd",  # lowercase caller input
        chat_id="c",
        checkout_url_template="https://pay/?o={order_id}",
        now=TS,
    )
    assert r.ok
    assert r.intent.currency == "USD"
