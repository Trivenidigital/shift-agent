**Drift-check tag:** extends-Hermes

# Flyer Studio Hermes Autonomous Repair Loop Plan — 2026-05-27

## Goal

Reduce Flyer Studio manual-edit routing by adding a Hermes-first autonomous repair loop for QA-failed generated flyers. Manual review should remain a last-resort path for source-preserving edits, unsafe trust failures, missing customer facts, or repeated repair exhaustion.

## Current Finding

F0105 is the representative failure. The render dependency issue was fixed and the preview was generated, but QA failed on visible content. The system then stayed in `manual_edit_required` instead of attempting a bounded semantic repair. That is a product architecture gap: deterministic QA can detect the failure, but no Hermes-owned repair planner converts the QA report into a corrected generation attempt.

The current working branch also contains partial recovery-worker work aimed at Codex/Claude repair bundles. That is useful for code incidents, but it is not the customer flyer repair loop. Customer flyer repair must use Hermes/LLM semantic reasoning, not a developer agent.

## Drift Check

Read before plan:

- `src/agents/flyer/skills/flyer_generation/SKILL.md` — already states controlled direct generation and revision-policy expectations.
- `src/agents/flyer/scripts/generate-flyer-concepts` — generation is currently one-shot: render once, write concept state, or fail into manual review upstream.
- `src/agents/flyer/render.py` — deterministic text-manifest QA exists; visual/manifest blockers are already structured enough to become repair inputs.
- `src/agents/flyer/recovery.py` and `src/agents/flyer/scripts/flyer-recovery-watchdog` — recovery currently observes incidents / acks / bundles; partial dirty work adds Codex/Claude worker drafts but not Hermes customer repair.
- `src/platform/schemas.py` — recovery and Flyer schemas use Pydantic v2, `Literal[...]`, JSON state, and NDJSON audit variants.
- `tests/test_flyer_renderer.py`, `tests/test_flyer_recovery_watchdog.py`, `tests/test_flyer_create_project.py` — existing test patterns for render QA, recovery, and project creation.

## Hermes-First Analysis

Hermes docs checked: skills system, bundled skills catalog, Hermes Agent skill, and skill creation guidance. Hermes has the right substrate for this work: skills as procedural brain, WhatsApp gateway, persistent state/memory, tool execution, scheduled automations, multi-agent/subagent operation, and provider-agnostic LLM calls. No official skill was found that is a ready-made restaurant flyer semantic-repair contract. So this plan extends Hermes by adding Flyer-specific skill instructions and deterministic executors.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress and sender identity | yes — existing Hermes gateway + project `cf-router` plugin | use existing |
| Semantic intent / brief interpretation | yes — Hermes LLM + skills substrate | use Hermes skill instructions, add Flyer-specific semantic contract |
| QA report interpretation / repair strategy | yes — Hermes LLM + skills substrate, no flyer-specific built-in skill | extend Hermes with Flyer-specific repair prompt/contract |
| Deterministic hard-contract validation | no generic Hermes skill; existing repo code owns it | use existing Python QA and add small repair classifier/executor |
| Image generation execution | existing `render.py` OpenRouter/OpenAI image paths | use existing renderer, do not add a parallel image stack |
| Developer incident repair / PR drafting | yes — Hermes/Codex/Claude agent loops | keep separate from customer flyer repair |

Awesome-Hermes ecosystem check verdict: no off-the-shelf flyer semantic-repair skill replaces this domain-specific contract; use Hermes skills as the brain and keep net-new code to orchestration, state, audit, and deterministic gates.

## Product Contract

The Flyer Studio repair loop must behave like this:

1. Customer sends free-form request.
2. Hermes skill/classifier produces or refines a semantic flyer brief.
3. Renderer generates a preview from controlled facts.
4. Deterministic QA validates hard facts and visible-copy safety.
5. If QA fails with a repairable class, Hermes receives the brief + QA blockers + extracted text and returns a repair instruction.
6. The generator retries with the repair instruction under a bounded attempt budget.
7. If a repaired preview passes, the normal preview delivery path continues.
8. If repairs exhaust or a trust-risk class appears, route to manual review with a clear reason.

