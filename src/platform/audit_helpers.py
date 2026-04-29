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
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Optional

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
