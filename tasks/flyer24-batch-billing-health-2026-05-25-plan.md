# Flyer24 Batch Plan - Billing Health Readiness (2026-05-25)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Compute provider/runtime health payload -> **[Hermes]** existing health endpoint substrate, env layering, safe read-only posture.
2. Surface Flyer billing checkout readiness posture (provider + templates + quick-flyer amount) -> **[net-new]** Flyer product policy/read-only operator visibility.
3. Keep customer behavior fail-closed when checkout links are not configured -> **[Hermes]** existing fail-closed account/guest messaging already implemented.
4. Render health in Cockpit admin UI -> **[net-new]** frontend read-only visualization.
5. Operator guidance for source-edit provider setup -> **[net-new]** copy must be provider-neutral (OpenAI/OpenRouter/manual_review).
6. Regression coverage for readiness payload and UI-typing contract -> **[net-new]** tests for new provider block + no behavior drift.

Net-new effort applies only to steps 2, 4, 5, 6.

## Batch issues (6)
1. `/flyer/health` does not report payment/checkout readiness at all.
2. Health payload cannot distinguish quick-flyer checkout-template gap from plan checkout-template gap.
3. Health payload omits payment provider and configured quick-flyer price context needed for operator triage.
4. Cockpit source-edit queue playbook hardcodes `OPENAI_API_KEY` despite provider-neutral config.
5. Cockpit `manualStatusTone()` contains a duplicate unreachable `return "amber"` branch.
6. No tests pinning billing health severity matrix (configured/missing/partial template states).

## Drift reads performed before design/code
- `src/platform/schemas.py` (`FlyerConfig` payment fields)
- `web/backend/app/routers/flyer.py` (`/flyer/health` provider payload shape)
- `web/backend/tests/test_flyer_health.py` (existing health test posture)
- `web/frontend/src/sections/FlyerAdmin.tsx` (health panel + playbook + queue status rendering)

## Implementation outline
- Add a read-only `billing_checkout_provider` block in health providers list.
- Compute severity and detail from deployed `FlyerConfig` only:
  - green: both plan + quick-flyer checkout templates present and non-placeholder.
  - yellow: one present, one missing/placeholder.
  - red: both missing/placeholder.
- Include provider-neutral model config fields (`payment_provider`, `payment_checkout_url_template_configured`, `quick_flyer_checkout_url_template_configured`, `quick_flyer_price_cents`).
- Update frontend typing/rendering for the new provider row and provider-neutral playbook copy.
- Add/extend backend tests for billing readiness severity and detail.

## Verification
- `python3 -m py_compile web/backend/app/routers/flyer.py`
- `pytest -q web/backend/tests/test_flyer_health.py`
- `npm --prefix web/frontend run build` (or repo-standard frontend build command)
- `git diff --check`

## Risk and merge policy
- Risk: low (read-only health payload + Cockpit display/copy only; no customer/runtime mutation).
- Merge policy: if checks pass and self-review finds no blockers, this batch is merge-qualified/autonomous.
