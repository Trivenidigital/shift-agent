# Flyer Onboarding Phase 1 Design

**Drift-check tag:** extends-Hermes

**Goal:** Launch WhatsApp-native onboarding for paying Flyer Studio customers without a web app, while protecting account mutations, payment activation, and plan quotas.

**New primitives introduced:** `FlyerUsageEvent`, Flyer account audit entries, account activation command, atomic quota reservation/record helpers, account command handler, onboarding confirmation state.

## Hermes-first Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress and identity | yes - deployed Hermes gateway, `dispatch_shift_agent`, cf-router hooks, `identify-sender` | use it |
| Customer media intake | yes - Hermes image cache plus existing Flyer brand asset capture | use it |
| State storage | yes - JSON-on-disk, `FileLock`, `atomic_write_text` | use it |
| Audit | yes - existing `LogEntry` union and `safe_io.ndjson_append` | extend it with Flyer account events |
| Text/media delivery | yes - existing `safe_io.bridge_post` and `bridge_send_media` | use it |
| Flyer generation workflow | yes - existing `create-flyer-project`, `generate-flyer-concepts`, revisions, final package delivery | use it |
| Stripe/Razorpay payment automation | no installed local Flyer payment skill verified; `mcp/native-mcp`/vendor MCP remains the connector-first route | do provider-agnostic manual activation now; defer webhook/payment-link automation |
| Plan quotas and account commands | none found | build narrow local product logic |

Awesome Hermes Agent ecosystem check: no ready-made WhatsApp Flyer Studio onboarding/payment/quota skill was identified. Phase 1 is local SMB-Agents product logic around Hermes substrate, not a replacement for Hermes.

## Architecture

The account layer lives beside the existing Flyer module:

- `src/platform/schemas.py` remains the authoritative shape for customer state, usage events, and audit rows.
- `src/agents/flyer/onboarding.py` keeps the pure WhatsApp onboarding state machine.
- `src/agents/flyer/account.py` owns account commands, activation, quota reservation, usage finalization, and quota release.
- `src/agents/flyer/scripts/manage-flyer-account` exposes the account functions to cf-router and operators through subprocess calls.
- `src/plugins/cf-router/hooks.py` adds an early account-command intercept, then continues to brand asset, active project, onboarding, and primary flyer routing.
- `src/plugins/cf-router/actions.py` adds subprocess helpers for account commands and quota operations.

No database or web app is introduced. The same per-customer VPS JSON files remain the source of truth.

## Data Model

`FlyerCustomerProfile` gains these fields with safe defaults so existing JSON validates:

- `primary_chat_id: str = ""`
- `onboarded_by_phone: Optional[E164Phone] = None`
- `activated_at: Optional[datetime] = None`
- `plan_started_at: Optional[datetime] = None`
- `current_period_start: Optional[datetime] = None`
- `current_period_end: Optional[datetime] = None`
- `monthly_flyers_used: int = 0`
- `payment_reference: str = ""`
- `payment_amount_cents: Optional[int] = None`
- `payment_currency: str = "USD"`
- `pending_plan_id: str = ""`
- `pending_plan_checkout_url: str = ""`
- `pending_plan_requested_at: Optional[datetime] = None`
- `usage_events: list[FlyerUsageEvent] = []`

`FlyerUsageEvent`:

- `reservation_id`: deterministic id such as `<customer_id>:<project_id>`.
- `project_id`
- `customer_id`
- `kind`: `reserved`, `used`, or `released`
- `count`
- `recorded_at`
- `message_id`

Usage events are the source of truth. `monthly_flyers_used` is a cached/display field recomputed on each quota check from the latest event per `reservation_id` inside the active billing period. A reservation whose latest event is `reserved` or `used` counts once. A reservation whose latest event is `released` counts zero. Raw `reserved + used` events are never summed, which prevents double-counting after finalization.

Audit entries added to `LogEntry`:

- `flyer_customer_created`
- `flyer_customer_activated`
- `flyer_account_updated`
- `flyer_usage_recorded`
- `flyer_quota_blocked`

