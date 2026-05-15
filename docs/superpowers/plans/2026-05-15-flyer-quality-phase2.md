# Flyer Quality Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Drift-check tag:** extends-Hermes

**Goal:** Make Flyer Studio safer for paying-customer visual work by hardening real image-model generation, critical-text correctness, template/reference fidelity, and revision correctness before final approval.

**Architecture:** Keep the existing Hermes-first WhatsApp flow: cf-router routes explicit flyer work, Python scripts mutate JSON state under `FileLock`, and `safe_io.bridge_send_media` delivers assets. Add a narrow quality/composition layer around the existing renderer and revision update path instead of replacing the deployed Flyer workflow.

**Tech Stack:** Python, Pydantic v2, Pillow for local asset inspection/export/critical text overlay, OpenRouter image calls already used by `flyer_render`, Hermes gateway/WhatsApp bridge, pytest, tarball deploy.

---

**New primitives introduced:** rendered asset quality inspection report, critical-text overlay/compositor, expanded revision field patcher, template/reference preservation prompt policy, production `smoke-flyer-quality` CLI.

## Hermes-First Analysis

| Domain | Hermes skill/tool found? | Decision |
|---|---|---|
| WhatsApp ingress/delivery | yes - deployed Hermes gateway + cf-router + bridge `/send`/`/send-media` | use it |
| Image generation | yes - Hermes has `image_generate` via the `image_gen` toolset and Tool Gateway image models including GPT Image/Ideogram/Recraft/Qwen; live VPS currently has OpenRouter image config instead of Tool Gateway image provider | keep current OpenRouter path for Phase 2 smoke; defer Hermes Tool Gateway provider adapter to Phase 3 because direct script use of `image_generate` is not currently exposed as a stable deployed CLI contract |
| Vision/OCR | yes - `vision_analyze` and installed `productivity/ocr-and-documents` | do not custom OCR in Phase 2; render critical facts server-side and add OCR/vision exact-field QA only after a separate design |
| Reference/template image intake | yes - Hermes WhatsApp media cache and Flyer project reference assets already exist | extend prompts and revision state; no new media substrate |
| State/audit | yes - repo `safe_io.FileLock`, `atomic_write_text`, `ndjson_append`, `LogEntry` pattern | use existing state/audit conventions |
| Payment/onboarding | yes - Phase 1 Flyer account layer exists | out of scope except ensuring active customers can reach the quality path |

Awesome Hermes Agent ecosystem check: no ready-made WhatsApp flyer QA/template-revision workflow was found; Hermes provides media generation and vision primitives, while SMB-specific revision semantics remain product logic.

Sources checked:
- Hermes Tool Gateway image models and Gateway behavior: https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-gateway
- Hermes built-in tools include `image_generate` and `vision_analyze`: https://hermes-agent.nousresearch.com/docs/reference/tools-reference/
- Hermes `image_gen` toolset is text-to-image via FAL with opt-in OpenAI/xAI backends: https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference
- Hermes bundled plugins include `image_gen/openai` for OpenAI `gpt-image-2`: https://hermes-agent.nousresearch.com/docs/user-guide/features/built-in-plugins

## Drift Grounding

- Renderer work is grounded in `src/agents/flyer/render.py`, especially `_image_prompt`, `_image_message_content`, `render_concept_previews`, and `render_final_package`.
- Revision work is grounded in `src/agents/flyer/scripts/update-flyer-project`, `src/agents/flyer/scripts/generate-flyer-concepts`, and `src/agents/flyer/workflow.py`; these currently handle date/time edits but not menu/title/price correction reliably.
- Runtime config check on `main-vps` confirmed `flyer.draft_image_model=openai/gpt-5.4-image-2`, `final_image_model=openai/gpt-5.4-image-2`, `quality=high`, `concept_count=1`.
- Live Hermes capability check on `main-vps` confirmed `productivity/ocr-and-documents` and `mcp/native-mcp` are installed; no local image-generation skill directory is installed, and gateway is active/bridge connected.
- Deploy smoke currently checks Flyer scripts/modules and `/send-media`, but it does not run a real image-model health probe.

## Scope

