# PR-CF5 — Design (post-plan-v2)

**Drift-check tag:** `extends-Hermes`

Plan: `tasks/catering-cf5-state-migration-plan-v2.md`. This design fleshes out the implementation specifics for each commit in the v2 build sequence.

## Hermes-first checklist (final)

| Step | [Hermes] / [net-new] | LOC est. |
|---|---|---|
| Pydantic state schemas (existing, unchanged) | [Hermes] | 0 |
| Atomic JSON writes via safe_io.atomic_write_json | [Hermes] | 0 |
| safe_io.flock for read-modify-write critical section | [Hermes] | 0 (use existing helper) |
| NDJSON audit chokepoint (safe_io.ndjson_append) | [Hermes] | 0 (use existing helper) |
| 2 new audit variants in LogEntry union | [net-new] | ~30 |
| `tools/migrate-state-files.py` core (CLI, lock discipline, audit, override) | [net-new] | ~150 |
| migrate_send_counter (3 shape branches: no-op, intermediate, legacy + UnknownStateShapeError) | [net-new] | ~30 |
| migrate_seen_ids (4-field migration with documented zeroing) | [net-new] | ~25 |
| _resolve_customer_tz_best_effort + _resolve_today_with_fallback | [net-new] | ~30 |
| Deploy script integration (gate + override) | [Hermes pattern] | ~50 |
| Rollback tarball includes `tools/` (1-line fix) | [Hermes pattern] | ~5 |
| Tests (per-migrator, CLI, override, lock discipline, bootstrap) | [net-new] | ~150 |
| 3am runbook in REHEARSAL doc / new tasks/runbook-state-migration.md | [docs] | ~40 |

