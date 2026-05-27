# Flyer24 Batch Payment Contract Reland Plan (2026-05-27)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Receive account/guest payment activation input (`provider`, `payment_reference`, `amount`, `currency`) — [Hermes] ingress + script dispatch; [net-new] Flyer payment contract validation.
2. Resolve plan catalog amounts/currencies for expected payment facts — [net-new] Flyer business logic.
3. Evaluate activation transition idempotently and fail-closed on mismatch — [net-new] Flyer payment state machine.
4. Surface provider connector readiness to operator (Stripe/Razorpay MCP-first posture) — [net-new] readiness catalog metadata.
5. Persist/audit account/guest payment states — [Hermes] state/audit substrate + existing Flyer schema paths.

Net-new effort in this batch: steps 2-4 only.

## Batch issues (target 6)
1. `activation_event_state` accepts non-canonical provider strings (case/whitespace drift).
2. `activation_event_state` does not fail-closed for unknown providers.
3. Non-manual providers can pass with blank currency when amount provided.
4. Empty expected currency should fail-closed instead of implicit pass behavior.
5. MCP connector catalog is missing official Razorpay MCP candidate metadata.
6. No focused unit tests pinning the above contract/readiness edges.

## MCP-first verdict (payment)
Use MCP connector surfaces only (Stripe MCP + Razorpay MCP) and keep code provider-neutral. No custom payment API client, no live checkout/subscription/refund mutations, no credential or production state changes.
