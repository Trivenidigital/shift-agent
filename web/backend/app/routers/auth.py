"""Auth router — OTP request/verify/logout/me + TOTP fallback + status."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import totp as totp_mod
from ..audit import log as audit_log
from ..auth import issue_otp, verify_otp, mint_jwt, require_auth, require_fresh_otp
from ..config import get_settings
from ..deps import client_ip, client_ua
from ..models import MeResponse, OtpRequestResponse, OtpVerifyBody
from ..state import load_config

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


class AuthStatus(BaseModel):
    """Public — drives the login screen's tab visibility logic.
    No sensitive data (no owner phone, no rate-limit state)."""

    totp_enrolled: bool
    pushover_configured: bool


class TotpVerifyBody(BaseModel):
    code: str = Field(min_length=6, max_length=8, pattern=r"^\d{6,8}$")


# ─── Pushover OTP ──────────────────────────────────────────────────────


@router.post("/request-otp", response_model=OtpRequestResponse)
async def request_otp(request: Request):
    ip = client_ip(request)
    ua = client_ua(request)
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
    """Public — login screen reads this to decide tab visibility.
    No sensitive data; no rate-limit info; no owner phone."""
    cfg = load_config()
    return AuthStatus(
        totp_enrolled=totp_mod.is_enrolled(),
        pushover_configured=bool(
            cfg.alerting.pushover_user_key and cfg.alerting.pushover_app_token
        ),
    )


# ─── TOTP enrollment + verification ────────────────────────────────────────


@router.post("/totp/enroll-start")
async def totp_enroll_start(request: Request, _claims: dict = Depends(require_fresh_otp)):
    """Begin TOTP enrollment — requires JWT issued via Pushover OTP ≤5 min ago.

    Refuses if already enrolled (Reviewer 1 S1: was unbounded — now require_fresh_otp
    + the totp.enroll_start guard make this safe; audit-log every call so multiple
    enroll-start attempts are visible).
    """
    cfg = load_config()
    body = totp_mod.enroll_start(cfg.owner.phone)
    audit_log("auth.totp.enroll_start", ip=client_ip(request), ua=client_ua(request))
    return body  # {otpauth_uri, qr_b64, secret_for_manual_entry}


@router.post("/totp/enroll-verify")
async def totp_enroll_verify(
    body: TotpVerifyBody,
    request: Request,
    _claims: dict = Depends(require_auth),
):
    """Confirm enrollment by submitting a valid TOTP from the authenticator app."""
    totp_mod.enroll_verify(body.code)
    audit_log("auth.totp.enroll_verify", ip=client_ip(request), ua=client_ua(request))
    return {"ok": True}


@router.post("/totp/disable")
async def totp_disable(request: Request, _=Depends(require_fresh_otp)):
    totp_mod.disable()
    audit_log("auth.totp.disable", ip=client_ip(request), ua=client_ua(request))
    return {"ok": True}


@router.post("/verify-totp")
async def verify_totp_route(body: TotpVerifyBody, request: Request, response: Response):
    """Public — fallback login when Pushover OTP isn't available.

    Reads ONLY the committed totp_secret_path; refuses 412 if only pending exists
    (closes design-review S1 attack vector).
    """
    ip = client_ip(request)
    ua = client_ua(request)
    owner_phone = totp_mod.verify(body.code)
    if owner_phone is None:
        audit_log("auth.totp.verify_failed", ip=ip, ua=ua)
        raise HTTPException(401, "invalid TOTP code")
    jwt_token = mint_jwt(owner_phone)
    response.set_cookie(
        key=settings.cookie_name,
        value=jwt_token,
        max_age=settings.jwt_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    audit_log("auth.totp.verify_success", ip=ip, ua=ua, details={"owner": owner_phone})
    return {"ok": True}
