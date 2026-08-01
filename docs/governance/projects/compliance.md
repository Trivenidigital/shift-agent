# Compliance Calendar — Project Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   3 (project)
    Project id: compliance
    Lifecycle: pilot (gated by `cfg.compliance.enabled`)
    Supplements: docs/governance/engineering-directive.md

Governs `src/agents/compliance/**`.

---

## Purpose

Track licence, permit and filing deadlines; remind the owner ahead of each
gate; answer owner queries about what is due. Driven by
`check-compliance-deadlines.timer` at 06:00 customer-local.

## Hermes capability — reuse

Understanding the owner's free-text query ("what's due this month", "mark the
health permit done") and phrasing the reminder. Deployed in
`skills/compliance_owner_query/`.

## Deterministic kernels — reuse

| Concern | Deployed owner |
|---|---|
| Deadline gates + catch-up | `scripts/check-compliance-deadlines.py` |
| Idempotency — 3 layers | `FileLock(state/compliance-check.json.lock)`, the `compliance-last-sent.json` sentinel keyed by `(item_id, gate_days)`, and a `ComplianceReminderAttempted` audit row written *before* the bridge POST |
| Deferral policy | `cfg.compliance.max_deferral_days` → `ComplianceReminderDeferred` + Pushover instead of firing on a stale gate |
| Item completion | `scripts/mark-compliance-item-done.py` |
| Audit | `log-decision-direct` |

## Decision boundary

**May be probabilistic:** interpreting the owner's query and phrasing the
reminder.

**Must remain deterministic:** the deadline dates, which gate has fired,
whether a reminder already went out for that `(item_id, gate_days)` pair,
whether a late gate is inside the deferral window, and audit.

## Presumed NO-GO

- a second reminder scheduler or sentinel scheme;
- deriving a legal deadline from a model rather than from configured item data;
- a compliance-local notification path outside the shared transport gate.

## Known missing capability

The agent tracks deadlines the owner has configured. It does **not** know
jurisdiction-specific filing rules, and there is no state-tax-filing
integration. Do not present it as authoritative on what the law requires, and
do not add a filing integration without an approved exception.

## Required vertical E2E proof

A gate crossing produces exactly one reminder, a stale gate produces a
deferral rather than a reminder, and `mark-compliance-item-done` stops the
series.

## Escalation boundaries

Missed-reminder and duplicate-reminder findings are HIGH. Anything implying
legal advice is a product-scope escalation, not a code fix.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial Compliance directive. |
