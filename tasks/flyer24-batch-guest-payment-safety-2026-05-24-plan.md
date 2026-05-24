**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Guest Payment Safety

## Hermes-first checklist
1. Guest order intake/request capture -> [Hermes] WhatsApp ingress/routing already handled.
2. Persist guest-order payment intent/state -> [Hermes] JSON state + lock/write helpers already present.
3. Payment provider/API execution -> [net-new] not doing live API calls; only local provider-neutral contract enforcement.
4. Activation idempotency + duplicate payment reference safety -> [net-new] Flyer payment policy logic.
5. Customer fail-closed copy when checkout/provider info is missing or mismatched -> [net-new] Flyer business logic copy.
6. Operator-visible verification/tests for money-adjacent safety -> [net-new] deterministic tests.

Net-new scope only: activation contract hardening + tests. No Hermes substrate rewrite.

## Batch issues (6)
1. Guest-order activation dedupe ignores provider scope.
2. Guest-order idempotent replay accepts mismatched payment reference once status is paid.
3. Guest-order activation lacks amount mismatch validation.
4. Guest-order activation lacks currency mismatch validation.
5. Guest-order activation lacks provider validation/auditable capture in state.
6. Guest-order CLI cannot pass provider/amount/currency activation evidence, limiting safe operator workflows.

## Implementation
- Add payment metadata fields on guest orders needed for replay/mismatch checks.
- Extend activation logic to enforce provider+reference duplicate policy and mismatch fail-closed checks.
- Extend CLI args and config loading for provider/currency-aware activation input (no live API calls).
- Add/extend pytest coverage for idempotency, duplicate provider refs, amount/currency mismatches, and fail-closed behavior.

## Verification
- python3 -m py_compile for touched Python files
- pytest focused on flyer guest-order and related activation tests
- git diff --check

## Risk
Money-adjacent runtime behavior change. Open PR for operator review; do not merge/deploy in this batch.
