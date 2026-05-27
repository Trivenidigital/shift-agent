# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T11:27:00Z

## Current batch
- Branch: `codex/flyer24-batch-sample-prompt-gaps-202605271130` (merged)
- PR: #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` (merged)
- Deploy: `deploy-20260527-112213-f019b345` from `origin/main`
- Scope: close explicit sample-prompt lexical routing gaps that were causing clarification loops or active-edit misroutes.
- Root-cause evidence:
  - RED test additions failed on 6 phrase variants in `test_sample_prompt_variants_route_to_sample_idea_intake`.
  - Failures routed to `cf-router flyer starter already sent clarification sent` or `cf-router flyer exact edit already queued` instead of sample-idea intake.
  - `_SAMPLE_PROMPT_REQUEST` lacked coverage for lexical families (`caption/copy/line/text/template`).
- Risk: low (routing heuristic + tests/docs only; no payment/account/quota/provider/manual-close mutations).
- Hermes/MCP-first: Hermes continues to own ingress, identity, dispatch, and messaging substrate. Net-new scope is Flyer sample-request policy only.

## Batch issue list fixed
1. `need flyer caption ideas` now routes to sample ideas.
2. `give me promo lines for my poster` now routes to sample ideas.
3. `example flyer text please` now routes to sample ideas.
4. `show me some template ideas` now routes to sample ideas.
5. `i need sample ad copy` now routes to sample ideas.
6. `need prompt ideas for ads` now routes to sample ideas.

## PR queue classification refresh
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - operator-review-required (money-adjacent), still open.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged + deployed.

## Running PR list (hackathon)
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - open; operator-review-required.
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged to `main`; deployed `deploy-20260527-102236-c858caa1`.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged to `main`; deployed `deploy-20260527-112213-f019b345`.

## Verification for this batch
- `pytest -q tests/test_cf_router_flyer_routing.py -k "sample_prompt_variants_route_to_sample_idea_intake or explicit_sample_prompt_request_sends_starter_ideas or preference_command_with_polite_prefix_does_not_route_to_sample_ideas"` ✅ (35 passed)
- `python3 -m py_compile src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py` ✅
- `git diff --check` ✅
- Deploy smoke via `/usr/local/bin/shift-agent-deploy.sh deploy` ✅ (`Production pilot readiness: READY`)

## Post-deploy runtime checks
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers present and active (`flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `send-daily-brief`, `shift-agent-tail-logger`).