**Net-new total**: ~430 LOC (within plan v2's 350-500 estimate).

## Files changed

1. `src/platform/schemas.py` — 2 new audit variants + 2 entries in `LogEntry` union
2. `tools/migrate-state-files.py` (new file) — main migration script
3. `src/agents/shift/scripts/shift-agent-deploy.sh` — gate integration + rollback tarball fix
4. `tests/test_migrate_state_files.py` (new) — test suite
5. `tests/test_state_migration_audit_variants.py` (new) — schema variant validation
6. `REHEARSAL-2026-04-24.md` (or new `tasks/runbook-state-migration.md`) — operator runbook

## Schema additions

`src/platform/schemas.py`:

```python
# (insert near existing audit variants, before the LogEntry discriminated union)

class StateFileMigrated(_BaseEntry):
    """PR-CF5: a state file's on-disk shape was migrated from legacy to current schema.

    `from_shape`: JSON-stringified sorted list of legacy keys (e.g., '["date","sent_count"]')
    `to_shape`: JSON-stringified sorted list of current keys
    `backup_path`: full path to .pre-migrate-<epoch>-<pid> backup written before rewrite
    """
    type: Literal["state_file_migrated"]
    file: str = Field(min_length=1, max_length=200)
    from_shape: str = Field(min_length=1, max_length=500)
    to_shape: str = Field(min_length=1, max_length=500)
    backup_path: str = Field(min_length=1, max_length=500)


class StateFileMigrationFailed(_BaseEntry):
    """PR-CF5: migration could not complete — operator must investigate.

    Reason enum:
      - unknown_shape: file shape matches neither current schema nor known-legacy
      - load_failed_non_extra: Pydantic load failed with error other than extra_forbidden
                                (likely partial-shape or schema-mismatch; not corrupt)
      - json_decode_failed: file contains invalid JSON (corrupt at the parser level)
      - write_failed: backup or atomic_write step failed
      - backup_failed: backup file could not be created
    """
    type: Literal["state_file_migration_failed"]
    file: str = Field(min_length=1, max_length=200)
    reason: Literal[
        "unknown_shape", "load_failed_non_extra", "json_decode_failed",
        "write_failed", "backup_failed",
    ]
    detail: str = Field(default="", max_length=2000)


class StateFileMigrationOverridden(_BaseEntry):
    """PR-CF5: operator used STATE_MIGRATION_OVERRIDE=skip to bypass the gate.

    Separate from StateFileMigrationFailed because the override is a deliberate
    operator action, not a failure. file is omitted because the override skips
    ALL files (no per-file action). Mirrors the HERMES_PIN_OVERRIDE audit pattern.
    """
    type: Literal["state_file_migration_overridden"]
    reason: str = Field(min_length=1, max_length=2000,
                        description="Operator-supplied STATE_MIGRATION_OVERRIDE_REASON")
```

`LogEntry` union additions:
```python
# (in the discriminated union)
Annotated[StateFileMigrated, Tag("state_file_migrated")],
Annotated[StateFileMigrationFailed, Tag("state_file_migration_failed")],
Annotated[StateFileMigrationOverridden, Tag("state_file_migration_overridden")],
```

## tools/migrate-state-files.py — full skeleton

```python
#!/usr/bin/env python3
"""migrate-state-files — bring legacy state files up to current Pydantic schema.

Idempotent: running on already-current files is a no-op.
Fail-loud: unknown shapes exit 2 with audit row; load failures other than
extra_forbidden also exit 2 (do not auto-migrate corrupt-but-current files).

Per-file flow (under safe_io.flock(file)):
  1. Read current content
  2. Try model_validate against current schema
  3. If load OK → no-op (already current)
  4. If load fails with extra_forbidden → run registered migrator
  5. If migrator returns dict → backup + atomic_write + audit
  6. If migrator raises UnknownStateShapeError → audit + exit 2
  7. If load fails non-extra → audit + exit 2 (corruption-class; manual triage)

Override: STATE_MIGRATION_OVERRIDE=skip + STATE_MIGRATION_OVERRIDE_REASON='...'
  Skips ALL migrations, audits the skip, exits 0. Mirrors HERMES_PIN_OVERRIDE.

CLI:
  --check                  : dry-run; exit 0 clean, 1 if migrations needed, 2 unknown shape
  --apply                  : do migrations
  --apply --file <path>    : migrate one file only

Exit codes:
  0  : clean (no migrations needed) OR all migrations succeeded OR override-skipped
  1  : migrations needed (--check only)
  2  : unknown shape OR non-extra load failure (operator must triage)
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

sys.path.insert(0, "/opt/shift-agent")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from safe_io import (
    flock, atomic_write_json, ndjson_append, load_yaml_model,
)
from schemas import (
    Config, SendCounter, SeenIds,
    StateFileMigrated, StateFileMigrationFailed,
)
from pydantic import ValidationError

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

    Returns None if already current (no-op).
    Raises UnknownStateShapeError if shape unrecognized.
    """
    keys = set(legacy.keys())

    # No-op: already current shape
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
    """Legacy {} OR 2-field {seen_message_ids, max_size} → current 4-field shape.

    NOTE: Zeroing last_offset_bytes/agent_log_inode means the tail-logger starts
    a fresh scan from EOF on next start. Correct for empty-dict legacy file.
    Documented in audit row 'detail' field.
    """
    keys = set(legacy.keys())
    current_keys = {"seen_message_ids", "max_size", "last_offset_bytes", "agent_log_inode"}

    # No-op: already current
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
    Returns None on any failure.
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

# PR-CF5 design v2: per-file expected-current key-set for the dispatch
# pre-check in _migrate_one_file. Must match the Pydantic model fields
# exactly (including required + optional with defaults).
MIGRATOR_EXPECTED_KEYS: dict[Path, set[str]] = {
    SEND_COUNTER_PATH: {"day", "count", "last_send_ts"},
    SEEN_IDS_PATH: {"seen_message_ids", "max_size", "last_offset_bytes", "agent_log_inode"},
}


def _audit_migrated(file: Path, from_shape: list[str], to_shape: list[str], backup_path: Path):
    """Best-effort audit emission. Failures logged to stderr."""
    try:
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


def _audit_failed(file: Path, reason: str, detail: str):
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
    convention (`<file>.corrupt-<epoch>`). Uses path.with_name to avoid the
    with_suffix dotted-name pitfall. Single-PID per-deploy means PID suffix is
    unnecessary for collision avoidance — the operator's grep `*.corrupt-*` /
    `*.pre-migrate-*` muscle memory works uniformly."""
    return file.with_name(f"{file.name}.pre-migrate-{int(time.time())}")


def _migrate_one_file(
    file: Path, *, model_cls: type, migrator: Callable,
    customer_tz_resolved: Optional[str], dry_run: bool,
) -> tuple[bool, str]:
    """Migrate a single file under flock.

    Returns (needed_migration: bool, status_msg: str).
    Raises UnknownStateShapeError on unknown shape (caller decides exit code).

    DISPATCH LOGIC (PR-CF5 design v2 — fixes design v1's structural bug where
    Pydantic-with-defaults silently passed legacy shapes through without
    invoking the migrator):

      1. Read file + json.loads (json_decode_failed audit on parse error).
      2. Compute key-set of data dict.
      3. Look up `expected_current_keys` from migrator (declared per-migrator).
      4. If keys EXACTLY match expected_current_keys → no-op (already current).
      5. Otherwise → ALWAYS call migrator (which decides: legacy → new dict,
         intermediate → new dict, unknown → raise UnknownStateShapeError).
      6. Validate migrator output via model_cls.model_validate.
      7. Backup + atomic_write + audit.

    This avoids the trap where a 2-field SeenIds legacy file would pass
    `model_validate` (all fields have defaults) and silently bypass migration,
    OR where a {date, sent_count} SendCounter would fail with `missing` errors
    (not `extra_forbidden`) and incorrectly route to load_failed_non_extra.
    """
    if not file.exists():
        return (False, f"{file.name}: not present (skip)")

    with flock(file):  # safe_io.flock(file) acquires <file>.lock sibling — same lock target writers use
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

        # PR-CF5 design v2 dispatch: key-set pre-check bypasses Pydantic-with-defaults trap
        from_keys = set(data.keys())
        expected_current_keys = MIGRATOR_EXPECTED_KEYS[file]  # registry-declared
        if from_keys == expected_current_keys:
            # Final defense: validate against schema to catch type errors that
            # key-set check misses (e.g., {day: 123, count: "x", last_send_ts: null}).
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
            # Migrator decided no-op despite key-set diff (defensive case;
            # shouldn't happen given the pre-check, but documents the contract)
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
            return (True, f"{file.name}: WOULD migrate from {sorted(from_keys)} → {sorted(new_data.keys())}")

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
        return (True, f"{file.name}: migrated {sorted(from_keys)} → {sorted(new_data.keys())} (backup={backup.name})")


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
            return EXIT_MIGRATIONS_NEEDED  # exit 1 (matches HERMES_PIN_OVERRIDE fail convention)
        reason = os.environ.get("STATE_MIGRATION_OVERRIDE_REASON", "").strip()
        if not reason:
            sys.stderr.write(
                "FAIL: STATE_MIGRATION_OVERRIDE=skip requires STATE_MIGRATION_OVERRIDE_REASON='...'\n"
            )
            return EXIT_MIGRATIONS_NEEDED
        # Audit the override using dedicated variant (NOT misuse of MigrationFailed
        # with file="(all)" sentinel — separate event class, separate audit type)
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
```

## shift-agent-deploy.sh changes

### Change 1: Insert migration gate (post-Hermes-pin-verify, pre-install_artifacts)

```bash
# (new section, after line ~250 where Hermes pin verify currently runs)
echo "=== state-file migration check ==="
MIGRATOR="$STAGING/tools/migrate-state-files.py"
if [ ! -x "$MIGRATOR" ]; then
    echo "WARN: state-file migrator absent at $MIGRATOR — skipping (tarball may be pre-CF5 vintage)"
else
    if ! "$MIGRATOR" --check; then
        if [ -n "${STATE_MIGRATION_OVERRIDE:-}" ]; then
            # Override path: re-validation done by migrator itself (same env vars)
            "$MIGRATOR" --apply || {
                echo "FAIL: state-file migration override invocation failed"
                exit 1
            }
        else
            echo "=== applying migrations ==="
            "$MIGRATOR" --apply || {
                echo "FAIL: state-file migration failed; refusing to deploy"
                exit 1
            }
        fi
    else
        echo "OK: all state files current; no migration needed"
    fi
fi
```

### Change 2: Rollback tarball includes `tools/`

Around line 294 of `shift-agent-deploy.sh`:

```bash
# Was:
tar czf "$DEPLOYS_DIR/$tag.tgz" -C "$STAGING" src .commit-hash
# Becomes (conditional on tools/ existing — graceful for pre-CF5 staging):
if [ -d "$STAGING/tools" ]; then
    tar czf "$DEPLOYS_DIR/$tag.tgz" -C "$STAGING" src tools .commit-hash
else
    tar czf "$DEPLOYS_DIR/$tag.tgz" -C "$STAGING" src .commit-hash
fi
```

## Test plan (full enumeration)

**File**: `tests/test_migrate_state_files.py` (Linux-only, fcntl)

Per-migrator tests (unit-level):
- `test_send_counter_no_op_when_current` — `{day, count, last_send_ts}` → returns None
- `test_send_counter_intermediate_strips_sent_count` — `{day, count, sent_count}` → strips
- `test_send_counter_legacy_full_with_date` — `{date, sent_count}` with date set → migrates
- `test_send_counter_legacy_null_date_uses_tz_fallback` — `{date: null, sent_count: 0}` + tz="America/New_York" → migrates with NY today
- `test_send_counter_legacy_null_date_no_config_uses_utc` — `{date: null, sent_count: 0}` + tz=None → UTC today
- `test_send_counter_unknown_shape_raises` — `{foo, bar}` → raises UnknownStateShapeError
- `test_seen_ids_no_op_when_current` — 4-field shape → returns None
- `test_seen_ids_empty_dict_migrates_to_4_fields` — `{}` → all 4 fields with zeros
- `test_seen_ids_2_field_legacy_migrates` — `{seen_message_ids, max_size}` → 4 fields with zeros for last_offset_bytes/agent_log_inode
- `test_seen_ids_unknown_shape_raises` — `{seen_message_ids, max_size, foo}` → raises

Dispatch-layer tests (integration via `_migrate_one_file`):
- `test_dispatch_seen_ids_empty_dict_routes_to_migrator` — file `{}` → `_migrate_one_file` calls `migrate_seen_ids` (Finding 1 fix verified end-to-end)
- `test_dispatch_send_counter_legacy_routes_to_migrator` — file `{date, sent_count}` → `_migrate_one_file` calls `migrate_send_counter` (Finding 1 fix verified)
- `test_dispatch_keys_match_validation_succeeds_no_op` — file `{day, count, last_send_ts}` → no-op
- `test_dispatch_keys_match_but_validation_fails` — file `{day: 123, count: "x", last_send_ts: null}` → load_failed_non_extra audit + raises
- `test_dispatch_corrupt_json_emits_json_decode_failed` — invalid JSON → json_decode_failed audit + raises
- `test_dispatch_top_level_not_dict_raises` — `[1, 2, 3]` → unknown_shape audit + raises

CLI integration tests (subprocess):
- `test_check_clean_returns_0` — current files → exit 0
- `test_check_legacy_returns_1` — legacy file present → exit 1
- `test_check_unknown_returns_2` — unknown shape → exit 2
- `test_apply_writes_backup_before_rewrite` — verify `.pre-migrate-<epoch>-<pid>` exists
- `test_apply_writes_audit_row` — verify state_file_migrated row in decisions.log
- `test_apply_idempotent` — second invocation no-ops
- `test_apply_single_file` — `--file send-counter.json` migrates only that one
- `test_override_skip_without_reason_fails` — exit non-zero
- `test_override_skip_with_reason_succeeds` — exit 0, audit row written
- `test_lock_discipline` — synthetic concurrent writer holds flock; migrator blocks then succeeds

Schema validation tests (Linux + Windows):
- `test_state_file_migrated_variant_validates_via_LogEntry_union`
- `test_state_file_migration_failed_reason_literal_enforced`

Total: 28 tests; ~200 LOC. (Added 6 dispatch-layer integration tests in v2 to verify Finding 1 fix end-to-end + json_decode_failed reason).

## Runbook section (new file: `tasks/runbook-state-migration.md`)

```markdown
# State-file migration runbook (PR-CF5)

## Scenario A: Migration succeeded but gateway failed to restart

**Symptoms**: Deploy completed without errors, but `systemctl status hermes-gateway` shows failed.

**Steps**:
1. Identify migrated files:
   ```bash
   tail -100 /opt/shift-agent/logs/decisions.log | jq -c 'select(.type=="state_file_migrated")'
   ```
2. Locate backup:
   ```bash
   ls -lat /opt/shift-agent/state/<file>.pre-migrate-* | head -3
   ```
3. Restore + restart:
   ```bash
   cp /opt/shift-agent/state/send-counter.json.pre-migrate-1735834212-12345 /opt/shift-agent/state/send-counter.json
   systemctl restart hermes-gateway
   ```
4. Post-mortem: file `tasks/cf5-followup-<file>-shape.md` documenting the actual shape vs expected.

## Scenario B: Migration encountered unknown shape

**Symptoms**: Deploy fails with "FAIL: state-file migration failed".

**Steps**:
1. Check failure detail:
   ```bash
   tail -10 /opt/shift-agent/logs/decisions.log | jq -c 'select(.type=="state_file_migration_failed")'
   ```
2. Inspect shape:
   ```bash
   jq 'keys' /opt/shift-agent/state/<file>.json
   ```
3. Decide: new legitimate shape (add migrator) vs file corruption (manual repair).
4. Override to deploy + handle later:
   ```bash
   STATE_MIGRATION_OVERRIDE=skip \
   STATE_MIGRATION_OVERRIDE_REASON="seen-ids.json has unexpected key 'foo' from manual edit; will fix in PR-CF5.1" \
     /usr/local/bin/shift-agent-deploy.sh
   ```

## Scenario C: Rollback to a pre-CF5 tarball

**Symptoms**: Need to revert to a deploy from before CF5 landed.

**Steps**:
1. The migration gate auto-skips if migrator script absent (bootstrap-friendly):
   ```bash
   shift-agent-deploy rollback <pre-cf5-tag>
   ```
2. State files remain in current shape. The pre-CF5 deployed code expected the legacy shape — it may break.
3. Manually restore state files from backup if needed:
   ```bash
   cp send-counter.json.pre-migrate-* send-counter.json
   ```
```

## Reviewer lens for design

- **Correctness**: SeenIds 4-field migration includes `last_offset_bytes=0, agent_log_inode=0` (BLOCKER-1 fix verified)
- **Lock discipline**: `safe_io.flock(file)` wraps the entire read-validate-migrate-write section
- **Backup naming**: `path.with_name(f"{file.name}.pre-migrate-{int(time.time())}-{os.getpid()}")` — matches safe_io quarantine convention; epoch+PID avoids collision
- **Override pattern**: matches `HERMES_PIN_OVERRIDE` attestation (re-type value + REASON + audit row)
- **Bootstrap safety**: gate skips if migrator absent (rollback to pre-CF5 tarball compat)
- **Audit emission**: every failure path emits `state_file_migration_failed` with appropriate reason
- **Test coverage**: 22 tests covering all paths; reviewer verifies no asserted-but-unexercised path
- **3am runbook**: 3 scenarios covered with copy-pasteable commands

## Build sequence (commit-by-commit)

1. **Commit 1**: schemas.py — 2 new audit variants + LogEntry union entries + forward-compat tests (~50 LOC)
2. **Commit 2**: tools/migrate-state-files.py skeleton (CLI, override gate, customer_tz resolution, registry — but NO migrator functions) + CLI tests (~80 LOC)
3. **Commit 3**: migrate_send_counter (3 shape branches + UnknownStateShapeError) + 7 tests (~60 LOC)
4. **Commit 4**: migrate_seen_ids (4-field migration with documented zeroing) + 4 tests (~40 LOC)
5. **Commit 5**: shift-agent-deploy.sh — gate integration + rollback tarball fix (~60 LOC)
6. **Commit 6**: lock discipline test + bootstrap test + runbook section (~80 LOC docs + tests)

Total: 6 commits, ~370 LOC. ~5 hours including tests + 3-reviewer cycle.
