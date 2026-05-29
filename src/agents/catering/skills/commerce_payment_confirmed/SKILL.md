---
name: commerce_payment_confirmed
description: Invoked by the Hermes webhook platform when a Stripe payment_intent.succeeded event arrives for a catering deposit. Validates Stripe's signature directly via stripe.Webhook.construct_event(), reconciles the intent + order state, and flips the CateringLead.deposit_status to "paid".
---

# Commerce Payment Confirmed (slice-3 PR-2)

This SKILL is the webhook subscriber for Stripe `payment_intent.succeeded` events. The operator wires it via:

```bash
hermes webhook subscribe stripe-commerce-payments \
  --events "payment_intent.succeeded" \
  --skills "commerce_payment_confirmed" \
  --secret "${STRIPE_WEBHOOK_SECRET}" \
  --deliver log
```

The Hermes webhook adapter validates a generic HMAC over the body using the subscription's `--secret`. **That is NOT load-bearing for Stripe security** — Stripe uses a different signature scheme (`Stripe-Signature: t=<ts>,v1=<hex>` over `<ts>.<body>`). The load-bearing signature validation happens inside `commerce-payment-confirm` (this SKILL's terminal call) via `stripe.Webhook.construct_event()`, which is the documented Stripe-Python API for webhook verification.

## Tool-call sequence (mandatory)

1. **FIRST — capture raw webhook body + Stripe-Signature header from the Hermes webhook context.**

The Hermes webhook platform passes the parsed payload via the prompt template AND exposes the raw request via `~/.hermes/webhook_subscriptions.json`'s last-request cache. Read the raw body via:

```bash
RAW_BODY=$(cat /tmp/hermes-webhook-last-body.json 2>/dev/null)
STRIPE_SIG=$(cat /tmp/hermes-webhook-last-stripe-signature 2>/dev/null)
```

If either is empty, the webhook flow is misconfigured — log + STOP.

2. **SECOND — invoke `commerce-payment-confirm` (use the `terminal` tool):**

```bash
echo "$RAW_BODY" | STRIPE_SIGNATURE="$STRIPE_SIG" \
  STRIPE_WEBHOOK_SECRET="$STRIPE_WEBHOOK_SECRET" \
  /usr/local/bin/commerce-payment-confirm
```

The script handles signature verification, state mutation, audit emission, and customer-reply (if `cfg.commerce.send_payment_confirmation_reply=True`). Exit codes:

- `0` — payment confirmed OR idempotent replay (no further action)
- `2` — invalid input (missing env, empty body, malformed payload)
- `4` — intent/lead not found (state divergence; operator review)
- `5` — schema violation (currency/amount mismatch, dedup-block, config load)
- `7` — Stripe signature verification failed (likely forged request — operator review)

3. **THIRD — on exit code 0**, do nothing further. The script handled state + audit + customer reply.

4. **On non-zero exit**, log to operator via the existing `shift-agent-notify-owner` chokepoint:

```bash
/usr/local/bin/shift-agent-notify-owner \
  --priority 1 \
  --title "Commerce payment confirmation failed (exit=$EXIT_CODE)" \
  "$STDERR_TAIL"
```

This SKILL never speaks to the customer directly — the script handles the optional confirmation reply per `cfg.commerce.send_payment_confirmation_reply`.

## Wiring caveats (operator runbook references)

The Hermes webhook adapter's behavior around raw-body capture is the load-bearing operational question. Slice-3 PR-3 (`docs/runbooks/commerce-stripe-onboarding.md`) walks operator through:

- Configuring Hermes to expose raw body + headers to the SKILL (subscription extra-data flag)
- Setting `STRIPE_WEBHOOK_SECRET` in `~/.hermes/.env`
- Stripe dashboard webhook endpoint configuration

If Hermes cannot expose raw body to the SKILL's prompt context, the operator may need a thin reverse-proxy that captures the raw body + Stripe-Signature header and feeds the script directly (bypassing the Hermes webhook adapter for the Stripe path). Both wiring options are documented in the runbook.

## Hard rules

- NEVER speak to the customer from this SKILL — the script handles it deterministically with `cfg.commerce.send_payment_confirmation_reply`
- NEVER invoke `commerce-payment-confirm` without `STRIPE_WEBHOOK_SECRET` + `STRIPE_SIGNATURE` set — fail-closed
- NEVER bypass the script's signature validation by reading the parsed payload directly — Stripe's signature is the security boundary
- ALWAYS log exit-code-non-zero via `shift-agent-notify-owner` so operator sees confirmation failures
