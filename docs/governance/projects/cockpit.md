# Operator Cockpit — Project Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   3 (project)
    Project id: cockpit
    Supplements: docs/governance/engineering-directive.md

Governs `web/backend/**`, `web/frontend/**`, `web/portal/**` and
`web/deploy/**`. Product panels inside the cockpit that belong to another
product — `web/backend/app/routers/catering.py`,
`web/frontend/src/sections/catering/**`, `.../flyer/**`,
`routers/flyer.py`, `routers/commerce.py`, `CommerceOrders.tsx` — are
registered to those products and carry their directives instead.

---

## Purpose

A FastAPI + React operator surface over the agent fleet on a customer VPS:
roster, schedule, pending approvals, decisions/audit, safety controls,
WhatsApp state, disclosures, config and health.

## Model capability — reuse

None by default. The cockpit is a view-and-act surface over deterministic
state; it is not a place to add an LLM call. If an operator-facing explanation
is wanted, reuse the owning product's existing Hermes capability rather than
introducing generation in the web tier.

## Deterministic kernels — reuse, do not fork

| Concern | Deployed owner |
|---|---|
| Auth / session / TOTP | `web/backend/app/auth.py`, `totp.py` |
| Authorization dependencies | `web/backend/app/deps.py` |
| State reads | `web/backend/app/state.py` over the same on-disk state the agents own |
| Audit | `web/backend/app/audit.py` → shared decisions log |
| Safety controls / kill switches | `routers/safety.py` → `src/platform/automation_control.py` |
| Deploy | `web/deploy/deploy.sh`, `shift-agent-cockpit.service`, `Caddyfile` |

## Decision boundary

**May be probabilistic:** nothing, by default.

**Must remain deterministic:** authentication, authorization, every state
mutation, approval actions taken from the UI, kill-switch actions, and audit.
A cockpit action that mutates agent state must go through the same
deterministic script or module the agent uses — never a second write path.

## Presumed NO-GO

- writing agent state directly from a router instead of through the owning
  product's script/module;
- a cockpit-local copy of an approval, proposal or order store;
- a parallel auth mechanism alongside the deployed one;
- adding a product's business logic to the web tier because it is convenient
  to render there;
- shipping a frontend change whose backend contract is not in the generated
  OpenAPI surface.

## Required vertical E2E proof

An operator performs the action in the running cockpit and the corresponding
agent-side state and audit row change. `web/backend/tests/**` plus the
cockpit CI job is the regression floor, not the proof.

## Escalation boundaries

Auth, authorization and CSV/formula-injection findings are BLOCKER-class. Any
change touching `routers/safety.py` or kill switches is HIGH or above.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial Cockpit directive. |