Each account script writes audit entries through `safe_io.ndjson_append`. The production default is `/opt/shift-agent/logs/decisions.log`; tests may override it with `--audit-log-path`.

## Onboarding Flow

The existing WhatsApp onboarding prompts remain:

1. business name
2. business address
3. public phone
4. business WhatsApp number
5. authorized flyer request number
6. business category and preferred language
7. plan selection
8. confirmation summary
9. payment-pending customer creation

After plan selection, the session moves to `confirming_summary`. `confirming_summary` is added to `FlyerOnboardingStatus` before any state can persist it. The reply lists the captured fields, selected plan, and active uploaded logos/templates. The customer must reply `CONFIRM` to create the customer record. During onboarding:

- `HELP` returns state-aware guidance.
- `BACK` returns to the prior field and clears the field being revisited.
- `RESTART` discards the session and restarts at business name.
- Confirmation supports direct edits: `EDIT NAME: ...`, `EDIT ADDRESS: ...`, `EDIT PHONE: ...`, `EDIT WHATSAPP: ...`, `EDIT AUTHORIZED: ...`, `EDIT PROFILE: ...`, and `EDIT PLAN: 1|2|3|starter|growth|unlimited`.

When confirmed, the customer is created as `payment_pending`, `primary_chat_id` is set to the WhatsApp chat id, pending brand assets are attached, and a checkout URL is rendered from config if available.

Payment-pending customers can still send logos/templates, `STATUS`, or `HELP`. Normal flyer requests remain blocked until activation with a clear reply: "Your account is waiting for payment confirmation. I saved your account details, but flyer generation starts after activation." Phase 1 does not queue pending-first-flyer briefs because that would introduce another lifecycle state; customers can resend after activation.

## Account Commands

Requester-safe:

- `STATUS`
- `HELP`

Admin-only:

- `ADD AUTHORIZED NUMBER <phone>`
- `REMOVE AUTHORIZED NUMBER <phone>`
- `UPDATE PHONE <phone>`
- `UPDATE WHATSAPP <phone>`
- `CHANGE PLAN <1|2|3|starter|growth|unlimited>`

Admin identity is true when any of these match:

- sender role is `owner`
- sender phone equals `business_whatsapp_number`
- sender phone equals `onboarded_by_phone`

`primary_chat_id` is routing context only, not authorization. Group/shared chat spoofing is denied unless the sender phone is an admin phone. Removing a number is denied if it would leave no authorized requester number; business WhatsApp may also be the requester only when it is explicitly present in `authorized_request_numbers`. `CHANGE PLAN` stores pending plan fields and returns payment instructions; it does not silently change active service.

High-impact admin commands require confirmation. `UPDATE WHATSAPP`, `REMOVE AUTHORIZED NUMBER`, and `CHANGE PLAN` return a pending confirmation message; the same admin must reply `CONFIRM UPDATE` to apply. Phase 1 stores one pending account command per customer in the customer profile notes/structured pending field if added during implementation; if the implementation keeps this smaller, those commands must be denied rather than applied without confirmation.

Canonical commands are deterministic, but common aliases are accepted: `ADD NUMBER`, `ADD AUTH`, `UPDATE BUSINESS PHONE`, and `PLAN STATUS`.

## Activation

Operators or future payment webhooks call:

```bash
manage-flyer-account \
  --activate-customer CUST0001 \
  --payment-reference stripe_pi_123 \
  --provider stripe \
  --expected-plan starter \
  --amount-cents 4999 \
  --currency USD
```

Activation validates:

- customer exists
- provider is allowed
- for new `payment_pending` customers, expected plan must equal current `plan_id`
- for active customers with `pending_plan_id`, expected plan must equal `pending_plan_id`; current-plan renewal does not clear the pending plan
- amount/currency match the configured plan; amount/currency are required for `stripe`, `razorpay`, and `other`, and may be omitted only with explicit `provider=manual`
- payment reference is non-empty
- `(provider, payment_reference)` is globally unique across all customers
- same customer/provider/reference replay is idempotent only when customer, target plan, amount, and currency all match

