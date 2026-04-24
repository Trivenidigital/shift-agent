# Design Review 3/5 — Ops / systemd / Deployment (general-purpose)

**Verdict:** 2 BLOCKERs, 7 MAJORs, 1 MINOR

## BLOCKERS

### B1. logrotate NDJSON config wrong (§15/§10)
`copytruncate` loses lines in race (writer holds fd, rotate truncates, writer writes at old offset → sparse file gap). Correct config:
```
/opt/shift-agent/logs/decisions.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 shift-agent shift-agent
}
```
Works ONLY because §4.5 writers open-append-close per invocation. **Verify tail-logger re-opens decisions.log each run; do NOT cache fd.**

### B2. Deploy mechanism entirely undefined (§14)
§14 is build order only, zero deploy path. Need: git repo on VPS, `deploy.sh` that pulls + `install -m 755 scripts/* /usr/local/bin/` + `systemctl daemon-reload` + restart. Rollback = `git checkout <prev-sha> && deploy.sh`. Tag each deploy.

## MAJOR

### M1. systemd `ProtectSystem=strict` misses several paths (§10)
- `/tmp` — Python stdlib uses. Add `PrivateTmp=true`.
- `/run/shift-agent-tail-logger.lock` — `/run` blocked. Use `RuntimeDirectory=shift-agent` in unit (creates `/run/shift-agent` auto).
- `ProtectHome=true` blocks `/root/.hermes` — conflicts with ReadWritePaths. Set `ProtectHome=read-only` or `false`, OR move Hermes to `/opt/hermes`.

### M2. chown /root/.hermes post-migration breaks mid-session Baileys (§11.2)
Baileys rotates auth keys ~24h. If Hermes ever started as root post-chown (manual debug), new files land root:root → next shift-agent start EACCES → session dies silently. **Fix:** add `chown -R shift-agent:shift-agent /root/.hermes` to `ExecStartPre` (idempotent guard).

### M3. Timer contention at 02:00 (§10)
tail-logger (30s) + backup (02:00) collide. tar on /opt/shift-agent/logs/ + state/ while tail-logger holds flock + writes NDJSON → tar snapshots partial JSON line = corrupt backup. **Fix:** backup script first `systemctl stop shift-agent-tail-logger.timer`, waits for lock release, tars, restarts. OR `Conflicts=shift-agent-backup.service` in tail-logger.service.

### M4. tar of live baileys_auth = partial snapshot (§5.6)
Baileys writes creds.json + pre-keys-*.json atomically, but mid-rotation tar captures half-written set → restore yields unusable session. **Fix:** `cp -a session/ /tmp/shift-session-$$/ && tar` the copy; OR stop gateway for 10s during backup (acceptable at 02:00).

### M5. GPG passphrase on disk defeats encryption (§11.3)
`GPG_PASSPHRASE` in .env next to encrypted backup = pointless. **Fix for 48h:** use `--recipient <email>` with public key only. Drop passphrase mode. Document: private key stays OFF the VPS.

### M6. Smoke test has no auto-rollback (§5.8)
Fix: smoke-test non-zero → `deploy.sh` auto-reverts to previous git tag + WhatsApp alert to owner. Manual-only at 48h is acceptable IF WA alert fires on fail.

### M7. Zero external observability
Min viable: free healthchecks.io URL, curl'd from `shift-agent-health-check.sh` on every green run. Misses 2 pings → email. Add in 10min. No HTTP endpoint needed on VPS.

## MINOR

### m1. aws s3 sync path assumes awscli + creds on VPS (§5.6)
Not in §14 build list. **Fix:** make S3 strictly optional: `[ -x "$(command -v aws)" ] && [ -n "$S3_BUCKET" ]`; log + continue on missing. Don't fail backup.

## Top 48h fixes (priority order)
1. #B1 logrotate (data loss risk)
2. #B2 deploy script (can't roll back without it)
3. #M1 PrivateTmp + RuntimeDirectory + ProtectHome
4. #M5 drop GPG passphrase mode
5. #M7 healthchecks.io ping (external heartbeat)
6. #M3 backup/tail-logger conflict
7. #M4 session cp-then-tar
8. #M2 ExecStartPre chown
9. #M6 smoke-test WA alert
