# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T19:21:19Z

## Current batch
- Branch: `codex/flyer24-batch-manual-status-phrases-202605271922`
- PR: pending create
- Deploy: not run (PR stage)
- Scope: close manual-queue/status phrase gaps so project-id status check-ins route to deterministic status replies instead of revision/LLM ambiguity.
- Root-cause evidence:
  - RED tests failed for `status for project F0063`.
  - RED tests failed for `status of project F0063 please`.
  - RED tests failed for `where is update for F0063`.
- Risk: low (deterministic status phrase parsing + tests only; no payment/account/quota/provider/manual-close mutations).
- Hermes/MCP-first: Hermes continues to own ingress/identity/audit transport; net-new is Flyer-local status intent phrase coverage only.

## Batch issue list fixed
1. Added support for `status for project F####` status checks.
2. Added support for `status of project F####` status checks.
3. Added support for `update on project F####` status checks.
4. Added support for `queue status for F####` status checks.
5. Added support for `progress on F####` status checks.
6. Added support for `where is update for F####` status checks.

## PR queue classification refresh
- #299 `fix(flyer): harden billing health MCP readiness visibility` - operator-review-required (money-adjacent), open, merge-conflicting.
- #307 `fix(flyer): consolidate payment fail-closed contract and MCP parity` - operator-review-required (money-adjacent), open.
- #308 `feat(flyer): add Hermes-planned autorepair retry` - blocked/deferred for this batch (broader behavior change; separate review track).
- #<pending> `fix(flyer): close project-id status phrase gaps for manual queue/status replies` - pending open.

## Running PR list (hackathon)
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged to `main`; deployed `deploy-20260527-102236-c858caa1`.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged to `main`; deployed `deploy-20260527-112213-f019b345`.
- #297 `fix(flyer): route sample-request tagline/slogan variants to idea intake` - merged to `main`; deployed `deploy-20260527-121058-87db7152`.
- #298 `fix(flyer): close render dependency and recovery alert gaps` - merged to `main`; deployed in later train.
- #299 `fix(flyer): harden billing health MCP readiness visibility` - open; operator-review-required.
- #303 `fix(flyer): route sample ask-shape variants to starter ideas` - merged to `main`.
- #304 `fix(flyer): route missed sample request phrase variants to starter ideas` - merged to `main`.
- #305 `fix(flyer): normalize manual queue triage status and reason signals` - merged to `main`.
- #306 `fix(flyer): expand manual queue health backlog signals` - merged to `main`.
- #307 `fix(flyer): consolidate payment fail-closed contract and MCP parity` - open; operator-review-required.
- #308 `feat(flyer): add Hermes-planned autorepair retry` - open; deferred review track.
- #<pending> `fix(flyer): close project-id status phrase gaps for manual queue/status replies` - pending open.

## Verification for this batch
- `pytest -q tests/test_cf_router_plugin.py -k 'flyer_project_status_request_accepts_project_id_variants or flyer_project_status_request_keeps_edit_intent_guard'` ✅ (8 passed)
- `pytest -q tests/test_cf_router_plugin.py -k 'flyer_project_status_request or manual_edit_status_reply'` ✅ (11 passed)
- `python3 -m py_compile src/plugins/cf-router/actions.py tests/test_cf_router_plugin.py` ✅
- `git diff --check` ✅

## Runtime checks snapshot
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers active: `flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `send-daily-brief`, `shift-agent-tail-logger`.
