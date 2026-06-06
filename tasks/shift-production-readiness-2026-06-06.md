# Shift Production Readiness Evidence - 2026-06-06

**Drift-check tag:** Hermes-native

**New primitives introduced:** none. This is a current-state evidence report.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Shift sick-call routing | Existing `dispatch_shift_agent`, `identify-sender`, roster state, proposal scripts, audit chain | Reuse deployed Hermes/Shift substrate; no custom primitive needed |
| Daily Brief control tower | Existing `send-daily-brief`, timer, sentinel, dry-run smoke | Reuse deployed Daily Brief gate |
| Runtime health | Existing `shift-agent-smoke-test.sh`, `pilot-readiness-check`, systemd timers, WhatsApp bridge health | Use existing gates as authority |
| Operator out-of-band alerts | Existing Pushover check in `pilot-readiness-check` and smoke channel probe | Runtime config must be provisioned with real keys |

Awesome-Hermes-Agent ecosystem check: no turnkey Shift production bundle replaces the repo-native Shift/Daily Brief readiness gates; continue using Hermes messaging, skills, cron, and local scripts.

## Current result

Shift/Daily Brief code and runtime health passed. The customer pilot gate is still blocked by operator-owned Pushover credentials:

```text
Production pilot readiness: BLOCKED
Passed: 16  Failed: 1
FAIL alerting.pushover: Pushover credentials are MUTED_ (dev/rehearsal) - provision real keys before a customer pilot so owner alerts are delivered
```

## Evidence

Clean worktree baseline from `C:\projects\sme-agents-shift`:

```text
python -m pytest tests/test_pilot_readiness_check.py tests/test_shift_smoke_config_load.py tests/test_shift_reconcile.py tests/test_shift_fsck.py tests/test_daily_brief_script.py tests/test_daily_brief_schemas.py tests/test_daily_brief_log_source.py -q
61 passed, 20 skipped
```

Live runtime probe on `main-vps`:

```text
hermes-gateway: active/enabled
WhatsApp bridge: {"status":"connected","queueLength":0}
shift-agent-tail-logger.timer: enabled/active
shift-agent-health.timer: enabled/active
shift-agent-health-watchdog.timer: enabled/active
shift-agent-backup.timer: enabled/active
shift-agent-fsck.timer: enabled/active
send-daily-brief.timer: enabled/active
catering-pattern-report.timer: enabled/active
send-routing-accuracy-summary.timer: enabled/active
```

Live smoke:

```text
=== All smoke checks passed ===
send-daily-brief --force --dry-run passed
catering-pattern-report --dry-run passed
EOD snapshot timer fresh (14h old)
Daily Brief timer fresh (5h old)
Pushover credentials muted - smoke skipped channel probe as dev/rehearsal
```

## Required operator action

Provision real Pushover credentials in `/root/.hermes/.env` or the configured alerting source:

- `pushover_user_key`
- `pushover_app_token`

Then rerun:

```bash
ssh main-vps '/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/pilot-readiness-check --text' > .ssh_pilot_readiness.txt 2>&1
```

Read `.ssh_pilot_readiness.txt`. Shift/Daily Brief can be called production-pilot ready only when the readiness output is `READY` with zero failed rows and the Pushover smoke channel probe is not skipped.

## Code decision

No Shift-owned source patch is warranted from this evidence. The readiness gate is correctly blocking on a runtime secret/config value. Changing Shift code would risk hiding the real pilot blocker.
