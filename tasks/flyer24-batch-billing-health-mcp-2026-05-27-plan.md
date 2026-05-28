# Flyer24 Batch: Billing Health MCP Readiness (2026-05-27)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist

1. Inspect payment-health surface in Cockpit API (`/flyer/health`) and tests. `[net-new]`
2. Inbound routing, sender identity, account/payment state persistence, and audit substrate. `[Hermes]`
3. Billing provider readiness signaling in Cockpit (read-only visibility). `[net-new]`
4. MCP/provider connector awareness metadata (Stripe/Razorpay MCP-first posture) in read-only health output. `[net-new]`
5. Fail-closed customer copy behavior for missing checkout templates (already existing behavior). `[Hermes]`
6. Add regression tests for readiness visibility and mismatch detection. `[net-new]`

Net-new effort is limited to read-only operator visibility and tests; no payment mutation flow changes.

## MCP-first verdict (payment)

- Stripe and Razorpay remain provider/connector surfaces.
- This batch adds no custom payment API client and no live checkout/refund/subscription mutation.
- Scope is read-only health metadata and fail-closed readiness signaling so operators do not assume billing is live when config/provider posture is incomplete.

## Batch issues target (6)

1. `billing_checkout_provider` does not show supported providers; operator cannot verify provider-policy constraints quickly.
2. Health view does not expose connector-ready env key name for configured provider.
3. Health view does not flag when configured provider differs from expected plan catalog currency mix (USD-only vs INR-capable plan tiers).
4. Health view does not include MCP-first provider notes (Stripe MCP / Razorpay MCP) for operator action.
5. Health view does not include explicit configuration checklist when checkout templates are missing/partial.
6. No focused tests pinning these billing health visibility contracts.

## Implementation plan

1. Add RED tests in `web/backend/tests/test_flyer_health.py` for the six gaps above.
2. Extend `_flyer_provider_components()` billing provider block with provider metadata and checklist fields.
3. Keep severity/fail-closed behavior unchanged for missing templates except where richer detail is needed.
4. Run focused pytest, `py_compile`, and `git diff --check`.
5. Commit, push, open PR, self-review, and update hackathon running report.
