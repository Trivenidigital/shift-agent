**Drift-check tag:** extends-Hermes

# Flyer Edit Flow Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Flyer Studio edit requests safe, reliable, and honest: exact artwork edits must not create poor recreated posters, failed edits must not corrupt active project state, and customers must receive actionable responses.

**Architecture:** Reuse Hermes WhatsApp ingress, sender identity, media cache, JSON state, `safe_io` locking, bridge delivery, audit log, Flyer quota/guest access, and existing project scripts. Add narrow Flyer-layer hardening around edit classification, revision patch extraction, state mutation, source-edit provider readiness, and manual-queue/admin visibility.

**Tech Stack:** Python, Pydantic v2 schemas, existing Flyer JSON stores, pytest, existing cockpit/admin APIs where needed.

---

**New primitives introduced:** source-edit provider readiness helper, revision no-op preservation rule, stricter edit patch parser, admin edit-queue/stuck-work visibility fields.

**Hermes-first analysis**

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp/media ingress | yes - deployed Hermes gateway, cf-router sender/media hooks | use it |
| Sender identity and LID handling | yes - `identify-sender`, `lid-cache`, existing cf-router lookup | use it |
| State and audit | yes - JSON stores, `safe_io.FileLock`, `atomic_write_text`, NDJSON audit | use it |
| Message delivery | yes - existing bridge text/media/CTA helpers | use it |
| OCR/reference extraction | partial - Hermes `ocr-and-documents` exists for text extraction, but source artwork edit requests are image-generation/editing tasks | use existing reference media path; do not build broad OCR pipeline in this PR |
| Image generation/editing | partial - Hermes Skills Hub lists creative/image-generation skills such as ComfyUI and MLOps/SAM; no deployed Flyer-specific source-preserving WhatsApp approval loop exists | build narrow provider readiness/fallback around current renderer, do not add new Hermes substrate |
| PDF text editing | yes - Hermes `nano-pdf` edits PDF text/typos, but Flyer source edits are usually raster WhatsApp images | defer PDF exact edit support; fail closed with clear copy |
| Installed VPS runtime | partial - live `/root/.hermes/skills` has `productivity/ocr-and-documents`, `productivity/nano-pdf`, `creative/comfyui`, `mlops/*`, and the deployed `flyer_generation` skill | use installed Hermes ingress/skills as substrate; no installed skill owns Flyer quota/state/final-package edit loop |

Awesome-Hermes ecosystem check: checked official Skills Hub and bundled catalog; useful adjacent skills exist (`comfyui`, `segment-anything-model`, `ocr-and-documents`, `nano-pdf`) but none replace the current Flyer account/quota/state/delivery flow. Verdict: keep this as a thin Flyer-specific extension over Hermes primitives.

## File Map

- Modify `src/agents/flyer/workflow.py`: broaden deterministic revision parsing for common edit language and expose no-op/clarification semantics.
- Modify `src/agents/flyer/scripts/update-flyer-project`: do not clear current concepts/finals when the revision requires clarification or is a no-op; only invalidate designs when a patch is actionable.
- Modify `src/agents/flyer/render.py`: add source-edit provider readiness/error helper and customer-safe failures for missing edit credentials or unsupported PDFs.
- Modify `src/agents/flyer/scripts/generate-flyer-concepts`: fail closed with structured detail for source-edit provider unavailable; preserve project state on provider failure.
- Modify `src/plugins/cf-router/actions.py`: improve customer-facing source edit/manual queue copy and add clear support/escalation text.
- Modify `src/plugins/cf-router/hooks.py`: route text-only post-delivery corrections to safe revision handling; ensure queued/manual edit projects do not promise automatic delivery unless an operator path exists.
- Modify `web/backend/app/routers/flyer.py` and `web/frontend/src/sections/FlyerAdmin.tsx` only if existing admin project visibility cannot surface `manual_edit_required`/stuck edit projects.
- Tests: `tests/test_flyer_workflow.py`, `tests/test_flyer_update_project.py` or closest existing script test, `tests/test_flyer_renderer.py`, `tests/test_cf_router_plugin.py`, `tests/test_cf_router_flyer_routing.py`, `web/backend/tests/test_flyer_admin.py` if admin changes are needed.

