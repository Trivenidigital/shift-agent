"""Kill-switch + test-alert router."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..audit import log as audit_log
from ..auth import require_auth
from ..deps import client_ip, client_ua
from ..models import SafetyToggleBody
from ..shell import run_cli

router = APIRouter(prefix="/safety", tags=["safety"])


@router.post("/disable")
async def disable(body: SafetyToggleBody, request: Request, _=Depends(require_auth)):
    r = run_cli(
        "/usr/local/bin/shift-agent-disable",
        [],
        user_args=[body.reason],
    )
    if r.returncode != 0:
        raise HTTPException(500, r.stderr.strip() or "disable failed")
    audit_log("safety.disable", ip=client_ip(request), ua=client_ua(request), details={"reason": body.reason})
    return {"ok": True, "stdout": r.stdout.strip()}


@router.post("/enable")
async def enable(body: SafetyToggleBody, request: Request, _=Depends(require_auth)):
    r = run_cli(
        "/usr/local/bin/shift-agent-enable",
        [],
        user_args=[body.reason],
    )
    if r.returncode != 0:
        raise HTTPException(500, r.stderr.strip() or "enable failed")
    audit_log("safety.enable", ip=client_ip(request), ua=client_ua(request), details={"reason": body.reason})
    return {"ok": True, "stdout": r.stdout.strip()}


@router.post("/test-alert")
async def test_alert(request: Request, _=Depends(require_auth)):
    r = run_cli(
        "/usr/local/bin/shift-agent-notify-owner",
        ["--message", "Cockpit test alert (no action required)"],
    )
    audit_log("safety.test_alert", ip=client_ip(request), ua=client_ua(request))
    if r.returncode != 0:
        raise HTTPException(500, r.stderr.strip() or "test alert failed")
    return {"ok": True}
