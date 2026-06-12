# Flyer Hermes Intent Layer Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. This is a staged plan: plan review -> design review -> TDD build -> PR review.

**Drift-check tag:** `extends-Hermes` - move Flyer judgment toward Hermes, while preserving the existing cf-router, audit, state, customer-copy policy, and self-evaluation substrate.

**Goal:** Add the first production-safe "Hermes = brain, Flyer code = contract/safety harness" layer for Flyer Studio. The new layer must reduce whack-a-mole routing failures by centralizing intent decisions behind a strict JSON contract, deterministic validation, shadow-mode audit, and self-evaluation feedback. Default runtime behavior must remain unchanged unless explicitly configured later.

**Stopped-session finding:** The previous session died before implementation. The branch `feat/flyer-hermes-intent-layer` was still at `origin/main` with no code/design/plan changes beyond one untracked Hermes receipt.

## Plan Review Folds

| Finding | Fold |
|---|---|
| Plan initially landed in the primary checkout, not the worktree | Moved the plan into `C:\projects\sme-agents-flyer-intent`; no implementation should touch the dirty root checkout |
| Do not add a new router | Intent module is pure contract/validation/training-example logic consumed by existing cf-router; current hook order stays authoritative |
| Do not duplicate source-contract QA | Route intent is separate from `FlyerSourceContract`; source/reference obligations remain in existing locked-fact/source-aware QA paths |
| Active mode could be accidentally enabled by env | PR-1 accepts only `off` and `shadow` as supported modes. `low_risk_active` and `active` parse as `unsupported_active_mode`, stay inert, and must be reported |
| Simplified route preview could create false disagreement evidence | Shadow audit must record the actual live route chosen by cf-router after the branch runs. Simulated preview may be included only with `preview_source=simulated` |
| Audit schema needs attribution fields, not a vague detail blob | Typed audit row will carry schema_version, mode, decision_source, message_id, hashed chat key, has_media, validator fields, advisory fields, actual_route fields, risk_scope, and `live_route_changed=false` |
| Shadow rows could silently stop emitting | Self-eval adds `shadow_coverage_missing` when Flyer cf-router intercepts exist but no intent rows exist in shadow/unsupported-active mode |
| Active risk too narrowly tied to active project rows | Add `risk_scope` values: `active_project`, `active_customer`, `active_intake`, `pre_project_customer_visible`, `historical_audit` |
| Shadow boundary needs mechanical mutation tests | Tests spy on send/trigger/invoke/write surfaces and prove shadow helper only attempts the typed audit append |
| No live Hermes call means no claim that Hermes catches failures yet | PR-1 acceptance is contract + actual route-trace observability. Decision source is `none|fixture|deterministic_baseline|hermes_gateway_future`; self-eval does not treat missing classifier output as agreement |

## Drift-Check Findings

| Area | Existing primitive found | Residual gap |
|---|---|---|
| Hermes plugin routing | `src/plugins/cf-router/hooks.py::pre_gateway_dispatch` is the single inbound interception point | Flyer judgment is scattered across regex helpers and status branches |
| Flyer routing preview | `actions.flyer_routing_decision_preview()` delegates to current routing rules | It previews the current router; it is not a Hermes intent contract |
| Fresh-request guard | `should_start_new_flyer_over_active()` and `should_bypass_active_flyer_project_for_fresh_request()` protect recent failure classes | They remain regex/domain fragments and cannot generalize to messy WhatsApp language |
| Customer copy policy | `src/agents/flyer/customer_copy_policy.py` centralizes banned terms and duplicate ack markers | Intent-layer customer replies need to pass this policy before any future live use |
| Audit substrate | `CfRouterIntercepted` and typed Flyer audit entries exist in `src/platform/schemas.py` | No typed row for Hermes intent decision, validator result, and live-route comparison |
| Replay/self-eval | `tests/_flyer_replay_helpers.py`, `test_flyer_incident_replay.py`, `tools/flyer-self-evaluation.py` | No incident type for Hermes-vs-current-router disagreement or rejected Hermes decisions |
| Operator brief | `tools/operator-brief.py` groups active Flyer risks | No Hermes intent shadow-mode grouping |
| Operating-layer reporting | PR #169 docs/tools report Hermes capability readiness | Strategic readiness exists, but not a runtime intent layer |

## Hermes-First Analysis

