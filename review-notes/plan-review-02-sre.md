# Plan Review 2/5 — SRE / Production Readiness (general-purpose)

**Verdict:** 3 BLOCKERs, 4 HIGH-PRIORITY items, ship-able as friendly-design-partner beta only.

## BLOCKERS

### B1. No on-call path for overnight/weekend failures
Single dev, no pager, 6am Sunday sick-call = silence = lost trust. 
**Mitigation (fits 1h):** (a) explicit "08:00-22:00 supervised only" in runbook; (b) dead-man's switch — if gateway down AND business hours, health timer WhatsApps owner "AGENT DOWN — handle manually."

### B2. No backup of roster.json / decisions.log
Single `rm`, disk-full, bad edit → audit trail + employee contacts destroyed. 
**Mitigation (30m):** cron `rsync` to second path + nightly `tar | gpg` to second VPS or S3. Auditability is sick-call agent's value prop.

### B3. WhatsApp session backup missing
Baileys auth corrupts → lose linked-device session → owner re-pairs manually. 
**Mitigation (5m):** snapshot `/root/.hermes/baileys_auth/` (or actual path `/root/.hermes/whatsapp/session/`) into nightly backup.

## HIGH PRIORITY (48h-feasible)

### H1. Explicit SLIs/SLOs committed to customer in runbook
- Inbound capture rate: 100% (tail-logger invariant, measurable)
- Proposal latency p95: <60s from employee msg → owner proposal
- Approved-coverage delivery: <30s from approval → candidate
- Availability: "best-effort, business hours 08:00-22:00, not 24/7" — do NOT commit 9s numbers
- Weekly error budget: 2 missed sick-calls/week → post-mortem + pause

### H2. Monitoring gaps (silent-but-deadly)
- No alert on tail-logger timer dying → lose invariant silently. Check `systemctl is-active sick-call-tail-logger.timer`.
- No alert on pending-proposal aging (owner hasn't replied in 4h = operational failure).
- No alert on Baileys disconnect — gateway can be `active` in systemd but WA socket dead. Check last-successful-send timestamp, not just process liveness.
- No alert on OpenRouter 5xx / empty responses — degraded quality looks like success.
- Alerting via same WhatsApp channel = loop. Add one out-of-band: Pushover ($5 one-time) or email-to-SMS. 20 min.

### H3. Observability debt
- No correlation ID across inbound → skill → outbound. Add `message_id` as trace key in every log line.
- No structured outbound send failure vs success log beyond status field.
- Log rotation NOW (logrotate, daily, keep 30) — 10MB cap is a time bomb.

### H4. Deploy/rollback safer
- `git tag` every deploy → rollback is `git checkout <tag> && scp && restart`, not freehand.
- 10-line `smoke-test.sh` post-deploy: sends one test message end-to-end before declaring green. 30 min.

## Capacity ceiling (informational)
- Single-customer, 20 msgs/day, 3.7GB RAM = fine.
- Serialization point: Kimi-thinking reasoning ~5-10s serializes dispatcher → concurrent sick-calls queue.
- Baileys memory grows with chat history → weekly restart.
- decisions.log linear scan in approval tracker becomes O(n) painful at ~10k entries.
- Re-architect trigger: 2nd customer OR >100 msgs/day.

## Ship verdict
Ship-able as friendly-design-partner beta IF B1-B3 closed AND runbook expectations explicit: "beta, business-hours-supervised, backed up nightly, kill-switch = X." Do NOT ship as "always-on." Ship as "assisted dispatcher, human-in-loop, owner remains accountable."
