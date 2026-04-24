# Design Review 2/5 — Silent Failure Modes (pr-review-toolkit:silent-failure-hunter)

**Verdict:** 4 BLOCKERs, 9 MAJORs, 2 MINORs

## BLOCKERS

### B1. Cap-check ↔ counter-increment race (§5.2 steps 4, 9; §4.3)
Concurrent `send-coverage-message` invocations (reconciler + skill + retry) — both read count=5, both pass cap=6, both POST, both increment. 7 sends, no log of breach. **Fix:** hold `flock(send-counter.json.lock)` across entire check→POST→increment, OR do optimistic reserve (increment first, decrement on fail).

### B2. POST-success-before-state-update gap (§5.2 steps 8→9)
If process dies between successful POST and pending.json update, candidate was messaged but status still `approved`. Reconciler re-sends on boot. Duplicate message. **Fix:** write `outbound_attempted` log entry with message_id BEFORE POST; reconciler treats `attempted` as "do not retry without human signal."

### B3. Dead-man can't deliver when bridge is dead (§5.5)
Health check fires "AGENT DOWN" via `POST /send` → the component it just detected as dead. Pushover/email marked optional. If not configured → complete silence. **Fix:** make ≥1 out-of-band channel (Pushover, SMS via Twilio, email) REQUIRED. Log-and-refuse-to-start if not configured.

### B4. Health-check crash has no watchdog (§5.5)
If `shift-agent-health-check.sh` throws (jq missing, curl hang, disk-full health.log), systemd logs `status=1` but nobody reads journalctl. **Fix:** touch `last-health-check-ts` on success; second dirt-simple watchdog alerts if that file ages past 15min.

## MAJOR

### M1. tail-logger seen-ids.json corruption at startup (§5.4, §4.4)
If JSON truncated mid-write from prior crash, parse raises, script exits non-zero, next run repeats — but offset + dedup set both lost. Either infinite re-processing (dup proposals) or silence (crash loop). **Fix:** on parse error, rename to `.corrupt-$ts`, start with empty seen_ids + offset=EOF, fire dead-man.

### M2. Log rotation breaks tail-logger offset (§5.4)
`last_offset_bytes` meaningless after logrotate — new agent.log small, offset huge, reads past EOF forever. All inbounds invisible. **Fix:** stat inode before seek; if inode changed or size < offset, reset to 0 and log rotation event.

### M3. `identify-sender` roster.json load failure → silent denial (§7)
If roster.json truncated during owner edit, identify-sender returns `unknown` → dispatcher declines employee → sick-call silently lost. **Fix:** distinguish "load failed" (exit 2, fire dead-man, queue) from "loaded OK but unknown."

### M4. Backup tar excluding files on perm errors (§5.6)
tar czf with mixed-owner inputs → "Cannot open" stderr, exit 2. Script has no `set -e`, no exit-code check. Backup proceeds silently incomplete. **Fix:** `set -euo pipefail`, capture tar stderr, verify expected files in tarball before gpg, alert if missing.

### M5. gpg/S3 silent failure chain (§5.6)
gpg fails → script continues → rm deletes unencrypted original → S3 syncs stale .gpg files. **Fix:** check gpg exit code; round-trip decrypt test; only delete plaintext after; abort S3 on prior error.

### M6. Kill-switch `|| true` swallows notification failure (§5.7)
`shift-agent-notify-owner "..." || true` — notify fails, owner thinks agent live, employees keep texting dead service. **Fix:** drop `|| true`; on failure, write `agent_state_change_NOTIFICATION_FAILED` + exit non-zero.

### M7. Reconciler re-sends on boot without idempotency key (§12.1)
If send happened but log write failed AND pending.json didn't update — reconciler re-sends. **Fix:** reconciler requires human confirmation for `approved` older than N min, not auto-fire.

### M8. Proposal status transition multi-step non-atomic (§9)
`approved → sent` = (a) pending update, (b) log append, (c) counter increment. Crash between any two leaves inconsistent state. **Fix:** add intermediate `approved_sending` status with `attempted_ts`; reconciler reasons about age.

### M9. message_id synthesis collision on identical-text-same-second (§5.4)
"synthesize from ts + msg hash" — two employees sending "sick" in same second get same id, second dedup'd as seen, sick-call invisible. **Fix:** include sender phone in hash; log WARN when synthesis used (indicates gateway format change).

## MINOR

- **m1.** Disabled.flag check non-transactional (§5.2 step 3) — operator disables between step 3 and 8, message still sends. Re-check immediately before POST.
- **m2.** OpenRouter health check swallows jq/curl errors (§5.5) — `set +e` → bad checks pass. Capture both exit codes; explicit numeric test.

## Highest-consequence findings
- **B3** (dead-man can't self-notify when bridge dies) — the classic bootstrap paradox. Mandatory out-of-band channel is the only fix.
- **B2** (double-send on crash) — corrupts candidate experience + trust. Attempted-before-POST log is the discipline.
- **M3** (silent denial on roster edit) — customer shoots themselves in the foot just by trying to fix a typo.
