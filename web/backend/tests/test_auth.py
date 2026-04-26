"""OTP + JWT smoke tests.

Uses temp filesystem; doesn't actually call Pushover.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

# Patch settings BEFORE importing auth
def test_jwt_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("COCKPIT_JWT_SECRET", "0" * 64)
    from app import config as cfg_mod
    cfg_mod.get_settings.cache_clear()  # type: ignore[attr-defined]
    from app.auth import mint_jwt, decode_jwt

    token = mint_jwt("+19045550100")
    claims = decode_jwt(token)
    assert claims["sub"] == "+19045550100"
    assert "iat" in claims and "exp" in claims and "jti" in claims


def test_constant_time_eq_known_pairs():
    import hmac

    assert hmac.compare_digest("123456", "123456")
    assert not hmac.compare_digest("123456", "654321")
    # Different lengths still safe
    assert not hmac.compare_digest("123456", "1234567")


def test_otp_record_serialization(tmp_path):
    from app.auth import OtpRecord, OtpStore

    store = OtpStore(tmp_path / "otp.json")
    rec = OtpRecord(
        token="abc",
        code="123456",
        issued_to="+19045550100",
        issued_at=time.time(),
        expires_at=time.time() + 300,
    )
    store.write(rec)
    got = store.read()
    assert got is not None
    assert got.token == "abc"
    assert got.verify_attempts == 0
    store.clear()
    assert store.read() is None
