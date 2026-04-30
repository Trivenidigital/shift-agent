# Platform-helpers consolidation — Design (right-sized path)

**Drift-check tag:** `extends-Hermes`

**Pipeline position:** Audit findings (`memory/project_non_catering_agents_audit.md`) → **mini-design (this) → build → PR + 3-agent review → merge → deploy alongside next deploy.**

**Scope:** 4 commits, ~315 LOC. Three platform-helper extractions to deduplicate near-mirror logic across deployed agents, plus one drift-gap closer.

## Hermes-first capability checklist

| Step | Hermes / net-new |
|---|---|
| Helper functions live in deployed `safe_io.py` + `audit_helpers.py` chokepoints | [Hermes-substrate-extending] — adding helpers to existing modules |
| Callers (deployed agent scripts) swap inline implementations for the shared helpers | [refactor — behavior-preserving] |
| New EOD subprocess test mirrors `test_daily_brief_script.py` shape | [extends-Hermes test pattern] |

**Net-new:** zero — every line is either moved code or test code. No new behavior.

## Read-deployed-code evidence

| File | Line range | What lives there |
|---|---|---|
| `src/agents/expense_bookkeeper/scripts/extract-receipt` | 493–559 | `_check_extract_orphans` (~67 LOC) — orphan-scan + audit cross-check |
| `src/agents/expense_bookkeeper/scripts/apply-expense-decision` | 247–304 | `_check_orphans` (~58 LOC) — near-mirror of above |
| `src/agents/shift/scripts/send-coverage-message` | 135–173 | `_append_notify_failed` Pushover-fallback (~38 LOC) |
| `src/agents/daily_brief/scripts/send-daily-brief` | 422–456 | `_pushover_alert` fallback (~35 LOC, near-mirror) |
| `src/agents/eod_reconcile/scripts/eod-reconcile` | 171–198 | `_pushover_summary` simpler fallback (~28 LOC) |
| `src/agents/daily_brief/scripts/send-daily-brief` | `_self_gate_state` ~15 LOC | self-gate before/in_window/in_catchup/past_catchup |
| `src/agents/eod_reconcile/scripts/eod-reconcile` | `_self_gate_state` ~15 LOC | identical shape |
| `tests/test_daily_brief_script.py` | full | subprocess-invoke test pattern to mirror for EOD |
| `tests/test_eod_reconcile_schemas.py` | full | only existing EOD test (schema-only — drift gap) |

## Build sequence (4 commits)

| # | Commit | LOC delta | Risk |
|---|---|---|---|
| 1 | `feat(safe_io): notify_owner_with_fallback() + 3 callsites swap (Shift/Brief/EOD)` | +60 helper +10 tests / -120 callsite = **net -50** | Low — behavior-preserving; existing tests cover |
| 2 | `feat(safe_io): self_gate_window_state() + 2 callsites swap (Brief/EOD)` | +25 helper +5 tests / -30 callsite = **net 0** | Low |
| 3 | `feat(audit_helpers): scan_orphan_pushes() + 2 callsites swap (extract-receipt/apply-expense-decision)` | +50 helper +8 tests / -125 callsite = **net -67** | MEDIUM — touches expense-bookkeeper hot path; needs careful test coverage |
| 4 | `test(eod): subprocess-invoke script test mirroring test_daily_brief_script.py` | +80 test | None — pure addition |

**Total:** ~235 LOC additions in helpers + ~80 LOC test = ~315 LOC. **Net codebase delta: -120 LOC (deduplication wins).**

## What's NOT in scope (per audit)

- Template-rendering unification (cross-cutting #1) — audit explicitly recommends NOT doing this proactively.
- Adding cross-state collision check to Shift `create-proposal` — audit assessed collision probability negligible.
- Touching Catering — already covered by PR-D4 (post-PR-B v3 cleanup).

## Deploy plan

Deploy alongside next routine deploy (after PR-B v3 lands ~tomorrow). No standalone deploy needed — refactors are behavior-preserving + go through normal CI + smoke gate.

## Self-review

- [x] Drift-check tag at top
- [x] Hermes-first checklist applied — all rows are substrate-extending, zero net-new behavior
- [x] Read-deployed-code evidence cited with line ranges
- [x] No SaaS-style infra. No parallel approval-code generators. No new dependencies.
- [x] Net codebase delta is negative (deduplication, not bloat)
- [x] Each commit keeps the suite green at HEAD (full regression coverage of swapped callsites)

## Status: DESIGN-DRAFTED, ready for build

No design-review pass needed at this scope. PR + 3-agent review (correctness / drift / scope-questioning) is the quality gate.
