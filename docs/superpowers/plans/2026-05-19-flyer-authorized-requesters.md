# Flyer Authorized Requesters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow every Flyer Studio business account, including Free Trial accounts, to have up to 2 authorized requester phone numbers with shared account-level flyer quota.

**Architecture:** Reuse the existing Hermes/Flyer account profile, sender lookup, account command, and quota reservation paths. Add a product cap at the customer profile schema and account-command mutation boundary; quota remains counted by `customer_id` through existing `FlyerUsageEvent` reservations.

**Tech Stack:** Python, Pydantic v2 schemas, existing JSON state via `safe_io`, existing `manage-flyer-account` command path.

---

**Drift-check tag:** `extends-Hermes`

**New primitives introduced:** none.

**Hermes-first analysis:**

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Sender identity and routing | Existing Hermes/cf-router sender identity + `FlyerCustomerStore.find_customer_by_phone` | Use existing path. |
| Account state mutation | Existing `manage-flyer-account` + `agents.flyer.account` lock/audit path | Extend current command behavior only. |
| Quota tracking | Existing `FlyerUsageEvent` on `FlyerCustomerProfile` | Use account-level quota; no per-requester quota store. |

Awesome Hermes ecosystem check verdict: no external ecosystem skill is needed because this is a local account policy on top of existing Flyer Studio state.

## Tasks

### Task 1: Add product-policy tests

**Files:**
- Modify: `tests/test_flyer_onboarding.py`

- [x] Add a test that an admin can add a second authorized requester but a third requester is rejected with: `Remove one before adding another.`
- [x] Add a test that quota reservations from two authorized requesters count against the same customer account usage.
- [x] Run the new tests and confirm they fail before implementation.

### Task 2: Enforce max 2 authorized requesters

**Files:**
- Modify: `src/platform/schemas.py`
- Modify: `src/agents/flyer/account.py`

- [x] Add a shared account-policy constant for max authorized requesters.
- [x] Lower `FlyerCustomerProfile.authorized_request_numbers` schema `max_length` to that policy.
- [x] In `add_authorized`, reject new third numbers with the customer-facing message.
- [x] Keep idempotent re-add of an existing number successful.
- [x] Ensure `update_whatsapp` does not accidentally exceed the cap when adding the new business WhatsApp as an authorized requester.

### Task 3: Verify

**Files:**
- Test: `tests/test_flyer_onboarding.py`
- Test: `tests/test_flyer_schemas.py`
- Static: `src/platform/schemas.py`, `src/agents/flyer/account.py`

- [x] Run focused Flyer account/onboarding tests.
- [x] Run schema tests.
- [x] Compile changed Python files.
- [x] Review `git diff --check`.
