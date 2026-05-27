# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T18:32:00Z

## Current batch
- Branch: `codex/flyer24-batch-payment-contract-consolidation-202605271825`
- PR: pending create
- Deploy: not run (PR stage)
- Scope: payment contract fail-closed hardening + MCP readiness parity without live credential/payment mutations.
- Root-cause evidence:
  - `activation_event_state` previously accepted unknown providers and currency fallback edge cases.
  - account/guest-order activation paths treated casing/whitespace provider drift as invalid and persisted untrimmed references.
  - readiness connector candidates lacked explicit Razorpay MCP row despite MCP-first payment policy.
- Risk: medium (money-adjacent payment/account lifecycle validation paths).
- Hermes/MCP-first: Hermes owns ingress/state/audit/connector substrate; net-new is Flyer-local contract validation + readiness metadata only.

## Batch issue list fixed
1. `activation_event_state` now fails closed for unknown provider.
2. `activation_event_state` now fails closed when expected currency is blank.
3. `activation_event_state` now requires explicit non-manual event currency and rejects blank fallback.
4. Account activation now normalizes provider casing/whitespace before validation.
5. Account/guest-order activation now normalize payment-reference whitespace before dedupe/idempotency persistence.
6. Credential readiness connector catalog now includes official Razorpay MCP candidate alongside Stripe MCP.

## PR queue classification refresh
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - superseded by this batch (conflicting duplicate scope).
- #299 `fix(flyer): harden billing health MCP readiness visibility` - operator-review-required (money-adjacent), conflicting with main; keep open for rebase or follow-up.
- #<pending> `fix(flyer): consolidate payment fail-closed contract and MCP readiness parity` - pending open; operator-review-required.

## Running PR list (hackathon)
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - superseded/close after new PR opens.
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged; deployed `deploy-20260527-102236-c858caa1`.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged; deployed `deploy-20260527-112213-f019b345`.
- #297 `fix(flyer): route sample-request tagline/slogan variants to idea intake` - merged; deployed `deploy-20260527-121058-87db7152`.
- #298 `fix(flyer): close render dependency and recovery alert gaps` - merged.
- #299 `fix(flyer): harden billing health MCP readiness visibility` - open; operator-review-required.
- #303 `fix(flyer): route sample ask-shape variants to starter ideas` - merged.
- #304 `fix(flyer): route missed sample request phrase variants to starter ideas` - merged.
- #305 `fix(flyer): normalize manual queue triage status and reason signals` - merged.
- #306 `fix(flyer): expand manual queue health backlog signals` - merged.
- #<pending> `fix(flyer): consolidate payment fail-closed contract and MCP readiness parity` - pending open.

## Verification for this batch
- `python3 -m py_compile src/agents/flyer/payment_state.py src/agents/flyer/account.py src/agents/flyer/guest_order.py src/platform/credential_readiness.py tests/test_flyer_payment_state.py tests/test_flyer_onboarding.py tests/test_flyer_guest_order.py tests/test_credential_readiness.py` ✅
- `pytest -q tests/test_flyer_payment_state.py` ✅ (4 passed)
- `pytest -q tests/test_flyer_guest_order.py -k 'activation or replay or mismatch or amount or currency or invalid_provider or normalizes_provider_and_payment_reference'` ✅ (13 passed)
- `pytest -q tests/test_flyer_onboarding.py -k 'payment_reference_already_used or replay_mismatch or replay_not_active or normalizes_provider_and_payment_reference'` ✅ (2 passed)
- `pytest -q tests/test_credential_readiness.py -k 'payment_mcp_candidates_include_stripe_and_razorpay'` ✅ (1 passed)
- `git diff --check` ✅
