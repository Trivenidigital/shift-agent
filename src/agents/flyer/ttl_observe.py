"""TTL-0 observe-only stale flyer-project sweep (read-only).

First slice of the stale-project lifecycle fix (2026-07-20 P1-1 incident: 15
non-terminal CUST0001 projects held the sender inside the cf-router flyer
active-project intercept and swallowed a live catering inquiry, project F0224).

OBSERVE-ONLY BY CONSTRUCTION. This module reports stale candidates; it is
structurally incapable of mutating state or sending anything:
  - it imports NO bridge/send helpers and NO safe_io (so it stays importable on
    fcntl-less hosts and cannot reach the WhatsApp bridge),
  - it NEVER writes the projects store,
  - the ONLY filesystem write in this module lives in _write_digest_file, and
    that writes solely to the caller-supplied --digest-path (see the AST no-write
    guard in tests/test_flyer_ttl0_observe.py). Digest output otherwise goes to
    the caller / stdout.

The digest is metadata-only (project_id, customer_id, status, ages, TTLs, legal
transition) — no customer phone, chat_id, locked facts, or message text — so it
is privacy-safe by construction.

The sweep reads RAW project dicts (not validated Pydantic models) on purpose: a
row with a missing / unparseable / self-contradictory timestamp must be surfaced
for human review, not crash the whole sweep and not be silently trusted into
eligibility. See _timestamp_disposition.

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

from schemas import is_flyer_transition_allowed


ENABLE_ENV = "FLYER_TTL0_OBSERVE_ENABLED"
MODE = "observe_only"

# Per-status staleness TTLs (hours). A project is stale only when its
# activity-aware age is STRICTLY GREATER THAN its status TTL — a project sitting
# at exactly its TTL is not yet a candidate (see build_ttl0_digest). TTL-1 will
# consume these to drive real terminal transitions; TTL-0 only reports them.
NON_DELIVERED_TTL_HOURS: dict[str, int] = {
    "intake_started": 72,
    "collecting_required_info": 72,
    "awaiting_assets": 72,
    "awaiting_concept_selection": 168,
    "awaiting_final_approval": 168,
}
DELIVERED_TTL_HOURS = 168

# Tolerance for last_activity slightly after as_of (minor clock skew between the
# writer host and the sweep instant). Beyond this, a future last_activity is a
# contradiction routed to manual_review_required rather than trusted.
CLOCK_SKEW_HOURS = 1.0

# Statuses that are NEVER candidates; counted in the excluded_statuses tally only
# (never in the per-project `excluded` list):
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


def _parse_ts(value: Any) -> tuple[Optional[datetime], bool]:
    """Parse an optional timestamp field.

    Returns (dt, ok):
      - (None, True)  — absent/empty: a legitimately missing value, not corrupt.
      - (None, False) — present but unparseable: corrupt.
      - (dt, True)    — parsed to aware UTC.
    """
    if value is None:
        return None, True
    if isinstance(value, str) and not value.strip():
        return None, True
    try:
        return parse_utc(str(value)), True
    except (ValueError, TypeError):
        return None, False


def _status_ttl(status: str) -> Optional[int]:
    """TTL hours for a monitored candidate status, else None (unmonitored)."""
    if status == "delivered":
        return DELIVERED_TTL_HOURS
    return NON_DELIVERED_TTL_HOURS.get(status)


def _asset_delivered_ts(raw: Mapping[str, Any]) -> tuple[list[datetime], bool]:
    """Parsed asset delivered_at values + a corrupt flag (any unparseable one)."""
    result: list[datetime] = []
    corrupt = False
    for asset in raw.get("assets") or []:
        if not isinstance(asset, Mapping):
            continue
        dt, ok = _parse_ts(asset.get("delivered_at"))
        if not ok:
            corrupt = True
        elif dt is not None:
            result.append(dt)
    return result, corrupt


def _manual_review_ts(raw: Mapping[str, Any]) -> tuple[list[datetime], bool]:
    """Parsed manual_review timestamps + a corrupt flag (any unparseable one)."""
    result: list[datetime] = []
    corrupt = False
    manual = raw.get("manual_review")
    if isinstance(manual, Mapping):
        for key in ("queued_at", "completed_at", "claimed_at"):
            dt, ok = _parse_ts(manual.get(key))
            if not ok:
                corrupt = True
            elif dt is not None:
                result.append(dt)
    return result, corrupt


def compute_last_activity(raw: Mapping[str, Any]) -> Optional[datetime]:
    """Activity-aware last-activity timestamp for a raw project.

    max(updated_at, newest asset delivered_at across assets, manual_review
    timestamps if present). Returns None when no parseable timestamp exists.
    Unparseable values are skipped here; corruption is judged by
    _timestamp_disposition, which is what routes a row to manual_review_required.
    """
    updated_dt, _ = _parse_ts(raw.get("updated_at"))
    asset_dts, _ = _asset_delivered_ts(raw)
    manual_dts, _ = _manual_review_ts(raw)
    timestamps = [dt for dt in (updated_dt, *asset_dts, *manual_dts) if dt is not None]
    return max(timestamps) if timestamps else None


def _timestamp_disposition(
    raw: Mapping[str, Any], *, as_of: datetime,
) -> tuple[Optional[datetime], Optional[str]]:
    """Return (last_activity, integrity_reason).

    integrity_reason is "manual_review_required" when timestamps are missing or
    self-contradictory — the row must be surfaced for a human, never inferred
    into eligibility. Otherwise it is None and last_activity is trustworthy.

    Corruption / contradiction triggers:
      - updated_at missing or unparseable (no reliable activity baseline),
      - created_at present but unparseable,
      - any asset delivered_at or manual_review timestamp unparseable,
      - an asset delivered_at earlier than created_at (delivered before created),
      - last_activity after as_of beyond CLOCK_SKEW_HOURS (future activity).
    """
    updated_dt, updated_ok = _parse_ts(raw.get("updated_at"))
    created_dt, created_ok = _parse_ts(raw.get("created_at"))
    asset_dts, asset_corrupt = _asset_delivered_ts(raw)
    _manual_dts, manual_corrupt = _manual_review_ts(raw)

    last_activity = compute_last_activity(raw)

    if updated_dt is None or not updated_ok or not created_ok or asset_corrupt or manual_corrupt:
        return last_activity, "manual_review_required"
    if created_dt is not None and any(dt < created_dt for dt in asset_dts):
        return last_activity, "manual_review_required"
    if last_activity is not None:
        age_hours = (as_of - last_activity).total_seconds() / 3600.0
        if age_hours < -CLOCK_SKEW_HOURS:
            return last_activity, "manual_review_required"
    return last_activity, None


def _legal_terminal_transition(status: str) -> Optional[str]:
    """The legal terminal edge for a candidate status via the deployed
    FLYER_TRANSITIONS table, or None when no such edge exists (defensive —
    reported as the no_legal_terminal_edge exclusion)."""
    target = "completed" if status == "delivered" else "closed_no_send"
    return target if is_flyer_transition_allowed(status, target) else None


def _project_claimed(raw: Mapping[str, Any]) -> bool:
    manual = raw.get("manual_review")
    if isinstance(manual, Mapping) and str(manual.get("claimed_by") or "").strip():
        return True
    return bool(str(raw.get("claimed_by") or "").strip())


def _metadata_row(
    raw: Mapping[str, Any],
    *,
    status: str,
    age_hours: Optional[float],
    last_activity: Optional[datetime],
    ttl_hours: int,
    legal_transition: Optional[str],
    exclusion: Optional[str],
    claimed: bool,
) -> dict[str, Any]:
    """Metadata-only candidate row — no phone, chat_id, facts, or message text."""
    return {
        "project_id": str(raw.get("project_id") or ""),
        "customer_id": str(raw.get("customer_id") or ""),
        "status": status,
        "age_hours": round(age_hours, 1) if age_hours is not None else None,
        "last_activity_ts": last_activity.isoformat() if last_activity is not None else None,
        "ttl_hours": ttl_hours,
        "legal_transition": legal_transition,
        "exclusion": exclusion,
        "claimed": claimed,
    }


def build_ttl0_digest(store: Mapping[str, Any], *, as_of: datetime) -> dict[str, Any]:
    """Build the metadata-only stale-candidate digest from a raw store dict.

    `candidates` holds non-delivered stale projects with a legal terminal edge;
    `delivered_candidates` holds stale delivered projects (legal edge
    "completed", each tagged operator_decision_required so it can never read as a
    safe auto-close); `excluded` holds stale/corrupt projects held back for a
    stated reason (manual_review_required / claimed / no_legal_terminal_edge);
    `excluded_statuses` tallies projects in non-candidate statuses. Fresh
    (within-TTL) projects are simply omitted.
    """
    as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    as_of = as_of.astimezone(timezone.utc)
    projects = list(store.get("projects") or [])
    candidates: list[dict[str, Any]] = []
    delivered_candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    excluded_statuses: dict[str, int] = {}
    for raw in projects:
        if not isinstance(raw, Mapping):
            continue
        status = str(raw.get("status") or "")
        ttl_hours = _status_ttl(status)
        if status in EXCLUDED_STATUSES or ttl_hours is None:
            excluded_statuses[status] = excluded_statuses.get(status, 0) + 1
            continue

        last_activity, integrity_reason = _timestamp_disposition(raw, as_of=as_of)
        claimed = _project_claimed(raw)
        legal_transition = _legal_terminal_transition(status)

        # Corrupt / contradictory timestamps: surface for human review, never a
        # candidate, regardless of apparent age or claim state.
        if integrity_reason is not None:
            age: Optional[float] = None
            if last_activity is not None:
                age = max((as_of - last_activity).total_seconds() / 3600.0, 0.0)
            excluded.append(_metadata_row(
                raw, status=status, age_hours=age, last_activity=last_activity,
                ttl_hours=ttl_hours, legal_transition=legal_transition,
                exclusion=integrity_reason, claimed=claimed,
            ))
            continue

        age_hours = max((as_of - last_activity).total_seconds() / 3600.0, 0.0)
        # Stale requires STRICTLY greater than TTL; exactly-at-TTL is not stale.
        if age_hours <= ttl_hours:
            continue  # within TTL — not stale, not listed

        row = _metadata_row(
            raw, status=status, age_hours=age_hours, last_activity=last_activity,
            ttl_hours=ttl_hours, legal_transition=legal_transition,
            exclusion=None, claimed=claimed,
        )
        if claimed:
            row["exclusion"] = "claimed"
            excluded.append(row)
        elif legal_transition is None:
            row["exclusion"] = "no_legal_terminal_edge"
            excluded.append(row)
        elif status == "delivered":
            row["operator_decision_required"] = True
            delivered_candidates.append(row)
        else:
            candidates.append(row)

    def _by_project_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda r: r["project_id"])

    return {
        "enabled": True,
        "mode": MODE,
        "as_of": as_of.isoformat(),
        "candidates_scanned": len(projects),
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


def load_store(state_path: Path) -> dict[str, Any]:
    """Plain read-only load of the raw projects store (no safe_io / no FileLock).

    Missing file → empty store. A whole-file JSON parse error is raised (loud):
    per-project malformed timestamps are handled row-by-row by the digest
    builder, but a corrupt store file is an operational fault worth surfacing.
    """
    path = Path(state_path)
    if not path.exists():
        return {"projects": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"projects": []}
    return data


def serialize_digest(digest: Mapping[str, Any]) -> str:
    """Deterministic JSON for the digest — sorted keys so a fixed store + as_of
    yield byte-identical output across runs (idempotent)."""
    return json.dumps(digest, sort_keys=True, indent=2)


def _write_digest_file(digest_path: Path, payload: str) -> None:
    """Atomic write via plain tempfile + os.replace (NOT safe_io).

    This is the ONLY filesystem write in the module and it targets solely the
    caller-supplied --digest-path. The AST no-write guard whitelists this
    function BY NAME; do not add writes/mutations anywhere else.
    """
    path = Path(digest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
