"""Roster CRUD router + CSV bulk import."""
from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ..audit import log as audit_log
from ..auth import require_auth, require_fresh_otp
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


# ─── CSV bulk import (BL-144) ─────────────────────────────────────────────


# Cells starting with these characters trigger formula evaluation in
# Excel/Google Sheets. We REJECT (not sanitize) at parse time per security
# review — this is a roster, not a spreadsheet output.
_FORMULA_PREFIXES: frozenset[str] = frozenset({"=", "+", "-", "@", "\t"})
_CSV_MAX_BYTES = 256_000  # ~256KB cap on CSV payload


@router.post("/import-csv")
async def import_csv(
    request: Request,
    file: UploadFile = File(...),
    _=Depends(require_fresh_otp),  # destructive: replaces entire roster.employees
):
    """Atomic full-replace import of `roster.json` employees from CSV.

    Required columns: id, name, role, phone, can_cover_roles
    Optional: nickname, languages, status

    `can_cover_roles` and `languages` are pipe-separated (`cashier|floor`)
    or comma-separated (`cashier,floor`) within their cell.

    Rejects:
    - Cell values starting with =, +, -, @, \\t (formula injection)
    - Cells containing CR/LF (data corruption / smuggling)
    - Non-UTF-8 file content (with explicit 422, not 500)
    - File > 256 KB
    - Any row that fails Pydantic validation (Roster referential integrity
      preserved by re-validating the FULL roster after replacement)
    """
    raw_bytes = await file.read()
    if len(raw_bytes) > _CSV_MAX_BYTES:
        raise HTTPException(413, f"CSV exceeds {_CSV_MAX_BYTES // 1024} KB limit")

    # Decode with BOM tolerance — fall back to clear 422 (not 500) on bad encoding.
    try:
        raw = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            422,
            "File must be UTF-8 (with optional BOM). Re-save from Excel: "
            'File → Save As → "CSV UTF-8 (Comma delimited)".',
        )

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or "id" not in reader.fieldnames:
        raise HTTPException(422, "CSV missing required header 'id'")

    employees: list[dict] = []
    for row_num, row in enumerate(reader, start=2):  # row 1 = header
        for col, val in row.items():
            if val is None:
                continue
            v = str(val)
            stripped = v.lstrip()
            if stripped and stripped[:1] in _FORMULA_PREFIXES:
                raise HTTPException(
                    422,
                    f"row {row_num} col {col!r}: cell starts with '{stripped[:1]}' — "
                    "formula-injection prefix rejected (cells must not start with = + - @)",
                )
            if "\r" in v or "\n" in v:
                raise HTTPException(422, f"row {row_num} col {col!r}: CR/LF in cell rejected")

        # Parse list-typed fields (pipe or comma separated)
        def _to_list(s: str | None) -> list[str]:
            if not s:
                return []
            return [x.strip() for x in s.replace("|", ",").split(",") if x.strip()]

        emp = {
            "id": (row.get("id") or "").strip(),
            "name": (row.get("name") or "").strip(),
            "nickname": (row.get("nickname") or "").strip() or None,
            "role": (row.get("role") or "").strip(),
            "phone": (row.get("phone") or "").strip(),
            "languages": _to_list(row.get("languages")) or ["en"],
            "can_cover_roles": _to_list(row.get("can_cover_roles")),
            "status": (row.get("status") or "active").strip(),
        }
        # Per-row Pydantic validation
        try:
            EmployeeIn(**emp).model_dump()
        except Exception as e:
            raise HTTPException(422, f"row {row_num}: {e}")
        employees.append(emp)

    if not employees:
        raise HTTPException(422, "CSV had no employee rows")

    # Replace under flock; re-validate FULL Roster (referential integrity)
    with roster_session() as (roster, commit):
        # Build the new employees from EmployeeIn shapes; Roster will re-validate.
        from schemas import Employee  # noqa: PLC0415

        roster.employees = [Employee(**e) for e in employees]
        # If schedule references employees that disappeared, Roster validator
        # raises and the with-block exits without committing.
        commit()

    audit_log(
        "roster.import_csv",
        ip=client_ip(request),
        ua=client_ua(request),
        details={"imported": len(employees), "filename": file.filename or "unknown"},
    )
    return {"ok": True, "imported": len(employees)}
