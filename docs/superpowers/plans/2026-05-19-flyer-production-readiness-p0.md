# Flyer Studio Production Readiness P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Flyer Studio from the current 60-65% readiness state to a customer-safe production-pilot path that blocks known-bad flyers, preserves current-project context, extracts locked facts, handles references/template edits honestly, and gives deterministic status replies.

**Architecture:** Keep Hermes as the substrate for WhatsApp ingress, sender identity, project state, media delivery, audit, and existing Flyer scripts. Add a Flyer-specific locked facts layer, reference extraction metadata, actual preview QA artifacts, provider/source-edit preflight, and customer-safe state/status helpers around the existing renderer and cf-router flow. This plan deliberately does not replace the image provider; it makes generation one worker inside a controlled pipeline.

**Tech Stack:** Python 3.12, Pydantic v2 schemas in `src/platform/schemas.py`, JSON state via existing `safe_io`, Flyer scripts under `src/agents/flyer/scripts/`, renderer/workflow helpers under `src/agents/flyer/`, cf-router hooks/actions, pytest.

---

**Drift-check tag:** extends-Hermes

## Hermes-First Analysis

| Step | Hermes / in-tree primitive | Decision |
|---|---|---|
| WhatsApp ingress, sender validation, media paths | Existing Hermes gateway, cf-router, sender block, image cache | Use unchanged |
| Project state and audit | Existing Flyer JSON state, `safe_io`, Pydantic schemas, audit variants | Extend narrowly |
| Reference/media retention | Existing `FlyerAsset` + project assets | Reuse; add classification/extraction metadata |
| OCR/vision extraction | Hermes vision capability exists conceptually; no turnkey Flyer extractor is in tree | Add Flyer extractor interface with provider/fallback seams |
| Image generation | Existing OpenRouter image renderer and deterministic renderer | Reuse; add QA gate and retries already present |
| Source-preserving edits | Existing OpenAI source edit path | Add readiness/preflight and manual fallback semantics |
| Manual review/status | Partial `manual_edit_required` state and cf-router status replies | Extend to deterministic queue/status behavior |

Awesome Hermes Agent ecosystem check: no turnkey production Flyer Studio QA pipeline replaces the product-specific logic. Use Hermes primitives; build Flyer-specific fact extraction, QA, and state semantics.

## Readiness Claim Boundary

This plan is a P0 production-pilot hardening tranche, not permission to call Flyer Studio 90% ready by itself. After this plan passes, the correct claim is: "known live QA blockers now fail closed or route to manual review, and the deterministic P0 gate is green." The product still cannot be called 90% production-ready until the full backlog exit criteria pass: deterministic golden scenarios, spend-gated real-model eval, source-edit/manual fallback, real OCR/vision QA, operator queue clearance, and `main-vps` provider readiness.

## Pilot-Hardening Addendum - 2026-05-19

All P0 slices from this plan have shipped, deployed, and verified. The post-P0 follow-ups tracked after the 92% pilot-ready verdict are now closed in code except for live operator disposition of the six already-classified manual-queue projects:

- Spend-gated real-model golden smoke exists in `tests/test_flyer_golden_scenarios_real_model.py`; normal CI proves it fails closed without explicit `--allow-spend`.
- Cockpit manual queue now surfaces `source_edit_integrity_only` as an "Integrity only" badge via `verification_modes` from text-manifest sidecars.
- `flyer_manual_edit_status_reply` now delegates to the canonical state/reason table so source-edit status copy cannot drift from `MANUAL_REVIEW_REASON_LINES`.
- The deterministic golden suite includes messy F-series-style raw requests for Chloe salon service pricing, Lakshmi typo/price-list shorthand, and exact-edit wording.
- Manual queue burn-down remains an operator action, not a code action: production rows are classified and actionable, but completing or break-glassing real customer projects requires explicit operator approval plus backup/audit.

## Review Fixes Applied

