# Flyer Studio QA — re-execution of 117 scenarios after PR #106 merge

**Date:** 2026-05-19
**Code state:** `main` at `8f9b59e` (PR #106 merged + deployed as `deploy-20260518-234351-8f9b59eb` on main-vps)
**Workbook source:** `tasks/flyer-studio-qa-scenarios.xlsx`
**Drift-check tag:** extends-Hermes
**New primitives introduced:** None. QA execution only.

## Result summary

| Area | Pass | Fail / spec-mismatch | N/A | Total |
|---|---:|---:|---:|---:|
| A1 Onboarding | 24 | 2 | 0 | 26 |
| A2 Text Mode + Starter Briefs | 14 | 1 | 0 | 15 |
| A3 Image / Reference Scope | 15 | 0 | 0 | 15 |
| A4 Active Project / Revisions / Source-Edit | 13 | 1 | 1 | 15 |
| A5 Guest Orders | 14 | 0 | 0 | 14 |
| A6 Admin Dashboard | 16 | 0 | 1 | 17 |
| A7 cf-router Routing | 15 | 0 | 0 | 15 |
| **Total** | **111** | **4** | **2** | **117** |

Focused pytest baseline: **272 passed** across the union of `tests/test_flyer_*.py`, `tests/test_dispatcher_accuracy_report.py`, `tests/test_catering_proposal_schemas.py`, and `web/backend/tests/test_flyer_admin.py`.

## Confirmed regressions / mismatches

### BUG-FLYER-QA-2026-05-19-001 (P1) — Trial-path BACK from confirming_summary drops user into paid-plan chooser

**Scenario:** FS-A1-015

**Location:** `src/agents/flyer/onboarding.py` line 464 — `_BACK_TRANSITIONS["confirming_summary"]` is hardcoded to `("choosing_plan", {"plan_id": ""})`.

**Cause:** Trial sessions skip the `choosing_plan` step entirely (`plan_id="trial"` is set during welcome). When such a user presses BACK at the summary screen, the static back-chain still routes to `choosing_plan` and resets `plan_id=""`, dropping the trial user into the paid plan menu and losing their trial enrolment.

**Impact:** A trial customer who notices a typo at the summary and presses BACK to fix it cannot retain `plan_id="trial"` — they must re-trigger the trial CTA. Not data-loss-grade but a real UX dead-end.

**Fix sketch:** Make `_BACK_TRANSITIONS["confirming_summary"]` plan-aware. When `session.plan_id == "trial"`, route back to `collecting_business_profile` (skipping `choosing_plan`). Add a regression test in `tests/test_flyer_onboarding.py` covering trial-BACK-from-summary.

**Test coverage gap:** No existing test exercises BACK on a trial session.

### BUG-FLYER-QA-2026-05-19-002 (P1) — Language menu order in code disagrees with workbook positions 4-8

**Scenario:** FS-A2-012

**Location:** `src/agents/flyer/intake.py` `LANGUAGES` list around lines 32-44.

**Cause:** Workbook scenario expects `4=Tamil, 5=Kannada, 6=Malayalam, 7=Marathi, 8=Gujarati`. Deployed code orders them `4=Malayalam, 5=Tamil, 6=Kannada, 7=Gujarati, 8=Marathi`. The menu the customer sees IS internally consistent (the rendered menu matches what gets parsed and stored), so a customer typing "5" gets Tamil and the welcome reply correctly says "Great. I will use Tamil." No customer is misrouted relative to what they see.

**Impact:** Two-sided:
- If the workbook order is canonical (e.g., the operator launch deck or printed reference card uses 5=Kannada), the deployed build will appear to give wrong languages to customers steered to a specific number.
- If the deployed order is canonical, the workbook is the source of error.

**Recommendation:** Pick one as canonical and align the other. The simpler fix is to update the workbook to match deployed code (since customers parse what they see, not the workbook).

**Test coverage gap:** No existing test pins the menu ordering. Add one that constructs the language prompt and asserts the line `"5. Tamil"` (or whichever ordering is chosen as canonical).

### BUG-FLYER-QA-2026-05-19-003 (P2, design intent) — `should_start_new_flyer_over_active` returns False for vague "start a new flyer" phrasing

**Scenario:** FS-A4-006

**Location:** `src/plugins/cf-router/actions.py` `is_vague_flyer_start` short-circuits before `should_start_new_flyer_over_active` (and the latter's `_NEW_FLYER_REQUEST` regex) at the hook layer. Existing test `tests/test_cf_router_flyer_routing.py:462-464` explicitly asserts `should_start_new_flyer_over_active("Create a flyer")` returns False.

**Cause:** "start a new flyer for next week" has no detail signals (`$`, `:`, time pattern, weekday/sale/special/etc.), so `is_vague_flyer_start=True`. The vague branch hits first in `pre_gateway_dispatch` and routes to the starter-brief intercept (or starter-brief-already-sent fallback). The workbook scenario expected `should_start_new_flyer_over_active=True` and a force-new project — current implementation instead surfaces a starter brief.

**Impact:** When an active-trial customer types a vague "start a new flyer" message, they receive a starter brief rather than entering a fresh project pipeline. Whether this is a bug depends on product intent: starter briefs are designed to disambiguate vague requests, so this is a deliberate routing choice. The workbook scenario was written before the starter-brief gate landed (PR #102).

**Recommendation:** Update the workbook scenario to reflect the starter-brief gate (expected: starter brief sent). If product wants the force-new path for `_NEW_FLYER_REQUEST` patterns regardless of detail, the hook ordering needs revision — but that would diverge from the deliberate starter-brief gating.

### BUG-FLYER-QA-2026-05-19-004 (P3, spec ambiguity) — Authorized-request SKIP scenario unreachable in normal flow

**Scenario:** FS-A1-010

**Location:** Scenario precondition states "Onboarding before public phone collected" but the step under test is `collecting_business_whatsapp`, which only follows `collecting_public_phone` in the state machine. Trying to be at `collecting_business_whatsapp` before `public_phone` is saved is structurally impossible in normal flow.

**Cause:** Workbook scenario combines an unreachable precondition with the SKIP-validation error message, so the test as written cannot run.

**Recommendation:** Rewrite the scenario. Pick one of:
- "At `collecting_business_whatsapp` with public_phone saved, reply SKIP → next step prompt sent" (happy path).
- Or remove the precondition mismatch entirely; the SKIP-after-public-phone guard at `onboarding.py:772-776` is currently dead code under normal flow.

## Hygiene notes (no scenario failed)

1. `CfRouterIntercepted.reason` Literal in `src/platform/schemas.py` lists `flyer_starter_brief` (lines 3608 + 3624) and `flyer_customer_not_active` (lines 3609 + 3627) twice each. Pydantic v2 accepts duplicates without error, but the values are redundant and should be deduped in a future housekeeping pass.

2. Welcome reply: workbook says `"Absolutely, lets create..."` (no apostrophe); code emits `"Absolutely, let's create..."` (with apostrophe). Cosmetic transcription issue in the workbook; deployed code is correct.

## Items deferred as N/A

- **FS-A6-001** — Operator login + OTP flow. Requires a live FastAPI session + Pushover delivery. Not exercisable from the unit suite; covered in earlier rounds via manual smoke. No change since PR #106.
- **FS-A4-014** — Active project + paid guest order interaction. The code path exists (`hooks.py:173-179` checks `find_paid_flyer_guest_order` before `force_new=True`), partial contract is unit-tested, but the end-to-end requires a live `FLYER_GUEST_ORDERS_PATH` state file on VPS.

## Verification matrix

| Bug ID | Severity | File:line | Test coverage gap |
|---|---|---|---|
| 2026-05-19-001 | P1 | `src/agents/flyer/onboarding.py:464` | No trial-BACK-from-summary test |
| 2026-05-19-002 | P1 | `src/agents/flyer/intake.py:32-44` | No menu-ordering test |
| 2026-05-19-003 | P2 (intent) | `src/plugins/cf-router/actions.py` (`is_vague_flyer_start` precedence) | Tested at routing layer; workbook out-of-date |
| 2026-05-19-004 | P3 (spec) | Workbook only | Scenario rewrite needed |

## Verification commands run

```text
python -m pytest tests/test_flyer_onboarding.py tests/test_flyer_guest_order.py tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py tests/test_flyer_starter_briefs.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py tests/test_flyer_delivery_retry.py tests/test_dispatcher_accuracy_report.py tests/test_catering_proposal_schemas.py web/backend/tests/test_flyer_admin.py -q
Result: 272 passed
```

Previous round's confirmed bugs (BUG-FLYER-QA-001 through 005) are all verified fixed in the deployed code at commit `8f9b59e`:
- BUG-001 idempotency: `_find_consumed_guest_order` present + tested
- BUG-002 pagination: offset/limit/total/truncated returned + frontend wired
- BUG-003a/003b: schema Literal includes new reasons + report whitelists Flyer reasons
- BUG-004: Hermes scrubbed from customer-facing footer (both renderer paths)
- BUG-005: POSIX gate on `safe_io.atomic_write_text` parent-dir fsync
