# Flyer24 Batch Plan - Routing Compat Guardrails (2026-05-26)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Inbound WhatsApp event normalization and dispatch -> **[Hermes]** existing cf-router hook substrate.
2. Account command execution / state mutation script -> **[Hermes]** existing `manage-flyer-account` path.
3. Customer-facing send chokepoint -> **[Hermes]** existing `safe_io.bridge_post` path.
4. Active project/manual queue routing precedence -> **[net-new]** Flyer policy bugfix in cf-router.
5. Legacy bridge mock compatibility (`bridge_post` without `action_context`) -> **[net-new]** Flyer compatibility shim.
6. Regression assertions for stale manual-edit over-capture + account-command routing -> **[net-new]** tests only.

Net-new scope only: tighten manual-edit intercept gating, preserve account-command routing, add send compatibility fallback, and pin with focused tests.

## Batch issue list
1. `_try_flyer_account_intercept` always passes `action_context` kwarg, breaking existing call sites/tests that still use 2-arg `send_flyer_text` stubs.
2. `send_flyer_text` always passes `action_context` to `safe_io.bridge_post`, breaking legacy bridge adapters that accept only `(chat_id, message)`.
3. `manual_edit_required` active-project branch treats unrelated messages (sample prompt/start/upgrade/status helpers) as queued exact-edit followups.
4. Status-path project override to latest can shadow the actual active manual-edit project and misreport reason route.
5. Upgrade/account commands silently fall through when send-path raises signature mismatch (caught by top-level hook and returned as `None`).
6. One copy assertion drift for guarded plan-change confirmation text.

## Planned edits
- `src/plugins/cf-router/hooks.py`
- `src/plugins/cf-router/actions.py`
- `src/agents/flyer/onboarding.py` (only if copy contract update is needed)
- Targeted tests in `tests/test_cf_router_flyer_routing.py`, `tests/test_flyer_project_isolation.py`, `tests/test_flyer_onboarding.py`, `tests/test_cf_router_plugin.py` (only minimal assertion adjustment if product copy is unchanged)

## Verification
- `python3 -m py_compile` on touched Python files
- Focused pytest cluster for the failing routing/account/dedupe tests
- `git diff --check`

## Risk
Low-medium (Flyer routing + customer copy path; no payment/provider mutation, no live sends in this batch).
