"""FastAPI dependencies — re-exports + helpers."""
from __future__ import annotations

from fastapi import Request

from .auth import require_auth, require_fresh_otp  # noqa: F401


def client_ip(request: Request) -> str:
    # Trust X-Forwarded-For only from Caddy (which is on 127.0.0.1)
    if request.client and request.client.host == "127.0.0.1":
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def client_ua(request: Request) -> str:
    return request.headers.get("user-agent", "")[:200]
