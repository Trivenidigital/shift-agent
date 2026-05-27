# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T01:30:00Z

## Current batch
- Branch: `codex/flyer24-batch-routing-dedupe-regressions-202605270122`
- Scope: fix Flyer cf-router regressions around account-command dispatch, stale/vague routing precedence, delivered+media revision capture, and outbound dedupe compatibility.
- Root-cause evidence: `tests/test_cf_router_flyer_routing.py` had 8 initial failures on main, plus 3 secondary failures after first patch pass; all now pass with focused policy/compatibility fixes.
- Risk: low-to-medium (routing policy and bridge compatibility in cf-router only; no payment mutation, no quota/account state mutation logic changed).
- Hermes/MCP-first: Hermes still owns ingress, sender identity, bridge transport, audit substrate, and state files. This batch modifies only Flyer routing policy and compatibility handling on top of Hermes primitives.

## PR queue classification (drained before new batch)
- #282 `PR-ζ.1b: full cf-router send-path migration + allowlist enforcement flip` - open; broad/risky routing/payment-adjacent send-path migration; operator-review-required.
- #280 `fix(flyer): restore cockpit auth override compatibility and source-edit health detail` - open with green checks; overlaps adjacent routing/visibility surfaces; keep open pending operator queue decision.
- #279 `fix(flyer): expand source-edit/manual-queue health visibility` - open with failing checks; not merge-qualified.
- #271 `fix(flyer): restore account/manual-edit routing compatibility` - open; routing behavior change; operator-review-required.
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility` - open with failing checks; money-adjacent and not merge-qualified.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog` - open; money-adjacent; operator-review-required.
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior` - open; broad routing work; operator-review-required.

## Running PR list (hackathon)
- #254 open
- #256 open
- #268 open
- #271 open
- #279 open
- #280 open
- #282 open
- #TBD this batch: routing + dedupe regressions

## Verification for this batch
- `python3 -m py_compile src/plugins/cf-router/hooks.py src/plugins/cf-router/actions.py`
- `pytest -q tests/test_cf_router_flyer_routing.py` -> `214 passed`
- `git diff --check`
