# End-of-Day Reconcile — Project Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   3 (project)
    Project id: eod-reconcile
    Supplements: docs/governance/engineering-directive.md

Governs `src/agents/eod_reconcile/**`.

---

## Purpose

An end-of-day snapshot: read the day's decisions log and unresolved proposals,
write `state/eod-snapshot.json` atomically, log an `EodSnapshot` audit row, and
alert the owner when unresolved items remain. Driven by `eod-reconcile.timer`
every 15 minutes, self-gating on `cfg.eod.eod_time` in the customer timezone.
The snapshot is consumed by Daily Brief the next morning.

## Hermes capability — reuse

The wording of the owner alert only. No interpretation step is required by
this agent today.

## Deterministic kernels — reuse

| Concern | Deployed owner |
|---|---|
| Fire-time self-gate | `scripts/eod-reconcile` (`cfg.eod.eod_time`) |
| Source of truth | shared decisions log + `state/pending.json` |
| Snapshot write | `safe_io.atomic_write_json` |
| Audit | `EodSnapshot` entry via the shared chokepoint |
| Alerting | shared Pushover/transport path |

## Decision boundary

**May be probabilistic:** the phrasing of the unresolved-items alert.

**Must remain deterministic:** the day window, what counts as unresolved, the
snapshot contents, and audit.

## Presumed NO-GO

- a second snapshot store, or a snapshot schema that diverges from what Daily
  Brief reads;
- a second scheduler alongside the timer;
- deriving reconciliation numbers from a prompt.

## Known missing capability

There is **no POS integration** in v0.1 — register/sales reconciliation is
deferred. Do not describe this agent as reconciling revenue, and do not build a
POS importer here without an approved exception naming the integration.

## Required vertical E2E proof

A timer fire writes a snapshot whose numbers match the day's decisions log, and
the next morning's brief reads it.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial EOD Reconcile directive. |
