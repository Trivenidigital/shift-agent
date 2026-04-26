"""Pending proposals router — read-only + cancel via CLI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..audit import log as audit_log
from ..auth import require_auth
from ..deps import client_ip, client_ua
from ..models import CancelBody, ProposalView
from ..shell import run_cli
from ..state import load_pending

router = APIRouter(prefix="/pending", tags=["pending"])


@router.get("", response_model=list[ProposalView])
async def list_pending(
    include_terminal: bool = Query(False),
    _=Depends(require_auth),
):
    store = load_pending()
    TERMINAL = {"accepted", "declined", "denied_by_owner", "expired", "cancelled", "no_response_timeout"}
    out: list[ProposalView] = []
    for p in store.proposals.values():
        if not include_terminal and p.status in TERMINAL:
            continue
        out.append(
            ProposalView(
                proposal_id=p.proposal_id,
                code=p.code,
                status=p.status,
                absent_employee_id=p.absent_employee_id,
                candidate_employee_id=getattr(p, "candidate_employee_id", None),
                absent_date=p.absent_date,
                absent_shift=p.absent_shift,
                absent_role=p.absent_role,
                absent_reason=p.absent_reason,
                created_ts=p.created_ts.isoformat() if hasattr(p.created_ts, "isoformat") else str(p.created_ts),
                last_updated_ts=p.last_updated_ts.isoformat() if hasattr(p.last_updated_ts, "isoformat") else str(p.last_updated_ts),
                outbound_message_id=getattr(p, "outbound_message_id", None),
            )
        )
    out.sort(key=lambda x: x.last_updated_ts, reverse=True)
    return out


@router.post("/{proposal_id}/cancel")
async def cancel(proposal_id: str, body: CancelBody, request: Request, _=Depends(require_auth)):
    """Cancel via CLI; CLI is source of truth on transition legality."""
    r = run_cli(
        "/usr/local/bin/update-proposal-status",
        ["--cause", "owner_cockpit_cancel", "--actor", "owner_cockpit"],
        user_args=[proposal_id, "cancelled"],
    )
    if r.returncode != 0:
        # Surface CLI stderr verbatim for debugging
        raise HTTPException(400, r.stderr.strip() or "cancel failed")

    audit_log(
        "pending.cancel",
        ip=client_ip(request),
        ua=client_ua(request),
        details={"proposal_id": proposal_id, "reason": body.reason},
    )
    return {"ok": True}
