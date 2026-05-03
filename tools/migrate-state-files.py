#!/usr/bin/env python3
"""migrate-state-files — bring legacy state files up to current Pydantic schema.

PR-CF5 (2026-05-03). Idempotent: running on already-current files is a no-op.
Fail-loud: unknown shapes exit 2 with audit row; load failures other than
extra_forbidden also exit 2 (do not auto-migrate corrupt-but-current files).

Per-file flow (under safe_io.flock(file)):
  1. Read current content (json_decode_failed audit on parse error).
  2. Compute key-set; compare to expected_current_keys for the file.
  3. If keys exactly match expected → final-defense Pydantic validate. If
     pass → no-op. If fail → load_failed_non_extra audit + exit 2 (the
     keys are right but values are wrong types — operator must triage).
  4. Otherwise (keys differ) → ALWAYS call migrator (which decides:
     legacy → new dict, intermediate → new dict, unknown → raise).
  5. Validate migrator output via model_cls.model_validate.
  6. Backup + atomic_write + audit.

This dispatch logic (key-set pre-check before model_validate) avoids a
v1-design trap where SeenIds legacy shapes (which have all-default fields)
silently pass model_validate and bypass the migrator, AND where SendCounter
{date, sent_count} fails with `missing` errors (not `extra_forbidden`)
and incorrectly routes to load_failed_non_extra.

Override: STATE_MIGRATION_OVERRIDE=skip + STATE_MIGRATION_OVERRIDE_REASON='...'
  Skips ALL migrations, audits the skip via StateFileMigrationOverridden,
  exits 0. Mirrors HERMES_PIN_OVERRIDE attestation pattern (re-type value +
  required REASON env + audit row).

CLI:
  --check                  : dry-run; exit 0 clean, 1 if migrations needed, 2 unknown
  --apply                  : do migrations
  --apply --file <path>    : migrate one file only

Exit codes:
  0  : clean (no migrations needed) OR all migrations succeeded OR override-skipped
  1  : migrations needed (--check only) OR malformed override (HERMES_PIN_OVERRIDE convention)
  2  : unknown shape OR non-extra load failure OR JSON decode failure
  3  : write failed (backup or atomic_write step)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

# Path resolution: tools/migrate-state-files.py needs platform/ on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, "/opt/shift-agent")
sys.path.insert(0, str(_REPO_ROOT / "src" / "platform"))

from safe_io import (  # noqa: E402
    flock, atomic_write_json, ndjson_append, load_yaml_model, assert_local_disk,
)
from schemas import (  # noqa: E402
    Config, SendCounter, SeenIds,
    StateFileMigrated, StateFileMigrationFailed, StateFileMigrationOverridden,
)
from pydantic import ValidationError  # noqa: E402

# === Constants ===
STATE_DIR = Path("/opt/shift-agent/state")
LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")
SEND_COUNTER_PATH = STATE_DIR / "send-counter.json"
SEEN_IDS_PATH = STATE_DIR / "seen-ids.json"
CONFIG_PATH = Path("/opt/shift-agent/config.yaml")

EXIT_OK = 0
EXIT_MIGRATIONS_NEEDED = 1   # also: malformed override (mirrors HERMES_PIN_OVERRIDE convention)
EXIT_UNKNOWN_SHAPE = 2
EXIT_WRITE_FAILED = 3


class UnknownStateShapeError(Exception):
    """Raised when a file's shape matches neither current nor known-legacy."""


# === Per-file migrators ===

