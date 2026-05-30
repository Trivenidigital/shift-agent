# Catering Deposit — Stripe Onboarding (slice 3)

**Status:** Live as of `deploy-20260529-192056-ed6f2fb5` (slice-3 PR 1 + PR 2 baseline).
**Audience:** SMB-Agents operator (founder + on-call).
**Prerequisites:** slice-2.5 baseline live (per `docs/runbooks/commerce-deposit-onboarding.md`); operator has a Stripe account (test mode is free + recommended for first customer).

---

## What this runbook covers

After slice-3 PR-1 + PR-2 deployed, the catering deposit caller has the **machinery** to mint real Stripe Payment Links and process Stripe webhook confirmations — but it's all dormant until the operator wires it. This runbook is the wiring procedure.

After completing all 7 steps, qualifying catering leads will:
1. Receive a real Stripe Payment Link in WhatsApp (instead of the static-link or unconfigured fallback)
2. Have their `deposit_status` automatically flip to `paid` when the customer completes payment
3. Get an event-anchored confirmation reply

---

## Step 1 — Stripe test-mode account setup

If you don't have a Stripe account yet:

1. Sign up at https://stripe.com (free; test mode requires no business verification)
2. After login, the dashboard shows a banner "**Viewing test data**" — leave it ON for slice-3 ship
3. **Note your test API key:** Developers → API keys → Standard keys → "Secret key" → reveal + copy. Format: `sk_test_XXXXXXXXXXXXX`

If you have a Stripe account already:

1. **Verify you're in test mode** (top-left toggle)
2. Generate a test secret key (don't reuse live keys for this slice)

---

## Step 2 — Install stripe-python in the Hermes venv

The slice-1 `commerce_payment_link.mint()` lazy-imports `stripe`; it's not a hard dependency. Install it now:

```bash
ssh main-vps 'pip install stripe' > /tmp/install.txt 2>&1
cat /tmp/install.txt
```

Or if there's a dedicated venv:

```bash
ssh main-vps '/opt/shift-agent/venv/bin/pip install stripe' > /tmp/install.txt 2>&1
```

Verify:

```bash
ssh main-vps 'python3 -c "import stripe; print(stripe.VERSION)"' > /tmp/check.txt 2>&1
cat /tmp/check.txt
```

Expected: a version number like `5.x.x`. If you see `ModuleNotFoundError`, the install went to the wrong Python — check `which python3` and adjust.

---

## Step 3 — Set credentials in Hermes env

Edit `/root/.hermes/.env` and add:

```bash
STRIPE_API_KEY=sk_test_YOUR_KEY_HERE
STRIPE_WEBHOOK_SECRET=whsec_TO_BE_FILLED_IN_STEP_5
WEBHOOK_ENABLED=true
WEBHOOK_PORT=8644
WEBHOOK_SECRET=GENERATE_A_32-BYTE_HEX_SECRET_HERE
```

Generate the global webhook secret with:

```bash
ssh main-vps 'openssl rand -hex 32'
```

(The global `WEBHOOK_SECRET` is the Hermes-level HMAC; the per-subscription Stripe webhook secret is separate, see Step 5.)

Restart Hermes gateway:

```bash
ssh main-vps 'systemctl restart hermes-gateway'
```

Verify webhook platform is up:

```bash
ssh main-vps 'curl -sf http://localhost:8644/health' > /tmp/webhook-health.txt 2>&1
cat /tmp/webhook-health.txt
```

Expected: `{"status":"ok"}`.

---

## Step 4 — Verify the deployed reconciler

```bash
ssh main-vps 'ls -la /usr/local/bin/commerce-payment-confirm /usr/local/bin/commerce-list-active-stripe-links' > /tmp/check-bin.txt 2>&1
cat /tmp/check-bin.txt
```

Both should be executable. If not, re-run the most recent deploy.

---

## Step 5 — Create the Stripe webhook subscription via Hermes

Decide your subscription name (default: `stripe-commerce-payments`; matches `cfg.commerce.webhook_subscription_name`).

```bash
ssh main-vps 'hermes webhook subscribe stripe-commerce-payments \
  --events "payment_intent.succeeded" \
  --skills "commerce_payment_confirmed" \
  --prompt "Stripe payment succeeded for {data.object.metadata.commerce_order_id}: amount={data.object.amount} reference={data.object.id}" \
  --secret "PASTE_STRIPE_WEBHOOK_SECRET_HERE" \
  --deliver log' > /tmp/sub.txt 2>&1
cat /tmp/sub.txt
```

This returns:
- A webhook URL (e.g., `https://main-vps.example.com:8644/webhook/stripe-commerce-payments`)
- The HMAC secret (the one you passed via `--secret`)

**The HMAC secret is what Stripe will sign with.** Save it; you'll paste it in the Stripe dashboard in Step 6 + back into `STRIPE_WEBHOOK_SECRET` in `/root/.hermes/.env` (so `commerce-payment-confirm` validates Stripe's signature scheme — this is the load-bearing security gate).

