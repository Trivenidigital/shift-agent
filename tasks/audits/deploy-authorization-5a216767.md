# Deploy record — `5a216767`

**Drift-check tag:** `Hermes-native` — a deploy record. No runtime code, schema,
skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deploy provenance record | none found — Hermes has no deploy-record primitive; this repo's convention is `tasks/audits/deploy-authorization-<sha>.md` | use the existing repo convention (no code) |

awesome-hermes-agent ecosystem check: nothing in the ecosystem records this
project's deploy provenance, and a generic one could not carry the box-specific
evidence below. Verdict: **documentation only, no net-new engineering.**

---

**Tag:** `deploy-20260823-033350-5a216767` · **DEPLOY_EXIT=0**
**Artifact:** `shift-agent-deploy.tgz`, sha256 `27809c57feb6ffae918ff901…`,
6,652,602 B, 524 entries, `.commit-hash` = `5a216767ea62591e5be89399549d5cb09d9d6251`
**Contents:** PR #752 only — catering owner-approval reachability.

## Authorisation basis

Within the standing autonomous-deploy criteria: inside the established
architecture; **no schema change** and no data migration; no new external or
irreversible capability; no payment capability (deposits stay `deposit_pct: 0`);
rollback known; artifact built from the exact merged `main`; pre-deploy state
captured below.

## Build note — stated rather than glossed

`tools/build-deploy-tarball.sh` was run with `--skip-pytest`. Its gate is a
monolithic `pytest tests/`, which this repo has never had green: the run
produced 94 failures, the documented co-residency artifact class. The repo gates
via curated per-subset CI instead, and **all 8 checks passed on this exact
commit**, including the new deterministic catering lifecycle gate. The substitute
is stronger than the skipped gate, but the skip is recorded because a future
reader should not infer the full suite was green.

## Pre-deploy state

| | |
|---|---|
| previous deploy | `deploy-20260822-224523-6a1f128f` |
| services | `hermes-gateway` active, `shift-agent-cockpit` active |
| bridge | connected, queue 0 |
| catering leads | 20 — AWAITING_OWNER_APPROVAL 3, CLOSED 4, CUSTOMER_FINALIZED 2, OWNER_REJECTED 8, SENT_TO_CUSTOMER 3 |
| audit rows | 32 |

## Runtime verification

A `/health -> 200` is not verification, so the deployed wrapper's own routing
decision was exercised directly. The control replaces `subprocess.run`, so the
apply-script never executes, no state file is opened and nothing is sent — what
runs is the **deployed** `/root/.hermes/plugins/cf-router/actions.py`, the file
Hermes actually loads.

| lead shape | result |
|---|---|
| AWAITING + no finalize + no items — **the repaired case** | script invoked with **no quote flag**, rc=11 |
| AWAITING + `customer_finalized_at` set | script NOT invoked, rc=2 (unchanged) |
| CUSTOMER_FINALIZED, no items | script NOT invoked, rc=2 (unchanged) |
| OWNER_APPROVED replay | script NOT invoked, rc=2 (unchanged) |
| delivery-uncertain shape | script NOT invoked, rc=2 (unchanged) |
| CUSTOMER_FINALIZED + items — happy path | `--quote-from-lead-state` |
| real `quote_text` — legacy path | `--quote-text-stdin` |

`--skip-finalize` absent from argv on **all seven**.

The four "NOT invoked" rows are the point of the review round: those shapes can
reach `EXIT_OK` and would have closed the turn while telling the owner nothing.
They are excluded at the deployed wrapper, not merely in tests.

## Post-deploy state — nothing moved

| | |
|---|---|
| catering leads | 20 — **identical distribution**, no lead changed status |
| deposits | `deposit_pct: 0` |
| `catering-followup-sweep.timer` | still `disabled` |
| bridge | connected, queue 0 |
| audit rows | 32 → 42; all ten are routine — 3 `brief_skipped`, 3 `eod_skipped` (timer idempotency), 2 `owner_alert_dispatched` + 2 `owner_alert_delivered` from the deploy's own Pushover smoke. **No catering row.** |

Deploy smoke: all checks passed, including `cf-router plugin compiles + actions
importable`, catering schema + transition table, and 78 available menu items.
One pre-existing warning unrelated to this change: Agent #21 venv absent, so
expense-bookkeeper smoke checks skip.

## Rollback

Anchor: `deploy-20260822-224523-6a1f128f`. Both mixed-version directions are
safe and were reasoned through before shipping — new plugin + old script yields
no stdout JSON, so `payload` is `None` and the path degrades exactly to
pre-change behaviour; new script + old plugin never reaches the new line at all.
No schema change and no new audit tag, so none of the four rollback categories
applies.

## Unchanged by this deploy

No live WhatsApp test was run. The transport-evidence harness was not executed.
TTL sweep not armed. Quote scaling (`qty=1`) untouched. Reject/edit customer
silence untouched. OpenRouter remains at −$0.18 — an operator dependency, not
worked around here.
