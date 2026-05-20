**Drift-check tag:** extends-Hermes

# Flyer Source-Edit Provider Config Plan

Date: 2026-05-20

## Goal

Move exact uploaded-flyer source edits off the direct-OpenAI-only runtime gate so they can use the configured Flyer provider path, primarily OpenRouter via `OPENROUTER_API_KEY`. Keep customer copy, manual queue UX, admin controls, and draft/final provider policy unchanged. The only dashboard-adjacent change allowed is tiny health/readiness wording so operators do not see stale `openai_source_edit` posture.

This plan intentionally supersedes the older `docs/runbooks/flyer-model-policy.md` source-edit boundary that deferred all OpenRouter source-edit work until after a regression dataset. The narrow posture for this PR is: wire the provider path and offline fail-closed tests now, do not deploy, and require spend-gated real source-edit smoke before any customer-grade operational reliance.

## New primitives introduced

- `source_edit_provider_policy` schema block for Flyer source-edit provider intent.
- `resolve_source_edit_render_provider()` helper on `FlyerConfig`.
- OpenRouter source-edit renderer dispatch that reuses the existing source-edit prompt and fail-closed `FlyerRenderError` behavior.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp/source media ingestion | Yes - existing Hermes bridge and Flyer project assets already store uploaded reference images | Use existing project asset path; no new ingestion code |
| Provider credential substrate | Yes - existing Hermes/OpenRouter env path and Flyer `_read_env_value` chain | Reuse env lookup; do not introduce a new secret store |
| Image generation provider routing | Partial - Hermes supports image generation and OpenRouter, but Flyer source-edit has source-preservation, QA, and manual-review semantics | Extend Flyer-side dispatch; do not move to broad Hermes routing |
| Source-preserving edit QA/manual fallback | None generic enough; existing Flyer logic owns source contracts and manual queue reason taxonomy | Keep Flyer fail-closed behavior and typed manual-review mapping |

Awesome Hermes ecosystem check: current ecosystem has image generation and OpenRouter integrations, but no drop-in source-preserving uploaded-flyer edit skill with Flyer Studio state, audit, and manual-review semantics, so this remains Flyer-side code built on Hermes substrate.

## Drift Findings

- `docs/runbooks/flyer-model-policy.md` says PR #144 intentionally wired draft/final provider policy only and kept source-preserving edits on direct OpenAI pending a later PR.
- `src/platform/schemas.py` has `draft_provider_policy` and `final_provider_policy`, but no `source_edit_provider_policy`; `edit_image_model` remains the direct OpenAI-era legacy field.
- `src/agents/flyer/workflow.py::source_edit_provider_ready(...)` hardcodes `OPENAI_API_KEY` readiness.
- `src/agents/flyer/render.py::render_source_edit_preview(...)` calls `_openai_source_edit_bytes(...)`, which hardcodes `OPENAI_API_KEY`.
- `src/agents/flyer/scripts/generate-flyer-concepts` source-edit branch passes `cfg.flyer.edit_image_model` and `edit_image_quality` directly to `render_source_edit_preview(...)`.
- `src/plugins/cf-router/actions.py::flyer_source_edit_preflight(...)` calls `source_edit_provider_ready(project)` and maps readiness failures to `source_edit_provider_unavailable`.
- Deployed `main-vps` code still contains `/opt/shift-agent/flyer_render.py` direct OpenAI source-edit references, including `OPENAI_IMAGE_EDIT_URL` and `OPENAI_API_KEY`.

## Scope

In scope:

- Add source-edit provider policy/config resolution.
- Allow source-edit readiness to pass when an OpenRouter source-edit provider is configured and `OPENROUTER_API_KEY` is present.
- Dispatch source-edit rendering through OpenRouter when configured.
- Preserve direct OpenAI rendering only when explicitly configured or explicitly requested as the provider.
- Preserve manual-review fail-closed behavior for missing/placeholder keys, unsupported provider, HTTP/timeout failures, invalid responses, and unsupported remote URL-only responses.
- Update focused offline tests.

Out of scope:

- Customer copy changes.
- Dashboard frontend/backend controls. Health/readiness label updates are allowed only to avoid contradictory provider status.
- Draft/final generation policy changes.
- Manual queue UX changes.
- Real API calls in tests.
- Deploy or merge.
- VPS/customer/manual queue mutation or WhatsApp sends.

## Implementation Steps