- Added Task 0 for P0-1 project context isolation and stale-state routing.
- Visual QA now requires OCR/vision-derived text for customer-facing automated sends; manifest-only is not a pass.
- Reference extraction now includes a real OpenRouter/Hermes-style vision adapter in P0; sidecars are tests only.
- Manual review now includes an operator queue/list/complete path, not only customer copy.
- New modules require deploy/smoke/static-test wiring.
- Source-edit readiness must use the same env-file lookup as render.
- Golden scenarios are scoped to 5 overnight deterministic gates, with the 50-100 case suite remaining a broader backlog item.

## Task 0: Project Context Isolation And Stale-State Guard

**Files:**
- Modify: `src/agents/flyer/scripts/create-flyer-project`
- Modify: `src/agents/flyer/workflow.py`
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Test: `tests/test_flyer_create_project.py`, `tests/test_cf_router_flyer_routing.py`, `tests/test_flyer_workflow.py`

- [ ] **Step 1: Write failing context-isolation tests**

Cover:
- A customer with an old `awaiting_final_approval` project sends a complete "Create a new flyer..." request and gets a new clean project.
- A customer with a `manual_edit_required` source-edit queue sends a complete new poster request and does not append it to the queued edit.
- A correction without new-flyer wording still applies to the latest project for the account.
- A created project has no facts/revisions/assets from older projects unless the request explicitly says "reuse previous flyer/project FNNNN".

Run:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py::test_flyer_new_request_over_old_approval_starts_clean_project tests/test_flyer_create_project.py::test_create_project_does_not_copy_stale_project_context -q
```

Expected red: tests/functions missing or stale routing still intercepts.

- [ ] **Step 2: Add isolation helper**

In `workflow.py`, add:

```python
ALLOWED_NEW_PROJECT_FACT_SOURCES = {"customer_text", "customer_profile", "reference_ocr", "reference_vision", "uploaded_asset", "operator", "system"}

def is_explicit_new_flyer_request(text: str, *, has_media: bool = False) -> bool:
    ...

def is_explicit_previous_project_reuse(text: str) -> bool:
    ...

def context_isolation_blockers(project: FlyerProject) -> list[str]:
    ...
```

The blocker function should reject stale provenance values and any revision-derived facts on a newly created project. P0 should not automate previous-project reuse. If `is_explicit_previous_project_reuse()` is true, route to manual review/clarification until explicit `source_project_id` provenance is implemented.

- [ ] **Step 3: Wire routing**

At active-project intercept time, status checks still win first, but explicit new flyer requests must bypass old `awaiting_final_approval`, `manual_edit_required`, and old delivered projects unless the message says to continue/reuse that project.

- [ ] **Step 4: Verify**

Run:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py tests/test_flyer_create_project.py tests/test_flyer_workflow.py -q
```

## Files And Responsibilities

- Modify `src/platform/schemas.py`
  - Add structured Flyer facts and QA/provider/manual review metadata with backward-compatible defaults.
  - Keep `extra="ignore"` for extractor-facing shapes and `extra="forbid"` for persisted state.
- Create `src/agents/flyer/facts.py`
  - Extract locked facts from typed request fields, notes, customer profile, and reference extraction output.
  - Provide provenance and render-readiness helpers.
- Create `src/agents/flyer/reference_extract.py`
  - Classify uploaded media role and run deterministic/testable OCR/vision extraction provider seam.
  - Provide a no-network local fallback that fails closed with useful reasons.
- Create `src/agents/flyer/visual_qa.py`
  - Validate actual preview/final artifacts against locked facts using OCR text supplied by provider or sidecar.
  - Block placeholders, missing headline/tagline/items/prices/contact, wrong business name, and source-edit integrity-only sends.
- Modify `src/agents/flyer/render.py`
  - Feed locked facts into prompts and write QA artifacts beside generated images.
  - Fail preview/final generation when QA blockers exist unless source edit is explicitly manual/integrity-only.
- Modify `src/agents/flyer/workflow.py`
  - Add customer-safe status reply helpers and render-readiness checks.
- Modify `src/agents/flyer/scripts/create-flyer-project`
  - Populate locked facts, classify/extract reference assets, and queue unsupported exact edits honestly.
- Modify `src/agents/flyer/scripts/generate-flyer-concepts`
  - Stop after QA failure with manual-review/project failure metadata instead of sending bad previews.
