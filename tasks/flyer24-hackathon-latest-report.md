# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T12:26:00Z

## Current batch
- Branch: `codex/flyer24-batch-sample-lexicon-202605271222`
- PR: #297 `fix(flyer): route sample-request tagline/slogan variants to idea intake` (open)
- Deploy: not run (PR open)
- Scope: close six sample-request lexical misses that fell into non-sample routes.
- Root-cause evidence:
  - RED test additions failed on 6 phrase variants in `test_sample_prompt_variants_route_to_sample_idea_intake`.
  - Failures routed to `cf-router flyer exact edit already queued ...` / `cf-router flyer business scope blocked` instead of sample-idea intake.
  - `_SAMPLE_PROMPT_REQUEST` under-covered lexical families (`tagline/slogan/punchline/copies/options`) and `what are ...` ask shape.
- Risk: low (routing heuristic + tests/docs only; no payment/account/quota/provider/manual-close mutations).
- Hermes/MCP-first: Hermes continues to own ingress, identity, dispatch, and messaging substrate. Net-new scope is Flyer sample-request policy only.

## Batch issue list fixed
1. `give me some taglines for my poster` now routes to sample ideas.
2. `need catchy slogans for my store offer` now routes to sample ideas.
3. `share a few ad copies for my weekend sale` now routes to sample ideas.
4. `what are good promo captions for my business` now routes to sample ideas.
5. `can you suggest punchlines for my business poster` now routes to sample ideas.
6. `give marketing slogan options for my shop ad` now routes to sample ideas.

## PR queue classification refresh
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - operator-review-required (money-adjacent), still open.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged + deployed.
- #297 `fix(flyer): route sample-request tagline/slogan variants to idea intake` - open; merge-qualified pending checks/review.

## Running PR list (hackathon)
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - open; operator-review-required.
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged to `main`; deployed `deploy-20260527-102236-c858caa1`.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged to `main`; deployed `deploy-20260527-112213-f019b345`.
- #297 `fix(flyer): route sample-request tagline/slogan variants to idea intake` - open.

## Verification for this batch
- `pytest -q tests/test_cf_router_flyer_routing.py -k sample_prompt_variants_route_to_sample_idea_intake` ✅ (39 passed)
- `pytest -q tests/test_cf_router_flyer_routing.py` ✅ (234 passed)
- `python3 -m py_compile src/plugins/cf-router/hooks.py` ✅
- `git diff --check` ✅

## Post-deploy runtime checks
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers present and active (`flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `send-daily-brief`, `shift-agent-tail-logger`).
