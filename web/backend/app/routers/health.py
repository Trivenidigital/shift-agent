"""Health + dashboard router.

GET /health   public: returns only {ok}; doesn't leak topology.
GET /dashboard auth: returns full status payload.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..config import get_settings
from ..models import ComponentStatus, DashboardResponse, PublicHealth
from ..state import is_disabled, load_config, load_pending, load_send_counter, settings as state_settings

router = APIRouter(tags=["status"])
settings = get_settings()


def _gateway_active() -> bool:
    try:
        r = subprocess.run(["systemctl", "is-active", "hermes-gateway"], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == "active"
    except Exception:
        return False


async def _bridge_health() -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(settings.bridge_health_url)
            if r.status_code == 200:
                data = r.json()
                return data.get("status") == "connected", data.get("status", "unknown")
        return False, f"http {r.status_code}"
    except Exception as e:
        return False, f"unreachable: {type(e).__name__}"


def _wa_paired() -> tuple[bool, str | None]:
    creds = settings.hermes_creds_json
    if not creds.exists():
        return False, None
    try:
        data = json.loads(creds.read_text())
        me_id = data.get("me", {}).get("id")
        return bool(me_id), me_id
    except Exception:
        return False, None


def _disk_ok() -> tuple[bool, str]:
    try:
        usage = shutil.disk_usage("/opt/shift-agent")
        free_gb = usage.free / (1024**3)
        return free_gb > 1.0, f"{free_gb:.1f} GB free"
    except Exception as e:
        return False, str(e)[:80]


@router.get("/health", response_model=PublicHealth)
async def health_public():
    """Public binary status — no topology leakage."""
    gw = _gateway_active()
    paired, _ = _wa_paired()
    bridge_ok, _ = await _bridge_health()
    return PublicHealth(ok=(gw and paired and bridge_ok))


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(_claims: dict = Depends(require_auth)):
    cfg = load_config()
    components: list[ComponentStatus] = []

    gw = _gateway_active()
    components.append(ComponentStatus(name="gateway", ok=gw, detail="active" if gw else "inactive"))

    bridge_ok, bridge_detail = await _bridge_health()
    components.append(ComponentStatus(name="bridge", ok=bridge_ok, detail=bridge_detail))

    paired, me_id = _wa_paired()
    components.append(ComponentStatus(name="whatsapp_paired", ok=paired, detail=me_id or "no creds"))

    disk_ok, disk_detail = _disk_ok()
    components.append(ComponentStatus(name="disk", ok=disk_ok, detail=disk_detail))

    pushover_ok = bool(cfg.alerting.pushover_user_key and cfg.alerting.pushover_app_token)
    components.append(ComponentStatus(name="pushover", ok=pushover_ok, detail="configured" if pushover_ok else "missing keys"))

    counter = load_send_counter()
    counter_dict = counter.model_dump(mode="json") if counter else None

    # Counter resets at midnight in customer tz
    try:
        tz = ZoneInfo(cfg.customer.timezone)
        now_local = datetime.now(tz)
        midnight = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        resets_at = midnight.isoformat()
    except Exception:
        resets_at = None

    pending = load_pending()
    active_pending = sum(
        1
        for p in pending.proposals.values()
        if p.status not in {"accepted", "declined", "denied_by_owner", "expired", "cancelled", "no_response_timeout"}
    )

    # Last 5 decisions
    last_decisions: list[dict] = []
    if state_settings.decisions_path.exists():
        try:
            lines = state_settings.decisions_path.read_text().strip().splitlines()[-5:]
            for line in lines:
                try:
                    last_decisions.append(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass

    return DashboardResponse(
        components=components,
        send_counter=counter_dict,
        counter_resets_at=resets_at,
        disabled=is_disabled(),
        pending_active_count=active_pending,
        last_decisions=last_decisions,
    )
