"""PR-D1: audit-emission helpers that need both schemas + safe_io primitives.

Why a separate module:
  - safe_io.py is filesystem/lock primitives. Importing schemas there creates
    a cycle (schemas references safe_io for serializers).
  - These helpers MUST be best-effort: if the audit-write itself fails, the
    helper swallows the secondary error and returns silently — the caller
    is typically already in a primary error path (config-load failed,
    lead diverged) and double-failure must not shadow the first.

Naming convention: `log_<event>_best_effort` so the swallow-all-errors
contract is visible at every callsite.

Platform-helpers consolidation 2026-04-30 added scan_orphan_pushes() — NOT
a best-effort helper (it returns flagged IDs for caller persistence) but
shares the module since it's audit-log-tail-scanning logic.
"""
from __future__ import annotations
import json as _json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Optional

from pydantic import TypeAdapter

from safe_io import ndjson_append, FileLock
from schemas import ConfigLoadFailed, CateringQuoteSentLeadMissing


_LOG_PATH_DEFAULT = Path("/opt/shift-agent/logs/decisions.log")


def _append_best_effort(line: str, log_path: Path) -> None:
    """Append one NDJSON line under the conventional `<path>.lock` flock.
    Tolerates lock acquisition failure by appending without lock — the
    caller is already in a primary error path; a contended audit log is
    informational, not load-bearing.
    """
    lock_path = Path(str(log_path) + ".lock")
    try:
        with FileLock(lock_path):
            ndjson_append(log_path, line)
    except Exception:
        try:
            ndjson_append(log_path, line)
        except Exception:
            pass  # double-fault — give up


def log_config_load_failed_best_effort(
    config_path: Path,
    exc: BaseException,
    log_path: Path = _LOG_PATH_DEFAULT,
) -> None:
    """Append a config_load_failed row. NEVER raises.

    Always uses datetime.now(timezone.utc) — when config fails to load,
    the customer-tz source isn't available, so UTC is the only safe ts
    (design v2 §4.2 / M4).

    Test override: pass log_path=tmp_path / "decisions.log".
    """
    try:
        entry = ConfigLoadFailed(
            type="config_load_failed",
            ts=datetime.now(timezone.utc),
            path=str(config_path),
            error_class=type(exc).__name__,
            error_detail=str(exc)[:2000],
            script_name=(Path(sys.argv[0]).name if sys.argv and sys.argv[0]
                         else "<unknown>")[:80] or "<unknown>",
        )
        line = TypeAdapter(ConfigLoadFailed).dump_json(entry).decode("utf-8")
        _append_best_effort(line, log_path)
    except Exception:
        pass  # never let audit-emission shadow the primary error


def log_quote_sent_lead_missing_best_effort(
    lead_id: str,
    original_message_id: str,
    customer_phone_at_approve: str,
    outbound_message_id: str,
    detail: str = "",
    log_path: Path = _LOG_PATH_DEFAULT,
) -> None:
    """Best-effort emission of state-vs-outbound divergence audit row.
    Same swallow-all-errors contract as log_config_load_failed_best_effort.

    Caller (apply-catering-owner-decision post-bridge re-load) MUST also
    fire Pushover priority=2 — this audit row is the durable trace, but
    the high-priority alert is the operator-visibility surface.
    """
    try:
        entry = CateringQuoteSentLeadMissing(
            type="catering_quote_sent_lead_missing",
            ts=datetime.now(timezone.utc),
            lead_id=lead_id,
            original_message_id=original_message_id,
            customer_phone_at_approve=customer_phone_at_approve,
            outbound_message_id=outbound_message_id,
            detail=detail[:500],
        )
        line = TypeAdapter(CateringQuoteSentLeadMissing).dump_json(entry).decode("utf-8")
        _append_best_effort(line, log_path)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────
# Orphan-push detection (consolidated from extract-receipt + apply-expense-decision)
# ─────────────────────────────────────────────────────────────────

DEFAULT_ORPHAN_STALE_SECONDS = 60


