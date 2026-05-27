# Flyer24 Batch Plan - Manual Reason Canonicalization (2026-05-27)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Inbound WhatsApp routing and sender resolution -> [Hermes]
2. Project/manual queue state storage + locks -> [Hermes]
3. Manual-review reason classification/readability in Flyer queue and status replies -> [net-new]
4. Cockpit health/manual-queue visibility derived from Flyer reason codes -> [net-new]
5. Customer-facing manual-queue status copy for legacy/unclassified reason rows -> [net-new]
6. Payment/provider credentials handling -> [Hermes] (no live credential or checkout mutation in this batch)

Net-new effort is limited to reason canonicalization helpers, status-copy fallback behavior, and regression tests.

## Batch issue set (target 5-6)
1. Legacy/manual rows with empty or unclassified `reason_code` lose provider-readiness signal in manual queue summaries.
2. Mixed-case and token-variant reason strings are treated as distinct values in queue reason histograms.
3. Legacy reason phrases embedded in `manual_reason` are not canonicalized to typed reason families.
4. Customer status reply for manual-review rows falls back to generic copy when canonical reason can be inferred.
5. Source-edit/manual queue stale visibility under-counts canonical blocker types when row reason data is noisy.
6. Missing regression coverage for canonical reason extraction from `reason_code` + `reason` + `detail`.

## Implementation notes
- Add a shared Flyer helper to canonicalize manual-review reasons from `reason_code`, then fallback to `reason` and `detail` markers.
- Use the helper in `agents.flyer.manual_queue` queue row shaping and in cf-router manual status reply selection.
- Keep behavior fail-closed: unknown patterns remain `unclassified`.
- Add focused tests in `tests/test_flyer_manual_queue.py` and `tests/test_cf_router_plugin.py`.

## Verification
- `python3 -m py_compile` on touched Python files.
- Focused pytest for manual queue and cf-router manual status behavior.
- `git diff --check`.

## Merge/deploy policy
- This batch is low-risk (classification + copy selection only, no payment/runtime mutation).
- If PR checks are green and self-review has no blockers, it is merge/deploy eligible under autonomous policy.