- Modify `src/agents/flyer/scripts/finalize-flyer-assets`
  - Recheck QA artifacts before final package creation/delivery.
- Modify `src/plugins/cf-router/actions.py` and `src/plugins/cf-router/hooks.py`
  - Route status/check-in messages deterministically for every active/manual/failed state.
  - Use provider/source-edit preflight before accepting exact edit automation.
- Add tests:
  - `tests/test_flyer_facts.py`
  - `tests/test_flyer_reference_extract.py`
  - `tests/test_flyer_visual_qa.py`
  - Extend existing Flyer create/render/workflow/cf-router tests.

## Task 1: Locked Facts Schema And Extraction

**Files:**
- Modify: `src/platform/schemas.py`
- Create: `src/agents/flyer/facts.py`
- Test: `tests/test_flyer_facts.py`
- Extend: `tests/test_flyer_schemas.py`, `tests/test_flyer_create_project.py`

- [ ] **Step 1: Write failing schema tests**

Add tests proving persisted projects can carry locked facts with provenance:

```python
def test_flyer_project_accepts_locked_facts_with_provenance():
    project = FlyerProject.model_validate({... minimum project ..., "locked_facts": [
        {"fact_id": "headline", "label": "Headline", "value": "Premium Clean Chicken", "source": "customer_text", "required": True},
        {"fact_id": "item:0:price", "label": "Price", "value": "$14.99", "source": "customer_text", "required": True},
    ]})
    assert project.locked_facts[0].value == "Premium Clean Chicken"
```

Expected red: `locked_facts` is extra-forbidden on `FlyerProject`.

- [ ] **Step 2: Write failing facts extraction tests**

Cover:
- headline/tagline labels from prompt
- menu/service item-price pairs
- customer profile address/phone provenance
- non-visible style instructions excluded from visible facts

Run:

```powershell
python -m pytest tests/test_flyer_facts.py -q
```

Expected red: module/function missing.

- [ ] **Step 3: Implement schema additions**

Add:

```python
FlyerFactSource = Literal["customer_text", "customer_profile", "reference_ocr", "reference_vision", "uploaded_asset", "operator", "system"]

class FlyerLockedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)
    source: FlyerFactSource
    required: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_project_id: str = Field(default="", max_length=40)
    source_asset_id: str = Field(default="", max_length=40)
    source_message_id: str = Field(default="", max_length=200)
    source_sha256: str = Field(default="", max_length=64)
```

Add `locked_facts: list[FlyerLockedFact] = Field(default_factory=list, max_length=100)` to `FlyerProject`.

- [ ] **Step 4: Implement `facts.py`**

Functions:
- `extract_text_facts(fields: FlyerRequestFields, raw_request: str) -> list[FlyerLockedFact]`
- `merge_customer_profile_facts(facts, customer) -> list[FlyerLockedFact]`
- `required_fact_blockers(project: FlyerProject) -> list[str]`
- `facts_by_id(project: FlyerProject) -> dict[str, FlyerLockedFact]`

Use conservative regex only for high-confidence facts. Unknowns should be omitted and later ask for clarification or manual review.

- [ ] **Step 5: Wire project creation**

In `create-flyer-project`, after customer hydration, populate text/profile `project.locked_facts`. Reference facts are added later by Task 2 after the provider seam exists.

- [ ] **Step 6: Verify**

Run:

```powershell
python -m pytest tests/test_flyer_facts.py tests/test_flyer_schemas.py tests/test_flyer_create_project.py -q
```

## Task 2: Reference Media Classification And Extraction

**Files:**
- Create: `src/agents/flyer/reference_extract.py`
- Modify: `src/platform/schemas.py`
- Modify: `src/agents/flyer/scripts/create-flyer-project`
- Test: `tests/test_flyer_reference_extract.py`

- [ ] **Step 1: Write failing tests**

Cover:
- logo-only request classifies as `logo`
- "extract items/prices from attached sample" classifies as `menu_reference`
- exact edit wording classifies as `source_edit_template`
- low-confidence/no provider returns `FlyerReferenceExtraction.status` plus `project.manual_review.status="queued"` with detail
- extracted item/prices become locked facts and do not override typed facts

