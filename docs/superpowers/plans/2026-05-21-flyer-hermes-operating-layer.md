# Flyer Hermes Operating Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Drift-check tag:** `extends-Hermes` — extend the existing read-only Flyer self-evaluation and operator brief substrate. Do not add a parallel Flyer report CLI, task database, queue, social publisher, or background worker.

**Goal:** Add the first safe Hermes-update adoption slice for Flyer Studio: an `operating_layer` block inside the existing self-evaluation report that tells operators which Hermes-backed capabilities are ready, blocked, or deferred, while filing every broader option into backlog.

**Architecture:** Reuse `tools/flyer-self-evaluation.py` as the one Flyer report CLI and `tools/operator-brief.py --flyer-evaluation-json` as the one operator brief ingress. Add a small pure helper module for deterministic operating-layer policy only. Inputs remain explicit JSON fixtures; the tool does not probe live Hermes memory, SSH, X/Grok, Codex, or video APIs.

**Tech Stack:** Python 3, Pydantic v2 for the optional input fixture, existing Flyer JSON/self-eval patterns, pytest.

---

## Plan Review Folds

| Finding | Fold |
|---|---|
| Parallel report CLI duplicates self-eval substrate | Removed standalone `tools/flyer-hermes-operating-layer.py`; implementation extends `tools/flyer-self-evaluation.py` only |
| Operator brief should reuse `--flyer-evaluation-json` | No new operator-brief input; summarize `operating_layer` from existing Flyer evaluation JSON |
| Missing per-step Hermes-first evidence | Added step checklist below |
| Backlog filing too broad as a new island | Add a scoped subsection under existing `Active - Hermes Flyer Studio Agent` instead of creating a new top-level island |
| Readiness could false-green | Use schema-shaped operating-layer input and conservative checks for active brand assets, delivered/completed campaigns, QA evidence, rollout blockers, and source-edit smoke state |
| Backlog completeness not testable | Add traceability matrix and tests over `deferred_backlog` keys |
| No-live-dependency under-tested | Add static tests banning SSH/subprocess/network/live-path probes in the new helper |
| Platform truthfulness missing | Include Instagram Story/package-truthfulness as its own backlog key |
| Source-edit posture ambiguous | Resolve exactly one rollout context: self-eval rollout when present, otherwise input rollout; conflicts become conservative yellow |
| Brand memory could false-green globally | Rename status to `ready_for_at_least_one_customer`, include denominator/coverage, and require QA timestamp |

## Drift-Check Findings

| Area | Existing primitive found | Residual gap |
|---|---|---|
| Flyer self-evaluation | `tools/flyer-self-evaluation.py` renders read-only JSON/Markdown, active-risk incidents, rollout readiness | No Hermes operating-layer opportunity/status block |
| Flyer rollout readiness | `src/agents/flyer/rollout_readiness.py` owns rollout verdict and posture | Keep customer-readiness gate as the rollout blocker; operating-layer report must not supersede it |
| Operator brief | `tools/operator-brief.py` already summarizes Flyer self-eval via `--flyer-evaluation-json` | Add operating-layer summary inside that section |
| Customer profile/project state | `FlyerCustomerProfile`, `FlyerProject`, assets, usage, QA, and decisions log exist | No concise brand/campaign-memory readiness signal |
| Customer-copy policy | `src/agents/flyer/customer_copy_policy.py` centralizes banned terms | Do not duplicate copy policy here |
| Platform package truthfulness | Open backlog item says Instagram Story claims are not yet true story-safe | Carry into operating-layer backlog as a near-term customer-readiness blocker |
| Source-edit | Provider config exists but production remains manual_review until smoke proof | Keep as yellow/deferred; no enablement |

## Hermes-First Analysis