def migrate_send_counter(legacy: dict, *, customer_tz_resolved: Optional[str]) -> Optional[dict]:
    """Legacy {date, sent_count} OR intermediate {day, count, sent_count} → current {day, count, last_send_ts}.

    Returns None if already current (no-op — defensive; dispatch layer
    pre-checks key-set so this should be unreachable).
    Raises UnknownStateShapeError if shape unrecognized.
    """
    keys = set(legacy.keys())

    # Defensive no-op: dispatch layer already pre-checks key-set match.
    # Reached only if caller bypasses _migrate_one_file (e.g., direct unit test).
    if keys == {"day", "count", "last_send_ts"} or keys == {"day", "count"}:
        return None

    # Intermediate: post-partial-migration {day, count, sent_count} — strip extra
    if keys == {"day", "count", "sent_count"} or keys == {"day", "count", "sent_count", "last_send_ts"}:
        return {
            "day": legacy["day"],
            "count": legacy["count"],
            "last_send_ts": legacy.get("last_send_ts"),
        }

    # Legacy: {date, sent_count} → fully migrate
    if keys <= {"date", "sent_count"}:
        return {
            "day": legacy.get("date") or _resolve_today_with_fallback(customer_tz_resolved),
            "count": legacy.get("sent_count", 0),
            "last_send_ts": None,
        }

    raise UnknownStateShapeError(
        f"send-counter.json has unrecognized keys: {sorted(keys)}"
    )


def migrate_seen_ids(legacy: dict, **_kwargs) -> Optional[dict]:
    """Legacy {} OR partial subset → current 4-field shape.

    NOTE: When migrating from `{}` or 2-field `{seen_message_ids, max_size}`,
    `last_offset_bytes` and `agent_log_inode` are zeroed. This means the
    tail-logger starts a fresh scan from EOF on next start (matches
    `_fresh_seen_ids_at_eof()` behavior). Correct for empty-dict legacy
    file (no historic state to preserve). Documented in audit row 'detail'.

    Returns None if already current (defensive; unreachable via dispatch).
    Raises UnknownStateShapeError if shape unrecognized.
    """
    keys = set(legacy.keys())
    current_keys = {"seen_message_ids", "max_size", "last_offset_bytes", "agent_log_inode"}

    # Defensive no-op
    if keys == current_keys:
        return None

    # Migrate from any subset of current keys (or empty dict)
    if keys <= current_keys:
        return {
            "seen_message_ids": legacy.get("seen_message_ids", []),
            "max_size": legacy.get("max_size", 10000),
            "last_offset_bytes": legacy.get("last_offset_bytes", 0),
            "agent_log_inode": legacy.get("agent_log_inode", 0),
        }

    raise UnknownStateShapeError(
        f"seen-ids.json has unrecognized keys: {sorted(keys)}"
    )


# === Helpers ===

def _resolve_customer_tz_best_effort() -> Optional[str]:
    """Try to load config.yaml + extract customer.timezone.
    Returns None on any failure (config missing/corrupt/invalid).
    """
    try:
        cfg = load_yaml_model(CONFIG_PATH, Config)
        return cfg.customer.timezone
    except Exception:
        return None


def _resolve_today_with_fallback(customer_tz: Optional[str]) -> str:
    """Resolve current date in customer tz, with UTC fallback if tz invalid."""
    if not customer_tz:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(customer_tz)).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


# === Migrator registry ===

MIGRATORS: dict[Path, tuple[type, Callable]] = {
    SEND_COUNTER_PATH: (SendCounter, migrate_send_counter),
    SEEN_IDS_PATH: (SeenIds, migrate_seen_ids),
}

# Per-file expected-current key-set for dispatch pre-check. Must match the
# Pydantic model fields exactly (including required + optional with defaults).
MIGRATOR_EXPECTED_KEYS: dict[Path, set[str]] = {
    SEND_COUNTER_PATH: {"day", "count", "last_send_ts"},
    SEEN_IDS_PATH: {"seen_message_ids", "max_size", "last_offset_bytes", "agent_log_inode"},
}


# === Audit emission ===

def _audit_migrated(file: Path, from_shape: list[str], to_shape: list[str], backup_path: Path) -> None:
    """Best-effort audit emission. Failures logged to stderr."""
    try:
        # model_dump_json() (no indent) is required because ndjson_append
        # prohibits embedded newlines.
        ndjson_append(LOG_PATH, StateFileMigrated(
            type="state_file_migrated",
            ts=datetime.now(timezone.utc),
            file=str(file),
            from_shape=json.dumps(sorted(from_shape)),
            to_shape=json.dumps(sorted(to_shape)),
            backup_path=str(backup_path),
        ).model_dump_json())
    except Exception as e:
        sys.stderr.write(f"WARN: audit emit (state_file_migrated) failed: {e}\n")


