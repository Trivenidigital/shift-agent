#!/usr/bin/env python3
"""add-compliance-item.py — Agent #13 seed path for state/compliance-items.json.

WHY THIS EXISTS (Wave-1 2026-08-08): `ComplianceItemsFile` shipped documented as
"operator-seeded + mark-done-mutated", but nothing in the repo could do the
seeding. `check-compliance-deadlines.py` and `compliance_owner_query` both read
the file; `mark-compliance-item-done.py` mutates it. There was no writer for the
first row, so every deployment answered the owner's "what's coming up?" from an
empty list — an answer that looks authoritative and is merely uninformed.

This is operator/owner tooling, not an agent authority: the SKILL never calls it.
Agent #13's first workflow stays READ (owner asks, agent lists).

Mirrors mark-compliance-item-done.py exactly: same lock file, same recovery
behavior, same audit chokepoint, same yaml.safe_load config path (NOT load_model
— config.yaml is YAML; load_model corrupts it on parse failure).

CLI:
  --id <str>                required  ^[a-z0-9_]+$, <=40 chars (no ':' — the
                                      sentinel key format is '<id>:<gate>')
  --name <str>              required  owner-facing label
  --category <str>          required  license_renewal|tax_filing|inspection|
                                      certification|insurance|other
  --renewal-date YYYY-MM-DD required  the NEXT occurrence
  --recurrence-days <int>   required  0 = one-shot (deleted on mark-done)
  --location-id <str>       optional
  --agency <str>            optional
  --resource-url <url>      optional
  --notes <str>             optional
  --replace                 allow overwriting an existing id
  --dry-run                 validate + report; NO state mutation whatsoever

There is deliberately no `--actor` flag. This is an operator seed tool invoked
from a shell with no authenticated sender context, so the audit actor is
hardcoded `operator`. Letting argv assert `actor=owner` would let any shell
caller mint an audit row claiming the owner did something they did not — the
audit actor must describe proven provenance, not caller-supplied text. If an
authenticated owner-facing mutation path is added later, that path may supply
`owner` from verified sender context; this one may not.

Output (JSON to stdout):
  {"item_id": "...", "renewal_date": "YYYY-MM-DD", "replaced": bool,
   "previous_renewal_date": "YYYY-MM-DD"|null, "n_items": int, "dry_run": bool}

Dry-run contract (enforced by tests): if the store was absent it stays absent;
if it was present it stays byte-identical; decisions.log stays byte-identical.
The only artifact dry-run may leave is the advisory `.lock` file, which is a
lock and not state.

Exit codes:
  0 — item written (or would be, under --dry-run)
  1 — id already exists and --replace was not passed
  2 — bad input (schema violation, missing/malformed/invalid config, store full)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/opt/shift-agent")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "platform"))

import yaml  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from safe_io import (  # noqa: E402
    FileLock, atomic_write_json, customer_now, load_model, assert_local_disk,
)
from schemas import (  # noqa: E402
    Config, ComplianceItem, ComplianceItemsFile,
    ComplianceItemUpserted, InvariantViolation,
)
from audit_helpers import _append_best_effort  # noqa: E402

CONFIG_PATH = Path(os.environ.get("SHIFT_AGENT_CONFIG_PATH", "/opt/shift-agent/config.yaml"))
ITEMS_PATH = Path(os.environ.get("SHIFT_AGENT_COMPLIANCE_ITEMS_PATH",
                                  "/opt/shift-agent/state/compliance-items.json"))
DECISIONS_LOG = Path(os.environ.get("SHIFT_AGENT_DECISIONS_LOG_PATH",
                                     "/opt/shift-agent/logs/decisions.log"))

# ComplianceItemsFile caps `items` at 200. Checked before append so the operator
# gets a clear message instead of a raw pydantic max_length error.
MAX_ITEMS = 200

# Hardcoded: this tool has no authenticated sender context. See the module
# docstring — argv must not be able to assert owner provenance.
AUDIT_ACTOR = "operator"


def _customer_now(tz_name: str) -> datetime:
    """Tz-aware datetime in customer tz, with SHIFT_AGENT_NOW_OVERRIDE for tests."""
    override = os.environ.get("SHIFT_AGENT_NOW_OVERRIDE", "")
    if override:
        return datetime.fromisoformat(override)
    return customer_now(tz_name)


def _emit_invariant(check_name: str, detail: str) -> None:
    entry = InvariantViolation(
        type="invariant_violation",
        ts=datetime.now(timezone.utc),
        check=check_name, detail=detail[:500],
    )
    _append_best_effort(entry.model_dump_json(), DECISIONS_LOG)


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed a compliance deadline (Agent #13)")
    ap.add_argument("--id", required=True, dest="item_id")
    ap.add_argument("--name", required=True)
    ap.add_argument("--category", required=True,
                    choices=["license_renewal", "tax_filing", "inspection",
                             "certification", "insurance", "other"])
    ap.add_argument("--renewal-date", required=True,
                    help="YYYY-MM-DD — the NEXT occurrence")
    ap.add_argument("--recurrence-days", required=True, type=int,
                    help="0 = one-shot (deleted on mark-done)")
    ap.add_argument("--location-id", default=None)
    ap.add_argument("--agency", default=None)
    ap.add_argument("--resource-url", default=None)
    ap.add_argument("--notes", default=None)
    ap.add_argument("--replace", action="store_true",
                    help="overwrite an existing item with this id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    assert_local_disk(ITEMS_PATH.parent)
    # Missing, unparseable and schema-invalid config all exit 2 cleanly. An
    # uncaught ValidationError traceback is not a usable operator message.
    try:
        cfg_dict = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError as e:
        sys.stderr.write(f"config not found at {CONFIG_PATH}: {e}\n")
        return 2
    except yaml.YAMLError as e:
        sys.stderr.write(f"config at {CONFIG_PATH} is not valid YAML: {e}\n")
        return 2
    try:
        cfg = Config.model_validate(cfg_dict)
    except ValidationError as e:
        sys.stderr.write(f"config at {CONFIG_PATH} failed schema validation: {e}\n")
        return 2

    # Build + validate the item BEFORE taking the lock — a malformed id or date
    # should never hold the items lock or touch the file.
    try:
        item = ComplianceItem.model_validate({
            "id": args.item_id,
            "name": args.name,
            "category": args.category,
            "renewal_date": args.renewal_date,
            "recurrence_days": args.recurrence_days,
            "location_id": args.location_id,
            "agency": args.agency,
            "resource_url": args.resource_url,
            "notes": args.notes,
        })
    except ValidationError as e:
        sys.stderr.write(f"invalid compliance item: {e}\n")
        return 2

    items_lock = Path(str(ITEMS_PATH) + ".lock")
    # The lock lives beside the store, so its directory must exist before
    # FileLock. Creating a directory is not creating state; the store file
    # itself is still only written below, and only when this is not a dry run.
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(items_lock):
        if ITEMS_PATH.exists():
            try:
                f, _ = load_model(ITEMS_PATH, ComplianceItemsFile)
            except Exception as e:
                _emit_invariant("compliance_items_file_unreadable_on_add",
                                f"could not load {ITEMS_PATH}: {e}")
                sys.stderr.write(f"items file unreadable at {ITEMS_PATH}: {e}\n")
                return 2
        else:
            # First seed on a fresh box is the expected path, not an anomaly.
            # Build the container IN MEMORY: validation, duplicate detection and
            # the cap all run against this model, and it reaches disk only in the
            # write below. Materializing it here is what made --dry-run mutate
            # state on a fresh box.
            f = ComplianceItemsFile()

        existing = next((i for i in f.items if i.id == item.id), None)
        if existing is not None and not args.replace:
            print(json.dumps({
                "error": "item_exists",
                "item_id": item.id,
                "renewal_date": existing.renewal_date.isoformat(),
                "hint": "pass --replace to overwrite",
            }))
            return 1

        previous_renewal_date = existing.renewal_date if existing else None
        replaced = existing is not None

        if replaced:
            f.items = [i for i in f.items if i.id != item.id]
        elif len(f.items) >= MAX_ITEMS:
            sys.stderr.write(
                f"compliance-items.json already holds {len(f.items)} items "
                f"(cap {MAX_ITEMS}); mark stale items done before adding more\n"
            )
            return 2
        f.items.append(item)

        if not args.dry_run:
            atomic_write_json(ITEMS_PATH, f.model_dump(mode="json"))

        n_items = len(f.items)

    if not args.dry_run:
        entry = ComplianceItemUpserted(
            type="compliance_item_upserted",
            ts=_customer_now(cfg.customer.timezone),
            item_id=item.id,
            renewal_date=item.renewal_date,
            recurrence_days=item.recurrence_days,
            category=item.category,
            actor=AUDIT_ACTOR,
            replaced=replaced,
            previous_renewal_date=previous_renewal_date,
        )
        _append_best_effort(entry.model_dump_json(), DECISIONS_LOG)

    print(json.dumps({
        "item_id": item.id,
        "renewal_date": item.renewal_date.isoformat(),
        "replaced": replaced,
        "previous_renewal_date": (
            previous_renewal_date.isoformat() if previous_renewal_date else None
        ),
        "n_items": n_items,
        "dry_run": args.dry_run,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