| Step | Hermes or net-new? | Decision |
|---|---|---|
| Store/retrieve sessions and memory | `[Hermes]` | Use Hermes memory/session search later; this PR only identifies Flyer memory readiness |
| Background tasks / Kanban / worker execution | `[Hermes]` | Backlog adoption; do not create queue |
| Codex CLI/runtime self-improvement | `[Hermes]` | Backlog offline/staging loop only; production code changes stay PR/deploy-gated |
| X/Grok search/posting | `[Hermes]` | Backlog explicit customer opt-in publishing; no posting now |
| Native video generation | `[Hermes]` | Backlog video conversion; no generation now |
| Read existing Flyer self-eval/project inputs | `[Hermes]` substrate + `[net-new]` Flyer policy | Reuse JSON/audit substrate; add small Flyer-specific interpretation |
| Compute brand/campaign-memory readiness | `[net-new]` | Flyer product policy: active assets, delivered campaigns, QA evidence, language/category |
| Render self-eval JSON/Markdown | `[Hermes]` pattern | Extend existing CLI only |
| Operator brief summary | `[Hermes]` pattern | Extend existing Flyer self-eval summarizer only |
| File backlog | `[net-new]` product planning | Add scoped bullets under existing Flyer/Hermes backlog |

Awesome-Hermes-Agent ecosystem check: broad Hermes memory/tool/agent orchestration exists, but no Flyer Studio brand-memory/readiness policy was found. Verdict: extend existing Flyer reporting with product-specific interpretation; do not duplicate Hermes primitives.

## Hermes Update Traceability Matrix

| Source option | Report key | Backlog key | Current posture |
|---|---|---|---|
| Improved memory/session search | `brand_memory` | `persistent_brand_memory_readiness_signal`, `persistent_brand_memory_activation` | implement read-only readiness signal now; activation remains backlog |
| Ask what we worked on / campaign history | `campaign_history` | `session_search_campaign_history` | backlog |
| Background tasks | `background_jobs` | `background_render_qa_exports` | backlog |
| xAI OAuth/Grok orchestration | `provider_posture` | `xai_grok_provider_posture` | backlog |
| X posting/fetching | `social_x` | `x_search_fetching`, `x_social_posting_approval` | backlog |
| Codex CLI | `codex_self_improvement` | `codex_offline_self_improvement` | backlog |
| Native AI videos | `video` | `native_video_conversion` | backlog |
| Auto Kanban tasks | `kanban` | `auto_kanban_operator_work` | backlog |
| Multi-format exports | `exports` | `multi_format_export_truthfulness` | backlog |
| Autonomous campaigns | `campaign_orchestration` | `autonomous_campaigns_with_approval` | backlog |
| Analytics + memory moat | `analytics` | `campaign_analytics_memory` | backlog |
| Publishing engine | `publishing` | `publishing_engine_approval_gates` | backlog |
| AI marketing OS | `strategy` | `marketing_os_long_term` | backlog |
| Source-edit smoke proof | `source_edit` | `source_edit_smoke_proof` | backlog/blocker |
| Deterministic/hybrid final rendering | `hybrid_rendering` | `hybrid_layout_final_renderer` | backlog |

## Scope For This PR

In scope:
- Add pure `src/agents/flyer/operating_layer.py`.
- Add optional `--operating-layer-input` to `tools/flyer-self-evaluation.py`; when provided, inject `operating_layer` into JSON/Markdown.
- Extend `tools/operator-brief.py` to summarize `operating_layer` from existing `--flyer-evaluation-json`.
- Add tests/fixtures.
- Add scoped backlog bullets under existing Flyer/Hermes backlog.

Out of scope:
- No new standalone CLI.
- No WhatsApp sends.
- No deploy.
- No customer/profile/project mutation.
- No live Hermes memory/session DB access.
- No X/Grok auth, search, or posting.
- No Codex CLI invocation.
- No video generation.
- No provider/model/source-edit enablement.
- No dashboard UI.

## Task 1: Operating-Layer Helper

**Files:**
- Create: `src/agents/flyer/operating_layer.py`
- Test: `tests/test_flyer_operating_layer.py`

- [ ] Write RED tests for:
  - complete customer/campaign evidence -> brand memory `ready_for_at_least_one_customer`
  - missing active brand asset -> `yellow`
  - missing delivered/completed campaign -> `yellow`
  - missing QA timestamp -> `yellow`
  - rollout verdict red or source-edit not smoke-proven -> capability is blocked/deferred, not ready
  - input rollout is used when self-eval rollout is absent
  - self-eval/input rollout conflict emits a conservative yellow reason
  - platform truthfulness false keeps `multi_format_export_truthfulness` blocked
  - `deferred_backlog` contains every key from the traceability matrix
  - helper source does not contain `subprocess`, `requests`, `urllib`, `socket`, `ssh`, `/opt/shift-agent`, `/root/.hermes`, `write_text`, `atomic_write`, `ndjson_append`, or `open(`
