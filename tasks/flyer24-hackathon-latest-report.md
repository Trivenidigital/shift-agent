# Flyer24 Hackathon Latest Report

Updated: 2026-05-25T09:44:00Z

## Batch
- Branch: `codex/flyer24-batch-recovery-ack-guardrails-202605250940`
- Scope: added fail-closed recovery customer-ack guardrails so closed incidents, missing chat ids, stale incidents, and malformed timestamps do not send proactive customer messages.
- Risk: low (recovery no-send policy + tests only; no payment/account/quota/runtime mutation).
- Hermes/MCP-first: Hermes continues to own ingress, bridge send, and audit substrate; this batch only tightens Flyer deterministic ack eligibility rules.

## Running PR list
- #216 - fix(flyer): harden guest-order payment activation contracts (operator-review-required; payment-adjacent)
- #187 - feat: add Flyer Studio concierge intake (non-merge-qualified; dirty/conflict broad scope)
- #185 - test-repair-tarball-gate-harness-drift (non-merge-qualified draft)
- #219 - fix(flyer): generalize stale manual queue readiness/reporting (merged)
- #220 - fix(flyer): route business idea asks to sample prompts (merged)
