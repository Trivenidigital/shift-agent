"""Auth router — OTP request/verify/logout/me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from ..auth import issue_otp, verify_otp, decode_jwt, require_auth
from ..config import get_settings
from ..deps import client_ip, client_ua
from ..models import MeResponse, OtpRequestResponse, OtpVerifyBody
from ..state import load_config

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


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
