# Pushover Un-mute Runbook — PREPARED 2026-07-20, EXECUTION NOT AUTHORIZED

> **STATUS: PREPARE-ONLY.** Pushover remains muted (`MUTED_20260501T161536Z`).
> Execution requires separate explicit reviewer/operator authorization and must NOT
> be bundled with any deploy, canary, or planning work. This runbook is the final
> verification gate for the alarm layer shipped 2026-07-11 (#607/#608).

## 0. Preconditions
- [ ] Operator has provisioned a Pushover account: USER key + an application API
      token for shift-agent. (The pre-mute key predates every surviving on-box
      backup and is NOT recoverable — fresh credentials required.)
- [ ] Confirm current mute marker: `grep -i pushover /root/.hermes/.env` (expect the
      MUTED sentinel) and note the exact variable names the notify path reads
      (`shift-agent-notify-owner` — read the script's env consumption FIRST; do not
      assume names).
- [ ] Back up the env TARGET file (never sed the symlink):
      `cp /root/.hermes/.env /root/.hermes/.env.bak-pushover-unmute-<ts>`.

## 1. Credential installation (the only state change)
- [ ] Edit `/root/.hermes/.env` (the symlink TARGET) replacing the MUTED sentinel
      with the real user key + token, exactly per the variable names verified in 0.
- [ ] `systemctl restart hermes-gateway` is NOT required unless the notify path
      reads env only at gateway start — verify the script reads env per-invocation
      (expected: subprocess reads at run time → no restart needed).

## 2. One controlled alert (delivery proof)
- [ ] Send exactly ONE test alert via the production chokepoint:
      `shift-agent-notify-owner --priority 0 --title "Pushover un-mute verification" "controlled test <ts>"`
- [ ] Confirm delivery on the operator device (acknowledgment).
- [ ] Confirm the §12b audit pair in decisions.log: `owner_alert_dispatched` +
      `owner_alert_delivered` for this alert (shipped in #622).

## 3. Duplicate suppression
- [ ] Re-send the SAME title/body within the dedup window; confirm the notify-dedup
      layer suppresses the duplicate (check `state/notify-dedup.json` mutation +
      absence of a second device notification) and that suppression is visible in
      the audit trail rather than silent.

## 4. Rate-limit behavior
- [ ] Review Pushover's account rate/quota status page after the test (7,500/mo
      free-tier app limit); confirm the health-check throttling (census C-7/S-10
      fixes) means normal operation stays far below quota. Do NOT stress-test.

## 5. Full-cycle watch (the actual gate, per census ruling)
- [ ] Watch ONE full daily cycle: the daily-brief page must fire ONLY if a brief is
      genuinely missing (A2 fix proof), and the dead-letter reader (A3) must deliver
      anything that lands in notify-failed.log. No false P1 pages across the cycle.
- [ ] Only after this clean cycle may "deployed" be upgraded to
      "alarm-layer verified end-to-end" in the production-status matrix.

## 6. Rollback to muted
- [ ] Restore: `cp /root/.hermes/.env.bak-pushover-unmute-<ts> /root/.hermes/.env`
      (or re-set the MUTED sentinel value). Verify with a probe alert that delivery
      is suppressed again and dead-lettering resumes to notify-failed.log.
- [ ] Record the rollback + reason in the approvals log.

## Evidence to record on execution
Timestamped: credential-install diff summary (no secrets), the controlled alert's
dispatched/delivered audit rows, device acknowledgment, dedup-suppression proof,
quota snapshot, full-cycle result, and the approvals-log row naming the authorizer.
