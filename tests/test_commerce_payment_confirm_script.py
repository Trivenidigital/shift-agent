"""Subprocess tests for commerce-payment-confirm reconciler.

Pattern mirrors tests/test_catering_v02_scripts.py + slice-2's
test_catering_mint_deposit_script.py: subprocess-invoke with prepared state,
assert on state mutations + audit rows + exit code.

Skipped on Windows (commerce-payment-confirm depends on safe_io/fcntl).
The stripe-python module is stubbed via a sitecustomize shim that injects
a mock `stripe.Webhook.construct_event()` returning a pre-canned event.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="commerce-payment-confirm depends on safe_io (fcntl — Linux only)",
)


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "src" / "platform" / "scripts" / "commerce-payment-confirm"
TS = datetime(2026, 5, 29, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
def isolated_state(tmp_path: Path) -> dict:
    state = tmp_path / "state"
    state.mkdir()
    commerce_state = state / "commerce"
    commerce_state.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()

    return {
        "tmp_path": tmp_path,
        "state": state,
        "commerce_state": commerce_state,
        "leads_path": state / "catering-leads.json",
        "leads_lock": state / "catering-leads.json.lock",
        "log_path": logs / "decisions.log",
        "config_path": tmp_path / "config.yaml",
        "intents_path": commerce_state / "payment_intents.json",
        "orders_path": commerce_state / "orders.json",
        "references_path": commerce_state / "payment_references.json",
    }


def _write_config(path: Path, send_reply: bool = True) -> None:
    cfg = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc", "timezone": "America/New_York", "languages": ["en"]},
        "owner": {"name": "O", "phone": "+15550000001", "self_chat_jid": ""},
        "limits": {"max_outbound_per_day": 2, "max_outbound_per_minute": 30,
                   "pending_proposal_ttl_hours": 4, "per_message_timeout_sec": 120,
                   "send_failure_retry_count": 1},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t",
                     "healthchecks_io_url": "", "email": ""},
        "backup": {"gpg_recipient_email": "x@y.z", "s3_bucket": "", "retention_days": 30},
        "operations": {"business_hours_local": "08:00-22:00"},
        "catering": {"enabled": True, "deposit_pct": 0.25, "deposit_threshold_guests": 50},
        "commerce": {
            "enabled": False, "provider": "stripe", "provider_mode": "sdk",
            "payment_checkout_url_template": "", "minimum_deposit_cents": 500,
            "send_payment_confirmation_reply": send_reply,
            "stripe_livemode_expected": False,
        },
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _write_intent(intents_path: Path, intent_id: str = "CPI00001",
                   order_id: str = "CO00001", amount_cents: int = 15000,
                   currency: str = "USD", status: str = "sent") -> None:
    intents_path.parent.mkdir(parents=True, exist_ok=True)
    store = {
        "intents": [{
            "intent_id": intent_id,
            "order_id": order_id,
            "originating_message_id": "msg_x",
            "amount_cents": amount_cents,
            "currency": currency,
            "provider": "stripe",
            "checkout_url": "https://buy.stripe.com/test_xxx",
            "status": status,
            "payment_reference": "",
            "created_at": TS.isoformat(),
            "updated_at": TS.isoformat(),
            "voided_at": None,
            "refunded_at": None,
            "refunded_amount_cents": 0,
            "chargeback_received_at": None,
        }]
    }
    intents_path.write_text(json.dumps(store, indent=2), encoding="utf-8")


def _write_order(orders_path: Path, order_id: str = "CO00001",
                  amount_cents: int = 15000, status: str = "pending_payment",
                  currency: str = "USD") -> None:
    orders_path.parent.mkdir(parents=True, exist_ok=True)
    store = {
        "orders": [{
            "order_id": order_id,
            "sender_phone": "+15551234567",
            "sender_lid": None,
            "chat_id": f"catering_deposit_L0007@s.whatsapp.net",
            "cart_id": "CC00001",
            "line_items": [{
                "sku": "CATERING-DEPOSIT-L0007",
                "display_name": "Catering deposit for SmokeCustomer",
                "quantity": 1,
                "unit": "each",
                "unit_price_cents": amount_cents,
                "line_total_cents": amount_cents,
                "added_at": TS.isoformat(),
            }],
            "subtotal_cents": amount_cents,
            "tax_cents": 0,
            "fee_cents": 0,
            "total_cents": amount_cents,
            "currency": currency,
            "status": status,
            "payment_intent_id": "CPI00001",
            "payment_reference": "",
            "status_history": [
                {"from_status": None, "to_status": "pending_payment", "ts": TS.isoformat(),
                 "cause": "customer_checkout", "actor": "caller", "event_ref": "CC00001"},
            ],
            "created_at": TS.isoformat(),
            "updated_at": TS.isoformat(),
        }]
    }
    orders_path.write_text(json.dumps(store, indent=2), encoding="utf-8")


def _write_lead(leads_path: Path, lead_id: str = "L0007",
                 deposit_payment_intent_id: str = "CPI00001",
                 deposit_status: str = "awaiting_payment") -> None:
    leads_path.parent.mkdir(parents=True, exist_ok=True)
    lead = {
        "lead_id": lead_id,
        "status": "SENT_TO_CUSTOMER",
        "customer_phone": "+15551234567",
        "customer_name": "Lakshmi",
        "raw_inquiry": "catering for 100 on 2026-06-15",
        "original_message_id": "msg_inquiry_001",
        "created_at": TS.isoformat(),
        "updated_at": TS.isoformat(),
        "quote_text": "Quote (100 guests on 2026-06-15) Total $600 (Ref: L0007)",
        "quote_version": 1,
        "quote_total_usd": 600,
        "extracted": {"headcount": 100, "event_date": "2026-06-15"},
        "deposit_required": True,
        "deposit_amount_cents": 15000,
        "deposit_commerce_order_id": "CO00001",
        "deposit_payment_intent_id": deposit_payment_intent_id,
        "deposit_payment_reference": "",
        "deposit_status": deposit_status,
        "deposit_minted_at": TS.isoformat(),
    }
    leads_path.write_text(json.dumps({"leads": [lead]}, indent=2), encoding="utf-8")


def _run_script(
    isolated: dict, raw_body: bytes,
    stripe_signature: str = "t=1234567890,v1=mock_sig_will_be_stubbed",
    stripe_construct_event_returns: dict = None,
    stripe_construct_event_raises: type = None,
) -> subprocess.CompletedProcess:
    """Run commerce-payment-confirm with a stubbed stripe module.

    The shim installs a sitecustomize.py that monkey-patches
    stripe.Webhook.construct_event() to return the pre-canned event
    (or raise the given exception class)."""
    shim_dir = isolated["tmp_path"] / "shim"
    shim_dir.mkdir(exist_ok=True)
    event_json = json.dumps(stripe_construct_event_returns or {})
    raises_cls = stripe_construct_event_raises.__name__ if stripe_construct_event_raises else ""
    sitecustomize = shim_dir / "sitecustomize.py"
    sitecustomize.write_text(
        f'''
import json, sys, types
def _install_stripe_stub():
    stripe = types.ModuleType("stripe")
    stripe.api_key = None
    class _Webhook:
        @staticmethod
        def construct_event(payload, sig, secret):
            if {raises_cls!r}:
                # The reconciler catches any Exception subclass; use a
                # generic Exception to simulate stripe.error.SignatureVerificationError
                raise Exception({raises_cls!r} + ": simulated_signature_verification_failure")
            return json.loads({event_json!r})
    stripe.Webhook = _Webhook
    sys.modules["stripe"] = stripe
_install_stripe_stub()
''',
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(isolated["config_path"]),
        "SHIFT_AGENT_LEADS_PATH": str(isolated["leads_path"]),
        "SHIFT_AGENT_LEADS_LOCK": str(isolated["leads_lock"]),
        "SHIFT_AGENT_LOG_PATH": str(isolated["log_path"]),
        "COMMERCE_INTENTS_PATH": str(isolated["intents_path"]),
        "COMMERCE_ORDERS_PATH": str(isolated["orders_path"]),
        "COMMERCE_REFERENCES_PATH": str(isolated["references_path"]),
        "STRIPE_WEBHOOK_SECRET": "whsec_test_xxx",
        "STRIPE_SIGNATURE": stripe_signature,
        "PYTEST_CURRENT_TEST": "smoke",
        "PYTHONPATH": f"{shim_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input=raw_body, env=env, capture_output=True, timeout=30,
    )


def _read_audit_rows(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _build_stripe_event(intent_id: str = "CPI00001", order_id: str = "CO00001",
                        amount: int = 15000, currency: str = "usd",
                        pi_id: str = "pi_test_abc123") -> dict:
    return {
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": pi_id,
                "amount": amount,
                "currency": currency,
                "metadata": {
                    "commerce_order_id": order_id,
                    "commerce_intent_id": intent_id,
                },
            },
        },
    }


# ─────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────

def test_happy_path_confirms_intent_order_and_lead(isolated_state):
    _write_config(isolated_state["config_path"])
    _write_intent(isolated_state["intents_path"])
    _write_order(isolated_state["orders_path"])
    _write_lead(isolated_state["leads_path"])

    event = _build_stripe_event()
    result = _run_script(
        isolated_state, raw_body=json.dumps(event).encode(),
        stripe_construct_event_returns=event,
    )
    assert result.returncode == 0, f"stderr={result.stderr.decode()!r}"

    # Intent confirmed
    intents = json.loads(isolated_state["intents_path"].read_text())
    assert intents["intents"][0]["status"] == "confirmed"
    assert intents["intents"][0]["payment_reference"] == "pi_test_abc123"

    # Order paid
    orders = json.loads(isolated_state["orders_path"].read_text())
    assert orders["orders"][0]["status"] == "paid"

    # Lead deposit_status=paid
    leads = json.loads(isolated_state["leads_path"].read_text())
    assert leads["leads"][0]["deposit_status"] == "paid"
    assert leads["leads"][0]["deposit_payment_reference"] == "pi_test_abc123"

    # Reference ledger populated
    refs = json.loads(isolated_state["references_path"].read_text())
    assert refs["references"]["pi_test_abc123"] == "CO00001"

    # Audit rows
    rows = _read_audit_rows(isolated_state["log_path"])
    types = [r["type"] for r in rows]
    assert "commerce_payment_confirmed" in types
    assert "catering_deposit_paid" in types


def test_idempotent_replay_same_payment_reference(isolated_state):
    """Stripe retries on 5xx; second delivery is a no-op (transition idempotent,
    register_reference noop on same order, mark_confirmed noop on same ref)."""
    _write_config(isolated_state["config_path"])
    _write_intent(isolated_state["intents_path"])
    _write_order(isolated_state["orders_path"])
    _write_lead(isolated_state["leads_path"])

    event = _build_stripe_event()
    r1 = _run_script(isolated_state, raw_body=json.dumps(event).encode(),
                      stripe_construct_event_returns=event)
    assert r1.returncode == 0
    r2 = _run_script(isolated_state, raw_body=json.dumps(event).encode(),
                      stripe_construct_event_returns=event)
    assert r2.returncode == 0

    # Intent status stays confirmed (not double-flipped)
    intents = json.loads(isolated_state["intents_path"].read_text())
    assert intents["intents"][0]["status"] == "confirmed"


# ─────────────────────────────────────────────────────────────────
# Signature validation
# ─────────────────────────────────────────────────────────────────

def test_signature_invalid_fails_closed(isolated_state):
    _write_config(isolated_state["config_path"])
    _write_intent(isolated_state["intents_path"])
    _write_order(isolated_state["orders_path"])
    _write_lead(isolated_state["leads_path"])

    event = _build_stripe_event()
    result = _run_script(
        isolated_state, raw_body=json.dumps(event).encode(),
        stripe_construct_event_raises=Exception,
    )
    assert result.returncode == 7  # EXIT_SIGNATURE_INVALID

    rows = _read_audit_rows(isolated_state["log_path"])
    failed = next(r for r in rows if r["type"] == "commerce_payment_confirmation_failed")
    assert failed["reason"] == "signature_invalid"

    # Intent NOT advanced
    intents = json.loads(isolated_state["intents_path"].read_text())
    assert intents["intents"][0]["status"] == "sent"  # unchanged


# ─────────────────────────────────────────────────────────────────
# Validation failures
# ─────────────────────────────────────────────────────────────────

def test_empty_payment_reference_fails_closed(isolated_state):
    """Reviewer B-HIGH-3 + 2026-05-25 lesson: Stripe payload with empty/null id refused."""
    _write_config(isolated_state["config_path"])
    _write_intent(isolated_state["intents_path"])
    _write_order(isolated_state["orders_path"])
    _write_lead(isolated_state["leads_path"])

    event = _build_stripe_event(pi_id="")  # empty payment_intent.id
    result = _run_script(isolated_state, raw_body=json.dumps(event).encode(),
                          stripe_construct_event_returns=event)
    assert result.returncode == 2  # EXIT_INVALID_INPUT

    rows = _read_audit_rows(isolated_state["log_path"])
    failed = next(r for r in rows if r["type"] == "commerce_payment_confirmation_failed")
    assert failed["reason"] == "empty_payment_reference"


def test_currency_mismatch_fails_closed(isolated_state):
    """Reviewer B-HIGH-2: USD intent + INR webhook payload = refuse."""
    _write_config(isolated_state["config_path"])
    _write_intent(isolated_state["intents_path"], currency="USD")
    _write_order(isolated_state["orders_path"], currency="USD")
    _write_lead(isolated_state["leads_path"])

    event = _build_stripe_event(currency="inr")
    result = _run_script(isolated_state, raw_body=json.dumps(event).encode(),
                          stripe_construct_event_returns=event)
    assert result.returncode == 5  # EXIT_SCHEMA_VIOLATION

    rows = _read_audit_rows(isolated_state["log_path"])
    failed = next(r for r in rows if r["type"] == "commerce_payment_confirmation_failed")
    assert failed["reason"] == "currency_mismatch"

    # Intent NOT confirmed
    intents = json.loads(isolated_state["intents_path"].read_text())
    assert intents["intents"][0]["status"] == "sent"


def test_amount_mismatch_fails_closed(isolated_state):
    _write_config(isolated_state["config_path"])
    _write_intent(isolated_state["intents_path"], amount_cents=15000)
    _write_order(isolated_state["orders_path"], amount_cents=15000)
    _write_lead(isolated_state["leads_path"])

    event = _build_stripe_event(amount=99999)  # wrong amount
    result = _run_script(isolated_state, raw_body=json.dumps(event).encode(),
                          stripe_construct_event_returns=event)
    assert result.returncode == 5

    rows = _read_audit_rows(isolated_state["log_path"])
    failed = next(r for r in rows if r["type"] == "commerce_payment_confirmation_failed")
    assert failed["reason"] == "amount_mismatch"


def test_intent_not_found(isolated_state):
    _write_config(isolated_state["config_path"])
    _write_intent(isolated_state["intents_path"], intent_id="CPI00001")
    _write_order(isolated_state["orders_path"])
    _write_lead(isolated_state["leads_path"])

    event = _build_stripe_event(intent_id="CPI99999")  # mismatched intent
    result = _run_script(isolated_state, raw_body=json.dumps(event).encode(),
                          stripe_construct_event_returns=event)
    assert result.returncode == 4  # EXIT_NOT_FOUND

    rows = _read_audit_rows(isolated_state["log_path"])
    failed = next(r for r in rows if r["type"] == "commerce_payment_confirmation_failed")
    assert failed["reason"] == "intent_not_found"


def test_missing_metadata_fails_closed(isolated_state):
    _write_config(isolated_state["config_path"])
    _write_intent(isolated_state["intents_path"])
    _write_order(isolated_state["orders_path"])
    _write_lead(isolated_state["leads_path"])

    event = {
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_test", "amount": 15000, "currency": "usd"}},  # no metadata
    }
    result = _run_script(isolated_state, raw_body=json.dumps(event).encode(),
                          stripe_construct_event_returns=event)
    assert result.returncode == 2

    rows = _read_audit_rows(isolated_state["log_path"])
    failed = next(r for r in rows if r["type"] == "commerce_payment_confirmation_failed")
    assert failed["reason"] == "missing_metadata"


def test_empty_webhook_body_refused(isolated_state):
    _write_config(isolated_state["config_path"])
    result = _run_script(isolated_state, raw_body=b"",
                          stripe_construct_event_returns={})
    assert result.returncode == 2


def test_missing_env_vars_refused(isolated_state):
    _write_config(isolated_state["config_path"])
    _write_intent(isolated_state["intents_path"])
    _write_order(isolated_state["orders_path"])
    _write_lead(isolated_state["leads_path"])

    # Run without the env-injection helper (no STRIPE_WEBHOOK_SECRET)
    env = {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(isolated_state["config_path"]),
        "SHIFT_AGENT_LEADS_PATH": str(isolated_state["leads_path"]),
        "SHIFT_AGENT_LOG_PATH": str(isolated_state["log_path"]),
        "COMMERCE_INTENTS_PATH": str(isolated_state["intents_path"]),
        "COMMERCE_ORDERS_PATH": str(isolated_state["orders_path"]),
        "COMMERCE_REFERENCES_PATH": str(isolated_state["references_path"]),
        # Note: STRIPE_WEBHOOK_SECRET + STRIPE_SIGNATURE intentionally absent
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input=b'{"type":"x"}', env=env, capture_output=True, timeout=30,
    )
    assert result.returncode == 2


# ─────────────────────────────────────────────────────────────────
# Cross-order reference reuse (slice-1 immutability invariant)
# ─────────────────────────────────────────────────────────────────

def test_reference_reuse_across_orders_blocked(isolated_state):
    """A payment_reference already bound to order A cannot confirm order B.
    Slice-1 register_reference enforces this; the reconciler emits the
    confirmation_failed audit row."""
    _write_config(isolated_state["config_path"])
    # Pre-populate the reference ledger with pi_test_abc123 bound to CO99999
    isolated_state["references_path"].parent.mkdir(parents=True, exist_ok=True)
    isolated_state["references_path"].write_text(
        json.dumps({"references": {"pi_test_abc123": "CO99999"}}), encoding="utf-8",
    )
    _write_intent(isolated_state["intents_path"], order_id="CO00001")
    _write_order(isolated_state["orders_path"], order_id="CO00001")
    _write_lead(isolated_state["leads_path"])

    event = _build_stripe_event(order_id="CO00001", pi_id="pi_test_abc123")
    result = _run_script(isolated_state, raw_body=json.dumps(event).encode(),
                          stripe_construct_event_returns=event)
    assert result.returncode == 5  # EXIT_SCHEMA_VIOLATION

    rows = _read_audit_rows(isolated_state["log_path"])
    failed = next(r for r in rows if r["type"] == "commerce_payment_confirmation_failed")
    assert failed["reason"] == "reference_reused"

    # Intent NOT confirmed (left in original status)
    intents = json.loads(isolated_state["intents_path"].read_text())
    assert intents["intents"][0]["status"] == "sent"
