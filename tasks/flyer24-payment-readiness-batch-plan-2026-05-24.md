# Flyer24 payment readiness batch plan (2026-05-24)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Receive onboarding/plan-change/quick-flyer payment request text and route to Flyer scripts. **[Hermes]**
2. Persist customer/order state with JSON + safe_io semantics. **[Hermes]**
3. Build provider checkout link string from configured template. **[net-new]** (Flyer business logic)
4. Emit customer-facing payment-pending copy when link missing/misconfigured. **[net-new]**
5. Activate account from payment reference with idempotency and mismatch guards. **[net-new]**
6. Emit audit trail rows for activation outcomes. **[Hermes]** substrate + existing Flyer audit entry usage.

Net-new scope only: steps 3-5.

## Batch issues (target 6)
1. Invalid checkout template placeholders raise runtime formatting errors instead of fail-closed empty link.
2. Onboarding payment-pending copy hardcodes "Stripe/Razorpay" even when provider is manual/other.
3. Account plan-change payment copy does not clearly surface "link not configured" for operator readiness.
4. Activation idempotency currently replays success without verifying the target customer status is already active/trial.
5. Activation idempotency accepts replay even when expected plan mismatches current plan on the same customer record.
6. Activation idempotency accepts replay even when amount/currency mismatch on same provider reference.

## MCP-first verdict (payment)
- No Stripe/Razorpay client implementation in this batch.
- Keep provider-neutral primitives and fail-closed copy only; no live checkout creation/webhook mutation.
- This is connector-safe prep work and test hardening around existing config-driven templates.
