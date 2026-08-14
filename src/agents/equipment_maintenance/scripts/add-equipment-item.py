#!/usr/bin/env python3
"""add-equipment-item.py — Agent #19 seed path for state/equipment-items.json.

WHY THIS EXISTS (Wave-1 2026-08-08): Agent #19 shipped as a self-declining v0.1
stub — one SKILL.md and no store. It was deployed via tools/skills-manifest.txt
and had no data to answer from: even once a caller could reach it, there was
nothing to read.

This script is the write half of the agent's first useful workflow. The read
half is the `get_equipment_maintenance_due` tool in src/plugins/shift-agent-read/,
which is reachable in production — the live gateway disables the `skills` and
`terminal` toolsets, so a dispatcher SKILL row is not (see
docs/runbooks/gateway-toolset-scoping.md).

Operator/owner tooling: no agent path calls this. Agent #19's first-workflow
authority tier stays READ.

Mirrors src/agents/compliance/scripts/add-compliance-item.py (same lock
discipline, same recovery behavior, same audit chokepoint, yaml.safe_load for
config — NOT load_model, which corrupts config.yaml on parse failure).

CLI:
  --id <str>                    required  ^[a-z0-9_]+$, <=80 chars, no ':'
  --name <str>                  required  owner-facing label
  --category <str>              required  refrigeration|cooking|pos|vehicle|
                                          hvac|fire_safety|other
  --next-service-date YYYY-MM-DD required
  --interval-days <int>         required  0 = one-shot
  --location-id / --vendor-name / --vendor-phone / --serial / --notes  optional
  --replace                     allow overwriting an existing id
  --dry-run                     validate + report; NO state mutation whatsoever

There is deliberately no `--actor` flag. This is an operator seed tool invoked
from a shell with no authenticated sender context, so the audit actor is
hardcoded `operator`. Letting argv assert `actor=owner` would let any shell
caller mint an audit row claiming the owner did something they did not — the
audit actor must describe proven provenance, not caller-supplied text.

Dry-run contract (enforced by tests): absent store stays absent; present store
stays byte-identical; decisions.log stays byte-identical. The only artifact
dry-run may leave is the advisory `.lock` file, which is a lock and not state.

Exit codes:
  0 — written (or would be, under --dry-run)
  1 — id exists and --replace not passed
  2 — bad input / unreadable config / store full
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
    Config, EquipmentItem, EquipmentItemsFile,
    EquipmentItemUpserted, InvariantViolation,
)
from audit_helpers import _append_best_effort  # noqa: E402

CONFIG_PATH = Path(os.environ.get("SHIFT_AGENT_CONFIG_PATH", "/opt/shift-agent/config.yaml"))
ITEMS_PATH = Path(os.environ.get("SHIFT_AGENT_EQUIPMENT_ITEMS_PATH",
                                  "/opt/shift-agent/state/equipment-items.json"))
DECISIONS_LOG = Path(os.environ.get("SHIFT_AGENT_DECISIONS_LOG_PATH",
                                     "/opt/shift-agent/logs/decisions.log"))

MAX_ITEMS = 200  # matches EquipmentItemsFile.items max_length

# Hardcoded: no authenticated sender context here. See the module docstring.
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
    ap = argparse.ArgumentParser(description="Seed a tracked asset (Agent #19)")
    ap.add_argument("--id", required=True, dest="equipment_id")
    ap.add_argument("--name", required=True)
    ap.add_argument("--category", required=True,
                    choices=["refrigeration", "cooking", "pos", "vehicle",
                             "hvac", "fire_safety", "other"])
    ap.add_argument("--next-service-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--interval-days", required=True, type=int,
                    help="0 = one-shot (no recurrence)")
    ap.add_argument("--location-id", default=None)
    ap.add_argument("--vendor-name", default=None)
    ap.add_argument("--vendor-phone", default=None)
    ap.add_argument("--serial", default=None)
    ap.add_argument("--notes", default=None)
    ap.add_argument("--replace", action="store_true")
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

    # Validate before taking the lock — malformed input never touches the store.
    try:
        item = EquipmentItem.model_validate({
            "id": args.equipment_id,
            "name": args.name,
            "category": args.category,
            "next_service_date": args.next_service_date,
            "interval_days": args.interval_days,
            "location_id": args.location_id,
            "vendor_name": args.vendor_name,
            "vendor_phone": args.vendor_phone,
            "serial": args.serial,
            "notes": args.notes,
        })
    except ValidationError as e:
        sys.stderr.write(f"invalid equipment item: {e}\n")
        return 2

    items_lock = Path(str(ITEMS_PATH) + ".lock")
    # The lock lives beside the store, so its directory must exist before
    # FileLock. A directory is not state; the store file itself is written
    # below, and only when this is not a dry run.
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(items_lock):
        if ITEMS_PATH.exists():
            try:
                f, _ = load_model(ITEMS_PATH, EquipmentItemsFile)
            except Exception as e:
                _emit_invariant("equipment_items_file_unreadable_on_add",
                                f"could not load {ITEMS_PATH}: {e}")
                sys.stderr.write(f"items file unreadable at {ITEMS_PATH}: {e}\n")
                return 2
        else:
            # First seed on a fresh box is expected, not an anomaly. Build the
            # container IN MEMORY: validation, duplicate detection and the cap
            # all run against this model, and it reaches disk only in the write
            # below. Materializing it here is what made --dry-run mutate state.
            f = EquipmentItemsFile()

        existing = next((i for i in f.items if i.id == item.id), None)
        if existing is not None and not args.replace:
            print(json.dumps({
                "error": "item_exists",
                "equipment_id": item.id,
                "next_service_date": existing.next_service_date.isoformat(),
                "hint": "pass --replace to overwrite",
            }))
            return 1

        previous = existing.next_service_date if existing else None
        replaced = existing is not None

        if replaced:
            f.items = [i for i in f.items if i.id != item.id]
        elif len(f.items) >= MAX_ITEMS:
            sys.stderr.write(
                f"equipment-items.json already holds {len(f.items)} items "
                f"(cap {MAX_ITEMS})\n"
            )
            return 2
        f.items.append(item)

        if not args.dry_run:
            atomic_write_json(ITEMS_PATH, f.model_dump(mode="json"))

        n_items = len(f.items)

    if not args.dry_run:
        entry = EquipmentItemUpserted(
            type="equipment_item_upserted",
            ts=_customer_now(cfg.customer.timezone),
            equipment_id=item.id,
            next_service_date=item.next_service_date,
            interval_days=item.interval_days,
            category=item.category,
            actor=AUDIT_ACTOR,
            replaced=replaced,
            previous_next_service_date=previous,
        )
        _append_best_effort(entry.model_dump_json(), DECISIONS_LOG)

    print(json.dumps({
        "equipment_id": item.id,
        "next_service_date": item.next_service_date.isoformat(),
        "replaced": replaced,
        "previous_next_service_date": previous.isoformat() if previous else None,
        "n_items": n_items,
        "dry_run": args.dry_run,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
