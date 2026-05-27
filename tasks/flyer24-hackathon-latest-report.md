# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T14:35:00Z

## Current batch
- Branch: `codex/flyer24-batch-sample-ask-shapes-202605271430`
- PR: pending create
- Deploy: not run (PR stage)
- Scope: close six sample-request ask-shape misses that were falling through instead of routing to sample-idea intake.
- Root-cause evidence:
  - RED test additions failed on 2 of 6 phrases in `test_sample_prompt_variants_route_to_sample_idea_intake`.
  - Failing variants returned `None` (passthrough) instead of `cf-router flyer sample prompts sent`.
  - `_SAMPLE_PROMPT_REQUEST` under-covered ask-shapes `what can you suggest for ...` and `what should I write for ... flyer`.
- Risk: low (routing heuristic + tests/docs only; no payment/account/quota/provider/manual-close mutations).
- Hermes/MCP-first: Hermes owns ingress, identity, dispatch, audit substrate, and intake script orchestration. Net-new scope is Flyer lexical policy only.

## Batch issue list fixed
1. `what can you suggest for my weekend offer flyer` now routes to sample ideas.
2. `what should i write for a grand opening flyer` now routes to sample ideas.
3. Added variant coverage for `any ideas for my summer sale poster` in the explicit sample corpus.
4. Added variant coverage for `help me with caption ideas for my store promotion` in the explicit sample corpus.
5. Added variant coverage for `give me few prompt ideas for my business ad` in the explicit sample corpus.
6. Added variant coverage for `can you share a couple of flyer prompt ideas for my salon` in the explicit sample corpus.

## PR queue classification refresh
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - operator-review-required (money-adjacent), open, merge conflict with main.
- #299 `fix(flyer): harden billing health MCP readiness visibility` - operator-review-required (money-adjacent), open.

## Running PR list (hackathon)
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - open; operator-review-required.
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged to `main`; deployed `deploy-20260527-102236-c858caa1`.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged to `main`; deployed `deploy-20260527-112213-f019b345`.
- #297 `fix(flyer): route sample-request tagline/slogan variants to idea intake` - merged to `main`; deployed `deploy-20260527-121058-87db7152`.
- #299 `fix(flyer): harden billing health MCP readiness visibility` - open; operator-review-required.
- #<pending> `fix(flyer): route sample ask-shape variants to starter ideas` - pending open.

## Verification for this batch
- `pytest -q tests/test_cf_router_flyer_routing.py -k sample_prompt_variants_route_to_sample_idea_intake` ✅ (45 passed)
- `pytest -q tests/test_cf_router_flyer_routing.py` ✅ (240 passed)
- `python3 -m py_compile src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py` ✅
- `git diff --check` ✅

## Runtime checks snapshot
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers active: `flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `send-daily-brief`, `shift-agent-tail-logger`.
