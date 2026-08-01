# Daily Brief — Project Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   3 (project)
    Project id: daily-brief
    Supplements: docs/governance/engineering-directive.md

Governs `src/agents/daily_brief/**`.

---

## Purpose

A scheduled morning summary to the owner's WhatsApp self-chat, plus customer
birthday capture that feeds it. Driven by `send-daily-brief.timer` every 15
minutes, self-gating on `cfg.daily_brief.brief_time` in the customer timezone.

## Hermes capability — reuse

Summarization and the prose of the brief. Aggregated facts come from
deterministic state; only their phrasing is a model concern.

## Deterministic kernels — reuse

| Concern | Deployed owner |
|---|---|
| Send window self-gate | `scripts/send-daily-brief` (`cfg.daily_brief.brief_time`) |
| Idempotency — 3 layers | `FileLock(state/last-brief-sent.json.lock)`, the `last-brief-sent.json` sentinel, and a `BriefAttempted` audit row written *before* the bridge POST |
| Aggregation sources | shared decisions log, `state/pending.json`, EOD snapshot |
| Send eligibility | shared `automation_control.py` / transport evidence |
| Audit | `log-decision-direct` |
| Birthday capture | `scripts/record-customer-birthday` |

## Decision boundary

**May be probabilistic:** the wording and ordering of the brief.

**Must remain deterministic:** whether it is time to send, whether today's
brief already went out, what the underlying numbers are, and audit.

**Deployed invariant — do not weaken:** the attempt-before-send audit anchor.
If a `BriefAttempted` row exists within 30 minutes with no matching
`BriefSent`, the script refuses to auto-resend and requires operator
verification. Do not "fix" a missing brief by removing this.

## Presumed NO-GO

- a second scheduler alongside the systemd timer;
- a second idempotency mechanism, or replacing the three-layer one;
- computing brief numbers in a prompt rather than reading deterministic state;
- a brief-local send path bypassing the shared transport gate.

## Required vertical E2E proof

A timer fire on a real VPS produces exactly one brief with correct numbers and
the matching audit rows, and a second fire in the same window produces none.

## Escalation boundaries

Duplicate-send and wrong-number findings are HIGH or above — the owner treats
this message as ground truth for the day.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial Daily Brief directive. |
