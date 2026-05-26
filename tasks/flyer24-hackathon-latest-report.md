# Flyer24 Hackathon Latest Report

Updated: 2026-05-26T13:21:30Z

## Batch
- Branch: `codex/flyer-sla-chat-dedupe`
- Scope: dedupe stale manual-queue customer SLA updates by chat within watchdog runs.
- Risk: low (watchdog customer-update throttling only; no payment/account/quota/manual-queue closure/send mutation beyond duplicate suppression).
- Hermes/MCP-first: Hermes owns ingress/identity/state/audit/storage; this batch changes only Flyer watchdog dedupe policy.

## Running PR list
- #262 - fix(flyer): dedupe stale edit updates by chat (merged and deployed: `deploy-20260526-132023-7cc6f63b`)
- #256 - fix(flyer): tighten payment activation contract and MCP readiness catalog (open; operator-review-required; payment-adjacent)
- #254 - fix(flyer): restore CTA/account routing and intake ack fail-closed behavior (open; operator-review-required; routing/payment-surface change)
