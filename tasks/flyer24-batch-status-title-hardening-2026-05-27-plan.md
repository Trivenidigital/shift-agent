**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Status/Title Hardening (2026-05-27)

## Hermes-first checklist

1. Inbound WhatsApp message ingest and sender resolution -> [Hermes]
2. Active Flyer project lookup and status intercept -> [net-new]
3. Status reply copy selection for manual queue/source-edit reasons -> [net-new]
4. Locked-fact extraction for campaign/business fields -> [net-new]
5. Audit/log substrate and state persistence -> [Hermes]
6. Test replay/execution harness -> [Hermes]

Net-new effort scope: only steps 2-4 (Flyer product policy/logic), no new substrate.

## Batch issues (target 6 related fixes)

1. Campaign title can incorrectly retain trailing medium words (`Flyer`, `Poster`) even when event field is clean.
2. Campaign title sanitization lacks regression coverage for suffix variants/casing.
3. Status check on active `manual_edit_required` rows can be overridden by unrelated latest-for-status rows.
4. Manual status branch duplication increases route drift risk between the two status-check handlers.
5. Existing stale manual-status routing test is brittle against global store leakage.
6. No explicit guard test for `manual_edit_required` preferring active row when status text is generic (`any update?`).

## Implementation outline

- Add/extend RED tests in `tests/test_flyer_facts.py` and `tests/test_flyer_project_isolation.py`.
- Harden campaign-title normalization in `src/agents/flyer/facts.py` (strip medium suffixes only).
- Add helper in `src/plugins/cf-router/hooks.py` to resolve status target consistently.
- Ensure generic status checks keep active `manual_edit_required` target unless an explicit project id is provided.
- Keep audit/detail semantics unchanged except for corrected project id selection.

## MCP-first verdict (payment/connectors)

- Not payment or connector scope; Stripe/Razorpay/MCP surfaces unchanged.

## Verification

- `python3 -m py_compile` on touched Python files.
- Focused pytest for touched tests.
- `git diff --check`.