def _audit_failed(file: Path, reason: str, detail: str) -> None:
    """Best-effort audit emission. Failures logged to stderr."""
    try:
        ndjson_append(LOG_PATH, StateFileMigrationFailed(
            type="state_file_migration_failed",
            ts=datetime.now(timezone.utc),
            file=str(file),
            reason=reason,
            detail=detail[:2000],
        ).model_dump_json())
    except Exception as e:
        sys.stderr.write(f"WARN: audit emit (state_file_migration_failed) failed: {e}\n")


def _backup_path_for(file: Path) -> Path:
    """<file>.pre-migrate-<epoch> — matches safe_load_json corrupt-quarantine
    convention. Uses path.with_name to avoid the with_suffix dotted-name pitfall.
    Single-PID per-deploy makes PID suffix unnecessary for collision avoidance.
    """
    return file.with_name(f"{file.name}.pre-migrate-{int(time.time())}")


# === Per-file migration (dispatch layer) ===

def _migrate_one_file(
    file: Path, *, model_cls: type, migrator: Callable,
    customer_tz_resolved: Optional[str], dry_run: bool,
) -> tuple[bool, str]:
    """Migrate a single file under flock.

    Returns (needed_migration: bool, status_msg: str).
    Raises UnknownStateShapeError on unknown shape (caller decides exit code).

    Dispatch logic (PR-CF5 design v2): key-set pre-check BEFORE model_validate.
    Avoids the trap where Pydantic-with-defaults silently passes legacy shapes
    through without invoking the migrator.
    """
    if not file.exists():
        return (False, f"{file.name}: not present (skip)")

    with flock(file):  # safe_io.flock acquires <file>.lock sibling — same target writers use
        try:
            raw = file.read_text(encoding="utf-8")
        except OSError as e:
            _audit_failed(file, "json_decode_failed", f"read failed: {e}")
            raise UnknownStateShapeError(f"{file.name}: read failed: {e}")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            _audit_failed(file, "json_decode_failed", f"json.JSONDecodeError: {e}")
            raise UnknownStateShapeError(f"{file.name}: invalid JSON: {e}")

        if not isinstance(data, dict):
            _audit_failed(file, "unknown_shape", f"top-level is {type(data).__name__}, not dict")
            raise UnknownStateShapeError(f"{file.name}: top-level not a dict")

        # Key-set pre-check: bypasses Pydantic-with-defaults trap
        from_keys = set(data.keys())
        expected_current_keys = MIGRATOR_EXPECTED_KEYS[file]
        if from_keys == expected_current_keys:
            # Final defense: validate against schema to catch type errors
            # (e.g., {day: 123, count: "x", last_send_ts: null})
            try:
                model_cls.model_validate(data)
                return (False, f"{file.name}: already current (no-op)")
            except ValidationError as ve:
                _audit_failed(
                    file, "load_failed_non_extra",
                    f"keys current but ValidationError: {ve.errors()[:3]}"
                )
                raise UnknownStateShapeError(
                    f"{file.name}: keys match but validation failed: {ve.errors()[:1]}"
                )

        # Keys differ from current → migrator decides legacy vs intermediate vs unknown
        try:
            new_data = migrator(data, customer_tz_resolved=customer_tz_resolved)
        except UnknownStateShapeError as e:
            _audit_failed(file, "unknown_shape", str(e))
            raise

        if new_data is None:
            # Defensive: shouldn't happen given key-set pre-check
            return (False, f"{file.name}: migrator returned no-op despite key-set diff (unexpected)")

        # Validate the new shape against current schema
        try:
            model_cls.model_validate(new_data)
        except ValidationError as ve:
            _audit_failed(
                file, "write_failed",
                f"migrator output failed validation: {ve.errors()[:1]}"
            )
            raise UnknownStateShapeError(
                f"{file.name}: migrator produced invalid shape: {ve.errors()[:1]}"
            )

        if dry_run:
            return (True, f"{file.name}: WOULD migrate from {sorted(from_keys)} -> {sorted(new_data.keys())}")

        # Backup + write
        backup = _backup_path_for(file)
        try:
            backup.write_text(raw, encoding="utf-8")
        except OSError as e:
            _audit_failed(file, "backup_failed", str(e))
            raise

        try:
            atomic_write_json(file, model_cls.model_validate(new_data))
        except Exception as e:
            _audit_failed(file, "write_failed", str(e))
            raise

        _audit_migrated(file, sorted(from_keys), sorted(new_data.keys()), backup)
        return (True, f"{file.name}: migrated {sorted(from_keys)} -> {sorted(new_data.keys())} (backup={backup.name})")