Verify the subscription is registered:

```bash
ssh main-vps 'hermes webhook list' > /tmp/list.txt 2>&1
cat /tmp/list.txt
```

You should see `stripe-commerce-payments` in the output.

---

## Step 6 — Configure the Stripe dashboard webhook endpoint

In the Stripe dashboard (test mode):

1. **Developers** → **Webhooks** → **Add endpoint**
2. **Endpoint URL:** the URL Hermes returned in Step 5
3. **Description:** "SMB-Agents commerce payment confirmations"
4. **Events to send:** `payment_intent.succeeded` (only this one for slice-3 PR-2; refund + chargeback land in PR-4)
5. **Signing secret:** Stripe auto-generates this. Click "Reveal" + copy the `whsec_...` value
6. Paste the signing secret into `/root/.hermes/.env` as `STRIPE_WEBHOOK_SECRET=whsec_...`

If the Hermes-side and Stripe-side secrets don't match, every webhook fails signature validation + Pushover P1 fires (operator alert).

Restart Hermes one more time to pick up the new env var:

```bash
ssh main-vps 'systemctl restart hermes-gateway'
```

---

## Step 7 — Smoke test against a scratch lead

**Do not flip `cfg.commerce.provider="stripe"` until this smoke passes.**

1. Pick a scratch lead (don't use a real customer's lead). You can use a manually-created test lead via `create-catering-lead` with a fake phone like `+15550000099`.
2. Trigger a deposit-mint manually:

```bash
ssh main-vps '/usr/local/bin/catering-mint-deposit --lead-id LSMOKE_S3 2>&1' > /tmp/mint.txt 2>&1
cat /tmp/mint.txt
```

3. The audit log should show a `commerce_payment_intent_minted` row with `provider=stripe` and the lead's `deposit_payment_intent_id` populated.
4. Open the returned Stripe Payment Link URL in a browser. Use Stripe's test card: `4242 4242 4242 4242`, any future expiry, any 3-digit CVC, any ZIP.
5. Complete the payment.
6. Within 30 seconds, the Stripe dashboard should show the payment + a successful webhook delivery.
7. Verify on the VPS:

```bash
ssh main-vps 'tail -20 /opt/shift-agent/logs/decisions.log | grep -E "commerce_payment_confirmed|catering_deposit_paid"' > /tmp/audit.txt 2>&1
cat /tmp/audit.txt
```

Expected: a `commerce_payment_confirmed` row + a `catering_deposit_paid` row, both within seconds of the customer pressing "Pay".

8. Verify the lead state:

```bash
ssh main-vps 'jq ".leads[] | select(.lead_id==\"LSMOKE_S3\") | {deposit_status, deposit_payment_reference}" /opt/shift-agent/state/catering-leads.json' > /tmp/lead.txt 2>&1
cat /tmp/lead.txt
```

Expected: `deposit_status: "paid"`, `deposit_payment_reference: "pi_..."`.

If all 8 checks pass, you're ready for first customer.

---

## Step 8 — Flip the provider

After Step 7 passes:

```bash
ssh main-vps 'sed -i s/provider:.*placeholder/provider: stripe/ /opt/shift-agent/config.yaml'
```

