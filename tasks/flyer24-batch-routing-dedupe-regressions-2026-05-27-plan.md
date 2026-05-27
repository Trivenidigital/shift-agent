# Flyer24 Batch Plan - Routing + Dedupe Regressions (2026-05-27)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Receive WhatsApp message + sender identity lookup -> [Hermes]
2. Dispatcher pre-gateway routing + audit chokepoint -> [Hermes]
3. Flyer account-command phrase detection/order policy -> [net-new]
4. Flyer active-project revision gating policy -> [net-new]
5. Starter-idea vs active-project/status copy policy -> [net-new]
6. Outbound bridge send + dedupe compatibility shim -> [net-new]
7. Regression tests for routing/dedupe behavior -> [net-new]

Net-new scope only: fix cf-router Flyer policy regressions and bridge-call compatibility; no substrate rewrite, no payment mutation, no live sends.

## Batch issues (target 6)
- Account command intercept returns `None` instead of skip for explicit upgrade/change-plan/business-name commands.
- Active-project intercept not reliably returning skip for delivered existing-media revision path.
- Vague "Create flyer" for trial/active customer incorrectly routes into stale active-project revision instead of starter ideas.
- Legacy trial-link complaint path incorrectly falls into active-project edit branch.
- Ineligible status customer copy omits explicit status token expected by routing tests.
- `send_flyer_text` dedupe path breaks with older `safe_io.bridge_post` signature and missing `FileLock` export.

## Verification
- `python3 -m py_compile` for touched files.
- Focused pytest for failing routing + dedupe tests.
- `git diff --check`.
