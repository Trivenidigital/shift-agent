# Backlog — pending items

## Autonomous production-readiness run — 2026-05-29 (Claude builder / Codex reviewer)

**Operating-mode change (reversible):** disabled Codex *builder* timers on main-vps
`codex-flyer-autodev-main.timer` + `codex-production-push-loop-main.timer`
(`systemctl disable --now`; units kept). Left `codex-auth-guard.timer` +
`codex-fleet-telegram-status.timer` enabled (review/auth/visibility). Restore with
`systemctl enable --now <timer>`. Enforces Claude-builds / Codex-reviews split.

**Mandate:** dormant-safe production-readiness hardening only. Fresh branch off
origin/main per task; drift-check + Hermes-first before code; TDD; push + Codex
review on main-vps; merge only after tests/smokes pass + Codex clean. Hard stops:
no secrets, no Stripe activation, no provider flip, no prod mutation beyond read-only
probes / required deploy-smoke, no customer sends, no speculative agents, no PR-4.

- [x] **Item 1 — Commerce slice-3.5 webhook-subscription deploy gate** ✅ MERGED PR #340 (origin/main f3156a3). 20 tests, 3 Codex rounds → CLEAN. Dormant-verified on live VPS. Not yet deployed (dormant-safe).
- [x] **Item 2 — Commerce slice-3.1 Stripe livemode-match deploy gate** ✅ MERGED PR #342 (origin/main 7aa3a10). 13 tests (33/33 both suites), Codex CLEAN round 1. urllib (no SDK). Dormant-verified on live VPS. Not yet deployed.
- [x] **Item 3 — EOD reconcile deploy-smoke coverage** ✅ MERGED PR #343 (origin/main 79f7002). Added `eod-reconcile --force --dry-run` to smoke (snapshot path → temp). Codex 2 rounds → CLEAN. Live-verified.
- [x] **Item 4 — Daily Brief deploy-smoke coverage** ✅ MERGED PR #344 (origin/main c939f98). `send-daily-brief --force --dry-run` (sentinel → temp). Codex CLEAN (after a re-run with file-specific prompt). Live-verified.
- [x] **Item 5 — Catering pattern-report deploy-smoke coverage** ✅ MERGED PR #345 (origin/main 7cac726). `catering-pattern-report --dry-run` (outputs → temp). Codex CLEAN. Live-verified ("0 findings", no new /opt files).
- [x] **Item 6 — Timer-liveness freshness WARN (EOD #5 + Daily Brief #4)** ✅ MERGED PR #346 (origin/main 44b1c18). §12a freshness done safely (read-only, enabled-gated, WARN-only, no new writers). Codex CLEAN. Live-verified.
- [x] **Item 7 — Expense Bookkeeper #21 prune/expire core-loop tests** (branch `feat/expense-prune-expire-tests`) — stale duplicate; merged as PR #347 / `8d4423b`.
  - Survey (Explore agent): prune-and-expire-expenses.py core loop (expiration AWAITING→EXPIRED + receipt-JPEG retention pruning) was UNTESTED — existing test covers only --dry-run config-load (explicitly deferred the loop). Money/state + retention/audit, deterministic, no external deps → test-only, strictly dormant-safe.
  - [x] `tests/test_prune_and_expire_logic.py` — 8 cases (stale-expire+audit, fresh-not-expired, non-AWAITING-not-expired, old-receipt-pruned+metadata audit, fresh-receipt-kept, non-retention-kept, idempotency, disabled-noop). Mirrors dry-run test's importlib+path-override wrapper; Linux-only (fcntl).
  - [x] Verified on VPS: 8/8 pass via Hermes venv pytest.
  - [x] push → Codex review (test-specific prompt) → merge if clean
- [x] **Item 7 — Expense #21 prune/expire core-loop tests** ✅ MERGED PR #347 (origin/main 8d4423b). 12 cases. Codex CLEAN. 12/12 on VPS.
- [x] **Item 8 — Expense #21 dedup-primitive tests** ✅ MERGED PR #349 (origin/main 113f17b). _hamming + _dhash; valid-PNG fixture (PIL + fallback). Codex CLEAN. Verified both paths on VPS.
- [x] **Item 9 — Multi-Location #3 geocode_address error-path tests** (branch `feat/multi-location-geocode-error-tests`) — stale duplicate; merged on `origin/main` with the full `TestGeocodeAddressErrorPaths` class in `tests/test_agent_3_multi_location.py`.
  - geocode_address must return None (never raise) on every maps_client.py failure mode → caller degrades instead of crashing the store-locator reply. Error branches (rc≠0/empty/missing-key/malformed-JSON/missing-coords/non-numeric/subprocess-exception) were untested. Test-only, subprocess mocked → strictly dormant-safe.
  - [x] Added `TestGeocodeAddressErrorPaths` to tests/test_agent_3_multi_location.py — 11 cases (success, first-match, rc≠0, empty, missing-key, malformed-JSON, missing-coords, non-numeric, string-coercion, TimeoutExpired, OSError). Mirrors existing class's SourceFileLoader fixture + patch.object pattern.
  - [x] Verified on VPS: full file 43/43 (32 existing + 11 new), new class 11/11, no regressions.
  - [x] push → Codex review (test-specific prompt) → merge if clean
- [ ] Item 10+ — remaining dormant-safe thinning out: runbook fixes; any other shipped-script error-path test gaps. NOTE most remaining pipeline agents (P&L #22, Tier-2 stubs, commerce) need operator activation (real blocker to dormant-safe "finishing every agent") — worth surfacing to operator as the run continues.
- [x] **Item 10 - Shift `shift-agent-fsck.py` invariant coverage** (branch `codex/builder-backlog-20260531`) — merged as PR #387 / `618fab7` (verified via `gh pr view 387`).
  - 2026-05-31 drift-check: Items 7 and 9 are already present on `origin/main` via `e0ce7d9`/`fb2d869` and `e97dcf3`; the real remaining no-operator test-only gap is fsck coverage.
  - [x] Add send-safe tests for clean state, duplicate active codes, stuck reconciling proposals, malformed audit rows, orphan audit references, send-counter mismatches, seen-offset drift, and config-load failure. (`tests/test_shift_fsck.py`, 8 cases.)
  - [x] Run focused Shift fsck/reconcile tests and relevant existing P5 suites. (`test_shift_fsck.py` 8/8 + `test_shift_reconcile.py` 10/10 = 18 passed locally; in-test Windows `fcntl` stub keeps it cross-platform.)
  - [x] Claude Code read-only review of the final diff.
  - Review (Claude Code, read-only): test-only + doc-only diff; no source/script/schema change, no new writer, no deploy wiring, no customer-facing behavior → dormant-safe. The new cases cover clean state plus deployed checks for code_uniqueness, reconciling_stuck, orphan_log_entry/orphan_proposal, counter_mismatch, seen_offset_past_eof, decisions_log_malformed, and config-load `EXIT_SCHEMA_VIOLATION`. Send-safety boundary correct: `_alert` replaced with a recorder (no notify-owner subprocess), all `/opt`+`/root` paths redirected to tmp, `customer_now`/`customer_today_str` pinned. Script's check 4 (roster resolution) is intentionally non-enforced and correctly untested. Note (pre-existing, NOT in this diff — future cleanup): `shift-agent-fsck.py:141` has a dead `if False else ({}, "missing")` no-op + unused `counter` var.


## Flyer Hermes Semantic Brief Reliability - 2026-05-27

- [x] Write reliability plan with drift-check + Hermes-first analysis.
- [x] Plan review by 2 parallel agents.
- [x] Apply plan review fixes.
- [x] Write design.
- [x] Design review by 2 parallel agents.
- [x] Apply design review fixes.
- [x] Build with TDD.
- [x] Create PR.
- [x] PR review by 3 parallel agents.
- [x] Apply PR review fixes.
- [ ] Merge and deploy.

Living checklist. Items grouped by priority; each completed item gets `✅` and a date.
For history of *completed* multi-phase initiatives (platform extract, sender-id, agent #2/4/5, etc.), see git log + `tasks/all-phases-*.md`.

Last updated: 2026-05-21 (Flyer customer-readiness stabilization gate work added; prior note: Flyer brief builder low-typing intake)

---

## Active - Flyer Hermes autonomous repair loop (2026-05-27)

**Drift-check tag:** extends-Hermes

Plan: `tasks/flyer-hermes-autonomous-repair-loop-plan-2026-05-27.md`
Design: `tasks/flyer-hermes-autonomous-repair-loop-design-2026-05-27.md`

- [x] Write plan and fold two independent reviews.
- [x] Write design and fold two independent reviews.
- [x] Build first bounded Hermes-planned regeneration slice with tests.
- [ ] Open PR and run three-vector review.
- [ ] Merge and deploy with VPS smoke gate.

---

## Active - Flyer Studio reliability outcomes (2026-05-27)

**Drift-check tag:** extends-Hermes

Plan: `tasks/flyer-studio-reliability-outcomes-plan-2026-05-27.md`

- [x] Write plan grounded in F0105 production incident and current `origin/main`.
- [x] Run two parallel plan reviews and fold findings.
- [x] Write design and run two parallel design reviews.
- [x] Build scoped fixes with tests.
- [ ] Open PR and run two parallel PR reviews.

---

## Active - Flyer Hermes intent layer (2026-05-22)

**Drift-check tag:** extends-Hermes

Hermes-first summary: Hermes owns WhatsApp ingress, identity, bridge delivery, media cache, future LLM/gateway classification, memory/session learning, and background task substrate. Flyer owns the strict intent schema, validator, legal route/action contract, source-edit safety boundary, customer-copy policy, and operator-visible incidents. This slice is shadow/observability only; it does not enable Hermes to route live traffic.

Plan: `docs/superpowers/plans/2026-05-22-flyer-hermes-intent-layer.md`
Design: `docs/superpowers/specs/2026-05-22-flyer-hermes-intent-layer-design.md`

- [x] Investigate stopped session: branch was at `origin/main`; no usable plan/design/build existed beyond a Hermes-check receipt.
- [x] Write plan and fold 2 reviewer passes (Hermes-first/scope + runtime/silent-failure).
- [x] Write design and fold 2 reviewer passes (code-path coverage + fixture/statistical validity).
- [x] Add strict Flyer intent contract/validator and canonical Hermes prompt scaffold.
- [x] Add inert mode handling: `off` and `shadow` only; `active`/`low_risk_active` become `unsupported_active_mode`.
- [x] Add typed `flyer_hermes_intent_decision` audit row with hashed ids and terminal route evidence.
- [x] Add cf-router shadow context wrapper with terminal route-event accumulation and ContextVar reset.
- [x] Add self-eval incidents and operator-brief grouping for Hermes intent rejection, disagreement, coverage, and unsupported active mode.
- [x] Add static cf-router audit-reason parity test so Flyer audit reason schema drift is loud.
- [x] PR in progress 2026-05-22 - live Hermes classifier shadow adapter through an injected existing gateway callable, post-route only, no new router or LLM/provider client.
- [x] PR in progress 2026-05-22 - offline Flyer intent training export for Hermes self-evolution input; renamed from "memory ingestion" because this PR writes a redacted operator-reviewed artifact, not live Hermes memory.
- [ ] Follow-up - actual Hermes memory ingestion receipt after operator review of training export artifacts; must remain state/memory only and never mutate code/SKILL/prompt/model/config at runtime.
- [ ] Follow-up - shadow soak evidence gate: require per-route-family sample counts, classifier success rate, validator-ok rate, and disagreement review before any active-routing proposal.
- [ ] Follow-up - active low-risk status-check route only after per-family shadow sample thresholds, validator-ok rate, and replay coverage pass.
- [ ] Follow-up - dashboard/operator lane for Hermes intent incidents if self-eval/operator brief noise is manageable.
- [ ] Follow-up - multilingual route examples for Telugu/Hindi/Malayalam/Tamil/Kannada customer phrasing.
- [ ] Superseded by this track - avoid new standalone regex-only patches for broad new-vs-revision/status/account judgment unless they are urgent customer-copy or safety hotfixes. File them as intent-layer replay/training examples instead.

---

## Active - Flyer Hermes operating layer backlog (2026-05-21)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes memory/session search, background jobs, gateway/provider routing, task orchestration, and operator brief/self-eval substrate. Flyer owns product policy: brand memory readiness, campaign/flyer lifecycle, customer copy, source-edit proof, deterministic final asset truthfulness, approval gates, and backlog evidence. This slice is read-only reporting plus backlog filing; no customer behavior, provider routing, social posting, video generation, or production state changes.

Plan: `docs/superpowers/plans/2026-05-21-flyer-hermes-operating-layer.md`
Design: `docs/superpowers/specs/2026-05-21-flyer-hermes-operating-layer-design.md`

- [x] Drift-check existing self-eval, operator brief, rollout readiness, customer-copy policy, and model/provider posture docs before adding anything.
- [x] Hermes-first review: mark Hermes-owned substrate (memory, background, X/Grok/Codex/video/Kanban orchestration) separately from Flyer product policy.
- [x] Add read-only Flyer operating-layer readiness helper and fixture schema.
- [x] Wire optional `--operating-layer-input` into `tools/flyer-self-evaluation.py`.
- [x] Surface operating-layer status and next action in `tools/operator-brief.py`.
- [x] File every Hermes-update option as either an implemented readiness-signal key or a deferred backlog key with owner and guardrail.
- [x] Implemented readiness signal - `persistent_brand_memory_readiness_signal`: report-only proof that at least one trial/active customer has profile identity, active brand assets, and a QA-passed delivered campaign. Activation remains deferred.
- [ ] Deferred - `source_edit_smoke_proof`: run a spend-gated 5-10 case source-preserving edit smoke before enabling automated source-edit reliance.
- [ ] Deferred - `persistent_brand_memory_activation`: turn the readiness signal into customer-visible brand memory only after enough QA-passed delivered assets exist.
- [ ] Deferred - `session_search_campaign_history`: use Hermes memory/session search to retrieve prior campaign styles, edits, and outcomes.
- [ ] Deferred - `background_render_qa_exports`: use Hermes background tasks for render, QA, resize/export, translation, and caption work after state-machine gates are stable.
- [ ] Deferred - `xai_grok_provider_posture`: evaluate Grok/xAI OAuth as an orchestration provider without changing production routing.
- [ ] Deferred - `x_search_fetching`: evaluate Hermes X fetching for campaign research, trend discovery, and customer-approved social monitoring.
- [ ] Deferred - `x_social_posting_approval`: add approval gates before any X/social publishing or reply automation.
- [ ] Deferred - `codex_offline_self_improvement`: use Codex/Hermes only for offline PR-gated template/test improvements; no runtime code/prompt/model self-modification.
- [ ] Deferred - `native_video_conversion`: design flyer-to-reel/video exports as an approved media lane with paid-generation gates.
- [ ] Deferred - `auto_kanban_operator_work`: use Hermes Kanban/task decomposition for operator backlog management, not autonomous production mutation.
- [ ] Deferred - `multi_format_export_truthfulness`: prove WhatsApp image, Instagram post/story, and printable PDF artifact shapes before advertising them as supported outputs.
- [ ] Deferred - `autonomous_campaigns_with_approval`: define "run Friday specials campaign" as a multi-step plan with explicit customer/operator approval gates.
- [ ] Deferred - `campaign_analytics_memory`: store campaign performance/outcome memory before ranking designs or sources.
- [ ] Deferred - `publishing_engine_approval_gates`: design social/email publishing connectors with identity, approval, and audit gates.
- [ ] Deferred - `hybrid_layout_final_renderer`: move toward structured layout plus deterministic text renderer for final assets where feasible.
- [ ] Deferred - `marketing_os_long_term`: package Flyer Studio as an autonomous local business marketing OS only after memory, exports, approvals, publishing, and analytics are proven.

---

## Active - Flyer customer-readiness stabilization gate (2026-05-21)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes JSON state, `decisions.log` audit chain, operator-brief substrate, the existing `tools/flyer-self-evaluation.py` + `customer_copy_policy.py` scanner, and the cf-router `pre_gateway_dispatch` replay harness. Net-new scope is product policy: rollout-decision verdict rules, host-supplied posture input fixture, source-edit yellow rule, 7 net-new replay fixtures (sample-idea / onboarding-sample / text-brief / guided-brief / visible-text-removal / LID-only / duplicate-phone), and a shared replay-helpers module.

Plan: `tasks/flyer-customer-readiness-gate-plan.md`
Design: `tasks/flyer-customer-readiness-gate-design.md`

- [x] Write and review the implementation plan (2 parallel reviewers; APPROVE WITH CHANGES; findings folded).
- [x] Write and review the design (2 parallel reviewers; APPROVE WITH CHANGES; findings folded — echo-leak loop-order bug fixed; Pydantic `Optional[X]` convention restored; SEVERITY_RANK single-sourced; fixture format aligned to top-level array; `--rollout-input` rename).
- [x] C1: extract `_install_common_replay_mocks` + supporting helpers into `tests/_flyer_replay_helpers.py`; add `onboarding`, `intake_consumed`, `account_intercept` route branches to `assert_expected_route`. Behaviour-preserving against existing `tests/test_flyer_incident_replay.py`.
- [x] C2-C3: add `src/agents/flyer/rollout_readiness.py` with input-fixture schema, single-sourced `SEVERITY_RANK` + `incident_color`, 5-state `compute_source_edit_posture`, `compute_rollout_verdict`, `build_rollout_section`, and Markdown render helpers.
- [x] C4: wire `--rollout-readiness`, `--rollout-input`, `--rollout-replay-summary-json`, `--manual-stale-red-minutes` into `tools/flyer-self-evaluation.py` (banner above incidents, full Rollout Readiness section at bottom). `tools/operator-brief.py` surfaces a `Rollout: …` summary line when present. Replaced local severity-rank literal with the shared import.
- [x] C5-C6: ship 7 net-new rollout-replay fixtures + cross-refs to 4 existing incident-replay fixtures, all asserted via `pre_gateway_dispatch`; rollout-replay test installs per-fixture intercept overrides for brief-builder, LID-only, and account-recognition paths.
- [ ] Sequencing for PR #154 (open, `fix/flyer-schedule-through-smoke`): orthogonal — touches `src/agents/flyer/render.py` + `tests/test_flyer_renderer.py`; no overlap with self-eval, operator-brief, replay harness, or rollout_readiness. Ship #154 first; rebase this PR if needed.
- [ ] Deferred — spend-gated 5-10 case source-edit visual-quality smoke. Blocks green rollout posture on source-edit path. Owner: next operator authorization. (Also tracked under "Flyer model policy lock-in".)
- [ ] Deferred — multi-turn co-owner reference-scope-memory rollout-replay variant. Current source-edit fixture (`F0063-source-choice-queues-manual-edit`) is single-turn `SOURCE`; the 2026-05-19 lesson on remembered relationships is not exercised.
- [ ] Deferred — optional live on-VPS posture probe that replaces the host-supplied input fixture (pilot-readiness-check integration).
- [ ] Deferred — conservative-bias gate for `configured_with_smoke`: cross-check `source_edit_smoke_evidence_age_days` and `provider_routing_changed_at`. A hand-edited fixture claiming `configured_with_smoke` without supplying or with a stale age currently passes. Demote to YELLOW with `"claimed configured_with_smoke but evidence age unverifiable"` when either field is missing or evidence is older than the latest provider-routing change. (PR-reviewer Rollout I1 + Hermes-first H1; non-blocking.)
- [ ] Deferred — real-intercept rollout-replay layer for PR #158 brief-builder scenarios. Current rollout fixtures verify the cf-router contract (intercept dict ⇒ cf-router returns dict, no fall-through) but do not call the real `_try_flyer_intake_intercept` logic; PR #158's own unit tests still gate lifecycle correctness. (PR-reviewer Rollout H1.)
- [ ] Deferred — rollout-replay fixture for the 2026-05-21 "routing previews must mirror live exception gates" lesson. The gate currently does not exercise a divergence between `should_start_new_flyer_over_active` and a stricter local-regex variant. (PR-reviewer Rollout I3.)
- [ ] Run focused verification (pytest + py_compile + git diff --check + CLI smoke for green / yellow / red).
- [ ] Request final PR review (rollout-behavior + Hermes-first/runtime). Fold findings. Open PR. No deploy.

---

## Active - Flyer brief builder / low-typing customer intake (2026-05-21)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes WhatsApp ingress, sender identity, bridge delivery, JSON state, and cf-router audit substrate. Reuse Flyer’s existing intake store, project parser, profile hydration, and locked-fact/QA pipeline. Net-new scope is deterministic Flyer policy for compact sample ideas, guided/text brief preview, and explicit customer approval before generation.

- [x] Write and review the implementation plan: `docs/superpowers/plans/2026-05-21-flyer-brief-builder.md`.
- [x] Write and review the design: `docs/superpowers/specs/2026-05-21-flyer-brief-builder-design.md`.
- [x] Add persisted intake states for sample idea, awaiting typed brief, and pending brief approval.
- [x] Add compact category idea choices for low-typing customers while preserving the existing full starter prompts.
- [x] Limit the sample picker to two choices and align the examples with the provided marketing-material / food-specials references.
- [x] Route active/trial vague `Create flyer` requests into sample choices instead of a standalone starter prompt.
- [x] Require `APPROVE`/approved aliases before sample, guided, or typed briefs create a project.
- [x] Preserve `Pick an idea` through free-trial onboarding completion.
- [x] Resolve LID-only active/trial customers by chat id for sample ideas.
- [x] Keep old in-flight mode prompt numbering safe while new prompts use `1=idea`, `2=guided`, `3=text`.
- [x] Preserve approved brief state if project creation fails; clear it only after successful project handoff.
- [x] Preserve one-time order/payment gating and existing source-edit/provider behavior.
- [x] Request final PR review and fold findings. Open PR; no deploy.

---

## Active - Flyer visible-text revision acceptance (2026-05-21)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse cf-router active-project routing, Flyer revision state updates, bridge/customer-copy substrate, and existing render/regeneration flow. Net-new scope is only Flyer-specific parsing for exact visible-text remove/delete revisions after preview.

- [x] Reproduce the live evening-snacks failure where `Time: 16:00 is duplicated. I'd like you to remove this.` asked for clarification.
- [x] Extend revision parsing to accept exact visible time text before a duplicate marker when remove/delete/exclude intent is present.
- [x] Add state-file coverage so `update-flyer-project` returns `revision_requires_clarification=false`.
- [x] Add cf-router coverage so the customer receives the regeneration acknowledgement, not a clarification prompt.
- [x] Open PR #157; no merge and no deploy.

---

## Active - Flyer replay stability audit (2026-05-21)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes WhatsApp/cf-router ingress, JSON state, audit logs, media cache, LLM/vision gateway boundaries, existing Flyer golden scenarios, self-evaluation, operator brief, and PR #149 SLA watchdog. Net-new scope is deterministic offline replay fixtures, shared Flyer customer-copy policy/lint constants, and read-only reporting guardrails.

Plan: `tasks/flyer-replay-stability-audit-plan-2026-05-21.md`

- [x] Write and review the replay stability audit plan.
- [x] Write and review the replay harness design. Draft: `tasks/flyer-replay-stability-audit-design-2026-05-21.md`.
- [x] Add replay fixtures and failing tests first.
- [x] Consolidate customer-copy policy/lint helper without changing customer behavior.
- [x] Extend self-eval/operator brief reporting where fixtures expose gaps.
- [x] Run focused verification and open PR. No deploy. PR #155 opened.

Review:
- Plan/design review findings folded: reused existing dispatcher replay substrate, added only a Flyer hook replay adapter, kept Hermes substrate boundaries, and preserved #149 SLA watchdog ownership.
- Final reviewer findings folded: all replay fixtures now traverse `pre_gateway_dispatch`, route assertions reject silent no-ops, temp-state project creation uses temp asset dirs, live `send_flyer_*` helpers are captured via fake `safe_io`, and static self-eval source-copy scans use the shared policy scanner.
- Final re-review fixes folded: duplicate initial acknowledgement grouping no longer uses outbound `message_id` as identity, and static customer-copy scans now catch dynamic project-id f-strings like `Project {project_id}`.
- Verified: focused Flyer pytest `262 passed, 7 warnings`; touched Python `py_compile`; self-eval JSON/Markdown CLI smoke with temp fixtures; `git diff --check`.
- No deploy performed. #149 is merged and deployed.

---

## Active - Flyer fresh-intent routing + customer deactivation (2026-05-21)

**Drift-check tag:** extends-Hermes

Do not create a new identity/auth/audit substrate. Reuse `identify-sender`, existing active-project helpers, existing fresh-OTP Cockpit mutation pattern, and existing audit chokepoints. If Hermes/cf-router already has a generic fresh-request-vs-followup classifier, reuse or extend it instead of inventing a parallel parser.

| Step | Hermes-owned? | Decision |
|---|---|---|
| WhatsApp sender identity and chat routing | yes | reuse existing cf-router/Hermes identity helpers |
| Active project lookup | no, Flyer-specific | use existing Flyer state helpers |
| New flyer vs revision intent policy | no, Flyer-specific | implement in Flyer routing helper |
| Customer deactivation auth | yes-ish | reuse existing Cockpit auth/fresh-OTP guard |
| Customer lifecycle state | no, Flyer-specific | add minimal Flyer customer status if missing |
| Audit emission | yes-ish | use existing safe_io / cockpit audit / LogEntry pattern |
| Messaging/customer copy | yes substrate, copy policy Flyer-specific | no copy changes in this PR |

- [x] Add RED routing regression for the F0061/F0062 evening-snacks phrase with old active projects.
- [x] Preserve legitimate revision, approval, and status handling tests.
- [x] Implement minimal fresh-flyer intent strengthening plus active-project bypass audit detail.
- [x] Add RED backend tests for fresh-OTP customer deactivation, required reason, historical project preservation, list/status behavior, audit, 404, and repeat handling.
- [x] Implement soft deactivation using existing Flyer customer status semantics and Cockpit audit.
- [x] Add Cockpit customer safe action with reason-confirm flow and inactive status display.
- [x] Run focused pytest, py_compile, frontend type/build, and `git diff --check`.
- [x] Open PR only; no merge and no deploy.

Review:
- Routing regression covers the exact F0061/F0062 evening-snacks phrase against old `awaiting_final_approval` and `revising_design` projects; it bypasses active-project revision and emits `flyer_active_project_bypassed`.
- Customer removal is soft deactivation (`cancelled`) guarded by fresh OTP, with reason/audit, and historical projects/audit/media untouched.
- Verified: cf-router/state-reply pytest, Flyer admin pytest, touched Python py_compile, frontend typecheck/build, and `git diff --check`.

## Active - Flyer model policy lock-in (2026-05-20)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes/OpenRouter gateway credentials and existing Flyer render scripts. Net-new scope is Flyer-side provider-policy config for draft/final rendering, a policy runbook, and admin-dashboard backlog documentation. Source-edit provider migration is explicitly deferred until a regression dataset exists.

- [x] Verify current code defaults and live OpenRouter slug posture.
- [x] Lock PR-1 scope: draft/final provider policy only; keep source-edit path unchanged.
- [x] Add schema defaults for `draft_provider_policy` and `final_provider_policy`.
- [x] Wire concept generation and finalization through provider policy resolvers.
- [x] Add policy runbook and admin-dashboard backlog item.
- [x] Add focused schema/static/cockpit health tests.
- [x] Run final focused verification before PR handoff.
- [x] PR-1 landed as PR #144 and deployed to `main-vps`: `draft_provider_policy` and `final_provider_policy` are wired; policy docs and admin-dashboard backlog exist; source-edit path remains unchanged.
- [ ] PR-2 source-edit provider wiring: PR #147 wires the configured provider path offline/no-deploy; production reliance remains blocked until a spend-gated 5-10 case source-preservation smoke proves layout fidelity.
- [ ] Source-edit regression dataset: build real visual-QA/source-contract cases before treating OpenRouter source edits as customer-grade or adding any automatic challenger/fallback routing.
- [ ] PR-3 after bakeoff: optionally add a separate Ideogram provider key and admin-dashboard model controls if the 20-case bakeoff justifies the added provider/subscription.

## Active - Hermes fleet upgrade train (2026-05-20)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes source, deployed Shift Agent patch gates, tarball deploy, env/config gates, and current VPS service layout. Net-new scope is fleet-level read-only reporting and weekly promotion planning for Srilu, Main, and VPIN.

- [x] Write plan: `tasks/hermes-fleet-upgrade-train-plan-2026-05-20.md`.
- [x] Add read-only fleet check + promotion-plan CLI: `tools/hermes-fleet-upgrade.py`.
- [x] Enhance fleet check with high-risk Hermes upstream path diffing and update-risk classification.
- [x] Add report-only skill/plugin sync posture report.
- [x] Add Srilu/VPIN normalization checklist: `tasks/hermes-fleet-normalization-2026-05-20.md`.
- [x] Add regression coverage for host order, health classification, secret-safe JSON, promotion gates, LF-only remote SSH probe payload, installed patch-gate baseline guard, high-risk path classification, skill-sync report, and normalization report.
- [x] Create app automations:
  - Daily Hermes fleet check: active, Srilu/Main/VPIN, 08:00 local daily.
  - Weekly Hermes promotion plan: active, Monday 09:00 local, report-only.
- [x] Review first live fleet-check output: Main is yellow (upstream high-risk changes + persistent standalone patch-gate availability); Srilu is red (bridge/env/deploy-marker/patch-gate/cockpit posture); VPIN is red (env/deploy-marker/patch-gate/cockpit posture).
- [ ] Normalize Srilu/VPIN runtime posture before adding any execute-mode upgrade command.

## Active - Flyer Studio autonomous improvement train (2026-05-20)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes scheduling, repo-backed task docs, existing Flyer golden/source-contract tests, reviewer-first PR flow, and the operator brief. Net-new scope is deterministic offline policy/report tooling; no deploy, VPS mutation, customer mutation, GitHub mutation, or live auto-merge runner is enabled.

- [ ] v0.1 policy/spec + offline report/eligibility tooling: `docs/superpowers/specs/2026-05-20-autonomous-ops-control-layer-design.md`, `tools/flyer-autonomous-train.py`, and operator brief integration.
- [ ] Daily/8-hour runner only after report output is stable and reviewed.
- [ ] Auto-merge runner only after two-reviewer policy gates are proven against trusted, commit-bound metadata.
- [ ] No autonomous deploy.

## Active - Flyer Studio self-evaluation loop (2026-05-20)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes/Shift WhatsApp ingress, Flyer `projects.json`, `decisions.log`, existing golden/source-contract/visual-QA tests, and the operator brief. Net-new scope is a deterministic read-only self-evaluation report that turns runtime evidence into incidents and suggested eval/backlog candidates. No production mutation, no customer messaging, and no prompt/SKILL/model/code self-modification is enabled.

- [x] v0.1 report-only wiring: PR #148 adds read-only incidents for stale manual source edits, customer-copy internal leaks, missing source contracts, missing/current source-aware QA, stale QA by version/timestamp, repeated check-ins, stuck generation states, and redacted JSON/Markdown output.
- [x] Operator brief integration: PR #148 optional `--flyer-evaluation-json` section surfaces self-evaluation status, grouped stale queue/source-contract/QA/customer-waiting buckets, active-vs-historical risk counts, top incidents, eval candidates, and Needs Srini items.
- [ ] Future slice: optional fixture-generation proposal mode after report output stabilizes; must remain PR/review gated and never mutate production behavior directly.
- [x] 2026-05-21 manual source-edit SLA alert: add a 5-minute read-only watchdog that pages the operator when exact source-edit manual queue rows exceed the 10-minute threshold, with alert throttling and no project/customer/provider mutation. Plan: `docs/superpowers/plans/2026-05-21-flyer-source-edit-sla-alert.md`.
  - Review: two-agent plan review fixed quiet-hours false-alert risk, queue-row throttle identity, explicit eligible-row semantics, typed audit rows, Flyer systemd deploy wiring, and watchdog failure notification. Focused verification passed: `python -m pytest tests/test_flyer_source_edit_sla_watchdog.py tests/test_flyer_scripts_static.py tests/test_flyer_self_evaluation.py -q` -> 64 passed; `python -m py_compile src/agents/flyer/scripts/flyer-source-edit-sla-watchdog src/platform/schemas.py` passed; CLI advisory empty-state run exited 0; `git diff --check` passed.
- [ ] Next anti-silent-failure slice after v0.1 report hardening: validate PR #147 or merged successor with source-edit provider routing smoke; improve Hermes vision/OCR source-contract extraction; audit legacy projects where source contracts did not project into locked facts; enforce source-aware QA before preview/delivery across every source-edit path; keep Hermes cron/operator brief alerts stable after the SLA watchdog lands.

## Active - Operator ops brief via Hermes memory (2026-05-20)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse repo-backed task docs, existing app automations, Hermes chat/cron as the reminder surface, and `tools/hermes-fleet-upgrade.py` report output. Net-new scope is a deterministic local Markdown brief generator plus a small operator-decisions file; v1 does not post to WhatsApp/Telegram or create a competing task database.

- [x] Add `tasks/operator-decisions.md` as the human-maintained decision/blocker/handoff queue.
- [x] Add `tools/operator-brief.py` to render a daily Markdown brief from repo docs, optional fleet JSON, automations, and git state.
- [x] Add focused parser/rendering tests for the operator brief.
- [x] Add a runbook for invoking the brief from Hermes/cron without mutating production state.
  - Review: `python -m pytest tests\test_operator_brief.py tests\test_hermes_fleet_upgrade.py -q` -> 28 passed; `python -m py_compile tools\operator-brief.py tools\hermes-fleet-upgrade.py` passed; `python tools\operator-brief.py --repo-root . --no-git` rendered the repo-backed brief; `git diff --check` passed.

## Active - Hermes SMB operating layer strategy (2026-05-14)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes gateway, skills, memory, cron, delegation, profiles, Kanban, installed productivity skills, `mcp/native-mcp`, and the deployed Shift/Catering/Daily Brief pilot. Net-new scope is product strategy, role structure, backlog ordering, and narrow vertical business loops that Hermes does not already provide.

Roadmap: `tasks/hermes-smb-operating-layer-roadmap-2026-05-14.md`

- [x] Market research synthesis: Hermes Agent substrate, Paperclip control-plane pattern, SMB AI adoption signals, MCP/action-tool risk, and existing SMB-Agents roadmap.
- [x] Capture strategic thesis: external product is an AI operations desk for ethnic SMBs; internal execution may use AI-company roles.
- [x] Write phased roadmap with Hermes-first analysis and source links.
- [x] Add active backlog section to `tasks/todo.md`.
- [ ] Phase 1 pilot proof: complete live WhatsApp smoke from `docs/runbooks/production-pilot-shift-catering-daily-brief.md` and capture evidence.
- [x] Phase 1 readiness hardening: tighten `pilot-readiness-check` so config customer location and roster location must agree, reject test/placeholder roster labels, and block stale real labels when the location-id token is absent from the roster location name.
- [ ] Phase 2 candidate selection: choose the first low-risk customer-facing expansion loop after pilot smoke. Current recommendation: Special Request Memory.
- [x] Phase 3 internal operating model: draft role cards for SMB Ops CEO, Hermes Engineer, Hermes Tester, Integration Scout, Customer Success Agent, Market/Content Agent, and Safety/Governance Agent.
- [ ] Phase 3 tooling choice: decide whether to start with lightweight repo/Codex role prompts, Hermes profiles plus Kanban, Paperclip, or a hybrid.
- [x] Phase 4 connector queue: define focused connector-review queue for QBO, POS, payments, reviews, and e-sign before estimating custom API work.
- [x] Phase 5 eval loop: define initial golden scenario/eval backlog for catering, shift, Daily Brief, menu update, and future Special Request Memory.
- [x] Phase 6 GTM spine: draft category, wedge, promise, proof path, differentiator, and first demo story.
- [ ] Phase 6 GTM proof: create owner-facing positioning and demo script only after pilot smoke evidence is captured.

Current recommendation: finish the pilot proof first, then build Special Request Memory as the first Phase 2 loop. It is Hermes-native, low-risk, high-delight, and reinforces the "operations desk that remembers your business" story.

## Active - Hermes Flyer Studio Agent (2026-05-15)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes WhatsApp ingress, `dispatch_shift_agent`, sender validation, image cache, skill dispatch, JSON state, NDJSON audit chain, and the bridge `/send-media` endpoint. Checked live installed Hermes skills plus official Hermes skill/image docs and awesome-hermes-agent; no purpose-built flyer workflow exists. Net-new scope is the flyer state machine, brand-kit memory, revision/version history, deterministic final asset packaging, media delivery helper, and flyer-specific QA.

- [x] 2026-05-19 Flyer Studio post-P0 pilot-hardening follow-ups: add spend-gated real-model golden smoke, surface `source_edit_integrity_only` in Cockpit manual queue, unify source-edit status copy through the canonical reason table, and add messy F-series-style raw requests to the deterministic golden suite.
  - Review: focused state/manual/golden suite `73 passed, 1 skipped`; cockpit backend manual-queue suite `24 passed`; frontend `npm run typecheck` and `npm run build` passed after `npm ci`; targeted red/green tests pinned copy consistency and the new integrity-only queue metadata.
  - Operator burn-down: PR #127 added `closed_no_send` so stale/superseded manual-review rows can be closed without implying customer delivery or QA break-glass. Deployed `deploy-20260519-211053-a8244611`; backup `/opt/shift-agent/state/flyer/projects.json.backup-pre-manual-burndown-20260519T211134Z`; closed F0036/F0043/F0045/F0052/F0053/F0056 plus duplicate F0058; final manual-queue triage `total=0`.
- [x] 2026-05-20 pilot-hardening golden live-shape sampling and backlog reconciliation.
  - Drift/Hermes-first check: reused deployed Flyer `projects.json`, `customers.json`, `decisions.log`, the existing deterministic golden harness, cf-router classifier helpers, and task docs. No dashboard or runtime behavior changes.
  - Live samples redacted into `tests/fixtures/flyer_golden/live_customer_message_shapes.json`: source flyer + long changes + `co-owner`, `any update?`, title-case `Approve`, and short item-swap correction.
  - Backlog reconciliation keeps history but removes stale P0-looking ambiguity around CTA idempotency, F0012, adaptive language/mode, F0023/F0024/F0029/edit-flow, and the mobile design draft disposition. Production-quality real-model smoke remains open as Session 3-owned until that PR lands.
  - Review: red run caught missing `--manual-edit-required` fixture wiring; final focused suite `187 passed`; `py_compile` and `git diff --check` passed. Mobile app draft disposition recorded separately in `tasks/flyer-mobile-app-v1-follow-up-2026-05-20.md`.
- [ ] 2026-05-18 business-type starter briefs: store 10 editable sample flyer briefs keyed by customer business category, show the best brief after registration or before vague flyer creation, and let the user edit/submit it as the normal Flyer Studio request.
  - Drift/Hermes-first check: reuse Flyer `business_category`, onboarding, intake sessions, project creation, renderer, WhatsApp delivery, live VPS `flyer_generation`/`dispatch_shift_agent` skills, and `cf-router`. Checked Hermes Skills Hub and awesome-hermes-agent; no purpose-built business-type flyer prompt catalog found. Net-new scope is a small local starter-brief catalog and reply integration.
  - [x] Create branch `codex/flyer-starter-briefs`.
  - [x] Write implementation plan: `docs/superpowers/plans/2026-05-18-flyer-business-starter-briefs.md`.
  - [x] Get plan reviewed by two parallel agents and apply findings.
    - Review fixes: added cf-router active/trial vague-start path, parser-validity tests for starter text -> project creation, stricter per-step `[Hermes]` / `[net-new]` checklist, explicit onboarding data-flow change, and softer customer-facing copy rules.
  - [x] Write design doc: `docs/superpowers/specs/2026-05-18-flyer-business-starter-briefs-design.md`.
  - [x] Get design reviewed by two parallel agents and apply findings.
    - Review fixes: added explicit `trial`/`active` status guard, non-eligible status tests, compound `CONFIRM. Create ...` suppression, all-category starter text parser-validity coverage, customer-copy internal-term checks, and design-local Hermes domain table.
  - [x] Build with TDD.
  - [x] Run focused verification.
    - Review: `python -m pytest tests/test_flyer_starter_briefs.py tests/test_flyer_onboarding.py tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py -q` -> 148 passed. `python -m py_compile src\agents\flyer\starter_briefs.py src\agents\flyer\intake.py src\agents\flyer\onboarding.py src\agents\flyer\render.py src\plugins\cf-router\actions.py src\plugins\cf-router\hooks.py src\platform\schemas.py` -> passed. `git diff --check` -> passed.
  - [x] Create PR: https://github.com/Trivenidigital/shift-agent/pull/102
  - [x] Get PR reviewed by three parallel agents and apply findings.
    - Review fixes: kept starter briefs out of guided-mode collection, blocked vague starts for payment-pending/suspended/cancelled customers before project creation, made AI-powered heading selection phrase/token based, tightened service-business renderer copy away from food/festival defaults, and added instruction-leak text-manifest QA.
- [ ] 2026-05-18 starter prompt timing/preferences: show category starter prompts only at helpful ready/vague moments, suppress repeated examples after first automatic send, and let customers turn sample prompts off/on for the business account.
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: reuse Flyer customer JSON state, `starter_briefs.py`, onboarding/intake ready flows, cf-router routing, and account-command audit path. Net-new scope is rollback-safe store-level preference metadata and deterministic opt-out/opt-in routing.
  - [x] Create isolated worktree/branch `codex/flyer-starter-prompt-preferences`.
  - [x] Write implementation plan: `docs/superpowers/plans/2026-05-18-flyer-starter-prompt-preferences.md`.
  - [x] Get plan reviewed by two parallel agents and apply findings.
    - Review fixes: avoid nested schema rollback hazard with top-level store maps, support LID-only preference commands, fail closed on account-command errors, normalize sender-block-wrapped commands, keep CTA retries concise, guard active-project states before vague-start starter prompts, clarify payment-pending CTA behavior, and make the account-wide preference explicit in copy.
  - [x] Write design doc and run two parallel design reviews.
    - Review fixes: recognized preference commands fail closed on all lookup/CLI paths, LID-only handling uses a store-level sender lookup, starter prompt send uses an atomic claim/release contract, transient metadata uses namespaced keys, payment-pending CTA handling is explicit, compound confirm suppresses starters, Guided Mode consumes auto-eligibility, and opt-out copy/aliases are account-wide.
  - [x] Build with focused tests.
    - Review: `python -m pytest tests/test_flyer_starter_briefs.py tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py -q` -> 123 passed. `python -m py_compile src\agents\flyer\starter_briefs.py src\agents\flyer\onboarding.py src\agents\flyer\intake.py src\agents\flyer\account.py src\plugins\cf-router\actions.py src\plugins\cf-router\hooks.py src\platform\schemas.py` -> passed. `git diff --check` -> passed.
  - [x] Create PR and run three parallel PR reviews.
    - PR: https://github.com/Trivenidigital/shift-agent/pull/105
    - Review fixes: restored account phone normalization for audit/pending-change guards, added new cf-router reasons to the strict audit schema, reset starter sent-counts when customers opt back in, and included opt-out hints on every full starter prompt surface.
    - Follow-up review fixes: guided-mode no longer consumes starter prompt entitlement without showing a starter; cf-router starter prompt claim/release now goes through the locked `manage-flyer-account` path; onboarding/intake release starter claims on hard send failure; broad account commands for unknown users fall through while preference commands still fail closed.
- [x] 2026-05-17 launch funnel reliability pass: fix compound `CONFIRM + flyer request`, broaden new-project detection for menu/marketing requests, prevent generic LLM fallback during active intake, require explicit media intent before saving brand assets, deploy, and send a fresh campaign message for user testing.
- [ ] 2026-05-17 CTA idempotency and live-state repair: code/state repair is shipped; only manual campaign resend verification remains.
  - [x] Local bugfix: duplicate-phone `CONFIRM` from the same sender now resumes the existing Flyer account, clears the stale onboarding session, and keeps true cross-account duplicate blocking customer-safe.
  - [x] VPS targeted hotfix: deployed duplicate-onboarding resume plus free-trial project field hydration without tarballing unrelated dirty worktree changes; temp-state smokes passed for both paths.
  - [x] Current live state verified 2026-05-20: `CUST0001` is restored as `trial` with `public_phone/business_whatsapp/onboarded_by_phone=+17329837841`, `primary_chat_id=17329837841@s.whatsapp.net`, and `authorized_request_numbers=["+17329837841","+19045550104"]`.
  - [x] Current local coverage: CTA routing has focused tests for payment-pending, trial/active existing customer retries, sender-block/card-text CTA detection, LID-only senders, stale onboarding-session bypass, and active-customer ready prompts in `tests/test_cf_router_flyer_routing.py` and `tests/test_cf_router_plugin.py`.
  - [ ] Manual resend verification only: from the existing Flyer admin/campaign sender, resend the current Flyer Studio campaign to the test WhatsApp account `+17329837841`, then tap both quick replies. Expected: `Start Free Trial` returns the existing-account ready prompt without restarting onboarding or creating a project; `Act Now! Save Time and Money` returns the same ready/account prompt; decisions.log records `flyer_onboarding`/active-customer-ready style handling, not `flyer_intake_started` or duplicate customer creation.
- [x] Add Flyer Agent schemas: config, request fields, brand kit, assets, concepts, revisions, project store, exact workflow-state transitions, and audit entries.
- [x] Add WhatsApp media delivery helper for the existing bridge `/send-media` endpoint.
- [x] Add Flyer Agent SKILLs for dispatch, intake, generation/revision guidance, final approval, and delivery.
- [x] Add Flyer Agent scripts for project create/update, concept generation, finalization, quality checks, and package delivery scaffold.
- [x] Wire flyer intent and active-project messages into `dispatch_shift_agent` without stealing generic catering/event traffic.
- [x] Add deployment/smoke coverage for flyer skills, scripts, state directories, and media delivery prerequisites.
- [x] Add tests for schemas, state transitions, scripts, media helper, dispatcher routing, and SKILL static contracts.
- [x] Verify locally with focused pytest and syntax checks before deployment planning.
- [x] Production hardening: deterministic Pillow renderer creates real concept previews plus WhatsApp, Instagram post/story, and PDF final assets.
- [x] Runtime smoke on `main-vps`: project create -> field update -> concept PNGs -> selection -> approval -> final PNG/PDF assets.
- [x] Deploy to `main-vps` as `deploy-20260515-004633-f6dfaac0`; deploy smoke passed and Hermes gateway remained active.
- [x] Production font/runtime readiness: installed `python3-pil` and `fonts-noto-core` on `main-vps`; Telugu smoke rendered PNG/PDF assets with Noto font files present.
- [x] Enable live Flyer Agent config on `main-vps` with deterministic-renderer draft/final settings for near-zero-cost smoke.
- [x] Live end-to-end low-cost smoke: prompt -> project F0002 -> 3 concepts -> approval -> 4 final assets -> WhatsApp `/send-media` delivery; audit `flyer_assets_delivered` recorded 4 outbound message IDs.
- [x] Fix live WhatsApp misroute where cf-router F7 treated explicit flyer messages as Catering follow-ups when the sender had an active catering lead.
- [x] Redeploy cf-router flyer-priority guard and verify the exact Ugadi flyer prompt clears cf-router and routes to Flyer in dispatcher replay.
- [x] Add deterministic cf-router Flyer primary-mode so explicit flyer WhatsApp requests do not depend on the brittle generic LLM dispatcher.
- [ ] Production-quality image generation smoke: owned by Session 3 / pending PR.
  - Evidence already shipped: controlled direct-generation poster path landed 2026-05-17 and deployed as `deploy-20260517-181423-59407d33`; `tests/test_flyer_golden_scenarios_real_model.py` keeps the real-model path spend-gated.
  - Still pending before closure: Session 3's real-model/spend-gated smoke and provider-posture PR must land, then update this item with the exact PR/deploy/test evidence.
- [x] Credit optimization: switch Flyer Studio default from three generated concepts to one best generated design, with WhatsApp copy `Reply APPROVE or reply with changes.`
- [x] Marketing CTA correction: split `Start Free Trial` and `Act Now! Save Time and Money` into distinct WhatsApp prefill intents, add router coverage for `ACT NOW`, and make the trial welcome copy start with a ready-to-create flyer prompt.
- [x] 2026-05-18 Chloe Hair Studio creation-flow blocker: prevent non-food service flyers from inheriting Indian festive/food-menu defaults, block instruction-text leakage in text QA, reject language-only business profile replies, parse `Location:`/`Contact:` labels, repair live `CUST0004` category to `Hair salon`, and move unsafe `F0036` out of final approval to `manual_edit_required`.
  - Review: local focused suite `132 passed`; `python -m compileall -q src\agents\flyer src\platform` passed; `git diff --check` passed for changed files; VPS hotfix syntax check passed; VPS smoke parsed a fresh Chloe request as `Chloe Hair Studio` with Virginia location/contact and no blocked food/festival prompt terms.
  - Fresh candidate: generated `F0048` from the repaired path; visual QA passed for salon-appropriate imagery and required copy. Earlier QA candidates `F0043`/`F0045` were contained as `manual_edit_required`.
- [x] Free Trial flyer generation bug: live F0012 created after onboarding but stayed `intake_started` because project extraction required `contact_info` from the flyer text and did not hydrate the saved trial customer profile.
  - Drift/Hermes-first check: reused existing Flyer onboarding account state, project script, cf-router primary path, quota path, and renderer. No new Hermes substrate or custom workflow is needed; net-new scope is filling missing project fields from the already-collected Flyer customer profile.
  - [x] Isolate work in branch/worktree `codex/fix-flyer-free-trial-generation`.
  - [x] Verify live state: F0012 request has breakfast menu/prices but `fields.contact_info=null`; customer `CUST0002` is trial-active with `public_phone=+19802005022`.
  - [x] Add regression test for project creation hydrating missing contact/location from a trial customer profile.
  - [x] Implement profile hydration in `create-flyer-project`.
  - [x] Run focused verification and record result.
  - Review: `python -m pytest tests\test_flyer_create_project.py tests\test_flyer_schemas.py tests\test_flyer_onboarding.py tests\test_cf_router_flyer_routing.py tests\test_flyer_scripts_static.py -q` -> 56 passed.
  - Reconciliation 2026-05-20: fix is present on `origin/main` via `_hydrate_fields_from_customer()` in `src/agents/flyer/scripts/create-flyer-project` and regression `test_create_project_hydrates_missing_contact_from_trial_customer`. Historical live row `F0012` remains `intake_started`; treat that as optional operator cleanup if the tester still uses that thread, not as an open code gap.

### Infrastructure Hardening - Hermes Runtime Ownership (2026-05-16)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse existing `hermes-gateway.service`, tarball deploy, smoke test, and `/root/.hermes` runtime layout. Net-new scope is a targeted permissions preflight replacing the brittle recursive service-start `chown` that can block gateway startup on stale operator backup files.

- [x] Create branch `codex/hermes-ownership-hardening`.
- [x] TDD red tests for service ExecStartPre, permissions preflight script, and deploy/smoke wiring.
- [x] Replace broad `/bin/chown -R shift-agent:shift-agent /root/.hermes` startup guard with targeted `shift-agent-hermes-permissions`.
- [x] Add deploy pre-restart gate and smoke coverage for Hermes runtime permissions.
- [x] Run focused verification, merge, and deploy to `main-vps`.
  - PR #95 merged targeted runtime-permissions preflight as `147f887`; initial deploy correctly failed before gateway restart because `/root/.hermes/node/bin/node` is absent on `main-vps` even though the gateway is healthy, then rollback passed.
  - PR #96 made the optional bundled Node path non-blocking while preserving strict checks for `config.yaml`, `.env`, and Hermes Python; merged as `1b3db0e`.
  - Deployed `deploy-20260516-153945-1b3db0e3`; deploy smoke passed and now includes `Hermes runtime permissions verified`.
  - Direct restart proof passed: `ExecStartPre=+/usr/local/bin/shift-agent-hermes-permissions`, preflight returned OK, `hermes-gateway` became `active`, and WhatsApp bridge health returned `{"status":"connected","queueLength":0}`.

### Phase 1 - WhatsApp Customer Onboarding And Paid Readiness (2026-05-15)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes WhatsApp ingress, sender identity, cf-router, JSON state, bridge text/media delivery, deployed Flyer workflow scripts, and config-driven plan tiers. Net-new scope is narrow account lifecycle logic: confirmation, activation, quotas, account commands, and payment-provider handoff metadata.

Plan: `docs/superpowers/plans/2026-05-15-flyer-onboarding-phase1.md`

- [x] Create Phase 1 branch `codex/flyer-onboarding-phase1`.
- [x] Ground in deployed Flyer schemas, onboarding, cf-router, dispatcher, tests, safe_io, and deploy smoke patterns.
- [x] Write Phase 1 implementation plan with drift and Hermes-first analysis.
- [x] Get Phase 1 plan reviewed by two parallel agents.
- [x] Fix plan review findings: admin-only mutations, durable audit, idempotent activation, quota reserve/finalize/release, payment-pending command placement, connector-first Phase 2 posture.
- [x] Write Phase 1 design doc.
- [x] Get design reviewed by two parallel agents.
- [x] Fix design review findings: phone-based admin authorization, explicit activation target, integer payment cents, global payment-reference uniqueness, latest-state quota reservations, quota before active-project resume, and expanded deploy smoke.
- [x] Build schema, onboarding confirmation, activation, quota, and account-command implementation.
- [x] Run local focused tests and script syntax checks: `python -m py_compile ...` passed; `python -m pytest` focused Flyer/cf-router suite passed (63 tests).
- [x] Create PR for Phase 1: https://github.com/Trivenidigital/shift-agent/pull/89
- [x] Get PR reviewed by three parallel agents.
- [x] Fix PR review findings: fail-closed sender identity for Flyer paths, ambiguous phone collision checks, immutable payment-reference history, exact plan cents/currency validation, admin-only saved brand-kit replacement, Hermes-venv subprocess execution, bridge `/send-media` deploy/smoke gates, rollback cleanup for Flyer artifacts, and onboarding catch-all gating.
- [x] Merge Phase 1 PR #89 to `main` at `bddf0d0`.
- [x] Deploy Phase 1 to `main-vps` as `deploy-20260515-180102-bddf0d07`; first attempt auto-rolled back because the old installed deploy script lacked the new Flyer account install rule, rerun via the staging deploy script succeeded.
- [x] Run production smoke: onboarding confirmation -> payment activation -> idempotent activation replay -> status command -> non-admin mutation denial -> quota check -> one flyer project create. Result: `{"ok": true, "customer_id": "CUST0001", "activation": "active", "quota_blocked": true, "project_create": true}`.

### Phase 2 - Flyer Quality, Revision Fidelity, And Production Smoke (2026-05-15)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse the deployed Flyer WhatsApp flow, JSON state, media delivery, OpenRouter-backed renderer, and Hermes bridge. Net-new scope is narrow quality hardening: asset inspection, stronger reference/template prompt policy, structured revision patching, and an isolated smoke CLI.

Plan: `docs/superpowers/plans/2026-05-15-flyer-quality-phase2.md`

- [x] Create Phase 2 branch `codex/flyer-quality-phase2`.
- [x] Ground in deployed Flyer renderer, workflow, updater, deploy scripts, live config, and Hermes runtime capabilities.
- [x] Write Phase 2 implementation plan with drift and Hermes-first analysis.
- [x] Get Phase 2 plan reviewed by two parallel agents and apply findings: add deployed `flyer_workflow.py`, isolated non-root quality smoke, `--allow-spend` real-model guard, server-side critical-text overlay, and stricter revision-applied semantics.
- [x] Write Phase 2 design doc.
- [x] Get Phase 2 design reviewed by two parallel agents and apply findings: raw model backgrounds before overlays, no model-owned critical text, schema-compatible `resulting_version` revision marker, temp-root isolation, per-binary cleanup, no long OpenRouter calls under `FileLock`, and WhatsApp clarification for unresolved revisions.
- [x] Build render-quality inspection, reference/template prompt hardening, revision patch extraction, and quality smoke CLI.
- [x] Run focused local verification and script syntax checks: Flyer pytest suite `66 passed`, `py_compile` clean, `git diff --check` clean, and local deterministic `smoke-flyer-quality` returned `{"ok": true}`.
- [x] Create PR for Phase 2: https://github.com/Trivenidigital/shift-agent/pull/90
- [x] Get PR reviewed by three parallel agents and apply findings: sanitized model prompt, system-Pillow overlay/PDF fallback, no deadlocking unresolved revisions, small revision-result envelope for router parsing, safer title parsing, and per-project generation lock.
- [x] Merge Phase 2 PR #90 to `main` at `c64d299`.
- [x] Deploy Phase 2 to `main-vps` as `deploy-20260515-205337-5e2e1690`; deploy smoke passed, deterministic Flyer quality smoke passed, and one real-model smoke passed with `openai/gpt-5.4-image-2` high quality (`1080x1350`, 1.85 MB, variance 506) at `/opt/shift-agent/state/flyer/quality-smoke-20260515-205337/assets/F9001-C1-preview.png`.

### Phase 3 - Flyer Text QA, Revision Proof, And Send Gate (2026-05-15)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes WhatsApp ingress/delivery, existing Flyer renderer, server-side critical-text compositor, JSON project state, and deploy smoke. Net-new scope is a deterministic exact-fact manifest and final-package gate so revised customer facts cannot be silently omitted or replaced by stale model/template text.

Plan: `docs/superpowers/plans/2026-05-15-flyer-text-qa-phase3.md`

- [x] Create Phase 3 branch `codex/flyer-text-qa-phase3`.
- [x] Ground in deployed Flyer renderer, finalization script, smoke CLI, deploy installer, Phase 2 design, and active backlog.
- [x] Write Phase 3 implementation plan with drift and Hermes-first analysis.
- [x] Get Phase 3 plan reviewed by two parallel agents and apply findings: gate preview sends and final sends, cover deterministic renderer, store expected-vs-rendered facts, bind manifests to project version/selected concept/output, and persist PDF sidecars.
- [x] Write Phase 3 design doc.
- [x] Get Phase 3 design reviewed by two parallel agents and apply findings: exact fact-id/text equality, fail on omitted/truncated facts, move terminal delivered state to successful send, run final-package QA in deploy smoke, and tighten break-glass direct asset sends.
- [x] Build deterministic text QA manifest, final package gate, deploy install, and smoke output.
- [x] Run focused local verification and script syntax checks: `py_compile` clean, `git diff --check` clean, deterministic `smoke-flyer-quality --final-package` returned `{"ok": true}`, and focused Flyer/cf-router pytest suite passed (81 tests).
- [x] Create PR for Phase 3: https://github.com/Trivenidigital/shift-agent/pull/91
- [x] Get PR reviewed by three parallel agents and apply findings: prevent manifest certification of truncated deterministic text, suppress stale old phone details from revised notes, support daily/weekday/every schedules plus 10-item menus, fail malformed manifests closed, enforce final package status/kinds/output formats, add send CAS before `delivered`, and cover dry-run send state transition in smoke.
- [x] Merge Phase 3 PR #91 to `main` at `ea2720e`.
- [x] Deploy Phase 3 to `main-vps` as `deploy-20260515-233720-ea2720ed`; deploy smoke passed, pilot readiness remained READY (16 passed), deterministic `smoke-flyer-quality --final-package` passed with dry-run final send delivered, and guarded real-model smoke passed with `openai/gpt-5.4-image-2` high quality plus text QA for concept and all four final assets at `/opt/shift-agent/state/flyer/quality-smoke-phase3-real-20260515-233720`.

### Phase 3.1 - Flyer Package Retry Safety (2026-05-16)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse existing Flyer JSON state, `FlyerAsset`, bridge `/send-media`, NDJSON delivery audit, and Phase 3 text QA. Net-new scope is only per-final-asset delivery status so a retry after partial bridge failure sends the missing files instead of duplicating already-sent files.

- [x] Create branch `codex/flyer-delivery-retry-state`.
- [x] Root cause: `send-flyer-package` records outbound message IDs only after all four bridge sends succeed, so a failure after partial success leaves no durable per-asset proof and retries resend the already-delivered assets.
- [x] TDD red tests for partial failure persistence, retry-only-missing behavior, and `send_uncertain` no blind retry.
- [x] Add backwards-compatible delivery fields to `FlyerAsset`.
- [x] Update `send-flyer-package` to persist each successful asset immediately, skip delivered assets on retry, and block/alert on uncertain sends.
- [x] Extend smoke to assert all final assets are marked sent during dry-run delivery on Linux; local Windows smoke skips this path because `safe_io` requires `fcntl`.
- [x] Run focused verification, merge, and deploy to `main-vps`.
  - PR #92 merged to `main` as `831e37f`.
  - Deployed `deploy-20260516-141525-831e37fa`.
  - Production final-package smoke passed with `send_dry_run.ok=true`, `delivered=true`, and `all_final_assets_sent=true`.

### Phase 4 - Flyer Ops Launch Hardening (2026-05-16)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse existing Flyer JSON state, `FlyerAsset.delivery_status`, `send-flyer-package`, deploy smoke, and NDJSON audit entries. Net-new scope is a small operator report plus audit surfacing for blocked/uncertain package retries so paid-customer delivery failures are visible and actionable.

- [x] Create branch `codex/flyer-ops-launch-hardening`.
- [x] TDD red tests for delivery-status report output and deploy install/smoke coverage.
- [x] Add `flyer-delivery-report` CLI to summarize blocked/failed/pending deliveries without sending media.
- [x] Emit an audit row when a project retry is blocked by uncertain asset delivery.
- [x] Install the report in deploy, add smoke coverage, and update stale rollback cleanup.
- [x] Run focused verification, merge, and deploy to `main-vps`.
  - PR #93 merged to `main` as `0ed759a`; PR #94 fixed legacy delivered-project report compatibility and merged as `058218c`.
  - Deployed `deploy-20260516-150309-058218c1` after clearing a pre-existing `/root/.hermes` ownership restart issue with `chown -R shift-agent:shift-agent /root/.hermes`.
  - Production smoke passed: `smoke-flyer-quality --final-package` returned `ok=true`, `send_dry_run.delivered=true`, and `all_final_assets_sent=true`.
  - `flyer-delivery-report --json` on live state returned `ok=true`, `issues_total=0`, `blocked_projects=0`, `failed_assets=0`, `uncertain_assets=0`, and `pending_assets=0`.
  - Gateway health after deploy: `active`, bridge health `{"status":"connected","queueLength":0}`.

### Phase 5 - Flyer Studio Launch Funnel (2026-05-16)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse deployed Flyer account quotas, WhatsApp onboarding, cf-router, media delivery, and marketing/sample generation workflow. Net-new scope is a hard-limited free trial tier, click-to-WhatsApp trial entry copy, and launch collateral that avoids cold WhatsApp blasting.

Plan: `docs/superpowers/plans/2026-05-16-flyer-launch-funnel-phase5.md`

- [x] Create branch `codex/flyer-launch-funnel`.
- [x] Write Phase 5 implementation plan with drift and Hermes-first analysis.
- [x] Add default 3-sample `trial` tier and trial customer status.
- [x] Add `START FREE TRIAL` WhatsApp onboarding path that skips paid plan choice and activates trial immediately after confirmation.
- [x] Enforce hard trial quota through the existing reserve/finalize usage path, blocking the fourth new flyer request with an upgrade CTA.
- [x] Add marketing launch pack with WhatsApp-safe copy, sample-gallery guidance, free-trial link, plan pricing, upsell prompts, and opt-in posture.
- [x] Launch polish: replace marketing placeholders with the live `wa.me/918522041562` trial link and add a scannable trial QR PNG.
- [x] Launch polish: add a high-quality HTML sample gallery for restaurant, temple, salon, tutor, realtor, and food-truck examples.
- [x] Launch polish: automatically send trial upsell prompts after final sample delivery, using remaining trial quota to choose the message.

### Phase 5.1 - Flyer Studio Clickable CTA Campaign Send (2026-05-16)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse the existing WhatsApp bridge, `safe_io` loopback bridge validation, Flyer marketing image, and Flyer Studio trial link. Net-new scope is a small interactive CTA bridge endpoint plus a narrow campaign-send script so customer-facing outreach can show button labels instead of raw URLs.

- [x] Add RED tests for a `/send-cta` bridge helper, Flyer campaign script contract, and bridge patch/smoke coverage.
- [x] Add `safe_io.bridge_send_cta` with fail-closed URL validation and uncertain-send handling.
- [x] Add `send-flyer-campaign` CLI to send `Flyer.png` plus clickable CTA labels without exposing raw URLs in the visible message.
- [x] Extend Hermes bridge patching and deploy/smoke gates to require `/send-cta`.
- [x] Run focused verification and document deployment/manual live-test steps.
  - Local verification: `python -m pytest tests/test_flyer_scripts_static.py tests/test_safe_io_bridge_send_cta.py -q` -> 17 passed, 3 skipped on Windows; `python -m py_compile` for touched Python files passed; Git Bash `bash -n` for deploy/smoke/check scripts passed; `git diff --check` passed.
  - Patcher smoke against a temp sender-id-patched bridge added `BEGIN shift-agent-cta-buttons` and `app.post('/send-cta')`.
  - Live bridge patched on `main-vps`; new `bridge.js` sha256 `72953d3050313381eceb28e6813da4b161a0b2684f1948dac2ef301613749710`; gateway active and bridge health connected.
  - Deployed to `main-vps` as `deploy-20260516-203356-ff848bb0`; smoke passed, including `/send-media` + `/send-cta`, Flyer deterministic quality smoke, delivery report, pilot readiness, config gates, and cf-router compile/import sanity.
  - Sent Flyer Studio CTA campaign to `17329837841@s.whatsapp.net`; bridge returned `ok=true`, `status=sent`, `message_id=3EB0D4021D5F8341174A87`.
- [x] Correct CTA semantics after live review: new sends use distinct URLs for `Start Free Trial` and `Act Now! Save Time and Money`, `ACT NOW` routes as onboarding intent, and free-trial onboarding opens with a ready-to-create flyer prompt.
  - Local verification: focused CTA/onboarding tests first failed, then `python -m pytest tests/test_flyer_scripts_static.py tests/test_cf_router_flyer_routing.py tests/test_flyer_onboarding.py tests/test_safe_io_bridge_send_cta.py -q` -> 40 passed, 3 skipped.
  - Deployed to `main-vps` as `deploy-20260516-205856-604fb6c0`; smoke passed.
  - Live dry-run proof shows `Start Free Trial` prefills `START FREE TRIAL - Help me create a beautiful flyer for my business`, and `Act Now! Save Time and Money` prefills `ACT NOW - I want to set up Flyer Studio for my business`.
- [x] Replace URL CTAs with WhatsApp quick-reply buttons after live click test showed URL dialogs instead of chat intent.
  - Root cause: `/send-cta` used `cta_url`; WhatsApp opened link/dialog UI instead of producing inbound agent text.
  - Fix: `/send-cta` now emits `quick_reply` buttons with reply payloads, and bridge inbound parsing maps button responses into normal text for cf-router.
  - Local verification: `python -m pytest tests/test_flyer_scripts_static.py tests/test_cf_router_flyer_routing.py tests/test_flyer_onboarding.py tests/test_safe_io_bridge_send_cta.py -q` -> 41 passed, 3 skipped; Python compile, shell syntax, and diff check passed except existing baseline CRLF warning.
  - Deployed to `main-vps` as `deploy-20260516-211553-604fb6c0`; smoke passed. Live bridge sha256 `de178b6fa6227f923f479ff2d34a3419b4a2e5f83bc5e5408137712cd25ed7ec`.
  - Sent corrected campaign to `17329837841@s.whatsapp.net`; media id `3EB0349803A518A3D34C48`, CTA id `3EB0FB345147FA164645BE`.

## Active - Production pilot: Shift + Catering + Daily Brief (2026-05-14)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse the deployed WhatsApp gateway, `dispatch_shift_agent`, `cf-router`, shift coverage scripts, catering menu/proposal scripts, and `send-daily-brief` timer. Net-new scope is limited to a deterministic pilot readiness gate, menu-update authority wording consistency, and a WhatsApp smoke runbook.

Plan: `docs/superpowers/plans/2026-05-14-production-pilot-shift-catering-daily-brief.md`

- [x] Confirm first production pilot bundle: Shift Agent + Catering Agent + Daily Brief Agent.
- [x] Drift/Hermes-first grounding: read dispatcher, catering/menu skills, shift sick-call skill, daily brief script/schema, readiness matrix, live VPS timers/config, and installed Hermes skills/plugins.
- [x] Create implementation branch: `codex/productionize-shift-catering-brief`.
- [x] Write implementation plan with runtime-state grounding and Hermes-first analysis.
- [x] TDD: add `pilot-readiness-check` script for real-customer onboarding gates.
- [x] Tighten catering menu update authority contract so upload/apply permissions match dispatcher behavior.
- [x] Write the three-agent WhatsApp production smoke runbook.
- [x] Local focused verification.
- [x] Deploy to `main-vps`.
- [x] Runtime verification: readiness gate installed, bridge connected, gateway active, timers firing.
- [x] Seed real customer identity in `/opt/shift-agent/config.yaml`: `customer.name=Triveni`, `customer.location_id=loc_pineville_01`.
- [x] Correct live roster location metadata from `Triveni Jacksonville (TEST)` / `loc_jax_01` to `Triveni Pineville` / `loc_pineville_01`.
- [x] Rerun `/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/pilot-readiness-check --text`; result is READY (13 passed, 0 failed).
- [ ] Live WhatsApp smoke script from `docs/runbooks/production-pilot-shift-catering-daily-brief.md`.
- [x] Tighten `pilot-readiness-check` to require `config.customer.location_id == roster.location.id`, reject roster location names containing test/placeholder labels, and require meaningful location-id tokens such as `pineville` to appear in `roster.location.name`; local regression tests cover location-id mismatch, stale real name, id/name test labels, invalid-config comparison semantics, non-string/null location metadata, and text output.

Current runtime status after deploy `deploy-20260514-203430-bb243517` + identity patch `20260514T212446Z`: gateway active, WhatsApp bridge connected, timers active, roster valid, 6 active employees, 23 scheduled shifts, catering menu valid, 78 available menu items, Daily Brief catering learning enabled, and pilot readiness is READY.

## Active - Catering self-learning rails (2026-05-14)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse existing catering state, `catering-pattern-report.timer`, and Daily Brief delivery. Net-new scope is limited to a counts-only `catering-learning-summary.json` sidecar plus opt-in Daily Brief rendering. Runtime learning remains state/report-only; code/SKILL changes still require PR/review/deploy.

Plan: `docs/superpowers/plans/2026-05-14-catering-100-autonomy-self-learning-plan.md`
Design: `docs/superpowers/specs/2026-05-14-catering-self-learning-rails-design.md`

- [x] Write plan for the 100% catering autonomy destination and first safe self-learning slice.
- [x] Run 2-agent plan review and apply scope/safety fixes.
- [x] Write design for counts-only catering learning sidecar and opt-in Daily Brief readout.
- [x] Run 2-agent design review and apply privacy/runtime fixes.
- [x] Implement local schema, pattern-report, Daily Brief, and focused tests.
- [x] Create PR and run 3 parallel reviewers.
- [x] Fix PR-review findings: opt-in no-leads warning, broader privacy regression tests, catering timer install/enable, Hermes-venv runtime execution, and missing-log degraded sidecar.
- [x] Merge PR #87 to `main`.
- [x] Deploy to `main-vps` as `deploy-20260514-203430-bb243517`; deploy smoke passed.
- [x] Run `sudo -u shift-agent /usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/catering-pattern-report --dry-run --learning-days 30` on VPS and review output.
- [x] Enable `daily_brief.sections += ["catering_learning"]` after dry-run review.
- [x] Runtime dry-run Daily Brief with learning enabled: learning section rendered counts only; no raw customer text or prices.

## P0 — Live verification (passive, blocked on real customer traffic)

Reporter floor as of 2026-04-28: **0/26 (0%)** — all 26 entries are pre-fix synthetic test injections; no real Kimi-routed inbound since dispatcher schema deployed. Floor will move once real traffic starts. Trigger: send any test message to self-chat to validate the pipeline end-to-end.

- ✅ **2026-05-14 — Hotfix active-lead catering proposal requests** — live smoke showed "send two proposal menus" reached Hermes but cf-router returned `None`, allowing the LLM to take a generic path instead of invoking the proposal workflow. Fixed by routing active-lead proposal requests through `create-catering-proposal-options --auto-generate-from-menu`, adding `cf_router_intercepted.reason=f7_proposal_request`, and pairing that audit reason in `dispatcher-accuracy-report`. Review result: focused VPS tests passed (`127 passed, 1 warning`) plus py_compile clean.
- [ ] **Verify dispatcher routing live** — next real inbound to your self-chat should produce a `dispatcher_routed` entry in `decisions.log` within ~10s of the matching `raw_inbound`. Run `sudo /usr/local/bin/dispatcher-accuracy-report --days 1` to check. Validates PR #14 + #15 end-to-end.
- [ ] **Verify menu photo upload pipeline** — auxiliary-vision auth fix (OPENROUTER/OPENAI keys mirrored into `/opt/shift-agent/.env`) is unverified live. Send a menu photo to self-chat; expect `parse-menu-photo` to extract items → owner preview reply with confirmation code.
- [ ] **Run dispatcher-accuracy-report after first real inbound** — confirm coverage % climbs above 0% as real traffic accumulates.

## P1 — Architecture review follow-ups (from reviewer thread, 2026-04-28)

### Test pyramid investments

- ✅ **2026-05-05 — Layer C v0.1 — recorded replay harness (scaffold + synthetic fixtures)** — shipped in unstaged tree:
  - `tests/_dispatcher_replay.py` (348 LOC) — `Fixture`/`ReplayResult` dataclasses, `load_fixtures()`, `load_dispatcher_skill()`, `parse_handler_from_response()` (longest-match handler extraction), `replay_one()`/`replay_all()`, `mock_llm_priority_order` (deterministic priority-walker), `mock_llm_returns_expected` (test-only), `openrouter_llm_caller` (placeholder for v0.2).
  - `tests/test_dispatcher_replay.py` (143 LOC) — pytest harness; 19 passing tests + 8 skipped (parametrize over 20 slots, 12 fixtures). Self-consistency check: priority-order mock matches every fixture's expected_handler.
  - `tests/fixtures/dispatcher_traffic.jsonl` — 12 synthetic fixtures covering matrix priorities 1, 2, 3, 4, 5, 6, 7, 9, 11, 13, 14 + one priority-trap regression case.
  - `src/platform/scripts/extract-replay-fixtures` (167 LOC) — VPS-runnable script pairing `raw_inbound`+`dispatcher_routed` from decisions.log into fixture JSONL; emits notes that state_files/config are placeholders (audit log doesn't capture state at decision time, curator must fill in for priority-1-5 cases). Production audit log on srilu-vps currently has 0 raw_inbound entries (per memory: 0/26 floor) — synthetic fixtures carry the harness for now.
  - **v0.2 deferred work:** wire up real `openrouter_llm_caller` (openai-Python client + OpenRouter base URL + parse with `parse_handler_from_response`); add `HERMES_REPLAY_MODEL` env-driven test variant; add cost-tracking decoration; grow synthetic fixture set to ~30 (cover priorities 8/10/12); pull real fixtures once production traffic flows. Real-LLM gating is what unblocks step 4 (default-model flip) per P2.5.
- [ ] **Layer A — full E2E with real Kimi** (high cost, run rarely). 36-case smoke suite, ~$0.10–0.50/run, 3–6 min. Run pre-deploy and on any SKILL.md change. Build after Layer C is stable.
- [ ] **Auxiliary vision pipeline test** — synthetic image upload through the bridge stub, assert pending file gets created within N seconds. Doesn't fit cleanly into A/B/C since failure mode is auth/wiring not LLM judgment. Standalone reliability test.

### Catering test-case doc revision (reviewer's Option 1)

- [ ] **Drop "agent invents prices" failure modes** (C23, C25 in reviewer's case list). Impossible by construction — Kimi never sees prices.
- [ ] **Refocus C06–C13 dietary cases** from "did the LLM filter correctly" to "did the LLM extract dietary tags into the lead correctly" (Python deterministically filters the menu downstream).
- [ ] **Resolve C02 design** — does Catering recognize returning customers via:
  - (a) Catering SKILL Python preamble does phone-lookup against `catering-leads.json`, injects "returning customer, last booking N days ago" into Kimi context (recommended; matches menu-source pattern), OR
  - (b) Treat as unknown until self-identification (simpler, loses warm-recognition UX)?
- [ ] **Add 3–4 prompt-injection variants** to C32 (reaches 5 total).
- [ ] **Add 2 dispatcher-routing-layer cases** now that we know it's the highest-leverage testable surface.
- [ ] **Reduce 2 cases that became low-stakes** under per-VPS isolation (cross-tenant threat scope was wrong).

### Schema implications from review

- ✅ **2026-04-28** — **C23 renderer + extractor-prompt** — shipped via commit `46780e8`. Renderer in `create-catering-lead._render_approval_card` lines 152-203; extractor prompt in `parse_catering_inquiry/SKILL.md` line 24. Both halves shipped in the same commit; no silent-drop window. Backlog tracking only (re-confirmed by 2026-04-29 plan-review pass).
- ✅ **2026-04-28** — **Past-date validation in `create-catering-lead`** — shipped via commit `8f4e6ea`. `_validate_event_date` covers past-date / invalid-calendar / timezone-invalid with `CateringLeadRejected` audit + `REASON_TO_ERR_PREFIX` dispatch. v3.1 C10 transitions from design-spec-pending to RUNNABLE.
- ✅ **2026-04-28** — **Build `lookup-prior-leads-by-phone` script** — C02-Option-C foundation per `docs/catering-edge-cases.md` (v3.1) C02 case. Shipped via PR #26 (squash-merged). 22 tests; full Plan→5→Design→5→Build→PR→5 pipeline applied.
- ✅ **2026-04-29** — **SKILL preamble integration for `lookup-prior-leads-by-phone`** — shipped via PR-A (catering omnibus). `parse_catering_inquiry/SKILL.md` Step 0 invokes the script subprocess-style and feeds the dict back into Kimi's prompt context as soft priors for extraction. v3.1 C02 transitions from "shipped-but-unwired" to RUNNABLE end-to-end. 8 SKILL static tests pin the contract (script invocation, all `LOOKUP_STATUS_*` constants branched, default-row for unparseable output, sender_phone provenance documented, no privacy-leak phrasing).
- ✅ **2026-04-29** — **Hardening: `oserror:` status handling in `safe_load_json` consumers** — shipped via PR-A. New `safe_io.assert_load_status_clean` helper centralizes the contract; `apply-catering-owner-decision` (initial load + post-bridge re-load with stricter status!=ok check) and `create-catering-lead` use it. Closes silent-failure-hunter NEW-1.
- ✅ **2026-04-29** — **Lock target migration: writer-side flock pattern** — shipped via PR-A. New `safe_io.try_acquire_filelock_with_retry` (raise-on-exhaustion `LockUnavailable` contract); `lookup-prior-leads-by-phone` now targets the SAME `.lock` sibling that writers use. Cross-script convention test asserts all 3 scripts agree on `LEADS_LOCK`. Closes silent-failure-hunter NEW-5.

## P1.4 — PR-A follow-ups (deferred during 2026-04-29 design-review + PR-review pipelines)

These items were dropped from PR-A (catering omnibus) when 5-agent reviews surfaced enough specific concerns that they warranted their own focused cycles. Each has a known design path; none is blocking.

### From design review (round 2)

- [ ] **Catering edge-case doc revision (v3.2)** — drop unreachable cases (Hermes never sees prices), refocus C06–C13 dietary cases (extraction-target not filter-target), formally mark C02 RUNNABLE end-to-end, add 4 prompt-injection variants to C32, add 2 dispatcher-routing cases. **Reviewer-R5 finding:** the prior plan referenced case IDs that don't exist in `docs/catering-edge-cases.md` v3.1 (caps at C22; no C23/C25/C32/C40/C41/cross-tenant cases). Before drafting: read the actual deployed doc, identify accurate insertion points, decide whether to introduce in-place tombstone convention OR keep using existing "Deferred cases" table at line ~524. C32 prompt-injection variants split between dispatcher-layer (v=1 spoof, code-fence subprocess) and parse-skill-layer (markdown link, Unicode normalization) — target the right SKILL.
- [ ] **`lookup_invoked` LogEntry variant for observability** — PR-A's SKILL preamble runs `lookup-prior-leads-by-phone` on every catering inquiry but produces NO `decisions.log` entry. Soak monitoring for `lookup_status=lock_timeout` rate is currently a manual journald grep, disjoint from `dispatcher_routed` correlation. Add a new `_BaseEntry` subclass with `type: Literal["lookup_invoked"]`, `lookup_status`, `prior_lead_count`, `last_seen_days_ago`. Either the SKILL emits via `log-decision-direct` after parsing the JSON, OR the script writes the entry itself. Pair with a follow-up `lookup-status-distribution-report` cron mirroring `send-routing-accuracy-summary`. **Reviewer-R3 finding from PR-A design review.** ~half-day.
- [ ] **Test fixture conftest hoist** — `tests/_b1_helpers.py` docstring says fixtures should hoist to `tests/conftest.py` per design-review HIGH-C1. PR-A skipped this to avoid scope creep; new tests in PR-A used `_b1_helpers` directly. The hoist remains valuable for future tests (no fourth copy of `BridgeStub`). Watch out for: Windows-portability of conftest-collection-time imports (lazy-import inside fixture body), and the broken `mod.__name__ = "__main__"` pattern in `tests/test_catering_v02_scripts.py` which `_b1_helpers.py` claims was always no-op'ing. **Reviewer-R1+R3 findings from PR-A design review.** ~half-day.
- [x] **`tests/_b1_helpers.py` "v02 tests no-op'd" investigation** — closed by existing `tests/test_v02_probe.py`: static `SourceFileLoader` pattern guard plus Linux-only runtime probe that imports `apply-catering-owner-decision`, verifies module body execution, and confirms the `SHIFT_AGENT_CONFIG_PATH` module-level override fires. This resolves the import/module-body ambiguity; it does not separately inject `assert False` into each legacy v02 test method.

### From PR #34 review (2026-04-29 — expense YAML-loaded-as-JSON fix)

- [x] **Smoke gate step 11 invokes one expense script** - closed by existing `shift-agent-smoke-test.sh` prune-and-expire `--dry-run` marker (`SMOKE_OK` / `SMOKE_FAIL`) at `src/agents/shift/scripts/shift-agent-smoke-test.sh`.
- [x] **`hermes-alignment.md` Part 1 Storage one-line policy note** - closed by existing Storage section note: JSON `load_model` rename-quarantines parse failures; YAML `load_yaml_model` is YAML-aware, raises explicitly, and does not auto-rename operator-edited files.
- [x] **`config_load_failed` LogEntry variant** - closed by existing `ConfigLoadFailed` schema variant, `LogEntry` union wiring, and `audit_helpers.log_config_load_failed_best_effort` coverage.
- [x] **Script-level integration test for expense config-load** - added `tests/test_expense_config_load_integration.py` covering `extract-receipt` and `apply-expense-decision` with valid YAML config reaching the next deterministic boundary, not `EXIT_SCHEMA_VIOLATION`.
- [x] **Migrate existing inline yaml.safe_load callsites to `load_yaml_model`** — catering scripts were already migrated; remaining `shift-agent-smoke-test.sh` config.yaml readers now use `safe_io.load_yaml_model` for config validation, owner phone extraction, Pushover key extraction, freshness enabled-gates, and Expense #21 schema/config validation. Added `tests/test_shift_smoke_config_load.py` static guard. Claude Code read-only review: CLEAN; no blocking findings.

### From PR review (round 3, 2026-04-29 PR #33)

- [ ] **Two-phase smoke gate (deploy-order pre-restart import check)** — `shift-agent-deploy.sh` runs smoke-test AFTER `systemctl restart hermes-gateway`. A missing safe_io symbol means traffic hits the new code in the ~5s+smoke-window before rollback fires. Split: run a fast `python3 -c "from safe_io import ..."` import-only gate BEFORE the restart, then full smoke (Pushover + systemd checks) after restart. **Reviewer-R5 medium finding.** ~1 hour.
- [ ] **Rollback path re-runs smoke** — `shift-agent-deploy.sh` rollback case extracts prior tarball + install + restart, but doesn't re-run smoke. If the prior tarball is itself broken (e.g. someone manually edited `/opt/shift-agent/safe_io.py` between deploys), rollback completes silently. After rollback install: run smoke; if it fails, Pushover priority 2 ("Rollback FAILED smoke — agent in uncertain state, SSH"). **Reviewer-R5 low finding.** ~30 min.
- [x] **Pre-existing audit-log-wrong-lead bug at apply-script post-bridge re-load** — closed by existing PR-D2 code: `apply-catering-owner-decision` uses `matched_idx = next(...)`, exits with `EXIT_SCHEMA_VIOLATION` when the lead is absent after bridge POST, emits `log_quote_sent_lead_missing_best_effort`, and Pushover P2 best-effort alert. Static guard: `tests/test_catering_apply_post_bridge_missing_lead.py`.
- [x] **`from contextlib import contextmanager` mid-file in safe_io.py** - closed; `src/platform/safe_io.py` imports `contextmanager` in the top import block.
- ✅ 2026-04-29 — **`tasks/todo.md` P1.6 numbering** — renumbered to P3.5 in PR-C to match file position (Expense Bookkeeper v0.2 follow-ups sit after P3 Platform / infrastructure cleanup). Reviewer-R2 low finding.
- [ ] **Test gaps from R3 PR-review** — (a) `assert_load_status_clean` empty-string + leading-whitespace status; (b) `try_acquire_filelock_with_retry` negative attempts/sleep clamps; (c) integration test for corrupt: status path through writer scripts (currently only unit-tested in test_safe_io_load_status); (d) post-bridge re-load BUG path via BridgeStub side-effect (delete or chmod leads.json after writing the response); (e) lock-parent-dir auto-creation; (f) ast-based LOOKUP_STATUS_* enumeration (replaces fragile regex). **Reviewer-R3 medium findings.** ~half-day to add all six.
- [ ] **PR body / soak-monitoring instructions inconsistency** — PR #33 description says watch `decisions.log` for `oserror`/`lock_timeout` paths, but PR-A's new error paths only emit to stderr→journald. After deploy: soak instructions for operators should be `journalctl -u hermes-gateway -f | grep -E 'unhealthy load|LockUnavailable|BUG: leads.json|lookup_status='` for the new branches AND `tail -f /opt/shift-agent/logs/decisions.log` for existing audit signals. **Reviewer-R5 medium finding.** Reflect in any future ops runbook.
- [ ] **`safe_io_pure` cross-platform split** — 4 of 5 new test files skip on Windows because safe_io.py imports fcntl unconditionally. Splitting `assert_load_status_clean` + `LoadStatusError` into a fcntl-free `safe_io_pure` module would let those tests run cross-platform. **Reviewer-R3 low finding.** Out of scope for PR-A; track for future safe_io refactor.

## P1.5 — Catering Lead v0.4 — LLM-drafted customer quote (DEFERRED to PR-B with reviewer-flagged corrections)

**Status (2026-04-29):** Originally bundled into a single "catering omnibus" plan; 5-agent plan-review consensus was to split into PR-A (hardening + lookup wiring + hygiene) and PR-B (v0.4 LLM-drafted quote). PR-A merged 2026-04-29; PR-B remains its own full-pipeline cycle.

**Reviewer-flagged corrections that PR-B MUST address (do not re-implement the prior plan as-is):**

- **`extra="ignore"` rollback narrative was invalid** — flipping `CustomerConfig` and `CateringLead` from `extra="forbid"` to `extra="ignore"` does NOT actually provide v0.4→v0.3 rollback safety. Rollback runs the v0.3 BINARY which still has `extra="forbid"` baked in. The deployed convention for forward-compat is `mode="before"` validators with sentinels (see `_backfill_legacy_quote_text` at `schemas.py:535`). PR-B should use that pattern OR re-tag as `drifts-from-Hermes` with explicit operational rationale.
- **`catering-lead-context` helper that doesn't exist** — prior plan/design's `handle_catering_owner_approval/SKILL.md` Step 2.5 referenced `/usr/local/bin/catering-lead-context` with a "fallback to direct jq queries if the helper doesn't exist yet" branch. Either build the helper as a sub-task of PR-B (small read-only context bundler) OR drop the reference and inline the exact `jq` queries in the SKILL.md. Don't ship a SKILL that conditionally calls a non-existent binary.
- **`CateringQuoteSkillFailed` audit class needs `original_message_id`** — v0.3 idempotency anchors (`CateringQuoteAttempted`, `CateringDeclineAttempted`) all carry `original_message_id` for replay correlation. The new failure variant must too.
- **SKILL→`log-decision-direct` audit-write path is too vague** — prior plan said "the SKILL logs `catering_quote_skill_failed` via `log-decision-direct`". Either inline the exact CLI invocation in the SKILL prompt, or move the audit-write into `apply-catering-owner-decision` via a `--skill-failure-reason` flag. Option (b) matches the deployed script-as-chokepoint convention better.
- **Truth-preservation guard substring check is exploit-trivial** — `str(headcount)` in `qt` passes for `headcount=50` if the quote contains `"150 people"` or `"the 50% off promotion"`. Use word-boundary regex like `re.search(rf"\b{re.escape(str(hc))}\b", qt)` and similar for `event_date`.
- **`headcount=None AND event_date=None` defense gap** — guard skips both checks when both fields are None. PR-B should either (a) require non-empty `--quote-text` minimum length and emit a WARN when neither truth field exists, or (b) explicitly test the "guard skips" behavior so it's pinned not accidental.
- **`CateringQuoteAttempted` v0.3 idempotency anchor was never actually written** by deployed code despite docstring claim. v0.4 inherits this gap; PR-B should write the anchor BEFORE the bridge POST under the same lock, and on retry-entry check its presence to short-circuit duplicate sends.
- **WhatsApp markdown injection** — drafted text goes straight to `_bridge_post(jid, message)`. PR-B should normalize: strip zero-width chars (`​-‏`, `‪-‮`, `﻿`), enforce single-line CRLF→LF, cap length at 600 chars. Add 1 test for malicious-zero-width-LRO inquiry → drafted text → apply-script strips them.
- **YAGNI: `voice_quality` field** — `bad-tone` parser is deferred to v0.5; `voice_quality` is dead code in v0.4. PR-B should drop it AND drop the bad-voice filter from `recent_sent_quotes()`. Reintroduce both together in v0.5.
- **Active-traffic deploy runbook missing** — paradigm change (template→LLM-orchestrated draft) needs explicit runbook for in-flight `AWAITING_OWNER_APPROVAL` leads during the deploy window.
- **`menu_filter.py` extraction location** — prior plan invented `src/agents/catering/menu_filter.py` with no peers. PR-B should pick a justified home: inline into `lookup-prior-leads-by-phone` (only runtime caller) OR `src/platform/` (since it depends on platform schemas). Don't create a new per-agent helper-module convention for one ~30-line function.
- **Branch divergence rationale** — `fix/catering-comprehensive` doesn't *delete* expense-bookkeeper code; it predates PR #30. Cherry-pick onto a fresh branch off main is operationally simpler than rebasing 12 v0.3-hardening commits over the merged expense work. State the reason accurately.

**Hermes capability checklist (per CLAUDE.md):**

| Step | Hermes? | Net-new? |
|---|---|---|
| Owner WhatsApp inbound | [Hermes] | — |
| Skill dispatch on approval-code reply | [Hermes] dispatcher | — |
| Parse code + verb (approve / reject / edit) | [Hermes] LLM in SKILL | — |
| Read lead context from `catering-leads.json` | [Hermes-adjacent] tiny preamble or existing helper | minor |
| Draft customer quote in owner's voice | [Hermes] LLM-orchestrated SKILL | prompt only |
| Persist quote_text on lead + transition status | [existing] `apply-catering-owner-decision` | tiny `--quote-text` flag |
| Bridge POST → customer | [existing] apply-script's `_bridge_post` | — |
| Audit (`CateringQuoteSent`, `CateringLeadStatusChange`) | [existing] | — |

Genuinely net-new: tone-sample plumbing + `--quote-text` flag + small schema additions. ≤ ~150 LOC + ~15 tests. v2 design was sized at ~615 LOC + 78 tests; ~75% of that was over-engineering.

**Read deployed code first** (per drift-rule §Part 3):
- `src/agents/catering/skills/handle_catering_owner_approval/SKILL.md` — current v0.2 verb classifier; gets one new step ("draft a quote in owner's voice")
- `src/agents/catering/scripts/apply-catering-owner-decision` — current approve flow renders quote via template; gets one new flag (`--quote-text`)
- `src/agents/catering/templates/catering_quote_to_customer.txt` — gets removed
- `src/platform/schemas.py` — `CateringLead`, `CustomerConfig`, `CateringLeadStore`
- `tools/catering-state-migrate.py` — only modified if voice-sample backfill is genuinely needed; the `recent_sent_quotes()` method reads leads.json directly so backfill may be unnecessary

## P2 — Routing reliability hardening (incremental)

- [ ] **Log `dispatcher_routed` for declined unknowns too** (Item 2 of original P1+P2 bundle, deferred during 2026-04-28 design-review pipeline). Currently the SKILL writes only `unknown_sender_declined` on the decline path. Uniform logging would simplify the report (no fallback by-phone matching). Small SKILL.md edit + reporter tweak — but **needs its own plan/design/review cycle** because the design review surfaced that `DispatcherRouted.message_id` is required (`Field(min_length=1)`) and `UnknownSenderDeclined` doesn't currently carry message_id; the source path in the SKILL instruction needs explicit specification + a no-op fallback warning addition in the reporter.
- [ ] **Schedule weekly cron for `dispatcher-accuracy-report`** (Item 3 of original P1+P2 bundle, deferred during 2026-04-28 design-review pipeline). Pushover summary on Sunday morning. **Substantial silent-failure-hunter findings during design** that need addressing before build: (a) OnFailure handler service for cron-itself-broken case + ConditionPath* removal so OnFailure actually fires, (b) "cron never ran" watchdog (3-week silent skip undetected today), (c) exit-code surface 0/1/2/3 over-engineered for an 80-line script, (d) `capture_output=True` swallows reporter stderr WARN, (e) empty-window `0/0 (0%)` panics owner, (f) `Persistent=true` multi-fires after weekend outage, (g) `--priority -1` is silent on Pushover. Needs its own cycle.
- [ ] **Capture interesting routing pairs to fixtures file** as they arrive — start a `tests/fixtures/dispatcher_traffic.jsonl` with manually-curated entries from `decisions.log`. Seeds Layer C.
- [ ] **Strengthen image+menu fallback** — currently Fix 3 in PR #14 catches misrouted image+menu in `handle_owner_command`. Audit other handlers for similar misroute paths once data shows where Kimi actually misroutes.

## P2.5 — Model strategy & cost optimization (2026-05-05)

**Context:** Discussion arc starting from a proposed model swap (k2-thinking → tiered split) culminated in a verified finding: per-skill model routing does NOT exist in Hermes 0.12.0 (see `~/.claude/projects/.../memory/reference_hermes_model_routing.md`). Available granularity is global default + task-type auxiliary overrides (Vision, STT/TTS, compression). Decision: capture ~95% of achievable savings via "B today + step 4 after dispatcher validation" — i.e., OpenRouter cheapest-provider routing now + flip global default to gpt-4o-mini after the Layer C dispatcher-replay harness validates parity. Multi-profile architecture (option A) deferred with explicit triggers below.

**Cost math at 10-customer realistic mix (3 quiet + 5 mid + 2 active):**

| Architecture | Monthly | Annualized | Δ vs current |
|---|---|---|---|
| All-Kimi-k2.5 (current default) | $461 | $5,535 | baseline |
| All-gpt-4o-mini | $151 | $1,810 | −$3,725/yr |
| A+B (multi-profile + OpenRouter cheapest) | $170 | $2,040 | −$3,495/yr |
| **B-only (Kimi everywhere + OpenRouter cheapest)** | **$345** | **$4,140** | **−$1,395/yr** (turn on now) |

**Drift-check tag:** `Hermes-native` for B (config-only) and step 4 (config-only). Multi-profile A is `extends-Hermes` (uses existing `hermes profile` capability without patching).

**Hermes capability checklist (per CLAUDE.md):**

| Step | Hermes? | Net-new? |
|---|---|---|
| Switch global default model | [Hermes] `hermes config set` | — |
| OpenRouter cheapest-provider routing | [Hermes] OpenRouter passthrough already configured | ~5 LOC config |
| `hermes profile` for multi-profile isolation | [Hermes] existing CLI capability | — |
| Per-skill model override | [net-new] would need upstream Hermes change OR fork+patch | deferred — not built |
| Dispatcher-replay harness for validation | [net-new] tracked separately as P1 Layer C | medium effort |

### Now-ish — independent of dispatcher work

- ✅ **2026-05-05 — Enable OpenRouter cheapest-provider routing on Kimi calls (B)** — added top-level `provider_routing: { sort: "price" }` block to `/root/.hermes/config.yaml` on srilu-vps. Verified end-to-end by composition: (a) Hermes 0.12.0 schema supports `provider_routing` per `cli-config.yaml.example` lines 42–60 + `gateway/run.py` reads it via `pr.get("sort")`; (b) `agent/transports/chat_completions.py:extra_body["provider"] = provider_prefs` actually sends it to OpenRouter; (c) yaml parses cleanly; (d) `hermes config show` runs without error; (e) hermes-gateway service active+running post-restart with config loaded; (f) **direct curl to OpenRouter with `provider.sort: price` returned `"provider":"Novita"` (cheap Kimi provider, not Moonshot direct), test cost $0.0007848 for 320 tokens** — proving the parameter is honored at the API. Backup at `/root/.hermes/config.yaml.bak-20260505-022248`. Pre-deploy gateway-restart hit a `chown` race; root cause: stale backup files with restrictive perms (see P4 cleanup item below).
- [ ] **First-traffic runtime confirmation of `provider_routing.sort=price`** — not directly observable on srilu-vps until WhatsApp inbound flows again or a scheduled job (Daily Brief, EOD) fires. When traffic resumes: capture one `auxiliary_client.async_call_llm` response and confirm `provider` field is non-Moonshot (Novita / Together / DeepInfra / SiliconFlow). Until then, B is verified-by-composition (see ✅ above) but lacks single-trace observation. Effort: ~5 min of log inspection once traffic flows. Tracker for B's full closure.

### Sequenced — gated on dispatcher-replay harness completion

- ✅ **2026-05-05 — Investigate 2026-05-01 dispatcher hangs** — full diagnosis in `tasks/diag-2026-05-01-hangs.md`. Root cause is NOT k2-thinking reasoning+tool-use interaction. The 320s/11-api-call/0-char-response signature is **vision auxiliary-client `401 AuthenticationError` ("Missing Authentication header") loops** in `/usr/local/lib/hermes-agent/agent/auxiliary_client.py:3708` triggering main-client tool-call thrashing. As of 2026-05-05, 64 occurrences of the 401 in last 2000 log lines on srilu-vps — issue is currently active. Switching the dispatcher model would NOT fix this. **New blocker for step 4 (default-model flip):** vision auth must be fixed first, OR step 4's quality validation must explicitly carve out image-bearing inputs as a separate test surface. See diag doc for three approach candidates (auto-provider, explicit api_key in aux block, upstream Hermes auxiliary-client fix).

- ✅ **2026-05-05 — Fix vision auxiliary-client 401** — applied candidate (a): changed `auxiliary.vision.provider` from `openrouter` to `auto` in `/root/.hermes/config.yaml` on srilu-vps. The `auto` chain calls `_try_openrouter()` at `auxiliary_client.py:78` which uses `os.getenv("OPENROUTER_API_KEY")` directly (verified the env var IS in process env via systemd `EnvironmentFile=/opt/shift-agent/.env`). Backup at `config.yaml.pre-vision-fix-20260505-180337`. Verification: **0 `AuthenticationError` occurrences in journalctl since 18:05:01 restart** (was 64/2000 lines pre-fix). Gateway active+running. Single-call observation deferred to next inbound image (same idle-traffic constraint as P0). See `tasks/diag-2026-05-01-hangs.md` §"Recommended actions" #1 for full details. **Companion follow-up still open:** wire the existing P1 "Auxiliary vision pipeline test" into deploy-gate smoke so this regression class is caught at install time.

- ✅ **2026-05-05 — Step 4 SHIPPED on srilu-vps** — `model.default` flipped from `moonshotai/kimi-k2-thinking` → `openai/gpt-4o-mini`. **Kimi retained as `fallback_providers` entry** for 14–30 day soak-window resilience (fires on primary errors only, costs nothing during normal operation). Backup at `/root/.hermes/config.yaml.pre-step4-20260505-203536`. Verified: `hermes config show` reports new primary; `hermes fallback list` shows 1-entry chain (kimi via openrouter); hermes-gateway service active+running post-restart. All 4 substrate config layers in harmony: primary=gpt-4o-mini, fallback=kimi, provider_routing.sort=price (PR #72), auxiliary.vision.provider=auto (vision-auth fix 2026-05-05). Per `tasks/step-4-readiness-summary.md`: 93.3% routing parity + 100% catering prose truth-guard, 11x cheaper, 12x faster, eliminates the 0-char-response failure mode documented in production. **Pre-flip checklist (all closed):**
  - ✅ (a) `dispatch_shift_agent/SKILL.md` has explicit priority-order + anti-shortcut framing — already does
  - ✅ (b) Replay-harness parity proven — gpt-4o-mini = kimi-k2-thinking = 93.3%, single mismatch is documented edge case both models handle the same way; gpt-4o-mini is 11x cheaper, 12x faster (avg 1s vs 12s)
  - ✅ (c) catering's `handle_catering_owner_approval` LLM-drafted-quote prose A/B'd via 5 synthetic leads on srilu-vps 2026-05-05. **gpt-4o-mini: 5/5 = 100% truth-guard pass; kimi-k2-thinking: 4/5 = 80% (one 0-char-response failure mirroring the documented production failure mode).** gpt-4o-mini is 12x cheaper, 25x faster on this workload. Tool: `tools/run-catering-prose-parity.py`. Full report: `tasks/step-4-readiness-summary.md`. **All 4 step-4 gates closed.** Awaiting operator authorization to flip. (Real-traffic A/B remains a follow-up post-flip soak observation, not a prerequisite.)
  - ✅ (d) EOD show-your-math prompting — **N/A** (EOD reconcile + Daily Brief are deterministic Python, not LLM-driven; verified 2026-05-05). See "Gate (d) … N/A finding" below for resolution.
  - Mitigation if regression surfaces post-flip: ✅ Kimi retained as `fallback_providers` entry on srilu-vps; rollback is one `cp /root/.hermes/config.yaml.pre-step4-20260505-203536 /root/.hermes/config.yaml` + `systemctl restart hermes-gateway` away.

- [ ] **Step 4 soak-window observation (NEW 2026-05-05)** — for 14–30 days post-flip on srilu-vps, watch `/opt/shift-agent/logs/hermes-gateway.log` for: (a) zero `AuthenticationError` recurrence (vision-auth fix is the dependency), (b) zero `0-char response` patterns (was the kimi failure mode that step 4 eliminates), (c) `dispatcher_routed` audit entries written for every inbound, (d) customer-facing catering quotes contain headcount + ISO date (truth-guard intact in production), (e) fallback never fires (primary stays healthy) OR fires cleanly (graceful degradation when it does). After 30 clean days: retire the kimi fallback via `hermes fallback clear`, simplifying the config.

- [ ] **Step 4 fleet rollout (NEW 2026-05-05)** — after srilu-vps soak passes, replicate the same config change on the other VPSs (main-vps, any per-customer VPSs). Same approach: backup config.yaml, edit `model.default` to gpt-4o-mini + add fallback, restart hermes-gateway. ~10 min per VPS. No tarball deploy needed (config-only change).

**See P2.6 for the structural finding** (owner self-chat blocked by agent_echo filter, gated on BSP). The earlier rough-draft inline note is superseded.

- ✅ **2026-05-05 — Gate (d) EOD show-your-math prompting — N/A finding** — investigated and resolved as moot. `src/agents/eod_reconcile/scripts/eod-reconcile` is fully deterministic Python (counts events from decisions.log + pending.json, writes snapshot, sends Pushover summary). `src/agents/daily_brief/scripts/send-daily-brief` is also template-based ("`Render the brief by interpolating into the template (no LLM in v0.1).`"). No LLM in either path → no prompt to add show-your-math to. Original concern (gpt-4o-mini's multi-step arithmetic drift) doesn't apply because the production EOD path doesn't ask the model to do arithmetic. Step 4 checklist updated: gate (d) removed; only (c) catering prose A/B remains. Refocus, if needed: future agents that DO involve multi-step arithmetic (expense_bookkeeper RealQBOClient with tax rules per V02-4, or pnl_anomaly when scaffolded) can adopt show-your-math then.

### Deferred — multi-profile architecture (option A)

**Status (2026-05-05):** Technically buildable today via existing `hermes profile` capability (no patch needed). Operational tax: 2x systemd units, 2x process memory (~250MB → ~500MB resident; fine on CCX13), shared filesystem state via existing flock + `safe_io.atomic_write_json` conventions. Deferred because (a) B + step 4 captures ~95% of achievable savings, (b) reasoning-heavy agents that would benefit (pnl_anomaly, compliance, expense_bookkeeper full prod) aren't built yet, (c) operational tax not justified at 10-customer scale.

**Architecture sketch when triggered:**
- `hermes-gateway-rt` profile: gpt-4o-mini default; handles WhatsApp inbound dispatcher + real-time skill chains (high volume, latency-sensitive, ~85% of LLM calls).
- `hermes-gateway-batch` profile: Kimi-k2.5 (with B = OpenRouter cheapest) OR claude-haiku-4.5 default; handles cron-triggered jobs — Daily Brief, EOD, future pnl_anomaly + expense_bookkeeper batch path (low volume, latency-tolerant, quality-sensitive, ~15% of LLM calls).
- **Shared infrastructure:** WhatsApp bridge (Baileys), cf-router, watchdogs, project SKILL files (`src/agents/*/skills/*`), `/opt/shift-agent/state/`, NDJSON `decisions.log` via `safe_io.ndjson_append` chokepoint, approval-code namespace via `generate_unique_code` shared file under flock.
- **Per-profile:** `~/.hermes-rt/` and `~/.hermes-batch/` (config + sessions + memory), separate gateway processes (systemd units), separate LLM provider clients.
- **Routing rule:** WhatsApp inbound → RT profile (existing default path). Cron-triggered jobs (`systemctl start daily-brief.service`, etc.) → invoke batch profile binary explicitly via `HERMES_PROFILE=batch hermes ...`.

**Trigger conditions — build A when ANY of these is true:**

| Trigger | Why it matters |
|---|---|
| Customer count crosses ~30 | Cost gap >$1K/mo, payback <1 quarter on engineering effort |
| First reasoning-heavy agent (pnl_anomaly / compliance / expense_bookkeeper full prod) scaffolded | Global-default tradeoff becomes unacceptable; A is the path to keep both quality (batch) and cost (real-time) |
| Production quality incident traced to model-skill mismatch | Real evidence vs. theoretical optimization; e.g., Daily Brief misses anomaly that reasoning model would catch |
| Hermes upstream adds per-skill auxiliary support (`auxiliary.<skill_name>`) | Adopt native; A becomes obsolete in favor of upstream-supported per-skill routing |

**Estimated effort when triggered:** 3–5 days incl. systemd unit creation, env-symlink validation across both profiles (don't break the existing `.env` symlink gate from PR #18), dispatcher-replay harness rerun on both profiles, deploy-gate updates (`shift-agent-deploy.sh` needs to know about both gateway services), Pushover/cf-router integration with both profiles, runbook for failure modes (one profile down ≠ full outage; verify systemd unit independence).

**Re-check trigger:** every alignment-doc audit pass (currently 2026-07-28). If Hermes upstream PR adds `auxiliary.<skill_name>` support, switch from "build A" to "adopt upstream native" and close this entry.

### Credit-burn defense (2026-05-08 audit deferrals)

**Context:** Vizora burned all OpenRouter credits to $0 on 2026-05-06 via three compounding failures: (1) `hard_stop_enabled: false` letting tool-error loops accumulate per-turn, (2) `max_tokens` defaulting to 16,384 per call, (3) 5-min cron on a low-event LLM skill firing 288×/day. SMB-Agents audit on 2026-05-08 (full report at `tasks/audits/credit-burn-audit-2026-05-08.md`) confirmed:

- F1 (loop) ✓ NOT PRESENT — Hermes session caps `max_tool_calls: 50`, `max_turns: 60`, `delegation.max_iterations: 50` in `/root/.hermes/config.yaml`. Hard ceiling per session = 50 tool calls.
- F2 (16K tokens) ⚠️ GAP EXISTS BUT MITIGATED — Hermes `chat_completions` transport passes `params.get("max_tokens")` which is `None` when not set; OpenRouter falls back to model default. **Mitigated today** by low call volume (~25 LLM fires/day, lifetime spend $7.78, daily $0.00003). Worst-case at current volume = $0.24/day.
- F3 (5-min cron) ✓ NOT PRESENT — no LLM-calling 5-min cron. Daily-brief / EOD are date-gated (296 `brief_skipped` vs 5 `brief_sent` in last 24h prove gating works).

**Two deferred items, captured here for revisit:**

- [ ] **R1 — Add `max_tokens` cap to Hermes config (closes F2)**. Edit `/root/.hermes/config.yaml`'s `agent:` section to add `max_tokens: 4096` (sensible default for routing/dispatch; matches `parse-menu-photo`'s explicit `max_tokens: 8192` for vision extraction). One-line config change. **Re-check trigger:** any of (a) catering inquiry volume scales >10/day, (b) new LLM-calling agent ships (pnl_anomaly, compliance full prod, expense_bookkeeper RealQBOClient), (c) `usage_daily` from OpenRouter key check exceeds $0.10/day for 3 consecutive days, or (d) any Hermes upgrade past 0.12.0 (re-verify the default behavior didn't change). Latent gap: also `src/agents/expense_bookkeeper/scripts/extract-receipt` lines 320 + 360 have no `max_tokens` in payload — fix in same commit. Effort: ~10 min config + 5 min script edit + deploy verify.

- [ ] **R3 — OpenRouter daily-spend alarm (closes ALL credit-burn modes via independent backstop)**. Set up an OpenRouter dashboard email alert at $X/day threshold AND/OR add a periodic `usage_daily` health check from `shift-agent-health-check.sh` that emits a `decisions.log` warning if daily spend > $1. The dashboard alarm is 5-min UI work; the in-audit-chain version is ~30 min. **Re-check trigger:** same as R1 — defer until volume scales OR a new LLM-heavy agent ships. **Why both R1 and R3:** R1 is a per-call cap (prevents runaway), R3 is a spend-floor alarm (catches anything R1 misses, including model-pricing changes or new agents that bypass the global cap).

**Why deferred:** today's volume + lifetime spend ($0.00003/day, $7.78 lifetime) is orders of magnitude below the danger zone. Adding caps now is good hygiene but not urgent. Re-visit triggers above are the discipline gate — don't let "low volume today" become "still no cap when traffic 10×s."

### Agent #33 Loyalty v0.2 follow-ups (2026-05-10 — v0.1 birthday-only shipped)

**Context:** Agent #33 v0.1 shipped birthday reminders only (Daily Brief
section + record-customer-birthday CLI script per option-C plan-review pivot).
The full Loyalty & Punch-Card vision is staged across these v0.2/v0.3 items.

- [ ] **v0.2 — WhatsApp owner-command for adding birthdays**. Wraps the existing
  `record-customer-birthday` CLI in a SKILL + dispatcher row so owner can text
  `add birthday +1xxx 03/15 Suresh Patel` instead of SSH-calling the CLI.
  Per `feedback_dont_overengineer_llm_intent.md`: let Hermes classify intent
  (no regex whitelist). Trigger: customer demand for owner-friendly UX OR a
  customer onboarding cycle where SSH-only feels operationally too thin.

- [ ] **v0.2 — Punch-card / points schema + visit counter + reward triggers.**
  The full "Loyalty" surface per portfolio.md:998. Adds `LoyaltyPoints` schema,
  visit-counting from `CateringLead` history (or POS integration when #30/#31
  ship), reward-trigger thresholds (e.g., 10 visits → free dessert). Trigger:
  customer asks for a digital punch-card.

- [ ] **v0.2 — Auto-customer-facing birthday greeting**. Daily-brief cron
  fires the existing logic; in v0.2 also send a WhatsApp greeting to the
  customer directly (with outbound-cap accounting via existing send-counter
  + opt-out flag stored on `CustomerBirthday`). Trigger: owner asks for
  hands-off greeting workflow.

- [ ] **v0.3 — Year-aware "turning N" extension.** Add optional `year` field
  to `CustomerBirthday`; render "Suresh Patel turning 35 (+1xxx)". Skip until
  a customer asks; many customers don't share or won't update accurately.

- [ ] **Test-helper factor-up (cross-cutting)**. Third occurrence of importlib
  `SourceFileLoader` + `sys.modules` pre-load pattern in this PR (after #41
  + #32). Per #32 retro lesson #3: factor `_load_script(name, path)` +
  `preload_platform_modules(platform_dir)` to a shared `tests/_test_helpers.py`
  (or extend `tests/conftest.py`). Currently 4 call sites duplicate the
  pattern (`_b1_helpers.py`, `test_owner_wellbeing_quiet_hours.py`,
  `test_lookup_prior_leads.py`, `test_daily_brief_birthdays.py`). Estimated
  ~50 LOC helper + ~10 LOC change per call site = ~90 LOC refactor PR.
  **Trigger: another agent-build session that needs the same pattern.**

- [ ] **Default-sections opt-in vs explicit-opt-in policy** for new BriefSection
  values. v0.1 chose explicit opt-in (operator must add `"birthdays"` to
  `cfg.daily_brief.sections`). Re-evaluate when v0.2 lands more sections —
  may want a per-section default that ships enabled-by-default for low-noise
  ones.

## P2.6 — Owner self-chat structurally blocked by agent_echo filter

**Status:** Logged 2026-05-05. Resolution gated on BSP-backed number go-live.

**Drift-check tag:** `extends-Hermes` — finding documents an upstream Hermes bridge.js behavior that interacts with the deployed multi-device WhatsApp pairing model. No code change in this section; gates ride on BSP timeline (separate workstream).

### Finding

The bridge's agent_echo filter (bridge.js, `fromMe: true && (REPLY_PREFIX match || recentlySentIds match)`) blocks every owner self-chat message from reaching the gateway. Path is always-blocked, not intermittent — confirmed against bridge logs on 2026-05-05.

### Evidence

- Bridge log entries `{"event":"ignored","reason":"agent_echo","chatId":"918522041562@s.whatsapp.net"}` for owner-side test messages
- L0003 (5/3) trace previously cited as "owner self-chat that worked" was actually **customer-side**: `customer_phone +17329837841`, `chat_id 17329837841@s.whatsapp.net`, `role unknown`, `fromMe: false`. Bot's own JIDs are `918522041562@s.whatsapp.net` (IN number) + `211390371475536@lid`
- Cache-pollution hypothesis (`recentlySentIds` filling over the day) is **rejected** — no successful owner self-chat trace exists in any window we have logs for

### Path status

| Path | Status |
|---|---|
| Customer-side traffic (external chatId, `fromMe: false`) | ✅ Validated end-to-end via L0003 on 5/3 — dispatcher + parse_catering_inquiry + create-catering-lead chain all ran clean |
| Owner self-chat (own chatId, `fromMe: true`) | ❌ Structurally blocked since deployment |

### Step 4 deployment status (qualified)

Three separate statements that should not be conflated:

1. **Deployed** on srilu — yes
2. **Mechanically correct** (filter is upstream of step 4 logic, no behavior change from this finding) — yes
3. **Validated under real traffic** — *partially*. Customer-side path validated via L0003. Owner-as-customer test path has never validated and won't until bot ≠ owner architecturally

### Architectural fix

BSP-backed number is the resolution path, not interim burner SIM:

- BSP route is bot-only by design → bot ≠ owner → owner-as-customer testing unblocks naturally
- Meta Business verification timeline (2–4 weeks) is shorter than the cost-benefit of pairing a separate WhatsApp account on srilu and re-doing QR + audit-chain config for an interim window
- BSP verification paperwork is already in flight in parallel with design partner outreach

Hot-patching bridge.js to disable agent_echo (option C from diagnostic) is **rejected** — risk of bot's own outbound replies being processed as inbound creates real loop potential on a production WhatsApp number, with rate-limit and BSP verification scrutiny consequences.

### Re-validation gate

Add as explicit checkpoint when BSP-backed number lands:

> When BSP-backed number is paired on srilu/prod (bot ≠ owner), re-run step 4 validation using owner-as-customer test traffic. Treat that as the actual end-to-end validation moment for the owner-test path. Customer-side validation remains continuous via real catering inquiries — first inbound real inquiry post-BSP confirms customer-path is unbroken under new routing.

### Re-evaluation triggers (kill / extend criteria)

- **If BSP verification slips past 2026-06-15** (≈6 weeks from logging): revisit interim burner SIM (option B). Cost-benefit shifts when delay exceeds the architectural fix's natural timeline
- **If a bug surfaces in step 4 under real customer traffic before BSP** that owner self-chat testing would have caught: priority escalates to P1, interim burner SIM gets greenlit immediately as test infrastructure
- **If BSP verification fails** (paperwork rejection, business verification issue): close this finding by promoting interim burner SIM to permanent solution, re-scope as architecture decision

### Connection to global discipline §9 (runtime-state verification)

The "test step 4 on srilu via owner self-chat" plan had an unstated runtime-state assumption: that the bridge would forward owner messages to the gateway. That assumption was wrong, and the wrongness was structural (always-on filter), not transient. The plan still produced the right next action — bridge log diagnostic ran, root cause was identified in one session — but the underlying lesson is that test-traffic plans deserve the same runtime-state scrutiny as config-only probes.

**Forward rule for similar plans:** before scoping any "validate via test traffic" workflow, list the message-path assumptions explicitly: which JID? `fromMe` true or false? Which filters does the message traverse before reaching the system under test? Verify each against runtime config / live logs before running. Bridge filters, dispatcher rules, audit-chain inserts, and approval-gateway state are all runtime-state surfaces that test plans implicitly depend on.

### Postmortem — failed patch attempt 2026-05-05 21:51 UTC

**Attempted:** drop the `recentlySentIds.has(msg.key.id)` half of the agent_echo filter at bridge.js:327, leaving only the `body.startsWith(REPLY_PREFIX)` check as loop guard.

**Reasoning at design time (faulty):** "In self-chat mode, `formatOutgoingMessage` prepends REPLY_PREFIX to all bot outbound text → REPLY_PREFIX check catches all bot echoes → safe to drop recentlySentIds." This was based on reading the function source, not on inspecting actual runtime outbound payloads.

**What actually happened:**

1. Patch applied at 21:51 UTC; bridge.js sha256 `753cbcbd881a...4ef72`; gateway restarted, bridge mode self-chat preserved, syntax-check passed
2. Operator (user) sent owner self-chat menu update test
3. Bridge correctly forwarded owner inbound to gateway (patch worked at the inbound side — this part of the design was correct)
4. Gateway processed, generated reply: *"The catering menu has been successfully updated with the new items and prices..."* (LLM-generated prose, no REPLY_PREFIX prefix)
5. Bridge sent reply via WhatsApp; outbound text did **not** start with REPLY_PREFIX
6. WhatsApp echoed bot's own outbound back to bridge as `fromMe: true` upsert event
7. Patched filter checked `body.startsWith(REPLY_PREFIX)` — false (no prefix)
8. Without `recentlySentIds` half, no other guard fired → bridge forwarded bot's own reply back to gateway as a new inbound
9. Gateway processed it as a fresh menu-update inbound → generated another reply
10. **Loop**. 4+ messages observed by operator within ~60 seconds before manual intervention

**Why the assumption was wrong:** at least one of two conditions held at runtime:
- `WHATSAPP_REPLY_PREFIX` env var is empty/unset (DEFAULT_REPLY_PREFIX path is bypassed because the env var is *defined as empty string*, not undefined; `process.env.WHATSAPP_REPLY_PREFIX === undefined` is false → empty REPLY_PREFIX), OR
- A bot outbound code path bypasses `formatOutgoingMessage` (e.g., a SKILL doing direct bridge POST, or the agent's terminal-output-as-WhatsApp-reply path that may not go through the same formatter)

Did not verify which at runtime. Either would invalidate the patch. **The pre-flight check was incomplete.**

**Resolution:**
- 21:59 UTC: bridge.js restored from backup `bridge.js.pre-self-chat-fix-20260505-215100` (filter back to original)
- Gateway restarted; bridge in self-chat mode; agent_echo filter catching loops as designed
- Loop messages stopped within seconds (in-flight LLM calls aborted with `Interrupted during API call`)

**State now:** identical to pre-patch (P2.6 finding stands; owner self-chat blocked by recentlySentIds matching → that's the original behavior, accepted as the cost of preventing loops).

**Lesson learned (escalated):** This is the third runtime-state-assumption failure today (R3 SQL UPDATE, agent_echo block at plan time, this patch postmortem). The "watch for >1/week" threshold in `memory/feedback_runtime_state_verification.md` is broken by ~7×. Discipline escalated from advisory to hard plan-time gate — see updated memory.

**Blockers for any future patch attempt:**
- Must inspect actual runtime outbound payload before assuming REPLY_PREFIX coverage
- Must enumerate ALL bot outbound code paths (not just the bridge's HTTP send endpoint — also direct subprocess sends from SKILLs, terminal-output-as-reply, catering quote drafting, etc.)
- Must test in a controlled environment where loops are containable (e.g., bot paired to a test number, not the production-receiving WhatsApp account)

**Recommendation forward:** do not patch the bridge filter. Instead, pair a separate WhatsApp number to srilu (option B from the original P2.6 framing). Bot ≠ owner eliminates the ambiguity that motivated this filter in the first place; the filter becomes irrelevant for owner-as-customer testing.

## P3 — Platform / infrastructure cleanup

See `docs/hermes-alignment.md` Part 2 for the silent-failure-ranked operational drift checklist. Items below cross-reference that doc; resolve there as the canonical tracker.

### Critical tier (silent-failure surface — from alignment doc)

- ✅ **2026-04-28** — Reconcile `shift-agent-deploy.sh` with actual VPS pattern (PR #16). Tarball-based deploy with snapshot-before-install, smoke gate, auto-rollback. End-to-end validated on VPS: deploy + rollback + rollforward + list. `tools/build-deploy-tarball.sh` runs pytest gate locally, captures `git rev-parse HEAD` into `.commit-hash`, ships ~116K tarball.
- ✅ **2026-04-28** — Pin Hermes commit hash in deploy.sh (PR #17). 3-field baseline pin (`HERMES_COMMIT`, `HERMES_VERSION`, `BRIDGE_POST_PATCH_SHA256`) verified by `tools/check-shift-agent-patch.sh` as first deploy gate. Override path with `HERMES_PIN_OVERRIDE=<full-hash>` + `HERMES_PIN_OVERRIDE_REASON` both required, dual-channel audit (pin-overrides.log + log-decision-direct), all 4 validation paths exercised live on VPS: fail-closed on drift, override-accepts-current, override-rejects-wrong-hash, override-rejects-missing-reason.
- ✅ **2026-04-28** — bridge.js patch inventory (subsumed by PR #17). Same gate covers `shift-agent-template-bypass` markers (added in PR #14, previously uncovered) + sha256 fingerprint of as-deployed bridge.js (catches in-version code drift + manual edits + partial patch reapplication).

### Config.yaml shape gate (NEW 2026-05-05)

- ✅ **2026-05-16 — `tools/check-hermes-config-yaml.sh` deploy-time gate** — closes M2 silent-failure surface: typo'd Hermes config keys (e.g. `model.dafault`) silently fall back to defaults because `hermes config check` / `hermes doctor` do not validate YAML shape (verified live 2026-05-16). New gate asserts shift-agent-load-bearing fields (`model.default`, `model.provider`, conditional `auxiliary.vision.{provider,model}`, advisory `provider_routing.sort`); 2-level subkey enumeration catches `auxiliary.visoin.provider`-class typos as WARN. Two-variable override (`HERMES_CONFIG_GATE_OVERRIDE_FIELD` + `HERMES_CONFIG_GATE_OVERRIDE_REASON`) with attestation check + dual-channel audit (plain-text log + new `config_gate_override` LogEntry variant). Wired into `shift-agent-deploy.sh` AFTER Hermes pin gate + `VENV_PY` guard / BEFORE credential-minimized foundation gate (ordering matters: foundation gate reads config.yaml for cf-router validation). Rollback path uses asymmetric WARN-skip posture for pre-merge tarball compat. Smoke-side informational pass (fail-closed on missing binary). Full pipeline cadence: Plan → 2 reviews (13 findings closed) → Design → 2 reviews (9 findings closed) → Build → PR → 3 reviews. Drift-check tag: `extends-Hermes`.

### Replay-harness follow-ups (NEW 2026-05-05 — PR #72 / Layer C v0.1)

- [ ] **Factor out `src/platform/audit_pairing.py` shared helper** — `src/platform/scripts/dispatcher-accuracy-report` and `src/platform/scripts/extract-replay-fixtures` both pair raw_inbound with dispatcher_routed by message_id. Currently the second duplicates the first with documented divergence (stricter validation, PII redaction, fail-closed default). Factor into `src/platform/audit_pairing.py` with a single `pair_by_message_id(entries, mode="strict"|"observability")`. Refactor both call sites. ~half-day. **Reviewer-R1 H3 finding (2026-05-05); v0.1 acceptable, debt tracked here.**
- [ ] **Replay harness v0.2 — real-LLM caller** — wire `openrouter_llm_caller` placeholder in `tests/_dispatcher_replay.py` to actually hit OpenRouter via openai-Python client. Add `HERMES_REPLAY_MODEL` env var driving a parameterized pytest variant. Add cost-tracking decoration so a CI run prints total $ spent. Expand synthetic fixture set to ~30 (cover unfilled priority rows: 8 image-no-caption, 10 compliance, 12 text-only-owner-no-code). Replace `parse_handler_from_response` substring scan with structured-output prompt (function-calling enum) — current scan is documented-fragile. **This unblocks step 4 (default-model flip) per P2.5.** Effort: 1–2 days.
- [ ] **SKILL.md hash gate — manifest with commit SHA** — current `SKILL_MD_KNOWN_SHA256` is trust-on-first-use. Reviewer-R2 MEDIUM-5 (2026-05-05) suggested storing hash + commit SHA together in `tests/fixtures/dispatcher_skill_manifest.json`, refusing to advance unless the manifest's commit SHA is reachable from `HEAD`. Acceptable trade-off for v0.1 (TOFU is loud-when-broken); track for hardening. ~1 hour. Bonus: also breaks the cosmetic-edit churn (Reviewer-R1 M5) by including non-semantic-edit detection.
- [ ] **Pull real fixtures from production audit log** — once srilu-vps gets real WhatsApp traffic, run `extract-replay-fixtures --in /opt/shift-agent/logs/decisions.log --out tests/fixtures/dispatcher_traffic_real.jsonl --since 2026-05-05` (default redacted; review residual-PII warnings; manually verify before commit). Currently 0 raw_inbound entries in deployed audit log so harness runs on synthetic only. Effort: ~30 min once real traffic exists; precondition: vision-auth fix landed.

### Hermes pin follow-ups (low priority)

- [ ] **Tighten WARN→FAIL on missing check script in `shift-agent-deploy.sh`** — per PR #17 reviewer's Low-4. After one full deploy cycle confirms tarballs ship `tools/`, change `else WARN` to `else FAIL` so future refactors can't silently bypass the gate. ~5 min.
- [ ] **Bats tests for override semantics** — per PR #17 reviewer's Low-5. Project has no bats infrastructure today; multi-day investment. Real gap: bash gate logic only validated by manual VPS run.
- [ ] **Clean up `hermes_agent.__version__` warn** — informational warn fires every deploy because import returns `unknown` (likely venv path or import-order issue in `check-shift-agent-patch.sh:5`). Doesn't affect correctness (commit-hash pin is authoritative); just noisy. ~30 min to investigate.

### High tier (active gotcha)

- ✅ **2026-04-28** — Single canonical `.env` via symlink (PR #18 + PR #19 strict-gate fix). `/opt/shift-agent/.env` is now a symlink to `/root/.hermes/.env`. Pre-flight drift detector (`tools/check-env-drift.sh`) hashes overlapping keys without leaking secrets; idempotent migration (`tools/migrate-env-to-symlink.sh`) auto-detects shift-only keys + creates timestamped backup; strict symlink-integrity gate in `shift-agent-deploy.sh` fails-closed before install_artifacts. Gate validated end-to-end: break symlink → exit 1 → restore → deploy passes.
- ✅ **2026-04-28** — Audit log rotation (subsumed). Investigation revealed the SHA-256 chain was decoration (~3% coverage, no verifier). Logrotate already configured (daily, 30-day retention, archive to `/var/log/shift-agent-archive/`). Removed the chain (Option B per review thread) rather than spending half-day building infrastructure to back up an aspirational claim. Deployed integrity story is now honest: append-only via flock + `0640` perms + off-server backups + deploy-time gates. See `docs/hermes-alignment.md` Part 1 for the architecture sketch if compliance need emerges.

### Deferred until specific need emerges

- [ ] **Cryptographic audit-log chain** (deferred 2026-04-28; see PR #20 for context). Architecture if needed: move `_append_sha_chain` into `safe_io.ndjson_append` chokepoint so all writers covered, add `verify-decisions-log` script, add daily-cron verification, run one-time backfill (with explicit "trust boundary" docs noting pre-backfill entries aren't cryptographically defensible). Total ~half-day. **Chokepoint claim audited 2026-04-28** — every `decisions.log` writer in `src/agents/*/scripts/` and `src/platform/scripts/` calls `safe_io.ndjson_append`; no raw `open(..., "a")` bypass exists. Re-introduction at the chokepoint will cover all writers. Triggers: regulator audit requirement, formal customer dispute defense, multi-tenant compliance posture.
- [ ] **Alignment-doc audit pass — next due 2026-07-28** (90 days from baseline) — pattern observed where alignment doc and deployed code drift in either direction: doc claims a feature we lack (PRs #17 Hermes pin, #18 .env consolidation, #20 audit chain), OR doc understates a feature we have (v3.1 catering-edge-cases audit-chain framing, 2026-04-28). Cheap quarterly exercise; surfaces drift before it bites. Concrete cadence (vs "~quarterly?") so the entry can't rot in the backlog. Roll the next-due date forward 90 days each time it runs.

### Deferred until informed by agent #2-style use case

- [ ] **`docs/platform-contract.md` with semver** — Medium tier in alignment doc. Enumerate `src/platform/*.py` public surface + log-entry types + script exit codes; tag v0.1.
- [ ] **Phase A.5 — `schemas.py` runtime registry split** (`register_agent_entries()`). LogEntry union now ~30 variants and growing.
- [ ] **Phase B — `/opt/shift-agent/` → `/opt/smb-agents/` rename** (~292 references including `tools/patch-hermes.py:158`). Half-day, ideally bundled with a maintenance window.
- [ ] **Phase C — cockpit modular split** (frontend section registry + backend `state.py` `_AGENT_ROOT` parameterization). Wait until agent #2 ships its own cockpit needs.

## P3.5 — Expense Bookkeeper v0.2 follow-ups

**Context:** v0.1 shipped 2026-04-29 via PR #30 (schema + mock QBO + 3 SKILLs + 10 templates). PR #32 closed 4 audit-found bugs (1 HIGH dispatcher routing, 1 MED whitespace validator, 2 LOW). Feature is opt-in (`cfg.expense_bookkeeper.enabled = false` by default); no real QBO write path until v0.2 ships `RealQBOClient`.

**Drift-check tag:** `extends-Hermes` — Hermes substrate handles vision-extract / structured output / approval-code dispatch / audit chain. Genuine net-new: QBO write API, money-moving UX (code+amount approval, perceptual-hash dedup, per-amount thresholds, reversibility window).

**Authoritative deferral list:** `tasks/expense-bookkeeper-v02-followups.md` — full rationale + suggested action for each item below.

### From audit-fix Stage 2 reviewer thread (defence-in-depth + DRY)

- [x] **V02-1 — Extend whitespace/null-byte validator** to `sender_lid`, `qbo_account`, `rejection_reason` (when present). Shared `ExpenseLead` validator now covers the optional fields while preserving `None`; `tests/test_expense_bookkeeper_guardrails.py` parametrizes blank/control-char rejection and clean optional values. Claude Code review: CLEAN.
- [ ] **V02-2 — Refactor `sender_phone` to `Optional[E164Phone]` + at-least-one-of validator** (mirrors `RawInbound` `schemas.py:1186-1208`). Drops the BUG-2 `Field(min_length=1)` constraint as redundant. ~200 LOC scope: extract-receipt persistence path, every `ExpenseLead` test fixture (currently plain strings), `apply-expense-decision` comparison logic. Pipeline: medium cadence.
- [ ] **V02-3 — DRY `_check_orphans` helper** — lift the ~70-line duplicate from `extract-receipt` + `apply-expense-decision` to `src/platform/expense_orphan.py`. Companion: `_scan_audit_for_push_completion`. Both scripts import. Expands install_artifacts surface; deferred from v0.1 fix-up explicitly. ~half-day with tests.
- [ ] **V02-4 — Token-redactor: bare OAuth `state=` / PKCE `code_verifier=` patterns** outside URL context. v0.1 risk surface is zero (MockQBOClient never produces real OAuth payloads). Add to `_TOKEN_PATTERNS` when `RealQBOClient` lands:
  ```python
  re.compile(r'\bstate=[A-Za-z0-9_\-\.]{8,}', re.IGNORECASE),
  re.compile(r'\bcode_verifier=[A-Za-z0-9_\-\.]{16,}', re.IGNORECASE),
  ```
- [ ] **V02-5 — `image_path` `os.path.realpath` symlink resolve** — only relevant if multi-tenant sharing of receipts dir ever happens. Currently impossible per per-customer-VPS isolation. Track but do not ship until that scenario emerges.

### From plan v2 §9 deferral list

- [ ] **V02-6 — `expense_lookup` SKILL** — analog of catering's `lookup-prior-leads-by-phone` (PR #26). Owner can query past expenses ("show me what I expensed at Costco last month"). Mirror the catering script + SKILL pattern. ~half-day.

### Cross-cutting (not strictly v0.2 but surfaced in audit-fix review)

- [ ] **V02-7 — Pre-existing dispatcher regex inconsistency** — `dispatch_shift_agent/SKILL.md:79` uses `#[A-HJ-NP-Z2-9]{5}` while canonical alphabet in `schemas.py:843` is `#[A-HJKMNPQR-Z2-9]{5}`. Both are functionally restrictive enough; the dispatcher's regex is stricter near the seam (excludes `K`/`M`). Unify to canonical regex everywhere — one PR, ~5 file edits, mostly tests. ~1 hour.
- [ ] **V02-8 — jq syntax-validity assertion in audit test** — `test_audit_bug1_dispatcher_skill_includes_expense_jq_lookup` is string-presence + ordering only. A subtle filter typo (missing paren) would pass the test but fail at runtime. Add a Linux-only test (`pytestmark.skipif(platform.system() == "Windows")`) that pipes each jq filter through `subprocess.run(["jq", "-en", filter])` and asserts exit 0.

### From original v0.1 PR review (overnight-report carry-forward)

- [ ] **Plan §4g edge cases not yet covered:** #2 typo'd code (silent), #7 sum-mismatch resolution, #9 vendor name normalization, #11 approval-code collision regenerate, #16 multi-receipt batch. Each is its own 1-2 day ticket once the v0.2 scope is concrete.
- [ ] **Apply-side `original_message_id` idempotency runtime test** (currently schema-only). Subprocess invoke `apply-expense-decision` twice with the same `original_message_id`; assert second invocation no-ops + emits the right audit class.
- [ ] **Cockpit web UI for above-threshold review** — v0.1 ships paper spec only. Owner currently has no GUI surface; reviews happen via WhatsApp approval codes. Cockpit-web extension is a separate platform-level project; sequence after V02-6 (lookup SKILL) so the cockpit has data to render.
- [ ] **Real `RealQBOClient` impl** — currently raises `NotImplementedError` in `src/platform/qbo_client.py`. Genuinely net-new (Hermes does not own external write APIs). Bundle with V02-4 token-redactor patterns. Pipeline: full cadence (>500 LOC, new architectural surface — OAuth + write scope + reversibility window).

**Total v0.2 scope estimate (excluding `RealQBOClient` which is its own arc):** V02-1 + V02-2 + V02-3 + V02-6 + V02-7 + V02-8 ≈ ~1.5 weeks elapsed. Pipeline cadence per matrix: medium for V02-2/V02-3/V02-6, light for V02-1/V02-7/V02-8.

## P4 — Hygiene + housekeeping

- [ ] **Stale `/root/.hermes/config.yaml.*-bak` backup files cause systemd `chown` race** (NEW 2026-05-05) — discovered while applying provider_routing change on srilu-vps. The systemd ExecStartPre `chown -R shift-agent:shift-agent /root/.hermes` step exits with `Operation not permitted` when leftover `config.yaml.bak-*` and `config.yaml.with-*-bak` files exist with restrictive perms (root-owned with `--e----` extent flag). Result: gateway enters restart-loop until manual `chown` unstuck it. Currently ownership-fixed on srilu-vps but the files remain. **Fix:** (a) prune backups older than ~7 days via cron, OR (b) make the operator-applied backup pattern always create files chowned to shift-agent up-front, OR (c) make ExecStartPre `chown` tolerant (`|| true`) and add a separate strict-mode verification step. Same risk on every VPS that's ever run a manual config edit. ~1 hour incl. cleanup + cron. Cross-reference: this is the actual root cause of the "chown race" mentioned in the P2.5 B-completion note.
- [ ] **`--yolo` CLI flag invocation mismatch** (NEW 2026-05-05) — `/opt/shift-agent/logs/hermes-gateway.log` shows recurring `hermes: error: unrecognized arguments: --yolo` errors from a non-gateway process. The systemd ExecStart for `hermes-gateway.service` is `python -m hermes_cli.main gateway run --replace` (no --yolo), so the errors come from a different process polluting the shared application log. Likely sources: cron job, watchdog script, or operator manual invocation. Investigation: `crontab -l` + `systemctl list-timers` + grep for `--yolo` in `/etc/systemd/system/*.service` + `/opt/shift-agent/scripts/`. Once found: fix the invocation (the flag IS valid for `chat` subcommand but not for `gateway run`). ~30 min. Surfaced in `tasks/diag-2026-05-01-hangs.md` §4 but parked as separate issue.
- [ ] **Clean up scratch-file pollution in repo root** — 400+ untracked `.AA_*.txt`, `.B_*.txt`, `.ph17_*.txt` etc. from prior debugging sessions. Either extend `.gitignore` with a smarter wildcard pattern (`.[A-Z]*.txt`, `.[a-z][_a-z0-9]*.txt`) or `git clean -fd` in a careful pass.
- [ ] **Review old pending task #8** — "Re-engage safety + commit validated fixes" — has been pending since the start of session history. Likely obsolete given subsequent safety/hardening commits (021e090, 7525c22, 8c14069). Confirm and close.
- [ ] **VPS `/opt/shift-agent/config.yaml` provisioning gap** — surfaces every deploy as a smoke-gate failure → auto-rollback. Current VPS state: `config.yaml` was renamed to `config.yaml.corrupt-1777465716` at some prior point; smoke test (`config.yaml does not validate against Config schema`) trips because the file is missing. Auto-rollback works correctly (verified PR #30 + PR #32 deploys), but no new code lands until `config.yaml` is restored from `config.yaml.template` + populated with the live owner phone/customer config + chmod-protected. Hermes-gateway + cockpit remain active on prior code throughout — not a service outage, just a code-freeze. ~30 min on VPS to fix; pure ops work, no PR needed. Flag: also surfaced via `WARN: Hermes version drift expected=0.11.0 current=unknown` (informational; commit-hash pin is authoritative).

---

## Process notes — pipeline cadence calibration

Three observations from review-pipeline experience worth carrying forward:

1. **The discipline catches real bugs at the design phase.** In one observed cycle, design review surfaced a wrong-target issue that would have cost a half-day of build+revert; PR review separately surfaced a silent-drop concern that drove a CONTRACT comment on a new field. Without the rigorous review rounds, both would have shipped silently.

2. **Bundle splits naturally surface under rigor.** A 3-item bundled cycle decoupled cleanly into "ship Item 1 focused, defer Items 2 + 3 to own cycles" once design review found design-blocking issues unique to Items 2 + 3. Without the rigor, the bundle would have shipped half-baked.

3. **Pipeline cost-per-line is high for small changes.** A representative observation point: ~15 agent calls per ~90-line schema PR. The recommendation below balances discipline against compute cost by sizing the pipeline to the PR.

**Recommended cadence-by-PR-size:**
   - **<100 lines, schema/doc/single-script:** lighter pipeline (Plan → Build → PR → 3 reviews)
   - **100-500 lines, multi-file feature with operational gates:** medium pipeline (Plan → 3 reviews → Design → 3 reviews → Build → PR → 5 reviews)
   - **>500 lines or new architectural surface:** full pipeline as established (Plan → 5 reviews → Design → 5 reviews → Build → PR → 5 reviews)

This is a future-process decision; not worth retrofitting prior PRs, but worth applying to upcoming work. Re-evaluate the matrix periodically (e.g., as part of the alignment-doc audit pass) — if observed-vs-recommended cadence diverges meaningfully, the matrix needs recalibration.

---

## Recently completed (this week)

- ✅ 2026-05-13 — **cf-router employee-private catering fix** — Hermes-first/drift check: reused the live `cf-router` plugin F7 primary path rather than adding a new skill. Root cause was the role gate suppressing `employee` after catering intent was already classified. Fixed primary + dormant rescue helper to suppress only `owner`; added regression coverage for employee/private/family catering inquiries. Hotfixed on `main-vps`; `hermes-gateway` active and WhatsApp bridge `/health` reports connected.
- ✅ 2026-05-13 — **cf-router active-lead weak follow-up fix** — follow-ups like “two proposal menus / non-veg / veg” emitted only weak food/menu signals and missed Branch B because active-lead lookup was gated behind the stricter new-inquiry classifier. Fixed F7 so weak catering signals can suppress/reply against an existing active lead, while still refusing to create a new lead from weak text alone. Hotfixed on `main-vps`; live probe against `L0014` now returns `f7_primary_followup_suppressed`.

- ✅ 2026-04-29 — **PR #34: Expense Bookkeeper YAML-loaded-as-JSON regression fix** — three scripts (`extract-receipt`, `apply-expense-decision`, `prune-and-expire-expenses.py`) called `safe_io.load_model(CONFIG_PATH, Config)` on `config.yaml`. `load_model` → `safe_load_json` → `json.loads(yaml_content)` → `JSONDecodeError` → file rename-quarantined to `config.yaml.corrupt-<epoch>`. Smoke gate failed (config missing) → deploy auto-rollback. Fix: new `safe_io.load_yaml_model` chokepoint helper using `yaml.safe_load`, no auto-rename, explicit raise. Migrated 3 callsites. 7 unit tests including no-rename regression guard. Discovered during PR-A deploy. Lighter pipeline (Plan→Build→PR→3 reviews) per matrix.
- ✅ 2026-04-29 — **PR-A: catering omnibus** — `feat/catering-omnibus-pra`. 7 commits: (1) `safe_io.assert_load_status_clean` helper for writer load chokepoints; (2) oserror surfacing in `apply-catering-owner-decision` + `create-catering-lead` (silent-failure-hunter NEW-1) + post-bridge state-loss strict-status check; (3) `safe_io.try_acquire_filelock_with_retry` (raise-on-exhaustion `LockUnavailable` — no bool footgun); (4) lookup-script lock-target migration to unified `.lock` sibling (NEW-5) + cross-script convention assertion test; (5) `parse_catering_inquiry/SKILL.md` Step 0 invokes lookup script with R4 design-review fixes (Hard rule + MUST framing, default-row for unparseable output, no privacy-leak phrasing, sender_phone provenance documented) — v3.1 C02 RUNNABLE end-to-end + 8 SKILL static tests; (6) smoke-gate import roundtrip for new safe_io chokepoint symbols; (7) backlog hygiene + PR-B + follow-up entries. Full Plan→5-agent-review→Design→5-agent-review→Build pipeline. v0.4 LLM-drafted quote split to PR-B follow-up cycle with 12 reviewer-flagged corrections.
- ✅ 2026-04-29 — PR #32: expense-bookkeeper audit-fix (4 bugs — 1 HIGH dispatcher routing, 1 MED whitespace validator on `sender_phone`+`original_message_id`, 2 LOW). Full ceremony: audit → plan v1.1 → 5-agent plan review → design folded → 5-agent design review → build → PR → 5-agent PR review → merge → deploy gate (auto-rolled-back on pre-existing config.yaml provisioning gap, feature stays opt-in so no behavior delta). 168/168 tests on PR head; 317/317 on merged main. 8 v0.2 follow-ups documented in `tasks/expense-bookkeeper-v02-followups.md` (now backlog P3.5).
- ✅ 2026-04-29 — PR #31: CLAUDE.md DRIFT RULES section (read deployed code BEFORE proposing). Companion to Hermes-first rule. Authority: `docs/hermes-alignment.md` Parts 1+3. Drift-check tags introduced (Hermes-native | extends-Hermes | drifts-from-Hermes) — every new plan/spec/design doc carries one. Memory file mirrored at `~/.claude/projects/.../memory/feedback_drift_rules.md`.
- ✅ 2026-04-29 — PR #30: Agent #21 Expense Bookkeeper v0.1 — schema + mock QBO + Solid 17 docs. Schema additions: `ExpenseBookkeeperConfig`, `ExpenseLead`/`ExpenseLeadStore`, 15 audit-entry classes, `EXPENSE_TRANSITIONS` table. Mock `QBOClient` Protocol + `MockQBOClient` + `RealQBOClient` stub (raises `NotImplementedError`). 3 SKILLs + 3 scripts + 10 templates + 2 systemd units. Feature ships **opt-in** (`enabled: false`). Full ceremony: plan → 5-review → design → 5-review → build → PR → 5-review → merge.
- ✅ 2026-04-29 — Solid 17 portfolio consolidation: 17 active + 5 backlog (was 20-agent commitment). Retired: #17, #18, #20. Live portal at http://46.62.206.192:8080/portal/ updated to terracotta+navy chess-board styling. Master spec at `docs/portfolio.md` (v2).
- ✅ 2026-04-28 — PR #22: catering edge case scenario library v3.1 (`docs/catering-edge-cases.md`); replaces v3 inline doc; 5 grounded corrections vs deployed code + 3-agent code-review round (must-fix `_normalize` accuracy bug + Bucket A count drift + claim-rot patterns); merged as 94177d2
- ✅ 2026-04-28 — PR #21: C23 schema field `off_menu_items` (full pipeline: plan → 5 reviews → design → 5 reviews → bundle-split decision → build → PR → 5 reviews → 8 review fixes → merge → deploy; 162 tests passing, deploy tagged 3b83c034)
- ✅ 2026-04-28 — PR #20: SHA-256 chain decoration removed; deployed integrity story now matches reality (append-only flock + 0640 perms + logrotate + backups)
- ✅ 2026-04-28 — PR #19: symlink-integrity gate strictness fix (PR #18's gate had inverted polarity — silently passed when symlink replaced by regular file; new gate is unconditionally strict; Step-5 break-then-restore validation confirmed exit 1)
- ✅ 2026-04-28 — PR #18: `.env` symlink consolidation + Hermes pin WARN→FAIL tightening (drift detector, migration script, integrity gate, smoke-check doc)
- ✅ 2026-04-28 — PR #17: Hermes pin gate (3-field baseline, fail-closed + override + dual audit; all 4 validation paths exercised live)
- ✅ 2026-04-28 — PR #16: tarball-based deploy formalizing actual VPS pattern (`docs/deploy.md` + `tools/build-deploy-tarball.sh` + rewritten `shift-agent-deploy.sh`); end-to-end validated incl. rollback path
- ✅ 2026-04-28 — `docs/hermes-alignment.md` v1: deployed-patterns reference + silent-failure-ranked operational checklist + read-deployed-code working agreement
- ✅ 2026-04-28 — PR #15: `dispatcher-accuracy-report` Layer 0 monitor (149 tests passing)
- ✅ 2026-04-28 — PR #14: dispatcher routing reliability hardening (3 fixes: routing matrix, `DispatcherRouted` schema, image+menu fallback)
- ✅ 2026-04-28 — `.gitattributes` enforces LF line endings for VPS scripts (root-cause fix for CRLF shebang break)
- ✅ 2026-04-28 — Catering menu v0.2 photo-upload pipeline shipped + deployed
- ✅ 2026-04-28 — Tier 2 sweep: agents 6, 7, 9, 10, 12, 13, 14, 15, 16 scaffolded (opt-in disabled)
- ✅ 2026-04-28 — Tier 1 complete: agents 1–5 shipped (2 LIVE full impl, 1 was-already-LIVE, 2 ship-disabled-opt-in)
- ✅ 2026-04-28 — Platform extract: `src/platform/` + `src/agents/<name>/` repo layout (PR #11)
- ✅ 2026-04-27 — Sender-id context (Phase A→D, LID injection + lid-learn cron)
- ✅ 2026-04-27 — Owner cockpit Phase 2 + Phase 3 deployed at http://46.62.206.192:9001/ui
## Active - Catering autonomous proposal flow (2026-05-13)

- [x] Confirm policy: no customer-facing pricing, deposits, payment instructions, or booking confirmation before final owner approval.
- [x] Run drift/Hermes-first review: reuse existing catering lead/menu/finalize/owner-approval primitives; source-control and constrain the live VPS `creative-catering-proposals` skill instead of greenfield chat logic.
- [x] Write design spec: `docs/superpowers/specs/2026-05-13-catering-autonomous-proposals-design.md`.
- [x] Incorporate Reviewer 1 spec feedback: proposal lock/lifecycle, bridge-failure non-selectability, finalize `--code`, finalize exit handling, and prose grounding.
- [x] Incorporate Reviewer 2 spec feedback: reachable selection routing, pinned-test disambiguation, dispatcher matrices, no-price regex, owner-card estimate label, audit-union/reporting, and rollout flag.
- [x] User review of written spec.
- [x] Implementation plan after spec approval: `docs/superpowers/plans/2026-05-13-catering-autonomous-proposals-implementation-plan.md`.
- [x] TDD implementation. (completed locally on `codex/catering-autonomous-proposals`; no commit/stage/deploy yet)
- [x] Local focused tests. Windows host verification:
  - `python -m pytest tests/test_catering_proposal_schemas.py tests/test_catering_proposal_skill_md.py tests/test_cf_router_plugin.py tests/test_dispatcher_accuracy_report.py tests/test_dispatcher_replay.py tests/test_repo_invariants.py -q` -> `68 passed, 80 skipped`.
  - `python -m py_compile` on proposal scripts, finalize script, cf-router, dispatcher report, and replay harness -> passed.
  - Git Bash `bash -n` on deploy/smoke scripts -> passed.
  - `git diff --check` -> passed with line-ending warnings only.
  - Full `python -m pytest -q` -> `7 failed, 741 passed, 598 skipped`; failures are known unrelated Windows/static baseline issues (`test_pr_b_v3_static.py` substring false positive on `_render_quote_from_lead_state`; web backend imports Linux-only `fcntl` through `safe_io` on Windows).
- [x] Linux/VPS script tests for `tests/test_create_catering_proposal_options.py`, `tests/test_select_catering_proposal.py`, and `tests/test_catering_finalize_menu.py` before deploy. VPS verification: `65 passed, 24 warnings`.
- [x] Tarball deploy to `main-vps` with proposal branch initially disabled, then enabled after verification. Gateway active, bridge connected, health check rc=0, `F7_PROPOSAL_BRANCH_ENABLED = True`.
- [ ] Live WhatsApp smoke: proposal request for active catering lead, then customer selection of option number, then owner approval gate. Fold into the production pilot smoke script after customer identity is seeded.

## Active - Credential-minimized Hermes mode (2026-05-14)

- [x] Review current repo drift rules, `tasks/lessons.md`, deploy smoke, deploy script, existing skills roadmap, and portfolio docs.
- [x] Run live VPS Hermes inventory for installed/enabled skills/plugins and credential presence by name only.
- [x] Perform market research across Hermes built-ins, Awesome Hermes, Self-Evolution Kit, vendor MCP servers, iPaaS MCP, no-key messaging, local LLM/OCR/maps alternatives, and manual-export workflows.
- [x] Write plan: `tasks/credential-minimized-hermes-mode-plan-2026-05-14.md`.
- [x] Run two parallel plan reviews and fold findings into the plan.
  - Fixed: readiness is additive and must not downgrade OpenRouter/Pushover/runtime gates.
  - Fixed: strict foundation check moves pre-install/pre-restart so rollback is not used for external Hermes install drift.
  - Fixed: matrix must be deployable under `src/platform/` and carry freshness/source/maturity fields.
  - Fixed: POS market coverage now includes Clover and customer-POS triage, not Square-only.
- [x] Write design spec with CLI/data/deploy/test contract: `docs/superpowers/specs/2026-05-14-credential-minimized-hermes-mode-design.md`.
- [x] Run two parallel design reviews and fold findings into the design.
  - Fixed: pre-install strict gate checks only external Hermes foundation skills; repo-installed `cf-router` validates after plugin install/pre-restart.
  - Fixed: CLI must use staged import path before `/opt/shift-agent`.
  - Fixed: subprocess tests use `sys.executable` for Windows.
  - Fixed: connector rows carry freshness windows and richer maturity/auth status; stale rows surface in output.
  - Fixed: docs updates must surgically amend stale QBO/payments/e-sign/review API claims.
- [x] Build readiness matrix + `credential-minimized-readiness` CLI + deploy smoke integration.
- [x] Update roadmap/portfolio/no-key analysis docs with current market research.
  - CLI/module: `src/platform/credential_readiness.py` + `src/platform/scripts/credential-minimized-readiness`.
  - Deploy: pre-install strict foundation gate, post-install/pre-restart `cf-router` enabled-state gate, smoke-test non-blocking report.
  - Docs: QBO/payments/e-sign/reviews updated to vendor MCP/vetted MCP first, custom raw API only after connector review fails.
  - Focused verification: `python -m pytest tests/test_credential_readiness.py tests/test_repo_invariants.py tests/test_tarball_includes_summary_artifacts.py -q` -> `18 passed, 2 skipped`.
  - Syntax verification: `python -m py_compile src\platform\credential_readiness.py`; Git Bash `bash -n` on deploy and smoke scripts; `git diff --check` -> all passed.
  - Full Windows host suite: `python -m pytest -q` -> `7 failed, 758 passed, 606 skipped`; failures match pre-existing baseline recorded above (`test_pr_b_v3_static.py` substring false positive and web backend `safe_io` import of Linux-only `fcntl` on Windows).
- [x] Open PR, run three parallel implementation reviews, fix findings, merge, and deploy.
  - PR #86 opened from `codex/credential-minimized-hermes-mode`.
  - Three implementation-review vectors dispatched: code/test mechanics, deploy/runtime/security, Hermes-first/market claims.
  - Fixed review findings:
    - CLI wrapper now preserves staged module precedence before `/opt/shift-agent`.
    - Connector readiness now reports `partial_env` for incomplete credential sets and recognizes connector-specific env names.
    - `cf-router` validation now compiles/imports read-only, checks `pre_gateway_dispatch`, avoids `__pycache__`, and treats `plugins.disabled` as a deny-list.
    - Roadmap/plan/no-key docs no longer call connector-backed surfaces guaranteed custom gaps or say `cf-router` is part of strict external foundation mode.
    - Re-review fix: QBO complete readiness now requires refresh token/environment as documented by Intuit MCP; Venmo API/OAuth row now cites PayPal/Braintree Venmo developer docs.
    - Re-review fix: strict foundation report no longer imports live `cf-router`; post-install `--validate-plugin cf-router` still does. Rollback to older tarballs now removes the new readiness binary/module.
  - Review-fix verification: `python -m pytest tests/test_credential_readiness.py tests/test_repo_invariants.py tests/test_tarball_includes_summary_artifacts.py -q` -> `20 passed, 2 skipped`; Python byte-compile for module + wrapper, Git Bash `bash -n` on deploy/smoke, and `git diff --check` -> all passed.

Review results:
- Final implementation review found four issues; all fixed:
  - cf-router now skips LLM for handled proposal-selection exits `{0,2,4,6,11}`.
  - `select-catering-proposal` no longer holds `PROPOSALS_LOCK` while invoking `finalize-catering-menu`; it claims under lock, finalizes unlocked, then rechecks superseding proposal sets before marking selected.
  - proposal generation now requires exactly two options by default or exactly three only when requested; schema/audit option counts enforce `2..3`.
  - generation failures now audit and best-effort alert the owner, including schema-level invalid options.
- Final focused re-review reported no blockers after the stale-selection and schema-invalid alert fixes.
- Deploy notes:
  - Hermes pin gate correctly blocked first deploy: live Hermes was `486b692d...`, repo baseline `c5b4c481...`, and sender-id markers were absent after migration. Trial-patched a copy first, then applied `tools/patch-hermes.py` live with backup `/opt/shift-agent/deploys/hermes-pre-proposal-senderpatch-20260514-010345.tgz`.
  - `.env` symlink gate correctly blocked next deploy. `/opt/shift-agent/.env` and `/root/.hermes/.env` were byte-identical, so `migrate-env-to-symlink.sh` ran safely and left backup `/opt/shift-agent/.env.pre-symlink-backup`.
  - Manual plugin rsync was needed after deploy because live `cf-router/hooks.py` lagged staging. Removed pycache created by manual compile so `hermes-gateway` ExecStartPre chown succeeded. Final restart active with bridge port 3000 listening.
  - PR #86 was merged to `main` and deployed as `deploy-20260514-162731-f4ce14db`.

## Active - Flyer Studio WhatsApp customer onboarding (2026-05-15)

- [x] Grounding: reuse cf-router pre-gateway WhatsApp interception, existing Flyer state directory, JSON-on-disk state, and bridge text delivery. No web app surface.
- [x] Design decision: new non-catering sender starts a WhatsApp registration wizard before flyer creation; active registered authorized numbers pass through to normal flyer routing; active flyer projects always take precedence.
- [x] Required onboarding fields: business name, business address, public business phone, business WhatsApp number, authorized flyer request number, business type, preferred flyer language, and monthly plan.
- [x] Plan tiers are config-driven via `FlyerConfig.plan_tiers`, defaulting to `$49.99/30`, `$69.99/60`, `$199/unlimited`; store only `plan_id` on customers so future price edits do not rewrite customer history.
- [x] Payment readiness: customer enters `payment_pending` with provider/url fields ready for Stripe, Razorpay, or manual payment-link integration.
- [x] Build: `src/agents/flyer/onboarding.py`, `src/agents/flyer/scripts/handle-flyer-onboarding`, Flyer customer schemas, cf-router onboarding intercept, and deploy smoke inclusion.
- [x] Verification: `python -m pytest tests/test_flyer_onboarding.py tests/test_flyer_scripts_static.py tests/test_flyer_schemas.py tests/test_flyer_agent_static.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py -q` -> `41 passed`.
- [x] Syntax verification: `python -m py_compile src/agents/flyer/onboarding.py src/agents/flyer/scripts/handle-flyer-onboarding src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py src/platform/schemas.py` -> passed.
- [x] Deploy to `main-vps` as `deploy-20260515-051124-f6dfaac0`; deploy smoke passed. First attempt exposed the known deploy-script self-overwrite gap for new install rules, so the new module was pre-placed from staging and the deploy was rerun successfully.
- [x] VPS temp-state smoke: completed WhatsApp onboarding flow through `payment_pending` without writing to the real customer registry; verified `CUST0001`, `growth` plan, business WhatsApp, authorized request number, and payment-pending status in `/tmp/flyer-onboarding-smoke.json`, then removed the temp file.
- [x] Extend onboarding with logo/template capture: customers can upload logos/templates during onboarding, during an active flyer request, or later as replacements.
- [x] Brand assets are stored under managed flyer state, preserve history, and make the latest upload of each kind active while deactivating the prior active asset.
- [x] Flyer generation now reads the active customer logo/template assets and includes them in the generation prompt plus image-reference content where supported by the provider.
- [x] Verification: `python -m pytest tests/test_flyer_onboarding.py tests/test_flyer_renderer.py tests/test_flyer_scripts_static.py tests/test_flyer_agent_static.py tests/test_flyer_schemas.py tests/test_flyer_workflow.py -q` -> `45 passed`.
- [x] Syntax verification: `python -m py_compile src/agents/flyer/onboarding.py src/agents/flyer/scripts/handle-flyer-onboarding src/agents/flyer/scripts/store-flyer-brand-asset src/agents/flyer/render.py src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py src/platform/schemas.py` -> passed.
- [x] Deploy to `main-vps` as `deploy-20260515-135242-f6dfaac0`; smoke passed.
- [x] VPS temp-state brand asset smoke: uploaded logo during onboarding, completed signup, uploaded replacement logo from same signup thread, verified old logo inactive, new logo active, and temp files removed.
- [x] Fix live Flyer routing regression: explicit new flyer requests, media-backed menu/template edits, and wrong-flyer correction messages now start new work instead of attaching to stale active projects.
- [x] Add project-level uploaded reference images so a template sent by a non-onboarded sender is available to the renderer immediately, not only after customer brand registration.
- [x] Allow menu/price-list flyers such as Lakshmi's Kitchen to generate without event date/time/venue when business name, priced items, and contact are present.
- [x] Verification: `python -m pytest tests/test_cf_router_flyer_routing.py tests/test_flyer_schemas.py tests/test_flyer_renderer.py tests/test_flyer_onboarding.py tests/test_flyer_scripts_static.py -q` -> `47 passed`; script/cf-router byte-compile passed.
- [x] Deploy to `main-vps` as `deploy-20260515-144411-f6dfaac0`; deploy smoke and pilot readiness passed.
- [x] VPS route smoke: Lakshmi price-list project has no missing fields; uploaded-template project stores `reference_image`; router returns new-work=true for explicit flyer, media price-change, and wrong-flyer correction, and false for logo replacement.
- [x] Live repair: created and sent Lakshmi's Kitchen project `F0007` preview to WhatsApp. Original Thursday Dosa image was not retained in Hermes image cache, so exact template repair requires resending the image after the routing fix.
- [x] Live recovery after user sent approved expanded Lakshmi item list: router correctly created fresh `F0008`; first generation was interrupted by provider/bridge reset, then manually regenerated, approved, finalized, and sent 4-file package through WhatsApp.
- [x] Retired superseded `F0007` draft to `completed` so a later `APPROVE` cannot send the older Lakshmi item list.
- [x] Retired stale `F0006` Weekend Breakfast project to `completed` so it can no longer absorb future generic replies or approvals.
- [x] Fix stale onboarding/session regression for trial customers: cf-router now bypasses onboarding intercepts for existing `trial` or `active` Flyer customers before stale session handling, with regression coverage for the exact `+17329837841` failure shape.
- [x] Verification: `python -m pytest tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_cf_router_plugin.py -q` -> `36 passed, 103 skipped`; `python -m py_compile src\agents\flyer\onboarding.py src\plugins\cf-router\actions.py src\plugins\cf-router\hooks.py` -> passed.
- [x] Deploy to `main-vps` as `deploy-20260517-155245-59407d33`; deploy smoke and pilot readiness passed.
- [x] Live repair/recovery: verified `CUST0001` remains `trial`, no stale onboarding session remained after deploy, production-side router probe confirmed stale-session + trial-customer flyer requests create a project instead of onboarding, and recovered the swallowed breakfast request as `F0013` with one concept sent for approval.
- [x] Fix second-sender duplicate onboarding regression: when a separate WhatsApp/LID repeats setup for the same registered business, close the duplicate session, connect the sender as an authorized requester only when the conflicting account is unique and the business name closely matches, and preserve pending brand assets.
- [x] Verification: `python -m pytest tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_cf_router_plugin.py -q` -> `37 passed, 103 skipped`; `git diff --check` and script byte-compile passed.
- [x] Deploy to `main-vps` as `deploy-20260517-162150-59407d33`; deploy smoke and pilot readiness passed. Live recovery cleared the `201975216009469@lid` stuck session, added `+19045550104` as authorized requester for `CUST0001`, preserved 2 brand assets, and sent ready prompt `3EB048F78D022D957A303A`.
- [x] Diagnose and fix bad `F0014` flyer quality: production used `openai/gpt-5.4-image-2` high quality, but the parser produced `a breakfast` and the compositor pasted raw request text into a black debug panel. Added recurring breakfast title cleanup, menu-item extraction, schedule cleanup, and a designed menu-card overlay.
- [x] Verification: `python -m pytest tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_flyer_onboarding.py tests/test_cf_router_flyer_routing.py tests/test_cf_router_plugin.py -q` -> `62 passed, 103 skipped`; script byte-compile and `git diff --check` passed.
- [x] Deploy to `main-vps` as `deploy-20260517-164403-59407d33`; repaired `F0014` without another model call by reusing the raw generated background, replacing the bad overlay, updating state/hash, and sending corrected preview `3EB0B626DCB1E86518B369`.
- [x] Controlled direct generation for customer-grade Flyer Studio posters (2026-05-17).
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: reuse Hermes WhatsApp ingress, image-reference media passing, JSON project state, OpenRouter image gateway, existing delivery, and text-manifest send gate. No new Hermes substrate is needed; net-new scope is the Flyer-specific poster copy plan, direct image prompt contract, and production render path that lets the image model produce integrated poster typography instead of a text-free background plus overlay.
  - [x] Add regression tests proving menu poster prompts include exact copy, item cards, reference-following instructions, and no longer tell the image model to suppress all readable text.
  - [x] Add regression tests proving real image-model concept renders use direct poster output instead of applying the server overlay path.
  - [x] Implement controlled poster copy plan and direct-generation prompt.
  - [x] Preserve deterministic/Pillow fallback for low-cost smoke and final package exports.
  - [x] Run focused verification, deploy to `main-vps`, and run a production smoke or monitored campaign repair.
  - Review: focused local suite `87 passed, 103 skipped`; byte-compile and `git diff --check` passed. Deployed `deploy-20260517-181423-59407d33`; production smoke checks passed. Real-model smoke passed after direct poster resize fix. Regenerated `F0015` with registered-business guard and sent customer-testable preview `3EB01B691C6675F7F69E06,3EB04855DECCA3152B68B9`.
  - [x] Fix approved-preview/final-package mismatch: finalization now ignores stale raw-background siblings for direct-generated posters and removes old raw siblings on new real-model renders.
  - [x] Verification: focused local suite `88 passed, 103 skipped`; byte-compile and `git diff --check` passed. Deployed `deploy-20260517-183126-59407d33`; production smoke checks passed.
  - [x] Live proof: `F0015` final-package smoke now renders `final_whatsapp_image` with the same SHA-256 as the approved preview, proving the final derives from the approved design.
  - [x] Live test unblock: reset `+17329837841` trial quota by appending operator `released` events for prior test reservations `F0013`, `F0014`, and `F0015`; backup saved at `/opt/shift-agent/state/flyer/customers.json.pre-test-unblock-20260517T184037Z`.
  - [x] Fix approval UX bug: `Approve`, `approve`, and sender-block-wrapped approval replies now finalize the active flyer instead of falling through to revision clarification.
  - Review: focused local cf-router/Flyer suite `29 passed, 104 skipped`; `py_compile` and `git diff --check` passed. Deployed `deploy-20260517-191129-59407d33`; live plugin smoke proved `Approve` calls `finalize_and_send_flyer` and does not call revision update.
  - [x] Fix post-delivery correction routing: active project lookup now uses all phones on the Flyer customer account and can reopen delivered projects for clear revisions, preventing stale project `F0013` from swallowing corrections meant for newer delivered `F0019`.
  - [x] Fix deterministic menu item swaps: revision parsing now handles requests such as `Swap Tatte Idly with Ghee Karam Idly` without clarification and injects a strong replacement/exclusion instruction into the regenerated prompt.
  - Review: focused workflow/schema/cf-router suite `56 passed, 106 skipped`; broader Flyer/cf-router suite `128 passed, 106 skipped`; byte-compile and `git diff --check` passed. Deployed `deploy-20260517-201010-59407d33`; applied the correction to live `F0019`, regenerated one preview, and sent it to `+17329837841`.
- [x] Payment-first quick flyer CTA (2026-05-17): add third campaign button `Create One Flyer - $4` for guest buyers who do not want onboarding.
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: reuse WhatsApp CTA replies, cf-router pre-gateway interception, JSON-on-disk state, safe_io locks/atomic writes, existing flyer project generation, preview/final approval flow, and bridge delivery. Net-new scope is only a lightweight guest-order state machine and routing gate that allows exactly paid guest orders to create one flyer without monthly onboarding.
  - [x] Add guest order schema/store and payment-first management CLI.
  - [x] Add third campaign CTA and route it before onboarding/project intake.
  - [x] Allow paid guest orders to bypass subscription quota once, then mark them used after successful preview generation.
  - [x] Verify locally, deploy, and document live behavior.
  - Review: focused quick-order/Flyer/cf-router suite `117 passed, 109 skipped`; byte-compile and `git diff --check` passed. Deployed with staging deploy script as `deploy-20260517-195240-59407d33`; all smoke checks passed. Temp-state VPS smoke proved guest order `pending_payment -> paid -> used` and one paid order is no longer reusable after consumption.
  - Operational note: live `quick_flyer_checkout_url_template` is not configured yet, so the CTA reply currently says the payment link is not configured. Configure a real payment template before sending this CTA to prospects.
- [x] Flyer Studio adaptive language/mode intake and location entitlement gate (2026-05-17).
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: reuse Hermes WhatsApp ingress, cf-router pre-gateway interception, sender identity, existing Flyer customer/onboarding JSON state, guest-order state, project creation, media/reference handling, quota, and delivery. Net-new scope is only Flyer-specific preflight state, guided intake prompts, language selection copy, and per-customer location entitlement validation.
  - Product decisions:
    - Supported language prompt starts with English, Telugu, Hindi, Malayalam, Tamil, Kannada, plus a few additional languages where feasible.
    - Use adaptive mode selection: ask language/mode for campaign CTAs and vague flyer starts; let complete text requests create immediately as Text Mode.
    - Text Mode must accept normal typed requests plus existing flyer images, logos, photos, and reference media sent by the customer.
    - Keep the free trial at 3 flyers for now.
    - Unlimited-plan cross-location denial copy: `This account is set up for Pineville. I can't create a flyer for Virginia under this subscription. Contact Support.`
  - [x] Add reusable Flyer intake/preflight session schema and helpers.
  - [x] Route campaign CTAs through language-first flow before onboarding/payment/project creation.
  - [x] Add mode choice after language: guided agent mode or text mode.
  - [x] Implement guided agent mode MVP with simple questions that synthesize a flyer request and create the project.
  - [x] Keep complete flyer requests on the fast Text Mode path and continue accepting reference flyers/logos/media.
  - [x] Add per-customer allowed-location fields and a conservative location mismatch detector for paid/unlimited customers.
  - [x] Add focused tests for CTA language-first, adaptive fast path, guided flow, text+media handling, and cross-location blocking.
  - [x] Deploy to `main-vps`, run local/VPS smoke, and send a fresh business campaign for manual testing.
  - Review: focused local suite `125 passed, 106 skipped`; `py_compile` and `git diff --check` passed. Deployed `deploy-20260517-212022-59407d33` through the staged deploy script after the first installed-script pass exposed the known self-overwrite gap for the new `flyer_intake.py` install rule. VPS smoke proved language-first -> Malayalam -> guided onboarding handoff, location block copy for Pineville/Virginia, and vague-vs-complete adaptive detection. Fresh campaign sent to `+17329837841` with media `3EB019AACFFB76C2C61F6C` and CTA `3EB0BFADC4F5AC9750C509`.
- [x] F0023 empty-project regression repair (2026-05-17): bare `Create flyer` retried from `+19802005022` created F0020-F0023 in `intake_started` with no concepts because the deterministic new-project classifier still returned true for vague action text.
  - [x] Reproduce with failing classifier regression test: `should_start_new_flyer_over_active("Create flyer")` returned true.
  - [x] Fix root cause so vague flyer starts do not count as new-project triggers.
  - [x] Add defense-in-depth guard in primary project creation so fallback Flyer routing starts intake instead of creating a blank project.
  - [x] Verify locally with focused Flyer/cf-router suite.
  - [x] Deploy and retire stale empty F0020-F0023 live projects.
  - Review: focused regression passed; broader Flyer/cf-router suite `125 passed, 106 skipped`; `py_compile` and `git diff --check` passed. Deployed `deploy-20260517-215123-59407d33`; live classifier now reports `Create flyer vague=True start_new=False` and detailed breakfast request `vague=False start_new=True`. Retired empty projects F0020-F0023 to `completed` after backup.
- [x] F0024 attached-sample readiness repair (2026-05-17): guided request asked to extract items/prices from a sample flyer, but no media/reference asset reached the project; the readiness heuristic treated generic `items/prices` words as enough and produced a misleading processing acknowledgement.
  - [x] Reproduce with failing regression test for attachment-dependent request without assets.
  - [x] Mark attachment-dependent briefs as incomplete until reference media exists.
  - [x] Replace misleading incomplete-project acknowledgement with an actionable prompt to attach the sample flyer or type details.
  - [x] Verify locally with focused Flyer/cf-router suite.
  - [x] Deploy and monitor live F0024 state/customer prompt.
  - Review: focused local Flyer/cf-router suite `126 passed, 106 skipped`; `py_compile` and `git diff --check` passed. Deployed `deploy-20260517-232228-59407d33`. Live audit showed F0024 later recovered through generation/approval/delivery before a corrective prompt was needed: status `delivered`, selected `C1`, final assets `A0002-A0005`, delivery audit at `2026-05-17T23:17:25Z`.
- [x] F0024 guided media/language fidelity repair (2026-05-17): customer attached a sample flyer during Guided Mode, selected Telugu, and asked to extract items/prices; generated design was visually good but all-English and generic because guided intake did not carry media into project creation and the render prompt did not hard-require Telugu/sample-price extraction.
  - [x] Add failing regressions for guided intake media carry-forward, router reference-media project creation, processing ETA copy, Telugu-first prompt copy, and extract-items/prices reference instructions.
  - [x] Preserve attached media through Guided Mode sessions and pass it to project creation as `reference_media_path`.
  - [x] Strengthen processing acknowledgement with a 5-6 minute check-back expectation.
  - [x] Strengthen direct-generation prompt for Telugu-first flyer text and exact item/price extraction from sample/reference flyers.
  - [x] Verify locally, deploy to `main-vps`, and smoke the live path.
  - Review: focused local suite `95 passed, 107 skipped`; byte-compile and `git diff --check` passed. Deployed `deploy-20260517-234251-59407d33`; deploy smoke passed. Temp-state VPS smoke proved Guided Mode image attachment -> `reference_media_path` -> `create-flyer-project` `reference_image` asset, with `preferred_language=te`. Post-deploy gateway active and WhatsApp bridge connected (`queueLength=0`).
- [x] Flyer Studio admin dashboard for operator support (2026-05-18): replace manual SSH/state-file operations with an authenticated operator UI.
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: reuse existing Flyer JSON state, account/quota scripts, campaign sender, project store, delivery audit, and current cockpit/portal deployment patterns. Net-new scope is a narrow operator surface and audited admin actions.
  - Initial actions: search customer by phone/LID/business, view plan/status/usage/projects, reset trial quota for testing, change plan/status, resend campaign, view latest campaign/flyer/project messages, and inspect stuck intake/project state.
  - Safety requirements: every mutation must create an operator audit event, write a state backup before mutation, require an explicit reason, and avoid deleting customer history.
  - [x] Write implementation plan: `docs/superpowers/plans/2026-05-18-flyer-admin-dashboard.md`.
  - [x] Add backend Flyer admin APIs with tests.
  - [x] Add frontend Flyer Studio dashboard section.
  - [x] Run focused tests/build.
  - [x] Deploy cockpit and verify live.
  - Review: backend cockpit tests `42 passed, 1 skipped`; focused Flyer schema/onboarding/project tests `50 passed`; frontend `npm run build` passed; agent tarball deploy `deploy-20260518-013249-59407d33` passed smoke/readiness; cockpit served at `http://46.62.206.192:8080/` with `/api/health` returning `{"ok":true}` and live Flyer summary reporting 2 customers, 1 one-time order, 11 active projects, and 7 stuck/intake projects.
- [ ] Flyer package platform-truthfulness backlog (2026-05-18): stop claiming “Instagram story” unless the asset is a true story-safe creative, not just a generic vertical image.
  - Need revisit later with product/design decision: either generate platform-specific WhatsApp image, square feed post, vertical story/status creative, and printable PDF with visual QA, or rename deliverables honestly to “WhatsApp image,” “square image,” “vertical/status image,” and “printable PDF.”
- [x] F0029 exact-reference-edit quality gate (2026-05-18): customer attached an existing Lakshmis Kitchen flyer and asked to remove an extra `08:00` plus add an item for `$9.99`, but the app generated a new low-quality flyer titled `Uploaded Flyer Template`.
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: reuse Hermes WhatsApp media ingress, project-level reference assets, JSON state, bridge replies, admin/manual review surface, and existing reference-scope gate. Net-new scope is only Flyer-specific classification of source-preserving artwork edits so they do not go through the new-poster image-generation path.
  - [x] Trace live F0029 state and confirm the wrong title/request mapping.
  - [x] Add regression tests for media-backed exact edit requests.
  - [x] Queue exact edits as manual/source-preserving work instead of auto-generating new artwork.
  - [x] Verify locally and deploy to `main-vps`.
  - Review: focused local suite `52 passed`; `py_compile` and `git diff --check` passed. Hot-deployed `actions.py`, `hooks.py`, `schemas.py`, and `create-flyer-project` to `main-vps`; gateway active and WhatsApp bridge health `{"status":"connected","queueLength":0}`. VPS smoke proved exact-edit project creation now returns `status=manual_edit_required`, uses the registered business name instead of `Uploaded Flyer Template`, and creates no concepts.
  - [x] Add source-preserving image-edit generation path using the OpenAI image edit endpoint with `input_fidelity=high` for uploaded flyer corrections.
  - [x] Reuse the exact approved source-edit preview for final exports and contain/pad it for alternate formats instead of regenerating or cropping the artwork.
  - [x] Reserve subscription/guest access before long-running generation, release it on generation or preview-delivery failure, and finalize usage only after the preview was delivered.
  - [x] Mark source-edit manifests as `source_edit_integrity_only` so the system does not over-claim OCR/text QA for model-edited artwork.
  - Review: focused Flyer suite `131 passed`; backend cockpit tests `48 passed, 1 skipped`; frontend `npm run build` passed; `py_compile` passed; `git diff --check` returned only line-ending warnings. Three reviewer passes found and the branch fixed: auth bypass in cockpit service, source-edit classifier/durability gaps, partial-preview access release, and guest-order reservation idempotency. `tests/test_cf_router_plugin.py` is Linux-only and skipped on this Windows host.
- [x] Flyer edit-flow hardening follow-up (2026-05-18): rigorous testing found that source-preserving edits still have production gaps: missing `OPENAI_API_KEY` makes source edits fail, unclear revisions can clear active previews/finals, natural edit language is under-parsed, and manual edit queue copy promises more than the system can complete automatically.
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: reuse Hermes WhatsApp ingress, sender identity, media cache, JSON state, `safe_io`, audit, bridge delivery, Flyer quota/guest access, and admin cockpit. Net-new scope is only Flyer-specific edit classification, revision parsing/state safety, source-edit provider readiness, and operator-visible manual edit work.
  - [x] Write implementation plan: `docs/superpowers/plans/2026-05-18-flyer-edit-flow-hardening.md`.
  - [x] Get implementation plan reviewed by two parallel agents.
  - [x] Write design spec and get it reviewed by two parallel agents.
  - [x] Build fixes with TDD and focused verification.
  - [x] Create PR and get three parallel review vectors before merge.
  - Reconciliation: superseded by the 2026-05-19 P0 run. S6 P0-5 shipped typed source-edit preflight/reason routing and source-edit provider reliability; S7 P0-6 shipped reason-code-aware customer replies; S8 P0-7 shipped golden coverage and the remaining S6 follow-ups.
- [x] Flyer reference-scope relationship memory fix (2026-05-19): after a customer chooses authorized use for an unrelated-looking source flyer, answers like `Co-owner` must be remembered and should continue the flyer update instead of asking the same relationship/logo/details question repeatedly.
  - Root cause: `consume_flyer_reference_authorization_reply()` stored the relationship as `authorization_note_recorded`, kept the pending row alive, and only continued on the exact phrase `use account details`.
  - Fix: first relationship/detail reply now consumes the pending authorization row, carries the note into the authorized source-edit project path, and uses saved account details by default. Exact option/reference choices are still excluded from this detail parser.
  - Review: focused router/static tests `81 passed`; broader Flyer suite `250 passed`; compileall and `git diff --check` passed.
- [x] P0-3 Flyer reference media OCR/vision extraction (2026-05-19): image menu/reference uploads must produce locked facts before generation; unsupported/PDF/provider-failed references must fail closed to manual review, not generic generation.
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: reuse Hermes WhatsApp media ingress, Flyer `FlyerAsset`/`FlyerReferenceExtraction`/`FlyerLockedFact` state, `safe_io` locks, existing OpenRouter vision pattern from `check-flyer-reference-scope`, and deployed visual QA/manual review gates. `productivity/ocr-and-documents` exists for future PDF work, but this slice deliberately uses image vision only and defers PDF extraction.
  - [x] Add red tests: image menu upload creates reference extraction facts; old flyer/reference facts are preserved unless customer text overrides; logo-only image does not become menu facts; PDF/non-image upload queues manual review; provider unavailable queues manual review; generation refuses unextracted required references.
  - [x] Implement OpenRouter image vision reference extraction provider with strict JSON parsing and deterministic injected/sidecar test seams.
  - [x] Wire `create-flyer-project` to use the production provider by default and queue `manual_edit_required` when supported image extraction fails or PDF/non-image is uploaded.
  - [x] Add generation preflight so projects with failed/unsupported required reference extraction do not generate generic flyers.
  - [x] Run focused Flyer reference/create/generation/static/QA tests and commit.
  - [x] Fix PR review findings: semantic item override instead of positional `item:N`; no low-confidence locked facts; promo/coupon discount suppression; common attached-menu wording; price-first typed offers; router manual-review copy on resumed paths; source-edit preflight quota release; deploy smoke for deferred reference extraction.
  - [x] Update production readiness backlog with landed P0-3 status and remaining gaps.
  - Review: focused P0-3 acceptance subset `11 passed`; reference/create/generation/static/QA suite `46 passed`; broader focused Flyer suite first `292 passed, 13 warnings`, then final `303 passed, 117 skipped, 13 warnings`; `py_compile` and `git diff --check` passed. PR #113 review vectors were extraction correctness, state/manual fallback safety, and runtime/deploy readiness.
  - 2026-05-19 continuation review on local worktree head `27de178` plus uncommitted review fixes: focused P0-3/Flyer/router suite `166 passed`; broader Flyer suite `304 passed, 13 warnings`; `py_compile` passed; `git diff --check` passed; Git Bash syntax check for deploy/smoke scripts passed. The default `bash` shim failed locally because `/bin/bash` was unavailable, so `C:/Program Files/Git/bin/bash.exe` was used.
- [ ] Flyer source-edit provider config wiring (2026-05-20): exact uploaded-flyer source edits should use the configured provider path instead of requiring a separate `OPENAI_API_KEY`.
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: reuse Hermes WhatsApp media ingress, Flyer project assets, config schema, OpenRouter env lookup, renderer QA, and manual-review fail-closed behavior. Net-new scope is only Flyer-specific source-edit provider resolution and OpenRouter dispatch.
  - [x] Write implementation plan: `docs/superpowers/plans/2026-05-20-flyer-source-edit-provider-config.md`.
  - [x] Get implementation plan reviewed by two parallel agents.
  - [x] Write design spec.
  - [x] Get design spec reviewed by two parallel agents.
  - [x] Build with TDD and focused verification.
  - [x] Open PR; no merge or deploy. PR #147: https://github.com/Trivenidigital/shift-agent/pull/147
  - Review: red run first produced 19 focused failures across source-edit preflight, renderer provider dispatch, workflow readiness, schema resolver, and script static wiring. Final verification passed: `tests/test_flyer_source_edit_preflight.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py tests/test_flyer_schemas.py` -> `119 passed`; `tests/test_flyer_generate_concepts.py tests/test_cf_router_flyer_routing.py -k "source_edit or preflight"` -> `7 passed, 93 deselected`; `tests/test_flyer_golden_scenarios_real_model.py tests/test_flyer_scripts_static.py` -> `34 passed, 1 skipped`; `tests/ -k "flyer and source_edit"` -> `53 passed, 4 skipped, 2094 deselected`; touched-file `py_compile` passed; `git diff --check` passed. No deploy performed.
- [x] Flyer Studio contract/lifecycle stabilization (2026-05-21): prevent F0065-class natural requests from poisoning locked business identity, remove project IDs/internal workflow from customer copy, and make initial ack lifecycle single-contract.
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: reused cf-router/Hermes sender identity, audit/log chokepoints, Flyer JSON state/customer profile store, existing visual QA/source-contract gates, and messaging substrate. Net-new scope is Flyer-specific fact contract, customer copy policy, and transcript-level regression coverage.
  - [x] Read deployed/current Flyer code, recent plans/PR notes, and relevant tests before coding.
  - [x] Write plan: `docs/superpowers/plans/2026-05-21-flyer-contract-lifecycle.md`.
  - [x] Get plan reviewed by two parallel agents and fold Critical/High findings.
  - [x] Write design: `docs/superpowers/specs/2026-05-21-flyer-contract-lifecycle-design.md`.
  - [x] Get design reviewed by two parallel agents and fold Critical/High findings.
  - [x] Build with transcript-level tests for F0065, profile authority, customer-copy leaks, duplicate initial ack, and self-eval tripwires.
  - [x] Open PR #152 and get two parallel PR reviews.
  - [x] Fold PR-review High findings: visual-QA failure fallback, truthful generic fallback copy, deterministic render title path, explicit business override bounds/prompt propagation, and SOURCE/NEW pending status phrasing.
  - Review: focused acceptance suite `324 passed, 117 skipped`; touched-file `py_compile` passed; `git diff --check` passed. No deploy performed.
- [x] Flyer Studio price/lifecycle contract repair (2026-05-23): fix F0085-class offer-price QA miss, category-price revision parsing, manual-review status check priority, and finalization-failure customer copy without moving judgment out of Hermes.
  - Drift-check tag: extends-Hermes
  - Hermes-first analysis: Hermes remains the judgment/classification brain. This slice only adds deterministic Flyer contracts: locked offer-price fact extraction, category price revision instruction, status phrase coverage, and approval/finalization fail-closed copy.
  - [x] Add red regressions for `all you can eat @ $25.99`, `Update Prices of any biryani to $22.99`, `Where is the update flyer?`, and final visual-QA failure after `APPROVE`.
  - [x] Implement minimal code fixes and verify focused Flyer facts/workflow/visual-QA/router suite.
  - Review: focused regression tests first failed, then passed; broader focused suite `198 passed`.

## Flyer Runtime Recovery/Delivery Fix - 2026-05-25T02:35:37Z

- [x] Merged PR #213 source-edit preflight parity before follow-up work.
- [x] Added regression coverage for successful source-edit queue rows not becoming recovery incidents.
- [x] Persisted concept-preview media delivery metadata after WhatsApp bridge success.
- [x] Verified focused recovery/delivery tests.
- [ ] Run broader Flyer regression slice.
- [ ] Triage stale manual queue without sending aged customer messages blindly.
- [ ] Create/review/merge/deploy PR.

### Review Notes

Root cause from F0095: source-edit config was unset at runtime and recovery classified a successful manual-queue row as provider failure because it keyed on detail text without checking `subprocess_rc`. Follow-up code fix prevents new false incidents and records concept-preview delivery ids after media send.

## Flyer Source-Edit Autonomous Repair - 2026-05-25T19:30Z

- [x] Reproduced F0097 post-deploy failure: source edit generated a preview but write_text_manifest rejected the long edit instruction as critical text facts do not fit.
- [x] Added regression for source-edit fit failures mapping to isual_qa_failed, not provider_timeout.
- [x] Added regression for source_edit_integrity_only manifests accepting long edit instructions without enforcing poster copy-fit gates.
- [x] Implemented the minimal classifier and manifest changes.
- [x] Verified focused generator/recovery/renderer slice.
- [ ] Merge/deploy, then repair F0097 customer-visible state using the existing good preview or a regenerated one.

Review: red tests first failed with provider_timeout and critical text facts do not fit; green targeted suite passed 27 passed, 70 deselected plus py_compile for the touched scripts.

## Flyer Brand-Asset Contamination Retry - 2026-05-25T20:00Z

- [x] Reproduced F0098: saved active logo asset for Lakshmi's Kitchen contained Desi Chowrastha branding, and the generated flyer copied the wrong brand.
- [x] Added regression that a missing required `business_name` visual-QA failure on a saved-logo request retries once with saved brand assets suppressed.
- [x] Implemented bounded retry and restored `FLYER_DISABLE_BRAND_ASSETS` after render.
- [x] Added regression and fix so a successful autonomous regeneration clears stale queued `manual_review` state.
- [ ] Merge/deploy and regenerate F0098 so the customer gets a clean preview.

Review: focused retry/source-edit generator slice passed `5 passed, 71 deselected`; touched-script `py_compile` and `git diff --check` passed.

## Flyer Recovery Observation Boundary - 2026-05-26T20:15Z

- Drift-check tag: extends-Hermes
- Hermes-first analysis: Hermes already provides audit logging, recovery worker bundles, no-live-send worker drafts, and customer-visible success resolution. This slice extends only the deterministic observation boundary so existing Hermes recovery machinery receives the right incidents.
- [x] Reproduced F0102 attribution failure: `cf_router_intercepted` had `subprocess_rc=0` but nonblank `ack_error=concept_generation_failed ... visual_qa_failed`, so recovery opened no incident.
- [x] Added regression for router success rows with nonblank `ack_error` becoming recovery incidents.
- [x] Added regression for stale `manual_review.status=queued` project state opening and queueing a repair bundle even with no failing audit row.
- [x] Implemented classifier and watchdog state-scan changes.
- [x] Merged the live PR #272 branch into this worktree before deploy testing so live SLA/parser/render fixes are preserved.
- [x] Verified focused recovery, self-eval, Flyer generation/QA, SLA/manual queue suites.

Review: recovery watchdog now observes both audit-level `ack_error` failures and durable stale manual-review state. This does not yet grant the worker direct production deploy authority; it queues bounded no-live-send repair work under the existing recovery worker contract.

## Flyer Semantic Brief Contract - 2026-05-26

- [x] Created isolated branch/worktree `codex/flyer-semantic-brief` at `C:\projects\SME-Agents-semantic-brief` from current `origin/main` (`604def2`).
- [x] Wrote architecture plan: `tasks/flyer-semantic-brief-contract-plan-2026-05-26.md`.
- [x] Review plan with two parallel agents and fold in recommendations.
- [x] Write design spec: `docs/superpowers/specs/2026-05-26-flyer-semantic-brief-contract-design.md`.
- [x] Review design spec with two parallel agents and fold in recommendations.
- [x] Build semantic QA/render slice with TDD.
- [x] Create PR #277 and review PR with two parallel agents.
- [x] Fold first PR-review blockers: install `flyer_semantic_brief` in production flat layout, preserve saved-logo retry exact-brand behavior, block known source-contract brands even when `forbidden_substrings` is empty, and reject conservative unlabeled wrong-brand mastheads while still allowing campaign-title flyers with stored contact anchors.
- [x] Re-run two parallel PR reviewers after blocker fixes.
- [x] Fold second PR-review blockers: exact-brand omission now requires profile-owned contact/address anchors, title-case org mastheads are blocked, stored phone/address phrasing no longer forces exact brand unless logo/brand is requested, and apostrophe-normalized account names are not misclassified as wrong brands.
- [x] Re-run final PR review after second blocker fixes.

Review: focused semantic brief gates passed after the first blocker-fix pass: `tests/test_flyer_visual_qa.py tests/test_flyer_facts.py tests/test_flyer_renderer.py` -> `122 passed`; `tests/test_flyer_generate_concepts.py tests/test_flyer_create_project.py` -> `47 passed`; `tests/test_flyer_scripts_static.py` -> `34 passed`; touched-file `py_compile` passed; `git diff --check` clean.

Second blocker-fix verification: `tests/test_flyer_visual_qa.py tests/test_flyer_facts.py tests/test_flyer_renderer.py` -> `125 passed`; `tests/test_flyer_generate_concepts.py tests/test_flyer_create_project.py` -> `47 passed`; `tests/test_flyer_scripts_static.py` -> `34 passed`; touched-script `py_compile` passed; `git diff --check` clean.

Final PR review: two parallel reviewers approved. Product/trust semantics confirmed account-owned anchors, title-case wrong-brand blocking, and saved-contact vs saved-brand separation. Runtime/deploy reviewer confirmed flat production import, smoke/static coverage, saved-logo retry compatibility, and regression coverage. No merge or deploy performed.

Post-review fix: added campaign-title exemption for the unlabeled org-suffix masthead heuristic so valid titles such as Restaurant Week Specials, Kitchen Essentials Sale, Cafe Style Biryani, and Biryani Bazaar pass when profile anchors are visible. Explicit identity labels and source-contract wrong-brand checks remain strict. Verification after fix: targeted 4-test semantic gate passed; `tests/test_flyer_facts.py tests/test_flyer_renderer.py tests/test_flyer_visual_qa.py tests/test_flyer_scripts_static.py` -> `160 passed`; `tests/test_flyer_generate_concepts.py tests/test_flyer_create_project.py` -> `47 passed`; touched-file `py_compile` passed; `git diff --check origin/main...HEAD` clean.

## Flyer Exact Identity Overlay - 2026-05-27

- [x] Investigated live F0104 failure after PR #277 deploy.
- [x] Verified QA correctly blocked visible generated typos: business rendered as `Lakshni's Kitchen` and phone as `+173298378841`.
- [x] Verified ARE opened incident `FRI20260527-6D61EBF9DC25`, queued a worker draft, but the draft proposed unsafe OCR-tolerance rather than checking the actual artifact.
- [x] Added deterministic exact identity/contact overlay for non-deterministic image-model previews, retaining the raw model image as background source.
- [x] Added regression proving real image-model previews are not the raw bitmap and have top/bottom exact-text overlay pixels.
- [x] Verified focused Flyer renderer/QA/generation suites.
- [x] Added follow-up extraction/QA fix so F0104-style requests keep chicken/goat price pairs, lock recurring schedules, and render schedule in the deterministic overlay.

Review: `tests/test_flyer_renderer.py` -> `67 passed`; `tests/test_flyer_visual_qa.py tests/test_flyer_generate_concepts.py` -> `50 passed`; full focused gate `tests/test_flyer_facts.py tests/test_flyer_renderer.py tests/test_flyer_visual_qa.py tests/test_flyer_scripts_static.py` -> `160 passed`; `tests/test_flyer_generate_concepts.py tests/test_flyer_create_project.py` -> `47 passed`; `py_compile src/agents/flyer/render.py` passed; `git diff --check` clean.

Follow-up verification after reviewer fixes: `tests/test_flyer_facts.py tests/test_flyer_renderer.py tests/test_flyer_visual_qa.py tests/test_flyer_scripts_static.py` -> `167 passed`; `tests/test_flyer_generate_concepts.py tests/test_flyer_create_project.py` -> `47 passed`; touched-file `py_compile` passed; `git diff --check` clean.

## Flyer Invented Operational Claims Guard - 2026-05-27

- [x] Visually inspected regenerated F0104 and found an unrequested `WhatsApp Delivery` claim despite passing locked-fact QA.
- [x] Added prompt instruction forbidding delivery/catering/payment/order-channel claims unless present in the render facts.
- [x] Added visual QA blocker for unrequested delivery/catering/payment operational claims.
- [x] Added focused regression coverage for requested vs unrequested delivery wording.

Review: `tests/test_flyer_facts.py tests/test_flyer_renderer.py tests/test_flyer_visual_qa.py tests/test_flyer_scripts_static.py` -> `169 passed`; `tests/test_flyer_generate_concepts.py tests/test_flyer_create_project.py` -> `47 passed`; touched-file `py_compile` passed; `git diff --check` clean.

## Flyer Recovery Operator-Action Resolution - 2026-05-27

- [x] Verified F0104 repaired preview was delivered, but old recovery incidents stayed `operator_action_required`.
- [x] Updated customer-visible repair resolution to close operator-action incidents whose only reason was worker completion/failure without visible success.
- [x] Added regression for asset delivery resolving a prior `worker_completed_no_customer_visible_success` incident.

Review: `tests/test_flyer_recovery.py tests/test_flyer_recovery_watchdog.py` -> `37 passed`; touched-file `py_compile` passed; `git diff --check` clean.

## Pre-PR #298 stuck-project customer_id / chat_id backfill - 2026-05-27

Filed during F0105 incident response (deploy `deploy-20260527-134933-affe3c0a` + recovery runbook). Not yet started.

- [ ] Survey: count projects with `customer_id=None` AND `customer_phone` populated in `/opt/shift-agent/state/flyer/projects.json`. Upper bound via `grep -c '"customer_id": null' /opt/shift-agent/state/flyer/projects.json`. Per-status breakdown for `manual_edit_required` / `awaiting_final_approval` / `revising_design`.
- [ ] Write deterministic backfill script: for each pre-PR-#298 project where `customer_id is None` AND `customer_phone` matches exactly one row in `customers.json`, write `customer_id` + (where deterministic) `primary_chat_id` back onto the project row. Idempotent. Safe-IO atomic write + flock.
- [ ] Dry-run mode that prints would-write rows without mutating.
- [ ] Regression test that asserts the backfill leaves a multi-match phone alone (refuses to guess).
- [ ] Run dry-run on prod state, review, then apply.
- [ ] Drift-tag: extends-Hermes. Plan/design before build per the standard cycle.

**Why this matters:** PR #298 (commit 87db715) added `customer_id` + `chat_id` persistence at CREATE time. Pre-PR-#298 projects still in `manual_edit_required` etc. carry `customer_id=None` / `chat_id=None`. The recovery engine's customer-origin heuristic suppresses follow-up customer acks for those projects (`missing_strong_customer_origin_evidence`), because it can't prove the chat binding from the project row alone. Surfaced concretely on F0105 during the 2026-05-27 incident: `customer_phone=+17329837841` matched CUST0001 unambiguously, but no `customer_id` was on the project row, so the recovery engine couldn't auto-ack the customer.

**Out of scope:** changing the recovery heuristic itself. The heuristic stays as a defense against future projects that genuinely lack origin evidence. This entry is the targeted backfill for pre-cutover stuck rows.

**Source incident:** F0105 (Lakshmi's Kitchen). Cutover commit: PR #298 (`87db715`). Affected project count: TBD (survey first).
