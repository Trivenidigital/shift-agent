**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Billing Provider Health Contract (2026-05-28)

## Hermes-first checklist
1. Collect runtime/env/config posture for Flyer billing provider -> [Hermes] existing config/env substrate and cockpit `/flyer/health` endpoint.
2. Evaluate checkout-provider readiness and fail-closed customer-copy posture -> [net-new] Flyer-specific policy shaping in health details.
3. Report MCP-first connector posture for Stripe/Razorpay in read-only operator visibility -> [net-new] metadata/detail shaping only.
4. Mutate payment/account/quota state -> [Hermes] existing account/payment scripts; out of scope for this batch.

## Batch scope (6 related issues)
1. Normalize unsupported `payment_provider` values and expose both raw + effective provider in health metadata.
2. Mark billing provider posture degraded when effective provider is `stripe` but `STRIPE_SECRET_KEY` is missing/placeholder.
3. Mark billing provider posture degraded when effective provider is `razorpay` but `RAZORPAY_KEY_ID` or `RAZORPAY_KEY_SECRET` is missing/placeholder.
4. Add explicit provider-credential readiness fields in billing `model_config` (`provider_credentials_ready`, `provider_credential_detail`).
5. Expand operator checklist copy with connector-safe MCP-first guidance for Stripe/Razorpay when credentials/templates are incomplete.
6. Add focused tests for provider normalization + credential readiness + fail-closed detail copy.

## Risk
- Low: read-only `/flyer/health` visibility contract + tests only. No runtime payment mutation, checkout creation, webhook changes, or customer/account/quota state writes.

## Verification
- `pytest -q web/backend/tests/test_flyer_health.py -k billing_checkout_provider`
- `python3 -m py_compile web/backend/app/routers/flyer.py web/backend/tests/test_flyer_health.py`
- `git diff --check`
