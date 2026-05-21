**Drift-check tag:** extends-Hermes

# Flyer Replay Stability Audit Plan - 2026-05-21

## Goal

Stop recurring Flyer Studio silent failures by adding deterministic replay coverage for real WhatsApp-style incidents, consolidating customer-copy policy/lint, and extending read-only self-evaluation/operator brief signals. Keep this PR offline and non-mutating: no deploy, no WhatsApp sends, no VPS/customer/payment/manual-queue mutation, and no provider routing changes.

## Drift-Check

Existing replay/self-eval/golden/test infrastructure found before scoping:

- `tests/test_flyer_golden_scenarios.py` already runs deterministic create-project scenarios and delegates some routing axes to owner tests.
- `tests/fixtures/flyer_golden/live_customer_message_shapes.json` already stores redacted live reply/create-project shapes for F-series flows.
- `tests/_dispatcher_replay.py` already provides a JSONL dispatcher replay substrate with fixture loading, replay execution, dispatcher SKILL drift hashing, and mocked LLM caller variants.
- `src/platform/scripts/extract-replay-fixtures` already extracts redacted dispatcher replay fixtures from decisions logs.
- `tests/test_flyer_self_evaluation.py` already covers customer-copy internal leaks, duplicate acks, malformed business name facts, source-contract gaps, latest-request-not-reflected, repeated check-ins, and preview-approved-final-QA-failed.
- `tools/flyer-self-evaluation.py` is a read-only incident reporter with JSON/Markdown output and source-copy scanning.
- `tools/operator-brief.py` already groups Flyer self-evaluation incidents and separates active customer risk from historical/audit-only findings.
- `tests/test_cf_router_flyer_routing.py`, `tests/test_flyer_create_project.py`, `tests/test_flyer_project_isolation.py`, `tests/test_flyer_workflow.py`, `tests/test_flyer_visual_qa.py`, `tests/test_flyer_customer_lifecycle_copy.py`, and PR #149's `tests/test_flyer_source_edit_sla_watchdog.py` cover adjacent routing, creation, workflow, QA, copy, and SLA surfaces.
- `src/plugins/cf-router/actions.py` and `hooks.py` are the deployed deterministic Flyer routing/customer-copy boundary.
- `src/agents/flyer/facts.py`, `render.py`, and `visual_qa.py` already carry the business_name/campaign_title split, locked facts, source-contract facts, renderer fact usage, and visual/OCR QA.

Conclusion: do not build a second dispatcher replay substrate or a separate workflow engine. Reuse `tests/_dispatcher_replay.py` when the question is dispatch. Add only a Flyer hook replay adapter for cf-router branch-order, state, customer-output, and QA/fact-contract checks that dispatcher replay cannot observe. Extract existing customer-copy policy constants into a shared helper instead of inventing a new lint system.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress and sender identity | Hermes gateway/cf-router + `identify-sender` already own this | Reuse event-shaped offline fixtures; do not add live ingress code. |
| Media cache and source files | Hermes image cache and Flyer managed asset copy already own this | Use mocked local fixture media only when needed. |
| Audit and JSON state | Hermes/Flyer JSON state, `safe_io`, decisions.log, and read-only self-eval exist | Reuse fixture JSON/NDJSON; no new storage substrate. |
| LLM/vision/OCR gateway | Hermes/OpenRouter/OpenAI paths exist | Do not call providers; replay uses mocked/offline fixtures only. |
| Route/state lifecycle | Flyer owns product-specific routing, project lifecycle, locked facts, source contracts | Add deterministic replay assertions at Flyer boundary. |
| Customer copy policy | Flyer owns deterministic customer-facing wording constraints | Add shared helper/constants used by self-eval and tests. |
| Operator-visible incidents | Flyer self-eval/operator brief own reporting | Extend existing incidents/groups; no alert timer duplication. |
| Manual queue SLA | PR #149 watchdog merged/deployed (`8986597e`, deploy `deploy-20260521-043544-8986597e.tgz`) | Report active stale rows in self-eval only; do not add another timer/alert. |

Hermes ecosystem check: checked the Hermes Skills Hub (`hermes-agent.nousresearch.com/docs/skills`) for replay/copy/audit/workflow capabilities. It has broad skills infrastructure and productivity/OCR capabilities, but no Flyer-specific deterministic customer-copy policy or incident replay harness. Checked `0xNyk/awesome-hermes-agent`; it catalogs Hermes resources, messaging gateway, plugins, skills, and self-evolution tooling, but no turnkey Flyer Studio replay/customer-copy guardrail. Verdict: reuse Hermes substrate; build the narrow Flyer-owned test/reporting layer.

## Scope

- Add a deterministic incident replay adapter around existing replay/fixture patterns, with at least 8 F0061/F0063/F0065-style scenarios.
- Consolidate existing Flyer customer-copy policy/lint into one shared helper used by self-eval and tests, with no runtime wording change.
- Extend self-eval/operator brief only where reporting gaps remain after replay tests.
- Fix only high-confidence failures that replay tests expose, scoped to copy guardrails, route guardrails, fact-contract sanity checks, and reporting.

Out of scope:

- Provider/model routing.
- Dashboard UI except tiny read-only reporting hooks if unavoidable.
- Source-edit operational enablement.
- Paid model smoke.
- Broad refactors.
- Deploy, VPS mutation, customer state mutation, payment/manual-queue mutation, or WhatsApp sends.

## Work Plan

