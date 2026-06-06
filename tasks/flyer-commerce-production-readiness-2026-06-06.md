# Flyer/Commerce Production Readiness Evidence - 2026-06-06

**Drift-check tag:** extends-Hermes

**New primitives introduced:** none. This is a current-state evidence report.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Flyer generation/intake/recovery | Existing Flyer dispatcher, generation scripts, visual QA, recovery watchdog, deploy smoke | Reuse shipped Flyer substrate; no bounded source patch from this evidence |
| Source-preserving edits | Existing source-edit preflight and manual-review fallback | Operator must provision/verify source-edit provider before automated source edits are customer-grade |
| Commerce payment links | Existing Commerce cart/order/payment primitives plus Stripe livemode and webhook gates | Keep dormant until operator activates Stripe/provider config |
| Runtime readiness | Existing `credential-minimized-readiness`, `flyer-deploy-smoke`, Commerce deploy gates | Use existing gates as authority |

Awesome-Hermes-Agent ecosystem check: no turnkey Flyer/Commerce production bundle replaces the repo-native Flyer and Commerce gates; continue using Hermes messaging/MCP/skills substrate and local payment-safety gates.

## Current result

Updated after deploy `deploy-20260606-182517-076c9d48`
(`076c9d48719df4fd3f2a709f20a4592fcfd4a089`): Flyer smoke checks pass, but
live runtime is not enabled for broad customer production traffic or live
payments:

- `flyer.enabled: false`
- `OPENROUTER_API_KEY: present`; the configured Flyer draft/source provider
  policies point at OpenRouter-backed models
- `STRIPE_SECRET_KEY: unset` in credential-minimized readiness; Stripe Commerce activation itself uses `STRIPE_API_KEY` per `docs/runbooks/commerce-stripe-onboarding.md`
- Commerce config is absent/empty on this VPS, so schema defaults apply:
  `enabled=False`, `provider=placeholder`
- Commerce webhook/livemode gates skip safely because Commerce is dormant

## Evidence

Clean worktree baseline from `C:\projects\sme-agents-flyer`:

```text
python -m pytest tests/test_flyer_rollout_readiness.py tests/test_flyer_create_project.py tests/test_flyer_schemas.py tests/test_flyer_workflow.py tests/test_flyer_visual_qa.py tests/test_flyer_facts.py tests/test_flyer_semantic_brief.py tests/test_commerce_payment_link.py tests/test_commerce_order_state.py tests/test_commerce_livemode_gate.py tests/test_commerce_webhook_subscription_gate.py -q
488 passed
```

Live credential/minimized readiness:

```text
Strict foundation: OK
productivity/maps: present (live)
productivity/ocr-and-documents: present (live)
mcp/native-mcp: present (live)
cf-router: present (enabled=True, compile=True)
OPENROUTER_API_KEY: env_present
OPENAI_API_KEY: unset
STRIPE_SECRET_KEY: unset
Connector candidates: 29 total, 4 stale
```

Live Flyer deploy smoke:

```text
{
  "ok": true,
  "root": "/opt/shift-agent",
  "errors": []
}
```

Live config slice:

```text
flyer:
  enabled: false
  draft_image_model: google/gemini-2.5-flash-image
  final_image_model: deterministic-renderer
  recovery:
    mode: worker_draft
    enable_timer: true
    worker_runner: codex
    worker_auto_run: true
  source_edit_provider_policy:
    default:
      provider: openrouter
      model: openai/gpt-5.4-image-2
commerce: {}
```

Live Commerce gates:

```text
commerce: provider=placeholder (enabled=False) - webhook-subscription gate not applicable, skipping.
commerce: provider=placeholder (enabled=False) - Stripe livemode gate not applicable, skipping.
```

## Local doc fix

Updated the Commerce activation runbooks to follow the project-required Windows SSH capture pattern:

- `docs/runbooks/commerce-stripe-onboarding.md`
- `docs/runbooks/commerce-deposit-onboarding.md`

The operator-facing commands now redirect SSH output to local `.ssh_*.txt` scratch files and tell the operator to read those files separately. This avoids the known broken inline SSH stdout path during Stripe/deposit activation.

Clarified the Stripe credential split:

- `STRIPE_API_KEY` is the key used by the Commerce Stripe mint/livemode gate path.
- `STRIPE_SECRET_KEY` is what `credential-minimized-readiness` reports for a broader Stripe write-rail check.
- Setting only `STRIPE_SECRET_KEY` is not enough for Commerce activation; `check-commerce-stripe-livemode` needs `STRIPE_API_KEY`.

## Required operator actions

Before Flyer customer production:

1. Decide whether this VPS should enable Flyer customer traffic now.
2. Set `flyer.enabled: true` only after confirming the current customer pilot scope.
3. Provision/verify the configured OpenRouter source-edit provider path if
   automated source-preserving edits should be customer-grade.
4. Run spend-gated real-model Flyer golden/source-edit smoke before broad launch.

Before Commerce/Stripe production:

1. Provision Stripe Commerce credentials: `STRIPE_API_KEY` for Stripe API access and `STRIPE_WEBHOOK_SECRET` for webhook signature validation.
2. Set the Commerce provider and livemode expectation intentionally.
3. Subscribe/configure the Stripe webhook per `docs/runbooks/commerce-stripe-onboarding.md`.
4. Rerun:

```bash
ssh main-vps '/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/check-commerce-webhook-subscription; /usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/check-commerce-stripe-livemode' > .ssh_commerce_gates.txt 2>&1
```

Commerce can be called payment-production-ready only when these gates run their active Stripe paths and pass.

## Code decision

No bounded Flyer/Commerce source patch is warranted from this evidence. The remaining gaps are operator activation, credentials, source-edit bakeoff, or a high-blast-radius intent rollout decision that requires a separate plan.