## Task 1: Protect Project State On Unclear Edits

**Files:**
- Modify: `src/agents/flyer/scripts/update-flyer-project`
- Modify: `tests/test_flyer_scripts_static.py`
- Test: add focused script-state regression in the closest existing Flyer script/workflow test file.

- [ ] **Step 1: Write failing test for no-op edit state preservation**

Create a delivered or awaiting-final-approval project with one selected concept and final asset ids, then run `update-flyer-project --revision-text "The flyer is still not right."`. Expected red behavior: current code returns `revision_requires_clarification=true`, moves status to `revising_design`, and clears `concepts`, `selected_concept_id`, and `final_asset_ids`.

- [ ] **Step 2: Run the focused test and verify it fails for state corruption**

Run: `python -m pytest tests/test_flyer_workflow.py tests/test_flyer_create_project.py -q`

- [ ] **Step 3: Change update script to invalidate only actionable revisions**

Keep the entire pre-revision approveable/delivered state when `revision_requires_clarification` is true: status, version, updated_at, revisions, fields, raw_request, assets, concepts, selected concept, and final asset ids all remain unchanged. Do not leave the project in `revising_design` with no concepts unless regeneration can actually run.

- [ ] **Step 4: Update static contract test**

Adjust `tests/test_flyer_scripts_static.py` so it no longer requires unconditional clearing of concepts/finals. It should assert the clear happens only after an actionable patch or visual-only revision.

- [ ] **Step 5: Run focused tests and commit**

Run: `python -m pytest tests/test_flyer_workflow.py tests/test_flyer_scripts_static.py tests/test_flyer_renderer.py -q`

Commit: `fix(flyer): preserve preview state for unclear edits`

## Task 2: Parse Common Customer Edit Requests

**Files:**
- Modify: `src/agents/flyer/workflow.py`
- Test: `tests/test_flyer_workflow.py`

- [ ] **Step 1: Add failing parser tests**

Cover:
- `Remove that extra 08:00. Add Any Item for $9.99.` -> `changed=true`, `ambiguous=false`, `notes_update` and `raw_request_update` include `Remove duplicate/extra time text "08:00"` and `Add menu item Any Item for $9.99`.
- `Swap Kheema Dosa with Any Item for $9.99.` -> `changed=true`, `ambiguous=false`, `notes_update` includes `Replace menu item Kheema Dosa with Any Item for $9.99`.
- `Remove Tatte Idly and add Ghee Karam Idly same price.` -> `changed=true`, `ambiguous=false`, `notes_update` includes remove/add instruction.
- `Change Kheema Dosa price to $9.99.` -> if the current notes contain exactly one `Kheema Dosa $12.99`, `changed=true` and notes replace only that price; if the item is absent or repeated, `ambiguous=true` with a useful unresolved reason.
- `Change Kheema Dosa price to $9.99.` parser rule: search `fields.notes` first, then `raw_request`; match the item once; find nearest price in the same sentence/comma-delimited segment; replace only that price. If no adjacent price exists, return `ambiguous=true`.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_flyer_workflow.py -q`

- [ ] **Step 3: Implement minimal parser improvements**

Add item add/remove instructions to notes/raw_request when the source text contains clear add/remove/swap language. For time removal, append an explicit instruction such as `Remove duplicate/extra time text "08:00" from the flyer.` without changing structured `event_time`.

- [ ] **Step 4: Verify green and commit**

Run: `python -m pytest tests/test_flyer_workflow.py -q`

Commit: `fix(flyer): parse natural edit requests`

## Task 3: Make Source-Edit Provider Readiness Explicit

**Files:**
- Modify: `src/agents/flyer/render.py`
- Modify: `src/agents/flyer/scripts/generate-flyer-concepts`
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Test: `tests/test_flyer_renderer.py`
- Test: `tests/test_cf_router_plugin.py`

- [ ] **Step 1: Add failing router/CLI tests for missing provider and PDF reference**

Assert exact source edits do not send the active-generation processing ack when the edit provider is unavailable or the latest reference is a PDF. Cover both new exact media edit and reference-scope authorized edit branches. The router should send manual/designer-assisted queue copy instead and return `skip`; `generate-flyer-concepts` should return structured non-traceback detail for source-edit provider failures.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_flyer_renderer.py -q`

