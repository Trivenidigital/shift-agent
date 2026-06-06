# Catering Production Readiness Evidence - 2026-06-06

**Drift-check tag:** extends-Hermes

**New primitives introduced:** none. This report records one bounded fix to an existing operator script.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Catering routing/intake | Existing `dispatch_shift_agent` and `catering_dispatcher` skills | Reuse deployed routing; no new dispatcher primitive |
| Lead/proposal state | Existing `catering-leads.json`, proposal scripts, schema transitions, audit rows | Reuse current state model |
| Operator cleanup | Existing `catering-lead-reconcile` script | Extend its safe source-status allowlist for stale finalized leads |
| Runtime observability | Existing `catering-pattern-report`, `pilot-readiness-check`, Daily Brief learning summary | Use current reports as authority |

Awesome-Hermes-Agent ecosystem check: no turnkey Catering lead-cleanup capability replaces the repo-native operator reconcile script; use the existing Hermes/state/audit pattern.

## Current result

Updated after deploy `deploy-20260606-182517-076c9d48`
(`076c9d48719df4fd3f2a709f20a4592fcfd4a089`): Catering runtime state is
clean. The stale pilot/test leads were closed through the audited reconcile
path, and the pattern report now shows no active missing-info backlog:

```text
catering-pattern-report: 0 findings in last 1d. No-op.
proposal_health: sent=3 selected=1 send_failed=0 select_failed=0
active_missing_info_count: 0
menu_freshness_days: 31
degraded_sources: []
```

Final stale-lead cleanup:

```text
L0015 AWAITING_OWNER_APPROVAL -> CLOSED
L0014 CUSTOMER_FINALIZED -> CLOSED
audit rows: catering_lead_status_change + catering_lead_manually_reconciled
```

## Finding

`catering-lead-reconcile` could close `L0015` from `AWAITING_OWNER_APPROVAL`, but the deployed script still refuses `L0014` from `CUSTOMER_FINALIZED`:

```text
refusing reconcile from terminal/unsafe status 'CUSTOMER_FINALIZED'
```

That blocks the standard audited operator cleanup path for stale finalized leads.

## Local code fix

Changed `src/agents/catering/scripts/catering-lead-reconcile` so stale `CUSTOMER_FINALIZED` leads can be operator-reconciled only to `CLOSED`. It does not allow `CUSTOMER_FINALIZED` to move backward to `SENT_TO_CUSTOMER` or sideways to `OWNER_REJECTED`.

Regression added in `tests/test_catering_lead_reconcile.py`:

```text
Parse SAFE_FROM_STATUSES and CUSTOMER_FINALIZED_ALLOWED_TARGETS with ast.literal_eval.
Assert CUSTOMER_FINALIZED is not in the general source allowlist and is CLOSED-only.
```

Red/green evidence:

```text
RED: tests/test_catering_lead_reconcile.py failed because CUSTOMER_FINALIZED was still in the general allowlist and CUSTOMER_FINALIZED_ALLOWED_TARGETS was absent.
GREEN: tests/test_catering_lead_reconcile.py - 13 passed.
Focused Catering suite: 156 passed, 58 skipped.
py_compile: src/agents/catering/scripts/catering-lead-reconcile passed.
```

## Required next actions

No Catering-owned code or state cleanup remains from this evidence. Catering is
still covered by the overall customer-pilot blocker in
`pilot-readiness-check --text`: real Pushover credentials must replace the
`MUTED_` values before owner alerts can be considered production-ready.

## Code decision

This is a bounded Catering-owned source patch. It does not touch shared schema, safe IO, cf-router, deploy scripts, or config.
