#!/usr/bin/env python3
"""prune-and-expire-expenses — daily cron for Agent #21.

Two responsibilities, both keyed off cfg.expense_bookkeeper:
  1. Expire stale leads — AWAITING_OWNER_APPROVAL leads older than
     proposal_ttl_hours transition to EXPIRED.
  2. Prune receipt JPEGs — terminal-status leads (PUSHED/REVERSED/REJECTED/
     EXPIRED) whose receipt JPEG is older than receipt_retention_days
     get the JPEG deleted; lead metadata (vendor / total) preserved in audit.

Idempotent. Safe to run hourly. Wired up via
src/agents/expense_bookkeeper/systemd/prune-expense-receipts.timer (daily).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, "/opt/shift-agent")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "platform"))

from safe_io import (  # noqa: E402
    flock, ndjson_append, atomic_write_json, load_model, load_yaml_model,
    notify_owner_with_fallback,
)
from schemas import (  # noqa: E402
    Config, ExpenseLeadStore, EXPENSE_RETENTION_CANDIDATES,
    ExpenseLeadStatusChange, ExpenseReceiptPruned,
)
from pydantic import ValidationError  # noqa: E402


CONFIG_PATH = Path("/opt/shift-agent/config.yaml")
LEADS_PATH = Path("/opt/shift-agent/state/expense-bookkeeper/leads.json")
RECEIPTS_DIR = Path("/opt/shift-agent/state/expense-bookkeeper/receipts")
LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")


def _log(entry):
    ndjson_append(LOG_PATH, entry.model_dump_json())


def _notify_owner_expired(expense_id: str, vendor: str | None, total_cents: int | None) -> None:
    """§12b: an expense the owner was asked to approve was auto-expired — tell them.

    Auto-expiry silently reverses an owner-pending state (the owner was asked to approve),
    so the operator's mental model is wrong unless we alert at the write site. Best-effort:
    notify_owner_with_fallback never raises for delivery failure (it degrades to
    notify-failed.log / stderr), so a Pushover outage can't fail the prune. The
    dispatched/delivered stderr pair makes every fire traceable in journald regardless of
    delivery outcome (mirrors shift-agent-proposal-sweep's §12b logging).
    """
    amount = f"${(total_cents or 0) / 100:.2f}"
    who = vendor or "an expense"
    msg = (
        f"An expense you were asked to approve ({expense_id}, {who} {amount}) expired "
        f"unactioned and was auto-closed. If it's still valid, re-submit the receipt."
    )
    sys.stderr.write(f"expense_expiry_alert_dispatched expense={expense_id}\n")
    delivered = notify_owner_with_fallback(
        "Expense expired", msg, source="prune-and-expire-expenses",
    )
    sys.stderr.write(
        f"expense_expiry_alert_{'delivered' if delivered else 'delivery_failed'} "
        f"expense={expense_id}\n"
    )


def main():
    ap = argparse.ArgumentParser(prog="prune-and-expire-expenses")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config-load and exit; emit SMOKE_OK marker on success "
             "or SMOKE_FAIL: <reason> on failure. Used by smoke-test step 12.",
    )
    args = ap.parse_args()

    # config.yaml is YAML, NOT JSON. PR #34 added load_yaml_model so this
    # path no longer rename-quarantines customer config every timer fire.
    try:
        cfg = load_yaml_model(CONFIG_PATH, Config)
    except (FileNotFoundError, RuntimeError, ValidationError) as e:
        if args.dry_run:
            print(f"SMOKE_FAIL: {type(e).__name__}: {e}")
        sys.stderr.write(f"config load failed: {e}\n")
        return 1
    if args.dry_run:
        # --dry-run validates config-load only; does NOT exercise the prune
        # loop body. A future regression in the prune logic itself won't be
        # caught here. PR-C R2 finding accepted: scope is config-load-only.
        print("SMOKE_OK")
        return 0
    if not cfg.expense_bookkeeper.enabled:
        return 0  # silent no-op

    now = datetime.now(timezone.utc)
    ttl_hours = cfg.expense_bookkeeper.proposal_ttl_hours
    retention_days = cfg.expense_bookkeeper.receipt_retention_days
    expired_count = 0
    pruned_count = 0
    # §12b (BL-OBS-03): AWAITING_OWNER_APPROVAL leads that auto-expire this run —
    # (expense_id, vendor, total_cents). Owner is alerted AFTER the lock releases
    # (notify subprocesses + may hit the network; never hold LEADS_LOCK across it).
    expired_awaiting: list[tuple[str, str | None, int | None]] = []

    if not LEADS_PATH.exists():
        return 0

    with flock(LEADS_PATH):
        store, st = load_model(LEADS_PATH, ExpenseLeadStore,
                               default=ExpenseLeadStore())
        if st not in ("ok", "missing", "empty"):
            sys.stderr.write(f"leads store status={st}\n")
            return 2

        for lead in store.leads:
            # Expire stale AWAITING leads
            if lead.status == "AWAITING_OWNER_APPROVAL":
                received = lead.received_at
                if isinstance(received, str):
                    received = datetime.fromisoformat(received)
                if received.tzinfo is None:
                    received = received.replace(tzinfo=timezone.utc)
                age_hours = (now - received).total_seconds() / 3600.0
                if age_hours > ttl_hours:
                    old = lead.status
                    lead.status = "EXPIRED"
                    _log(ExpenseLeadStatusChange(
                        ts=now, type="expense_lead_status_change",
                        expense_id=lead.expense_id,
                        from_status=old, to_status="EXPIRED",
                        reason=f"proposal_ttl_hours={ttl_hours} exceeded",
                    ))
                    expired_count += 1
                    expired_awaiting.append((
                        lead.expense_id,
                        lead.extraction.vendor_normalized if lead.extraction else None,
                        lead.extracted_total_cents,
                    ))

            # Prune receipt JPEG for retention-candidate leads (PUSHED + true terminals)
            if lead.status in EXPENSE_RETENTION_CANDIDATES and lead.image_path:
                img_path = Path(lead.image_path)
                if img_path.exists():
                    try:
                        mtime = datetime.fromtimestamp(img_path.stat().st_mtime,
                                                       tz=timezone.utc)
                    except OSError:
                        continue
                    age_days = (now - mtime).total_seconds() / 86400.0
                    if age_days > retention_days:
                        try:
                            img_path.unlink()
                        except OSError as e:
                            sys.stderr.write(f"unlink {img_path} failed: {e}\n")
                            continue
                        _log(ExpenseReceiptPruned(
                            ts=now, type="expense_receipt_pruned",
                            expense_id=lead.expense_id,
                            vendor_normalized=(
                                lead.extraction.vendor_normalized
                                if lead.extraction else None
                            ),
                            extracted_total_cents=lead.extracted_total_cents,
                            reason="retention_expired",
                        ))
                        pruned_count += 1

        atomic_write_json(LEADS_PATH, store.model_dump(mode="json"))

    # §12b owner alerts — outside the leads lock, best-effort, one per auto-expired
    # AWAITING lead. A retention-prune of an already-terminal lead is NOT alerted (the
    # owner already saw those resolve); only the operator-pending reversal is.
    for expense_id, vendor, total_cents in expired_awaiting:
        _notify_owner_expired(expense_id, vendor, total_cents)

    print(json.dumps({"expired": expired_count, "pruned": pruned_count}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
