**Drift-check tag:** extends-Hermes

# Flyer Studio - Source-Edit Provider Posture

## Current Decision

Flyer Studio source-preserving edits now use **OpenRouter** as the single production image provider key. The intended OpenAI image-edit model is configured through OpenRouter as `flyer.edit_image_model` (default: `openai/gpt-image-1`).

This supersedes the earlier two-key posture where generation/vision used `OPENROUTER_API_KEY` and exact source edits required a separate direct `OPENAI_API_KEY`.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Image generation / vision gateway | yes - Hermes/OpenRouter gateway already backs Flyer generation and vision | Reuse `OPENROUTER_API_KEY` and the existing OpenRouter chat-completions image-output shape. |
| Source-edit policy and locked-fact contract | none found for Flyer-specific exact-edit policy | Keep Flyer-specific source-preservation prompt, manual-review fallback, and visual QA semantics. |
| Provider health surfacing | none found for Flyer cockpit posture | Expose source edits as `openrouter_source_edit` in the Flyer health endpoint and cockpit panel. |

Awesome-Hermes-agent ecosystem check: no existing fleet/Flyer-specific source-edit provider policy was found; this remains an `extends-Hermes` Flyer integration.

## Runtime Shape

| Flyer Studio operation | Provider key | Model field |
|---|---|---|
| Draft generation | `OPENROUTER_API_KEY` | `flyer.draft_image_model` |
| Final generation | `OPENROUTER_API_KEY` | `flyer.final_image_model` |
| Vision / OCR QA | `OPENROUTER_API_KEY` | Hermes/OpenRouter vision config |
| Exact source-preserving edit | `OPENROUTER_API_KEY` | `flyer.edit_image_model` |

## Operational Semantics

- Missing or placeholder `OPENROUTER_API_KEY` still fail-closes source edits into `manual_edit_required` with `reason_code="source_edit_provider_unavailable"`.
- Cockpit health reports normal generation/vision as `openrouter_generation_vision` and exact source edits as `openrouter_source_edit`.
- Credential readiness no longer treats `OPENAI_API_KEY` as a Flyer requirement.
- Operators should configure the desired OpenAI image model inside OpenRouter rather than provisioning a second direct provider key.

## Follow-Ups

- Run a spend-gated real source-edit smoke on the VPS before declaring the OpenRouter edit lane customer-grade.
- Add a real-customer exact-edit golden scenario that verifies source layout preservation, not just required text presence.
- Revisit source-edit fidelity if OpenRouter changes image-input/image-output behavior for `openai/gpt-image-1` or equivalent models.