On success it sets `status=active`, payment fields, activation timestamps, current period start/end, and clears pending plan fields only when the activation target equals `pending_plan_id`. If activation is for a pending plan change, the plan changes only during this activation step. Activation returns customer notification text so cf-router/operator tooling can send: plan active, monthly quota, current usage, and "Send your first flyer request now."

## Quota Flow

Before any generation path, including creation and active-project resume:

1. cf-router creates/resumes the project as it does today only to obtain a `project_id`; no generation runs yet.
2. If the project needs concept generation and has no used/released quota state for this project, cf-router calls `manage-flyer-account --reserve-quota --customer-phone <phone> --project-id <id> --message-id <id>`.
3. The account script rolls the billing period if `now >= current_period_end`, recomputes usage from in-period events, and checks the configured tier limit.
4. If quota is exhausted, it writes `flyer_quota_blocked`, returns a customer-safe message, and cf-router does not generate.
5. If quota is available, it appends an idempotent `reserved` usage event under lock for `reservation_id=<customer_id>:<project_id>`.
6. If concept generation succeeds, cf-router calls `--finalize-usage`, which appends `used` for the same `reservation_id`.
7. If concept generation fails before preview delivery, cf-router calls `--release-quota`, which appends `released` for the same `reservation_id`.

Revisions, approval, final asset export, and media/logo uploads never consume additional quota.

## Routing Order

Within `pre_gateway_dispatch`, Flyer order becomes:

1. forced new flyer request over stale active project
2. account command intercept
3. brand asset intercept
4. active project intercept
5. onboarding intercept for unknown/payment-pending senders
6. primary flyer intent intercept

Account command intercept is before active project so `STATUS` cannot be misread as a design revision. It is before onboarding so payment-pending users can ask payment/status questions.

## Deployment And Smoke

Deploy smoke adds `manage-flyer-account` to the executable list and import probe.

Post-deploy smoke on `main-vps` must use the Windows SSH two-step redirect/read rule and prove:

- onboarding reaches `confirming_summary`
- `CONFIRM` creates `payment_pending`
- manual activation with unique reference succeeds
- replay of the same reference is idempotent
- `STATUS` returns active plan and usage
- starter quota exhaustion blocks generation
- quota exhaustion on an active no-concept project resume also blocks generation
- non-admin `ADD AUTHORIZED NUMBER` is denied
- group/primary-chat spoof without admin phone is denied
- payment-pending `STATUS` explains activation state
- activation with wrong amount is denied
- copied payment reference for another customer is denied
- a normal active customer flyer request still creates one design preview

## Risks

- Payment automation is intentionally incomplete. Mitigation: Phase 1 uses explicit provider/reference activation and records the exact reference for audit.
- Quota reservation can strand reserved events if the process dies mid-generation. Mitigation: `--reserve-quota` treats old reservations for the same project as idempotent, status displays reserved usage, and `--release-quota` can be run manually for failed projects.
- Admin identity is lightweight. Mitigation: only business WhatsApp, onboarding phone, or owner/operator role can mutate account fields. `primary_chat_id` alone is never admin identity. Full OTP admin verification is Phase 2.

## Design Review Findings Applied

- Replaced `primary_chat_id` admin authorization with phone-based `onboarded_by_phone` / business WhatsApp / owner role.
- Made activation target-plan rules explicit so pending plan changes cannot be cleared by current-plan renewal.
- Required amount/currency for non-manual providers and switched payment storage to integer cents.
- Enforced global `(provider, payment_reference)` uniqueness and strict idempotent replay matching.
- Changed quota computation to latest state per deterministic `reservation_id`, preventing reserved+used double counts.
- Required quota reservation before every concept generation path, including active no-concept project resume.
- Made audit path default to production decisions.log instead of optional.
- Added direct confirmation edits, state-aware payment-pending messaging, admin denied smoke, and activation notification text.
