# Cockpit backend is three months behind `main` — merged security work is not running

**Date:** 2026-09-01
**Drift-check tag:** `Hermes-native` — an audit record; no runtime code, schema,
skill or config is introduced by this document.
**Status:** PARKED for an operator decision. Nothing was deployed or changed.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deploy-state audit record | none found — repo convention is `tasks/audits/` | use the existing convention (no code) |

Verdict: **documentation only.**

---

## The finding

`/opt/shift-agent/cockpit/backend/app/` on the production box:

| measure | value |
|---|---|
| total `.py` files | 25 |
| files dated **2026-05-31** | **24** |
| files newer than 2026-08-01 | 1 (`config.py`, 2026-08-06 15:05) |
| commits touching `web/backend/` since 2026-05-31 | **14** |

The cockpit backend has effectively not been deployed since **2026-05-31**.

That single Aug-6 `config.py` is its own concern: it is newer than every other
file in the tree, which is the signature of a hand-edit on the box rather than
a deploy. The same signature produced the CRLF logrotate config that failed
nightly for three months (see `deploy-authorization-1ff4bba7.md`).

## What is merged and not running

Verified directly against the deployed files, not inferred:

| change | on `main` | running on the box |
|---|---|---|
| **#777** — owner identity fields (`owner.lid`, `owner.self_chat_jid`, `owner.authorized_identities`) behind the Pushover step-up | yes | **no** — the live `sensitive_config_fields` still contains only `owner.phone` |
| **#777** — ancestor-key bypass closed (`_sensitive_touched`) | yes | **no** — the live gate is still the plain set intersection |
| **#781** — cross-store privileged-identity guard at the roster mutation boundary | yes | **no** — `PrivilegedIdentityViolation` absent from the deployed `state.py` |
| **#780** — `FLYER_STATE_ROOT` test redirect | yes | n/a (test-only) |
| 11 further `web/backend/` commits since 2026-05-31 | yes | **not audited individually** |

So both security fixes landed this session are **merged but not in force**.
The asymmetry they close — a plain session being able to append an authorized
owner identity, and an `"owner"` ancestor key bypassing the step-up on
`owner.phone` — is still live.

**Mitigating, and load-bearing for the severity assessment:** the cockpit is
not internet-facing. nginx binds `127.0.0.1:8080`, uvicorn `127.0.0.1:8081`,
and only SSH is exposed. Reaching `PATCH /config` requires an SSH tunnel plus
a valid session. `ufw` is inactive, so that containment rests entirely on the
bind addresses.

## Why this was not deployed autonomously

The cockpit backend is deployed by `web/deploy/deploy.sh`, a **separate**
process from `shift-agent-deploy.sh`. It is not a drop-in:

- line 21 runs `rsync -az --delete web/frontend/dist/ …`. If `dist/` is
  absent or stale locally, `--delete` **wipes or downgrades the deployed
  frontend**. That is a destructive production action.
- lines 32-34 run `apt-get update` and `apt-get install`.
- it restarts the cockpit service.

Running it blind trades one gap for a possible outage, so it is an operator
decision rather than an ordinary safety deploy.

Note also that `web/deploy/deploy.sh` does not parse under `bash -n` at
`origin/main` (unbalanced quote inside its ssh block, around line 97). That is
pre-existing and untouched, but it is worth knowing before anyone runs it.

## What the operator needs to decide

1. **Deploy the cockpit backend**, having first built `web/frontend/dist/` so
   the `rsync --delete` is safe — or run a backend-only subset deliberately.
2. **Or accept the gap**, on the basis that the cockpit is loopback-only and
   the resolver-side refusals from #773 still stand underneath.

Recommendation: **deploy**, because the two security fixes are exactly the
ones that make a compromised-session scenario materially worse, and because
every further cockpit change accumulates behind the same gate. But it needs a
frontend build in hand first, which is why it is not done here.

## The generalisable point

A merged PR is not a running change, and this repo has two independent deploy
paths with very different cadences. `shift-agent-deploy.sh` ran twice today;
`web/deploy/deploy.sh` has not run since May. Anything merged under
`web/backend/` is invisible to the frequently-run path.

Compare `deploy-authorization-1ff4bba7.md`, where a file correct in the repo
and correctly referenced by the deploy script still never reached the box.
Same class, different mechanism: **verify the deployed artifact, not the
merge.**
