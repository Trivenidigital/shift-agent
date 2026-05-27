# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T06:33:00Z

## Current batch
- Branch: `codex/flyer24-batch-payment-contract-reland-202605270625`
- Scope: re-land payment activation contract hardening and MCP readiness metadata from fresh `main`, with focused tests only.
- Root-cause evidence: activation helper accepted unknown providers and readiness catalog missed Razorpay MCP candidate entry.
- Risk: medium (money-adjacent fail-closed validation/readiness metadata; no live checkout/webhook/provider mutation).
- Hermes/MCP-first: Hermes/MCP remain the connector substrate; this batch only hardens local provider-neutral contract checks and read-only readiness catalog metadata.

## PR queue classification (before this batch)
- #280 `fix(flyer): restore cockpit auth override compatibility and source-edit health detail` - mergeable + green; low-risk visibility/auth compatibility.
- #279 `fix(flyer): expand source-edit/manual-queue health visibility` - open with failing CI; not merge-qualified.
- #284 `fix(flyer): resolve cf-router routing and dedupe regressions` - open, mergeable, no CI reported.
- #271 `fix(flyer): restore account/manual-edit routing compatibility` - conflicting.
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility` - conflicting + failed CI; money-adjacent.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog` - conflicting; money-adjacent.
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior` - conflicting.

## Running PR list (hackathon)
- #254 `fix(flyer): restore CTA/account routing and intake ack fail-closed behavior` - closed as superseded.
- #256 `fix(flyer): tighten payment activation contract and MCP readiness catalog` - closed (to re-land cleanly on latest main).
- #268 `fix(flyer): harden billing provider readiness and cockpit visibility` - closed as superseded by #291 and follow-ons.
- #271 `fix(flyer): restore account/manual-edit routing compatibility` - closed as superseded.
- #279 `fix(flyer): expand source-edit/manual-queue health visibility` - closed as superseded by #291.
- #280 `fix(flyer): restore cockpit auth override compatibility and source-edit health detail` - closed as superseded by #291.
- #284 `fix(flyer): resolve cf-router routing and dedupe regressions` - closed as superseded.
- #291 `fix(flyer): consolidate cockpit auth compatibility and manual-queue health visibility` - merged.
- Pending new PR (this batch): payment contract re-land branch above.

## PR queue classification refresh (post-main@91274c4)
- #280: superseded by this consolidated branch (same target files, cleanly rebased on latest main).
- #279: superseded by this consolidated branch (same health impact/detail expansion, plus auth-override compatibility retained).
- #268: blocked for this lane (money-adjacent and mixed with broad non-Flyer `safe_io` bridge refactor); keep open for operator decision or split.
- #256: operator-review-required (money-adjacent activation contract/readiness hardening).
- #254: blocked/dirty due main drift and overlap with later routing fixes now partly landed in #287/#288; requires rebase/split before merge.
- #271: blocked/dirty due overlap with later routing fixes in #284 and landed commits on `main`; requires rebase or closure.
- #284: blocked/dirty due main drift; likely superseded by landed routing guardrail commits + additional replay updates, needs rebase verdict.

## Verification for this batch
- `python3 -m py_compile src/agents/flyer/payment_state.py src/platform/credential_readiness.py tests/test_flyer_payment_state.py tests/test_credential_readiness.py`
- `pytest -q tests/test_flyer_payment_state.py tests/test_credential_readiness.py -k "activation_event or payment_mcp_candidates_include_stripe_and_razorpay"` (pass: 6)
- `pytest -q tests/test_flyer_guest_order.py -k "amount or currency or provider or payment_reference"` (pass: 8)
- `pytest -q tests/test_flyer_onboarding.py -k "payment_reference or pending_plan_payment_state or upgrade"` (pass: 4)
- `git diff --check`
