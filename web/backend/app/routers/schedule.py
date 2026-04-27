"""Schedule router — operates on roster.json schedule field under same flock."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..audit import log as audit_log
from ..auth import require_auth
from ..deps import client_ip, client_ua
from ..models import ScheduleDayPut
from ..state import load_roster, roster_session

_AGENT_ROOT = Path("/opt/shift-agent")
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))
from schemas import ScheduleEntry  # noqa: E402

router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.get("")
async def get_schedule(
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
    _=Depends(require_auth),
):
    roster = load_roster()
    out = {d: [e.model_dump(mode="json") for e in entries] for d, entries in roster.schedule.items() if from_date <= d <= to_date}
    return {"schedule": out}


@router.put("/{date}")
async def put_day(date: str, body: ScheduleDayPut, request: Request, _=Depends(require_auth)):
    """Replace a day's full schedule (atomic)."""
    with roster_session() as (roster, commit):
        # Validate every entry's employee_id exists
        emp_ids = {e.id for e in roster.employees}
        for ent in body.entries:
            if ent.employee_id not in emp_ids:
                raise HTTPException(422, f"unknown employee_id {ent.employee_id}")
        roster.schedule[date] = [ScheduleEntry(**e.model_dump()) for e in body.entries]
        commit()
    audit_log("schedule.day.put", ip=client_ip(request), ua=client_ua(request), details={"date": date, "count": len(body.entries)})
    return {"ok": True}


@router.delete("/{date}")
async def delete_day(date: str, request: Request, _=Depends(require_auth)):
    with roster_session() as (roster, commit):
        if date in roster.schedule:
            del roster.schedule[date]
            commit()
    audit_log("schedule.day.delete", ip=client_ip(request), ua=client_ua(request), details={"date": date})
    return {"ok": True}