| Step | Hermes or net-new? | Decision |
|---|---|---|
| WhatsApp ingress, sender identity, media cache, bridge delivery | `[Hermes]` | Continue using cf-router/Hermes hook and existing identity helpers |
| LLM/vision gateway and future classifier call | `[Hermes]` | Define prompt + strict schema adapter; do not hardcode provider calls in PR-1 |
| Memory/session learning | `[Hermes]` | Emit training/evaluation examples through audit/reporting; production memory writes remain future work |
| Background tasks / Kanban / Codex | `[Hermes]` | Backlog for later; not needed for the first intent contract |
| Flyer product semantics | `[net-new]` | Flyer owns intent labels, legal transitions, source-edit boundaries, account updates, copy policy |
| Contract validation | `[net-new]` | Deterministic validator rejects unsafe/low-confidence/malformed decisions |
| Audit and self-evaluation | `[Hermes]` substrate + `[net-new]` Flyer policy | Use existing decisions.log/self-eval/operator-brief paths; add Flyer-specific incidents |

Awesome-Hermes-Agent ecosystem check: Hermes provides the orchestration/memory/tool substrate, but no Flyer-specific intent schema or validator exists. Verdict: build only the Flyer product contract and validator; keep Hermes as the future classifier brain.

## Scope

In scope:
- Add a pure Flyer intent module with:
  - canonical Hermes prompt text,
  - strict `FlyerIntentDecision` Pydantic model,
  - deterministic `FlyerIntentValidationResult`,
  - mode parser for `FLYER_HERMES_INTENT_MODE=off|shadow|low_risk_active|active`,
  - read-only current-router comparison helpers,
  - training/example payload builder.
- Add typed audit row `flyer_hermes_intent_decision`.
- Add shadow-mode audit from cf-router without changing customer behavior.
- Extend self-evaluation and operator brief with Hermes intent incidents/grouping.
- Add focused tests proving no live behavior change by default.
- File follow-up backlog items and mark superseded regex-only backlog where applicable.

Out of scope:
- No live Hermes/LLM network calls in this PR.
- No direct memory writes from production.
- No provider/model/source-edit routing changes.
- No dashboard UI changes.
- No WhatsApp sends in tests.
- No production deploy.
- No deletion of existing deterministic routing.

## Safety Model

Default mode is `shadow`. In shadow:
- Current cf-router behavior remains authoritative.
- The intent layer may produce an advisory decision and validator result.
- Audit/self-eval compare intent vs the actual live route selected by cf-router.
- No customer reply, state mutation, provider call, or router branch changes.

PR-1 mechanically supports only `off` and `shadow`. If an operator sets `FLYER_HERMES_INTENT_MODE=low_risk_active` or `active`, the parser must return `unsupported_active_mode`; live behavior remains identical to `shadow`, and self-evaluation/operator brief must surface the unsupported configuration. Future active modes require a separate PR and promotion criteria.

The only allowed shadow write is the typed audit append. Shadow code must not call bridge sends, media sends, project/customer/manual queue mutation, provider rendering, safe_io atomic writes, or update scripts.

## Task 1: Intent Contract

**Files:**
- Create `src/agents/flyer/intent.py`
- Create `tests/test_flyer_intent_layer.py`

- [ ] Write RED tests for strict schema extra-field rejection.
- [ ] Write RED tests for banned customer copy rejection.
- [ ] Write RED tests for low-confidence mutating decision rejection.
- [ ] Write RED tests for source-edit automated actions being rejected unless explicitly allowed.
- [ ] Write RED tests that `active` and `low_risk_active` parse to inert `unsupported_active_mode`.
- [ ] Implement `FlyerIntentDecision`, `FlyerIntentValidationResult`, literals, prompt builder, and validator.
- [ ] Implement `mode_from_env()` and `build_training_example()`.

## Task 2: Audit Contract

**Files:**
- Modify `src/platform/schemas.py`
- Modify `src/plugins/cf-router/actions.py`
- Test via `tests/test_log_entry_forward_compat.py` or a new intent audit test.

- [ ] Add `FlyerHermesIntentDecision` audit row with:
  - `schema_version`
  - `mode`
  - `decision_source`
  - `message_id`
  - `chat_key_hash`
  - `has_media`
  - `validator_ok`
  - `validator_reasons`
  - `advisory_intent`
  - `advisory_action`
  - `confidence`
  - `would_mutate`
  - `actual_route`
  - `actual_reason`
  - `selected_project_id`
  - `project_status`
  - `customer_status`
  - `intake_status`
  - `preview_source`
  - `live_route_changed=false`
  - `active_customer_risk`
  - `risk_scope`