In scope:
- Add local quality inspection for generated PNG/PDF assets: existence, expected dimensions, minimum size, image variance/nonblank check, PDF parseability/min-size, and optional source-preview export check.
- Fail concept/final generation if generated assets do not pass quality inspection.
- Add a server-side critical-text overlay/compositor for generated model images. The image model may create the visual background, but dates, times, phone numbers, venue/address, title/offer, and price/menu details must be rendered by Pillow after generation so customer-facing facts do not depend on model text rendering.
- Keep a raw/internal model background beside each overlaid preview, then create each final format from the raw background and apply exactly one per-format overlay. Never crop an already-overlaid preview into square/story/PDF.
- Prompt model generation to avoid readable critical text and leave facts to the compositor; for reference/template images, preserve visual identity and layout feel while neutralizing stale readable facts where possible.
- Add a `smoke-flyer-quality` CLI that runs against an isolated temp state, exercises the configured real image model only with an explicit spend flag, and emits JSON suitable for deploy/operator smoke.
- Install any shared Flyer Python module used by deployed scripts, especially `workflow.py` as `/opt/shift-agent/flyer_workflow.py`, and smoke-import it on the VPS.
- Expand revision extraction so high-confidence customer corrections update project state before regeneration:
  - price replacements such as `$14.99 to $16.99`
  - title/offer corrections such as `from Weekend Breakfast to Thursday Dosa Night Special`
  - phone/contact changes
  - venue/location changes
  - existing date/time corrections remain covered
- Mark revisions applied only after either structured project facts changed or the revision is explicitly visual-only, and after regenerated assets pass the overlay/quality gate. Use existing `FlyerRevision.resulting_version` as the schema-compatible acceptance marker; do not persist extra metadata in `FlyerRevision`.
- Surface ambiguous/unresolved revisions to WhatsApp with a clarification message and keep approval blocked.
- Strengthen prompts when a reference/template image exists: preserve the attached template/offer identity, do not redesign from scratch, and apply revision edits exactly.
- Add tests for prompt policy, critical-text overlay, revision extraction, quality checks, and the smoke CLI's no-network deterministic mode.

Out of scope:
- Building a full OCR text-fidelity evaluator. Phase 2 handles exact critical facts through server-side rendering instead.
- Adding a Hermes Tool Gateway image provider adapter.
- Stripe/Razorpay webhooks.
- New UI or dashboard.

## Plan

### Task 1: Add Render Quality Inspection And Critical Text Overlay

**Files:**
- Modify: `src/agents/flyer/render.py`
- Test: `tests/test_flyer_renderer.py`

- [ ] Write failing tests for `inspect_rendered_asset` accepting a valid PNG/PDF export and rejecting blank/tiny/wrong-dimension PNGs.
- [ ] Write failing tests proving generated/model images receive a server-rendered critical-text overlay and PDF exports are at least parseable/non-empty.
- [ ] Implement `RenderedAssetQuality`, `inspect_rendered_asset(path, expected_width, expected_height, mime_type)`, and `apply_critical_text_overlay(project, source, target, size)`.
- [ ] Wire quality inspection and overlay into `render_concept_previews` and `render_final_package`; deterministic renderer can keep its native text, but model outputs must pass through the overlay before customer display.
- [ ] Store model raw backgrounds as internal sibling files and generate each final format from the raw background with exactly one overlay.
- [ ] Run `python -m pytest tests/test_flyer_renderer.py -q`.

### Task 2: Expand Revision Patch Extraction

**Files:**
- Modify: `src/agents/flyer/workflow.py`
- Modify: `src/agents/flyer/scripts/update-flyer-project`
- Modify: `src/agents/flyer/scripts/generate-flyer-concepts`
- Test: `tests/test_flyer_workflow.py`
- Test: `tests/test_flyer_scripts_static.py`

