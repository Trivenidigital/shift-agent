# Flyer Onboarding Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Drift-check tag:** extends-Hermes

**Goal:** Make WhatsApp-native Flyer Studio onboarding ready for the first paying customers by adding confirmation, payment activation, quota enforcement, account commands, and deployment coverage without adding a web app.

**Architecture:** Keep the deployed Hermes pattern: cf-router handles WhatsApp ingress, deterministic Python scripts mutate JSON state under `FileLock`, bridge helpers send replies, and config-driven plan tiers remain in `Config.flyer.plan_tiers`. Add a small account-control layer around the existing Flyer workflow rather than replacing the working flyer generation path.

**Tech Stack:** Python, Pydantic v2 schemas, JSON state under `/opt/shift-agent/state/flyer`, `safe_io.FileLock`/`atomic_write_text`, cf-router plugin hooks, pytest subprocess tests, tarball deploy scripts.

---

**New primitives introduced:** `FlyerUsageEvent`, Flyer account audit entries, account activation command, atomic quota reservation/record helpers, account command handler, onboarding confirmation state.

## Hermes-first Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress and identity | yes - deployed Hermes gateway + `dispatch_shift_agent` + `identify-sender` | use it |
| Text/media delivery | yes - existing bridge `/send` and `/send-media` helpers via `safe_io` | use it |
| JSON state and audit | yes - repo `safe_io.FileLock`, `atomic_write_text`, `ndjson_append` | use it |
| Flyer workflow | yes - existing `src/agents/flyer` scripts/state machine | extend it |
| Skills hub onboarding/payment skill | none found in official bundled skills catalog; official docs list generic skills/MCP, not a Stripe/Razorpay SMB onboarding workflow | build narrow local account lifecycle |
| Stripe/Razorpay collection | no installed local Flyer payment skill found; repo guidance says check `mcp/native-mcp` / vendor MCP before custom provider code | Phase 1 uses provider-agnostic activation and checkout URL metadata for launch speed; webhook/payment-link automation must go connector-first in Phase 2 |
| Plan quotas | none found; plan tiers exist in local Flyer config only | build from scratch as local state logic |
| Account self-service commands | Hermes messaging supports command-like input, no Flyer-specific account command exists | build from scratch |

Awesome Hermes Agent ecosystem check: no purpose-built WhatsApp Flyer Studio onboarding/payment/quota skill is in the documented ecosystem references; this remains SMB-Agents product logic rather than Hermes substrate. Official Hermes docs confirm the relevant substrate is skills/plugins and optional catalog installation, not a ready-made SMB flyer billing workflow.

## Drift Grounding

- Schema work grounded in `src/platform/schemas.py` Flyer models and `LogEntry` union.
- Routing work grounded in `src/plugins/cf-router/hooks.py`, `src/plugins/cf-router/actions.py`, and `src/agents/shift/skills/dispatch_shift_agent/SKILL.md`.
- Script work grounded in existing `src/agents/flyer/scripts/*` subprocess patterns.
- Test work grounded in `tests/test_flyer_onboarding.py`, `tests/test_flyer_schemas.py`, and `tests/test_cf_router_flyer_routing.py`.
- Deploy work grounded in `src/agents/shift/scripts/shift-agent-smoke-test.sh`.

## Scope

In scope for Phase 1:
- Onboarding confirmation summary before creating a payment-pending customer.
- Account lifecycle fields for primary chat, admin requesters, billing period, usage events, pending plan changes, and payment activation metadata.
- Provider-agnostic activation script for manual/Stripe/Razorpay confirmation.
- Atomic quota reservation/finalization around one generated flyer design per new project.
- WhatsApp account commands: `STATUS`, `HELP`, `ADD AUTHORIZED NUMBER`, `REMOVE AUTHORIZED NUMBER`, `UPDATE PHONE`, `UPDATE WHATSAPP`, and `CHANGE PLAN`.
- `BACK` and `RESTART` recovery during onboarding.
- Tests and deploy smoke coverage for the new scripts.

Out of scope for Phase 1:
- Hosted web onboarding portal.
- Full Stripe/Razorpay webhook receiver.
- Multi-tenant central billing dashboard.
- Brand-kit extraction from logos/templates beyond storing/replacing uploaded assets.

## Phase 1 Safety Rules From Plan Review

- Account commands are split into requester-safe and admin-only commands. `STATUS` and `HELP` may be run by any active authorized sender. Mutating commands require the sender phone to be the `business_whatsapp_number`, the onboarding phone, owner/operator role, or a future verified admin. `primary_chat_id` is routing context only, not admin authorization. The system must not remove the last reachable requester number.
- Payment activation is idempotent. The same `payment_reference` can be replayed for the same customer/plan/provider and returns success without double-counting. A different reference for an already-active customer is rejected unless it matches a pending plan change.
- Payment activation validates customer id, provider, expected pending plan, optional amount/currency, and current lifecycle state before setting `active`.
- Usage events are the source of truth. `monthly_flyers_used` is display/cache only and is recomputed from latest event state per reservation within the current billing period whenever quota is checked.
- Quota is reserved atomically under `FileLock` before every concept generation path, including active-project resume, and finalized after generation succeeds. A failed generation releases the reservation. Revisions and final exports do not consume quota.
- Account lifecycle actions emit durable audit entries in the `LogEntry` union, not only cf-router intercept rows.
- Account commands are intercepted before active-project and onboarding handlers so payment-pending customers can ask for status/help and active customers do not accidentally turn `STATUS` into a flyer revision.