Or edit `/opt/shift-agent/config.yaml` manually:

```yaml
commerce:
  enabled: false
  provider: stripe          # CHANGED from placeholder
  provider_mode: sdk
  payment_checkout_url_template: ""   # no longer used in stripe mode
  minimum_deposit_cents: 500
  webhook_subscription_name: stripe-commerce-payments
  send_payment_confirmation_reply: true
  stripe_livemode_expected: false     # test mode for first customer
```

**No restart needed** — `apply-catering-owner-decision` re-reads config on each invocation.

The next qualifying owner-approve will mint a real Stripe Payment Link instead of the placeholder template.

---

## Deploy gates protecting activation

Once commerce is active for Stripe — `commerce.enabled: true` **and**
`commerce.provider: stripe` — **every subsequent `shift-agent-deploy` runs two
fail-closed pre-restart gates** that verify the activation is still sound. When
commerce is dormant (`provider: placeholder`, or `enabled: false`) both gates
**skip cleanly (exit 0)** and never affect pre-activation deploys.

1. **`check-commerce-webhook-subscription`** (`commerce_webhook_gate.py`) —
   asserts `cfg.commerce.webhook_subscription_name` (default
   `stripe-commerce-payments`) appears in `hermes webhook list`. If the
   subscription is missing it aborts the deploy (exit 1):
   `FATAL: commerce.provider=stripe but webhook subscription
   'stripe-commerce-payments' is not registered ...`. So **Step 5 must be
   complete** before any deploy runs with commerce active, or
   `payment_intent.succeeded` events would silently 404.

2. **`check-commerce-stripe-livemode`** (`commerce_livemode_gate.py`) — reads
   `STRIPE_API_KEY` from `/root/.hermes/.env`, calls
   `GET https://api.stripe.com/v1/account`, and asserts the account's `livemode`
   matches `cfg.commerce.stripe_livemode_expected`. A mismatch aborts the deploy
   (exit 1) — this catches an `sk_live_` key while `stripe_livemode_expected:
   false` (or vice versa) before a customer can pay against the wrong mode. A
   missing/invalid key or unreachable Stripe aborts the deploy (exit 2).

A gate failure triggers the deploy's normal **auto-rollback** to the previous
tarball. Safe activation order: finish Step 5 (webhook subscribe) and set
`stripe_livemode_expected` to match your key's mode **before** the first deploy
that runs with commerce active. (Activation via the Step 8 config edit needs no
deploy — these gates fire on the *next* deploy and keep the active config honest.)

---

## Live mode rollout (only after multiple test-mode successes)

When you're ready to accept real payments:

