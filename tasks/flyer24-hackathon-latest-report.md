# Flyer24 Hackathon Latest Report

Updated: 2026-05-24T21:50:00Z

## Batch
- Branch: `codex/flyer24-batch-reference-scope-202605242145`
- Scope: reference-scope false-block reduction for exact source-edit requests.
- Risk: low to medium (routing decision heuristics only, no payment/quota mutation, no provider writes).
- Hermes/MCP-first: Hermes continues to own ingress, dispatch, state, and sends; this batch changes only Flyer decision heuristics inside existing scope-check script and tests.

## Running PR list
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
