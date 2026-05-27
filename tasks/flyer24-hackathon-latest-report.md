# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T20:31:00Z

## Current batch
- Branch: `codex/flyer24-batch-manual-status-phrases-202605272028`
- PR: #310 `fix(flyer): close project-id status phrase gaps for manual queue replies`
- Deploy: not run (PR stage)
- Scope: close remaining project-id status phrase gaps so manual-queue/status check-ins resolve deterministically instead of falling through revision/LLM ambiguity.
- Root-cause evidence:
  - RED tests failed for `status for project: F0063`.
  - RED tests failed for `status on project F0063`.
  - RED tests failed for `where is the update for project F0063`.
  - RED tests failed for `need status of F0063`.
  - RED tests failed for `status about F0063`.
- Risk: low (deterministic parser phrase coverage + tests only).
- Hermes/MCP-first: Hermes continues to own ingress/identity/audit/send paths; net-new is Flyer-local status intent phrase coverage only.

## Batch issue list fixed
1. Added support for `status for project: F####`.
2. Added support for `status on project F####`.
3. Added support for `where is the update for project F####`.
4. Added support for `need status of F####`.
5. Added support for `status about F####`.
6. Added support for `status update for project F####`.

## PR queue classification refresh
- #299 `fix(flyer): harden billing health MCP readiness visibility` - operator-review-required (money-adjacent), open, merge-conflicting.
- #307 `fix(flyer): consolidate payment fail-closed contract and MCP parity` - operator-review-required (money-adjacent), open.
- #308 `feat(flyer): add Hermes-planned autorepair retry` - blocked/deferred for this batch (broader behavior change; separate review track).
- #310 `fix(flyer): close project-id status phrase gaps for manual queue replies` - open, low-risk, merge/deploy eligible when checks/review are green.

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
- #310 `fix(flyer): close project-id status phrase gaps for manual queue replies` - open.

## Verification for this batch
- `pytest -q tests/test_cf_router_plugin.py -k 'flyer_project_status_request_accepts_project_id_variants or flyer_project_status_request_keeps_edit_intent_guard or flyer_manual_edit_status_reply'` ✅ (17 passed)
- `pytest -q tests/test_cf_router_plugin.py -k 'flyer_project_status_request or manual_edit_status_reply'` ✅ (17 passed)
- `python3 -m py_compile src/plugins/cf-router/actions.py tests/test_cf_router_plugin.py` ✅
- `git diff --check` ✅

## Runtime checks snapshot
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers active: `flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `shift-agent-tail-logger`.