- [ ] **Step 2: Add reference extraction metadata schema**

Add:

```python
FlyerReferenceRole = Literal["logo", "menu_reference", "old_flyer_reference", "source_edit_template", "inspiration", "unsupported"]

class FlyerReferenceExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    role: FlyerReferenceRole
    provider: str = ""
    status: Literal["not_run", "ok", "low_confidence", "provider_unavailable", "unsupported"] = "not_run"
    extracted_facts: list[FlyerLockedFact] = Field(default_factory=list, max_length=100)
    detail: str = Field(default="", max_length=500)
    extracted_at: Optional[datetime] = None
```

Add `reference_extractions` to `FlyerProject`.

- [ ] **Step 3: Implement provider seam**

Implement deterministic interfaces:
- `class ReferenceExtractionProvider`
- `class OpenRouterVisionReferenceExtractionProvider`
- `class SidecarTextReferenceExtractionProvider`
- `class NoopReferenceExtractionProvider`

Use the existing OpenRouter vision style from `check-flyer-reference-scope` as the production adapter. Use `.ocr.txt` or `.json` sidecar only in tests and deterministic local scenarios. `NoopReferenceExtractionProvider` is a fail-closed test double, never a production success path.

- [ ] **Step 4: Wire creation**

In `create-flyer-project`, after copying reference asset:
- classify the asset
- if request requires extraction and provider unavailable, keep project out of render-ready state and mark manual/review detail
- if sidecar/provider returns facts, merge them with customer-typed facts, with customer text winning conflicts

- [ ] **Step 5: Verify**

Run:

```powershell
python -m pytest tests/test_flyer_reference_extract.py tests/test_flyer_create_project.py -q
```

## Task 3: Visual/OCR QA Gate

**Files:**
- Create: `src/agents/flyer/visual_qa.py`
- Modify: `src/platform/schemas.py`
- Modify: `src/agents/flyer/render.py`
- Modify: `src/agents/flyer/scripts/generate-flyer-concepts`
- Modify: `src/agents/flyer/scripts/finalize-flyer-assets`
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/agents/flyer/scripts/send-flyer-package`
- Test: `tests/test_flyer_visual_qa.py`, `tests/test_flyer_renderer.py`

- [ ] **Step 1: Write failing tests**

Cover:
- blocker for `[price]`
- blocker for missing headline/tagline
- blocker for missing item price
- blocker when `source_edit_integrity_only` is used for customer-facing preview without manual-review state
- pass when OCR text contains all required locked facts
- fail customer send when only declared manifest facts are available
- fail preview send in `send_flyer_concept_previews` when latest preview lacks a passing QA report matching artifact hash/version
- fail send after artifact mutation or project version bump invalidates the QA report

- [ ] **Step 2: Add QA schema**

Add:

```python
class FlyerVisualQAReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    asset_id: str = ""
    artifact_path: str
    artifact_sha256: str
    project_version: int
    output_format: str
    provider: str
    qa_source: Literal["ocr_vision", "sidecar_test"]
    status: Literal["passed", "failed", "not_run", "provider_unavailable"]
    blockers: list[str] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    extracted_text: str = Field(default="", max_length=5000)
    checked_at: datetime
