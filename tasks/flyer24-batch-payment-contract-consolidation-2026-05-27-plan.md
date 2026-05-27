**Drift-check tag:** extends-Hermes

# Hermes-first checklist
1. Activation event contract evaluation -> [net-new] Flyer payment-state policy logic.
2. Account activation provider/ref normalization -> [net-new] Flyer business logic on Hermes state substrate.
3. Guest-order activation provider/ref normalization -> [net-new] Flyer business logic on Hermes state substrate.
4. MCP connector candidate metadata for payment providers -> [Hermes] substrate reference; [net-new] readiness catalog row parity.
5. Tests for idempotency/mismatch/fail-closed -> [net-new] repo test coverage.

## Scope (5-6 related fixes)
- Harden `activation_event_state` fail-closed behavior for unknown provider, blank/invalid expected currency, and non-manual currency blank/mismatch.
- Normalize provider casing/whitespace at payment activation entry points (account + guest order).
- Normalize payment reference whitespace before dedupe/idempotency checks and persistence.
- Ensure readiness catalog includes Razorpay MCP candidate alongside Stripe for MCP-first payment posture.
- Add focused regression tests for each fail-closed/idempotency edge above.

## Out of scope
- No live checkout creation or subscription/refund mutation.
- No credential changes.
- No webhook runtime integration.