- [ ] Implement Pydantic fixture models:
  - `OperatingLayerCustomer`
  - `OperatingLayerCampaign`
  - `OperatingLayerRolloutContext`
  - `OperatingLayerPlatformTruthfulness`
  - `OperatingLayerReadinessInput`
- [ ] Implement:
  - `build_operating_layer_section(payload: dict | OperatingLayerReadinessInput | None, rollout: dict | None = None) -> dict`
  - `render_operating_layer_markdown(section: dict) -> list[str]`
- [ ] Verify tests pass.

## Task 2: Self-Evaluation Integration

**Files:**
- Modify: `tools/flyer-self-evaluation.py`
- Test: `tests/test_flyer_operating_layer.py`, `tests/test_flyer_self_evaluation.py`
- Fixtures: `tests/fixtures/flyer_operating_layer/*.json`

- [ ] Write RED CLI test:
  - `python tools/flyer-self-evaluation.py --projects ... --decisions-log ... --operating-layer-input tests/fixtures/flyer_operating_layer/ready.json --format json`
  - Assert `operating_layer.brand_memory.status == "ready_for_at_least_one_customer"`.
- [ ] Add `--operating-layer-input` to self-evaluation.
- [ ] Load explicit JSON input only; never scan live paths.
- [ ] Add Markdown rendering for the operating-layer block.
- [ ] Verify focused tests.

## Task 3: Operator Brief Integration

**Files:**
- Modify: `tools/operator-brief.py`
- Test: `tests/test_operator_brief.py`

- [ ] Write RED test where `--flyer-evaluation-json` contains `operating_layer`.
- [ ] Extend `summarize_flyer_evaluation_report()` to include:
  - operating-layer status
  - brand-memory readiness
  - next capability
  - source-edit/platform-truthfulness blockers when present
  - actionable next line such as `Next: source_edit_smoke_proof - operator - blocked until 5-10 case smoke`
- [ ] Do not add another CLI argument.
- [ ] Verify operator brief tests.

## Task 4: Backlog Filing

**Files:**
- Modify: `tasks/todo.md`

- [ ] Add scoped bullets under `Active - Hermes Flyer Studio Agent`.
- [ ] Include every `deferred_backlog` key from the traceability matrix.
- [ ] Mark each item as one of:
  - operator-only,
  - customer-visible behind approval,
  - blocked on smoke/eval,
  - deferred until static Flyer reliability is stable.
- [ ] Cross-link existing platform-truthfulness backlog instead of duplicating it.

## Task 5: Verification And PR

- [ ] Run:

```powershell
python -m pytest tests/test_flyer_operating_layer.py tests/test_flyer_self_evaluation.py tests/test_operator_brief.py -q
python -m py_compile src/agents/flyer/operating_layer.py tools/flyer-self-evaluation.py tools/operator-brief.py
python tools/flyer-self-evaluation.py --projects tests/fixtures/flyer_self_eval/projects.json --decisions-log tests/fixtures/flyer_self_eval/decisions.log --operating-layer-input tests/fixtures/flyer_operating_layer/ready.json --format json
python tools/flyer-self-evaluation.py --projects tests/fixtures/flyer_self_eval/projects.json --decisions-log tests/fixtures/flyer_self_eval/decisions.log --operating-layer-input tests/fixtures/flyer_operating_layer/partial.json --format markdown
git diff --check
```

- [ ] Request two PR reviewers:
  1. Hermes-first/runtime reviewer: no duplicate substrate, no live probes, no hidden mutation.
  2. Product-readiness reviewer: traceability/backlog completeness and no false “ready” signal.

## Acceptance

- Existing `flyer-self-evaluation` remains the only Flyer report CLI touched.
- Existing `--flyer-evaluation-json` remains the operator brief input.
- `operating_layer` is deterministic, explicit-input-only, read-only, and redacted through existing report/brief paths.
- Traceability matrix and `deferred_backlog` cover every Hermes update option from the user prompt.
- Report explicitly says current customer rollout blockers outrank strategic operating-layer work.
- No customer behavior changes.
- No deploy performed.
