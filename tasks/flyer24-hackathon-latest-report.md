# Flyer24 Hackathon Latest Report

Updated: 2026-05-26T21:30:00Z

## Current batch
- Branch: `codex/flyer24-batch-status-checkin-hardening-202605262125`
- Scope: harden SOURCE/NEW pending status-check-in detection so natural customer check-ins re-send clarification instead of falling through.
- Root-cause evidence: targeted RED tests showed `eta please` and `any update please` were not treated as status check-ins in the dedicated helper/pending-choice branch.
- Risk: low (routing phrase coverage + helper unification + tests only; no payment/account/quota mutation).
- Hermes/MCP-first: Hermes continues to own ingress, identity, and messaging transport; this batch changes only Flyer-specific status phrase policy in cf-router.

## PR queue classification (drained before new batch)
- #272 `fix(flyer): harden stale edit SLA updates`: merged to `main` (already drained).
- #271 `fix(flyer): restore account/manual-edit routing compatibility`: open, clean, routing behavior change; requires operator review.
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility`: open, dirty with failing cockpit-ci run history; money-adjacent, operator-review-required.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog`: open, dirty/conflicting; money-adjacent, operator-review-required.
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior`: open, dirty/conflicting; broad routing work, operator-review-required.

## Running PR list (hackathon)
- #272 `fix(flyer): harden stale edit SLA updates` - merged.
- #271 `fix(flyer): restore account/manual-edit routing compatibility` - open.
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility` - open.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog` - open.
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior` - open.
- #TBD `fix(flyer): harden source-vs-new status check-in routing` - opening from this batch.

## Verification for this batch
- `python3 -m py_compile src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py`
- `pytest -q tests/test_cf_router_flyer_routing.py -k "status_checkin or source_vs_new"` (pass: 17)
- `git diff --check`
