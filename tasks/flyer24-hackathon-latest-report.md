# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T03:28:00Z

## Current batch
- Branch: `codex/flyer24-batch-active-intercept-guardrails-202605270225`
- Scope: fix active-project intercept overreach so dedicated starter-ideas/account-recovery branches execute, while delivered-flyer explicit revisions still attach to the active project.
- Root-cause evidence: RED routing tests on `main` showed four regressions from intercept ordering/guard drift:
  - `test_delivered_existing_flyer_media_revision_stays_on_active_project`
  - `test_vague_flyer_start_for_active_customer_sends_starter_ideas`
  - `test_registered_customer_legacy_trial_link_complaint_gets_account_aware_reply`
  - `test_vague_flyer_start_for_ineligible_customer_status_does_not_send_starter`
- Risk: low (cf-router guardrails + tests only; no payment/account/quota mutation).
- Hermes/MCP-first: Hermes continues to own ingress/identity/audit/send substrate; batch is deterministic policy glue in cf-router only. No connector/payment surface change.

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
- #279 `fix(flyer): expand source-edit/manual-queue health visibility` - open.
- #280 `fix(flyer): restore cockpit auth override compatibility and source-edit health detail` - open.
- #284 `fix(flyer): resolve cf-router routing and dedupe regressions` - open.
- #TBD `fix(flyer): consolidate cockpit health visibility + auth override compatibility` - this batch.

## PR queue classification refresh (post-main@91274c4)
- #280: superseded by this consolidated branch (same target files, cleanly rebased on latest main).
- #279: superseded by this consolidated branch (same health impact/detail expansion, plus auth-override compatibility retained).
- #268: blocked for this lane (money-adjacent and mixed with broad non-Flyer `safe_io` bridge refactor); keep open for operator decision or split.
- #256: operator-review-required (money-adjacent activation contract/readiness hardening).
- #254: blocked/dirty due main drift and overlap with later routing fixes now partly landed in #287/#288; requires rebase/split before merge.
- #271: blocked/dirty due overlap with later routing fixes in #284 and landed commits on `main`; requires rebase or closure.
- #284: blocked/dirty due main drift; likely superseded by landed routing guardrail commits + additional replay updates, needs rebase verdict.

## Verification for this batch
- `python3 -m py_compile src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py`
- `pytest -q tests/test_cf_router_flyer_routing.py -k "delivered_existing_flyer_media_revision or vague_flyer_start_for_active_customer_sends_starter_ideas or legacy_trial_link_complaint or ineligible_customer_status"` (pass: 5)
- `pytest -q tests/test_cf_router_flyer_routing.py -k "sample or starter or trial_link or customer_not_active or delivered_existing_flyer_media_revision"` (pass: 47)
- `git diff --check`
