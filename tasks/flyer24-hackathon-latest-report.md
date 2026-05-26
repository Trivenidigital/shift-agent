# Flyer24 Hackathon Latest Report

Updated: 2026-05-26T06:30:00Z

## Batch
- Branch: `codex/flyer24-batch-source-edit-watchdog-visibility-202605260625`
- Scope: harden source-edit/manual-review stale queue observability by surfacing reason/status splits, oldest queue timestamp, customer-update summary counts, and triage status distribution.
- Risk: low (read-only visibility + watchdog message/result shape; no payment/account/quota/customer-send mutation).
- Hermes/MCP-first: Hermes still owns ingress/identity/bridge/state/audit substrate; this batch only improves deterministic Flyer operator visibility on top.

## Running PR list
- #255 - fix(flyer): harden source-edit watchdog and triage visibility (open; low-risk visibility hardening)
- #254 - fix(flyer): restore CTA/account routing and intake ack fail-closed behavior (operator-review-required; routing-surface change)
- #216 - fix(flyer): harden guest-order payment activation contracts (operator-review-required; payment-adjacent)
- #223 - fix(flyer): broaden stale manual-queue self-eval coverage (merged + deployed in `deploy-20260525-113843-be846ebf`)
- #187 - feat: add Flyer Studio concierge intake (non-merge-qualified; dirty/conflict broad scope)
- #185 - test-repair-tarball-gate-harness-drift (non-merge-qualified draft)
- #219 - fix(flyer): generalize stale manual queue readiness/reporting (merged)
- #220 - fix(flyer): route business idea asks to sample prompts (merged)
