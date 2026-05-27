# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T17:21:58Z

## Current batch
- Branch: `codex/flyer24-batch-manual-queue-health-signals-202605271725`
- PR: pending create
- Deploy: not run (PR stage)
- Scope: expand Flyer `/flyer/health` manual-queue impact signals so source-edit and visual-QA backlog urgency is directly visible to operators.
- Root-cause evidence:
  - RED tests showed `_source_edit_manual_queue_impact()` lacked explicit `visual_qa_failed` queue/stale counters.
  - RED tests showed `_source_edit_manual_queue_impact()` lacked aggregate `customer_update_due` overdue counters.
  - RED tests showed health payload lacked reason-family and stale-family aggregate counts, forcing raw-code-only triage.
- Risk: low (read-only health payload + tests/docs only; no payment/account/quota/provider/manual-close mutations).
- Hermes/MCP-first: Hermes owns ingress, state, and audit substrate; net-new is Flyer-local read-only operator health shaping only.

## Batch issue list fixed
1. `_source_edit_manual_queue_impact()` now reports `visual_qa_queued_count`.
2. `_source_edit_manual_queue_impact()` now reports `visual_qa_stale_count`.
3. `_source_edit_manual_queue_impact()` now reports `visual_qa_oldest_stale_minutes`.
4. `_source_edit_manual_queue_impact()` now reports `customer_update_due_count` and `customer_update_due_oldest_minutes`.
5. `_source_edit_manual_queue_impact()` now reports `reason_family_counts` and `stale_reason_family_counts`.
6. Source-edit provider health detail now includes reason-family and customer-update-due summaries for stale backlog triage.

## PR queue classification refresh
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - operator-review-required (money-adjacent), open, merge conflict with main.
- #299 `fix(flyer): harden billing health MCP readiness visibility` - operator-review-required (money-adjacent), open, merge conflict with main.
- #<pending> `fix(flyer): expand manual queue health signals for source-edit and visual-QA backlog` - pending open.

## Running PR list (hackathon)
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - open; operator-review-required.
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged to `main`; deployed `deploy-20260527-102236-c858caa1`.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged to `main`; deployed `deploy-20260527-112213-f019b345`.
- #297 `fix(flyer): route sample-request tagline/slogan variants to idea intake` - merged to `main`; deployed `deploy-20260527-121058-87db7152`.
- #298 `fix(flyer): close render dependency and recovery alert gaps` - merged to `main`; deployed in later train (see git history).
- #299 `fix(flyer): harden billing health MCP readiness visibility` - open; operator-review-required.
- #303 `fix(flyer): route sample ask-shape variants to starter ideas` - merged to `main`.
- #304 `fix(flyer): route missed sample request phrase variants to starter ideas` - merged to `main`.
- #305 `fix(flyer): normalize manual queue triage status and reason signals` - merged to `main`.
- #<pending> `fix(flyer): expand manual queue health signals for source-edit and visual-QA backlog` - pending open.

## Verification for this batch
- `pytest -q web/backend/tests/test_flyer_health.py` ✅ (27 passed)
- `pytest -q web/backend/tests/test_flyer_health.py -k 'manual_queue_impact_zero_by_default or manual_queue_impact_counts_source_edit_unavailable_rows or manual_queue_impact_reports_stale_reason_counts or source_edit_detail_mentions_mixed_reason_backlog'` ✅ (4 passed)
- `python3 -m py_compile web/backend/app/routers/flyer.py web/backend/tests/test_flyer_health.py` ✅
- `git diff --check` ✅

## Runtime checks snapshot
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers active: `flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `send-daily-brief`, `shift-agent-tail-logger`.