# === Main ===

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="dry-run")
    g.add_argument("--apply", action="store_true", help="do migrations")
    parser.add_argument(
        "--file", default=None,
        help="if --apply, only migrate the named file (path or basename)",
    )
    args = parser.parse_args(argv)

    # Override gate (mirrors HERMES_PIN_OVERRIDE attestation pattern;
    # malformed override → exit 1 matching check-shift-agent-patch.sh fail() convention)
    override = os.environ.get("STATE_MIGRATION_OVERRIDE", "").strip()
    if override:
        if override != "skip":
            sys.stderr.write(
                f"FAIL: STATE_MIGRATION_OVERRIDE must be exactly 'skip'; got '{override}'\n"
            )
            return EXIT_MIGRATIONS_NEEDED
        reason = os.environ.get("STATE_MIGRATION_OVERRIDE_REASON", "").strip()
        if not reason:
            sys.stderr.write(
                "FAIL: STATE_MIGRATION_OVERRIDE=skip requires STATE_MIGRATION_OVERRIDE_REASON='...'\n"
            )
            return EXIT_MIGRATIONS_NEEDED
        # Audit the override using dedicated variant
        try:
            ndjson_append(LOG_PATH, StateFileMigrationOverridden(
                type="state_file_migration_overridden",
                ts=datetime.now(timezone.utc),
                reason=reason[:2000],
            ).model_dump_json())
        except Exception:
            pass
        sys.stderr.write(f"WARN: state-file migration SKIPPED by operator override; reason: {reason}\n")
        return EXIT_OK

    # Local-disk assertion (fcntl correctness)
    try:
        assert_local_disk(STATE_DIR)
    except Exception as e:
        sys.stderr.write(f"FAIL: state dir not on local disk: {e}\n")
        return EXIT_WRITE_FAILED

    # Resolve customer_tz once (best-effort; None on config failure)
    customer_tz = _resolve_customer_tz_best_effort()

    # Filter to single file if --file specified
    files_to_check = list(MIGRATORS.items())
    if args.file:
        target = Path(args.file) if "/" in args.file else STATE_DIR / args.file
        files_to_check = [(p, mc) for p, mc in MIGRATORS.items() if p == target]
        if not files_to_check:
            sys.stderr.write(f"FAIL: --file {args.file} not in migrator registry\n")
            return EXIT_UNKNOWN_SHAPE

    needed_count = 0
    for file, (model_cls, migrator) in files_to_check:
        try:
            needed, msg = _migrate_one_file(
                file, model_cls=model_cls, migrator=migrator,
                customer_tz_resolved=customer_tz, dry_run=args.check,
            )
            print(msg)
            if needed:
                needed_count += 1
        except UnknownStateShapeError as e:
            sys.stderr.write(f"FAIL: {e}\n")
            return EXIT_UNKNOWN_SHAPE
        except Exception as e:
            sys.stderr.write(f"FAIL: {file.name}: {type(e).__name__}: {e}\n")
            return EXIT_WRITE_FAILED

    if args.check and needed_count > 0:
        return EXIT_MIGRATIONS_NEEDED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
