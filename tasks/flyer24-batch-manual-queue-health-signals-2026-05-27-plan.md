# Flyer24 Batch Plan - Manual Queue Health Signals (2026-05-27)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Collect manual queue rows from Flyer state -> [Hermes] existing JSON/state substrate + `safe_io` + routing/audit.
2. Classify queue rows into actionable reason families and SLA urgency -> [net-new] Flyer-specific operator policy signals.
3. Expose health payload for Cockpit/operator checks -> [net-new] read-only Flyer health shaping.
4. Trigger customer/provider remediation decisions -> [Hermes] existing manual queue workflows + operator actions; no new connector/payment mutation.

## Batch scope (5-6 related issues)
1. Add explicit visual-QA backlog counts to `/flyer/health` manual queue impact so `visual_qa_failed` pain is first-class.
2. Add provider-readiness stale counters in health payload to isolate `source_edit_provider_unavailable` and dependency backlog risk.
3. Add `customer_update_due` aggregate counts/oldest age to surface overdue queue follow-ups.
4. Add reason-family aggregate counts in health payload to avoid over-reliance on raw reason-code strings.
5. Ensure top reason summaries prioritize stale pressure (stale-first ranking) for operator triage.
6. Add deterministic focused tests to pin these health signals and prevent regressions.

## Risk
- Low: read-only health/visibility shaping + tests; no runtime state mutation, no payment/account/quota/provider action changes.

## Verification
- `pytest -q web/backend/tests/test_flyer_health.py -k manual_queue`
- `python3 -m py_compile web/backend/app/routers/flyer.py web/backend/tests/test_flyer_health.py`
- `git diff --check`
