**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Source-Edit Watchdog Visibility (2026-05-26)

## Hermes-first checklist
1. Detect stale manual queue source-edit rows from Flyer project state: **[Hermes]** existing JSON state + queue status already present.
2. Route/notify operator and customer channels: **[Hermes]** existing bridge + notify helpers.
3. Surface provider-readiness context and actionable stale-row diagnostics: **[net-new]** add deterministic reporting fields/copy only.
4. Preserve fail-closed behavior (no credentials -> no paid action): **[Hermes]** existing fail-closed queueing + no payment mutation.
5. Test idempotent throttle/state behavior for new visibility fields: **[net-new]** focused pytest additions.

Net-new scope only: watchdog/manual-queue observability and copy fidelity; no checkout/provider/payment/account state mutation.

## Batch issues (target 6)
1. Watchdog alert copy omits queued vs in-progress split, slowing operator triage.
2. Watchdog alert copy omits oldest queued timestamp, making SLA age auditing harder.
3. Watchdog output omits per-reason counts even when multiple reason codes are monitored.
4. Customer-update telemetry is buried; watchdog result lacks aggregate sent/failed/skipped counts.
5. Manual queue triage output lacks manual status histogram (`queued` vs `in_progress`) for staffing decisions.
6. Manual queue triage output lacks oldest queued timestamp per customer group.

## Verification plan
- Add tests first in `tests/test_flyer_source_edit_sla_watchdog.py` and `tests/test_flyer_manual_queue.py`.
- Implement minimal code changes in watchdog/manual_queue.
- Run:
  - `python3 -m py_compile src/agents/flyer/scripts/flyer-source-edit-sla-watchdog src/agents/flyer/manual_queue.py`
  - `pytest -q tests/test_flyer_source_edit_sla_watchdog.py tests/test_flyer_manual_queue.py`
  - `git diff --check`
