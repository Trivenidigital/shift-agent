# Flyer24 Batch Plan - Recovery Ack Guardrails (2026-05-25)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Parse cf-router audit rows and classify recovery incidents -> **[Hermes]** existing log substrate + Flyer recovery classifier.
2. Decide whether to send customer recovery ack -> **[net-new]** Flyer deterministic policy guardrails.
3. Send WhatsApp message and persist ack audit/state -> **[Hermes]** existing bridge + ndjson + recovery state lock flow.
4. Keep aged/closed/malformed incidents from triggering proactive customer copy -> **[net-new]** fail-closed policy.
5. Regression tests for ack gating behavior -> **[net-new]** deterministic unit coverage.
6. Clean dead-path stale reservation helper branch -> **[net-new]** correctness/readability hardening.

Net-new effort applies only to steps 2, 4, 5, 6.

## Batch issues (6)
1. `ack_send_decision()` can approve a customer ack for incidents in non-open states.
2. `ack_send_decision()` can approve a customer ack when `chat_id` is blank.
3. `ack_send_decision()` has no age gate, so old incidents can still trigger proactive copy.
4. `_process_customer_acks()` may suppress non-actionable incidents (closed/missing chat/stale) instead of treating them as terminal no-send.
5. No test coverage for closed-incident/missing-chat/stale-age no-send behavior.
6. `finalize_stale_reservations()` contains a duplicate dead `continue` branch.

## Drift reads performed before code
- `src/agents/flyer/recovery.py`
- `src/agents/flyer/scripts/flyer-recovery-watchdog`
- `tests/test_flyer_recovery.py`
- `tests/test_flyer_recovery_watchdog.py`

## Implementation outline
- Add explicit no-send gates in `ack_send_decision` for:
  - incident status != `open`
  - blank `chat_id`
  - incident `last_seen` older than `ack_cooldown`
- Treat these new gates as terminal in watchdog ack loop to avoid pointless suppressed rows.
- Add unit tests for the three guardrails plus keep existing behavior unchanged for eligible incidents.
- Remove duplicate `continue` in `finalize_stale_reservations`.

## Verification
- `python3 -m py_compile src/agents/flyer/recovery.py src/agents/flyer/scripts/flyer-recovery-watchdog`
- `pytest -q tests/test_flyer_recovery.py tests/test_flyer_recovery_watchdog.py`
- `git diff --check`

## Risk and merge policy
- Risk: low (recovery no-send guardrails + tests; no payment/account/quota/live state mutation).
- Merge policy: merge/deploy eligible if checks pass and self-review finds no blocker.
