**Drift-check tag:** extends-Hermes

# Flyer Manual Source-Edit Finalization

## New primitives introduced

- One narrow renderer predicate for manually completed source-edit previews.
- No new messaging, routing, identity, approval, audit, state, or provider substrate.

## Hermes-first analysis

Hermes already owns WhatsApp ingress, identity, media transport, bridge delivery, audit/log conventions, runtime orchestration, and provider gateway posture. Flyer code already owns the deterministic final-package renderer, text manifest, visual QA sidecars, and manual queue completion state.

This slice is not a Hermes skill or prompt problem. The failure is inside Flyer renderer state interpretation: an operator-uploaded completion is valid as the approved preview, but the source-edit renderer now assumes source-edit previews always came from the recomposed model-edit path and therefore require a raw background sidecar.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Renderer/final asset packaging | none in Hermes Skills Hub (`https://hermes-agent.nousresearch.com/docs/skills`) | Keep in Flyer renderer; this is deterministic business logic. |
| Manual review state | Hermes substrate already provides routing/runtime; Flyer manual queue owns row completion | Extend existing Flyer manual completion contract only. |
| Messaging/approval | Hermes/Flyer bridge and `APPROVE` flow already exist | Do not add a parallel approval or delivery path. |
| Image/provider edit | Existing source-edit provider path exists for automated edits | Do not invoke any model for operator-approved assets. |

Awesome Hermes Agent ecosystem check: reviewed `https://github.com/0xNyk/awesome-hermes-agent`; no purpose-built Flyer final-package/manual-source-edit skill applies. Use existing in-tree Flyer code.

## Drift check

Read first:

- `src/agents/flyer/render.py`:
  - `_is_source_edit_project()` classifies explicit source-edit markers as source-edit.
  - `render_final_package()` requires a raw sidecar for source-edit projects before export.
  - Existing source-edit model previews write `.raw.png` and use `source_edit_overlay_recomposed`.
- `src/agents/flyer/manual_queue.py`:
  - `complete_manual_project()` creates an uploaded `concept_preview`, sets manual review to `completed`, and writes `source_edit_integrity_only` sidecars.
- `tests/test_flyer_renderer.py`:
  - Automated source-edit missing raw sidecar correctly fails closed.
  - No test covers manual-completed source-edit finalization.

## Problem

An operator can complete a queued source-edit by uploading a corrected preview. The customer can then approve it, but finalization still follows the automated source-edit branch and raises `source edit final package requires raw edited background sidecar`.

That is correct for automated source-edit previews, because their raw model edit is needed for per-format overlay recomposition. It is wrong for manual-completed operator assets, because the approved preview itself is the authoritative operator artifact and already has integrity-only sidecars.

## Plan

- [x] Add a failing renderer regression: source-edit marker + `manual_review.status == "completed"` + uploaded selected preview + no raw sidecar finalizes into all package formats.
- [x] Keep the existing automated source-edit missing-raw fail-closed test green.
- [x] Implement the smallest renderer predicate that treats manual-completed uploaded previews as direct-poster sources.
- [x] Ensure final manifests stay truthful: manual-completed finals use `source_edit_integrity_only`, not `source_edit_overlay_recomposed`.
- [x] Harden the bypass so the selected uploaded preview must be listed in `manual_review.operator_asset_ids`.
- [x] Run focused renderer tests.
- [x] Run multi-vector review.
- [x] Run broad/full verification before PR.
- [ ] Commit, PR, merge, tarball deploy, live verify.

## Verification

- RED: `python -m pytest tests/test_flyer_renderer.py::test_manual_completed_source_edit_final_package_uses_operator_preview_without_raw_sidecar -q` failed with `source edit final package requires raw edited background sidecar`.
- RED: `python -m pytest tests/test_flyer_renderer.py::test_manual_completed_source_edit_requires_selected_operator_asset_id_for_raw_sidecar_bypass -q` failed until the helper required an explicit operator asset id.
- GREEN focused: `python -m pytest tests/test_flyer_renderer.py::test_manual_completed_source_edit_final_package_uses_operator_preview_without_raw_sidecar tests/test_flyer_renderer.py::test_manual_completed_source_edit_requires_selected_operator_asset_id_for_raw_sidecar_bypass tests/test_flyer_renderer.py::test_source_edit_final_package_without_raw_sidecar_fails_closed -q` -> 3 passed.
- GREEN renderer: `python -m pytest tests/test_flyer_renderer.py -q` -> 92 passed.
- GREEN manual queue: `python -m pytest tests/test_flyer_manual_queue.py -q` -> 58 passed.
- GREEN routing: `python -m pytest tests/test_cf_router_flyer_routing.py -q` -> 315 passed.
- GREEN full suite: `python -m pytest -q` -> 2806 passed, 867 skipped, 40 warnings.

## Review result

- Claude structural review: no blocking findings; called out the empty `operator_asset_ids` widening risk.
- Claude Hermes/drift review: no blocking findings; no duplicated substrate.
- Internal safety review after tightening: no blocking findings; confirmed automated source-edits still fail closed without raw sidecar and manual operator finals use integrity-only manifest truth.

## Review focus

- Structural: Does the exception apply only to operator-completed source-edit assets, leaving automated source-edit raw-sidecar safety intact?
- Safety: Is the manifest truth correct, and does customer approval remain the visual/text gate for manual operator artifacts?
- Hermes/drift: Did the slice avoid any parallel manual-review, messaging, provider, or approval substrate?
