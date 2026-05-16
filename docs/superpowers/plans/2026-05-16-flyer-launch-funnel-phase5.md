# Flyer Launch Funnel Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a WhatsApp-native marketing launch funnel for Hermes Flyer Studio with a hard-limited three-sample free trial and ready-to-send customer acquisition copy/assets.

**Architecture:** Reuse the deployed Flyer account, onboarding, quota, WhatsApp ingress, and bridge delivery paths. Add the free trial as a config-driven plan/account status so the existing quota reservation blocks the fourth sample, then add marketing documentation that points prospects to click-to-WhatsApp onboarding rather than cold outbound blasting.

**Tech Stack:** Python, Pydantic v2 schemas, JSON Flyer customer state, existing Flyer account/onboarding scripts, cf-router WhatsApp hooks, pytest, Markdown marketing assets.

---

**Drift-check tag:** extends-Hermes

**New primitives introduced:** trial account status, default `trial` plan tier, trial onboarding entry intent/copy, launch marketing message pack.

## Hermes-first Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress/identity | yes - deployed Hermes gateway, cf-router, sender validation | use it |
| Flyer generation and delivery | yes - deployed Flyer Studio scripts, media bridge, text QA, delivery report | use it |
| Plan quotas | yes - local Flyer account quota/event model | extend with trial tier |
| Marketing social posting | partial - Hermes ecosystem has generic social/browser/MCP skills such as `xurl`; no Flyer Studio funnel skill | build local launch copy and leave channel posting manual for now |
| Payment/onboarding | yes - local Flyer onboarding plus provider-agnostic payment pending state | reuse for paid conversion |
| Compliance/opt-in | no Hermes-specific marketing compliance skill found | document opt-in/click-to-WhatsApp posture |

Awesome Hermes Agent ecosystem check: no ready-made SMB WhatsApp flyer marketing funnel skill was found in the Hermes ecosystem. This phase is narrow product logic and launch collateral on top of Hermes substrate.

## Drift Grounding

- Schema work grounded in `src/platform/schemas.py` Flyer models.
- Account/quota work grounded in `src/agents/flyer/account.py`.
- Onboarding work grounded in `src/agents/flyer/onboarding.py`.
- Router work grounded in `src/plugins/cf-router/actions.py` and `src/plugins/cf-router/hooks.py`.
- Tests grounded in `tests/test_flyer_schemas.py`, `tests/test_flyer_onboarding.py`, and `tests/test_cf_router_flyer_routing.py`.

## Scope

In scope:
- Add default `trial` plan with 3 included flyers and $0 price.
- Allow `trial` customer status to create flyers through the existing quota reserve/finalize flow.
- Add trial start language and trial conversion CTA.
- Make plan-choice prompts adapt to any number of configured tiers.
- Add launch marketing message pack with WhatsApp-safe copy, sample-gallery guidance, onboarding/free-trial CTA, and plan upsell prompts.
- Track Phase 5 in `tasks/todo.md`.

Out of scope:
- Automated cold WhatsApp blasting.
- Stripe/Razorpay webhook automation.
- New web portal.
- Spending real image credits to generate final sales artwork unless explicitly run later.

## Tasks

### Task 1: Trial Tier Schema And Quota Contract

**Files:**
- Modify: `src/platform/schemas.py`
- Test: `tests/test_flyer_schemas.py`
- Test: `tests/test_flyer_onboarding.py`

- [ ] Write failing tests that default tiers include `trial` with 3 flyers, paid tier prices remain unchanged, and a trial customer is blocked after three reserved/used flyer projects.
- [ ] Run the focused tests and confirm they fail because `trial` does not exist or is not treated as a generation-enabled status.
- [ ] Add `trial` as the first default `FlyerPlanTier`.
- [ ] Permit `FlyerCustomerProfile.status == "trial"` while preserving existing paid statuses.
- [ ] Update account status/help/quota copy to mention the onboarding/upgrade CTA when trial quota is exhausted.
- [ ] Run the focused tests and confirm they pass.

### Task 2: WhatsApp Trial Entry And Plan Prompt Copy

**Files:**
- Modify: `src/agents/flyer/onboarding.py`
- Modify: `src/plugins/cf-router/actions.py`
- Test: `tests/test_flyer_onboarding.py`
- Test: `tests/test_cf_router_flyer_routing.py`

- [ ] Write failing tests for `START FREE TRIAL` creating/selecting the `trial` plan and for plan prompts that do not hard-code "1, 2, or 3".
- [ ] Run the tests and confirm they fail on current copy/status behavior.
- [ ] Add trial-start detection as an onboarding intent and preselect trial where appropriate.
- [ ] Update plan prompt/copy to list all configured tiers dynamically.
- [ ] Keep paid onboarding intact for owners who choose paid plans directly.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Launch Marketing Pack

**Files:**
- Create: `docs/marketing/flyer-studio-launch-funnel.md`
- Modify: `tasks/todo.md`
- Test: `tests/test_flyer_scripts_static.py`

- [ ] Write a static test that the launch pack contains the required CTA, three-sample limit, opt-in posture, plan prices, and sample-gallery categories.
- [ ] Run the static test and confirm it fails because the document is missing.
- [ ] Create the launch marketing pack with WhatsApp message, social captions, sample-gallery brief, QR/wa.me CTA, free-trial prompts, and conversion nudges.
- [ ] Update `tasks/todo.md` with Phase 5 checklist.
- [ ] Run the static test and confirm it passes.

### Task 4: Verification And Deploy Readiness

**Files:**
- Existing test suite only.

- [ ] Run focused Flyer tests: `python -m pytest tests/test_flyer_schemas.py tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py -q`.
- [ ] Run script syntax checks for changed Python files.
- [ ] Run `git diff --check`.
- [ ] If tests pass, commit the Phase 5 implementation.

