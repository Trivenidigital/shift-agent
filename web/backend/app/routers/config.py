"""Config router — owner profile + alerting + limits.

Sensitive fields require fresh OTP (per design v1.1 §8 + review fixes).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..audit import log as audit_log
from ..auth import require_auth, require_fresh_otp
from ..config import get_settings
from ..deps import client_ip, client_ua
from ..models import ConfigPatch
from ..state import load_config, save_config

router = APIRouter(prefix="/config", tags=["config"])
settings = get_settings()


def _mask(value: str) -> str:
    if not value or len(value) < 8:
        return "***"
    return value[:4] + "…" + value[-2:]


@router.get("")
async def get_config(_=Depends(require_auth)):
    cfg = load_config()
    raw = cfg.model_dump(mode="json")
    # Mask sensitive fields in response
    if "alerting" in raw:
        raw["alerting"]["pushover_user_key"] = _mask(raw["alerting"].get("pushover_user_key", ""))
        raw["alerting"]["pushover_app_token"] = _mask(raw["alerting"].get("pushover_app_token", ""))
    return raw


def _set_dotted(obj: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


@router.patch("")
async def patch_config(body: ConfigPatch, request: Request, _=Depends(require_auth)):
    """Patch flat dotted-path fields. Sensitive fields blocked here -> use /sensitive."""
    sensitive = settings.sensitive_config_fields & body.fields.keys()
    if sensitive:
        raise HTTPException(
            403,
            f"Sensitive fields {sorted(sensitive)} require POST /config/sensitive (fresh OTP)",
        )
    cfg = load_config()
    raw = cfg.model_dump(mode="json")
    for k, v in body.fields.items():
        _set_dotted(raw, k, v)
    # Re-validate
    try:
        from schemas import Config

        new_cfg = Config.model_validate(raw)
    except Exception as e:
        raise HTTPException(422, f"invalid config: {e}")
    save_config(new_cfg)
    audit_log(
        "config.patch",
        ip=client_ip(request),
        ua=client_ua(request),
        details={"fields": list(body.fields.keys())},
    )
    return {"ok": True}


@router.patch("/sensitive")
async def patch_config_sensitive(
    body: ConfigPatch, request: Request, _=Depends(require_fresh_otp)
):
    """Patch sensitive fields — requires JWT issued ≤5min ago."""
    cfg = load_config()
    raw = cfg.model_dump(mode="json")
    for k, v in body.fields.items():
        _set_dotted(raw, k, v)
    try:
        from schemas import Config

        new_cfg = Config.model_validate(raw)
    except Exception as e:
        raise HTTPException(422, f"invalid config: {e}")
    save_config(new_cfg)
    audit_log(
        "config.sensitive_patch",
        ip=client_ip(request),
        ua=client_ua(request),
        details={"fields": list(body.fields.keys())},
    )
    return {"ok": True}
