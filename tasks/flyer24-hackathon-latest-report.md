# Flyer24 Hackathon Latest Report

Updated: 2026-05-25T11:52:00Z

## Batch
- Branch: `codex/flyer24-batch-self-eval-stale-reason-coverage-202605251145`
- Scope: broadened self-eval stale manual-queue incident coverage to include `missing_required_facts` and `reference_*` queue reasons, with reason-aware suggested actions and updated rollout-threshold CLI help text.
- Risk: low (read-only self-eval/reporting behavior + tests only; no payment/account/quota/runtime mutation).
- Hermes/MCP-first: Hermes continues to own ingress/routing/audit/state substrate; this batch only updates Flyer read-only incident taxonomy/reporting policy.

## Running PR list
- #216 - fix(flyer): harden guest-order payment activation contracts (operator-review-required; payment-adjacent)
- #223 - fix(flyer): broaden stale manual-queue self-eval coverage (open; low-risk read-only reporting, merge/deploy eligible after checks/review)
- #187 - feat: add Flyer Studio concierge intake (non-merge-qualified; dirty/conflict broad scope)
- #185 - test-repair-tarball-gate-harness-drift (non-merge-qualified draft)
- #219 - fix(flyer): generalize stale manual queue readiness/reporting (merged)
- #220 - fix(flyer): route business idea asks to sample prompts (merged)