- [ ] **Step 3: Implement router-callable provider readiness helper**

Add an actions/render helper that checks source-edit model requirements before quota reservation and before `send_flyer_edit_processing_ack`. Use it in every source-edit generation entry point: new exact media edit, reference-scope authorized edit, manual/source-edit follow-up if generation is attempted, and revision/regeneration when the project is source-edit. Keep the error customer-safe and operator-actionable.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_flyer_renderer.py tests/test_cf_router_plugin.py -q`

Commit: `fix(flyer): fail source edits closed when provider unavailable`

## Task 4: Align Router Copy With Real Edit Capability

**Files:**
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Test: `tests/test_cf_router_plugin.py`, `tests/test_cf_router_flyer_routing.py`

- [ ] **Step 1: Add failing route/copy tests**

Cover missing-provider source edit fallback, manual queue acknowledgement, follow-up correction while a project is queued, and quota/guest release failure after source-edit generation, preview, and finalize failure paths.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_cf_router_plugin.py tests/test_cf_router_flyer_routing.py -q`

- [ ] **Step 3: Update customer copy and routing**

Do not promise automatic completion when no worker exists. Suggested copy: `I saved this as a designer-assisted source edit. Support will review it and send the corrected flyer here.`

Also make `_release_flyer_access` return success/detail for both quota and guest access. Audit release failures in the existing cf-router audit detail and make the customer copy avoid implying their sample/credit is available when release failed.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_cf_router_plugin.py tests/test_cf_router_flyer_routing.py -q`

Commit: `fix(flyer): make edit queue replies honest`

## Task 5: Surface Stuck/Edit Projects To Admin

**Files:**
- Inspect first: `web/backend/app/routers/flyer.py`, `web/frontend/src/sections/FlyerAdmin.tsx`
- Modify: `web/backend/app/routers/flyer.py`
- Modify frontend only if backend fields are invisible in the existing Projects tab.
- Test: `web/backend/tests/test_flyer_admin.py`, frontend build.

- [ ] **Step 1: Drift-check current admin project visibility**

Use `rg` and existing tests to determine whether `manual_edit_required`, `revising_design` with zero concepts, and source-edit queued projects are visible.

- [ ] **Step 2: Add failing admin test only if visibility is missing**

Expected: admin summary/project list includes manual/stuck edit projects, `manual_edit_count`, `stuck_edit_count`, preserved existing `stuck_projects`, project age/updated age, and enough fields for operator triage. Manual edit older than 30 minutes and `revising_design` with zero concepts older than 10 minutes are stale/stuck.

- [ ] **Step 3: Implement minimal backend/frontend visibility if needed**

Avoid new mutation surfaces in this PR. This task is visibility only unless a safe existing action already exists. Manual queue durability is through the existing project row plus status, raw request, assets, updated age, and audit detail; a separate queue table is deferred unless tests prove the project row can silently disappear.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest web/backend/tests/test_flyer_admin.py -q`

If frontend changed: `npm --prefix web/frontend run build`

Commit: `fix(flyer): surface queued edit work in admin`

## Task 6: Final Verification And PR

**Files:**
- No production changes unless tests expose a gap.

- [ ] **Step 1: Run full focused verification**

Run:
- `python -m pytest tests/test_cf_router_flyer_routing.py tests/test_cf_router_plugin.py tests/test_flyer_workflow.py tests/test_flyer_renderer.py tests/test_flyer_delivery_retry.py -q`
- `python -m pytest tests/test_flyer_scripts_static.py web/backend/tests/test_flyer_admin.py -q`
- `python -m compileall -q src web/backend/app`
- `git diff --check`

- [ ] **Step 2: Update backlog/lessons**

Record any deferred platform-specific story/export truthfulness or manual edit worker backlog items in `tasks/todo.md` or a scoped backlog file.

- [ ] **Step 3: Push branch and open PR**

Create a ready PR with concise risk/verification notes.

- [ ] **Step 4: Request three parallel PR reviews**

Review vectors:
- Source-edit/routing correctness.
- State/quota/audit durability.
- Product UX/customer copy and admin operability.
