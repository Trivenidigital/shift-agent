# Flyer Studio Production Readiness P0 Design

**Drift-check tag:** extends-Hermes

## Goal

Implement the first production-pilot hardening tranche for Flyer Studio: isolate new project context, lock customer-visible facts, extract uploaded reference facts through a real provider seam, block automated sends without real visual/OCR QA, fail exact edits safely to manual review when source-edit automation is unavailable, and answer status checks deterministically.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress/media delivery | Existing Hermes gateway, cf-router, bridge send helpers | Use unchanged |
| Sender identity/account lookup | Existing sender block, `identify-sender`, Flyer customer store | Use unchanged |
| Project state/audit | Existing Flyer JSON state, Pydantic schemas, `safe_io` locks, NDJSON audit | Extend state models only |
| Reference image understanding | Existing OpenRouter vision pattern in `check-flyer-reference-scope`; Hermes OCR/vision can be connected behind same interface | Build Flyer adapter around existing pattern; sidecar only for deterministic tests |
| Image generation | Existing Flyer OpenRouter/deterministic renderer | Reuse; add QA gate |
| Source-preserving edits | Existing OpenAI source-edit renderer path | Share readiness helper and queue manual fallback |
| Manual review | Existing `manual_edit_required` state only | Add queue/list/complete helper; do not create new storage substrate |

Awesome Hermes Agent ecosystem check: no drop-in Flyer Studio production QA pipeline exists. The correct scope is to reuse Hermes substrate and add Flyer-specific facts, QA, and manual queue semantics.

## Non-Goals

- Do not claim 90% readiness after this tranche.
- Do not replace the image generation provider.
- Do not implement the full 50-100 golden scenario suite in this overnight tranche.
- Do not send customer-facing previews/finals using manifest-only QA.
- Do not auto-run spend-gated real-model eval without operator-approved credentials/budget.

## Data Model

Add the following schemas in `src/platform/schemas.py` and export them in `__all__`.

`FlyerLockedFact`
- `fact_id`: stable id such as `business_name`, `headline`, `tagline`, `item:0:name`, `item:0:price`, `contact_phone`.
- `label`: customer/operator-readable label.
- `value`: exact visible text expected on the flyer.
- `source`: one of `customer_text`, `customer_profile`, `reference_ocr`, `reference_vision`, `uploaded_asset`, `operator`, `system`.
- `required`: whether automated QA must find it.
- `confidence`: provider confidence.
- Optional provenance fields: `source_project_id`, `source_asset_id`, `source_message_id`, and `source_sha256`. P0 does not automate previous-project reuse; future reuse must use explicit provenance instead of hiding stale context as current text.

`FlyerReferenceExtraction`
- one row per reference asset.
- role: `logo`, `menu_reference`, `old_flyer_reference`, `source_edit_template`, `inspiration`, `unsupported`.
- status: `not_run`, `ok`, `low_confidence`, `provider_unavailable`, `unsupported`.
- extracted facts, detail, provider, timestamp.

`FlyerVisualQAReport`
- one row per generated artifact.
- project id, asset id, artifact path, artifact sha256, project version, output format, provider, `qa_source`, status, blockers, warnings, extracted text, checked timestamp.
- `status="passed"` is the only automated customer-send pass state.
- `qa_source="ocr_vision"` is required for production automated sends. `qa_source="sidecar_test"` is allowed only when `FLYER_QA_ALLOW_SIDECAR=1`.

`FlyerManualReview`
- status: `none`, `queued`, `in_progress`, `completed`, `break_glass_sent`.
- reason/category/detail.
- queued/completed timestamps and operator completion asset ids.
- used for source-edit provider unavailable, OCR/vision unavailable, unsupported media, or QA-blocked output.

Backward compatibility: all fields default to empty lists or `FlyerManualReview(status="none")`, so old state files still validate.

## Module Contracts

### `src/agents/flyer/facts.py`

Responsibilities:
- Extract high-confidence visible facts from `FlyerRequestFields` and `raw_request`.
- Hydrate profile facts from `FlyerCustomer`.
- Merge reference facts without letting stale/reference facts override customer-typed facts.
- Validate project isolation provenance.