```

Add `qa_reports` to `FlyerProject`.

- [ ] **Step 3: Implement QA module**

Implement:
- `extract_artifact_text(path)`: reads OCR/vision provider output or `.ocr.txt` sidecar in tests. Manifest facts may be recorded as diagnostics only; they must not satisfy automated customer send QA.
- `run_visual_qa(project, artifact_path, output_format) -> FlyerVisualQAReport`
- `qa_blocks_customer_send(project, report) -> bool`

If OCR/vision is unavailable, report `status="provider_unavailable"` and block automated send. Sidecar QA requires `FLYER_QA_ALLOW_SIDECAR=1` and is not a production readiness pass. Manual/operator paths may override only through explicit break-glass metadata.

- [ ] **Step 4: Wire render**

After concept/final rendering, write QA report next to the artifact. If blockers exist, raise `FlyerRenderError` for automated render/send paths. Source-edit integrity-only artifacts require manual approval/review state. Update both `send_flyer_concept_previews` and `send-flyer-package` so customer-facing sends require a passing QA report matching the artifact hash and project version.

`generate-flyer-concepts` and `finalize-flyer-assets` must catch QA/provider `FlyerRenderError`, reacquire the project lock, append QA/manual metadata, set safe manual-review status, revalidate the store, and exit nonzero. Router code must then send deterministic designer-review queued copy and release quota if the generation did not produce an automated preview.

- [ ] **Step 5: Verify**

Run:

```powershell
python -m pytest tests/test_flyer_visual_qa.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py -q
```

## Task 4: Source Edit Provider Readiness And Manual Fallback

**Files:**
- Modify: `src/agents/flyer/render.py`
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Modify: `src/agents/flyer/workflow.py`
- Test: `tests/test_cf_router_flyer_routing.py`, `tests/test_flyer_workflow.py`

- [ ] **Step 1: Write failing tests**

Cover:
- exact edit with missing `OPENAI_API_KEY` queues manual edit before generation
- exact edit status request returns deterministic queue status
- exact edit path never sends recreated generic poster as if it preserved source artwork
- unsupported PDFs queue manual edit unless source PDF edit provider is proven ready
- source edit final package derives from the approved source-edit preview, not a newly recreated poster
- direct `manual_edit_required -> finalizing_assets` is rejected; operator completion may move `manual_edit_required -> awaiting_final_approval` only after `manual_review.status=="completed"` and an operator preview asset is attached

- [ ] **Step 2: Implement provider readiness helper**

Expose one shared helper used by router and renderer:
- `source_edit_provider_ready(project_or_asset, *, env_path: Path | None = None) -> tuple[bool, str]`
- `manual_review_reply(project, reason) -> str`
- status replies for manual/source-edit queued states

The helper must read `OPENAI_API_KEY` through the same env-file path as `render.py` (`SHIFT_AGENT_ENV_PATH`) and validate source asset media type.

- [ ] **Step 3: Wire cf-router preflight**

Before accepting exact edit automation, call preflight. If unavailable, create project as `manual_edit_required`, send manual-review acknowledgment, and do not call generation.

- [ ] **Step 4: Verify**

Run:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py tests/test_flyer_workflow.py tests/test_flyer_renderer.py -q
```

## Task 5: Customer-Safe Status Replies And State Coverage

**Files:**
- Modify: `src/agents/flyer/workflow.py`
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Test: `tests/test_flyer_workflow.py`, `tests/test_cf_router_flyer_routing.py`

- [ ] **Step 1: Write failing tests**

For every `FlyerWorkflowStatus`, assert `build_project_status_reply(project)` returns a Flyer Studio-scoped response and never asks the customer to resend already-known facts.

- [ ] **Step 2: Implement status reply table**

Add a single table/function for:
- `intake_started`
- `collecting_required_info`
- `awaiting_assets`
- `generating_concepts`
- `awaiting_concept_selection`
- `awaiting_final_approval`
- `revising_design`
- `manual_edit_required`
- `finalizing_assets`
- `delivered`
- failure/manual/QA annotations from new metadata

- [ ] **Step 3: Wire status intercept**

Ensure `status`, `any update`, `is it ready`, and related messages are intercepted at the top of active-project handling for all non-terminal project states, before generation, approval, or revision parsing.

- [ ] **Step 4: Verify**

Run:

```powershell
python -m pytest tests/test_flyer_workflow.py tests/test_cf_router_flyer_routing.py -q
```

## Task 6: Manual Review Queue And Operator Completion

**Files:**
- Create: `src/agents/flyer/manual_queue.py`
- Create: `src/agents/flyer/scripts/flyer-manual-queue`
- Modify: `src/platform/schemas.py`
- Modify: `src/agents/shift/scripts/shift-agent-deploy.sh`
- Test: `tests/test_flyer_manual_queue.py`, `tests/test_flyer_scripts_static.py`

- [ ] **Step 1: Write failing tests**