1. Generate a Stripe **live** API key (Developers → API keys → live mode toggle)
2. Update `/root/.hermes/.env`: replace `STRIPE_API_KEY=sk_test_...` with `STRIPE_API_KEY=sk_live_...`
3. Re-do Step 6 in **live mode** Stripe dashboard (different webhook endpoint configured per environment)
4. Update `/opt/shift-agent/config.yaml`: set `stripe_livemode_expected: true` (the deployed `check-commerce-stripe-livemode` gate verifies this matches the key's mode on the next deploy — see "Deploy gates protecting activation")
5. `systemctl restart hermes-gateway` to pick up new env
6. Smoke against a personal scratch lead with a $1 deposit (a real $1 charge you'll refund)

The `stripe_livemode_expected` flag is a defense-in-depth check: the deployed `check-commerce-stripe-livemode` deploy gate **fails closed** if the API key's `livemode` doesn't match this flag — so a `sk_live_` key under `stripe_livemode_expected: false` aborts the next deploy rather than letting a customer pay against the wrong mode. (Now an automated deploy gate, not a manual step.)

---

## Kill switch + rollback

**Immediate disable (any time, no redeploy):**

```bash
ssh main-vps 'sed -i s/provider:.*stripe/provider: placeholder/ /opt/shift-agent/config.yaml'
```

This flips back to slice-2.5 placeholder mode. New deposit mints will use the template substitution path; **already-minted Stripe Payment Links remain live** — customer can still pay them.

To deactivate in-flight Stripe Payment Links so a flip-back is clean:

```bash
ssh main-vps '/usr/local/bin/commerce-list-active-stripe-links --only-stripe --format table' > /tmp/active.txt 2>&1
cat /tmp/active.txt
```

For each row, open the Stripe dashboard → Payment Links → that link → "Deactivate". This prevents the customer from completing payment on a stale link.

---

## Failure-mode triage

```bash
ssh main-vps 'grep -E "commerce_payment_(confirmed|confirmation_failed)|catering_deposit_(paid|link_(sent|failed))" /opt/shift-agent/logs/decisions.log | tail -30' > /tmp/audit.txt 2>&1
cat /tmp/audit.txt
```

| Audit row | Meaning |
|---|---|
| `commerce_payment_confirmed` + `catering_deposit_paid` | Happy path — customer paid, lead reconciled |
| `commerce_payment_confirmation_failed reason=signature_invalid` | Stripe webhook signed with wrong secret OR forged. Pushover P1 fired. Investigate. |
| `commerce_payment_confirmation_failed reason=sdk_not_installed` | `stripe-python` is not installed. Step 2 not done. |
| `commerce_payment_confirmation_failed reason=missing_metadata` | Stripe webhook payload missing `metadata.commerce_order_id` or `commerce_intent_id`. Likely a manually-created Payment Link without metadata (e.g., operator clicked "Create payment link" in dashboard rather than using slice-3 auto-mint). |
| `commerce_payment_confirmation_failed reason=intent_not_found` | Stripe webhook references an `intent_id` that doesn't exist on this VPS. State divergence — investigate. |
| `commerce_payment_confirmation_failed reason=currency_mismatch` | Stripe charged in a different currency than the intent expects. Pushover P1 fired. |
| `commerce_payment_confirmation_failed reason=amount_mismatch` | Customer paid a different amount than the intent. Pushover P1 fired. |
| `commerce_payment_confirmation_failed reason=reference_reused_other_order` | Stripe payment_intent.id is already bound to a different commerce order (cross-order dedup blocked). Pushover P1 fired — possible duplicate charge or forged webhook. |
| `commerce_payment_confirmation_failed reason=illegal_transition` | Commerce order is in cancelled/voided/refunded — webhook arrived after operator cancelled. Pushover P1 fired. State divergence; reconcile manually. |
| `commerce_payment_confirmation_failed reason=mark_confirmed_failed` | Slice-1 primitive refused to confirm (probably stale state). Investigate. |

---

## References

- Slice-2.5 baseline runbook: `docs/runbooks/commerce-deposit-onboarding.md`
- Slice-3 design: `tasks/hermes-commerce-slice3-provider-webhook-design.md`
- PRs:
  - PR #321 — slice 1 commerce primitives
  - PR #324 — slice 2 catering deposit caller
  - PR #327 — slice 2.5 orphan-order cleanup
  - PR #335 — slice 3 PR 1 provider abstraction + Stripe SDK
  - PR #337 — slice 3 PR 2 webhook reconciler
- Reconciler script: `/usr/local/bin/commerce-payment-confirm`
- Reconciler SKILL: `/root/.hermes/skills/commerce_payment_confirmed/SKILL.md`
- List script (this PR): `/usr/local/bin/commerce-list-active-stripe-links`

---

## What slice 3 still doesn't cover

Per `tasks/hermes-commerce-slice3-provider-webhook-design.md` §13:

- **Refund handling** (`charge.refunded` Stripe event) — PR-4
- **Chargeback handling** (`charge.dispute.created` Stripe event) — PR-4
- **Chargeback watchdog** (re-fire Pushover every 6h until resolved) — PR-4
- **Subscription-presence deploy-smoke gate** — slice-3.5
- **Stripe MCP path** (replace SDK with MCP for richer tool surface) — slice-3.1
- **livemode-match smoke** (auto-assert `cfg.commerce.stripe_livemode_expected` matches `stripe.Account.retrieve().livemode`) — slice-3.1
- **Multi-currency support** — slice 4
- **Razorpay / UPI providers** — slice 4 (India market)