## Task 1: Schema And Store Extensions

**Files:**
- Modify: `src/platform/schemas.py`
- Test: `tests/test_flyer_schemas.py`

- [ ] Add onboarding status `confirming_summary`.
- [ ] Add `FlyerUsageEvent` with fields: `reservation_id`, `project_id`, `customer_id`, `kind` (`reserved|used|released`), `count`, `recorded_at`, `message_id`.
- [ ] Extend `FlyerCustomerProfile` with `primary_chat_id`, `onboarded_by_phone`, `activated_at`, `plan_started_at`, `current_period_start`, `current_period_end`, `monthly_flyers_used`, `payment_reference`, `payment_amount_cents`, `payment_currency`, `pending_plan_id`, `pending_plan_checkout_url`, `pending_plan_requested_at`, and `usage_events`.
- [ ] Add helper methods on `FlyerCustomerProfile`: `included_flyer_limit(plan_tiers)`, `usage_count_for_current_period()`, `quota_remaining(plan_tiers)`, `can_create_flyer(plan_tiers)`, and `is_account_admin(sender_phone, chat_id, sender_role)` where `chat_id` alone never grants admin.
- [ ] Add store helpers to find customers by ID and apply updates without changing JSON storage.
- [ ] Add audit entries to `LogEntry`: `flyer_customer_created`, `flyer_customer_activated`, `flyer_account_updated`, `flyer_usage_recorded`, and `flyer_quota_blocked`.
- [ ] Write tests proving old customer JSON without new fields still validates, active starter quota blocks at 30 latest active reservations in-period, reserved+used for same reservation counts once, unlimited does not block, period rollover resets derived usage, and onboarding status literal accepts `confirming_summary`.

Run:
`pytest tests/test_flyer_schemas.py -q`

Expected: flyer schema tests pass.

## Task 2: Onboarding Confirmation And Recovery

**Files:**
- Modify: `src/agents/flyer/onboarding.py`
- Modify: `src/agents/flyer/scripts/handle-flyer-onboarding`
- Test: `tests/test_flyer_onboarding.py`

- [ ] Implement `BACK`, `RESTART`, and `HELP` for in-progress sessions.
- [ ] After plan selection, move to `confirming_summary` instead of directly creating a customer.
- [ ] Render a compact summary containing business name, address, public phone, business WhatsApp, authorized request number, language/category, selected plan, and uploaded logo/template count.
- [ ] On `CONFIRM`, create the `payment_pending` customer, set `primary_chat_id`, attach pending assets, and generate the checkout URL.
- [ ] Set `onboarded_by_phone` from the sender phone at confirmation time.
- [ ] Support confirmation edits such as `EDIT NAME: ...`, `EDIT ADDRESS: ...`, `EDIT PHONE: ...`, `EDIT WHATSAPP: ...`, `EDIT AUTHORIZED: ...`, and `EDIT PLAN: 1`.
- [ ] Preserve existing payment-pending behavior: repeated messages from the same customer return the payment instructions instead of restarting onboarding.
- [ ] Make payment-pending replies contextual: `STATUS` and `HELP` explain payment state; logo/template media remains accepted.
- [ ] Update tests to reflect the confirmation step and recovery commands.

Run:
`pytest tests/test_flyer_onboarding.py -q`

Expected: onboarding tests pass with the new summary gate.

## Task 3: Account Lifecycle Scripts

**Files:**
- Create: `src/agents/flyer/account.py`
- Create: `src/agents/flyer/scripts/manage-flyer-account`
- Modify: `src/agents/shift/scripts/shift-agent-smoke-test.sh`
- Test: `tests/test_flyer_onboarding.py`
- Test: `tests/test_flyer_scripts_static.py`

- [ ] Implement command parsing for `STATUS`, state-aware `HELP`, `ADD AUTHORIZED NUMBER <phone>`, `REMOVE AUTHORIZED NUMBER <phone>`, `UPDATE PHONE <phone>`, `UPDATE WHATSAPP <phone>`, and `CHANGE PLAN <plan>`.
- [ ] Pass `sender_phone`, `sender_role`, and `chat_id` into `manage-flyer-account` for authorization decisions.
- [ ] Allow `STATUS`/`HELP` for any active authorized sender or payment-pending customer. Require admin identity for mutating commands.
- [ ] Implement activation command: `manage-flyer-account --activate-customer CUST0001 --payment-reference <ref> --provider <manual|stripe|razorpay|other> --expected-plan <plan_id> [--amount-cents <cents>] [--currency USD]`.
- [ ] On activation, validate state, target plan, global provider/reference uniqueness, and idempotent replay; set `status=active`, `activated_at`, `plan_started_at`, `current_period_start`, `current_period_end`, payment fields, and clear pending plan fields only when activation targets the pending plan.
- [ ] For plan changes, do not silently upgrade paid service. Store `pending_plan_id`, `pending_plan_checkout_url`, and `pending_plan_requested_at`; return payment instructions and leave the current active plan until activation confirms the new plan.
- [ ] Append durable account audit entries for activation, account update, denied account command, usage record, and quota block.
- [ ] Add static smoke coverage so deployment verifies `manage-flyer-account` exists and imports.

