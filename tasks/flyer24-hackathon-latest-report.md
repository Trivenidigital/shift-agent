# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T15:30:00Z

## Current batch
- Branch: `codex/flyer24-batch-sample-request-phrases-202605271520`
- PR: pending create
- Deploy: not run (PR stage)
- Scope: close six sample-request phrase misses that were falling through instead of routing to sample-idea intake.
- Root-cause evidence:
  - RED additions in `test_sample_prompt_variants_route_to_sample_idea_intake` failed on all 6 new phrases.
  - Failures fell through to non-sample paths (`cf-router flyer exact edit already queued for F0105`, starter clarification, or passthrough `None`) instead of `cf-router flyer sample prompts sent`.
  - `_SAMPLE_PROMPT_REQUEST` under-covered `sample ... request`, `what should be/put on my flyer`, `suggest flyer wording`, `need ideas for caption`, and `can i get ... flyer ideas` shapes.
- Risk: low (routing regex + tests/docs only; no payment/account/quota/provider/manual-close mutations).
- Hermes/MCP-first: Hermes owns ingress, identity, dispatch, intake orchestration, and audit. Net-new scope is Flyer lexical routing policy only.

## Batch issue list fixed
1. `sample flyer request please` now routes to sample ideas.
2. `what should be on my flyer` now routes to sample ideas.
3. `what should i put on my flyer` now routes to sample ideas.
4. `suggest flyer wording for summer sale` now routes to sample ideas.
5. `need ideas for caption` now routes to sample ideas.
6. `can i get some flyer ideas?` now routes to sample ideas.

## PR queue classification refresh
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - operator-review-required (money-adjacent), open, merge conflict with main.
- #299 `fix(flyer): harden billing health MCP readiness visibility` - operator-review-required (money-adjacent), open, merge conflict with main.

## Running PR list (hackathon)
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - open; operator-review-required.
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged to `main`; deployed `deploy-20260527-102236-c858caa1`.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged to `main`; deployed `deploy-20260527-112213-f019b345`.
- #297 `fix(flyer): route sample-request tagline/slogan variants to idea intake` - merged to `main`; deployed `deploy-20260527-121058-87db7152`.
- #298 `fix(flyer): close render dependency and recovery alert gaps` - merged to `main`; deployed in later train (see git history).
- #299 `fix(flyer): harden billing health MCP readiness visibility` - open; operator-review-required.
- #303 `fix(flyer): route sample ask-shape variants to starter ideas` - merged to `main`.
- #<pending> `fix(flyer): route missed sample request phrase variants to starter ideas` - pending open.

## Verification for this batch
- `pytest -q tests/test_cf_router_flyer_routing.py -k sample_prompt_variants_route_to_sample_idea_intake` ✅ (51 passed)
- `pytest -q tests/test_cf_router_flyer_routing.py` ✅ (246 passed)
- `python3 -m py_compile src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py` ✅
- `git diff --check` ✅

## Runtime checks snapshot
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers active: `flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `send-daily-brief`, `shift-agent-tail-logger`.
