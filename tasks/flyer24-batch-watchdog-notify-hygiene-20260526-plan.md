# Flyer24 batch plan: watchdog notify hygiene (2026-05-26)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Detect stale source-edit manual queue rows from Flyer state -> **[Hermes]** existing JSON state + watchdog substrate.
2. Decide stale-row alert outcome and write audit row -> **[Hermes]** existing alert/audit schema path.
3. Send owner page for stale rows -> **[Hermes]** existing `shift-agent-notify-owner` chokepoint.
4. Handle owner-notify unavailable/misconfigured environments safely -> **[net-new]** watchdog/systemd fail-closed policy hygiene.
5. Run customer progress updates independent of owner channel health -> **[Hermes]** existing customer-update path.
6. Keep systemd health signal actionable (avoid sticky false-red from notifier config gaps) -> **[net-new]** unit + script exit-code posture.

Net-new scope in this batch: only steps 4 and 6.

## Batch issues (6)
1. `flyer-source-edit-sla-watchdog-failure.service` enters failed state on missing notifier config (exit 5/6), creating persistent `systemctl --failed` noise.
2. Failure notifier service currently treats dependency/config gaps as hard unit failures even though it is a secondary escalation path.
3. Main watchdog service requires executable `shift-agent-notify-owner` in `ExecStartPre`, preventing watchdog execution in degraded/no-notifier setups.
4. Watchdog exits non-zero (`exit_code=6`) on owner-notify failure, causing `OnFailure` loops instead of advisory degraded outcome.
5. No explicit degraded-but-recorded watchdog outcome for owner-notify failure while customer updates/audit still succeed.
6. Missing regression coverage for degraded owner-notify behavior and systemd unit guardrail semantics.

## Files in scope
- `src/agents/flyer/scripts/flyer-source-edit-sla-watchdog`
- `src/agents/flyer/systemd/flyer-source-edit-sla-watchdog.service`
- `src/agents/flyer/systemd/flyer-source-edit-sla-watchdog-failure.service`
- `tests/test_flyer_source_edit_sla_watchdog.py`

## Implementation outline
- Add/adjust tests first for degraded owner-notify behavior and deploy unit semantics.
- Change watchdog notify-failure branch to fail-closed customer copy while returning advisory/degraded success code with audit outcome.
- Remove hard `ExecStartPre` dependency on notifier binary from main watchdog unit.
- Make failure-notifier systemd unit non-sticky for expected config/dependency exits.

## Verification
- `python3 -m py_compile src/agents/flyer/scripts/flyer-source-edit-sla-watchdog`
- `pytest -q tests/test_flyer_source_edit_sla_watchdog.py`
- `git diff --check`
