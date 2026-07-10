# Plan — Batch 1: safety-hardening quick wins

**Drift-check tag:** `extends-Hermes` — hardens our own systemd/template/owner-command surfaces;
fights no Hermes convention. Additive copy/config only.

**Authorization:** operator "deliver all backlog items in batches" (2026-07-10). Batch 1 of the
improvement backlog (PR #588). Envelope: TDD → PR, **no merge/deploy**. All three items are additive,
non-behavioral (no coverage-logic, no money, no customer-facing send change).

**Backlog items:** BL-SEC-07, BL-SHIFT-05, BL-SHIFT-10 (from `tasks/audits/improvement-backlog-2026-07-10.md`).

## Hermes-first analysis
| Step | Hermes provides? | Tag |
|---|---|---|
| Backup / coverage / owner-command flows | Yes (existing) — untouched | `[Hermes]` |
| systemd `WorkingDirectory=/` hardening | No — our ops infra | `[net-new]` (1 line) |
| Candidate-template copy edit (drop health reason) | No — our agent copy | `[net-new]` |
| Proposal footer + owner-command `KILL CONFIRM` copy | No — our agent copy | `[net-new]` |

No Hermes primitive for systemd hardening or agent message copy; all trivial edits around existing flows.

## Drift-rule self-checks (deployed code Read before drafting)
- ✅ Read `src/agents/shift/systemd/shift-agent-backup.service` (`User=root`, no `WorkingDirectory`) +
  `shift-agent-backup.sh:31` (`python3 -c "import yaml"` → CWD sys.path-hijack) before the systemd fix.
- ✅ Read `src/agents/shift/templates/coverage_message_to_candidate.txt:3` (leaks `{absent_reason_short}`
  to the coworker) + `proposal_to_owner.txt:5,15` (owner KEEPS the reason; footer advertises KILL) +
  `handle_owner_command/SKILL.md:66,78` (bare `KILL` → `shift-agent-disable`) before the copy edits.

## Changes
1. **BL-SEC-07:** add `WorkingDirectory=/` to `shift-agent-backup.service` (root-owned `/`, no CWD hijack).
2. **BL-SHIFT-05:** remove `({absent_reason_short})` from `coverage_message_to_candidate.txt` — the
   coworker gets "is out {date}" with NO reason. `proposal_to_owner.txt` KEEPS the reason (owner-only).
3. **BL-SHIFT-10:** drop `KILL` from the routine `proposal_to_owner.txt` footer; change
   `handle_owner_command` so bare `KILL` asks for confirmation and only `KILL CONFIRM` disables.

## Tests (invariant — codify the fixes)
`tests/test_batch1_safety_hardening.py` (cross-platform, text-based): backup.service has
`WorkingDirectory=/`; candidate template has NO `absent_reason_short`; proposal (owner) template STILL
has `absent_reason_short` (owner keeps it); proposal footer no longer advertises `KILL`;
`handle_owner_command` requires `KILL CONFIRM`.

## Out of scope
- Deterministic gateway intercept for owner commands (BL-SHIFT-01 — batch 8).
- Localizing the candidate message (BL-SHIFT-09 — later batch).
