**Drift-check tag:** extends-Hermes

# Flyer Routing And Preview Tripwires Plan

Date: 2026-05-21

## Goal

Catch Flyer Studio silent routing and preview/final QA failures before they become customer-visible stale-context flows. This is a Hermes-first observability/safety slice: reuse cf-router, decisions.log, existing Flyer JSON state, self-evaluation, and operator brief. No provider policy, customer copy, dashboard UI, deploy, or production mutation.

## New primitives introduced

- Flyer fresh-new-request detector for active-project routing.
- Lightweight routing-decision preview helper for tests/self-eval evidence.
- Self-eval incidents for fresh request routed as revision, latest request not reflected, and preview-approved-final-QA failure.

## Hermes-first checklist

| Step | Hermes-owned? | Decision |
|---|---|---|
| WhatsApp sender identity/chat routing | yes | reuse existing cf-router/Hermes identity helpers |
| Inbound message capture/audit | yes-ish | reuse existing decisions.log / cf-router audit chokepoints |
| Active project lookup | Flyer-specific | reuse existing Flyer state helpers |
| New-request-vs-revision policy | Flyer-specific | implement as Flyer helper, but check whether existing Hermes/cf-router classifiers already cover it |
| Operator alert/reporting substrate | Hermes/operator brief | extend existing self-eval/operator brief, do not create a new daemon unless necessary |
| Audit emission | Hermes pattern | use existing safe_io / LogEntry / audit helper patterns |
| Provider routing/source-edit | out of scope | do not touch provider policy |
| Customer copy | out of scope | do not rewrite WhatsApp copy in this PR |

## Drift checks performed

- `AGENTS.md`, `docs/hermes-alignment.md`, `tasks/lessons.md`, and `tasks/todo.md`.
- Recent Flyer source-contract/provider/self-evaluation docs under `docs/superpowers/plans/` and `docs/superpowers/specs/`.
- `src/plugins/cf-router/actions.py` and `src/plugins/cf-router/hooks.py`.
- Existing routing/self-eval/operator tests: `tests/test_cf_router_flyer_routing.py`, `tests/test_flyer_self_evaluation.py`, `tests/test_operator_brief.py`.

Finding: cf-router already owns sender identity, active-project lookup, status/revision routing, and deterministic new-project creation. No generic Hermes classifier already covers the live “help me with evening snacks flier from 4 PM to 7 PM” shape, so the net-new code is limited to Flyer policy helpers plus report-only incidents.

## Implementation steps

- Add tests first for the live evening-snacks phrase, revision/status/approval false positives, active-project bypass, latest-request-not-reflected self-eval, preview-approved-final-QA failure, operator brief ordering, and redaction.
- Add a pure fresh-new-request helper in `actions.py` and have `should_start_new_flyer_over_active` use it.
- Add a minimal routing-decision preview helper that reports route, selected project, reason, fresh flag, bypass flag, and latest message id without mutating state.
- In `_try_flyer_active_project_intercept`, let fresh new requests bypass the revision branch and fall through to the existing forced-new project flow.
- Extend `tools/flyer-self-evaluation.py` with read-only incident rules for stale latest-request reflection and preview-approved-final-QA mismatch.
- Extend `tools/operator-brief.py` only if needed to group the new incidents as active customer risk before historical/audit items.

## Out of scope

- Provider/source-edit policy changes.
- Customer WhatsApp copy changes.
- Production/VPS/manual-queue mutation or deploy.
- Dashboard UI.
- Runtime self-modification.

## Verification

Run focused offline tests, py_compile for touched Python, self-eval fixture smoke, and `git diff --check`. No merge and no deploy.

## Deferred

- Full LLM/Hermes classifier for new-vs-revision if regex helper is insufficient.
- Operator push alert for active customer-risk incidents.
- Dashboard active-risk lane.
- Source-contract facts enforced as locked facts and QA blockers.
