**Drift-check tag:** extends-Hermes

# Flyer24 Batch SLA Watchdog Hygiene Plan (2026-05-26)

## Hermes-first checklist
1. Read Flyer projects/manual rows from JSON state: `[Hermes]` existing per-VPS JSON substrate.
2. Detect stale manual-queue rows and throttle repeat paging: `[net-new]` Flyer product policy in `flyer-source-edit-sla-watchdog`.
3. Persist throttle state for row-level alert/customer update cadence: `[net-new]` Flyer watchdog state hygiene only.
4. Emit audit entries + owner/customer notifications: `[Hermes]` existing safe_io notify + decisions log chokepoints.
5. Keep operator visibility aligned with current queue reality: `[net-new]` cleanup/pruning logic + tests.

Net-new effort is limited to watchdog state hygiene/guardrails and regression tests; no routing, payment, quota, or customer lifecycle mutation.

## Batch scope (5-6 related issues)
1. Prune stale `project_alerts` keys when queue rows no longer exist.
2. Prune superseded keys when same `project_id+reason_code` is requeued with newer `queued_at`.
3. Return pruning telemetry (`pruned_alert_rows`) in watchdog output for operator visibility.
4. Record pruning count in SLA audit rows for historical evidence.
5. Fail-closed normalize repeat/customer repeat minutes (`<=0` treated as no throttle) to avoid ambiguous spam/mis-throttle behavior.
6. Add regression tests for all above without changing customer/payment/runtime state.

## Verification
- `python3 -m py_compile src/agents/flyer/scripts/flyer-source-edit-sla-watchdog`
- `pytest -q tests/test_flyer_source_edit_sla_watchdog.py`
- `git diff --check`
