# Slice 1 Pre-flight Results (captured 2026-06-14, main-vps)

Task 0 of `plans/2026-06-14-flyer-arch-a-slice1.md`. Runtime values that anchor the migration.

## Current prod render config (`/opt/shift-agent/config.yaml`)
```
draft_image_model: google/gemini-2.5-flash-image
draft_image_quality: high
final_image_model: deterministic-renderer      # <-- delivered final is flat Pillow
final_image_quality: high
concept_count: 1
```
**Implication:** the *final* customers receive is deterministic-rendered today — a primary cause of gen.png flatness. Migration must set BOTH `draft_image_model` and `final_image_model` → `google/gemini-3.1-flash-image-preview`.

## Flags (env)
- `FLYER_BARE_SKIP_VISUAL_QA=1` — referee currently DISABLED on bare/integrated path. Task 4 orchestration must call `run_visual_qa` directly so this flag cannot suppress the referee for integrated output.
- `FLYER_ALLOW_INTEGRATED_POSTER=1` — already on; Task 2 widens eligibility.
- `FLYER_INTEGRATED_KILLSWITCH` — not set (new in Slice 1).
- `FLYER_VISUAL_QA_MODEL` / `FLYER_REGIONAL_QA_MODEL` — not set (defaults apply; Task 6 adds regional default).

## Credentials
- `OPENROUTER_API_KEY` present in `/root/.hermes/.env` and `/opt/shift-agent/.env`. `google/gemini-3.1-flash-image-preview` confirmed available (OpenRouter models list).
- `OPENAI_API_KEY` ABSENT → see `2026-06-14-openai-key-gpt-image-source-edit-degradation.md` (separate track; A unaffected).

## Kill-switch byte-identical baseline
Target = `deterministic-renderer` output (== today's `final_image_model`). Task 5 captures the baseline hash and asserts kill-switch reproduces it.
