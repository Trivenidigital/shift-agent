# Flyer Quality Phase 2 Design

**Drift-check tag:** extends-Hermes

**Status:** ready for design review

**Goal:** Harden Flyer Studio for paying SMB/customer use by making real-model flyer outputs visually useful while server-side code owns exact customer facts, revision application, deploy smoke, and rollback safety.

**New primitives introduced:** rendered asset quality inspection report, critical-text overlay/compositor, structured revision patch result, template/reference preservation prompt policy, isolated quality smoke CLI.

## Hermes-First Analysis

| Domain | Hermes skill/tool found? | Decision |
|---|---|---|
| WhatsApp ingress/delivery | yes - deployed Hermes gateway, cf-router, bridge `/send` and `/send-media` | use it unchanged |
| Image generation | yes - Hermes has `image_generate`/Tool Gateway image models, but the live Flyer path is script-level OpenRouter config | keep OpenRouter renderer path in Phase 2; add quality/composition around it |
| Vision/OCR | yes - `vision_analyze` and `productivity/ocr-and-documents` are available | defer OCR exact-field QA; render critical facts server-side now |
| Media/reference storage | yes - Hermes media cache plus Flyer project/customer assets | reuse existing asset models and state |
| State/audit | yes - JSON state with `FileLock`/`atomic_write_text` | reuse; do not introduce DB/schema migration |
| Payment/onboarding | yes - Phase 1 account/quota layer | unchanged |

Awesome Hermes Agent ecosystem check: no complete WhatsApp flyer studio quality/revision workflow exists. Hermes supplies substrate; SMB flyer correctness remains product logic.

## Design

### 1. Render Quality Gate

Add `RenderedAssetQuality` and `inspect_rendered_asset(path, expected_width, expected_height, mime_type)` in `src/agents/flyer/render.py`.

Checks:
- file exists and is above minimum size
- PNG opens with Pillow and matches expected dimensions
- PNG is not visually blank: resized sample has meaningful variance/unique colors
- PDF is at least large enough and starts with `%PDF`

`render_concept_previews` and `render_final_package` call the inspector after each artifact is written. Any failed artifact raises `FlyerRenderError`; scripts then fail closed before updating project state.

### 2. Critical Text Overlay

Add `apply_critical_text_overlay(project, source, target, size, output_format)` in `render.py`.

For model-generated PNGs, the flow becomes:
1. receive raw model image
2. save it as an internal sibling raw background, for example `F0001-C1-preview.raw.png`
3. crop/resize/export from the raw background to target dimensions if needed
4. overlay a polished facts panel with server-rendered text:
   - title/offer
   - date/schedule
   - time
   - venue/address
   - contact phone
   - compact menu/price/details lines from `fields.notes` or `raw_request`
5. inspect the final customer-facing artifact

Deterministic renderer already draws exact facts in code, so it does not need a second overlay. Final package exports from the selected raw sibling when present, not from an already-overlaid preview. Each output format gets exactly one overlay after its own crop/resize; this prevents the WhatsApp bottom panel from being cropped into Instagram square/story/PDF formats. PDF exports are created from the final overlaid image so print also carries server-rendered critical facts.

This follows `flyer_generation/SKILL.md`: image generation is for the visual design; critical text belongs to the compositor.

The image prompt must no longer ask the model to render exact critical facts as readable flyer copy. It should ask for a polished visual/background with space for an overlay panel. For uploaded reference/template images, the model should preserve layout feel, food/cultural imagery, logo identity, and offer category while neutralizing stale readable facts where possible. Phase 2 does not claim full OCR contradiction detection; real-model smoke keeps sample outputs for operator review.

### 3. Revision Patch Semantics

Move revision extraction into shared `workflow.py`, installed as `/opt/shift-agent/flyer_workflow.py`.

Add:
- `RevisionPatchResult`
- `extract_revision_patch(project, text)`
- compatibility wrapper `extract_revision_field_updates(project, text)`

Patch result fields:
- `field_updates`
- `notes_update`
- `raw_request_update`
- `changed`
- `visual_only`
- `ambiguous`
- `unresolved_reason`

High-confidence supported edits:
- date/time
- phone/contact
- venue/location
- title/offer corrections: `from X to Y`, `not X, Y`, `should be Y, not X`
- price replacements, but repeated old prices are ambiguous unless nearby item context is present

