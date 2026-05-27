# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T10:40:00Z

## Current batch
- Branch: `codex/flyer24-batch-status-phrase-gaps-202605271030`
- Scope: harden Flyer status-check intent detection to prevent clarification loops for natural check-in wording.
- Root-cause evidence:
  - New RED test `test_status_request_phrase_gaps_route_as_status_checkins` failed on 6 phrases that were falling through status detection.
  - Failures reproduced for: `f0058 status?`, `eta on my flyer`, `where my flyer at`, `did you complete it`, `whats happening with my flyer`, `what about my flyer`.
- Risk: low (routing heuristic only; no payment/account/quota/provider mutations).
- Hermes/MCP-first: Hermes remains owner for ingress, identity, dispatch substrate, and audit transport. Net-new is Flyer text-intent policy only.

## Batch issue list
1. `f0058 status?` was not classified as a status request.
2. `eta on my flyer` was not classified as a status request.
3. `where my flyer at` was not classified as a status request.
4. `did you complete it` was not classified as a status request.
5. `whats happening with my flyer` was not classified as a status request.
6. `what about my flyer` was not classified as a status request.

## PR queue classification refresh
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - operator-review-required (money-adjacent, merge conflict pending rebase).

## Running PR list (hackathon)
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - open; operator-review-required.
- (pending) status-phrase-gap hardening PR from `codex/flyer24-batch-status-phrase-gaps-202605271030`.

## Verification for this batch
- `python3 -m py_compile src/plugins/cf-router/actions.py tests/test_cf_router_flyer_routing.py`
- `pytest -q tests/test_cf_router_flyer_routing.py -k "status_request_phrase_gaps or flyer_is_status_checkin_matches_expected_phrases"` (pass: 7)
- `git diff --check`
