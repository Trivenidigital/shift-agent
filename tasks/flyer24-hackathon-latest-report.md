# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T07:35:00Z

## Current batch
- Branch: `codex/flyer24-batch-manual-status-normalization-202605270719`
- Scope: normalize manual reason-code handling for status replies and harden manual-queue detection from generation failure detail.
- Root-cause evidence:
  - `test_manual_review_status_normalizes_source_edit_reason_code_in_active_intercept` failed (`cf-router flyer status` instead of exact-edit status) when reason code casing/spacing drifted.
  - `test_manual_review_status_normalizes_source_edit_reason_code_in_pre_gateway` failed on uppercase reason code.
  - `test_generation_detail_with_reason_code_only_counts_as_manual_review` failed (manual-review signal missed when detail carried only `reason_code=source_edit_provider_unavailable`).
  - `test_generation_detail_with_in_progress_manual_state_counts_as_manual_review` failed (manual-review signal missed for `manual_review.status=in_progress` detail).
- Risk: low (routing/status normalization + detection helper hardening; no payment/account/quota mutation).
- Hermes/MCP-first: Hermes still owns ingress/identity/bridge/audit substrate. Net-new scope is Flyer policy logic for status/manual routing and observability-safe fail-closed behavior.

## PR queue classification (before this batch)
- #280 `fix(flyer): restore cockpit auth override compatibility and source-edit health detail` - mergeable + green; low-risk visibility/auth compatibility.
- #279 `fix(flyer): expand source-edit/manual-queue health visibility` - open with failing CI; not merge-qualified.
- #284 `fix(flyer): resolve cf-router routing and dedupe regressions` - open, mergeable, no CI reported.
- #271 `fix(flyer): restore account/manual-edit routing compatibility` - conflicting.
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility` - conflicting + failed CI; money-adjacent.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog` - conflicting; money-adjacent.
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior` - conflicting.

## Running PR list (hackathon)
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior` - open.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog` - open.
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility` - open.
- #271 `fix(flyer): restore account/manual-edit routing compatibility` - open.
- #279 `fix(flyer): expand source-edit/manual-queue health visibility` - closed as superseded by #291.
- #280 `fix(flyer): restore cockpit auth override compatibility and source-edit health detail` - closed as superseded by #291.
- #284 `fix(flyer): resolve cf-router routing and dedupe regressions` - open.
- #291 `fix(flyer): consolidate cockpit auth compatibility and manual-queue health visibility` - open (replacement for #279/#280).
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - open (money-adjacent; operator review required).

## PR queue classification refresh (post-main@91274c4)
- #292: operator-review-required (money-adjacent payment contract and readiness metadata).
- #284: blocked/dirty due main drift; likely superseded by landed routing guardrail commits + additional replay updates, needs rebase verdict.
- #271: blocked/dirty due overlap with later routing fixes in #284 and landed commits on `main`; requires rebase or closure.
- #268: blocked for this lane (money-adjacent and mixed with broad non-Flyer `safe_io` bridge refactor); keep open for operator decision or split.
- #256: operator-review-required (money-adjacent activation contract/readiness hardening).
- #254: blocked/dirty due main drift and overlap with later routing fixes now partly landed in #287/#288; requires rebase/split before merge.

## Verification for this batch
- `/opt/codex-flyer-autodev-venv/bin/python -m pytest -q tests/test_cf_router_flyer_routing.py -k "normalizes_source_edit_reason_code or manual_review_where_is_my_updated_flyer_routes_as_status or manual_review_where_is_update_flyer_routes_as_status"` (pass: 4)
- `/opt/codex-flyer-autodev-venv/bin/python -m pytest -q tests/test_flyer_source_edit_preflight.py -k "generation_detail_with_source_edit_failed_counts_as_manual_review or reason_code_only_counts or in_progress_manual_state"` (pass: 3)
- `/opt/codex-flyer-autodev-venv/bin/python -m pytest -q tests/test_cf_router_flyer_routing.py` (pass: 216)
- `/opt/codex-flyer-autodev-venv/bin/python -m pytest -q tests/test_flyer_source_edit_preflight.py` (pass: 16)
- `python3 -m py_compile src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py tests/test_flyer_source_edit_preflight.py`
- `git diff --check`
