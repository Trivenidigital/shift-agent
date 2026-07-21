"""TTL-0 observe-only stale flyer-project sweep (read-only).

First slice of the stale-project lifecycle fix (2026-07-20 P1-1 incident: 15
non-terminal CUST0001 projects held the sender inside the cf-router flyer
active-project intercept and swallowed a live catering inquiry, project F0224).

OBSERVE-ONLY BY CONSTRUCTION. This module reports stale candidates; it is
structurally incapable of mutating state or sending anything:
  - it imports NO bridge/send helpers and NO safe_io (so it stays importable on
    fcntl-less hosts and cannot reach the WhatsApp bridge),
  - it NEVER writes the projects store,
  - the only outputs are the metadata-only digest (returned to the caller /
    printed by the CLI) and an optional atomic write of that same digest JSON to
    a `--digest-path` file.

The digest is metadata-only (project_id, customer_id, status, ages, TTLs, legal
transition) — no customer phone, chat_id, locked facts, or message text — so it
is privacy-safe by construction.

NB (TTL-1): a decisions.log audit-row variant is deliberately NOT written here.
That needs a new LogEntry discriminated-union subclass in schemas.py; it is left
for TTL-1 so this observe slice stays a pure read.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from schemas import FlyerProject, FlyerProjectStore, is_flyer_transition_allowed


ENABLE_ENV = "FLYER_TTL0_OBSERVE_ENABLED"

# Per-status staleness TTLs (hours). A project whose activity-aware age exceeds
# its status TTL is a stale candidate. TTL-1 will consume these to drive real
# terminal transitions; TTL-0 only reports them.
NON_DELIVERED_TTL_HOURS: dict[str, int] = {
    "intake_started": 72,
    "collecting_required_info": 72,
    "awaiting_assets": 72,
    "awaiting_concept_selection": 168,
    "awaiting_final_approval": 168,
}
DELIVERED_TTL_HOURS = 168

# Statuses that are NEVER candidates; counted in the excluded_statuses tally
# only (never in the per-project `excluded` list):
#   - manual_edit_required: owned by the manual queue + source-edit SLA watchdog
#   - generating_concepts / revising_design / finalizing_assets: machine-active
#   - completed / closed_no_send: terminal
# Any other status not present in the TTL maps above (e.g. delivered_with_warning)
# is likewise unmonitored by TTL-0 and folds into the tally — see _status_ttl.
EXCLUDED_STATUSES: frozenset[str] = frozenset({
    "manual_edit_required",
    "generating_concepts",
    "revising_design",
    "finalizing_assets",
    "completed",
    "closed_no_send",
})


def parse_utc(value: str) -> datetime:
    """Parse an ISO8601 string to an aware UTC datetime (accepts trailing Z)."""
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _status_ttl(status: str) -> Optional[int]:
    """TTL hours for a monitored candidate status, else None (unmonitored)."""
    if status == "delivered":
        return DELIVERED_TTL_HOURS
    return NON_DELIVERED_TTL_HOURS.get(status)


def compute_last_activity(project: FlyerProject) -> datetime:
    """Activity-aware last-activity timestamp for a project.

    max(updated_at, newest asset delivered_at across assets, manual_review
    timestamps if present). All normalized to aware UTC.
    """
    timestamps = [_as_utc(project.updated_at)]
    for asset in project.assets:
        if asset.delivered_at is not None:
            timestamps.append(_as_utc(asset.delivered_at))
    manual = project.manual_review
    for ts in (manual.queued_at, manual.completed_at, manual.claimed_at):
        if ts is not None:
            timestamps.append(_as_utc(ts))
    return max(timestamps)


def _legal_terminal_transition(status: str) -> Optional[str]:
    """The legal terminal edge for a candidate status via the deployed
    FLYER_TRANSITIONS table, or None when no such edge exists (defensive —
    reported as the no_legal_terminal_edge exclusion)."""
    target = "completed" if status == "delivered" else "closed_no_send"
    return target if is_flyer_transition_allowed(status, target) else None


def _project_claimed(project: FlyerProject) -> bool:
    manual = project.manual_review
    if str(manual.claimed_by or "").strip():
        return True
    # Defensive: honor a top-level claimed_by if a future schema adds one.
    return bool(str(getattr(project, "claimed_by", "") or "").strip())


def _metadata_row(
    project: FlyerProject,
    *,
    status: str,
    age_hours: float,
    last_activity: datetime,
    ttl_hours: int,
    legal_transition: Optional[str],
    exclusion: Optional[str],
    claimed: bool,
) -> dict[str, Any]:
    """Metadata-only candidate row — no phone, chat_id, facts, or message text."""
    return {
        "project_id": project.project_id,
        "customer_id": project.customer_id,
        "status": status,
        "age_hours": round(age_hours, 1),
        "last_activity_ts": last_activity.isoformat(),
        "ttl_hours": ttl_hours,
        "legal_transition": legal_transition,
        "exclusion": exclusion,
        "claimed": claimed,
    }


def build_ttl0_digest(store: FlyerProjectStore, *, as_of: datetime) -> dict[str, Any]:
    """Build the metadata-only stale-candidate digest.

    `candidates` holds non-delivered stale projects with a legal terminal edge;
    `delivered_candidates` holds stale delivered projects (legal edge
    "completed"); `excluded` holds stale projects held back for a stated reason
    (claimed / no_legal_terminal_edge); `excluded_statuses` tallies projects in
    non-candidate statuses. Fresh (within-TTL) projects are simply omitted.
    """
    as_of = _as_utc(as_of)
    candidates: list[dict[str, Any]] = []
    delivered_candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    excluded_statuses: dict[str, int] = {}
    for project in store.projects:
        status = project.status
        ttl_hours = _status_ttl(status)
        if status in EXCLUDED_STATUSES or ttl_hours is None:
            excluded_statuses[status] = excluded_statuses.get(status, 0) + 1
            continue
        last_activity = compute_last_activity(project)
        age_hours = max((as_of - last_activity).total_seconds() / 3600.0, 0.0)
        if age_hours < ttl_hours:
            continue  # within TTL — not stale, not listed
        legal_transition = _legal_terminal_transition(status)
        claimed = _project_claimed(project)
        row = _metadata_row(
            project,
            status=status,
            age_hours=age_hours,
            last_activity=last_activity,
            ttl_hours=ttl_hours,
            legal_transition=legal_transition,
            exclusion=None,
            claimed=claimed,
        )
        if claimed:
            row["exclusion"] = "claimed"
            excluded.append(row)
        elif legal_transition is None:
            row["exclusion"] = "no_legal_terminal_edge"
            excluded.append(row)
        elif status == "delivered":
            delivered_candidates.append(row)
        else:
            candidates.append(row)

    def _by_project_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda r: r["project_id"])

    return {
        "enabled": True,
        "as_of": as_of.isoformat(),
        "candidates_scanned": len(store.projects),
        "candidates": _by_project_id(candidates),
        "delivered_candidates": _by_project_id(delivered_candidates),
        "excluded": _by_project_id(excluded),
        "excluded_statuses": dict(sorted(excluded_statuses.items())),
    }


def disabled_digest() -> dict[str, Any]:
    """Payload emitted when the feature flag is off — a strict no-op marker."""
    return {"enabled": False, "candidates_scanned": 0}


def is_observe_enabled(env: Mapping[str, str]) -> bool:
    """True only when FLYER_TTL0_OBSERVE_ENABLED is exactly "1" (default OFF)."""
    return env.get(ENABLE_ENV, "").strip() == "1"


def load_project_store(state_path: Path) -> FlyerProjectStore:
    """Plain read-only load of the projects store (no safe_io / no FileLock)."""
    path = Path(state_path)
    if not path.exists():
        return FlyerProjectStore()
    return FlyerProjectStore.model_validate(json.loads(path.read_text(encoding="utf-8")))


def serialize_digest(digest: dict[str, Any]) -> str:
    """Deterministic JSON for the digest — sorted keys so a fixed store + as_of
    yield byte-identical output across runs (idempotent)."""
    return json.dumps(digest, sort_keys=True, indent=2)


def write_digest_atomic(digest_path: Path, payload: str) -> None:
    """Atomic write via plain tempfile + os.replace (NOT safe_io)."""
    path = Path(digest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
