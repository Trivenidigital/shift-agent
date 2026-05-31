**Drift-check tag:** extends-Hermes

# Flyer Finalizing-Assets Customer Retry

## New primitives introduced

- No new substrate. This slice wires the existing `send-flyer-package` retry-safe sender into the customer reply path when a project is already `finalizing_assets`.

## Drift-rule self-checks

| Check | Evidence | Decision |
|---|---|---|
| Read active project reply path | `src/plugins/cf-router/hooks.py` finalization gate handles `revising_design`, `awaiting_final_approval`, and `delivered_with_warning`, but not `finalizing_assets`. | Add a bounded retry branch before revision fallback. |
| Read existing delivery sender | `src/agents/flyer/scripts/send-flyer-package` requires `finalizing_assets` and already retries only unsent assets, blocks uncertain deliveries, and marks delivered. | Reuse it; do not create a new sender. |
| Read action wrapper patterns | `src/plugins/cf-router/actions.py` wraps deployed scripts through `subprocess.run`. | Add a small wrapper for `send-flyer-package`. |
| Read delivery retry tests | `tests/test_flyer_delivery_retry.py` already covers idempotent retry and uncertain-delivery blocking at the script layer. | Add cf-router routing tests only. |

## Hermes-first analysis

Hermes owns WhatsApp ingress, identity, dispatch, and the send chokepoint. Flyer code owns project lifecycle, final package delivery, and deterministic customer copy after verified script results.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Customer reply ingress / sender identity | Hermes/cf-router already handles the inbound message and sender lookup. | Reuse existing active-project intercept. |
| Final package retry | No Hermes skill; existing Flyer script `send-flyer-package` already owns retry-safe package send. | Add a wrapper to call the existing script. |
| Customer-facing copy | Existing Flyer deterministic copy pattern. | Add outcome-only retry failure copy; never let Hermes author delivery-state prose. |
| Audit | Existing `audit_intercepted` and `FlyerDelivery*` script audits. | Reuse both; no new audit table. |

Hermes skill-hub check: https://hermes-agent.nousresearch.com/docs/skills has no Flyer final-package retry skill.

Awesome Hermes ecosystem check: https://github.com/0xNyk/awesome-hermes-agent is a general ecosystem index; no Flyer package-delivery primitive applies.

## Build Checklist

- [x] RED routing test: `send now` / `APPROVE` on `finalizing_assets` calls package retry.
- [x] RED routing test: retry failure sends deterministic delivery-failure copy and audits.
- [x] Add `actions.retry_send_flyer_package`.
- [x] Wire `finalizing_assets` branch in `_try_flyer_active_project_intercept`.
- [x] Subagent review.
- [x] Focused and full verification.
- [ ] PR, merge, deploy.

## Review Notes

- Structural reviewer initially found stale preview drift and an all-sent crash-window risk; both were fixed and re-reviewed to APPROVE.
- Safety reviewer initially found incomplete delivered-row truthfulness and filename-vs-asset-id audit risks; both were fixed and re-reviewed to APPROVE.
- Impacted-suite verification: `python -m pytest tests/test_cf_router_flyer_routing.py tests/test_flyer_delivery_retry.py tests/test_flyer_scripts_static.py -q` -> 404 passed.
- Full verification: `python -m pytest` -> 2857 passed, 867 skipped, 48 warnings.
