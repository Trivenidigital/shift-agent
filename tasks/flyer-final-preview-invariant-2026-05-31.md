**Drift-check tag:** extends-Hermes

# Flyer Final Preview Invariant - 2026-05-31

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Flyer final rendering | Existing Flyer renderer owns preview-to-final package export and text manifests. | Extend the existing `render_final_package` invariant; do not add a new rendering path. |
| Customer approval state | Existing Flyer project fields (`selected_concept_id`, `concepts`, `assets`) record the approved preview. | Treat a selected concept without a valid preview asset as fail-closed. |
| Runtime orchestration | Hermes triggers the existing finalization scripts. | Keep orchestration unchanged; only harden the render contract. |

Awesome Hermes Agent ecosystem check: this is a local Flyer rendering safety invariant, not an external skill capability. Verdict: extend Flyer renderer.

## Drift findings

- Source-edit finals already fail closed when no selected preview exists.
- Ordinary generated-flyer finals still fall back to `_render_model` when `selected_concept_id` is present but cannot be resolved to a valid preview asset.
- That fallback can create a final file the customer never saw or approved.

## Slice

- [x] Add RED renderer test: selected concept with missing/invalid preview must fail closed and never regenerate.
- [x] Harden `render_final_package` for all selected concepts, preserving the existing source-edit-specific error.
- [x] Run focused renderer tests, reviewer pass, and broad/full tests.
- [ ] PR, merge, deploy.

## Verification so far

- RED focused: `python -m pytest tests/test_flyer_renderer.py::test_final_package_with_selected_concept_missing_preview_fails_closed -q` -> failed, did not raise `FlyerRenderError`.
- GREEN focused: `python -m pytest tests/test_flyer_renderer.py::test_final_package_with_selected_concept_missing_preview_fails_closed tests/test_flyer_renderer.py::test_source_edit_final_package_without_selected_preview_fails_closed tests/test_flyer_renderer.py::test_final_package_exports_from_selected_generated_concept_without_new_model_call tests/test_flyer_renderer.py::test_final_package_reuses_selected_concept_with_deterministic_model -q` -> `4 passed`.
- Reviewer pass 1 found the existing `test_render_final_package_creates_expected_formats` encoded the unsafe selected-without-preview fallback; reviewer pass 2 found source-edit fail-closed narrowed when `selected_concept_id` was absent and requested invalid-preview coverage.
- Fixed review findings: canonical format test now exercises the valid no-selection direct-render path; source-edit guard is independent of `selected_concept_id`; added missing-preview, invalid-preview, and source-edit-without-selection tests.
- GREEN post-review focused: `python -m pytest tests/test_flyer_renderer.py::test_render_final_package_creates_expected_formats tests/test_flyer_renderer.py::test_final_package_with_selected_concept_missing_preview_fails_closed tests/test_flyer_renderer.py::test_final_package_with_selected_concept_invalid_preview_fails_closed tests/test_flyer_renderer.py::test_source_edit_final_package_without_selection_fails_closed tests/test_flyer_renderer.py::test_source_edit_final_package_without_selected_preview_fails_closed tests/test_flyer_renderer.py::test_final_package_exports_from_selected_generated_concept_without_new_model_call tests/test_flyer_renderer.py::test_final_package_reuses_selected_concept_with_deterministic_model -q` -> `7 passed`.
- GREEN renderer file after assertion tightening: `python -m pytest tests/test_flyer_renderer.py -q` -> `90 passed`.
- GREEN full suite: `python -m pytest -q` -> `2799 passed, 867 skipped, 40 warnings`.
- Diff hygiene: `git diff --check` -> clean.
