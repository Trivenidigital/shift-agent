"""Commerce confirm audit ownership: the intent->confirmed flip's audit row is
emitted by the CALLER (commerce-payment-confirm), not by the mark_confirmed
primitive.

Two pinned facts:
  1. payment_link.mark_confirmed flips status sent/minted -> confirmed but emits
     NO commerce_payment_confirmed row (documented: "emitted by the caller ...").
  2. The caller's _emit_payment_confirmed writes the commerce_payment_confirmed
     row. (commerce-payment-confirm line ~496, right after mark_confirmed.)

So the caller path DOES emit a commerce audit row on confirm — this pins that
contract. No product code is added in this batch.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fixtures_fleet import ensure_fcntl_stub, load_script, read_log_rows

ensure_fcntl_stub()

from commerce import payment_link  # noqa: E402  (Windows-importable; no fcntl)

REPO = Path(__file__).resolve().parent.parent
CONFIRM_SCRIPT = REPO / "src" / "platform" / "scripts" / "commerce-payment-confirm"
NOW = datetime(2026, 5, 29, 19, 0, tzinfo=timezone.utc)


def _mint(intent_path, log_path):
    return payment_link.mint(
        intent_state_path=intent_path,
        decisions_log_path=log_path,
        order_id="CO00001",
        originating_message_id="msg_abc",
        amount_cents=2599,
        currency="USD",
        chat_id="chat",
        checkout_url_template="https://pay.example.com/?intent={intent_id}",
        now=NOW,
    )


def test_mark_confirmed_flips_but_primitive_emits_no_audit(tmp_path):
    intent_path = tmp_path / "payment_intents.json"
    log_path = tmp_path / "decisions.log"
    minted = _mint(intent_path, log_path)
    assert minted.ok

    result = payment_link.mark_confirmed(
        intent_state_path=intent_path,
        decisions_log_path=log_path,
        intent_id=minted.intent.intent_id,
        payment_reference="pi_test_123",
        now=NOW,
    )
    assert result.ok
    assert result.intent.status == "confirmed"

    types = [r["type"] for r in read_log_rows(log_path)]
    assert "commerce_payment_intent_minted" in types
    # The primitive does NOT emit the confirmation row — that is the caller's job.
    assert "commerce_payment_confirmed" not in types


def test_caller_emit_writes_commerce_payment_confirmed_row(tmp_path):
    log_path = tmp_path / "decisions.log"
    mod = load_script("commerce_payment_confirm_under_test", CONFIRM_SCRIPT)
    mod.LOG_PATH = log_path
    mod._emit_payment_confirmed(
        intent_id="CPI00001", order_id="CO00001", payment_reference="pi_test_123",
    )
    rows = [r for r in read_log_rows(log_path) if r["type"] == "commerce_payment_confirmed"]
    assert len(rows) == 1
    assert rows[0]["intent_id"] == "CPI00001"
    assert rows[0]["order_id"] == "CO00001"
    assert rows[0]["payment_reference"] == "pi_test_123"
