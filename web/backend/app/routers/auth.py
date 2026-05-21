"""Auth router — OTP request/verify/logout/me + TOTP fallback + status.

Sensitive routes split (per PR-2 Reviewer High #2):
- `require_fresh_otp` (any method, ≤5 min): for medium-sensitivity actions.
- `require_fresh_pushover_otp` (Pushover-only, ≤5 min): for self-recovery-
  prevention routes — TOTP-only attacker MUST NOT be able to disable own
  TOTP, re-enroll a new TOTP, or swap Pushover keys (would create
  account-takeover from a single-secret compromise).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import totp as totp_mod
from ..audit import log as audit_log
from ..auth import (
    floor_then_audit,
    issue_otp,
    mint_jwt,
    require_auth,
    require_fresh_pushover_otp,
    verify_otp,
)
from ..config import get_settings
from ..deps import client_ip, client_ua
from ..models import MeResponse, OtpRequestResponse, OtpVerifyBody
from ..state import load_config

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


class AuthStatus(BaseModel):
    """Public — drives login screen tab visibility. No sensitive data."""

    totp_enrolled: bool
    pushover_configured: bool
    auth_bypass_enabled: bool = False


class TotpVerifyBody(BaseModel):
    # Reviewer Nit: pyotp default is 6 digits; pin to exactly 6 to avoid
    # silent-rejection of 7-8 digit input. If we ever want longer codes,
    # pass digits=N to pyotp.TOTP() and bump this together.
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


# ─── Pushover OTP ──────────────────────────────────────────────────────


@router.post("/request-otp", response_model=OtpRequestResponse)
async def request_otp(request: Request, response: Response):
    ip = client_ip(request)
    ua = client_ua(request)
    if settings.auth_bypass_enabled:
        cfg = load_config()
        jwt_token = mint_jwt(cfg.owner.phone, auth_method="pushover")
        response.set_cookie(
            key=settings.cookie_name,
            value=jwt_token,
            max_age=settings.jwt_ttl_hours * 3600,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path="/",
        )
        audit_log("auth.dev_bypass_login", ip=ip, ua=ua, details={"temporary": True})
        return OtpRequestResponse(token="__bypass__", expires_in_seconds=settings.jwt_ttl_hours * 3600)
    token = await issue_otp(ip, ua)
    return OtpRequestResponse(token=token, expires_in_seconds=settings.otp_ttl_seconds)


@router.post("/verify-otp")
async def verify(body: OtpVerifyBody, request: Request, response: Response):
    ip = client_ip(request)
    ua = client_ua(request)
    jwt_token = await verify_otp(body.token, body.code, ip, ua)
    response.set_cookie(
        key=settings.cookie_name,
        value=jwt_token,
        max_age=settings.jwt_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(claims: dict = Depends(require_auth)):
    cfg = load_config()
    return MeResponse(
        owner_phone=cfg.owner.phone,
        owner_name=cfg.owner.name,
        issued_at=claims["iat"],
        expires_at=claims["exp"],
    )


# ─── Public auth-status endpoint (drives login UI tab logic) ─────────────


@router.get("/status", response_model=AuthStatus)
async def auth_status() -> AuthStatus:
    """Public — login screen reads to decide tab visibility. No sensitive data."""
    cfg = load_config()
    return AuthStatus(
        totp_enrolled=totp_mod.is_enrolled(),
        pushover_configured=bool(
            cfg.alerting.pushover_user_key and cfg.alerting.pushover_app_token
        ),
        auth_bypass_enabled=bool(settings.auth_bypass_enabled),
    )


# ─── TOTP enrollment ──────────────────────────────────────────────────────


@router.post("/totp/enroll-start")
async def totp_enroll_start(
    request: Request, _claims: dict = Depends(require_fresh_pushover_otp)
):
    """Begin TOTP enrollment.

    Requires fresh Pushover OTP — TOTP-only login cannot self-enroll a new
    secret (closes self-recovery-prevention attack from PR-2 review).
    """
    cfg = load_config()
    body = totp_mod.enroll_start(cfg.owner.phone)
    audit_log(
        "auth.totp.enroll_start",
        ip=client_ip(request),
        ua=client_ua(request),
        details={"actor_method": "pushover"},
    )
    return body  # {otpauth_uri, qr_b64, secret_for_manual_entry}


@router.post("/totp/enroll-verify")
async def totp_enroll_verify(
    body: TotpVerifyBody,
    request: Request,
    _claims: dict = Depends(require_auth),
):
    """Confirm enrollment by submitting a valid TOTP from the authenticator."""
    totp_mod.enroll_verify(body.code)
    audit_log("auth.totp.enroll_verify", ip=client_ip(request), ua=client_ua(request))
    return {"ok": True}


@router.post("/totp/disable")
async def totp_disable(request: Request, _=Depends(require_fresh_pushover_otp)):
    """Disable TOTP — Pushover-only sensitive (TOTP attacker can't lock out the
    legitimate owner's recovery channel)."""
    totp_mod.disable()
    audit_log("auth.totp.disable", ip=client_ip(request), ua=client_ua(request))
    return {"ok": True}


@router.post("/verify-totp")
async def verify_totp_route(body: TotpVerifyBody, request: Request, response: Response):
    """Public — fallback login when Pushover is unavailable.

    Same wall-clock floor + audit-after-floor pattern as Pushover verify_otp,
    so timing doesn't differentiate happy/sad paths to a remote observer.
    """
    started = time.time()
    ip = client_ip(request)
    ua = client_ua(request)

    audit_event: str
    audit_details: dict
    jwt_token: str | None = None
    try:
        owner_phone = totp_mod.verify(body.code)
        if owner_phone is None:
            audit_event = "auth.totp.verify_failed"
            audit_details = {"reason": "wrong_code"}
        else:
            jwt_token = mint_jwt(owner_phone, auth_method="totp")
            audit_event = "auth.totp.verify_success"
            audit_details = {"owner": owner_phone}
    except HTTPException as he:
        # Lockout / not-enrolled paths — record + still apply floor.
        audit_event = "auth.totp.verify_failed"
        audit_details = {"reason": f"http_{he.status_code}"}
        await floor_then_audit(started, audit_event, ip, ua, audit_details)
        raise

    await floor_then_audit(started, audit_event, ip, ua, audit_details)

    if jwt_token is None:
        raise HTTPException(401, "invalid TOTP code")

    response.set_cookie(
        key=settings.cookie_name,
        value=jwt_token,
        max_age=settings.jwt_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"ok": True}
