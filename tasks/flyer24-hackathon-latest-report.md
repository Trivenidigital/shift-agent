# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T13:36:00Z

## Current batch
- Branch: `codex/flyer24-batch-billing-health-mcp-202605271325`
- PR: #299 `fix(flyer): harden billing health MCP readiness visibility` (open)
- Deploy: not run (money-adjacent visibility/config posture work)
- Scope: close six billing health visibility gaps in Cockpit `/flyer/health` without payment mutation.
- Root-cause evidence:
  - RED tests failed on missing `supported_payment_providers` / provider env metadata in billing model config.
  - RED tests failed because missing-template detail did not include explicit fail-closed operator checklist.
  - Billing health had no signal when configured provider was Razorpay but plan catalog stayed USD-only.
- Risk: medium (billing/cockpit visibility and severity policy only; no checkout creation/activation/refund/subscription mutation).
- Hermes/MCP-first: Hermes owns ingress/state/audit/payment workflow substrate; this batch adds read-only operator visibility for MCP-first Stripe/Razorpay posture.

## Batch issue list fixed
1. Added explicit supported payment-provider list in billing health model config.
2. Added configured-provider env-key hint (`STRIPE_SECRET_KEY` or `RAZORPAY_KEY_ID|RAZORPAY_KEY_SECRET`).
3. Added provider MCP posture marker (`official_mcp_available`) for Stripe/Razorpay.
4. Added plan-currency visibility in billing health model config.
5. Added yellow posture when provider is Razorpay but plan tiers contain no INR currency.
6. Added fail-closed operator checklist text when one or both checkout templates are missing/placeholder.

## PR queue classification refresh
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - operator-review-required (money-adjacent), currently conflicting with main, remains open.
- #298 `fix(flyer): close render dependency and recovery alert gaps` - open; broad runtime behavior change, needs review and check pass evidence before autonomous merge/deploy.

## Running PR list (hackathon)
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - open; operator-review-required.
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged to `main`; deployed `deploy-20260527-102236-c858caa1`.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged to `main`; deployed `deploy-20260527-112213-f019b345`.
- #297 `fix(flyer): route sample-request tagline/slogan variants to idea intake` - merged to `main`.
- #298 `fix(flyer): close render dependency and recovery alert gaps` - open.
- #299 `fix(flyer): harden billing health MCP readiness visibility` - open; operator-review-required (money-adjacent visibility).

## Verification for this batch
- `pytest -q web/backend/tests/test_flyer_health.py -k billing_checkout_provider` ✅ (6 passed)
- `pytest -q web/backend/tests/test_flyer_health.py` ✅ (30 passed)
- `python3 -m py_compile web/backend/app/routers/flyer.py web/backend/tests/test_flyer_health.py` ✅
- `git diff --check` ✅

## Post-deploy runtime checks
- No deploy in this batch.
- `pilot-readiness-check --text`: READY (16 pass, 0 fail).
- `systemctl --failed`: unrelated pre-existing `logrotate.service` only.
