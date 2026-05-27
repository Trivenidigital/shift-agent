# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T21:22:56Z

## Current batch
- Branch: `codex/flyer24-batch-manual-reason-canonicalization-202605272124`
- PR: #312 `fix(flyer): canonicalize legacy manual-review reasons`
- Deploy: not run (PR stage)
- Scope: canonicalize legacy/noisy manual-review reason markers so manual-queue visibility and customer status copy stay deterministic for source-edit and visual-QA blockers.
- Root-cause evidence:
  - RED test failed for legacy reason text containing `source_edit_provider_unavailable`: queue row stayed `unclassified`.
  - RED test failed for detail marker `visual_qa_failed`: queue row stayed `unclassified`.
  - RED test failed for manual status reply on legacy source-edit marker: returned generic queued copy.
- Risk: low (deterministic parser phrase coverage + tests only).
- Hermes/MCP-first: Hermes continues to own ingress/identity/audit/send paths; net-new is Flyer-local reason canonicalization and status-copy selection only.

## Batch issue list fixed
1. Canonicalize `reason_code=unclassified` rows from legacy `manual_review.reason` markers.
2. Canonicalize `reason_code=unclassified` rows from legacy `manual_review.detail` markers.
3. Drive manual queue `reason_family` from canonical reason, not raw reason_code.
4. Drive manual queue `operator_action_hint` from canonical reason, not raw reason_code.
5. Use canonical reason resolution in cf-router manual status copy selection.
6. Add regression tests for queue canonicalization and source-edit-specific manual status copy fallback.

## PR queue classification refresh
- #299 `fix(flyer): harden billing health MCP readiness visibility` - operator-review-required (money-adjacent), open.
- #307 `fix(flyer): consolidate payment fail-closed contract and MCP parity` - operator-review-required (money-adjacent), open.
- #312 `fix(flyer): canonicalize legacy manual-review reasons` - open, low-risk, merge/deploy eligible when checks/review are green.

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
- #312 `fix(flyer): canonicalize legacy manual-review reasons` - open.

## Verification for this batch
- `pytest -q tests/test_flyer_manual_queue.py -k 'canonicalizes_reason or reason_family or includes_reason_code'` ✅ (6 passed)
- `pytest -q tests/test_cf_router_plugin.py -k 'flyer_manual_edit_status_reply'` ✅ (4 passed)
- `python3 -m py_compile src/agents/flyer/manual_queue.py src/plugins/cf-router/actions.py tests/test_flyer_manual_queue.py tests/test_cf_router_plugin.py` ✅
- `git diff --check` ✅

## Runtime checks snapshot
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers active: `flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `shift-agent-tail-logger`.