## Repairability Policy

Auto-repair candidates:

- Duplicate visible item lines.
- Missing visible item/name/price facts when the facts exist in the project brief.
- Extra generic title/footer leakage such as repeating “Flyer” when not requested.
- Layout/text-manifest mismatch that can be corrected by stricter prompt wording.
- Model-output visual QA failure where the generated file exists and no protected fact is wrong.

Manual or hard-stop candidates:

- Wrong saved business identity.
- Wrong phone/address/contact.
- Wrong price after the fact was explicitly supplied.
- Missing required customer facts that do not exist in state or brief.
- Unauthorized external brand/source artwork.
- Source-preserving edit provider unavailable.
- Dependency/runtime failures.
- Repair attempts exhausted.

## Reviewer Findings Applied

Plan review found two blocking corrections:

- Hermes boundary was too aspirational. The revised slice adds an explicit Hermes repair contract artifact first; Python may only execute a returned contract, not invent repair strategy.
- Retry state must be durable. `FlyerProject` has `extra="forbid"` and no `manual_review` field, so the implementation must add a schema-backed repair ledger before retrying.

Additional corrections:

- Do not silently normalize/remove leaked text inside `render.py`; QA should flag it and Hermes should plan the repair.
- Do not use `regulated_send_*` as the post-deploy proof for Flyer preview safety; concept-preview sends use `bridge_send_media` / `bridge_post` through `send_flyer_concept_previews`.
- Add a no-live-send verification harness before any F0105 replay.
- Split the first PR down to the load-bearing repair path: semantic contract, durable retry ledger, one bounded generation retry, audit, smoke/import gates, and F0105-style tests.

## Revised First-PR Scope

This PR will not build the full long-term repair platform. It ships one vertical slice:

1. A Hermes-owned repair contract in the Flyer generation skill: inputs are semantic brief, QA blockers, extracted text, and hard-contract flags; output is one of `regenerate_with_instruction`, `manual_required`, or `ask_customer`.
2. A small schema-backed project repair ledger, for example `repair_attempts: list[FlyerRepairAttempt]`, with `attempt_id`, `project_version`, `qa_blocker_hash`, `repair_instruction_hash`, `status`, timestamps, and generated asset ids.
3. Deterministic repairability classification only for routing hard stops vs Hermes-plan-eligible failures. It does not decide the creative fix.
4. A bounded retry in `generate-flyer-concepts`: write a pre-attempt ledger/audit row, run generation with the Hermes repair instruction appended to the prompt, validate QA, then mark success/exhaustion.
5. No customer-visible send changes. Preview delivery remains in cf-router after generation succeeds.
6. A no-live-send test/dry-run harness that verifies the generation retry path without calling `send_flyer_concept_previews` or bridge endpoints.

Deferred:

- Source-preserving edit autorepair.
- Codex/Claude worker promotion.
- Broad repair taxonomy expansion beyond F0105-style generated-poster QA failures.
- Direct customer status follow-ups for repair in progress.

## Implementation Plan

### Phase 1 — Plan Review

- [ ] Dispatch two parallel plan reviewers:
  - Reviewer A: Hermes-first / product-scope reviewer. Attack vector: are we using Hermes as brain and avoiding custom orchestration bloat?
  - Reviewer B: runtime-safety / deploy-shape reviewer. Attack vector: will this route customer-visible sends safely and avoid silent manual-review loops?
- [ ] Apply any BLOCKER/HIGH findings before design.

### Phase 2 — Design

- [ ] Write a design doc under `tasks/` with:
  - semantic brief contract;
  - repairability taxonomy;
  - state transitions;
  - retry budget;
  - audit rows;
  - no-live-send / send-gate behavior;
  - F0105 regression path.
- [ ] Dispatch two parallel design reviewers:
  - Reviewer A: Hermes-first semantic design.
  - Reviewer B: state/audit/runtime safety.
- [ ] Apply findings before build.

### Phase 3 — Build

Target files:

