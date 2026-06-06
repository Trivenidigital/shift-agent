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

Catering runtime is mostly healthy, but live state still has one stale active pilot/test lead:

```text
catering-pattern-report: 0 findings in last 1d. No-op.
proposal_health: sent=3 selected=1 send_failed=0 select_failed=0
active_missing_info_count: 1
menu_freshness_days: 31
degraded_sources: []
```

State summary:

```text
menu_items: 78
menu_available: 78
menu_updated_at: 2026-05-05T20:36:25.980731-04:00
leads: 15
lead_status_counts:
  1 CUSTOMER_FINALIZED
  3 SENT_TO_CUSTOMER
  3 CLOSED
  8 OWNER_REJECTED
```

The remaining stale active row is from roster employee `e004` (`+19045550104`, Anjali Iyer) and old cf-router F7 rescue data:

```text
L0014 CUSTOMER_FINALIZED created 2026-05-13 updated 2026-05-14
notes: cf-router F7 rescue from missed-dispatch; LLM bypassed parse_catering_inquiry SKILL
event_date: null
```

`L0015` was safely closed through the deployed audited reconcile script on 2026-06-06:

```text
catering-lead-status: L0015 AWAITING_OWNER_APPROVAL -> CLOSED
audit rows: catering_lead_status_change + catering_lead_manually_reconciled
active_missing_info_count: 2 -> 1
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

1. Review and merge the Catering branch patch.
2. Deploy the merged patch to `main-vps`.
3. Close the remaining stale active lead through the standard audited script:

```bash
ssh main-vps 'sudo -u shift-agent /usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/catering-lead-reconcile --lead-id L0014 --target-status CLOSED --reason stale_pre_pilot_cleanup' > .ssh_close_L0014.txt 2>&1
```

4. Rerun `catering-pattern-report --dry-run --learning-days 30` and confirm `active_missing_info_count` is zero or only contains real in-progress customer work.

## Code decision

This is a bounded Catering-owned source patch. It does not touch shared schema, safe IO, cf-router, deploy scripts, or config.