Key functions:
- `extract_text_facts(fields, raw_request) -> list[FlyerLockedFact]`
- `customer_profile_facts(customer) -> list[FlyerLockedFact]`
- `merge_locked_facts(*fact_lists) -> list[FlyerLockedFact]`
- `context_isolation_blockers(project) -> list[str]`
- `required_fact_blockers(project) -> list[str]`

Fact source rules:
- New projects may only have sources from current customer text, current customer profile, current uploaded assets/reference extraction, operator/system.
- No previous revision/project source is allowed in P0 automation. If a customer asks to reuse a previous flyer/project, route to clarification/manual review until explicit reuse provenance is implemented.

### `src/agents/flyer/reference_extract.py`

Responsibilities:
- Classify reference asset role from request text plus MIME/path.
- Extract facts from uploaded reference media when the request requires extraction.
- Fail closed to manual review when provider unavailable or unsupported.

Provider order:
1. `OpenRouterVisionReferenceExtractionProvider`, using the current OpenRouter vision pattern from `check-flyer-reference-scope`.
2. `SidecarReferenceExtractionProvider`, enabled only by explicit test/local env or direct injection.
3. `NoopReferenceExtractionProvider`, fail-closed.

The production adapter must read API key/config the same way existing vision scripts do. It emits structured JSON facts, not free-form prompt text.

### `src/agents/flyer/visual_qa.py`

Responsibilities:
- Extract OCR/vision text from the actual generated artifact.
- Compare actual visible text to locked required facts.
- Block placeholders and random/template artifacts.
- Write/read `.qa.json` sidecars tied to artifact sha256/project version.

Customer-send rule:
- `FlyerVisualQAReport.status == "passed"` and matching artifact hash/version is required for automated preview/final sends.
- Manifest-only/declared facts are diagnostics only.
- If OCR/vision unavailable, automated send is blocked and the project is queued for manual review.

### `src/agents/flyer/manual_queue.py`

Responsibilities:
- List manual/QA/provider-blocked projects with customer, age, reason, assets, locked facts, and QA blockers.
- Complete a manual item by attaching an operator-approved asset.
- Record break-glass reason when an operator intentionally bypasses QA.

Storage:
- Reuse `FlyerProjectStore`; no new DB/table.
- State writes use existing file lock and atomic JSON writer.

## Flow Changes

### Project Creation

`create-flyer-project`:
1. Extract request fields.
2. Hydrate missing profile fields.
3. Copy current reference asset only.
4. Create text/profile locked facts.
5. Classify/extract reference media if present.
6. Merge facts with customer text winning.
7. Run context-isolation blockers.
8. If extraction/provider is required but unavailable, set `manual_review.status="queued"` and keep status `manual_edit_required` or `awaiting_assets` depending on request type.

### Active Project Routing

In `_try_flyer_active_project_intercept`:
1. Status/check-in messages are handled first for all non-terminal states.
2. Explicit new flyer requests bypass stale active projects unless message explicitly says continue/reuse previous project.
3. Corrections without new-flyer wording apply to latest account project across public phone, business WhatsApp, onboarded phone, LID-resolved phone, and authorized requester numbers.
4. P0 does not automate previous-project reuse. Reuse requests route to clarification/manual review.

### Rendering And Preview Send

Generation:
1. Render artifact.
2. Run visual QA on actual artifact.
3. If QA passed, attach asset/concept.
4. If QA failed/provider unavailable, catch the failure, reacquire the project lock, append QA/manual metadata, set the safe status, release quota where applicable, send deterministic "designer review queued; no resend needed" copy, and do not send preview.

Send chokepoints:
- `send_flyer_concept_previews` checks `.qa.json`, artifact hash, project id/version/output format.
- `send-flyer-package` does the same for final assets.
- Both chokepoints recompute artifact sha256 and reject stale QA after artifact mutation or project version changes.
- Existing text manifest validation may remain as an additional integrity check, not the gate.

### Exact Edit

