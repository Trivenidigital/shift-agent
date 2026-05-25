# Flyer24 Batch: Source-Edit Preflight + Cf-Router Reason Parity (2026-05-25)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist

### Step map
1. Inbound WhatsApp/cf-router intercept execution for Flyer requests -> `[Hermes]`
2. Flyer source-edit preflight helper call and provider readiness evaluation -> `[net-new]` (Flyer product policy)
3. Manual-review reason-code mapping for triage -> `[net-new]` (Flyer product policy)
4. Cf-router audit row schema acceptance of emitted Flyer reason literals -> `[net-new]` (Flyer schema contract)
5. Automated regression tests and static parity checks -> `[net-new]` (repo quality gates)
6. Customer messaging transport and audit append -> `[Hermes]`

### Scope decision
- Keep Hermes-owned ingress/routing substrate unchanged.
- Patch only Flyer policy/schemas/tests needed to restore fail-closed behavior and schema parity.
- No payment API calls, no provider credential mutation, no runtime state mutation.

## Batch issues (5)
1. `CfRouterIntercepted.reason` missing `flyer_sample_prompt_requested` literal used in hooks.
2. Source-edit preflight can return `ok=True` when provider resolves to `manual_review`, causing fail-open.
3. Preflight maps only one unsupported-media detail string and can misclassify PDF/non-image failures.
4. Provider-unavailable detail copy does not consistently include configured provider key guidance.
5. No direct test pin for manual-review provider policy resolving to fail-closed `source_edit_provider_unavailable`.

## Implementation plan
1. Add/align failing tests first for reason literal parity and manual-review provider fail-closed behavior.
2. Update `CfRouterIntercepted.reason` union to include emitted Flyer literal.
3. Harden `flyer_source_edit_preflight()` classification/messaging for:
   - manual-review sentinel provider
   - provider key missing/placeholder
   - PDF/non-image and missing-reference distinctions
4. Run focused verification: `py_compile`, targeted Flyer pytest suite, `git diff --check`.
5. Commit, push, open PR, and self-review with risk classification.

## Risk
- Risk level: low-to-medium (Flyer routing policy + schema union + customer-facing fail-closed copy).
- Merge policy: non-payment and no account/quota mutation; eligible for autonomous merge only if green and review-clean.
