# Deploy record — `91d675de`

**Drift-check tag:** `Hermes-native` — a deploy record; no runtime code, schema,
skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deploy provenance record | none found — repo convention is `tasks/audits/deploy-authorization-<sha>.md` | use the existing convention (no code) |

Verdict: **documentation only.**

---

**Tag:** `deploy-20260823-060843-91d675de` · **DEPLOY_EXIT=0**
**Contents:** PR #758 only — phase 2 of the reader-first audit-reason migration.

## What this completes

`f8_followup_approve` / `f8_followup_cancel` have been emitted by cf-router
since M5 and were never Literal members, so `audit_intercepted`'s best-effort
`try/except` swallowed every row. An owner answering an M5 follow-up card left
no trace at the audit chokepoint.

Ordering, as required: readers first (#756, deployed `0fff37a6`), verified in
runtime, then the writer (#758, this deploy).

## Runtime verification — the property, not the file

Measured against the deployed `/opt/shift-agent/schemas.py` **after** this
deploy:

| check | result |
|---|---|
| writer constructs `f8_followup_approve` | **accepted** → `CfRouterIntercepted` (previously: silently dropped) |
| writer constructs `f8_followup_cancel` | **accepted** → `CfRouterIntercepted` |
| an unknown future value via the union | still absorbed → `_UnknownReasonCfRouterIntercepted` |

The third row matters as much as the first two: phase 2 must not have removed
the forward-compat property phase 1 added, and it did not.

For contrast, the same probe against the deployed tree **before** this deploy
returned `ValidationError` on both writer constructions — which is the exact
mechanism that was losing the rows.

## Correction carried in this PR

The phase-1 comment (and my own first framing) said a reader older than phase 1
would REJECT such a row. True of the model, **false of production**: a sweep of
31 read sites found every one doing bare `json.loads` with skip-on-error. The
only strict `LogEntry` uses are write paths — `safe_io._emit_audit_row`,
`log-decision-direct`, the amendment and quote-ledger writers — plus the deploy
smoke test, which reads its own temp log. Verified the `safe_io` one directly
rather than on report: it validates, then appends.

So the real rollback blast radius is **semantic drift**, not a crash —
`dispatcher-accuracy-report`'s reason frozenset would silently count the
intercept as unpaired. The reader-first ordering still earned its keep for the
reason that actually holds: repo-side verification cannot see
deployed-but-unversioned code, and widening lands rows immediately because
cf-router already emits these strings. The nuance is now recorded at the schema
site.

## Post-deploy state

| | |
|---|---|
| services | `hermes-gateway` active, `shift-agent-cockpit` active |
| bridge | connected, queue 0 |
| catering leads | 20 — distribution **identical** to pre-deploy |
| deploy receipt | self-updated to `91d675de`, matches `.commit-hash` |
| smoke | all checks passed |

The receipt updating correctly on a **second** consecutive deploy is worth
noting: PR #755's mechanism is not a one-shot.

## Rollback

Anchor `deploy-20260823-052415-0fff37a6`. Rolling back to phase 1 degrades
safely — the shim absorbs rows already written. Rolling back past phase 1 leaves
rows the model would reject, but no production reader validates, so nothing
crashes.

## Unchanged

No live WhatsApp send · transport-evidence harness not executed · no timer
enabled or disabled · TTL sweep unarmed · `deposit_pct: 0` · no OpenRouter
credit · Flyer watchdog runtime user untouched · `hermes update` not run · the
live catering menu defect recorded in
`tasks/audits/live-catering-menu-extraction-defect-2026-08-23.md` is **not**
touched here; correcting owner-approved menu state is an operator action.
