"""Cockpit audit log reader (read-only — log itself is chattr +a)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from ..auth import require_auth
from ..config import get_settings

router = APIRouter(prefix="/audit", tags=["audit"])
settings = get_settings()


@router.get("")
async def list_audit(
    limit: int = Query(200, ge=1, le=2000),
    _=Depends(require_auth),
):
    if not settings.cockpit_audit_log.exists():
        return []
    out = []
    with settings.cockpit_audit_log.open() as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    out.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return out[:limit]
