# PR-CF5 v2 — state-file migration script + deploy gate

**Drift-check tag:** `extends-Hermes`

**Plan version:** v2 (post-review). v1 had 2 BLOCKERs + 7 HIGHs surfaced by 2 parallel reviewers. v2 addresses all of them. Net-new effort estimate revised from 250-350 LOC to 350-500 LOC.

## What v1 got wrong

1. **SeenIds schema described as 2 fields** — actually 4 fields (`last_offset_bytes`, `agent_log_inode` were missing). Migrator output of just 2 fields would cause the tail-logger to re-process the entire agent.log from the beginning on next start (silent data regression).
2. **Rollback tarball doesn't include `tools/`** — `shift-agent-deploy.sh:294` snapshots only `src/` + `.commit-hash`. CF5 deploy gate would call a missing `tools/migrate-state-files.py` on rollback to a pre-CF5 tarball, breaking rollback permanently.
3. **`STATE_MIGRATION_OVERRIDE=skip` weaker than deployed `HERMES_PIN_OVERRIDE`** — no operator attestation, no required REASON, no audit row.
4. **`customer_tz` source unspecified** when config.yaml itself is invalid (the exact failure mode that motivated this PR).
5. **Intermediate state shapes** (e.g., `{day, count, sent_count}` from a partial migration) would falsely trigger `UnknownStateShapeError`.
6. **Migration writes lacked `safe_io.flock(file)`** around read-validate-rewrite critical section — race with active writes from `send-coverage-message`.
7. **Step 7 (load fails non-extra) exited silently** — should emit `state_file_migration_failed` audit row.
8. **Backup naming `<file>.pre-migrate-<ts>`** ambiguous re: format; safe_io quarantine convention uses `<file>.corrupt-<int(time.time())>` via `path.with_name()`.

## Hermes-first checklist (v2)

| Step | [Hermes] / [net-new] | Notes |
|---|---|---|
| Pydantic state schemas (existing) | [Hermes] (unchanged) | extra="forbid" preserved everywhere |
| State file storage | [Hermes] JSON-on-disk + atomic_write + flock | No new persistence |
| Migration script `tools/migrate-state-files.py` | [net-new] (~150 LOC core + 4 file migrators × 30 LOC each) | Idempotent; per-file recognizers |
| Deploy gate hook in `shift-agent-deploy.sh` | [Hermes] pattern (mirrors PR-D1 `check-audit-helpers-symbols` + Hermes pin verify) | Adds 2-step pre-restart check |
| Rollback tarball includes `tools/` | [Hermes] (extends rollback discipline) | Required to avoid bricking rollback path |
| Audit emission of migrations | [Hermes] via NDJSON chokepoint | 2 new variants in LogEntry union |
| Backup-before-rewrite | [Hermes] pattern (mirrors safe_io quarantine) | `<file>.pre-migrate-<int(time.time())>-<pid>` |
| `STATE_MIGRATION_OVERRIDE` attestation | [Hermes] pattern (mirrors `HERMES_PIN_OVERRIDE`) | Re-type value + required REASON env + audit row |

