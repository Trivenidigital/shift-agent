**Drift-check tag:** extends-Hermes

# Flyer24 Batch Watchdog Failure Hardening Plan (2026-05-26)

## Hermes-first checklist
1. Detect stale/manual queue conditions: `[Hermes]` existing Flyer watchdog + JSON state substrate.
2. Trigger watchdog failure notification path via systemd `OnFailure`: `[Hermes]` existing service chaining.
3. Guard unit startup prerequisites (`.env`, notify binary): `[net-new]` hardening in failure unit config.
4. Keep non-configured/missing-binary states fail-closed without stuck failed unit: `[net-new]` service policy + explicit precheck.
5. Validate deploy/smoke static expectations for failure unit: `[net-new]` regression tests only.
6. Document/report operator-facing root cause and fix scope: `[net-new]` batch report update.

Net-new effort is limited to unit/service hardening and static test coverage; no customer messaging, payment, quota, or runtime state mutation.

## Batch scope (6 related issues)
1. Make failure unit `EnvironmentFile` optional to avoid pre-exec NOTCONFIGURED hard fail.
2. Add explicit `ExecStartPre` binary presence check for `shift-agent-notify-owner`.
3. Keep `SuccessExitStatus=5 6` while ensuring those statuses are reachable only after precheck passes.
4. Add static regression assertions for optional env-file + ExecStartPre in failure unit test.
5. Extend deploy/smoke static verification expectations for failure unit guards.
6. Update hackathon latest report with PR queue classification and this batch evidence.

## Verification
- `python3 -m py_compile tests/test_flyer_source_edit_sla_watchdog.py`
- `pytest -q tests/test_flyer_source_edit_sla_watchdog.py -k "deploy_installs_and_enables_sla_watchdog_timer"`
- `git diff --check`
