# Flyer24 Hackathon Latest Report

Updated: 2026-05-26T22:35:00Z

## Current batch
- Branch: `codex/flyer24-batch-sample-idea-coverage-202605262219`
- Scope: harden explicit sample/example/idea phrase routing so customer requests route to starter ideas instead of stale-project or account/clarification loops.
- Root-cause evidence: RED tests showed misses for explicit asks (`send me prompt examples`, `can you suggest hooks for my flyer`, `help with promotion ideas`, `give 3 ideas for weekend offer`) and revealed two stale-active-project test leaks.
- Risk: low (routing phrase matcher + intercept ordering + tests only; no payment/account/quota mutation).
- Hermes/MCP-first: Hermes continues to own ingress, sender identity, intake execution, and audit transport; this batch changes only Flyer phrase policy and deterministic interception order.

## PR queue classification (drained before new batch)
- #276 `fix(flyer): harden source-vs-new status check-in routing`: merged to `main` (already drained).
- #271 `fix(flyer): restore account/manual-edit routing compatibility`: open, clean, routing behavior change; requires operator review.
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility`: open, money-adjacent; operator-review-required.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog`: open, dirty/conflicting; money-adjacent, operator-review-required.
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior`: open, dirty/conflicting; broad routing work, operator-review-required.
- #277 `feat(flyer): add semantic brief visibility policy`: open; feature scope, operator review pending.

## Running PR list (hackathon)
- #276 `fix(flyer): harden source-vs-new status check-in routing` - merged.
- #271 `fix(flyer): restore account/manual-edit routing compatibility` - open.
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility` - open.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog` - open.
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior` - open.
- #277 `feat(flyer): add semantic brief visibility policy` - open.
- #TBD `fix(flyer): widen sample-idea request phrase coverage` - opening from this batch.

## Verification for this batch
- `python3 -m py_compile src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py`
- `pytest -q tests/test_cf_router_flyer_routing.py -k "sample_prompt_variants_route_to_sample_idea_intake or explicit_sample_prompt_request or vague_flyer_start_for_opted_out_customer or vague_flyer_start_after_first_starter"` (pass: 30)
- `pytest -q tests/test_cf_router_flyer_routing.py -k "sample or preference or clarification"` (pass: 49)
- `git diff --check`