**Net-new effort**: ~350-500 LOC (revised from v1's 250-350). Breakdown:
- 2 audit variants in schemas.py: ~30 LOC
- `tools/migrate-state-files.py` core: ~150 LOC (CLI, lock discipline, audit, override gate, idempotency)
- 2 file migrators (send-counter, seen-ids): ~30 LOC each = 60 LOC
- Deploy script integration: ~30 LOC
- Rollback tarball fix in `shift-agent-deploy.sh`: ~5 LOC
- Tests: ~150 LOC (Linux-only via pytest.mark.skipif Windows; per-migrator + idempotency + lock + override)
- 3am runbook section in REHEARSAL doc: ~20 LOC

## Scope (this PR — v2)

### Part A — `tools/migrate-state-files.py` (corrected)

CLI:
```
tools/migrate-state-files.py --check       # dry-run; exit 0 clean, 1 if migrations needed, 2 unknown shape
tools/migrate-state-files.py --apply       # do the migrations; backup + rewrite + audit row
tools/migrate-state-files.py --apply --file send-counter.json   # one file
```

**Per-file flow on `--apply` (v2 with all reviewer fixes):**

1. **Acquire `safe_io.flock(file)`** for the entire critical section (matches the same lock target writers use → automatic serialization with active agents).
2. Read current file content.
3. Try `model_validate` against current Pydantic schema.
4. If load succeeds → no-op (already migrated).
5. If load fails with `extra_forbidden` → run registered migrator function.
6. If migrator returns `dict` → backup `<file>.pre-migrate-<int(time.time())>-<pid>` via `path.with_name()` (matches safe_io quarantine convention) + `atomic_write_json` against current Pydantic model + audit row `state_file_migrated`.
7. If migrator raises `UnknownStateShapeError` → audit row `state_file_migration_failed{reason: "unknown_shape"}` + exit 2.
8. **If load fails with anything other than `extra_forbidden`** → audit row `state_file_migration_failed{reason: "load_failed_non_extra"}` + exit 2 (v2 fix: was silent in v1).
9. Release lock.

**Per-file migrators (v2 corrected):**

```python
def migrate_send_counter(legacy: dict, *, customer_tz_resolved: str) -> Optional[dict]:
    """Legacy {date, sent_count} → current {day, count, last_send_ts}.

    customer_tz_resolved is the result of try-resolve-with-fallback (see below).
    Returns None if already current shape.
    """
    # No-op if already current
    if "day" in legacy and "count" in legacy and "sent_count" not in legacy:
        return None  # already migrated

    # Intermediate shape: {day, count, sent_count} — strip the extra field
    if "day" in legacy and "count" in legacy and "sent_count" in legacy:
        return {
            "day": legacy["day"],
            "count": legacy["count"],
            "last_send_ts": legacy.get("last_send_ts"),
        }

    # Legacy: {date, sent_count} → fully migrate
    if set(legacy.keys()) <= {"date", "sent_count"}:
        return {
            "day": legacy.get("date") or _resolve_today_with_fallback(customer_tz_resolved),
            "count": legacy.get("sent_count", 0),
            "last_send_ts": None,
        }

    raise UnknownStateShapeError(
        f"send-counter.json has unrecognized keys: {sorted(legacy.keys())}"
    )


def migrate_seen_ids(legacy: dict) -> Optional[dict]:
    """Legacy {} (or any empty/2-field shape) → current 4-field shape.

    NOTE: Zeroing last_offset_bytes/agent_log_inode means the tail-logger
    starts a fresh scan from EOF on next start. This is correct for an
    empty-dict legacy file (no historic state to preserve). Documented
    explicitly in audit row detail.
    """
    if all(k in legacy for k in ["seen_message_ids", "max_size", "last_offset_bytes", "agent_log_inode"]):
        return None  # already current

    return {
        "seen_message_ids": legacy.get("seen_message_ids", []),
        "max_size": legacy.get("max_size", 10000),
        "last_offset_bytes": legacy.get("last_offset_bytes", 0),
        "agent_log_inode": legacy.get("agent_log_inode", 0),
    }


def _resolve_today_with_fallback(customer_tz_resolved: str) -> str:
    """Resolve current date in customer tz, with UTC fallback if tz invalid.

    customer_tz_resolved is the string from CustomerConfig.timezone if config
    loaded successfully, else None (caller passes None when config invalid).
    """
    if not customer_tz_resolved:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(customer_tz_resolved)).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()
```

**`customer_tz` resolution at script entry**:
```python
def _resolve_customer_tz_best_effort() -> Optional[str]:
    """Try to load config.yaml + extract customer.timezone. Return None on
    any failure (config missing/corrupt/invalid). Migrators handle None by
    falling back to UTC for the new 'day' field.
    """
    try:
        from safe_io import load_yaml_model
        from schemas import Config
        cfg = load_yaml_model(Path("/opt/shift-agent/config.yaml"), Config)
        return cfg.customer.timezone
    except Exception:
        return None
```

### Part B — Deploy-gate integration (corrected)

`shift-agent-deploy.sh`:

```bash
# After Hermes pin verify, before install_artifacts
echo "=== state-file migration check ==="

MIGRATOR="$STAGING/tools/migrate-state-files.py"
if [ ! -x "$MIGRATOR" ]; then
    echo "WARN: state-file migrator absent at $MIGRATOR — skipping (tarball is pre-CF5 vintage)"
    # Bootstrap-friendly: a pre-CF5 tarball won't have the migrator script;
    # rollback to such a tarball must NOT fail-closed on migration check.
else
    if ! "$MIGRATOR" --check; then
        # Override gate (mirrors HERMES_PIN_OVERRIDE pattern: re-type value + REASON)
        if [ -n "${STATE_MIGRATION_OVERRIDE:-}" ]; then
            if [ "$STATE_MIGRATION_OVERRIDE" != "skip" ]; then
                echo "FAIL: STATE_MIGRATION_OVERRIDE must be exactly 'skip' to bypass; got '$STATE_MIGRATION_OVERRIDE'"
                exit 1
            fi
            if [ -z "${STATE_MIGRATION_OVERRIDE_REASON:-}" ]; then
                echo "FAIL: STATE_MIGRATION_OVERRIDE=skip requires STATE_MIGRATION_OVERRIDE_REASON='...'"
                exit 1
            fi
            # Audit the skip via best-effort log-decision-direct (mirrors PR-D1 pin override)
            log-decision-direct "$(jq -n --arg ts "$(date -u -Iseconds)" --arg reason "$STATE_MIGRATION_OVERRIDE_REASON" \
                '{type:"state_file_migration_skipped",ts:$ts,reason:$reason}')" 2>&1 | logger -t state-migration-override || true
            echo "WARN: state-file migration SKIPPED by operator override; reason: $STATE_MIGRATION_OVERRIDE_REASON"
        else
            echo "=== applying migrations ==="
            "$MIGRATOR" --apply || {
                echo "FAIL: state-file migration failed; refusing to deploy"
                exit 1
            }
        fi
    fi
fi
```

### Part C — Rollback tarball includes `tools/` (BLOCKER-2 fix)

`shift-agent-deploy.sh:294`, change:
```bash
# Was:
tar czf "$DEPLOYS_DIR/$tag.tgz" -C "$STAGING" src .commit-hash
# Becomes:
tar czf "$DEPLOYS_DIR/$tag.tgz" -C "$STAGING" src tools .commit-hash 2>/dev/null
# Note: 'tools' may not exist on pre-CF5 tarballs — tar handles missing-dir
# gracefully if we use the bracket form; alternative: conditional check.
```

### Schema additions (v2, snake_case + extra="forbid" inherited from `_BaseEntry`)

```python
class StateFileMigrated(_BaseEntry):
    """v0.1 PR-CF5: a state file's on-disk shape was migrated to current schema."""
    type: Literal["state_file_migrated"]
    file: str = Field(min_length=1, max_length=200)
    from_shape: str = Field(min_length=1, max_length=500)  # JSON-stringified key set
    to_shape: str = Field(min_length=1, max_length=500)
    backup_path: str = Field(min_length=1, max_length=500)


class StateFileMigrationFailed(_BaseEntry):
    """v0.1 PR-CF5: migration could not complete — operator must investigate."""
    type: Literal["state_file_migration_failed"]
    file: str = Field(min_length=1, max_length=200)
    reason: Literal[
        "unknown_shape", "load_failed_non_extra", "write_failed",
        "backup_failed", "operator_override",
    ]
    detail: str = Field(default="", max_length=2000)
```

(Both inherit `_BaseEntry` which carries `extra="forbid"` + `ts`. Both registered in the `LogEntry` union with matching `Tag(...)`.)

## Failure-modes runbook (v2 — for 3am incident response)

Add to `REHEARSAL-2026-04-24.md` or a new `tasks/runbook-state-migration.md`:

**Scenario: migration succeeded, gateway failed to restart**

1. Identify the migrated file from the most-recent `state_file_migrated` audit row:
   ```bash
   tail -100 /opt/shift-agent/logs/decisions.log | jq -c 'select(.type=="state_file_migrated")'
   ```
2. Locate the backup file:
   ```bash
   ls -lat /opt/shift-agent/state/<file>.pre-migrate-* | head -3
   ```
3. Restore + restart:
   ```bash
   cp /opt/shift-agent/state/send-counter.json.pre-migrate-1735834212-12345 /opt/shift-agent/state/send-counter.json
   systemctl restart hermes-gateway
   ```
4. Post-mortem: file the actual on-disk shape vs expected schema as a `tasks/cf5-followup-<file>-shape.md` issue.

**Scenario: migration encountered unknown shape**

1. Check the audit row:
   ```bash
   tail -10 /opt/shift-agent/logs/decisions.log | jq -c 'select(.type=="state_file_migration_failed")'
   ```
2. Inspect the file's actual shape:
   ```bash
   jq 'keys' /opt/shift-agent/state/<file>
   ```
3. Decide: is this a new legitimate shape we need to add a migrator for, or is the file corrupt?
4. If new shape: file `tasks/cf5-add-migrator-<file>.md` and use override:
   ```bash
   STATE_MIGRATION_OVERRIDE=skip STATE_MIGRATION_OVERRIDE_REASON="<file> on shape: ..." \
   /usr/local/bin/shift-agent-deploy.sh
   ```

## Deployed-pattern checklist (v2)

- ✅ JSON-on-disk + atomic_write_json (migration writes via this)
- ✅ flock for concurrent agent + concurrent deploy (uses `safe_io.flock(file)` matching writer-side lock target)
- ✅ NDJSON audit via `safe_io.ndjson_append` chokepoint (2 new variants in `LogEntry` union)
- ✅ Pydantic v2 + extra="forbid" inherited from `_BaseEntry` on new audit variants
- ✅ No relaxation of any state schema's extra="forbid" — that is the point
- ✅ Audit variant snake_case naming: `state_file_migrated`, `state_file_migration_failed`, `state_file_migration_skipped`
- ✅ Override env mirrors `HERMES_PIN_OVERRIDE` deployed pattern (re-type value + REASON + audit)
- ✅ Backup naming uses `path.with_name(f"{path.name}.pre-migrate-{int(time.time())}-{os.getpid()}")` — matches safe_io quarantine convention; epoch+PID avoids collision; with_name avoids the with_suffix dotted-name pitfall noted in safe_io
- ✅ Tests: subprocess-invoke + assert on file mutations + audit rows + Linux-only via pytest.mark.skipif (matches `tests/test_catering_v02_scripts.py` pattern)
- ✅ Bootstrap-friendly: gate skips if migrator script absent (rollback to pre-CF5 tarball compat)

## Build sequence (v2)

1. **Commit 1**: 2 audit variants in schemas.py + forward-compat test
2. **Commit 2**: `tools/migrate-state-files.py` skeleton (CLI, customer_tz resolution, lock discipline, audit, no migrators yet) + tests
3. **Commit 3**: `migrate_send_counter` + intermediate-shape handling + tests
4. **Commit 4**: `migrate_seen_ids` (4 fields with documented zeroing) + tests
5. **Commit 5**: deploy script integration + rollback tarball `tools/` inclusion + override gate
6. **Commit 6**: 3am runbook section + smoke test on srilu

Total estimate: 350-500 LOC. ~5-6 hours including tests + 3-reviewer cycle.

## Test plan (v2)

- **Migrator unit tests**:
  - send-counter: `{date, sent_count}` → `{day, count, last_send_ts}` with date preserved
  - send-counter: `{date: null, sent_count: 0}` → uses `_resolve_today_with_fallback("America/New_York")`
  - send-counter: `{date: null, sent_count: 0}` with config-load failure → uses UTC fallback
  - send-counter: intermediate `{day, count, sent_count}` → strips `sent_count`, preserves rest
  - send-counter: current shape → no-op (returns None)
  - send-counter: unknown shape → raises UnknownStateShapeError
  - seen-ids: `{}` → 4-field default with explicit zeros
  - seen-ids: current 4-field shape → no-op
  - seen-ids: legacy 2-field `{seen_message_ids, max_size}` → adds `last_offset_bytes=0, agent_log_inode=0`
- **CLI tests**:
  - `--check` returns exit 1 when migrations needed, exit 0 when clean, exit 2 unknown shape
  - `--apply` writes backup before rewrite, preserves backup naming convention
  - `--apply --file <single>` migrates only the named file
  - Concurrent invocation (race) — second invocation no-ops (idempotent), or blocks on flock
- **Lock discipline test**:
  - Synthetic concurrent writer holds `safe_io.flock(send-counter.json)`; migrator blocks until release
- **Override gate tests**:
  - Missing `STATE_MIGRATION_OVERRIDE` → migration runs normally
  - `STATE_MIGRATION_OVERRIDE=skip` without REASON → exit 1
  - `STATE_MIGRATION_OVERRIDE=skip` + REASON → audit row, deploy proceeds
  - `STATE_MIGRATION_OVERRIDE=anything-else` → exit 1
- **Bootstrap tests**:
  - Gate runs when migrator script absent → WARN + skip (rollback to pre-CF5 tarball compat)

## Reviewer lens (v2)

- **Hermes-first**: confirm zero schema relaxation; extra="forbid" preserved everywhere; mirrors PR-D1 deploy-gate pattern
- **Drift compliance**: confirm backup naming + override env both match deployed conventions exactly
- **Correctness**: SeenIds 4-field migration (BLOCKER-1 fix); intermediate shape handling; lock discipline
- **Failure modes**: 3am runbook — does it cover migrate-then-restart-fail; unknown-shape; non-extra load failure?
- **Bootstrap / rollback**: confirm pre-CF5 tarball rollback isn't bricked by the gate
- **Test coverage**: 18+ test cases covering all v2 paths; reviewer verifies no path is asserted-but-unexercised
