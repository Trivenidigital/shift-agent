# Flyer Studio Model Policy

Last updated: 2026-05-20

## Rollout Decision

For the customer rollout window, Flyer Studio uses one production image-provider account: OpenRouter. Ideogram is not part of the rollout because it requires a separate key/subscription and is not available through OpenRouter.

Source-preserving uploaded-flyer edits are now being wired for provider configuration in the follow-up PR after PR #144. The code path may use OpenRouter when explicitly resolved by `source_edit_provider_policy`, but it must not be deployed or treated as customer-grade until spend-gated real source-edit smoke proves source preservation on real Flyer Studio cases.

## PR Sequence

- PR-1 before rollout: wire `draft_provider_policy` and `final_provider_policy`, add policy docs, add admin-dashboard backlog, and keep source-edit path unchanged. Status: done in PR #144 and deployed to `main-vps`.
- PR-2 before relying on automated source edits operationally: source-edit model/provider wiring plus offline fail-closed tests. No deploy until a spend-gated real source-edit smoke verifies provider capability and source preservation.
- PR-3 after bakeoff: optional Ideogram provider and admin-dashboard controls only if the bakeoff justifies the added key/subscription and operational complexity.

## Production Defaults

| Situation | Provider | Model | Quality | Runtime status |
|---|---|---|---|---|
| New flyer draft, default | OpenRouter | `openai/gpt-5.4-image-2` | `high` | wired |
| Final asset, default | local | `deterministic-renderer` | `high` | wired |
| Final fallback | OpenRouter | `openai/gpt-5.4-image-2` | `high` | config only |
| Source-preserving edit | OpenRouter by policy, direct OpenAI only for explicit legacy config | `openai/gpt-5.4-image-2` default policy, legacy `edit_image_model` when explicitly configured | `high` default policy | wired; no customer-grade reliance before spend-gated smoke |
| Source-edit emergency fallback | manual review | n/a | n/a | existing manual queue |

Finalization exports the selected approved preview when one exists. The `deterministic-renderer` default is therefore a no-new-model-call finalization posture, not permission to visually regenerate a different final after customer approval.

## Bakeoff Candidates

These models are policy candidates, not automatic customer traffic routes in PR-1.

| Situation | Provider | Model | Reason |
|---|---|---|---|
| Text-heavy draft primary candidate | OpenRouter | `recraft/recraft-v4.1` | cheaper text/design candidate for flyers |
| Text-heavy premium candidate | OpenRouter | `sourceful/riverflow-v2-pro` | stronger text-rendering candidate, higher cost |
| Visual-heavy candidate | OpenRouter | `black-forest-labs/flux.2-pro` | visual quality/editing challenger |
| Experimental multilingual/text candidate | OpenRouter | `x-ai/grok-imagine-image-quality` | promising, too new for default rollout |
| Future separate-provider candidate | Ideogram direct API | current Ideogram v3/latest | only after a 20-case bakeoff justifies another key |

Before promoting any candidate, run an authenticated OpenRouter slug check with the production key and a visual bakeoff using real Flyer Studio cases.

## Source-Edit Boundary

Do not rely on source-preserving edits through OpenRouter for customer operations until the follow-up verification includes:

- A visual-QA regression dataset with real source-edit cases.
- Provider capability verification for uploaded-reference image editing.
- A source-preservation pass/fail criterion that catches layout regeneration.
- Kill-switch behavior that queues manual review instead of sending degraded output.

The safe fallback for source-edit provider uncertainty remains manual review, not a cheap image model. If provider readiness, API shape, response shape, or source-preservation confidence fails, queue manual review.

## Admin Dashboard Backlog

The admin dashboard should eventually allow operator-controlled model policy changes, but only with audit logging and spend visibility. See `tasks/flyer-model-admin-controls-backlog-2026-05-20.md`.