1. Add schema support:
   - Add `FlyerSourceEditProviderPolicy` with `default` OpenRouter provider using `openai/gpt-5.4-image-2` at `high` quality.
   - Add `source_edit_provider_policy` to `FlyerConfig`.
   - Add `resolve_source_edit_render_provider()`.
   - Keep legacy `edit_image_model` for direct OpenAI compatibility and backward config visibility.
   - Resolver precedence:
     - If `source_edit_provider_policy` is explicitly present in config, use `source_edit_provider_policy.default`.
     - Else if legacy `edit_image_model` or `edit_image_quality` is explicitly present in config, preserve legacy direct OpenAI behavior with those fields.
     - Else use the `manual_review` sentinel. OpenRouter becomes active only when `source_edit_provider_policy` is explicitly present in config; this avoids accidental provider traffic from old callers or bare schema defaults.

2. Update readiness:
   - Add a small provider-target input to `source_edit_provider_ready(...)`.
   - Check `OPENROUTER_API_KEY` for OpenRouter, `OPENAI_API_KEY` for direct OpenAI.
   - Align renderer env lookup with workflow env lookup: process env, then Hermes env (`HERMES_ENV_PATH` or `/root/.hermes/.env`), then agent env (`SHIFT_AGENT_ENV_PATH` or `/opt/shift-agent/.env`).
   - Return provider-specific details such as `source edit provider configured: openrouter/openai/gpt-5.4-image-2` and `source edit provider is not configured: OPENROUTER_API_KEY missing`.
   - Keep reference-image and MIME checks unchanged.

3. Update router preflight:
   - Resolve source-edit provider from `config.yaml` when available using platform schema loading.
   - Fail closed on config read/parse/schema errors. Do not fall back to defaults after malformed or unreadable production config, because generation will later load the same config strictly.
   - Use `FlyerConfig().resolve_source_edit_render_provider()` only inside isolated unit seams where no config path is available by construction; default result is manual review, not OpenRouter.
   - Keep reason-code mapping unchanged.

4. Update renderer:
   - Add `_openrouter_source_edit_bytes(...)` using OpenRouter chat/completions image output style, with source image data URL and existing `_source_edit_prompt(...)`.
   - Dispatch in `render_source_edit_preview(...)` by provider.
   - Keep `_openai_source_edit_bytes(...)` for explicit direct OpenAI provider.
   - Fail closed with `FlyerRenderError` for missing keys, unsupported providers, HTTP/connection failures, invalid JSON/shape, and non-data-URL image responses.

5. Update generation script:
   - Use `cfg.flyer.resolve_source_edit_render_provider()` in the source-edit branch.
   - Pass provider/model/quality to `render_source_edit_preview(...)`.
   - Keep manual-review reason-code mapping, adding OpenRouter wording to the provider-unavailable classifier if needed.

6. Tests first:
   - Schema defaults and resolver tests.
   - Workflow readiness tests for OpenRouter-present/OpenAI-absent pass, OpenRouter missing/placeholder fail, and explicit OpenAI provider still requiring OpenAI key.
   - Router preflight test for OpenRouter-configured readiness.
   - Renderer tests for OpenRouter request payload and success response, HTTP/connection failure, invalid response, remote URL-only failure, and explicit OpenAI compatibility.
   - Script/static or generation test proving source-edit branch uses resolver.
   - Hook/router tests or focused static assertions proving the three preflight consumers still thread the dynamic reason-code triad and do not change customer copy/manual queue UX.
   - Health tests proving the provider block is named `source_edit_provider` and reports manual-review / configured-provider posture without leaking secrets.

7. Verification:
   - Red run after writing tests and before implementation.
   - Focused tests:
     - `python -m pytest tests/test_flyer_source_edit_preflight.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py tests/test_flyer_schemas.py -q`
     - `python -m pytest tests/ -k "flyer and source_edit" -q`
     - `python -m pytest tests/test_flyer_generate_concepts.py tests/test_cf_router_flyer_routing.py -k "source_edit or preflight" -q`
   - `python -m py_compile` for touched Python files.
   - `git diff --check`.

## Review Gates

- Plan review: two parallel reviewers before design.
- Design review: two parallel reviewers before build.
- Final code review: at least one reviewer after implementation, before PR.

## Risks

- OpenRouter image output API may not provide true source-preserving edits for this model. The PR only removes the direct OpenAI credential gate; customer-grade reliance still needs spend-gated real smoke.
- OpenRouter may return remote image URLs instead of data URLs. This PR should fail closed unless safe remote retrieval is explicitly designed.
- Router preflight must not silently become permissive when config cannot be loaded. Production config load/validation errors fail closed.
- A valid OpenRouter image response can still be a poor source-preserving edit. This PR does not claim source fidelity; it must not be deployed or relied on operationally until the deferred smoke/regression gate is run.

## Deferred

- Spend-gated real OpenRouter source-edit smoke on main-vps or staging with 5-10 real F-series cases.
- Operator SLA/alert for manual source-edit queue older than 5-10 minutes.
- Visual QA/source-contract hardening for source-preserving edits.
- Possible future direct GPT Image 2 fallback if OpenRouter source-edit fidelity is not good enough.
