# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T16:25:00Z

## Current batch
- Branch: `codex/flyer24-batch-manual-queue-normalization-202605271620`
- PR: pending create
- Deploy: not run (PR stage)
- Scope: normalize Flyer manual-queue status/family/hints so stale source-edit and QA backlog rows stay visible and actionable in Cockpit/triage.
- Root-cause evidence:
  - RED tests showed `list_manual_queue` treated `manual_review.status` as case/spacing-sensitive and leaked raw values to triage counts.
  - RED tests showed `list_manual_queue` crashed when `manual_review.queued_at` and `updated_at` were missing even though `created_at` existed.
  - RED tests showed `_reason_family("dependency_missing")` and `_reason_family("legacy_unknown")` fell into `other`, reducing operator triage signal quality.
- Risk: low (routing regex + tests/docs only; no payment/account/quota/provider/manual-close mutations).
- Hermes/MCP-first: Hermes owns ingress, state persistence, and audit transport; net-new is Flyer-local manual queue normalization only (no connector/payment work).

## Batch issue list fixed
1. `list_manual_queue` now normalizes `manual_review.status` (`queued`/`in_progress`) across case/spacing variants.
2. `list_manual_queue` now falls back age timestamps to `created_at` when `queued_at` and `updated_at` are absent.
3. `list_manual_queue` now emits normalized `manual_status` values in queue rows.
4. `_reason_family` now classifies `dependency_missing` as `provider_readiness`.
5. `_reason_family` now classifies `legacy_unknown` as `operator_policy`.
6. `_operator_action_hint` now provides deterministic hints for `dependency_missing` and `legacy_unknown`.

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
- #304 `fix(flyer): route missed sample request phrase variants to starter ideas` - merged to `main`.
- #<pending> `fix(flyer): normalize manual queue status/family triage signals` - pending open.

## Verification for this batch
- `pytest -q tests/test_flyer_manual_queue.py` ✅ (56 passed)
- `pytest -q tests/test_flyer_manual_queue.py -k 'manual_status_case_and_spacing_variants or uses_created_at_when_manual_queued_and_updated_missing or normalizes_manual_status_casing or groups_field_is_list or dependency_missing_and_legacy_unknown'` ✅ (5 passed)
- `python3 -m py_compile src/agents/flyer/manual_queue.py tests/test_flyer_manual_queue.py` ✅
- `git diff --check` ✅

## Runtime checks snapshot
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers active: `flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `send-daily-brief`, `shift-agent-tail-logger`.
