"""Decisions log reader + CSV export."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from ..audit import log as audit_log
from ..auth import require_auth, require_fresh_otp
from ..models import DecisionEntry
from ..state import settings

router = APIRouter(prefix="/decisions", tags=["decisions"])


def _read_decisions(
    type_filter: str | None = None,
    proposal_id: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    if not settings.decisions_path.exists():
        return []
    out: list[dict[str, Any]] = []
    with settings.decisions_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if type_filter and e.get("type") != type_filter:
                continue
            if proposal_id and e.get("proposal_id") != proposal_id:
                continue
            if from_ts and e.get("ts", "") < from_ts:
                continue
            if to_ts and e.get("ts", "") > to_ts:
                continue
            out.append(e)
    out.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return out[:limit]


@router.get("", response_model=list[DecisionEntry])
async def list_decisions(
    type_filter: str | None = Query(None, alias="type"),
    proposal_id: str | None = Query(None),
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    limit: int = Query(500, ge=1, le=5000),
    _=Depends(require_auth),
):
    raw = _read_decisions(type_filter, proposal_id, from_ts, to_ts, limit)
    return [
        DecisionEntry(
            ts=e.get("ts", ""),
            type=e.get("type", ""),
            proposal_id=e.get("proposal_id"),
            extras={k: v for k, v in e.items() if k not in {"ts", "type", "proposal_id"}},
        )
        for e in raw
    ]


@router.get(".csv")
async def export_csv(
    type_filter: str | None = Query(None, alias="type"),
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    _=Depends(require_fresh_otp),  # PII export: requires fresh OTP
):
    rows = _read_decisions(type_filter, None, from_ts, to_ts, 5000)
    audit_log("decisions.csv_export", details={"rows": len(rows)})

    buf = io.StringIO()
    if rows:
        keys: list[str] = list({k for r in rows for k in r})
        writer = csv.DictWriter(buf, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="decisions-{datetime.utcnow().strftime("%Y%m%d")}.csv"'},
    )
