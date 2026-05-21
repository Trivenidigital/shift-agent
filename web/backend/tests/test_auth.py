"""OTP + JWT smoke tests.

Uses temp filesystem; doesn't actually call Pushover.
"""
from __future__ import annotations

import time
from pathlib import Path


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


def test_audit_log_writes_json_string(monkeypatch, tmp_path):
    from app import audit

    captured: list[str] = []
    monkeypatch.setattr(audit.settings, "cockpit_audit_log", tmp_path / "audit.log")
    monkeypatch.setattr(audit.safe_io, "ndjson_append", lambda _path, line: captured.append(line))

    audit.log("auth.test", details={"ok": True})

    assert captured
    assert isinstance(captured[0], str)
    assert '"event":"auth.test"' in captured[0]


def test_auth_bypass_request_sets_cookie(monkeypatch):
    from fastapi import Response
    from app.routers import auth as auth_router

    class Owner:
        phone = "+17329837841"

    class Config:
        owner = Owner()

    monkeypatch.setattr(auth_router.settings, "auth_bypass_enabled", True)
    monkeypatch.setattr(auth_router, "load_config", lambda: Config())
    monkeypatch.setattr(auth_router, "audit_log", lambda *args, **kwargs: None)

    class Client:
        host = "127.0.0.1"

    class Url:
        scheme = "http"

    class Request:
        client = Client()
        headers = {}
        url = Url()

    response = Response()
    import anyio

    body = anyio.run(auth_router.request_otp, Request(), response)

    assert body.token == "__bypass__"
    assert "hjwt=" in response.headers["set-cookie"]
    assert "Secure" not in response.headers["set-cookie"]


def test_cookie_secure_only_when_https(monkeypatch):
    from fastapi import Response
    from app.routers import auth as auth_router

    class Owner:
        phone = "+17329837841"

    class Config:
        owner = Owner()

    monkeypatch.setattr(auth_router.settings, "auth_bypass_enabled", True)
    monkeypatch.setattr(auth_router.settings, "cookie_secure", True)
    monkeypatch.setattr(auth_router, "load_config", lambda: Config())
    monkeypatch.setattr(auth_router, "audit_log", lambda *args, **kwargs: None)

    class Client:
        host = "127.0.0.1"

    class UrlHttps:
        scheme = "https"

    class UrlHttp:
        scheme = "http"

    class RequestHttps:
        client = Client()
        headers = {}
        url = UrlHttps()

    class RequestHttp:
        client = Client()
        headers = {}
        url = UrlHttp()

    import anyio

    response = Response()
    anyio.run(auth_router.request_otp, RequestHttps(), response)
    assert "Secure" in response.headers["set-cookie"]

    response2 = Response()
    anyio.run(auth_router.request_otp, RequestHttp(), response2)
    assert "Secure" not in response2.headers["set-cookie"]


def test_cockpit_service_does_not_ship_auth_bypass_enabled():
    service = (Path(__file__).resolve().parents[2] / "deploy" / "shift-agent-cockpit.service").read_text(encoding="utf-8")

    assert "COCKPIT_AUTH_BYPASS=true" not in service
    assert "COCKPIT_COOKIE_SECURE=false" not in service


def test_auth_bypass_counts_as_fresh_for_temporary_sensitive_actions(monkeypatch):
    import anyio
    from app import auth

    async def fake_require_auth(_request):
        return {"iat": 0, "sub": "+17329837841"}

    monkeypatch.setattr(auth.settings, "auth_bypass_enabled", True)
    monkeypatch.setattr(auth, "require_auth", fake_require_auth)

    claims = anyio.run(auth.require_fresh_otp, object())

    assert claims["sub"] == "+17329837841"
