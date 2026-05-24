# Flyer24 Hackathon Latest Report

Updated: 2026-05-24T22:44:00Z

## Batch
- Branch: `codex/flyer24-batch-manual-queue-visibility-202605242236`
- Scope: manual-queue stale visibility + minute-level age precision across helper, health payload, and Cockpit UI.
- Risk: low (read-only diagnostics/UI; no payment/quota/provider/project mutation behavior changes).
- Hermes/MCP-first: Hermes continues to own ingress/routing/state/audit; this batch adds only read-only Flyer diagnostics and Cockpit rendering.

## Running PR list
- #210 - fix(flyer): manual-queue stale visibility + minute precision
- #208 - fix(flyer): fail closed for payment readiness
- #207 - fix(flyer): route explicit sample-idea asks through intake
- #192 - fix(flyer): classify 'my updated flyer' as status check
- #191 - fix(flyer): allow reference-scope allow when flyer shows account address
- #189 - test(flyer): forbid trial/quota leakage in manual ack copy
- #188 - test(flyer): pin category-price revision routing
- #187 - feat: add Flyer Studio concierge intake
- #185 - test-repair-tarball-gate-harness-drift
- #183 - fix(flyer): route where-are-updates check-ins to status
- #182 - fix(flyer): accept plural status check-in wording
- #181 - test(flyer): pin no-spend source-edit ownership bypass
- #180 - test(flyer): add incident replay for update-flyer status wording
- #179 - fix(flyer): treat updated flyer as status check
- #164 - fix(cockpit): auto-login when auth bypass enabled
