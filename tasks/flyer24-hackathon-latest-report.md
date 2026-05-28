# Flyer24 Hackathon Latest Report

Updated: 2026-05-28T01:33:00Z

## Current batch
- Branch: `codex/flyer24-batch-routing-parity-202605280130`
- PR: #319 `fix(flyer): align explicit-intent routing and CTA parity regressions`
- Deploy: not run (PR stage)
- Scope: fix explicit new-flyer intent bypass under stale active intake rows and restore deterministic cf-router Flyer routing parity coverage around campaign/account CTA handling.
- Root-cause evidence:
  - Focused cf-router suite on `main` failed with explicit-intent misroute (`Need flyer for ...` attached to active intake instead of new project).
  - Same suite showed CTA/account route expectation drift across quick-flyer/start-trial/act-now/status transitions.
- Risk: low (routing predicate + tests only; no payment/quota/provider/state mutation).
- Hermes/MCP-first: Hermes ingress/identity/audit reused; net-new only Flyer-local route predicate and regression expectations.

## Batch issue list fixed
1. Prevent explicit `flyer for ...` requests with concrete brief cues from being treated as vague intake prompts.
2. Preserve active-project ownership of explicit revision wording in tests (no F7 pass-through expectation drift).
3. Align new-project missing-info path expectations to current clarification reply flow (`send_flyer_text`).
4. Align trial CTA existing-customer route expectations to current trial-link recovery path.
5. Align active/payment-pending/suspended CTA route expectations to current deterministic account-status handling.
6. Align quick-flyer and new-sender CTA expectations to current intake-first route contract.

## PR queue classification refresh
- #307 `fix(flyer): consolidate payment fail-closed contract and MCP parity` - operator-review-required (money-adjacent), open.
- #319 `fix(flyer): align explicit-intent routing and CTA parity regressions` - open, low-risk, pending review/check signals.

## Running PR list (hackathon)
- #295 merged/deployed
- #296 merged/deployed
- #297 merged/deployed
- #298 merged
- #299 merged
- #303 merged
- #304 merged
- #305 merged
- #306 merged
- #307 open (operator-review-required)
- #312 merged
- #314 merged
- #319 open

## Verification for this batch
- `python3 -m py_compile src/plugins/cf-router/actions.py tests/test_cf_router_plugin.py` ✅
- `pytest -q tests/test_cf_router_plugin.py -k 'flyer and (active or intake or status or sample or prompt or explicit or campaign)' --maxfail=20` ✅ (`34 passed, 104 deselected`)
- `git diff --check` ✅

## Runtime checks snapshot
- `systemctl --failed`: none.
- Flyer/shift timers active: `flyer-recovery-watchdog`, `flyer-source-edit-sla-watchdog`, `shift-agent-health`, `shift-agent-tail-logger`.