- `src/agents/flyer/skills/flyer_generation/SKILL.md`
  - Add Hermes semantic repair policy and the exact structured contract.
  - Make clear that Hermes chooses creative repair strategy; Python only enforces hard stops and executes the returned instruction.
- `src/agents/flyer/recovery.py`
  - Add pure helpers for repairability classification from QA blockers: `hermes_plan_eligible`, `hard_stop`, or `manual_required`.
  - Do not add Codex/Claude worker behavior to this customer-facing path.
- `src/agents/flyer/scripts/generate-flyer-concepts`
  - Add bounded retry loop around concept generation for Hermes-plan-eligible QA failures.
  - Before every retry, persist a schema-backed repair attempt and append `flyer_autorepair_attempted`.
  - On success, attach only QA-passing assets and append `flyer_autorepair_succeeded`.
  - On exhaustion or hard stop, leave the project in the existing failure/manual-review path and append `flyer_autorepair_exhausted` or `flyer_autorepair_skipped`.
  - Do not send customer-visible assets from this script; preserve existing send path.
- `src/agents/flyer/render.py`
  - Add a repair-instruction prompt section consumed by `_image_prompt`.
  - Do not silently normalize leaked text. Existing QA remains responsible for flagging output defects; Hermes repair instruction tells the next generation what to avoid.
- `src/platform/schemas.py`
  - Add `FlyerRepairAttempt` and `repair_attempts` on `FlyerProject`.
  - Add audit entries for `flyer_autorepair_attempted`, `flyer_autorepair_succeeded`, `flyer_autorepair_exhausted`, and `flyer_autorepair_skipped`.
  - Each audit row includes `attempt_id`, `project_id`, `project_version`, `qa_blocker_hash`, `repair_instruction_hash`, and `mode`.
  - Add config knobs under `flyer.recovery`: `auto_repair_enabled`, `max_auto_repair_attempts`.
- `src/agents/shift/scripts/shift-agent-smoke-test.sh`
  - Add smoke/static verification that the deployed `generate-flyer-concepts` flat-module import path can reach the autorepair helpers and render prompt repair section.
- Tests:
  - Add F0105-style regression with duplicate/missing visible item blocker classified as repairable.
  - Add hard-stop tests for wrong business/contact/price classes.
  - Add retry-budget test: repairable first failure, pass on second render, no manual review.
  - Add exhaustion test: repeated repairable failure becomes manual review with `visual_qa_failed`.
  - Add schema/audit tests for new log variants.
  - Add no-live-send regression proving the retry test never calls `send_flyer_concept_previews`, `bridge_send_media`, or `bridge_post`.

### Phase 4 — PR Review

- [ ] Create PR.
- [ ] Dispatch three parallel reviewers:
  - Hermes-first/product reviewer.
  - Runtime-state/audit/send-safety reviewer.
  - Test/QA/F0105 regression reviewer.
- [ ] Fix findings and rerun targeted + broader verification.

### Phase 5 — Merge and Deploy

- [ ] Squash merge after review green.
- [ ] Post-merge verify on actual `origin/main`.
- [ ] Deploy by tarball, not git checkout on VPS.
- [ ] Run deploy smoke.
- [ ] Post-deploy health:
  - no new `flyer_autorepair_*` exhausted/skipped rows except expected controlled tests;
  - no preview delivery failures in `cf_router_intercepted` `ack_error` details;
  - no `flyer_delivery_failed` rows for final sends;
  - no new recovery failure/suppression rows;
  - autorepair imports work under deployed-flat module shape.
- [ ] Run an F0105-style no-live-send dry run before any customer-visible send.

## Non-Goals

- Do not build a separate custom “brain” outside Hermes.
- Do not allow Codex/Claude worker drafts to send customer flyers.
- Do not bypass customer approval or existing final send chokepoint.
- Do not auto-repair source-preserving edits in this slice.
- Do not change payment, quota, or plan behavior.

## Success Criteria

- A QA failure like F0105 gets at least one bounded autonomous repair attempt before manual queue.
- Wrong business/contact/price still fail closed.
- Customer-visible sends still happen only through existing audited send paths.
- Manual review remains available but is no longer the first stop for repairable model-output defects.
