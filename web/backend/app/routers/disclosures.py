"""Disclosures sign + status."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from ..audit import log as audit_log
from ..auth import require_fresh_otp, require_auth
from ..deps import client_ip, client_ua
from ..models import DisclosureSign

_AGENT_ROOT = Path("/opt/shift-agent")
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))
import safe_io  # noqa: E402

from ..config import get_settings  # noqa: E402

router = APIRouter(prefix="/disclosures", tags=["disclosures"])
settings = get_settings()


REQUIRED_DISCLOSURES = (
    ("baileys_tos", "I understand my WhatsApp number uses an unofficial client (Baileys); Meta may restrict it."),
    ("audit_immutability", "I understand the audit log is checksum-protected but not cryptographically immutable."),
    ("employee_notification", "I have sent my employees the pre-go-live notification and will keep the roster current."),
)


def _read_state() -> dict:
    if not settings.cockpit_disclosures_path.exists():
        return {}
    try:
        return json.loads(settings.cockpit_disclosures_path.read_text())
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    safe_io.atomic_write_text(settings.cockpit_disclosures_path, json.dumps(state, indent=2))


@router.get("")
async def status(_=Depends(require_auth)):
    state = _read_state()
    return {
        "disclosures": [
            {
                "id": did,
                "text": text,
                "signed": did in state,
                "signed_at": state.get(did, {}).get("signed_at"),
                "signed_by_name": state.get(did, {}).get("signed_by_name"),
            }
            for did, text in REQUIRED_DISCLOSURES
        ]
    }


@router.post("/sign")
async def sign(body: DisclosureSign, request: Request, _=Depends(require_fresh_otp)):
    state = _read_state()
    state[body.disclosure_id] = {
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "signed_by_name": body.signed_by_name,
        "ip": client_ip(request),
        "ua": client_ua(request),
    }
    _write_state(state)
    audit_log("disclosures.sign", ip=client_ip(request), ua=client_ua(request), details={"id": body.disclosure_id})
    return {"ok": True}
