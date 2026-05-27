# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T00:27:00Z

## Current batch
- Branch: `codex/flyer24-batch-cockpit-auth-health-202605270025`
- Scope: restore cockpit auth-override compatibility and source-edit manual-queue detail visibility in `/flyer/health`.
- Root-cause evidence: CI run `26480835826` failed 12 tests, mostly false `401 Authentication required` in Flyer cockpit tests plus source-edit detail regressions missing stale-threshold/mixed-blocker context.
- Risk: low (auth dependency wiring + read-only health detail string; no customer send/payment/quota/account mutation).
- Hermes/MCP-first: Hermes continues to own auth substrate, runtime state, and queue lifecycle; this batch is Flyer-side dependency wiring + operator visibility only.

## PR queue classification (drained before new batch)
- #276 `fix(flyer): harden source-vs-new status check-in routing`: merged to `main` (already drained).
- #271 `fix(flyer): restore account/manual-edit routing compatibility`: open, clean, routing behavior change; requires operator review.
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility`: open, money-adjacent; operator-review-required.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog`: open, dirty/conflicting; money-adjacent, operator-review-required.
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior`: open, dirty/conflicting; broad routing work, operator-review-required.
- #279 `fix(flyer): expand source-edit/manual-queue health visibility`: open, red CI, not merge-qualified.
- #277 `feat(flyer): add semantic brief visibility policy`: open; feature scope, operator review pending.

## Running PR list (hackathon)
- #276 `fix(flyer): harden source-vs-new status check-in routing` - merged.
- #271 `fix(flyer): restore account/manual-edit routing compatibility` - open.
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility` - open.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog` - open.
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior` - open.
- #279 `fix(flyer): expand source-edit/manual-queue health visibility` - open.
- #277 `feat(flyer): add semantic brief visibility policy` - open.
- #278 `fix(flyer): widen sample-idea request phrase coverage` - merged.
- #TBD `fix(flyer): restore cockpit auth override compatibility and health detail context` - opening from this batch.

## Verification for this batch
- `python3 -m py_compile web/backend/app/routers/flyer.py web/backend/tests/test_flyer_health.py web/backend/tests/test_flyer_admin.py web/backend/tests/test_flyer_admin_cockpit_ops.py` (pass)
- `python3 -m pytest -q web/backend/tests/test_flyer_health.py web/backend/tests/test_flyer_admin.py web/backend/tests/test_flyer_admin_cockpit_ops.py -k "flyer_health_returns_expected_shape or flyer_health_redacts_secret_values or source_edit_detail_surfaces_queue_impact_when_present or source_edit_detail_mentions_mixed_reason_backlog or deactivate_customer_endpoint_audits_action or project_asset_media_serves_owned_asset or operator_upload_media_serves_well_named_file"` (environment-blocked: missing `httpx` and `fastapi` in this runner)
- `git diff --check`
