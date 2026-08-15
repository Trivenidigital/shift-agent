# Catering — Rollback Runbook (post-lead-write)

Last updated: 2026-07-31

Scope: rolling the **Catering Studio** release back to `dc7a81a2` **after it has
already written catering state**. For the general lightest-lever-first ladder see
`docs/runbooks/rollback.md`; this file covers the one step that ladder does not.

## The problem this exists for

The Studio release added 12 fields and 2 statuses (`QUALIFYING`, `BOOKED`) to
`CateringLead`, and `expires_at` plus 2 statuses (`EXPIRED`, `SEND_UNCERTAIN`) to
`CateringProposalSet`. In `dc7a81a2` both models are `extra="forbid"`, and its
status Literals name none of the four new statuses.

So **one** lead written by the new release makes
`/opt/shift-agent/state/catering-leads.json` unreadable by the old code. The old
`safe_io.load_model` raises, and cf-router's tolerant raw-JSON lookups degrade to
"no lead" **without erroring** — a live customer's lead simply stops being found.

Same for `catering-proposals.json`, and it does **not** take a rare edge case to
get there. Any of these is enough on its own: a set carrying `expires_at`; a set
that reached `EXPIRED`, which happens whenever a customer replies after the
validity window; or a set that reached `SEND_UNCERTAIN`, which happens whenever
the bridge accepts a send without returning a usable acknowledgement.

`catering-state-downgrade` is the reverse migration that fixes this. It is
**lossless**: everything it strips or remaps is written to a per-store sidecar
first.

## This is a MANUAL step — the deploy script does not do it

`shift-agent-deploy`'s smoke-test failure path auto-rolls back to the previous
tarball. That auto-revert **does not** run this migration, and it does not need
to: it fires mid-deploy, seconds after the restart and before the new release has
served any inbound traffic, so no lead can carry new-format state yet.

The case this runbook covers is different — the new release ran, took catering
traffic, wrote leads, and is being rolled back **later**. Nothing automatic
detects that. The operator runs the migration by hand, from the NEW tree, before
the tarball rollback.

## Sequence

Run every step as the deploy user on the target VPS. Steps 1–4 happen while the
NEW release is still installed — the script only exists there.

**1. Stop the gateway.** No catering writer may run while the stores are rewritten.

```
systemctl stop hermes-gateway
```

**2. Dry-run the migration.** Writes nothing at all — no store, no sidecar, no
audit row. Read the output before proceeding.

```
/usr/local/bin/catering-state-downgrade --dry-run
```

**3. Run it for real.**

```
/usr/local/bin/catering-state-downgrade
```

Expect one `downgraded ...` line per rewritten store, each naming its sidecar.
`nothing to do` means the state is already old-readable — that is a valid result,
not a failure. Exit 0 is success; exit 7 means a store could not be parsed and
**nothing was rewritten** — stop and investigate before rolling back.

**4. Verify against the OLD schema before you roll back.** This is the step that
actually proves the rollback will work, so do not skip it.

The VPS has no git checkout (tarball deploy), so materialise the old schema module
on your workstation and copy it over:

```
# workstation, in the repo
git show dc7a81a2:src/platform/schemas.py > old_schemas.py
scp old_schemas.py root@<vps>:/tmp/old_schemas.py
```

Then, on the box:

```
/opt/shift-agent/venv/bin/python -c "
import importlib.machinery, importlib.util, json, sys
l = importlib.machinery.SourceFileLoader('old_s', '/tmp/old_schemas.py')
s = importlib.util.spec_from_loader('old_s', l); m = importlib.util.module_from_spec(s)
sys.modules['old_s'] = m; l.exec_module(m)
for name, path in (('CateringLeadStore', 'catering-leads.json'),
                   ('CateringProposalStore', 'catering-proposals.json')):
    p = '/opt/shift-agent/state/' + path
    try:
        getattr(m, name).model_validate(json.load(open(p)))
        print('VALID  ', path)
    except FileNotFoundError:
        print('ABSENT ', path)
    except Exception as e:
        print('INVALID', path, str(e)[:300]); sys.exit(1)
"
```

Every store must print `VALID` or `ABSENT`. A single `INVALID` means the rollback
would break catering — do not proceed.

Validate against the REAL old module, never against a hand-written approximation
of it — an approximation passes happily while the actual rollback still breaks.
A `dc7a81a2` build artifact's `schemas.py` works equally well as the source.

**5. Roll the tarball back.**

```
shift-agent-deploy list
shift-agent-deploy rollback <deploy-tag>
```

