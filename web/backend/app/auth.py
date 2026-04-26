"""OTP + JWT auth.

Single-user model. The owner.phone in /opt/shift-agent/config.yaml is the
only valid OTP recipient. There is no user registration.

Security design notes (from review v1.1):
- 5-attempt lockout per OTP token; on overflow, token is invalidated.
- hmac.compare_digest for code comparison.
- Verify handler enforces a minimum wall-clock floor (settings.otp_verify_min_wall_seconds)
  to equalize timing whether code is right, wrong, or token unknown.
- JWT secret minimum 256-bit, persisted at /opt/shift-agent/state/.cockpit-jwt-secret.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import string
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt
from pydantic import BaseModel

from .audit import log as audit_log
from .config import get_settings
from .state import load_config

settings = get_settings()


class OtpRecord(BaseModel):
    token: str
    code: str
    issued_to: str  # canonical owner phone
    issued_at: float
    expires_at: float
    verify_attempts: int = 0
    ip_issued: str = ""
    ua_issued: str = ""
    invalidated: bool = False


class OtpStore:
    """Single-record OTP store (one active OTP at a time)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> OtpRecord | None:
        if not self.path.exists():
            return None
        try:
            return OtpRecord.model_validate_json(self.path.read_text())
        except Exception:
            return None

    def write(self, rec: OtpRecord) -> None:
        self.path.write_text(rec.model_dump_json())
        self.path.chmod(0o600)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


otp_store = OtpStore(settings.otp_state_path)


def _gen_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


def _gen_token() -> str:
    return secrets.token_hex(16)  # 128-bit URL-safe-ish identifier


def _now() -> float:
    return time.time()


# ─── Rate limiting (per IP and per owner) ───────────────────────────────

_request_log: dict[str, list[float]] = {}


def _rl_check(key: str, max_count: int, window_seconds: int) -> bool:
    now = _now()
    cutoff = now - window_seconds
    times = _request_log.setdefault(key, [])
    # Drop expired
    while times and times[0] < cutoff:
        times.pop(0)
    if len(times) >= max_count:
        return False
    times.append(now)
    return True


# ─── OTP request flow ───────────────────────────────────────────────────


async def issue_otp(ip: str, ua: str) -> str:
    """Generate + persist + Pushover-deliver an OTP. Returns the opaque token."""
    cfg = load_config()
    owner_phone = cfg.owner.phone

    # Rate limit per IP
    per_ip_max, per_ip_window = settings.otp_request_per_ip
    if not _rl_check(f"ip:{ip}", per_ip_max, per_ip_window):
        raise HTTPException(429, "Too many OTP requests from this IP — wait 15 min")
    # Per owner
    per_o_max, per_o_window = settings.otp_request_per_owner
    if not _rl_check(f"owner:{owner_phone}", per_o_max, per_o_window):
        raise HTTPException(429, "Too many OTP requests for this account — wait 1 hour")

    code = _gen_code()
    token = _gen_token()
    rec = OtpRecord(
        token=token,
        code=code,
        issued_to=owner_phone,
        issued_at=_now(),
        expires_at=_now() + settings.otp_ttl_seconds,
        ip_issued=ip,
        ua_issued=ua[:200],
    )
    otp_store.write(rec)

    await _send_pushover_otp(cfg.alerting.pushover_user_key, cfg.alerting.pushover_app_token, code)
    audit_log("auth.otp.issued", ip=ip, ua=ua, details={"owner": owner_phone})
    return token


async def _send_pushover_otp(user_key: str, app_token: str, code: str) -> None:
    if not user_key or not app_token:
        # Dev mode: print code to stderr for local testing
        import sys

        print(f"[cockpit-otp DEV] code={code}", file=sys.stderr, flush=True)
        return
    payload = {
        "token": app_token,
        "user": user_key,
        "title": "Shift Agent Cockpit — login code",
        "message": f"Your code: {code}\nValid 5 minutes.",
        "priority": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post("https://api.pushover.net/1/messages.json", data=payload)
            if r.status_code != 200:
                # Don't leak Pushover details to user; just log
                audit_log("auth.otp.pushover_failed", details={"status": r.status_code})
    except Exception as e:
        audit_log("auth.otp.pushover_error", details={"error": str(e)[:200]})


# ─── OTP verify flow ────────────────────────────────────────────────────


async def verify_otp(token: str, code: str, ip: str, ua: str) -> str:
    """Verify OTP, return JWT on success.

    Equalizes wall-clock time to settings.otp_verify_min_wall_seconds floor
    regardless of which path (no record / wrong code / expired / locked / OK).
    """
    started = _now()
    jwt_token: str | None = None
    audit_event: str = "auth.otp.verify_failed"
    audit_details: dict[str, Any] = {}

    try:
        rec = otp_store.read()
        if rec is None:
            audit_details["reason"] = "no_active_otp"
        elif rec.invalidated:
            audit_details["reason"] = "invalidated"
        elif _now() > rec.expires_at:
            audit_details["reason"] = "expired"
            otp_store.clear()
        elif rec.verify_attempts >= settings.otp_max_verify_attempts:
            # Already locked out
            rec.invalidated = True
            otp_store.write(rec)
            audit_details["reason"] = "locked_out"
        else:
            # Token-string compare also constant-time
            if not hmac.compare_digest(rec.token, token):
                rec.verify_attempts += 1
                otp_store.write(rec)
                audit_details["reason"] = "wrong_token"
            elif not hmac.compare_digest(rec.code, code):
                rec.verify_attempts += 1
                if rec.verify_attempts >= settings.otp_max_verify_attempts:
                    rec.invalidated = True
                otp_store.write(rec)
                audit_details["reason"] = "wrong_code"
                audit_details["attempts"] = rec.verify_attempts
            else:
                # Success
                jwt_token = mint_jwt(rec.issued_to)
                otp_store.clear()
                audit_event = "auth.otp.verify_success"
                audit_details["owner"] = rec.issued_to
    finally:
        # Wall-time floor — sleep difference
        elapsed = _now() - started
        if elapsed < settings.otp_verify_min_wall_seconds:
            await asyncio.sleep(settings.otp_verify_min_wall_seconds - elapsed)
        audit_log(audit_event, ip=ip, ua=ua, details=audit_details)

    if jwt_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired code")
    return jwt_token


# ─── JWT ────────────────────────────────────────────────────────────────


def mint_jwt(owner_phone: str) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": owner_phone,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.jwt_ttl_hours)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algo)


def decode_jwt(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algo])


# ─── Dependency: require auth ───────────────────────────────────────────


async def require_auth(request: Request) -> dict[str, Any]:
    cookie = request.cookies.get(settings.cookie_name)
    if not cookie:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    try:
        return decode_jwt(cookie)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")


async def require_fresh_otp(request: Request) -> dict[str, Any]:
    """Require a JWT issued within the last 5 minutes (for sensitive actions).

    The owner must `POST /auth/refresh-otp` and `POST /auth/verify-otp`
    immediately before a sensitive PATCH.
    """
    claims = await require_auth(request)
    issued_at = claims.get("iat", 0)
    age = _now() - issued_at
    if age > 300:
        raise HTTPException(403, "Sensitive action requires fresh OTP — re-verify")
    return claims
