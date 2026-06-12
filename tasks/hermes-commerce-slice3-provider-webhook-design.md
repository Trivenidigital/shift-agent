# Hermes Commerce slice 3 — Provider + Webhook + payment_confirmed (design)

**Drift-check tag:** `extends-Hermes`

**Status:** Draft for 2-vector parallel design review (2026-05-29).

**Upstream gates satisfied:**
- Slice 2.5 live as baseline (`deploy-20260529-145506-d485cdc3`)
- Operator-set defaults via "your call" mandate: Stripe provider, test creds posture, per-order auto-mint, all other gates default
- **New rule applied (`tasks/lessons.md` 2026-05-29):** ecosystem audit performed BEFORE design draft

**Hermes-check receipt:** `tasks/.hermes-check-receipts/hermes-commerce-slice3-provider-webhook-design.json`

---

## Drift-rule self-checks (CLAUDE.md §"DRIFT RULES" compliance)

- ✅ Read `/root/.hermes/skills/devops/webhook-subscriptions/SKILL.md` (Hermes-native webhook platform: `hermes webhook subscribe <name> --events ... --skills ... --secret ...` — auto-generated HMAC, signature validation per POST, persists subscriptions to `~/.hermes/webhook_subscriptions.json`, hot-reloads on each incoming request; explicitly lists Stripe as a supported source: "external services (GitHub, GitLab, **Stripe**, CI/CD, IoT sensors, monitoring tools)")
- ✅ Read `/root/.hermes/skills/mcp/native-mcp/SKILL.md` (Hermes-native MCP client: configure `mcp_servers.<name>` in `~/.hermes/config.yaml`; supports stdio + HTTP transport; tools auto-registered with `mcp_<server>_*` prefix and available to every conversation)
- ✅ Read `src/platform/commerce/payment_link.py` (slice-1 primitive: `mint()`, `mark_attempted/sent`, `void()`, `register_reference()`, `emit_payment_link_failed()`. `CommercePaymentIntent.provider` Literal already includes `"stripe"` — no schema migration needed to switch from `"placeholder"` to `"stripe"`)
- ✅ Read `src/platform/schemas.py` (already-deployed audit variants: `CommercePaymentConfirmed`, `CommercePaymentDedupBlocked`, `CommercePaymentWebhookReceived`, `CommercePaymentWebhookVerifyFailed`, `CommercePaymentRefunded`, `CommercePaymentChargebackReceived` — all reserved in slice 1 PR #321 specifically to avoid schema migration when slice 3 lands. ✓)
- ✅ Read `src/agents/catering/scripts/catering-mint-deposit` (slice-2 caller: currently hard-codes `provider="placeholder"` via the `commerce_payment_link.mint()` call. Will switch via cfg knob to invoke Stripe via MCP tool OR direct API.)
- ✅ Read `tasks/hermes-commerce-prd-v2.md` (slice-3 scope envelope §12: real provider integration, webhook receiver, `commerce_payment_confirmed` flow, refund/void/chargeback variants)
- ✅ Read `tasks/hermes-commerce-slice2-catering-deposit-caller-design.md` (caller contract carry-forward: lead.deposit_status="awaiting_payment" today → slice 3 webhook flips to "paid")
- ✅ Read `tasks/lessons.md` (2026-05-29 rule verified present + applied — the audit FOUND substantial Hermes substrate that shrinks custom-code estimate from ~600 LOC to ~250 LOC)

Drift-check tag rationale: this PR **extends Hermes substrate** by configuring the existing `webhook-subscriptions` + `native-mcp` SKILLs for Stripe payment events, adding one new `commerce_payment_confirmed` SKILL that the webhook subscription invokes, and adding mode-switching in slice-1 `commerce_payment_link.mint()` to call Stripe (via MCP) when configured. No new daemon, no new webhook receiver, no new HTTP server, no custom HMAC code. No `drifts-from-Hermes` justification needed.

---

## 1. Hermes-first capability checklist (per-step)

End-to-end flow: customer pays Stripe Payment Link → Stripe webhook → Hermes webhook platform → `commerce_payment_confirmed` SKILL → lead.deposit_status="paid".

| # | Step | Tag |
|---|---|---|
| 1 | Operator catering-mint-deposit triggers a Stripe Payment Link mint | `[Hermes]` — slice-2 caller (unchanged) |
| 2 | `commerce_payment_link.mint()` routes by `cfg.commerce.provider` | `[net-new]` — small mode-switch in slice-1 primitive |
| 3 | Stripe API: create Payment Link with `metadata.commerce_order_id` + `amount_cents` | `[Hermes via MCP]` — Stripe MCP server invoked through `native-mcp` (preferred) OR `[net-new]` direct stripe-python SDK (fallback) |
| 4 | `commerce_payment_link.mint()` persists intent with `provider="stripe"`, `checkout_url=<stripe url>` | `[Hermes]` — slice-1 primitive (unchanged) |
| 5 | Catering caller sends URL to customer | `[Hermes]` — slice-2 caller (unchanged) |
| 6 | Customer pays on Stripe-hosted page | external |
| 7 | Stripe POSTs webhook to Hermes webhook platform | `[Hermes]` — `webhook-subscriptions` SKILL |
| 8 | Hermes webhook adapter validates HMAC signature | `[Hermes]` — built-in |
| 9 | Hermes routes to `commerce_payment_confirmed` SKILL (via subscription config) | `[Hermes]` — `webhook-subscriptions` dispatch |
| 10 | `commerce_payment_confirmed` SKILL extracts `metadata.commerce_order_id` from Stripe payload | `[net-new]` — new SKILL |
| 11 | SKILL invokes `commerce-payment-confirm` deterministic script | `[net-new]` — new operator script |
| 12 | Script calls `commerce_payment_link.register_reference()` (slice-1 primitive — invariant: cross-order immutability) | `[Hermes]` — slice-1 primitive |
| 13 | Script transitions order state pending_payment → paid via `commerce_order_state.transition()` | `[Hermes]` — slice-1 primitive |
| 14 | Script looks up CateringLead by `deposit_payment_intent_id`, flips `lead.deposit_status="paid"`, sets `deposit_payment_reference` | `[net-new]` — new helper (~30 LOC) |
| 15 | Optional: customer-visible confirmation reply ("Thanks! Your deposit is received") via `_bridge_post` | `[Hermes]` — multi-channel response |
| 16 | Audit row emitted: `commerce_payment_confirmed` (slice-1 reserved variant) + `catering_deposit_paid` (new variant) | `[Hermes for slice-1]` + `[net-new for catering audit]` |
| 17 | Refund / chargeback webhook → operator-alert-only (per design Q4 + reconciliation invariant) | `[Hermes]` — `webhook-subscriptions` routes to a separate notify-only SKILL |

**Net-new: 5 of 17 (29%).** No red flag. Significantly smaller than my pre-audit estimate (~12 net-new of 17 if I'd assumed custom webhook daemon).

---

## 2. Design overview

### Architecture

```
┌──────────────────┐  ┌────────────────────────┐  ┌─────────────────────────┐
│ Customer pays at │  │  Hermes webhook        │  │ commerce_payment_       │
│ Stripe hosted    ├─▶│  platform (8644)       ├─▶│ confirmed SKILL         │
│ Payment Link     │  │  - HMAC validate       │  │ - parse metadata.       │
└──────────────────┘  │  - route by subscript. │  │   commerce_order_id     │
                      │  - hot-reload subs     │  │ - invoke                │
                      └────────────────────────┘  │   commerce-payment-     │
                                                  │   confirm script        │
                                                  └─────────────┬───────────┘
                                                                │
                                                                ▼
                                              ┌────────────────────────────────┐
                                              │ commerce-payment-confirm       │
                                              │ (new deterministic script)     │
                                              │  1. register_reference (s1)    │
                                              │  2. order_state.transition(    │
                                              │       pending_payment→paid)    │
                                              │  3. CateringLead.deposit_      │
                                              │     status = "paid"            │
                                              │  4. Customer reply (optional)  │
                                              │  5. Audit                      │
                                              └────────────────────────────────┘
```

### What's already Hermes substrate (NO custom code)

1. **Webhook platform** (`/root/.hermes/skills/devops/webhook-subscriptions/SKILL.md` lines 18-50) — listens on port 8644, validates HMAC-SHA256 per POST, dispatches to SKILLs based on subscriptions stored in `~/.hermes/webhook_subscriptions.json`.
2. **MCP client** (`/root/.hermes/skills/mcp/native-mcp/SKILL.md` lines 60-100) — connects to MCP servers at startup, auto-discovers tools, exposes them as first-class capabilities. Stripe's official MCP server (`@stripe/mcp` via npm) provides `payment_links.create`, `customers.create`, etc.
3. **Hermes config + env** (`/root/.hermes/config.yaml` + `/root/.hermes/.env`) — operator-edited to enable webhook platform + register `mcp_servers.stripe`.

### What's net-new (~250 LOC)

| Item | LOC | Description |
|---|---|---|
| `cfg.commerce.provider` + provider selection in `commerce_payment_link.mint()` | ~50 | Mode flag: `"placeholder"` (slice-2 behavior, unchanged) / `"stripe"` (new — call Stripe via MCP tool / fallback to direct SDK). |
| `commerce_payment_link._mint_via_stripe()` helper | ~80 | Builds Stripe Payment Link via MCP tool call. Metadata includes `commerce_order_id` for webhook correlation. Returns Stripe-hosted URL + idempotency-safe (Stripe API itself idempotent on metadata.commerce_order_id). |
| `commerce_payment_confirmed` SKILL | ~70 LOC SKILL.md prose | Reads Stripe webhook payload, extracts `metadata.commerce_order_id`, invokes `commerce-payment-confirm` script. |
| `commerce-payment-confirm` script | ~150 | Deterministic reconciler. Looks up intent + lead by order_id; calls slice-1 `register_reference` (immutability guard) + `order_state.transition(pending_payment, paid, actor="webhook")`; updates `CateringLead.deposit_status="paid"` + `deposit_payment_reference`; emits `commerce_payment_confirmed` + `catering_deposit_paid` audit rows; optional customer-reply. |
| New `catering_deposit_paid` LogEntry variant | ~15 | Mirrors `catering_deposit_link_sent` shape; cross-references commerce_order_id + commerce_payment_intent_id + payment_reference. |
| Test files (4-5 new) | ~200 tests | Unit tests for helpers; subprocess test for `commerce-payment-confirm`; integration test (mock-stripe-webhook → SKILL → script → state mutation). |

### Existing slice-1 variants already cover the audit layer (PR #321 reserved them)

No new commerce audit variants needed:
- `CommercePaymentConfirmed` — already in `LogEntry` union (PR #321 reserved)
- `CommercePaymentDedupBlocked` — already in (slice-1 enforced; webhook signature failure variant)
- `CommercePaymentWebhookReceived` — already in (PR #321 reserved)
- `CommercePaymentWebhookVerifyFailed` — already in (PR #321 reserved)
- `CommercePaymentRefunded` — already in (PR #321 reserved)
- `CommercePaymentChargebackReceived` — already in (PR #321 reserved)

Only ONE net-new audit variant in slice 3: `CateringDepositPaid` (mirror of `CateringDepositLinkSent`).

---

## 3. Stripe MCP vs direct SDK — provider abstraction choice

**Preferred (Option A): Stripe MCP via native-mcp**

```yaml
# /root/.hermes/config.yaml
mcp_servers:
  stripe:
    command: "npx"
    args: ["-y", "@stripe/mcp", "--tools=payment_links,customers,refunds"]
    env:
      STRIPE_API_KEY: "sk_test_..."
    timeout: 60
```

Pros:
- Zero custom Python SDK code; Hermes auto-discovers tools
- Updates to Stripe API land via Stripe's MCP maintenance (not our update cycle)
- Other agents (Cash & AR, future receipt-reconciler) reuse same MCP server
- Matches lesson rule's preferred path

Cons:
- Operator must `npm install` + Hermes restart on first config
- MCP server stdio-based; one-process-per-Hermes-instance overhead
- Stripe MCP tool surface may not cover edge cases (e.g., refund timing); fallback to SDK if hit

**Fallback (Option B): direct stripe-python SDK**

```python
# src/platform/commerce/providers/stripe_client.py (~80 LOC)
import stripe
def mint_payment_link(amount_cents, currency, order_id, metadata=None) -> str:
    stripe.api_key = os.environ["STRIPE_API_KEY"]
    link = stripe.PaymentLink.create(
        line_items=[{"price_data": {"unit_amount": amount_cents, ...}, "quantity": 1}],
        metadata={"commerce_order_id": order_id, **(metadata or {})},
    )
    return link.url
```

Pros:
- One Python dep; no Node.js + npx dependency
- Full Stripe API surface available

Cons:
- Custom maintenance burden
- Violates the Hermes-first lesson rule (must justify why MCP doesn't fit)

**Decision (this design):** ship Option A as primary, with Option B as compile-time fallback if MCP tool call fails. The `cfg.commerce.provider_mode: Literal["mcp", "sdk"] = "mcp"` switch determines runtime behavior. Default "mcp"; operator can flip per VPS.

---

## 4. Configuration additions (`/opt/shift-agent/config.yaml`)

```yaml
commerce:
  enabled: false        # slice-1 opt-in flag (unchanged)
  provider: "placeholder"   # NEW: "placeholder" | "stripe" | "razorpay" | "upi" | "manual"
  provider_mode: "mcp"      # NEW: "mcp" | "sdk" — only meaningful for stripe/razorpay
  payment_checkout_url_template: ""   # unchanged — only used when provider="placeholder"
  minimum_deposit_cents: 500          # unchanged
  # Slice-3 webhook subscription must be created via `hermes webhook subscribe`
  # at operator-onboarding time; this config knob just records the expected
  # subscription name for runbook + smoke verification.
  webhook_subscription_name: "stripe-commerce-payments"   # NEW
```

Hermes side (`/root/.hermes/config.yaml`):

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: "0.0.0.0"
      port: 8644
      secret: "<global-hmac-secret-managed-by-operator>"

mcp_servers:
  stripe:
    command: "npx"
    args: ["-y", "@stripe/mcp", "--tools=payment_links,customers,refunds"]
    env:
      STRIPE_API_KEY: "${STRIPE_API_KEY}"
```

Hermes env (`/root/.hermes/.env`):

```bash
STRIPE_API_KEY=sk_test_xxx
WEBHOOK_ENABLED=true
WEBHOOK_SECRET=<32-byte hex>
```

**Operator-only configuration; no code in this PR touches `/root/.hermes/` files.** The slice-3 runbook (drafted alongside PR ladder, PR 4) walks operator through these.

---

## 5. State-file impact + locking

**Existing slice-1 files (mutation-extended, no new schema):**
- `state/commerce/payment_intents.json` — slice-3 flips intent.status from "minted/sent" → "confirmed" via `commerce_payment_link.mark_confirmed()` (NEW helper)
- `state/commerce/payment_references.json` — slice-3 populates entries via `register_reference()` (existing slice-1 invariant; immutable cross-order)
- `state/commerce/orders.json` — slice-3 transitions order pending_payment → paid via existing `commerce_order_state.transition()` (legal transition)

**Existing slice-2 catering files:**
- `state/catering-leads.json` — slice-3 updates lead.deposit_status="paid" + lead.deposit_payment_reference=<stripe payment_intent.id>

**Lock-acquisition order in `commerce-payment-confirm` script:**
1. Webhook arrives → SKILL invokes script
2. Script reads webhook payload from stdin / args
3. Acquire `FileLock(LEADS_LOCK)` — outer lock
4. Look up lead by `deposit_payment_intent_id` matching payload's `metadata.commerce_order_id`
5. Inside lock: register_reference (cross-order dedup), transition order, update lead, atomic_write_json
6. Release lock
7. Emit audit rows (via `commerce.audit.emit` chokepoint)
8. Optional customer reply via `_bridge_post`

Mirrors slice-2 `catering-mint-deposit` lock-acquisition pattern.

---

## 6. Webhook event routing

Stripe events to subscribe (slice 3 scope):

| Event | Handler | Customer-facing |
|---|---|---|
| `checkout.session.completed` OR `payment_intent.succeeded` | `commerce_payment_confirmed` SKILL → `commerce-payment-confirm` script | YES — confirmation reply |
| `payment_intent.payment_failed` | `commerce_payment_failed` SKILL (operator-alert only) | NO |
| `charge.refunded` | `commerce_payment_refunded` SKILL → operator alert + audit | depends — operator decides per refund |
| `charge.dispute.created` (chargeback) | `commerce_chargeback_received` SKILL → operator alert P1 + audit (NO state mutation per PRD v2 §7 reconciliation rule) | NO |

Only the first one (`payment_intent.succeeded`) is required for slice 3 MVP. Others are reserved-but-not-wired in slice 3.0 (slice 3.1 adds them once basic flow proves out).

**Subscription registration command** (operator runs once per VPS):

```bash
hermes webhook subscribe stripe-commerce-payments \
  --events "payment_intent.succeeded" \
  --skills "commerce_payment_confirmed" \
  --prompt "Stripe payment succeeded for {data.object.metadata.commerce_order_id}: amount={data.object.amount} reference={data.object.id}" \
  --secret "${STRIPE_WEBHOOK_SECRET}" \
  --deliver log
```

Returns the webhook URL for operator to configure in Stripe dashboard.

---

## 7. Customer-visible copy on payment confirmation

Per slice-2 customer-copy invariants (PRD v2 §10 + lesson on no-internal-terminology):

**Configured (default):**
```
⚕ *Catering Agent*
────────────
Thanks! Your $150.00 deposit is confirmed. We'll be in touch with details.
```

**Forbidden (same as slice 2):** Commerce, intent, primitive, Hermes, Stripe, lead_id, order_id, intent_id.

**Operator can suppress with `cfg.commerce.send_payment_confirmation_reply: bool = True`** if business prefers to handle confirmation manually.

---

## 8. Compliance matrix update (slice-3 deltas vs PRD v2 §6)

Slice 3 enables real money flow for catering deposits. No category expansion vs slice 2:

| Category | Slice 2 | Slice 3 | Note |
|---|---|---|---|
| Catering deposits (general cuisine) | ✓ allowed (catering caller wired) | ✓ allowed | Stripe Payment Link delivered |
| Raw meat / alcohol / tobacco / age-gated / live animals | ✗ blocked | ✗ still blocked | No category change in slice 3 |
| Religious-restriction SKUs (per-VPS) | ✗ filtered via per-VPS list | ✗ still filtered | No change |
| Refund handling | n/a | operator-review-only (P1 alert; no auto state mutation) | Per "your call" mandate + PRD v2 §7 reconciliation rule |
| Chargeback handling | n/a | operator-review-only (P1 alert; no auto state mutation) | Per "your call" + PRD §7 |
| Card-number-in-chat | ✗ explicitly forbidden | ✗ still forbidden | Stripe-hosted page handles card capture; chat only carries the link |
| 24-hour Meta window | unchanged | unchanged | Payment-confirmation reply is customer-initiated context (customer paid → reply within 24h trivially); no template needed |

---

## 9. Failure-mode matrix

| Scenario | Behaviour | Customer copy | Audit | Operator alert |
|---|---|---|---|---|
| Stripe API down on mint | Catering caller emits `intent_mint_failed` (slice-2 path) + cancels order (slice-2.5 cleanup) | none | `catering_deposit_link_failed reason=intent_mint_failed` | journald |
| MCP tool call fails (npx subprocess crash) | Same as above; SDK fallback kicks in if `provider_mode="sdk_on_mcp_fail"` | none | same + `commerce_provider_mode_fallback` (new variant deferred to slice 3.1) | journald |
| Stripe webhook HMAC verification fails | `webhook-subscriptions` SKILL drops the request silently; Hermes platform emits its own audit | none (Stripe will retry per its policy) | `commerce_payment_webhook_verify_failed` | journald (operator can grep) |
| Webhook payload missing `metadata.commerce_order_id` | `commerce-payment-confirm` script returns EXIT_INVALID_INPUT + emits failed audit | none | `commerce_payment_webhook_received verified=true` + `commerce_payment_confirmation_failed reason=missing_order_id` | journald |
| `commerce_order_id` doesn't match any minted intent | Script returns EXIT_NOT_FOUND + emits failed audit | none (likely stale webhook OR cross-customer error) | `commerce_payment_confirmation_failed reason=intent_not_found` | journald |
| `payment_reference` collision (cross-order reuse attempt) | `register_reference()` returns `dedup_blocked`; script emits dedup audit + refuses confirmation | none (audit-only; operator MUST review) | `commerce_payment_dedup_blocked` + `commerce_payment_confirmation_failed reason=reference_reused` | Pushover P1 (cross-order reference reuse is fraud surface) |
| Lead not found (intent points to lead that was deleted) | Script EXIT_NOT_FOUND + emits failed audit; the commerce intent IS still marked paid (truth = customer paid) | none | `commerce_payment_confirmed` (intent state advanced) + `catering_lead_not_found` audit | Pushover P1 (state divergence) |
| Customer-reply bridge fails | log-only; payment is still confirmed; operator can manually message customer | none from automation | `commerce_payment_confirmation_reply_failed` | journald |
| Race: two webhook deliveries for same payment (Stripe retries on 5xx) | Idempotent — `register_reference()` returns "noop_same_order" on second call; transition is idempotent | none | One `commerce_payment_confirmed` row only (idempotent re-application) | none |

---

## 10. Build sequence (PR ladder)

### PR 1 — Provider abstraction + `provider="stripe"` mode in `commerce_payment_link.mint()` (~80 LOC + 60 LOC tests)

- Add `cfg.commerce.provider` + `cfg.commerce.provider_mode` to `CommerceConfig`
- Branch in `commerce_payment_link.mint()`: when `provider="stripe"`, invoke `_mint_via_stripe()` helper instead of template substitution
- `_mint_via_stripe()` uses Hermes MCP tool call (via `subprocess` to invoke an MCP tool through Hermes CLI) OR direct stripe-python SDK fallback
- Tests: mode-switch unit tests; mocked MCP tool call; mocked direct SDK; mode-fallback on MCP failure
- **Schema-only change to slice-1 primitive (`CommercePaymentIntent.provider` Literal already accepts "stripe").** Catering caller unaware of mode change until cfg flag flips.

### PR 2 — `commerce_payment_confirmed` SKILL + `commerce-payment-confirm` script (~150 LOC + ~120 LOC tests)

- New SKILL at `src/platform/skills/commerce_payment_confirmed/SKILL.md` invoked by `hermes webhook subscribe`
- New script at `src/platform/scripts/commerce-payment-confirm` (deterministic reconciler)
- New LogEntry variant: `CateringDepositPaid` (mirror of `CateringDepositLinkSent`)
- Helper in `src/agents/catering/deposit.py`: `_mark_deposit_paid(lead, payment_reference)` (pure function)
- Tests: pure-function unit tests; subprocess test for the script; mocked-webhook integration test

### PR 3 — Operator runbook for slice-3 onboarding (~docs only)

- New runbook: `docs/runbooks/commerce-stripe-onboarding.md`
- Walks operator through: Stripe test-account setup, MCP server install (`npm install @stripe/mcp`), `~/.hermes/config.yaml` + `.env` edits, `hermes webhook subscribe` command, Stripe dashboard webhook config, end-to-end smoke procedure (test card flow)
- References the slice-2.5 runbook (`docs/runbooks/commerce-deposit-onboarding.md`) — slice-3 onboarding picks up where slice-2.5 left off

### PR 4 (optional, gated on operator readiness) — Refund + chargeback audit-only handlers

- Two additional SKILLs subscribed to `charge.refunded` + `charge.dispute.created`
- Each invokes a separate operator-alert-only script (no state mutation; Pushover P1)
- New LogEntry variants: `CommercePaymentRefunded` + `CommercePaymentChargebackReceived` (both slice-1 reserved — just wire the emit)

---

## 11. Test plan

### Slice-3 unit tests (~150 LOC)

- `test_commerce_payment_link_stripe_mode.py` — mode-switch correctness, MCP tool call mock, SDK fallback mock, error paths
- `test_commerce_payment_confirm_helpers.py` — `_mark_deposit_paid` truth table, idempotency, lead-not-found, reference dedup

### Slice-3 subprocess tests (~120 LOC, Linux-only per slice-2 pattern)

- `test_commerce_payment_confirm_script.py` — happy path (webhook → script → state mutation), missing metadata, intent-not-found, reference collision, idempotent replay (webhook delivered twice)

### Slice-3 integration test (~80 LOC, Linux-only)

- `test_stripe_webhook_e2e.py` — mock Stripe webhook payload → invoke `commerce_payment_confirmed` SKILL → assert lead.deposit_status="paid" + audit rows

### Manual smoke gates (operator runbook §X)

- Stripe test card flow end-to-end (operator creates lead → approve → customer pays with test card → assert lead.deposit_status="paid" within 30s)
- Refund test card flow (test mode only) → assert P1 fires + no state mutation
- Webhook replay verification (Stripe CLI `stripe trigger payment_intent.succeeded` against scratch state)

---

## 12. Operator dependencies (per the "your call" mandate)

All decisions defaulted per the operator's mandate. **Only credentials remain operator-blocking — and only at deploy time, not at design/build time:**

| Item | Default applied | Operator action gate |
|---|---|---|
| Provider | Stripe | None (default applied) |
| Credentials | Test keys only for slice-3 ship | **Operator must supply `STRIPE_API_KEY` (test mode) + `STRIPE_WEBHOOK_SECRET` to deploy** |
| Where creds live | `/root/.hermes/.env` (matches existing pattern) | None |
| Webhook endpoint | Hermes webhook platform on port 8644, proxied via existing reverse-proxy at `https://main-vps.../webhook/...` | **Operator must verify TLS reverse-proxy path** |
| Refund policy | Operator-review-only (P1 alert; no auto state mutation) | None |
| Chargeback policy | Operator-review-only (P1 alert; no auto state mutation) | None |
| Currency scope | USD only | None |
| Per-lead vs static link | Per-order API-minted Payment Links | None |
| MCP vs SDK | MCP primary, SDK fallback | None |

**Build can proceed without credentials** (placeholder/mocked Stripe tool call in tests). Deploy can ship the code without creds (provider mode stays "placeholder" via cfg). Operator flips to `provider="stripe"` after credentials land — no redeploy needed for the flip itself.

---

## 13. Risks + open notes

### Risks

1. **Stripe MCP server maturity** — the official `@stripe/mcp` package may not cover all Stripe API edge cases. Mitigation: SDK fallback in `_mint_via_stripe()`; runtime tests assert against both modes.
2. **Webhook subscription persistence** — `~/.hermes/webhook_subscriptions.json` is operator-managed. If Hermes is reinstalled or `.hermes` dir is wiped during upgrade, the subscription is lost and webhooks silently 404. Mitigation: add a slice-3.5 watchdog that asserts the subscription exists at deploy time.
3. **Test-mode → live-mode flip** — operator-only action. The schema (`CommercePaymentIntent.provider="stripe"`) doesn't distinguish test from live; only the API key does. Mitigation: runbook explicitly walks the flip + smoke.

### Open notes (NOT blockers)

- **Slice 3.1 candidates** deferred from this design: refund/chargeback handlers, `commerce_provider_mode_fallback` audit variant for MCP→SDK transitions, watchdog for webhook subscription presence, Cockpit deposit-pending tab integration with `paid` state.
- **Slice 4 candidates** deferred: multi-currency, multi-provider (Razorpay for India market), per-customer Stripe Connect accounts.

---

## 13.5 Design review applied (2026-05-29)

Two parallel reviewers ran. Verdicts: both `APPROVE_WITH_RECOMMENDATIONS`. No BLOCKERs (slice-1 `transition()` already returns `noop_already_in_status` on `paid → paid`, closing the Stripe-retries-forever concern). Findings applied below.

### HIGHs applied

- **A-HIGH-1 + B-HIGH-1 (convergence)** — **Stripe signature scheme is `t=<ts>,v1=<hex>` HMAC-SHA256 over `<timestamp>.<raw_body>`, NOT generic HMAC over body.** Even if Hermes' `webhook-subscriptions` adapter does generic HMAC, it does not validate Stripe's specific scheme. **Fix**: PR-2 `commerce-payment-confirm` script does explicit Stripe-signature validation via `stripe.Webhook.construct_event(payload, sig_header, webhook_secret)` AS THE LOAD-BEARING SECURITY GATE. Hermes' HMAC becomes a non-load-bearing first gate. This is defense-in-depth + portable (works regardless of what Hermes adapter does).
- **A-HIGH-1 (Stripe MCP tool surface verification)** — gate PR-1 on a one-time capture committed at `tasks/.hermes-check-receipts/stripe-mcp-tool-surface-2026-05-29.txt` proving `payment_links.create` exposes the `metadata` parameter. If verification fails, PR-1 falls back to direct stripe-python SDK (Option B) — already designed.
- **A-HIGH-2 (`mcp_servers` not in hermes-config-yaml baseline)** — PR-3 must bump `tools/hermes-config-yaml-baseline.txt:KNOWN_TOP_LEVEL_KEYS` to add `mcp_servers` so `check-hermes-config-yaml.sh` doesn't WARN after operator adds the Stripe config. Without the bump, operator will see a WARN they may ignore.
- **A-HIGH-3 (webhook SKILL invocation cost)** — verify in PR-2 build whether `webhook-subscriptions` invokes target SKILL via Hermes LLM agent run OR `--deliver-only` bypasses LLM. If LLM round trip per webhook, runtime cost + latency hit per payment. Prefer subscription config that bypasses LLM (e.g., the target SKILL is invoked via subprocess `commerce-payment-confirm` script that does NOT require LLM reasoning). Document the actual invocation shape in PR-2.
- **A-HIGH-4 (lock release ordering)** — `commerce-payment-confirm` MUST release `FileLock(LEADS_LOCK)` BEFORE `_bridge_post` (mirrors slice-2 catering-mint-deposit pattern). Updated §5 step ordering: lock → register_reference → transition order → update lead → atomic_write → **release lock** → emit audit → bridge_post.
- **B-HIGH-2 (currency-mismatch validation missing)** — `commerce-payment-confirm` MUST assert `intent.currency == payload.data.object.currency` (case-insensitive). Mismatch → emit `commerce_payment_confirmation_failed reason=currency_mismatch` + Pushover P1. ~3 LOC addition.
- **B-HIGH-3 (explicit fail-closed on empty/null `stripe_payment_intent.id`)** — 2026-05-25 Flyer lesson application. `commerce-payment-confirm` MUST `if not payload.data.object.id: EXIT_INVALID_INPUT + emit failed audit` BEFORE calling `register_reference`. Do not rely on slice-1 `register_reference` to enforce.

### MEDIUMs applied

- **A-MEDIUM-1 (re-cost PR-1)** — PR-1 now ~125 LOC + 100 LOC tests (was 80/60). Added: 4 new `CommerceConfig` fields (`provider`, `provider_mode`, `webhook_subscription_name`, `send_payment_confirmation_reply`) + schema tests.
- **A-MEDIUM-2 / B-(verified)** — `CateringDepositPaid` is the ONE net-new audit variant in slice-3. Confirmed.
- **A-MEDIUM-3 (`mark_confirmed()` helper)** — explicitly placed in PR-2 (~25 LOC addition to `commerce/payment_link.py` mirroring `mark_attempted`).
- **B-MEDIUM-1 (test-vs-live key mismatch detection)** — runbook (PR-3) adds smoke that calls `stripe.Account.retrieve()` and asserts `livemode` matches `cfg.commerce.stripe_livemode_expected: bool` (new cfg field). Catches the "live key in test cfg" footgun before any customer pays.
- **B-MEDIUM-2 (in-flight Stripe links survive provider flip-back)** — runbook documents rollback procedure: (a) `stripe payment_links update <id> --active=false` for each awaiting_payment intent OR (b) keep webhook subscription active even after flip-back so confirmations still process. Recommend (b) + adds a `commerce-list-active-stripe-links` operator script (small).
- **B-MEDIUM-3 (customer-copy event-anchor)** — slice-2 invariant carryover. Confirmation copy default now: `"Thanks! Your $150.00 deposit for {event_date} ({headcount} guests) is confirmed. We'll be in touch with details."` with safe fallbacks if either field missing. Customer-copy lint test (already exists in slice-2 pattern) extended to cover confirmation copy.
- **B-MEDIUM-4 (chargeback watchdog)** — moved from slice-3.1 to PR-4 of THIS ladder: `commerce-chargeback-pending-alert` cron job, re-fires Pushover every 6h while any unhandled chargeback audit row exists, until operator emits a `commerce_chargeback_resolved` audit (new variant).

### LOWs applied

- **A-LOW-1 (subscription-persistence watchdog)** — moved from slice-3.5 to PR-2: a deploy-smoke gate that calls `hermes webhook list` + asserts `stripe-commerce-payments` subscription is present. If missing, deploy aborts (the slice-3 webhook flow is silently broken without it).
- **A-LOW-2 (test coverage gaps)** — added: `paid → paid` idempotency assertion + `mcp_servers` config-baseline gate test in PR-3.
- **B-LOW-1 (`webhook_subscription_name` cfg drift)** — keep the field BUT wire a deploy-smoke check that `hermes webhook list | grep <subscription_name>` returns non-empty.
- **B-LOW-2 (npm-install-as-root pinning)** — PR-3 runbook documents `package.json` pin for `@stripe/mcp` so `npx -y` doesn't silently pull a new version on every Hermes restart. Mitigates `project_main_vps_canonical_home` memory's npm-as-root warning.
- **B-LOW-3 (Stripe idempotency_key passthrough)** — PR-1 `_mint_via_stripe()` passes `idempotency_key=order_id` to Stripe. Defense-in-depth against double-mint on transient Stripe API failures.

### Re-costed PR ladder

| PR | LOC | Tests | What ships |
|---|---|---|---|
| PR 1 — Provider abstraction + Stripe mode + idempotency_key | ~125 | ~100 | `CommerceConfig` additions; `_mint_via_stripe()`; `mark_confirmed()` helper; mode-switch in `commerce_payment_link.mint()` |
| PR 2 — Webhook reconciler with Stripe-sig validation + watchdog | ~250 | ~200 | `commerce_payment_confirmed` SKILL; `commerce-payment-confirm` script with `stripe.Webhook.construct_event()` validation + currency match + empty-id fail-closed + lock release before bridge + event-anchor copy; new `CateringDepositPaid` LogEntry variant; subscription-presence deploy-smoke gate |
| PR 3 — Runbook + config-baseline bump + Stripe MCP tool-surface receipt | ~150 docs + ~30 LOC | ~50 | `docs/runbooks/commerce-stripe-onboarding.md`; bump `hermes-config-yaml-baseline.txt`; `tasks/.hermes-check-receipts/stripe-mcp-tool-surface-2026-05-29.txt`; `commerce-list-active-stripe-links` script |
| PR 4 — Refund + chargeback handlers + chargeback watchdog | ~150 | ~120 | Two SKILLs subscribed to `charge.refunded` + `charge.dispute.created`; operator-alert-only scripts; `commerce-chargeback-pending-alert` cron; new `commerce_chargeback_resolved` audit variant |
| **Total** | **~675 LOC + ~470 LOC tests** | | (was ~250 LOC pre-review; reviewer-applied fixes nearly doubled the count) |

The pre-review ~250 LOC estimate was honest given the audit, but did not credit:
- Custom Stripe-signature validation shim (would not have been needed if Hermes adapter natively supported Stripe scheme; reviewers correctly flagged the assumption)
- Currency-mismatch validation
- Empty-id fail-closed guard
- mark_confirmed helper
- CommerceConfig schema additions
- 4 new cfg fields + tests
- Chargeback watchdog cron
- Runbook smokes + livemode check
- Subscription-presence watchdog
- Stripe MCP tool-surface verification receipt

Even at ~675 LOC, this is still significantly smaller than the original ~600-800 LOC pre-audit estimate of "write a custom webhook receiver daemon from scratch." The Hermes-substrate path remains the right architecture.

---

## 14. References

- Slice 1 PR: https://github.com/Trivenidigital/shift-agent/pull/321
- Slice 1 deploy install PR: https://github.com/Trivenidigital/shift-agent/pull/322
- Slice 2 PR: https://github.com/Trivenidigital/shift-agent/pull/324
- Slice 2.5 orphan cleanup PR: https://github.com/Trivenidigital/shift-agent/pull/327
- Slice 2.5 tz cleanup PR: https://github.com/Trivenidigital/shift-agent/pull/330
- Slice 2 runbook PR: https://github.com/Trivenidigital/shift-agent/pull/331
- PRD v2: `tasks/hermes-commerce-prd-v2.md`
- Reconciliation: `tasks/hermes-commerce-portfolio-reconciliation.md`
- Slice 2 design: `tasks/hermes-commerce-slice2-catering-deposit-caller-design.md`
- Slice 2 runbook: `docs/runbooks/commerce-deposit-onboarding.md`
- Hermes webhook SKILL: `/root/.hermes/skills/devops/webhook-subscriptions/SKILL.md`
- Hermes MCP SKILL: `/root/.hermes/skills/mcp/native-mcp/SKILL.md`
- New lesson: `tasks/lessons.md` 2026-05-29 Hermes-first-for-payment-substrate rule