- [x] Plan review A: Hermes-first/scope reviewer checks whether replay/copy helper duplicates substrate.
- [x] Plan review B: production/runtime reviewer checks silent-failure coverage and PR #149 boundary.
- [x] Fold Critical/High/Important plan findings.
- [ ] Write design doc with failure taxonomy, replay fixture shape, self-eval integration, operator brief integration, and test strategy.
- [ ] Design review A: structural/code-path trace from WhatsApp input to customer output.
- [ ] Design review B: statistical/fixture validity and brittleness review.
- [ ] Fold Critical/High/Important design findings.
- [ ] Add failing tests for the Flyer hook replay adapter and shared customer-copy helper.
- [ ] Add replay scenarios from this gap table:
  - source flyer exact edit + co-owner reply: existing live fixture/test covers reply shape; residual gap is branch-order replay through `pre_gateway_dispatch` with seeded pending state.
  - `any update?` on queued edit: existing reply/status tests cover wording; residual gap is outbound-copy capture plus no create/revise assertions.
  - new evening-snacks flyer while old project exists: existing routing/create-project tests cover part of #150/#152; residual gap is multi-message active-project replay with outbound capture.
  - APPROVE after preview where final QA fails: existing self-eval covers incident shape; residual gap is customer-output capture and active-risk classification.
  - vague `create flyer`: existing route tests cover classification; residual gap is no mutation/send beyond safe clarification.
  - small revision like `make it red`: existing routing/revision tests cover decision; residual gap is no new project and safe outbound copy.
  - status check that must not create/revise project: existing route tests cover decision; residual gap is full branch-order replay with unmocked subprocess/write/send surfaces hard-failing.
  - F0065 business/campaign split: existing create-project tests cover fact state; residual gap is prompt/manifest/QA evidence that campaign text does not become profile business identity.
- [ ] Add shared copy policy helper with banned terms, static scan functions, duplicate ack markers, outbound scan helpers, and static source literal scan helpers.
- [ ] Refactor `tools/flyer-self-evaluation.py` to import/use the helper without changing incident names: `customer_copy_internal_leak`, `customer_copy_static_internal_leak`, and `duplicate_initial_ack`.
- [ ] Refactor tests to import the same constants instead of duplicating banned-term literals.
- [ ] Extend self-eval/reporting only where replay fixtures expose missing active customer-risk rows; do not add new stale source-edit SLA reporting unless a concrete missing gap remains after PR #149.
- [ ] Extend operator brief grouping only if a new self-eval incident type needs grouping to avoid historical F-series spam.
- [ ] Enforce replay non-mutation contract: monkeypatch bridge sends, media sends, SSH/scp/subprocess, `/opt/shift-agent` writes, payment/manual-queue mutations, and unexpected file writes so any unmocked live path fails the replay tests.
- [ ] Require replay capture of every would-be customer-facing body before send (`send_flyer_text`, `bridge_post`, `bridge_send_media`, and any registered equivalent).
- [ ] Require every self-eval incident type touched by this work to emit `evidence_details.active_customer_risk` as true/false, with terminal delivered/completed/closed rows audit-only unless an active follow-up loop remains.
- [ ] Run focused verification:
  - `python -m pytest tests/test_flyer_incident_replay.py tests/test_flyer_self_evaluation.py tests/test_operator_brief.py tests/test_flyer_customer_lifecycle_copy.py tests/test_cf_router_flyer_routing.py tests/test_flyer_create_project.py tests/test_flyer_workflow.py tests/test_flyer_visual_qa.py tests/test_flyer_source_edit_sla_watchdog.py -q`
  - `python -m py_compile tools/flyer-self-evaluation.py tools/operator-brief.py <new helper>`
  - Flyer self-evaluation JSON and Markdown CLI smoke using replay fixtures
  - `git diff --check`
- [ ] Before PR: request two final reviewers:
  - live-behavior reviewer: trace each replay fixture end-to-end,
  - Hermes-first/runtime reviewer: no duplicated substrate, no mutation, no false confidence.
- [ ] Fold final review findings and open PR.

## Acceptance Mapping

- At least 8 incident replay fixtures: covered by `tests/test_flyer_incident_replay.py` and fixture JSON.
- F0065 regressions fail tests: profile business locked fact and campaign title assertions in replay/create-project tests.
- Initial customer ack internal leaks fail tests: shared customer-copy policy scanned by tests and self-eval.
- Profile business_name not overwritten by campaign request: existing create-project test plus replay fixture.
- Self-eval active customer-risk rows: fixture-driven bad states must appear as active risk.
- Operator brief grouping: grouped by active/historical risk without spamming historical rows.
- Existing #150/#151/#152 behavior: focused routing/create/workflow/copy/self-eval suites remain green.
- No production mutation/deploy: static guard plus no live endpoints/SSH in implementation.

## Plan Review Findings Folded

- Reviewer A found existing dispatcher replay infrastructure in `tests/_dispatcher_replay.py` and `src/platform/scripts/extract-replay-fixtures`; the plan now reuses it for dispatch questions and scopes new work to a Flyer hook adapter only where branch-order, state, customer output, and QA evidence are not observable.
- Reviewer A noted PR #149 already owns stale source-edit SLA alerting/reporting; this plan now forbids duplicate stale-SLA work unless a concrete missing gap remains.
- Reviewer B found customer-facing failure coverage must capture bodies before live send; replay tests now must intercept registered customer-output surfaces, not just source logs.
- Reviewer B found active-vs-historical risk needed explicit typing; touched self-eval incidents must include `evidence_details.active_customer_risk` true/false.
- Reviewer B required hard replay side-effect guards; the plan now requires send/subprocess/SSH/scp/live-write/manual-queue mutations to fail tests unless explicitly mocked.
- Reviewer B found F0061 and F0065 need deeper coverage than fact checks; the gap table now calls out branch-order replay and prompt/manifest/QA evidence checks.
