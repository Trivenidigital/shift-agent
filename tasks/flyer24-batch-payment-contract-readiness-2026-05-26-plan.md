**Drift-check tag:** extends-Hermes

# Hermes-first checklist

1. Payment event ingestion and normalized request parsing (`manage-flyer-account`, `manage-flyer-guest-order`) — [Hermes]
2. Account/guest order state persistence, audit append, and idempotency substrate (`safe_io`, schemas, existing stores) — [Hermes]
3. Provider-neutral activation contract evaluation (`activation_event_state`) — [net-new]
4. Fail-closed verification for incomplete provider evidence (missing amount/currency/provider mismatch) — [net-new]
5. MCP-first connector readiness guidance for Stripe/Razorpay in readiness matrix — [net-new]
6. Regression tests pinning payment contract edge cases and connector-catalog expectations — [net-new]

Net-new scope (this batch only): tighten activation contract truthfulness + readiness catalog accuracy. No live checkout creation, no webhook execution, no state mutation beyond existing tested local flows.

## Batch issue list (6)

1. `activation_event_state` accepts blank currency for non-manual providers (can incorrectly confirm payment).
2. `activation_event_state` does not normalize provider casing/whitespace (false negatives/positives depending caller formatting).
3. `activation_event_state` allows unknown provider labels when direct callers bypass CLI validation.
4. Missing regression tests for the three activation contract gaps above.
5. Credential readiness catalog does not explicitly represent official Razorpay MCP connector path.
6. Missing regression test ensuring readiness connector table contains both Stripe MCP and Razorpay MCP with payment domain/auth metadata.
