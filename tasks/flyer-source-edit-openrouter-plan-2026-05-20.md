**Drift-check tag:** extends-Hermes

# Flyer Source-Edit OpenRouter Migration Plan - 2026-05-20

## Goal

Route Flyer Studio exact source-preserving edits through OpenRouter instead of requiring a separate direct `OPENAI_API_KEY`.

The operator should be able to configure the desired OpenAI image model *inside OpenRouter* (for example `openai/gpt-image-1`) and keep `OPENROUTER_API_KEY` as the single production provider key.

## Drift Check

Read first:

| Surface | Evidence | Decision |
|---|---|---|
| Source-edit readiness | `src/agents/flyer/workflow.py::source_edit_provider_ready` currently checks `OPENAI_API_KEY`. | Change to OpenRouter key/model posture. |
| Source-edit render | `src/agents/flyer/render.py::_openai_source_edit_bytes` posts multipart to OpenAI Images Edits. | Replace runtime path with OpenRouter image-output call using source image input. |
| Cockpit health | `web/backend/app/routers/flyer.py::_flyer_provider_components` renders `openai_source_edit`. | Rename/update posture to OpenRouter source edit. |
| Frontend health | `web/frontend/src/sections/FlyerAdmin.tsx` labels "OpenAI - exact source edits". | Update copy to "Source edits - OpenRouter". |
| Config | `FlyerConfig.edit_image_model` default is direct `gpt-image-1`. | Default to OpenRouter model id `openai/gpt-image-1`. |

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Provider routing / single key | Hermes already uses OpenRouter as the gateway provider for generation/vision. | Reuse `OPENROUTER_API_KEY` and current layered env lookup. |
| Image generation with image input | OpenRouter supports image generation via chat/responses endpoints, image output modalities, and image-to-image style inputs for compatible models. | Use OpenRouter chat completions image-output contract for source-edit proof path. |
| Direct source-edit policy | No Hermes-native Flyer-specific exact-edit contract exists. | Keep Flyer-specific source-preservation prompt, QA, and manual fallback. |
| Manual fallback | Existing Flyer manual queue and reason code `source_edit_provider_unavailable`. | Preserve fail-closed manual review when OpenRouter key/model is unavailable or API shape fails. |

Verdict: **extends-Hermes**. Provider credentialing moves to Hermes/OpenRouter; Flyer keeps the exact-edit policy and QA contract.

## Scope

1. `workflow.py`
   - `source_edit_provider_ready` requires `OPENROUTER_API_KEY`, not `OPENAI_API_KEY`.
   - Detail strings mention OpenRouter source-edit provider.

2. `render.py`
   - Replace direct OpenAI image edit runtime call with OpenRouter chat completions image-output request.
   - Include source image as a data URL in the message content.
   - Use `modalities: ["image", "text"]`.
   - Decode `message.images[0].image_url.url`.
   - Keep fail-closed errors and existing source-edit integrity-only manifest.
   - Keep a compatibility wrapper name only if tests/imports still reference it, but it must use OpenRouter.

3. Config / Health / UI
   - `FlyerConfig.edit_image_model` default becomes `openai/gpt-image-1`.
   - Cockpit backend exposes provider name `openrouter_source_edit`.
   - Frontend renders "Source edits - OpenRouter" and no longer instructs provisioning `OPENAI_API_KEY`.
   - Credential readiness no longer treats `OPENAI_API_KEY` as required for Flyer source edits.

4. Tests
   - Source-edit preflight passes with only `OPENROUTER_API_KEY`.
   - Source-edit preflight fails when OpenRouter key is missing/placeholder.
   - Renderer sends OpenRouter JSON request, not OpenAI multipart.
   - Health panel backend reports `openrouter_source_edit`.
   - Frontend static text no longer shows "OpenAI - exact source edits" or `OPENAI_API_KEY` as the next action.

## Out of Scope

- Execute real spend/API calls in CI.
- Claim OpenRouter source edits are production-quality before real-model proof smoke.
- Remove old OpenAI-related historical docs.
- Add execute-mode provider migration on VPS; deploy remains normal PR/tarball flow.

## Acceptance

- Focused Flyer workflow/render/source-edit/health tests pass.
- Frontend typecheck/build pass if frontend copy changes.
- `py_compile` passes for touched Python files.
- `git diff --check` passes.