Shared readiness helper:
- `source_edit_provider_ready(project_or_asset, *, env_path: Path | None = None)`
- Reads `OPENAI_API_KEY` the same way renderer does.
- Rejects unsupported/non-image media for automated exact edit.

If unavailable:
- Create/manual-update project with `manual_review.status="queued"`.
- Customer gets designer-assisted queue copy.
- Status checks return queue status.

Manual completion transition:
- `manual_edit_required -> awaiting_final_approval` is legal only when `manual_review.status=="completed"` and an operator-approved preview asset/concept is attached.
- The normal customer approval path then moves to `finalizing_assets`; direct `manual_edit_required -> finalizing_assets` remains invalid.

### Manual Queue

`flyer-manual-queue --list` emits JSON rows:
- project id, customer phone, status, manual reason/detail, age, assets, locked facts, latest QA blockers.

`flyer-manual-queue --complete PROJECT --asset PATH --reason TEXT`:
- copies/attaches asset under Flyer state.
- records manual completion.
- transitions to `awaiting_final_approval` through the legal manual-completion path.
- revalidates `FlyerProjectStore.model_validate(store.model_dump())` before writing.

## Deploy Design

Update `src/agents/shift/scripts/shift-agent-deploy.sh` to install/remove:
- `/opt/shift-agent/flyer_facts.py`
- `/opt/shift-agent/flyer_reference_extract.py`
- `/opt/shift-agent/flyer_visual_qa.py`
- `/opt/shift-agent/flyer_manual_queue.py`

Scripts must use dual imports:

```python
try:
    from flyer_facts import ...
except ImportError:
    from agents.flyer.facts import ...
```

Static tests must assert deploy and smoke import coverage.

All provider/manual outputs must be constructed as typed Pydantic models at the boundary. Any script that persists nested metadata after `model_copy(update=...)` must revalidate `FlyerProjectStore.model_validate(store.model_dump())` before writing.

## Test Strategy

TDD red-first tests:
- schema backward compatibility and `__all__`.
- locked fact extraction/merge/provenance.
- reference classification/extraction provider unavailable/manual queue.
- visual QA blocks placeholders, missing facts, manifest-only sends.
- QA rejects mutated artifacts and project version mismatches after a report is written.
- send chokepoints reject assets without passing QA.
- exact edit missing provider queues manual review.
- status checks in every active/manual/QA-blocked state.
- `finalizing_assets` has deterministic status reply coverage.
- source-edit readiness reads a temp env file through the shared helper.
- manual completion transition is valid only from completed manual review with operator asset attached.
- deploy static tests for new modules.
- 5 deterministic golden scenarios.

Final local verification:

```powershell
python -m pytest tests/test_flyer_onboarding.py tests/test_flyer_guest_order.py tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py tests/test_flyer_starter_briefs.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py tests/test_flyer_delivery_retry.py tests/test_flyer_schemas.py tests/test_flyer_facts.py tests/test_flyer_reference_extract.py tests/test_flyer_visual_qa.py tests/test_flyer_manual_queue.py tests/test_flyer_golden_scenarios.py -q
python -m py_compile src/agents/flyer/facts.py src/agents/flyer/reference_extract.py src/agents/flyer/visual_qa.py src/agents/flyer/manual_queue.py src/agents/flyer/render.py src/agents/flyer/workflow.py src/agents/flyer/scripts/create-flyer-project src/agents/flyer/scripts/generate-flyer-concepts src/agents/flyer/scripts/finalize-flyer-assets src/agents/flyer/scripts/send-flyer-package src/agents/flyer/scripts/flyer-manual-queue src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py src/platform/schemas.py
git diff --check
```

## Risks And Mitigations

- OCR/vision provider unavailable in prod: fail closed to manual review; do not send customer preview.
- Schema drift on old state: default fields and roundtrip tests.
- New modules missed by deploy: static tests on deploy script and smoke imports.
- Manual queue becoming hidden state: CLI list is required in this tranche; dashboard can follow.
- Provider spend/latency: deterministic tests use sidecars; real provider eval is spend-gated and separate.
