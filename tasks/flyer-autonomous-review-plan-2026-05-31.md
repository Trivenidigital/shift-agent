**Drift-check tag:** extends-Hermes

# Flyer Studio Autonomous Review

## New primitives introduced

- No new substrate. This review fixes Flyer-specific deterministic routing, lifecycle, recovery, and customer-output defects found by code review.

## Drift-rule self-checks

| Check | Evidence | Decision |
|---|---|---|
| Read final approval lifecycle path | Read `src/plugins/cf-router/hooks.py` around `_try_flyer_active_project_intercept`. | Fix customer-visible result handling after successful media sends; keep Hermes routing/audit substrate. |
| Read final package sender | Read `src/agents/flyer/scripts/send-flyer-package`. | Preserve existing send/audit mechanics; only classify post-send bookkeeping failures truthfully. |
| Read recovery watchdog | Read `src/agents/flyer/scripts/flyer-recovery-watchdog` and recovery systemd units. | Add action context/failure alerting without new recovery substrate. |
| Read concept selection route | Read `src/plugins/cf-router/hooks.py` around `awaiting_concept_selection` / `awaiting_final_approval`. | Add bounded deterministic selector before revision fallback. |
| Read existing routing tests | Read `tests/test_cf_router_flyer_routing.py` and `tests/test_cf_router_plugin.py`. | Add regression tests in the same style. |

## Hermes-first analysis

Hermes already owns WhatsApp ingress, sender identity, audit chokepoints, dispatch, runtime orchestration, and deployment. The issues in this pass are Flyer business-contract bugs after Hermes has already delivered the message to Flyer code.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress / sender identity | Hermes-native cf-router ingress, identity, and dispatch; no separate skill needed. | Reuse existing Hermes/cf-router ingress and identity. |
| Flyer asset finalization and media send | No external Hermes skill for Flyer final-package bookkeeping; existing Flyer code owns `finalize_and_send_flyer` / `send-flyer-package`. | Reuse existing Flyer sender; only classify post-send bookkeeping failure truthfully. |
| Audit and operator visibility | Hermes audit substrate exists through cf-router `audit_intercepted`; no new audit substrate needed. | Reuse the existing audit chokepoint. |
| Recovery watchdog customer send | Hermes/cf-router send chokepoint exists and requires action context. | Pass `ActionExecutionContext`; do not add a parallel sender. |
| Watchdog process failure alert | No Hermes skill for this Flyer-specific systemd failure edge; source-edit SLA watchdog has the local pattern. | Add systemd `OnFailure=` parity with the existing Flyer watchdog pattern. |
| Natural concept selection | No Hermes skill for bounded concept-ID/ordinal reply parsing. | Add a small selection-only Flyer parser; no routing substrate or LLM bypass beyond the existing cf-router hook. |

Hermes skill-hub check: https://hermes-agent.nousresearch.com/docs/skills lists no built-in, optional, or community skills for Flyer-specific final-package bookkeeping truthfulness.

Awesome Hermes ecosystem check: public catalogs are general skill/integration lists; no reusable Flyer finalization/audit primitive applies. Verdict: build the small Flyer lifecycle check in-tree.

## Customer Improvement Backlog

From a Flyer Studio customer perspective, the largest remaining autonomy gaps are:

- [ ] Final media truthfulness: never send failure copy after media has already reached the customer.
- [ ] Customer retry from `finalizing_assets`: let "send now" retry failed/pending final package delivery when safe.
- [ ] Natural concept selection: accept "first one", "second design", "go with option two" in `awaiting_concept_selection`.
- [ ] Incomplete-project follow-up merge: when Flyer asks for one missing detail, merge the customer's next detail instead of generic prompting.
- [ ] Onboarding single-owner shortcuts: accept "same", "skip", "online only" where safe.
- [ ] Recovery watchdog `OnFailure=` alert parity.
- [ ] Language/content contract: request language should be explicit and translated facts should drive overlays.
- [ ] Typed-edit lane: skip visual model for our-flyer text-only edits.

## Current Slice

- [x] RED test for access/finalize bookkeeping failure after successful media send.
- [x] Implement minimal lifecycle truthfulness fix.
- [x] RED tests for recovery watchdog send `action_context` and failure unit.
- [x] Implement recovery watchdog send context + `OnFailure=` alerting.
- [x] RED tests for natural concept selection and final-stage concept-reference reminder.
- [x] Implement bounded concept-selection resolver before revision fallback.
- [x] Subagent review.
- [x] Focused and full verification.
- [ ] PR, merge, deploy.

## Review Notes

- Customer-safety reviewer and Hermes/drift reviewer both blocked on the same issues: schema-valid audit reason, concept-selection grammar swallowing revisions, and smoke not fail-closing on the new watchdog failure unit.
- Resolved by adding the missing `CfRouterIntercepted.reason` literal, anchoring concept selection to selection-only grammar, routing concept-fragment correction text as revisions, preserving stale concept selections with real concepts, and making the watchdog failure unit mandatory in smoke.
- Verification: `python -m pytest tests/test_cf_router_flyer_routing.py tests/test_flyer_recovery_watchdog.py tests/test_flyer_scripts_static.py tests/test_flyer_project_isolation.py tests/test_flyer_intent_layer.py -q` => 420 passed. `python -m pytest` => 2840 passed, 867 skipped, 42 warnings.
