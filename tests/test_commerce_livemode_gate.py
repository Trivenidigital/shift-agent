"""Tests for the commerce Stripe livemode-match deploy gate.

In-process / cross-platform: the logic module imports only stdlib + PyYAML +
Pydantic `CommerceConfig`. The Stripe API key read and the `GET /v1/account`
call are injected (`key_reader`, `account_fetcher`) so tests need no real key,
no network, and no `stripe` SDK.

Mirrors tasks/commerce-slice3.1-livemode-gate-design.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

SRC_PLATFORM = Path(__file__).resolve().parent.parent / "src" / "platform"
if str(SRC_PLATFORM) not in sys.path:
    sys.path.insert(0, str(SRC_PLATFORM))

import commerce_livemode_gate as gate  # noqa: E402


def _no_key():
    raise AssertionError("key_reader must NOT be called on the skip path")


def _no_fetch(_key):
    raise AssertionError("account_fetcher must NOT be called on the skip path")


def _key(value="sk_test_abc123"):
    return lambda: value


def _account(status, livemode, message="ok"):
    return lambda _key: (status, livemode, message)


def _write_config(tmp_path, commerce):
    doc = {"customer": {"timezone": "America/New_York"}}
    if commerce is not None:
        doc["commerce"] = commerce
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(p)


# ── dormant-safe skip ───────────────────────────────────────────────────────

def test_dormant_no_commerce_section_skips(tmp_path):
    cfg = _write_config(tmp_path, None)
    code, out, err = gate.run(cfg, key_reader=_no_key, account_fetcher=_no_fetch)
    assert code == 0
    assert "not applicable" in out


def test_enabled_but_placeholder_provider_skips(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "placeholder"})
    code, out, err = gate.run(cfg, key_reader=_no_key, account_fetcher=_no_fetch)
    assert code == 0
    assert "not applicable" in out


def test_stripe_but_disabled_skips(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": False, "provider": "stripe"})
    code, out, err = gate.run(cfg, key_reader=_no_key, account_fetcher=_no_fetch)
    assert code == 0


# ── active path: livemode match / mismatch ──────────────────────────────────

def test_active_test_mode_matches_passes(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe", "stripe_livemode_expected": False})
    code, out, err = gate.run(cfg, key_reader=_key(), account_fetcher=_account("ok", False))
    assert code == 0
    assert "livemode" in out.lower()


def test_active_live_mode_matches_passes(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe", "stripe_livemode_expected": True})
    code, out, err = gate.run(cfg, key_reader=_key("sk_live_xyz"), account_fetcher=_account("ok", True))
    assert code == 0


def test_live_key_in_test_config_fails_closed(tmp_path):
    # stripe_livemode_expected False but the account reports livemode True.
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe", "stripe_livemode_expected": False})
    code, out, err = gate.run(cfg, key_reader=_key("sk_live_xyz"), account_fetcher=_account("ok", True))
    assert code == 1
    assert out == ""
    assert "livemode" in err.lower()
    assert "expected" in err.lower()
    # never leak the key
    assert "sk_live_xyz" not in err


def test_test_key_in_live_config_fails_closed(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe", "stripe_livemode_expected": True})
    code, out, err = gate.run(cfg, key_reader=_key("sk_test_abc"), account_fetcher=_account("ok", False))
    assert code == 1
    assert "sk_test_abc" not in err


# ── active path: key / API error -> config error (exit 2) ───────────────────

def test_missing_key_is_config_error(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, key_reader=lambda: "", account_fetcher=_no_fetch)
    assert code == 2
    assert "STRIPE_API_KEY" in err


def test_placeholder_key_is_config_error(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, key_reader=lambda: "placeholder", account_fetcher=_no_fetch)
    assert code == 2


def test_auth_failure_is_config_error(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, key_reader=_key(), account_fetcher=_account("auth", None, "HTTP 401"))
    assert code == 2
    assert "ERROR" in err or "key" in err.lower()


def test_transient_failure_is_config_error(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "stripe"})
    code, out, err = gate.run(cfg, key_reader=_key(), account_fetcher=_account("transient", None, "timeout"))
    assert code == 2


# ── config error paths ──────────────────────────────────────────────────────

def test_missing_config_exit_2(tmp_path):
    code, out, err = gate.run(str(tmp_path / "nope.yaml"), key_reader=_key(), account_fetcher=_account("ok", False))
    assert code == 2


def test_invalid_provider_value_exit_2(tmp_path):
    cfg = _write_config(tmp_path, {"enabled": True, "provider": "bogus"})
    code, out, err = gate.run(cfg, key_reader=_key(), account_fetcher=_account("ok", False))
    assert code == 2