Cover:
- queue lists `manual_edit_required`, QA-blocked, and provider-unavailable projects with customer, age, reason, assets, locked facts, and QA blockers.
- operator completion attaches approved assets and transitions to `awaiting_final_approval` through normal approval/delivery gates.
- break-glass/manual send requires an explicit reason and is represented in audit/metadata.

- [ ] **Step 2: Add queue metadata**

Add typed manual review/failure metadata to `FlyerProject` instead of relying on free-form status alone. If adding statuses is too invasive, keep statuses stable and use metadata with tested status replies.

- [ ] **Step 3: Implement CLI**

`flyer-manual-queue --list --state-path ...` emits JSON rows. `--complete PROJECT --asset PATH --reason TEXT` attaches an operator-provided asset and records completion metadata.

- [ ] **Step 4: Wire deploy**

Update deploy script/static tests so new modules and script are installed:
- `flyer_facts.py`
- `flyer_reference_extract.py`
- `flyer_visual_qa.py`
- `flyer_manual_queue.py`
- `flyer-manual-queue`

- [ ] **Step 5: Verify**

Run:

```powershell
python -m pytest tests/test_flyer_manual_queue.py tests/test_flyer_scripts_static.py -q
```

## Task 7: Golden Deterministic Scenario Harness

**Files:**
- Create: `tests/test_flyer_golden_scenarios.py`
- Create: `tests/fixtures/flyer_golden/README.md`
- Optional create: lightweight fixture `.ocr.txt` files only; do not commit large generated PNGs unless necessary.

- [ ] **Step 1: Write scenario table**

Start with 5 deterministic overnight gate scenarios. The full 50-100 case suite remains a backlog item:
- restaurant menu
- salon service flyer
- sample menu extraction via sidecar
- exact template edit unavailable/manual queue
- price correction
- stale/new project separation

- [ ] **Step 2: Run red**

Expected red for scenarios relying on new modules before Tasks 1-5 are implemented.

- [ ] **Step 3: Implement harness**

Use existing scripts/modules with temp state paths. Assertions should inspect locked facts, reference extraction metadata, QA reports, and status replies.

- [ ] **Step 4: Verify**

Run:

```powershell
python -m pytest tests/test_flyer_golden_scenarios.py -q
```

## Final Verification

Run:

```powershell
python -m pytest tests/test_flyer_onboarding.py tests/test_flyer_guest_order.py tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py tests/test_flyer_starter_briefs.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py tests/test_flyer_delivery_retry.py tests/test_flyer_schemas.py tests/test_flyer_facts.py tests/test_flyer_reference_extract.py tests/test_flyer_visual_qa.py tests/test_flyer_golden_scenarios.py -q
python -m py_compile src/agents/flyer/facts.py src/agents/flyer/reference_extract.py src/agents/flyer/visual_qa.py src/agents/flyer/manual_queue.py src/agents/flyer/render.py src/agents/flyer/workflow.py src/agents/flyer/scripts/create-flyer-project src/agents/flyer/scripts/generate-flyer-concepts src/agents/flyer/scripts/finalize-flyer-assets src/agents/flyer/scripts/send-flyer-package src/agents/flyer/scripts/flyer-manual-queue src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py src/platform/schemas.py
git diff --check
```

Additional readiness checks before any production claim:

```powershell
# spend-gated/manual; run only with operator-approved credentials and budget
python -m pytest tests/test_flyer_real_model_eval.py -q
```

On `main-vps`, readiness must report gateway active, bridge connected, image provider configured, OCR/vision provider configured, source-edit provider configured or exact edits fail-closed/manual, and no critical stale manual queue items.

## PR / Deploy Notes

- Create PR from `codex/flyer-production-ready-overnight`.
- Request three review lenses:
  - scope/readiness: does this actually close the live QA blockers?
  - code/data model: schema compatibility, state transitions, locking, persisted metadata
  - customer runtime: cf-router/status/manual fallback, no generic LLM loops, no bad flyer send path
- Do not deploy automatically if source-edit provider remains unavailable; deploy only the fail-closed/manual fallback path and report source-edit automation as unavailable.
