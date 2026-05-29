"""Subprocess tests for commerce-list-active-stripe-links.

Skipped on Windows (depends on safe_io/fcntl).
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


pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="commerce-list-active-stripe-links depends on safe_io (fcntl — Linux only)",
)


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "src" / "platform" / "scripts" / "commerce-list-active-stripe-links"
TS = datetime(2026, 5, 29, 19, 0, tzinfo=timezone.utc)


def _write_intents(intents_path: Path, intents: list[dict]) -> None:
    intents_path.parent.mkdir(parents=True, exist_ok=True)
    intents_path.write_text(json.dumps({"intents": intents}, indent=2), encoding="utf-8")


def _intent(intent_id: str = "CPI00001", order_id: str = "CO00001",
            provider: str = "stripe", status: str = "minted",
            amount: int = 15000, currency: str = "USD") -> dict:
    return {
        "intent_id": intent_id,
        "order_id": order_id,
        "originating_message_id": "msg_x",
        "amount_cents": amount,
        "currency": currency,
        "provider": provider,
        "checkout_url": f"https://buy.stripe.com/test_{intent_id}",
        "status": status,
        "payment_reference": "",
        "created_at": TS.isoformat(),
        "updated_at": TS.isoformat(),
        "voided_at": None,
        "refunded_at": None,
        "refunded_amount_cents": 0,
        "chargeback_received_at": None,
    }


def _run(intents_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "COMMERCE_INTENTS_PATH": str(intents_path)}
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        env=env, capture_output=True, text=True, timeout=10,
    )


def test_empty_store(tmp_path):
    intents_path = tmp_path / "intents.json"
    _write_intents(intents_path, [])
    r = _run(intents_path)
    assert r.returncode == 0
    assert "(no active commerce payment intents)" in r.stdout


def test_lists_minted_and_sent(tmp_path):
    intents_path = tmp_path / "intents.json"
    _write_intents(intents_path, [
        _intent(intent_id="CPI00001", status="minted"),
        _intent(intent_id="CPI00002", status="sent"),
        _intent(intent_id="CPI00003", status="confirmed"),
        _intent(intent_id="CPI00004", status="voided"),
    ])
    r = _run(intents_path)
    assert r.returncode == 0
    assert "CPI00001" in r.stdout
    assert "CPI00002" in r.stdout
    assert "CPI00003" not in r.stdout
    assert "CPI00004" not in r.stdout
    assert "Total: 2" in r.stdout


def test_only_stripe_filter(tmp_path):
    intents_path = tmp_path / "intents.json"
    _write_intents(intents_path, [
        _intent(intent_id="CPI00001", provider="stripe", status="minted"),
        _intent(intent_id="CPI00002", provider="placeholder", status="minted"),
    ])
    r = _run(intents_path, "--only-stripe")
    assert r.returncode == 0
    assert "CPI00001" in r.stdout
    assert "CPI00002" not in r.stdout


def test_json_format(tmp_path):
    intents_path = tmp_path / "intents.json"
    _write_intents(intents_path, [_intent(intent_id="CPI00001", status="minted")])
    r = _run(intents_path, "--format", "json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert len(data) == 1
    assert data[0]["intent_id"] == "CPI00001"
    assert data[0]["provider"] == "stripe"
