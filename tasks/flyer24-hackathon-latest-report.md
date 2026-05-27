# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T10:45:00Z

## Current batch
- Branch: `codex/flyer24-batch-status-phrase-gaps-202605271030` (merged)
- PR: #295 `fix(flyer): close status-check phrasing gaps in active project routing` (merged)
- Deploy: `deploy-20260527-102236-c858caa1` from `origin/main`
- Scope: harden Flyer status-check intent detection to prevent clarification loops for natural check-in wording.
- Root-cause evidence:
  - RED test `test_status_request_phrase_gaps_route_as_status_checkins` initially failed on 6 phrases.
  - Missed phrases: `f0058 status?`, `eta on my flyer`, `where my flyer at`, `did you complete it`, `whats happening with my flyer`, `what about my flyer`.
- Risk: low (routing heuristic + tests/docs only; no payment/account/quota/provider/manual-close mutations).
- Hermes/MCP-first: Hermes remains owner for ingress, identity, dispatch, and audit substrate. Net-new is Flyer status-intent policy only.

## Batch issue list fixed
1. `f0058 status?` now routes as status check-in.
2. `eta on my flyer` now routes as status check-in.
3. `where my flyer at` now routes as status check-in.
4. `did you complete it` now routes as status check-in.
5. `whats happening with my flyer` now routes as status check-in.
6. `what about my flyer` now routes as status check-in.

## PR queue classification refresh
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - operator-review-required (money-adjacent), currently merge-conflicting with main.
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged + deployed.

## Running PR list (hackathon)
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - open; operator-review-required.
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged to `main`; deployed `deploy-20260527-102236-c858caa1`.

## Verification for this batch
- `python3 -m py_compile src/plugins/cf-router/actions.py tests/test_cf_router_flyer_routing.py` ✅
- `pytest -q tests/test_cf_router_flyer_routing.py -k "status_request_phrase_gaps or flyer_is_status_checkin_matches_expected_phrases"` ✅ (7 passed)
- `git diff --check` ✅
- Deploy smoke via `/usr/local/bin/shift-agent-deploy.sh deploy` ✅ (`Production pilot readiness: READY`)

## Post-deploy runtime checks
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers present and active (`flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `send-daily-brief`, `shift-agent-tail-logger`).
