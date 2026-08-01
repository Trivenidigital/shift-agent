# Commerce Platform — Project Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   2/3 hybrid — a shared component with its own product directive
    Project id: commerce-platform
    Supplements: docs/governance/engineering-directive.md
                 docs/governance/shared-platform-directive.md

Registered as `shared_platform: true`. Every change to
`src/platform/commerce/**` or `src/platform/commerce_*.py` carries the
shared-platform affected-agent obligations.

---

## Purpose

Money movement for the agent fleet: cart composition, order state, Stripe
payment links, live-mode and webhook gates, and commerce audit. Today's
primary consumer is Catering Studio deposits
(`src/agents/catering/deposit.py`, `catering-mint-deposit`,
`commerce_payment_confirmed` skill); the cockpit exposes order views.

## Model capability — reuse

Explaining an order or payment state to an owner in natural language, and
summarizing commerce activity. That is the full extent. **No model capability
may participate in composing an amount, minting a payment link, or
transitioning an order.**

## Deterministic kernels — reuse, do not fork

| Concern | Deployed owner |
|---|---|
| Cart composition | `src/platform/commerce/cart.py` |
| Order state machine | `src/platform/commerce/order_state.py` |
| Payment links | `src/platform/commerce/payment_link.py` |
| Live-mode gate | `src/platform/commerce_livemode_gate.py` |
| Webhook authenticity gate | `src/platform/commerce_webhook_gate.py` |
| Commerce audit | `src/platform/commerce/audit.py` |
| Atomic persistence | `src/platform/commerce/_io_shim.py` → `safe_io` |

## Decision boundary

**Must remain deterministic — without exception:** amounts, currency, order
state transitions, payment-link creation and expiry, live-vs-test mode,
webhook authenticity, idempotency, refund/reversal eligibility, persistence
and audit.

**May be probabilistic:** only the prose used to describe any of the above to
a human.

This is the strictest boundary in the repository. A change that lets model
output reach an amount or a state transition is a BLOCKER regardless of test
results.

## Presumed NO-GO

- a second payment-link path or provider integration alongside the existing
  one;
- a per-agent order store parallel to the deployed one;
- a product-local copy of the live-mode or webhook gate;
- bypassing `commerce/audit.py` for any money-affecting event.

## Shared-change obligations

Any change here must list every affected agent, its default behavior, its
activation posture and its rollback — see the shared-platform directive §2.
Catering Studio is always materially affected while deposits are live.

## Required vertical E2E proof

A real deposit flow: quote → mint link → owner-visible link → webhook
confirmation → order state → audit rows. Onboarding runbooks:
`docs/runbooks/commerce-deposit-onboarding.md`,
`docs/runbooks/commerce-stripe-onboarding.md`.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial Commerce Platform directive. |
