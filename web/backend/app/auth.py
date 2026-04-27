"""OTP + JWT auth.

Single-user model. The owner.phone in /opt/shift-agent/config.yaml is the
only valid OTP recipient. There is no user registration.

Security design notes:
- 5-attempt lockout per OTP token; on overflow, token is invalidated.
- hmac.compare_digest for code comparison.
- Verify handler enforces a minimum wall-clock floor
  (settings.otp_verify_min_wall_seconds) to equalize timing whether code
  is right, wrong, or token unknown.
- JWT secret minimum 256-bit, persisted at
  /opt/shift-agent/state/.cockpit-jwt-secret.
- JWTs carry an `auth_method` claim ("pushover" | "totp") so sensitive
  routes can distinguish factor used. See `require_fresh_pushover_otp`
  for the gate that prevents TOTP-only attackers from disabling TOTP /
  swapping Pushover keys (closes the self-recovery-prevention attack
  surfaced in PR #2 review High #2).
"""
from __future__ import annotations

import asyncio
import hmac
import secrets
import string
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt
from pydantic import BaseModel

from .audit import log as audit_log
from .config import get_settings
from .state import load_config

settings = get_settings()


AuthMethod = Literal["pushover", "totp"]


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
    return secrets.token_hex(16)


def _now() -> float:
    return time.time()


# ─── Rate limiting (per IP and per owner) ───────────────────────────────

_request_log: dict[str, list[float]] = {}
_RL_MAX_KEYS = 4096   # cap dict size to prevent unbounded growth (Reviewer High #3)


def _rl_check(key: str, max_count: int, window_seconds: int) -> bool:
    now = _now()
    cutoff = now - window_seconds
    times = _request_log.setdefault(key, [])
    while times and times[0] < cutoff:
        times.pop(0)
    # Evict empty entries so unique-IP spray cannot leak memory.
    if not times and key in _request_log:
        del _request_log[key]
        times = _request_log.setdefault(key, [])
    # Hard cap fallback: evict oldest entries if dict has grown beyond the limit.
    if len(_request_log) > _RL_MAX_KEYS:
        # Drop ~10% — pick any 10% to keep this O(n).
        for k in list(_request_log.keys())[: _RL_MAX_KEYS // 10]:
            del _request_log[k]
    if len(times) >= max_count:
        return False
    times.append(now)
    return True


# ─── OTP request flow ───────────────────────────────────────────────────


async def issue_otp(ip: str, ua: str) -> str:
    cfg = load_config()
    owner_phone = cfg.owner.phone

    per_ip_max, per_ip_window = settings.otp_request_per_ip
    if not _rl_check(f"ip:{ip}", per_ip_max, per_ip_window):
        raise HTTPException(429, "Too many OTP requests from this IP — wait 15 min")
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
                audit_log("auth.otp.pushover_failed", details={"status": r.status_code})
    except Exception as e:
        audit_log("auth.otp.pushover_error", details={"error": str(e)[:200]})


# ─── OTP verify flow ────────────────────────────────────────────────────


async def verify_otp(token: str, code: str, ip: str, ua: str) -> str:
    """Verify Pushover OTP; return JWT on success.

    Timing structure: (per Reviewer Medium #6)
    1. All branch-distinguishing work (state read, comparison, mutations) happens.
    2. Wall-clock floor enforced via asyncio.sleep, based on `started` timestamp
       — independent of audit-log disk timing.
    3. Audit log written AFTER the floor sleep, so disk-pressure variance does
       not eat into the equalization budget.
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
            rec.invalidated = True
            otp_store.write(rec)
            audit_details["reason"] = "locked_out"
        else:
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
                jwt_token = mint_jwt(rec.issued_to, auth_method="pushover")
                otp_store.clear()
                audit_event = "auth.otp.verify_success"
                audit_details["owner"] = rec.issued_to
    finally:
        # Floor based on hot work only — independent of audit-disk variance.
        elapsed = _now() - started
        if elapsed < settings.otp_verify_min_wall_seconds:
            await asyncio.sleep(settings.otp_verify_min_wall_seconds - elapsed)
        # Audit AFTER the floor; its disk-pressure variance no longer eats
        # into the equalization budget. Response timing = floor + audit_time;
        # audit time is roughly uniform across branches (same-shape NDJSON).
        audit_log(audit_event, ip=ip, ua=ua, details=audit_details)

    if jwt_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired code")
    return jwt_token


async def floor_then_audit(started: float, event: str, ip: str, ua: str, details: dict) -> None:
    """Helper for TOTP path — same floor + audit-after pattern."""
    elapsed = _now() - started
    if elapsed < settings.otp_verify_min_wall_seconds:
        await asyncio.sleep(settings.otp_verify_min_wall_seconds - elapsed)
    audit_log(event, ip=ip, ua=ua, details=details)


# ─── JWT ────────────────────────────────────────────────────────────────


def mint_jwt(owner_phone: str, *, auth_method: AuthMethod = "pushover") -> str:
    """Mint a JWT carrying the auth method that produced it.

    `auth_method` is used by `require_fresh_pushover_otp` to gate
    self-recovery-prevention routes (TOTP-only login cannot disable own
    TOTP or swap Pushover keys — would create an account-takeover from
    a single-secret compromise).
    """
    now = datetime.now(timezone.utc)
    claims = {
        "sub": owner_phone,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.jwt_ttl_hours)).timestamp()),
        "jti": secrets.token_hex(8),
        "auth_method": auth_method,
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
    """JWT issued ≤5 min ago via ANY method (Pushover OR TOTP).

    Used for medium-sensitivity actions (most config PATCHes, unlinking
    WhatsApp). NOT used for self-recovery-prevention routes — see
    require_fresh_pushover_otp for those.
    """
    claims = await require_auth(request)
    issued_at = claims.get("iat", 0)
    if _now() - issued_at > 300:
        raise HTTPException(403, "Sensitive action requires fresh OTP — re-verify")
    return claims


async def require_fresh_pushover_otp(request: Request) -> dict[str, Any]:
    """JWT issued ≤5 min ago via Pushover OTP specifically.

    Closes the self-recovery-prevention attack: a TOTP-only-compromised
    attacker MUST NOT be able to (a) disable TOTP, (b) re-enroll a new
    TOTP secret, or (c) swap Pushover keys. Any of those would lock the
    legitimate owner out from a single-secret compromise.

    Pushover is always available to the owner (it's the primary login
    method); if Pushover is genuinely down, owner SSHes in and edits
    state directly per the runbook.
    """
    claims = await require_fresh_otp(request)
    if claims.get("auth_method") != "pushover":
        raise HTTPException(
            403,
            "This action requires Pushover OTP authentication "
            "(TOTP login cannot perform self-recovery-prevention operations).",
        )
    return claims
