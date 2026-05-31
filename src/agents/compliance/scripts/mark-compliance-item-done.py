#!/usr/bin/env python3
"""mark-compliance-item-done.py — Agent #13 owner-invoked state mutation.

Invoked by compliance_owner_query SKILL when owner says "mark <item> done".
Mutates state/compliance-items.json: advances renewal_date by recurrence_days
OR removes item entirely (one-shot, recurrence_days=0). Also prunes sentinel
keys for the item (deletion case clears all gates for that item_id).

Recovery: if state/compliance-items.json is missing, re-creates empty file +
emits InvariantViolation audit. Then exits 1 (item-not-found) so the SKILL
can reply gracefully.

CLI:
  --item-id <str>     required
  --actor <str>       owner|operator|system, default owner
  --dry-run           validate + log; no state mutation

Output (JSON to stdout):
  {"item_id": "...", "completed": "YYYY-MM-DD",
   "next": "YYYY-MM-DD"|null, "deleted": bool,
   "sentinel_keys_pruned": int}

Exit codes:
  0 — done
  1 — item not found / items file missing
  2 — bad input
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/opt/shift-agent")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "platform"))

import yaml  # noqa: E402

from safe_io import (  # noqa: E402
    FileLock, atomic_write_json, customer_now, load_model, assert_local_disk,
)
from schemas import (  # noqa: E402
    Config, ComplianceItemsFile, ComplianceLastSentFile,
    ComplianceItemMarkedDone, InvariantViolation,
)
from audit_helpers import _append_best_effort  # noqa: E402

CONFIG_PATH = Path(os.environ.get("SHIFT_AGENT_CONFIG_PATH", "/opt/shift-agent/config.yaml"))
ITEMS_PATH = Path(os.environ.get("SHIFT_AGENT_COMPLIANCE_ITEMS_PATH",
                                  "/opt/shift-agent/state/compliance-items.json"))
SENTINEL_PATH = Path(os.environ.get("SHIFT_AGENT_COMPLIANCE_SENTINEL_PATH",
                                     "/opt/shift-agent/state/compliance-last-sent.json"))
# Sentinel lock — MUST match the path used by check-compliance-deadlines.py
# (Reviewer 1 H1 fix: previously mark-done used items.json.lock for sentinel
# mutation while check-deadlines used compliance-check.json.lock; different
# locks meant last-writer-wins on sentinel race. Both scripts now share
# compliance-check.json.lock for sentinel I/O.)
SENTINEL_LOCK_PATH = Path(os.environ.get("SHIFT_AGENT_COMPLIANCE_LOCK_PATH",
                                          "/opt/shift-agent/state/compliance-check.json.lock"))
DECISIONS_LOG = Path(os.environ.get("SHIFT_AGENT_DECISIONS_LOG_PATH",
                                     "/opt/shift-agent/logs/decisions.log"))


def _emit_invariant(check_name: str, detail: str) -> None:
    entry = InvariantViolation(
        type="invariant_violation",
        ts=datetime.now(timezone.utc),
        check=check_name, detail=detail[:500],
    )
    _append_best_effort(entry.model_dump_json(), DECISIONS_LOG)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--item-id", required=True)
    ap.add_argument("--actor", default="owner",
                    choices=["owner", "operator", "system"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    assert_local_disk(ITEMS_PATH.parent)
    # HOTFIX 2026-05-04: yaml.safe_load not load_model (config.yaml is YAML
    # not JSON; load_model corrupts the file on parse failure).
    try:
        cfg_dict = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError as e:
        sys.stderr.write(f"config not found at {CONFIG_PATH}: {e}\n")
        return 2
    cfg = Config.model_validate(cfg_dict)
    items_lock = Path(str(ITEMS_PATH) + ".lock")

    with FileLock(items_lock):
        # Recovery: items file missing → recreate empty + emit invariant
        if not ITEMS_PATH.exists():
            ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(ITEMS_PATH, ComplianceItemsFile().model_dump(mode="json"))
            _emit_invariant(
                "compliance_items_file_recreated_on_mark_done",
                f"items file missing at {ITEMS_PATH}; recreated empty",
            )
            print(json.dumps({"error": "items_file_missing_recreated",
                              "item_id": args.item_id}))
            return 1

        f, _ = load_model(ITEMS_PATH, ComplianceItemsFile)
        match = next((i for i in f.items if i.id == args.item_id), None)
        if not match:
            print(json.dumps({"error": "item_not_found", "item_id": args.item_id}))
            return 1

        completed = match.renewal_date
        if match.recurrence_days == 0:
            f.items = [i for i in f.items if i.id != args.item_id]
            next_renewal = None
            deleted = True
        else:
            candidate = match.renewal_date + timedelta(days=match.recurrence_days)
            today = customer_now(cfg.customer.timezone).date()
            while candidate <= today:
                candidate = candidate + timedelta(days=match.recurrence_days)
            match.renewal_date = candidate
            next_renewal = match.renewal_date
            deleted = False

        if not args.dry_run:
            atomic_write_json(ITEMS_PATH, f.model_dump(mode="json"))

        # Sentinel GC: drop all <item_id>:* keys.
        # Reviewer 1 H1 + Reviewer 2 H1 fix: sentinel mutation uses the SAME
        # lock as check-compliance-deadlines.py (compliance-check.json.lock,
        # NOT items.json.lock); also re-construct ComplianceLastSentFile via
        # Pydantic before save so the model_validator re-runs against the
        # post-mutation dict (in-place dict mutation bypasses model_validator).
        sentinel_keys_pruned = 0
        if SENTINEL_PATH.exists() and not args.dry_run:
            try:
                with FileLock(SENTINEL_LOCK_PATH):
                    sentinel, _ = load_model(SENTINEL_PATH, ComplianceLastSentFile)
                    prefix = f"{args.item_id}:"
                    to_drop = [k for k in sentinel.last_sent if k.startswith(prefix)]
                    if to_drop:
                        new_last_sent = {
                            k: v for k, v in sentinel.last_sent.items()
                            if not k.startswith(prefix)
                        }
                        # Re-construct (NOT in-place mutate) so model_validator re-runs
                        new_sentinel = ComplianceLastSentFile(
                            schema_version=1, last_sent=new_last_sent,
                        )
                        atomic_write_json(SENTINEL_PATH, new_sentinel.model_dump(mode="json"))
                    sentinel_keys_pruned = len(to_drop)
            except Exception as e:
                _emit_invariant("compliance_sentinel_prune_failed",
                                f"could not prune sentinel for {args.item_id}: {e}")

    if not args.dry_run:
        entry = ComplianceItemMarkedDone(
            type="compliance_item_marked_done",
            ts=customer_now(cfg.customer.timezone),
            item_id=args.item_id,
            completed_renewal_date=completed,
            next_renewal_date=next_renewal,
            actor=args.actor,
            sentinel_keys_pruned=sentinel_keys_pruned,
        )
        _append_best_effort(entry.model_dump_json(), DECISIONS_LOG)

    print(json.dumps({
        "item_id": args.item_id,
        "completed": completed.isoformat(),
        "next": next_renewal.isoformat() if next_renewal else None,
        "deleted": deleted,
        "sentinel_keys_pruned": sentinel_keys_pruned,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
