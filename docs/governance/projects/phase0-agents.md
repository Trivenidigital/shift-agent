# Phase-0 Agent Family — Product-Family Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   3 (project family)
    Project id: phase0-agents
    Lifecycle: scaffold — every member is `cfg.<agent>.enabled = False`
    Supplements: docs/governance/engineering-directive.md

Covers nine agents that are, today, configurations of one common shape rather
than distinct systems. One directive governs all of them; there is deliberately
no per-agent file, because there is not yet per-agent architecture to govern.

**Members:** `cash_ar`, `employee_docs`, `equipment_maintenance`, `hiring`,
`inventory`, `pnl_anomaly`, `sales_tax`, `supplier`, `vip`.

---

## What actually exists

Each member is a single dispatcher `SKILL.md` (roughly 20–30 lines) that:

1. declares the intended Phase-1 behavior for the portfolio;
2. self-declines while `cfg.<agent>.enabled` is `False`;
3. logs `agent_declined` with `agent="<name>"` and
   `reason="agent_disabled"` through `log-decision-direct` **before** the
   decline reply.

There is no deterministic kernel, no store, no script and no timer for any
member. Do not write a directive, a plan or an estimate that implies otherwise.

## Rules for this family

1. **The self-decline contract is the deployed behavior.** A change that lets a
   disabled member act, or that skips the `agent_declined` audit row, is a
   BLOCKER.
2. **Activation is not a code change alone.** Moving a member to Phase 1
   requires: a per-step `[reuse]` / `[net-new]` pass against the universal
   directive §1, its own project directive and registry entry (promoting it out
   of this family), and a vertical E2E proof. Until then it stays here.
3. **Reuse before scaffolding.** Every capability these agents will need —
   vision extraction, approval codes, owner notification, scheduled sweeps,
   audit, role gating — already exists on the shared platform. A Phase-1 build
   that marks most of its steps net-new has missed something; re-check before
   proceeding.
4. **Do not build a shared "stub framework".** Nine ~25-line files do not
   justify an abstraction. Introducing a base class, a registry or a generator
   for them is exactly the infrastructure-instead-of-outcome pattern the
   universal directive §5 rejects.
5. **Honesty about dependencies.** Some members declare Phase-1 triggers that
   are not wired — e.g. Catering Follow-up's lead-closure hook. Where a SKILL
   carries an honesty note, keep it; do not delete it to make the scaffold read
   as finished.

## Decision boundary

**Must remain deterministic, even in Phase 0:** the enabled check, the
`agent_declined` audit row, and sender identity if the member is ever reached.

**Presumed NO-GO for every member:** an agent-local store, an agent-local
approval mechanism, an agent-local notification path, or an outbound send
while disabled.

## Standing hard rules carried from the scaffolds

These are already written into the member SKILLs and must survive any edit:

- **Cash & AR:** all outbound reminders require owner approval in Phase 0–1;
  collections are owner-only and never agent-initiated.
- **VIP:** privacy creep is the primary risk — never expose more than staff or
  the owner already knows; all outbound VIP messages require owner approval.

## Promotion checklist (family → own project)

- [ ] Own directive under `docs/governance/projects/`
- [ ] Own registry entry with real source/test/ops paths
- [ ] Removed from this family's `members` and `paths`
- [ ] Vertical E2E proof recorded
- [ ] Reuse map showing which shared capabilities carry which steps

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial Phase-0 family directive covering 9 scaffold agents. |
