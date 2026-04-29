#!/usr/bin/env python3
"""v0.3 catering state migration tool.

Run BEFORE deploying the v0.3 schema. Backfills:
1. Phone canonicalization: bare-10-digit-with-plus → +1XXXXXXXXXX
2. quote_text for legacy AWAITING/SENT leads (uses sentinel; apply-script
   re-renders proper quote on next approve flow)
3. Idempotent: safe to run twice

Always writes a .bak alongside the original BEFORE mutating.

Usage:
  python tools/catering-state-migrate.py \
      --leads-path /opt/shift-agent/state/catering-leads.json \
      [--dry-run]

Exit codes:
  0 — migration applied (or dry-run completed)
  1 — input file unreadable / malformed
  2 — migration failed mid-write (state may be inconsistent — restore from .bak)
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import re
import shutil
import sys
import time
from typing import Any

# Phone shape constants
_BARE_10_WITH_PLUS = re.compile(r"^\+\d{10}$")  # e.g. +9045551234 (the historical bug)
_VALID_E164 = re.compile(r"^\+\d{10,15}$")

# Sentinel value used by the v0.3 schema's mode="before" backfill.
_LEGACY_QUOTE_SENTINEL = "<legacy-pre-v0.3-no-quote-persisted>"

POST_AWAITING_STATUSES = {
    "AWAITING_OWNER_APPROVAL",
    "OWNER_APPROVED",
    "OWNER_EDITED",
    "SENT_TO_CUSTOMER",
}


def _migrate_lead(lead: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Migrate a single lead in-place. Returns (modified_lead, change_log)."""
    changes: list[str] = []

    # 1. Phone canonicalization (idempotent — only triggers on bare-10-digit-with-plus)
    phone = lead.get("customer_phone", "")
    if isinstance(phone, str) and _BARE_10_WITH_PLUS.match(phone):
        # Convert +9045551234 → +19045551234
        new_phone = "+1" + phone[1:]
        lead["customer_phone"] = new_phone
        changes.append(f"phone: {phone} → {new_phone}")

    # 2. quote_text backfill for post-AWAITING legacy leads (idempotent — only
    #    fills empty; re-runs leave existing sentinel/real text alone)
    status = lead.get("status")
    quote = (lead.get("quote_text") or "").strip()
    if status in POST_AWAITING_STATUSES and not quote:
        lead["quote_text"] = _LEGACY_QUOTE_SENTINEL
        changes.append(f"quote_text backfilled with sentinel (status={status})")

    return lead, changes


def main() -> int:
    ap = argparse.ArgumentParser(description="Catering state migration v0.3")
    ap.add_argument("--leads-path", required=True, help="Path to catering-leads.json")
    ap.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = ap.parse_args()

    leads_path = pathlib.Path(args.leads_path)
    if not leads_path.exists():
        print(f"NOTE: {leads_path} does not exist — no migration needed", file=sys.stderr)
        return 0

    try:
        raw = json.loads(leads_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: cannot read {leads_path}: {e}", file=sys.stderr)
        return 1

    if not isinstance(raw, dict) or "leads" not in raw or not isinstance(raw["leads"], list):
        print(f"ERROR: {leads_path} not a CateringLeadStore (missing 'leads' list)", file=sys.stderr)
        return 1

    total_changes: list[str] = []
    for lead in raw["leads"]:
        if not isinstance(lead, dict):
            continue
        lead_id = lead.get("lead_id", "?")
        _, changes = _migrate_lead(lead)
        for c in changes:
            total_changes.append(f"  {lead_id}: {c}")

    # Tag schema_version (idempotent — only set if not present)
    if "schema_version" not in raw:
        raw["schema_version"] = 1
        total_changes.append("  store: schema_version=1 added")

    if not total_changes:
        print(f"OK: {leads_path} already at v0.3 — no changes needed")
        return 0

    print("Migration changes:")
    for c in total_changes:
        print(c)

    if args.dry_run:
        print("--dry-run: not writing")
        return 0

    # Backup BEFORE mutating
    ts = int(time.time())
    backup_path = leads_path.with_suffix(f".json.pre-migrate-{ts}.bak")
    try:
        shutil.copy2(leads_path, backup_path)
        print(f"backup: {backup_path}")
    except OSError as e:
        print(f"ERROR: backup failed: {e}", file=sys.stderr)
        return 2

    # Atomic write via temp + rename
    tmp_path = leads_path.with_suffix(f".json.tmp-{ts}")
    try:
        tmp_path.write_text(
            json.dumps(raw, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, leads_path)
    except OSError as e:
        print(f"ERROR: write failed: {e}", file=sys.stderr)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        return 2

    print(f"OK: migrated {leads_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
