**Drift-check tag:** extends-Hermes

# Hermes-first checklist

1. Inbound message + sender resolution in cf-router (`pre_gateway_dispatch` + sender lookup) — [Hermes]
2. Active Flyer project lookup and status selection helpers — [Hermes]
3. Deterministic routing policy for vague starts / trial-link complaints / inactive-status copy / delivered revision edits — [net-new]
4. Customer reply send + audit rows — [Hermes]
5. Regression tests for routing outcomes in transcript-shaped fixtures — [net-new]

Net-new effort estimate: steps 3 and 5 only.

## MCP-first verdict (payment/connectors)

No Stripe/Razorpay checkout, webhook, or provider mutation in this batch. Payment/connectors remain unchanged; this is cf-router policy hardening only.

## Batch scope (5 related fixes)

1. Preserve delivered-project revision capture when message is an explicit revision intent (including media edit text) instead of bypassing to new-project flow.
2. Avoid active-project interception for vague start text (`Create flyer`) so starter-ideas/onboarding paths own the reply.
3. Avoid active-project interception for known legacy trial-link follow-up phrases so account recovery copy wins.
4. Align inactive-customer status reply assertion for `cancelled` with current fail-closed copy (`no longer active`) to prevent fragile literal dependence on forbidden completion verbs.
5. Add ordering guard tests to lock these branches against future regressions.

## Files to touch

- `src/plugins/cf-router/hooks.py`
- `tests/test_cf_router_flyer_routing.py`
- `tasks/flyer24-hackathon-latest-report.md`

## Verification

- `python3 -m py_compile src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py`
- `pytest -q tests/test_cf_router_flyer_routing.py -k "delivered_existing_flyer_media_revision or vague_flyer_start_for_active_customer_sends_starter_ideas or legacy_trial_link_complaint or ineligible_customer_status"`
- `git diff --check`

## Risk

Low: deterministic cf-router guardrail ordering + tests only; no payment/account/quota state mutation and no deploy/runtime scripts touched.
