# Flyer Self-Evaluation Loop Implementation Plan

**Drift-check tag:** extends-Hermes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Keep the runtime self-learning boundary from AGENTS.md: production agents may update state/memory/reports only; code, SKILL, prompt, model, provider, and deploy-config changes require tests, review, PR, and deploy.

**Goal:** Add Flyer Studio self-evaluation v0.1: a deterministic, read-only report that turns recent Flyer project/audit evidence into incidents, eval-candidate suggestions, and operator brief signals.

**Architecture:** Reuse Hermes/Shift substrate for WhatsApp ingress, project JSON, decisions.log, audit, and operator brief rendering. Add one report-only CLI that reads existing state and outputs Markdown/JSON; wire the optional JSON into `tools/operator-brief.py`. No customer messages, manual queue writes, deploys, provider changes, or automatic code/prompt mutation.

**Tech Stack:** Python stdlib, existing Flyer JSON state shape (`FlyerProjectStore`), decisions.log NDJSON, optional `safe_io.atomic_write_text` for report `--out` when available on Linux.

---

## New Primitives Introduced

- `tools/flyer-self-evaluation.py`: offline/read-only self-evaluation CLI.
- Optional operator-brief input: `--flyer-evaluation-json`.
- JSON report contract with `incidents`, `eval_candidates`, `needs_srini`, and `summary`.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress/outbound | yes - Hermes gateway, bridge, cf-router | Use existing logs/state only; do not send messages. |
| Durable audit | yes - decisions.log discriminated-union convention | Read existing audit lines; do not add a writer in v0.1. |
| Flyer project state | yes - JSON state under `/opt/shift-agent/state/flyer/projects.json` | Read existing project store; no new table/state file. |
| Operator control tower | yes - `tools/operator-brief.py` and runbook | Add an optional report section. |
| Golden/eval substrate | yes - `tests/test_flyer_golden_scenarios.py` + live-shape fixtures | Report suggested fixture categories; do not auto-write fixtures. |
| Hermes self-evolution | yes - Hermes docs describe skills/memory/self-improvement; Self-Evolution Kit exists | Use offline/report-only recommendations; no production hot-mutation. |
| Awesome Hermes ecosystem | no drop-in Flyer-quality evaluator found | Build narrow repo-local report on top of Hermes state. |

Sources checked: official Hermes docs and skills pages, NousResearch `hermes-agent-self-evolution` plan, existing repo Catering self-learning plan/spec, and current Flyer production-readiness backlog. Verdict: Hermes supplies substrate and offline evolution concepts; Flyer-specific incident detection and eval-candidate mapping are net-new application policy.

## Per-Step Hermes Checklist

| Step | Owner | Decision |
|---|---|---|
| Read projects.json | `[Hermes]` | Existing JSON state; no new store. |
| Read decisions.log | `[Hermes]` | Existing audit substrate; skip malformed rows. |
| Classify known Flyer failure classes | `[net-new]` | Flyer-specific incident rules. |
| Suggest eval/backlog candidates | `[net-new]` | Flyer-specific mapping from incident to PR/eval category. |
| Render JSON/Markdown report | `[net-new]` | Report shape is local ops glue. |
| Include in operator brief | `[Hermes]` | Extend existing renderer; no new delivery channel. |

Net-new: 3 of 6 steps. This avoids creating a second learning substrate.

## Drift Checks Performed

- `src/platform/schemas.py`: `FlyerProjectStore`, `FlyerProject`, `FlyerManualReview`, `FlyerSourceContract`, `FlyerVisualQAReport`, and existing Flyer audit variants.
- `src/platform/safe_io.py`: `atomic_write_text` is the production write pattern; v0.1 imports it opportunistically for `--out` and falls back only when running on Windows without `fcntl`.
- `tools/operator-brief.py`: existing optional JSON inputs for Flyer autonomous train and fleet normalization.
- `docs/superpowers/plans/2026-05-14-catering-100-autonomy-self-learning-plan.md` and `docs/superpowers/specs/2026-05-14-catering-self-learning-rails-design.md`: closest safe self-learning pattern.
- `tests/test_operator_brief.py`: current test style for optional report sections.

## Incident Rules In V0.1

1. **Manual source-edit stale:** `manual_edit_required` project with `manual_review.reason_code="source_edit_provider_unavailable"` and queued age over threshold.
2. **Customer copy internal leak:** recent audit row with customer-message-like text containing internal terms such as `queued project`, `Requested edit:`, `Original customer request`, `operator`, `provider`, or `reason_code`.
3. **Source contract missing:** exact/source-edit-looking project with reference media and source-preservation language but no extracted source contract.
4. **Source QA missing:** project has a source contract and generated/final/delivered assets but no QA report tied to source-aware verification.
5. **Repeated check-ins:** decisions.log contains repeated status/check-in messages for the same project or sender above threshold.
6. **Generation stuck:** active generation/finalizing status older than threshold.

## Explicit Non-Goals

- No OpenRouter/provider behavior changes.
- No customer WhatsApp send.
- No manual queue close/complete/break-glass.
- No project/customer/payment/campaign mutation.
- No generated fixture file writes in v0.1.
- No automatic prompt/SKILL/model/code evolution.

## Implementation Tasks

### Task 1: Tests For Self-Evaluation Core

**Files:**
- Add: `tests/test_flyer_self_evaluation.py`

- [x] Write tests for stale manual source-edit, internal copy leak, missing source contract, missing source QA, repeated check-ins, clean-state no incident, JSON/Markdown rendering, and static no-live-mutation guards.
- [x] Pin customer-copy detection boundary: decisions.log detection only sees rows with outbound text fields; metadata-only cf-router sends require the optional source-code scan (`--scan-source-copy`) or future outbound-body audit.
- [x] Run `python -m pytest tests/test_flyer_self_evaluation.py -q`; expected RED because `tools/flyer-self-evaluation.py` does not exist.

### Task 2: Implement Report CLI

**Files:**
- Add: `tools/flyer-self-evaluation.py`

- [x] Implement tolerant project/log loading.
- [x] Implement incident detection functions.
- [x] Implement optional targeted source-code scan for customer-ack copy leaks; scan function bodies only, not whole files.
- [x] Implement JSON and Markdown rendering.
- [x] Implement `--projects`, `--decisions-log`, `--now`, `--format`, `--out`, and threshold flags.
- [x] Use `safe_io.atomic_write_text` for `--out` when importable; fall back to direct write only for local Windows import failure.
- [x] Re-run `python -m pytest tests/test_flyer_self_evaluation.py -q`; expected GREEN.

### Task 3: Operator Brief Integration

**Files:**
- Modify: `tools/operator-brief.py`
- Modify: `tests/test_operator_brief.py`
- Modify: `docs/runbooks/operator-ops-brief.md`

- [x] Add optional `--flyer-evaluation-json`.
- [x] Summarize status, incident counts, top incidents, and Needs Srini lines.
- [x] Add focused operator-brief test.
- [x] Update runbook command examples.

### Task 4: Backlog And Verification

**Files:**
- Modify: `tasks/todo.md`
- Add: `tasks/.hermes-check-receipts/flyer-self-evaluation-loop.json`

- [x] Record backlog entry.
- [x] Run focused tests, py_compile, CLI smoke, and `git diff --check`.

## Completion Criteria

- Report-only self-evaluation CLI exists and is tested.
- Operator brief can include Flyer self-evaluation JSON.
- No live production mutation paths are introduced.
- No deploy performed.
- PR summary explicitly says self-learning remains report/eval/backlog only; no production self-modification enabled.