- [ ] Add it to the `LogEntry` union.
- [ ] Add `actions.audit_flyer_hermes_intent_decision(...)` using the existing `log-decision-direct` chokepoint pattern.
- [ ] Test known-type validation and malformed-row rejection.

## Task 3: Shadow Integration

**Files:**
- Modify `src/plugins/cf-router/hooks.py`
- Modify `src/plugins/cf-router/actions.py` if helper placement is cleaner.
- Test `tests/test_cf_router_flyer_routing.py`.

- [ ] Add a shadow helper that runs only for Flyer-shaped messages or active Flyer context.
- [ ] Record the actual live route after the authoritative cf-router branch returns.
- [ ] Compare advisory intent with the actual live route only when `decision_source != "none"`.
- [ ] Use `flyer_routing_decision_preview()` only for read-only context fields, marked `preview_source=simulated`.
- [ ] Emit `flyer_hermes_intent_decision` best-effort; audit failures must not block routing.
- [ ] Prove the same inbound still returns the same route/customer sends as before.
- [ ] Prove mode `off` emits no intent audit.
- [ ] Prove unsupported active modes emit inert audit/report evidence and no live route changes.
- [ ] Spy on bridge/send/trigger/invoke/write surfaces to prove shadow cannot mutate outside the typed audit append.

## Task 4: Self-Eval and Operator Brief

**Files:**
- Modify `tools/flyer-self-evaluation.py`
- Modify `tools/operator-brief.py`
- Tests: `tests/test_flyer_self_evaluation.py`, `tests/test_operator_brief.py`

- [ ] Add incidents:
  - `hermes_intent_rejected_by_validator`
  - `hermes_intent_live_route_disagreement`
  - `hermes_intent_would_clarify_but_router_mutated`
  - `hermes_intent_shadow_coverage_missing`
  - `hermes_intent_unsupported_active_mode`
- [ ] Mark active customer risk using `risk_scope`: `active_project`, `active_customer`, `active_intake`, `pre_project_customer_visible`, or `historical_audit`.
- [ ] Add operator brief grouping for Hermes intent shadow findings.
- [ ] Keep historical rows separate from active customer-risk rows.

## Task 5: Backlog and Lessons

**Files:**
- Modify `tasks/todo.md`
- Modify `tasks/lessons.md` only for actual corrections discovered during this run.

- [ ] File follow-ups for live Hermes classifier call, shadow acceptance thresholds, memory/self-learning ingestion, active low-risk mode, and regression replay expansion.
- [ ] Mark any regex-only router expansion backlog as superseded by "Hermes intent layer shadow -> active" when it is clearly duplicate.
- [ ] Keep source-edit provider, dashboard, and video/social/publishing items separate.

## Task 6: Verification and PR

- [ ] Run focused pytest:

```powershell
python -m pytest tests/test_flyer_intent_layer.py tests/test_cf_router_flyer_routing.py tests/test_flyer_self_evaluation.py tests/test_operator_brief.py tests/test_log_entry_forward_compat.py -q
```

- [ ] Run py_compile for touched Python files.
- [ ] Run self-eval CLI smoke on existing fixture.
- [ ] Run `git diff --check`.
- [ ] Open PR with "No deploy performed."
- [ ] Request two PR reviewers:
  1. Live-behavior reviewer: trace shadow audit through current routing and prove no customer behavior change.
  2. Hermes-first/runtime reviewer: prove no duplicate substrate, no live mutation, no false active-mode enablement.
- [ ] Fold Critical/High reviewer findings.

## Acceptance

- The intent layer exists as a strict contract and validator, not another free-form regex router.
- Shadow mode produces typed audit evidence without changing runtime behavior.
- Unsupported active modes are mechanically inert and visible to operators.
- Shadow coverage gaps are visible when Flyer traffic exists but intent audit rows do not.
- Customer-copy policy blocks unsafe intent replies before future active use.
- Self-evaluation can surface Hermes disagreements/rejections as active-risk or historical findings.
- Operator brief makes those findings visible without spamming old rows.
- Existing #150/#151/#152/#157/#161 behaviors stay intact.
- No provider behavior, dashboard behavior, production state, or WhatsApp sends change.