Run:
`pytest tests/test_flyer_onboarding.py tests/test_flyer_scripts_static.py -q`

Expected: account command and static script tests pass.

## Task 4: Quota Enforcement In Flyer Creation

**Files:**
- Modify: `src/agents/flyer/account.py`
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Test: `tests/test_cf_router_flyer_routing.py`

- [ ] Add action helpers to run `manage-flyer-account --reserve-quota`, `--finalize-usage`, and `--release-quota`.
- [ ] Before generating a new complete flyer project, block active customers whose plan quota is exhausted and send a WhatsApp message with current usage and plan.
- [ ] Reserve one usage event before generation under customer-state lock; finalize it only after concept generation succeeds.
- [ ] Reserve before every path that calls `trigger_generate_flyer_concepts`, including active no-concept project resume.
- [ ] Release the reservation if generation fails before preview delivery.
- [ ] Do not charge revisions, finalization/export, logo/template uploads, or failed generation attempts.
- [ ] Ensure old active projects can still receive revisions/approval without a new quota hit.

Run:
`pytest tests/test_cf_router_flyer_routing.py -q`

Expected: quota helper behavior and routing guard tests pass.

## Task 5: Router Account Commands

**Files:**
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Test: `tests/test_cf_router_flyer_routing.py`

- [ ] Detect account commands before active flyer-project revision routing.
- [ ] Route commands from active or payment-pending Flyer customers to `manage-flyer-account`.
- [ ] Place the command intercept before active-project and onboarding intercepts in `pre_gateway_dispatch`.
- [ ] Send the returned reply through `send_flyer_text`.
- [ ] Audit cf-router outcomes with existing `cf_router_intercepted` entries.
- [ ] Confirm ordinary flyer requests still enter `_try_flyer_primary_intercept`.

Run:
`pytest tests/test_cf_router_flyer_routing.py -q`

Expected: account commands bypass flyer design state and normal flyer requests still work.

## Task 6: Documentation, Backlog, And Verification

**Files:**
- Modify: `tasks/todo.md`
- Modify: `tasks/lessons.md` only if a new correction/failure pattern is discovered
- Modify: `docs/superpowers/specs/2026-05-15-flyer-onboarding-phase1-design.md`

- [ ] Keep `tasks/todo.md` updated as each phase gate completes.
- [ ] Add final design doc after plan review.
- [ ] Run focused test suite:
  - `pytest tests/test_flyer_schemas.py tests/test_flyer_onboarding.py tests/test_flyer_scripts_static.py tests/test_cf_router_flyer_routing.py -q`
- [ ] Run syntax checks for changed scripts:
  - `python -m py_compile src/agents/flyer/onboarding.py src/agents/flyer/account.py src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py`
- [ ] Run deterministic post-deploy smoke on `main-vps` using the Windows SSH two-step redirect/read pattern:
  - onboarding summary reaches `confirming_summary`
  - manual activation with a unique reference succeeds and replay is idempotent
  - `STATUS` command returns active plan/usage
  - exhausted starter quota blocks a new generated flyer
  - ordinary one-shot flyer request still reaches generation and preview delivery

## Review Gates

- Plan review: two parallel agents, one Hermes-first/scope reviewer and one production-risk reviewer.
- Design review: two parallel agents, one schema/state reviewer and one WhatsApp UX/payment reviewer.
- PR review: three parallel agents with orthogonal lenses: Hermes-first scope, security/payment/auth, and production deploy/runtime.

## Plan Review Findings Applied

- Reviewer 1 and 2 both flagged missing account mutation authorization. Plan now requires role split, sender metadata to account scripts, admin-only mutation tests, and no last-admin removal.
- Reviewer 1 flagged idempotent activation and payment fact validation. Plan now requires expected plan, provider, optional amount/currency, and duplicate-reference semantics.
- Reviewer 1 flagged quota period rollover and check/record race. Plan now uses usage events as source of truth plus atomic reserve/finalize/release.
- Reviewer 2 flagged durable audit. Plan now adds account and quota audit entries to `LogEntry`.
- Reviewer 2 flagged router placement. Plan now requires account command intercept before active-project and onboarding routing.
- Reviewer 2 flagged payment connector posture. Hermes-first analysis now records `mcp/native-mcp`/vendor MCP as the Phase 2 connector-first path while keeping Phase 1 provider-agnostic for immediate paying-customer launch.
