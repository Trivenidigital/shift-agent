**Drift-check tag:** extends-Hermes

# Flyer24 Batch - Manual Status Normalization (2026-05-27)

## Hermes-first checklist
1. Receive inbound WhatsApp text/media and sender block -> [Hermes]
2. Resolve sender identity + chat context -> [Hermes]
3. Detect Flyer status-check intent vs revision -> [net-new]
4. Pick status reply path for manual queue reason codes -> [net-new]
5. Emit deterministic audit reason for status responses -> [net-new]
6. Detect generation failures that already queued manual review -> [net-new]
7. Send customer-facing status/update copy via bridge -> [Hermes]

Effort estimate covers net-new steps 3-6 only.

## Batch issues (target 5)
1. `reason_code` comparisons are case/whitespace sensitive in cf-router status branches.
2. Status handling duplicates source-edit reason routing logic in two hook branches.
3. Audit reason selection duplicates the same brittle literal condition across branches.
4. Manual-review queued detection after generation failure misses detail strings that only include `reason_code=source_edit_provider_unavailable`.
5. No focused tests pin normalized reason-code behavior across both status code paths.

## Implementation notes
- Add shared normalization/predicate helpers in `src/plugins/cf-router/actions.py`.
- Use a single helper in `src/plugins/cf-router/hooks.py` to choose status reply + audit reason.
- Keep behavior unchanged for non-source-edit manual reasons.
- Extend targeted routing tests first (RED -> GREEN).

## Verification
- `python3 -m py_compile` on touched Python files
- focused pytest for `tests/test_cf_router_flyer_routing.py`
- `git diff --check`
