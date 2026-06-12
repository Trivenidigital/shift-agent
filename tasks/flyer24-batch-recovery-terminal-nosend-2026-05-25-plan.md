# Flyer24 Batch Plan - Recovery Terminal No-Send Hygiene (2026-05-25)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Ingest cf-router failures + maintain recovery incidents -> **[Hermes]** existing log/state substrate.
2. Decide whether customer ack is allowed -> **[net-new]** Flyer deterministic recovery policy.
3. Suppress customer sends for terminal/no-actionable incidents -> **[net-new]** fail-closed Flyer policy.
4. Persist operator-visible reason and audit row for no-send decisions -> **[net-new]** recovery observability policy.
5. Keep WhatsApp transport unchanged for eligible incidents only -> **[Hermes]** existing bridge send helper.
6. Regression coverage for terminal no-send state transitions -> **[net-new]** tests.

## Batch issues (6)
1. Terminal no-send decisions (`terminal_incident_status:*`) are skipped without persisted ack state.
2. Missing-chat incidents are skipped without persisted ack state.
3. Stale incidents are skipped without persisted ack state.
4. Invalid `last_seen` incidents are skipped without persisted ack state.
5. These incidents are re-evaluated every run with no durable terminal marker.
6. Watchdog tests do not assert terminal no-send persistence behavior.

## Implementation outline
- Add a terminal no-send reason matcher in watchdog ack loop.
- For first-seen terminal no-send decisions when `ack.status == none`, write `ack.status=suppressed` + `status_detail=<reason>` and emit `flyer_recovery_customer_ack_suppressed` audit row.
- Keep behavior fail-closed: no send attempts for these incidents.
- Add subprocess watchdog tests for stale + missing-chat terminal no-send persistence.

## Verification
- `python3 -m py_compile src/agents/flyer/recovery.py src/agents/flyer/scripts/flyer-recovery-watchdog`
- `pytest -q tests/test_flyer_recovery.py tests/test_flyer_recovery_watchdog.py`
- `git diff --check`

## Risk and merge posture
- Risk: low (recovery-state/audit observability hardening only; no payment/account/quota/customer-send mutation).
- Merge policy: merge/deploy eligible if checks pass and self-review has no blockers.
