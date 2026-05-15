# Flyer Text QA Phase 3 Plan

**Drift-check tag:** extends-Hermes

**Goal:** Prevent customer-facing Flyer Studio sends when generated or revised assets do not carry the current canonical flyer facts. Phase 3 adds a deterministic text manifest and release gate around the existing server-side compositor, then enforces it at render, preview-send, finalization, final-send, and smoke chokepoints.

**New primitives introduced:** Flyer text QA helper, overlay text sidecar manifest, final-package text gate, smoke output for text QA.

**Hermes-first analysis**

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress and delivery | yes - deployed Hermes gateway/cf-router plus bridge media delivery | use it |
| Vision/image handling | yes - Hermes docs describe native vision/image paste and image generation; bundled toolsets include `vision` and `image_gen` ([vision docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/vision/), [Hermes Agent skill](https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent)) | use existing renderer path; do not add a separate gateway integration |
| OCR/document skills | yes - bundled catalog includes `productivity/ocr-and-documents` for scanned/PDF extraction ([skills catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog/)) | keep OCR/vision adapter optional; build deterministic manifest gate now |
| Skills/memory substrate | yes - Hermes skills system is the source of reusable procedural behavior ([skills docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/)) | update Flyer skill contract only if behavior changes |
| Flyer-specific exact-fact QA | none found | build local deterministic QA because exact field matching is product-specific |

Awesome Hermes Agent ecosystem check: searched for ready-made flyer/OCR/WhatsApp flyer QA skills; no purpose-built Flyer Studio quality gate was identified. Phase 3 stays local product logic on Hermes substrate.

## Drift Grounding

- Renderer already owns exact text via `_critical_lines()` and `apply_critical_text_overlay()` in `src/agents/flyer/render.py`.
- Phase 2 deliberately deferred OCR/vision QA in `docs/superpowers/specs/2026-05-15-flyer-quality-phase2-design.md`.
- Finalization currently renders assets and persists them without a separate text manifest gate in `src/agents/flyer/scripts/finalize-flyer-assets`.
- Deploy already installs `flyer_render.py`, `flyer_workflow.py`, Flyer scripts, and runs `smoke-flyer-quality`.

## Implementation Steps

- [ ] Plan review by two parallel agents: one production/reliability lens, one Hermes/drift lens.
- [ ] Add focused tests for text manifest creation, stale field detection, and final smoke text-QA output.
- [ ] Add deterministic canonical fact collection and sidecar manifest validation in `src/agents/flyer/render.py` unless the design proves a separate runtime helper is cleaner.
- [ ] Teach `render.py` to write a `.text.json` sidecar for every concept and final artifact, including deterministic renders and PDF exports.
- [ ] Store `expected_facts` and `rendered_facts` separately, with manifest metadata for `project_id`, `project.version`, `selected_concept_id`, output format, source hash when present, output hash, and validation result.
- [ ] Gate `render_concept_previews()`, `render_final_package()`, `smoke-flyer-quality`, concept preview sending, and final package sending on the sidecar manifest. Direct `--asset` sends require a valid sidecar unless an explicit operator-only bypass is passed.
- [ ] Install any new runtime module/script through `shift-agent-deploy.sh`; add smoke import coverage and rollback cleanup if a separate helper is introduced.
- [ ] Run local focused tests, py_compile, diff check, and deterministic smoke.
- [ ] Create PR, run three parallel reviewers with code, product, and deploy/runtime vectors.
- [ ] Apply review fixes, merge, deploy to `main-vps`, and run deterministic plus real-model smoke without regenerating finals unnecessarily.

## Acceptance Criteria

- A flyer revision that changes date/time/price/title updates the manifest; stale-version manifests fail.
- A long menu/price-list flyer keeps bounded price-bearing facts, schedule, phone, and location in the manifest or fails closed before send.
- A stale or missing critical fact blocks preview send, finalization, and final WhatsApp media delivery.
- `smoke-flyer-quality` reports both pixel quality and text QA status and can exercise deterministic final package generation.
- Production deploy smoke still passes without real-model spend.
- The real-model smoke keeps one preview artifact for operator review and confirms the overlay manifest matches canonical facts.

## Risks

- OCR can be flaky on stylized posters. Phase 3 does not depend on OCR for the release gate; it validates the exact server-rendered text manifest. OCR/vision extraction remains an optional future cross-check.
- Long menu flyers may exceed overlay capacity. If bounded canonical details are omitted, rendering should fail rather than silently send an incomplete package.
- PDF text cannot be OCR-checked cheaply without more dependencies. Phase 3 writes a durable sidecar beside the PDF derived from the same canonical manifest and still performs existing PDF header/size checks.

## Plan Review Fixes

- Production reliability review added the send-path requirement: preview and final media sends must validate manifests, and final sends cannot rely only on `finalize-flyer-assets`.
- Hermes/drift review added deterministic-renderer coverage: manifest creation must be centralized so deploy smoke and real-model paths share the same canonical fact source.
- Both reviews added PDF sidecar and version-binding requirements so temporary overlay images do not become invisible proof gaps.
