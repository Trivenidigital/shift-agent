"""Roster CRUD router."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from ..audit import log as audit_log
from ..auth import require_auth
from ..deps import client_ip, client_ua
from ..models import EmployeeIn, EmployeePatch
from ..state import load_roster, roster_session

# Import schemas
_AGENT_ROOT = Path("/opt/shift-agent")
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))
from schemas import Employee, PhoneHistoryEntry  # noqa: E402

router = APIRouter(prefix="/roster", tags=["roster"])


@router.get("")
async def list_roster(_=Depends(require_auth)):
    return load_roster().model_dump(mode="json")


@router.post("/employee", status_code=201)
async def add_employee(body: EmployeeIn, request: Request, _=Depends(require_auth)):
    with roster_session() as (roster, commit):
        if any(e.id == body.id for e in roster.employees):
            raise HTTPException(409, f"employee id {body.id} already exists")
        try:
            new_emp = Employee(**body.model_dump())
        except Exception as e:
            raise HTTPException(422, f"invalid employee: {e}")
        roster.employees.append(new_emp)
        commit()
    audit_log(
        "roster.employee.add",
        ip=client_ip(request),
        ua=client_ua(request),
        details={"id": body.id, "name": body.name},
    )
    return {"ok": True, "id": body.id}


@router.patch("/employee/{employee_id}")
async def patch_employee(
    employee_id: str,
    patch: EmployeePatch,
    request: Request,
    _=Depends(require_auth),
):
    patched_fields: list[str] = []
    with roster_session() as (roster, commit):
        emp = next((e for e in roster.employees if e.id == employee_id), None)
        if emp is None:
            raise HTTPException(404, f"employee {employee_id} not found")

        data = patch.model_dump(exclude_unset=True)
        # Phone change → record phone_history
        if "phone" in data and data["phone"] != emp.phone:
            now = datetime.now(timezone.utc).isoformat()
            history = list(emp.phone_history or [])
            history.append(
                PhoneHistoryEntry(
                    phone=emp.phone,
                    effective_from=getattr(emp, "phone_set_ts", None) or emp.created_ts or now,
                    effective_to=now,
                )
            )
            emp.phone_history = history
            emp.phone = data["phone"]
            emp.phone_set_ts = now
            patched_fields.append("phone")
            data.pop("phone")

        for k, v in data.items():
            setattr(emp, k, v)
            patched_fields.append(k)

        commit()
    audit_log(
        "roster.employee.patch",
        ip=client_ip(request),
        ua=client_ua(request),
        details={"id": employee_id, "fields": patched_fields},
    )
    return {"ok": True, "fields": patched_fields}


@router.delete("/employee/{employee_id}")
async def terminate_employee(employee_id: str, request: Request, _=Depends(require_auth)):
    """Soft-delete: set status=terminated. Preserves history + phone for audits."""
    with roster_session() as (roster, commit):
        emp = next((e for e in roster.employees if e.id == employee_id), None)
        if emp is None:
            raise HTTPException(404)
        emp.status = "terminated"
        commit()
    audit_log(
        "roster.employee.terminate",
        ip=client_ip(request),
        ua=client_ua(request),
        details={"id": employee_id},
    )
    return {"ok": True}