**6. Smoke.** Confirm the old release reads catering state: send one inbound to a
known lead's number and check that the lead is FOUND (the failure mode is a
silent "no lead", so the absence of an error is not evidence).

```
tail -f /opt/shift-agent/logs/decisions.log
```

## What the migration does to your data

| Store | Action | Why |
|---|---|---|
| `catering-leads.json` | rewritten | `CateringLead` is `extra="forbid"` in `dc7a81a2`; 12 new fields + 2 new statuses |
| `catering-proposals.json` | rewritten | `CateringProposalSet` is `extra="forbid"`; M3 added `expires_at`, and the old status `Literal` names neither `EXPIRED` (M3) nor `SEND_UNCERTAIN` (P1) |
| `catering-quote-ledger.json` | untouched | store is `extra="allow"` with `records: list[dict]`; the strict record model runs only at append, never on read |
| `catering-amendments.json` | untouched | same preservation-safe tolerant-dict shape |
| `catering-followups.json` | untouched | the filename does not appear anywhere in `dc7a81a2`'s tree — the old release never opens it |
| `catering-pricebook.json` | untouched | likewise absent from the old tree |

Status remapping, every one recorded in the sidecar as `original_status`:

**Leads** (`catering-leads.json`):

- `QUALIFYING` → `NEW` — pre-quote, pre-approval, still workable, and nothing in
  the old release auto-acts on it. (`AWAITING_OWNER_APPROVAL` would trip the old
  quote-text sentinel backfill and queue a lead with no real quote for owner
  approval — one approval away from sending a sentinel to a customer.)
- `BOOKED` → `CLOSED` — the old terminal for "booked or customer-declined". The
  acceptance facts survive in the sidecar.

**Proposal sets** (`catering-proposals.json`):

- `EXPIRED` → `SUPERSEDED` — the set really was sent and is now dead.
  `SUPERSEDED` is the old release's terminal for exactly that and makes no claim
  about the send. (`SEND_FAILED` would assert the customer never received options
  they demonstrably did receive, and were given a validity date for.)
- `SEND_UNCERTAIN` → `SEND_FAILED` — the old release has no vocabulary for "the
  bridge accepted it but never acknowledged it". Of the two terminals available,
  `SEND_FAILED` is the honest one: nothing ever confirmed and the set carries no
  `outbound_message_id`, so the old code must not treat it as delivered.
  (`SUPERSEDED` would imply a LATER set replaced it, which is a different fact.)
  **The loss is to the OLD READER only** — the sidecar keeps `SEND_UNCERTAIN` as
  `original_status`, so the uncertainty is recoverable in full on re-upgrade.

Both proposal targets are non-selectable under `dc7a81a2`, whose
`select-catering-proposal` claims a set only when its status is exactly `SENT`.
So neither remap can hand the old release a set it might act on.

## Recovering what was stripped

Each rewritten store gets `<store>.downgrade-sidecar-<epoch>.json` alongside it in
`/opt/shift-agent/state/`, holding `{id, stripped_fields, original_status}` per
affected record. **Do not delete these** — they are the only copy of the
acceptance timestamps, hold reasons, cents-exact pricing provenance and validity
windows. Keep them until the Studio release is re-deployed and you have decided
whether to re-apply them.

The audit chain records the same thing: one `catering_state_downgraded` row per
store in `/opt/shift-agent/logs/decisions.log`, written **before** each mutation
(after the rollback the old binary can no longer write that row type — it can
still read it, since unknown tags route to `_UnknownLogEntry`).

## Known erosion after rollback (accepted, not a bug in the migration)

M1 added five sub-fields to `CateringLeadExtractedFields` — `event_type`,
`venue`, `service_style`, `veg_guest_count`, `nonveg_guest_count`. That nested
model is `extra="ignore"` in **both** releases, so the old code reads leads
carrying them without complaint; the migration deliberately leaves them in place
rather than stripping live intake data to solve a problem that does not exist.

The consequence: `extra="ignore"` drops on read, so the **first time the old
release rewrites a lead**, those five sub-fields are gone from disk permanently.
If the qualification answers matter for a lead in flight, snapshot
`catering-leads.json` before step 5 — that copy is the only recovery path, since
the sidecar does not carry them (the migration never stripped them).

## Re-upgrading later

The downgraded stores load cleanly under the NEW schemas too — every stripped
field is Optional or defaulted — so re-deploying the Studio release needs no
forward migration. Re-applying the sidecar contents is a separate, deliberate
decision; there is no tooling for it yet, and the leads have moved on in the
meantime.
