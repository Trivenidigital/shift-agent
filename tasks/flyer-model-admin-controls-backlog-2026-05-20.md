# Flyer Studio Model Admin Controls Backlog

**Status:** Backlog
**Owner:** Flyer Studio
**Rollout posture:** Post-customer-rollout. Do not block PR-1.

## Sequencing

- PR-1 before rollout: wire `draft_provider_policy` and `final_provider_policy`, add policy docs, add this admin-dashboard backlog item, and keep source-edit path unchanged. Status: done in PR #144 and deployed to `main-vps`.
- PR-2 after rollout: source-edit model/provider migration only after a visual-QA regression dataset exists and proves source preservation on real source-edit cases.
- PR-3 after bakeoff: optional Ideogram provider and admin-dashboard controls only after the 20-case bakeoff shows the quality/cost gain is worth another key/subscription.

## Goal

Add admin-dashboard controls for Flyer Studio model policy so operators can change draft/final/source-edit providers without editing YAML over SSH.

## Scope

- Show current draft provider, final provider, fallback provider, and source-edit provider.
- Show key readiness by provider: OpenRouter, OpenAI, future Ideogram.
- Show daily/monthly spend cap and recent spend estimate.
- Allow operator edits to:
  - default draft model
  - text-heavy candidate model
  - visual-heavy candidate model
  - final fallback model
  - source-edit model only after the source-edit regression dataset lands
- Write every model-policy change to the audit log with old value, new value, operator, timestamp, and reason.
- Provide a bakeoff mode that generates alternatives for operator review without sending challenger output to customers automatically.

## Deferred Ideogram Provider

Do not add an Ideogram key before the rollout. Revisit only after a 20-case bakeoff proves Ideogram materially beats OpenRouter candidates on text accuracy, retry rate, cost per shippable flyer, and latency.

## Acceptance Criteria

- Admin changes update `/opt/shift-agent/config.yaml` through the existing cockpit config-save path.
- Invalid provider/model combinations fail closed before saving.
- Source-edit provider controls remain disabled until the regression dataset gate is present.
- Dashboard displays a warning when configured model slugs have not been verified with the current provider key.
