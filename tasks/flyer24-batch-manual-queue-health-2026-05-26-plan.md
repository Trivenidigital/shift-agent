**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Read Flyer project/manual queue state -> **[Hermes]** existing JSON state + `list_manual_queue` helpers.
2. Compute operator diagnostics from queued rows -> **[net-new]** extend read-only aggregation in cockpit health helper.
3. Render provider health detail copy -> **[net-new]** deterministic wording in health endpoint payload.
4. Expose billing/source-edit readiness to cockpit -> **[Hermes]** existing provider component envelope.
5. Verify with focused tests only -> **[Hermes]** existing pytest suite and py_compile checks.

## Batch issues (related)
1. Manual queue impact omits stale counts per reason code.
2. Manual queue impact omits oldest age per reason code.
3. Source-edit impact omits source-edit-specific stale count/age.
4. Source-edit provider detail only references source-edit queue count and hides mixed-reason backlog context.
5. Source-edit provider detail does not surface stale threshold context for operator triage.
6. No focused tests pin the expanded manual queue impact + mixed-backlog detail contract.

## Files
- `web/backend/app/routers/flyer.py`
- `web/backend/tests/test_flyer_health.py`
- `tasks/flyer24-hackathon-latest-report.md`

## Verification
- `python3 -m py_compile web/backend/app/routers/flyer.py web/backend/tests/test_flyer_health.py`
- `pytest -q web/backend/tests/test_flyer_health.py -k "manual_queue_impact or source_edit_detail"`
- `git diff --check`

## Risk
Low: read-only health payload and tests; no customer messaging, no payment mutation, no manual queue disposition changes.
