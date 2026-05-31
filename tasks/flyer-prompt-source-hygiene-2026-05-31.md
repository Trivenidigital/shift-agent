**Drift-check tag:** extends-Hermes

# Flyer Prompt Source Hygiene - 2026-05-31

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Semantic intake/facts | Existing Flyer/Hermes semantic brief and `locked_facts` already capture validated customer facts. | Use existing facts; do not add a new extractor. |
| Prompt assembly | Flyer render code already owns `_image_prompt`, campaign-scene selection, deterministic overlay, and QA. | Keep prompt assembly in Flyer code; change only its context source. |
| Customer messaging/audit | Existing cf-router/bridge/action-context substrate owns WhatsApp sends and audit. | No messaging or audit change in this slice. |
| Hermes skills hub | `https://hermes-agent.nousresearch.com/docs/skills` currently exposes no flyer prompt-source primitive. | No external skill applies. |

Awesome Hermes Agent ecosystem check: `awesome-hermes-agent` does not provide a drop-in production flyer prompt-source hygiene primitive. Verdict: extend in-tree Flyer render logic using existing `locked_facts`.

## Drift findings

- `_poster_copy_block` already uses locked facts for controlled customer copy. Do not rebuild that.
- `render_source_edit_preview` and source-edit overlay are already merged. Do not rebuild that.
- Residual gap: campaign-scene and visual category selection still use `_category_context`, which includes `raw_request` and `fields.notes`. Customer instructions such as "no food or festival visuals" can therefore trip positive scene selectors such as `family_discovery`.

## Slice

- [x] Add RED test: locked/semantic facts should drive scene selection over raw negative instructions.
- [x] Add a prompt-selection context helper that prefers registered category + locked facts and falls back to raw context only for legacy projects without structured facts/category.
- [x] Wire campaign-scene, design-direction, and food/grocery checks to the prompt-selection context.
- [x] Preserve source-edit requested-edit prompt and reference-extraction detection, where raw text is the actual task.
- [x] Run focused tests, reviewer pass, and broad tests.
- [ ] PR, merge, deploy.

## Verification so far

- RED: `python -m pytest tests/test_flyer_campaign_scene_prompts.py::test_image_prompt_scene_uses_locked_facts_over_negative_raw_instruction_terms -q` failed because `family_discovery` was selected from raw negative terms.
- Reviewer fix RED: `python -m pytest tests/test_flyer_campaign_scene_prompts.py::test_image_prompt_scene_keeps_positive_style_preference_with_locked_facts -q` failed before `style_preference` joined the structured visual context.
- Reviewer fix RED: `python -m pytest tests/test_flyer_campaign_scene_prompts.py::test_image_prompt_scene_ignores_negated_style_preference_terms -q` failed before negated style clauses were stripped from deterministic scene selection.
- Reviewer fix RED: `python -m pytest tests/test_flyer_campaign_scene_prompts.py::test_image_prompt_scene_preserves_positive_style_after_negated_subclause -q` failed before negation stripping was narrowed to sub-clauses.
- GREEN focused: `python -m pytest tests/test_flyer_campaign_scene_prompts.py tests/test_flyer_renderer.py tests/test_flyer_customer_copy_policy.py -q` -> `128 passed`.
- Diff hygiene: `git diff --check` -> clean.
- Flyer-focused gate: all `tests/*flyer*` -> `1430 passed, 1 skipped, 40 warnings`.
- Full suite: `python -m pytest -q` -> `2784 passed, 867 skipped, 40 warnings`.

## Review

- Claude customer-output review found positive `style_preference` suppression; fixed with a RED/GREEN test.
- Internal customer-output reviewer found negated `style_preference` poisoning and then over-stripping of later positive clauses; both fixed with RED/GREEN tests.
- Internal Hermes/drift reviewer: APPROVE, no substrate duplication and `_category_context` remains for extraction/raw paths.
- Internal render integration reviewer: APPROVE, helper reaches prompt visual levers without touching source-edit/reference-source paths.