def scan_orphan_pushes(
    leads: Iterable[Any],
    *,
    pending_status: str,
    completion_types: tuple[str, ...],
    id_attr: str,
    timestamp_attr: str,
    log_path: Path,
    stale_seconds: int = DEFAULT_ORPHAN_STALE_SECONDS,
    tail_lines: int = 500,
) -> tuple[list[Any], list[str]]:
    """Scan leads for "approved-but-no-completion-audit" orphans.

    Caller responsibilities:
      - hold the leads.json file lock for the whole call
      - persist mutated leads via atomic_write_json after this returns
      - emit ExpenseOrphanDetected (or equivalent) audit rows for the
        returned `flagged_leads` — caller knows which schema variant
        to emit; this helper stays schema-agnostic

    Algorithm:
      1. For each lead with status == pending_status AND not already flagged
         (lead.reconcile_required is False), check the timestamp at
         `lead.<timestamp_attr>` is older than stale_seconds.
      2. If stale (or timestamp missing — defensive), it's a candidate.
      3. Cross-check the audit log tail: any candidate whose <id_attr>
         appears in a completion entry (one of completion_types) is NOT
         orphaned (push completed; only the state-write to leads.json
         crashed — different recovery shape).
      4. For remaining candidates, mutate `lead.reconcile_required = True`
         and return them so the caller can audit + persist.

    Args:
        leads: iterable of lead objects (typically store.leads).
        pending_status: status string that gates which leads are candidates.
            Per current callers: "APPROVED_PENDING_PUSH".
        completion_types: tuple of audit-row `type` values that mark a
            push as completed. Per current callers: ("expense_pushed",
            "expense_push_failed").
        id_attr: attribute name on each lead for the audit-row ID match
            (e.g. "expense_id"). The audit row's "expense_id" field must
            match this attribute on the lead.
        timestamp_attr: attribute name on each lead for the age gate
            (e.g. "owner_approval_received_at"). Accepts datetime, str
            (ISO-8601), or None (defensive: treat as orphan).
        log_path: path to decisions.log (audit-log tail scan target).
        stale_seconds: minimum age before a pending lead is considered
            orphaned (avoids false-positives during legitimate in-flight
            pushes when the lock is briefly released).
        tail_lines: how many trailing audit-log lines to scan.

    Returns:
        (flagged_leads, flagged_ids): the flagged leads (for caller to
        emit audit rows in its preferred shape) and their IDs (for
        callers that want a list).
    """
    candidates: list[Any] = []
    candidate_ids: set[str] = set()
    now = datetime.now(timezone.utc)
    for lead in leads:
        status = getattr(lead, "status", None)
        if status != pending_status:
            continue
        if getattr(lead, "reconcile_required", False):
            continue
        ts = getattr(lead, timestamp_attr, None)
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                ts = None
        lead_id = getattr(lead, id_attr, None)
        if lead_id is None:
            # Can't track; skip silently.
            continue
        if ts is None:
            # No timestamp → can't gauge age; treat as orphan defensively.
            candidates.append(lead)
            candidate_ids.add(lead_id)
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (now - ts).total_seconds() < stale_seconds:
            continue  # legit in-flight push
        candidates.append(lead)
        candidate_ids.add(lead_id)

    if not candidate_ids:
        return [], []

    # Audit-log cross-check.
    completed: set[str] = set()
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-tail_lines:]
            for line in tail:
                # Cheap pre-filter to skip JSON parsing for non-completion entries.
                if not any(f'"{ct}"' in line for ct in completion_types):
                    continue
                try:
                    entry = _json.loads(line)
                except (_json.JSONDecodeError, ValueError):
                    continue
                eid = entry.get(id_attr)
                et = entry.get("type")
                if eid in candidate_ids and et in completion_types:
                    completed.add(eid)
        except OSError:
            pass

    flagged_leads: list[Any] = []
    flagged_ids: list[str] = []
    for lead in candidates:
        lead_id = getattr(lead, id_attr)
        if lead_id in completed:
            continue
        lead.reconcile_required = True
        flagged_leads.append(lead)
        flagged_ids.append(lead_id)
    return flagged_leads, flagged_ids
