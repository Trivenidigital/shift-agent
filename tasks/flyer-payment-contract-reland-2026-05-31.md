**Drift-check tag:** extends-Hermes

# Flyer Payment Contract Reland - 2026-05-31

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Payment routing / state | Existing Flyer payment state, account activation, guest-order activation, and credential-readiness catalog already own the surface. | Extend the existing contract; do not add a provider API client or new payment substrate. |
| Provider integrations | Hermes/MCP posture is connector-first for money rails. | Add Razorpay connector metadata parity only; no live credential or payment write. |
| Customer/account state | Existing JSON state and activation helpers own state mutation and audit. | Normalize provider/reference inputs before existing checks and persistence. |

Awesome Hermes Agent ecosystem check: payment MCP connectors are discovery metadata here; no external skill replaces Flyer activation validation. Verdict: extend Flyer fail-closed validation and readiness catalog.

## Drift findings

- PR #307 was dirty against current `main` due stale report/doc churn.
- Current `main` still confirmed unknown providers in `activation_event_state`.
- Current `main` still treated whitespace/case payment references as distinct during account activation.
- Guest-order activation already trimmed references, but did not normalize provider casing/spacing.
- Razorpay MCP candidate metadata was absent from the readiness catalog.

## Slice

- [x] Add RED tests for provider fail-closed validation, currency fail-closed validation, account reference normalization/dedupe, guest-order provider/reference normalization, and Razorpay MCP metadata.
- [x] Reland only narrow code/test changes from PR #307; omit stale `tasks/flyer24-hackathon-latest-report.md` churn.
- [x] Run focused suites, reviewer pass, and broad/full tests.
- [ ] PR, merge, deploy.

## Verification so far

- RED focused: `python -m pytest tests/test_flyer_payment_state.py tests/test_flyer_onboarding.py::test_account_activation_normalizes_provider_and_payment_reference tests/test_flyer_onboarding.py::test_account_activation_duplicate_reference_compares_normalized_values tests/test_flyer_guest_order.py::test_guest_order_activation_normalizes_provider_and_payment_reference tests/test_credential_readiness.py::test_payment_mcp_candidates_include_stripe_and_razorpay -q` -> `6 failed, 2 passed`.
- GREEN focused after implementation: same command -> `8 passed`.
- Reviewer pass: local Claude Code money-safety, Hermes/MCP drift, and integration/regression reviewers all approved. Low-risk coverage gaps were addressed with manual, Razorpay, padded-currency, and guest duplicate-normalization tests.
- GREEN focused after reviewer coverage: `python -m pytest tests/test_flyer_payment_state.py tests/test_flyer_onboarding.py::test_account_activation_normalizes_provider_and_payment_reference tests/test_flyer_onboarding.py::test_account_activation_duplicate_reference_compares_normalized_values tests/test_flyer_guest_order.py::test_guest_order_activation_normalizes_provider_and_payment_reference tests/test_flyer_guest_order.py::test_guest_order_duplicate_reference_compares_normalized_values tests/test_credential_readiness.py::test_payment_mcp_candidates_include_stripe_and_razorpay -q` -> `12 passed`.
- GREEN broad payment/readiness: `python -m pytest tests/test_flyer_payment_state.py tests/test_flyer_guest_order.py tests/test_flyer_onboarding.py tests/test_credential_readiness.py -q` -> `122 passed`.
- GREEN full suite: `python -m pytest -q` -> `2796 passed, 867 skipped, 40 warnings`.
- Diff hygiene: `git diff --check` -> clean.