`FlyerRevision` is `extra="forbid"` and has no metadata field, so Phase 2 does not add persistent revision metadata. To stay rollback-safe, `update-flyer-project` returns the structured patch result on stdout for operators/tests, and persists only schema-compatible state:
- structured or visual-only revisions get `resulting_version` set to the new project version while `applied=False`
- unresolved/ambiguous revisions keep `resulting_version=None` and `applied=False`
- `generate-flyer-concepts` marks a revision applied only after rendering succeeds and `revision.resulting_version == project.version`

This uses the existing optional `resulting_version` as an acceptance marker without adding fields that older rollback binaries would reject. Ambiguous unresolved text revisions remain unapplied and block approval until clarified. Visual-only revisions can be regenerated and then marked applied by `generate-flyer-concepts`.

`generate-flyer-concepts` should not hold the `FileLock` across an OpenRouter image call. It loads a project snapshot under lock, renders outside the lock, then re-locks, verifies the project version/status still match the snapshot, and commits concepts/assets/revision-applied flags. If the state changed while rendering, it fails closed without overwriting newer customer changes.

The active WhatsApp revision path in `cf-router/hooks.py` must parse the update script JSON/stdout. If `ok=false` or the patch result says `ambiguous=true`/`changed=false` for a text correction, the customer receives a clarification message that includes the unresolved reason. It must not send "Revision noted" for a failed or unresolved correction.

`generate-flyer-concepts` marks a revision applied only after rendering succeeds and either the patch changed structured facts or the revision is visual-only.

### 4. Template/Reference Policy

`_reference_preservation_instruction(project)` strengthens prompts when brand/project references exist:
- use uploaded template/reference as source of truth
- do not redesign from scratch
- preserve offer category, layout intent, logo/business identity, and unchanged prices
- latest revision facts override older text

Attachment ordering changes to always include the latest project reference/template even when customer brand assets exist.

### 5. Quality Smoke CLI

Create `src/agents/flyer/scripts/smoke-flyer-quality`.

Deterministic mode:
- creates a temporary state root
- sets `FLYER_STATE_ROOT` to that root
- sets render/customer asset paths after `FLYER_STATE_ROOT` is established so customer lookup, project assets, generated assets, and final assets all remain temp-rooted
- renders one project with `deterministic-renderer`
- inspects the asset
- cleans up unless `--keep-output`
- prints JSON

Real-model mode:
- requires `--real-model --allow-spend`
- reads `Config.flyer`
- forces one concept
- renders with configured model/quality
- prints JSON diagnostics: model, quality, concept_count, output paths, dimensions, file sizes

Deploy smoke runs deterministic mode as `shift-agent`; it never runs real-model mode. Runtime operator smoke can run the real model once after deploy.

### 6. Deploy Packaging

Deploy script installs:
- `/opt/shift-agent/flyer_render.py`
- `/opt/shift-agent/flyer_workflow.py`
- `/usr/local/bin/smoke-flyer-quality`

Smoke imports `flyer_render`, `flyer_workflow`, `flyer_onboarding`, and `flyer_account`. Rollback/stale cleanup removes `flyer_workflow.py` and `smoke-flyer-quality` when absent from old tarballs. Cleanup is per-file/per-binary, not only "scripts directory absent", because old rollback tarballs can contain Flyer scripts while lacking this specific new binary.

`flyer_render` must continue importing cleanly when Pillow is absent from the Hermes venv. New overlay/inspection helpers use lazy Pillow loading and the existing `/usr/bin/python3` system-Pillow fallback where needed.

## Tests

- Renderer tests: quality accept/reject, overlay text changes pixels, PDF minimal parse, selected-preview final exports still avoid second image-model spend.
- Workflow tests: date/time, phone, venue, title, price, repeated-price ambiguity, old-text-not-found.
- Static script tests: deployed workflow import, smoke CLI flags/JSON, non-root/temp-state deploy smoke, rollback cleanup.
- Static script tests: no OpenRouter call while holding the project `FileLock`; deployed workflow import; smoke CLI flags/JSON; non-root/temp-state deploy smoke; per-binary rollback cleanup.
- Existing focused Flyer tests remain green.

## Risks

- Overlay may reduce the artistic feel of some model outputs. The panel must be compact and polished, but correctness wins over pure image-model typography.
- OCR/vision QA remains future work. Phase 2 avoids the biggest failure mode by not trusting the model for exact facts.
- Real-model smoke costs money. It is mechanically guarded by `--allow-spend` and never runs from deploy smoke.
