# State-file migration runbook (PR-CF5)

## Scenario A: Migration succeeded but gateway failed to restart

**Symptoms**: Deploy completed without errors, but `systemctl status hermes-gateway` shows failed.

**Steps**:
1. Identify migrated files from the most-recent `state_file_migrated` audit row:
   ```bash
   tail -100 /opt/shift-agent/logs/decisions.log | jq -c 'select(.type=="state_file_migrated")'
   ```
2. Locate the backup:
   ```bash
   ls -lat /opt/shift-agent/state/<file>.pre-migrate-* | head -3
   ```
3. Restore + restart:
   ```bash
   cp /opt/shift-agent/state/send-counter.json.pre-migrate-1735834212 /opt/shift-agent/state/send-counter.json
   systemctl restart hermes-gateway
   ```
4. Post-mortem: file `tasks/cf5-followup-<file>-shape.md` documenting the actual shape vs expected.

## Scenario B: Migration encountered unknown shape

**Symptoms**: Deploy fails with "ERROR: state-file migration failed".

**Steps**:
1. Check failure detail:
   ```bash
   tail -10 /opt/shift-agent/logs/decisions.log | jq -c 'select(.type=="state_file_migration_failed")'
   ```
2. Inspect actual shape:
   ```bash
   jq 'keys' /opt/shift-agent/state/<file>.json
   ```
3. Decide: new legitimate shape (add migrator) vs file corruption (manual repair).
4. Override to deploy + handle later (re-typing the literal `skip` value + REASON env mirrors HERMES_PIN_OVERRIDE attestation pattern):
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
3. **If a `.pre-migrate-*` backup exists** (migration ran during the CF5+ period), manually restore it:
   ```bash
   cp /opt/shift-agent/state/send-counter.json.pre-migrate-1735834212 /opt/shift-agent/state/send-counter.json
   ```
4. **If NO `.pre-migrate-*` backup exists** (state files were already current at CF5 deploy time), construct the legacy shape manually (impossible to perfectly reverse — `last_offset_bytes` was zeroed by the forward migration; tail-logger will start a fresh scan from current EOF):
   ```bash
   echo '{"date": "2026-05-03", "sent_count": 0}' > /opt/shift-agent/state/send-counter.json
   echo '{}' > /opt/shift-agent/state/seen-ids.json
   ```
5. Restart: `systemctl restart hermes-gateway`

## Scenario D: Operator override caused unexpected behavior

**Symptoms**: Migration was skipped via `STATE_MIGRATION_OVERRIDE=skip`, but downstream services now fail to read state files.

**Steps**:
1. Check the override audit row:
   ```bash
   tail -50 /opt/shift-agent/logs/decisions.log | jq -c 'select(.type=="state_file_migration_overridden")'
   ```
2. Re-run migration without override:
   ```bash
   /opt/shift-agent/staging-new/tools/migrate-state-files.py --apply
   ```
3. Restart: `systemctl restart hermes-gateway`

## Audit-row reference

| Variant | When emitted | Key fields |
|---|---|---|
| `state_file_migrated` | successful per-file migration | `file`, `from_shape`, `to_shape`, `backup_path` |
| `state_file_migration_failed` | per-file failure | `file`, `reason` (one of `unknown_shape`, `load_failed_non_extra`, `json_decode_failed`, `write_failed`, `backup_failed`), `detail` |
| `state_file_migration_overridden` | operator used `STATE_MIGRATION_OVERRIDE=skip` | `reason` (operator-supplied) |

## Common quick-greps

```bash
# All state-file migrations on this VPS:
grep state_file_migrated /opt/shift-agent/logs/decisions.log | jq -c '{ts, file, from: .from_shape, to: .to_shape}'

# All migration failures (debugging):
grep state_file_migration_failed /opt/shift-agent/logs/decisions.log | jq -c '{ts, file, reason, detail: .detail[0:200]}'

# Recent backup files (audit trail of what was rewritten):
ls -lat /opt/shift-agent/state/*.pre-migrate-* | head -10
```
