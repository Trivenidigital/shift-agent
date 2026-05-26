**Drift-check tag:** extends-Hermes

# Hermes-first checklist

1. Build Flyer cockpit billing readiness + visibility signals.
- [Hermes] WhatsApp ingress, identity, script dispatch, JSON state substrate, audit transport.
- [net-new] Flyer cockpit read-only policy fields (billing readiness, provider credential posture, payment-state visibility).

2. Enforce provider-neutral fail-closed behavior without live payment mutations.
- [Hermes] Connector substrate + MCP-first payment posture.
- [net-new] Local readiness classification and customer/operator-safe copy for missing provider config.

3. Add deterministic regression tests.
- [Hermes] Existing pytest harness + router/unit test framework.
- [net-new] New billing health + summary/customer row assertions.

## Batch issues (6)

1. `/flyer/health` billing provider could report green with checkout templates even when Stripe/Razorpay credentials were missing.
2. Billing provider value was not normalized in health view (`" Stripe "` drift risk).
3. Unsupported billing provider values were not surfaced as explicit red config gaps in health output.
4. Cockpit summary lacked payment-state breakdown for payment-pending customers (checkout missing vs ready).
5. Cockpit customer rows lacked billing/payment lifecycle fields needed for operator triage.
6. Regression coverage did not pin the above billing readiness + visibility contracts.