- [ ] Write failing tests for price change, title/offer correction, phone change, venue change, repeated-price ambiguity, and old-text-not-found behavior.
- [ ] Move duplicate revision extraction out of `update-flyer-project` into `agents.flyer.workflow`.
- [ ] Install `src/agents/flyer/workflow.py` as `/opt/shift-agent/flyer_workflow.py` and import it in deploy smoke before relying on it from `/usr/local/bin/update-flyer-project`.
- [ ] Update `update-flyer-project` to apply the shared extractor, mutate `fields.notes`/`raw_request` when old text is found, clear generated concepts/finals, and record structured patch results.
- [ ] Update `generate-flyer-concepts` so revisions are marked applied only after at least one structured fact changed or the revision is explicitly classified as visual-only, and after regenerated assets pass overlay/quality checks.
- [ ] Update cf-router active Flyer revision branch so unresolved revisions get a clarification response, not a false "Revision noted" acknowledgement.
- [ ] Run `python -m pytest tests/test_flyer_workflow.py tests/test_flyer_scripts_static.py -q`.

### Task 3: Harden Reference/Template Prompt Policy

**Files:**
- Modify: `src/agents/flyer/render.py`
- Test: `tests/test_flyer_renderer.py`

- [ ] Write failing tests showing prompts with a reference image include "do not redesign from scratch" and include the latest applied revision facts.
- [ ] Add `_reference_preservation_instruction(project)` and include it only when project/customer reference assets exist.
- [ ] Ensure the latest project reference/template image remains attached even when customer brand assets also exist.
- [ ] Run `python -m pytest tests/test_flyer_renderer.py -q`.

### Task 4: Add Production Quality Smoke CLI

**Files:**
- Create: `src/agents/flyer/scripts/smoke-flyer-quality`
- Modify: `src/agents/shift/scripts/shift-agent-deploy.sh`
- Modify: `src/agents/shift/scripts/shift-agent-smoke-test.sh`
- Test: `tests/test_flyer_scripts_static.py`

- [ ] Write static tests requiring the smoke CLI to exist, use Hermes venv-compatible imports, support `--real-model`, require `--allow-spend` for real-model mode, and emit JSON.
- [ ] Implement deterministic mode: create temp project under a temporary `FLYER_STATE_ROOT`, render with `deterministic-renderer`, inspect output, clean up unless `--keep-output` is supplied, and print `{"ok": true, ...}`.
- [ ] Implement real-model mode: require both `--real-model` and `--allow-spend`, read `Config.flyer`, call `render_concept_previews` with configured model/quality and one concept, inspect output, and print model/quality/concept count/asset diagnostics.
- [ ] Install the script in deploy, include a non-network deterministic smoke invocation in deploy smoke as `shift-agent`, and add rollback/stale cleanup for `smoke-flyer-quality` and `flyer_workflow.py`.
- [ ] Run `python -m pytest tests/test_flyer_scripts_static.py tests/test_flyer_renderer.py -q`.

### Task 5: Docs, Backlog, PR, Deploy

**Files:**
- Modify: `tasks/todo.md`
- Possibly modify: `tasks/lessons.md`

- [ ] Mark Phase 2 plan/design/build/review/deploy items in `tasks/todo.md`.
- [ ] Run focused tests and `python -m py_compile` on changed scripts/modules.
- [ ] Create PR and request three reviewer agents with orthogonal lenses: product visual quality, Hermes/deploy drift, and state/revision correctness.
- [ ] Fix review findings.
- [ ] Merge to `main`.
- [ ] Build tarball, deploy to `main-vps`, run deploy smoke, run `smoke-flyer-quality --real-model --allow-spend` once against the live/staging config if credentials/model are available, and record sample output paths/results.

## Acceptance Criteria

- Customer revision "change non-veg combo price from $14.99 to $16.99" changes the project facts before regeneration.
- Customer correction "this should be Thursday Dosa Night Special, not Weekend Breakfast" changes the project title/facts before regeneration.
- Generated concept/final assets fail closed if blank, wrong-sized, or suspiciously tiny.
- Generated model images have critical facts rendered server-side, so phone/date/time/venue/title/price text does not depend on image-model typography.
- A deterministic `smoke-flyer-quality` runs in deploy smoke without network cost.
- A real-model smoke can be run manually on `main-vps` only with `--allow-spend` and returns JSON diagnostics.
- Deployed `update-flyer-project` imports the shared workflow helper from an installed `/opt/shift-agent/flyer_workflow.py`; smoke fails closed if the module is missing.
- Deterministic deploy smoke writes only to a temporary `FLYER_STATE_ROOT` and cannot create root-owned production Flyer state.
- No new web UI is introduced.
