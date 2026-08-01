# Shift Agent (scheduling) — Project Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   3 (project)
    Project id: shift-agent
    Supplements: docs/governance/engineering-directive.md

Governs only the paths registered to `shift-agent` in
`docs/governance/project-registry.yaml` — the four non-dispatcher shift skills,
the message templates, the config template and the runbook.

> **Boundary warning.** This product shares a directory tree with the shared
> platform. `src/agents/shift/skills/dispatch_shift_agent/`,
> `src/agents/shift/scripts/` and `src/agents/shift/systemd/` are **shared
> platform**, not this product — they carry routing, deploy, health, backup and
> fsck for every agent. Changing them invokes
> `docs/governance/shared-platform-directive.md` and its affected-agent
> obligations.

---

## Purpose

When an employee calls out sick, find coverage: intake the sick call, look up
the roster, propose the shift to eligible candidates, process their replies,
and let the owner command the outcome.

## Hermes capability — reuse

Absence-intent detection in free text, the wording of coverage offers and
owner replies, interpreting an ambiguous candidate response, and summarizing
roster facts. Deployed in `handle_sick_call/`, `handle_candidate_response/`,
`handle_owner_command/`, `roster_lookup/`.

## Deterministic kernels — reuse

| Concern | Deployed owner |
|---|---|
| Sender identity (phone or LID) | shared `identify-sender` / `validate-sender-block` |
| Routing | shared `dispatch_shift_agent` matrix |
| Proposal state | `src/platform/proposal_sweep.py`, `state/pending.json`, `create-proposal`, `update-proposal-status` |
| Approval codes | shared `approval_code_pools.py` |
| Send eligibility / throttle | shared `automation_control.py`, `transport_evidence*.py` |
| Persistence + locking | `safe_io.py` |
| Audit | `log-decision-direct` |

## Decision boundary

**May be probabilistic:** whether a message is an absence report, how a
coverage offer is phrased, interpreting "can't tonight, maybe Friday".

**Must remain deterministic:** who the employee is, who is eligible for a
shift, which proposal a reply belongs to, proposal state transitions, whether
an outbound message may be sent, and audit.

## Presumed NO-GO

- a second proposal or pending store;
- a scheduling-specific approval-code generator;
- a bespoke roster store parallel to the deployed roster;
- routing logic inside a handler skill rather than the shared dispatcher
  matrix.

## Required vertical E2E proof

A real sick-call message routes, produces a proposal to a real candidate, and
the candidate's reply updates state — with `dispatcher_routed` and decision
rows to show it.

## Escalation boundaries

Employee-facing sends are real messages to real staff: a wrong-recipient or
duplicate-send finding is BLOCKER-class. Roster and identity correctness
findings are HIGH or above.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial Shift Agent directive. |
