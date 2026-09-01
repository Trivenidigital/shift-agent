# Deploy record — `1ff4bba7` (and the failed `5c168839` before it)

**Drift-check tag:** `Hermes-native` — a deploy record; no runtime code, schema,
skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deploy provenance record | none found — repo convention is `tasks/audits/deploy-authorization-<sha>.md` | use the existing convention (no code) |

Verdict: **documentation only.**

---

**Tag:** `deploy-20260901-204845-1ff4bba7` · **DEPLOY_EXIT=0**
**Rollback target:** `deploy-20260901-202302-5c168839`
**Contents:** #776 + #779 (logrotate coverage and the artifact-path repair),
plus #775's tooling. No agent runtime code changed.

## Why this record covers two deploys

`5c168839` (#776) was deployed, exited 0, passed 17/17 smoke checks — **and
changed nothing**. The record keeps both because the failure is the more
useful half.

## The original defect

`logrotate.service` had reported FAILED every night since **2026-06-01**.

`/etc/logrotate.d/shift-agent-cockpit` was hand-installed on 2026-05-31 with
CRLF line endings. logrotate cannot parse those — *"lines must begin with a
keyword or a filename"* — and skips the whole file. So `cockpit-audit.log` and
`cockpit.log` had not rotated for three months.

`cockpit-audit.log` matters more than its size suggests: **no cockpit mutation
reaches `decisions.log`**, so it is the only record of roster and config writes
through the privileged mutation surface.

Separately, **ten** logs written via systemd `StandardOutput=append:` had no
stanza at all. `flyer-recovery-watchdog.log` had reached **105 MB** and was
still growing. Among the others is `openrouter-balance.log`, which is not
journald and is the only record of the balance alarm that caught the
2026-08-06 credit outage.

I found two of the ten by eye. The coverage test found the other eight.

## Why the first deploy did nothing

The install line added in #776 read from `web/deploy/logrotate.conf`. The
deploy **artifact** is `src tools .commit-hash` — the same set the deploy
script snapshots for rollback at `shift-agent-deploy.sh:1616`. **`web/` is
never shipped.** So the `[ -f … ]` guard was false, the install was skipped,
and the deploy reported success.

Everything upstream was green, and everything upstream was *true*:

| claim | status |
|---|---|
| the repo file is correct, and LF | true |
| the deploy script references and installs it | true |
| CI passes | true |
| the deploy exits 0, 17/17 smoke checks | true |
| **the file reaches the box** | **never asserted** |

> "The deploy script installs it" is not "the file reaches the box." Only
> inspecting the deployed box distinguished them.

`#779` moved the config to `src/agents/shift/logrotate/shift-agent-cockpit` so
it ships with `src`, and pointed both installers — the shift deploy's
`install` and `web/deploy/deploy.sh`'s `scp` — at that one path. Two copies
would drift, and the drift is invisible until logrotate fails to parse one of
them, which is the outage being fixed.

That also explains the original CRLF: `web/deploy/deploy.sh` scps the file
straight from a working copy, so a Windows checkout with CRLF installs CRLF.

## Post-deploy verification

| check | result |
|---|---|
| cockpit config line endings | `{$` — **LF**, was `{^M$` |
| `systemctl is-failed logrotate.service` | **inactive** — the service now succeeds |
| failed units on the box | **none** |
| did it actually rotate? | **`cockpit.log.1` created** — the log that had not rotated since 2026-06-01 |
| new logs tracked in `/var/lib/logrotate/status` | **4/4 sampled**, including `flyer-recovery-watchdog.log` and `openrouter-balance.log` |
| `cockpit-audit.log` tracked | yes — for the first time |

`flyer-recovery-watchdog.log` is still 105 MB and that is expected: logrotate
records a first-seen timestamp for a previously-untracked file and rotates it
at the next daily boundary. It is now tracked, which is the verifiable claim.

## What the test suite now guards

- **`test_every_append_target_is_rotated`** derives its target list from
  systemd `StandardOutput=append:` directives rather than a hand-maintained
  list, so a unit that gains a new log fails the build instead of quietly
  growing one.
- **`test_installed_sources_are_inside_the_deploy_artifact`** — the assertion
  the first version was missing.
- **`test_no_logrotate_config_is_installed_from_outside_the_artifact`** —
  generalised to any future install line, not just today's two.
- A `decisions.log` control proving `copytruncate` is **not** globally correct,
  so a well-meaning "add copytruncate everywhere" change fails.

## A wrong invariant the test caught

The first draft required `copytruncate` for every `append:` target, reasoning
that such a unit holds its fd open across rotation. The coverage test
**disproved that** by failing on `send-daily-brief`, `eod-reconcile` and
`prune-expense-receipts` — all `Type=oneshot`, all long-standing users of
`create`, all fine.

Every unit here is a timer-fired oneshot that exits between runs, so `create`
is correct. The test now asserts only that a mode is chosen *explicitly*,
because a specific one is not an invariant this codebase has demonstrated.

## Also verified on the box during this window

`decisions.log` rotation was **never** affected — logrotate skips the bad file
and continues, so the audit chokepoint kept rotating throughout
(`decisions.log-20260831` was produced the night before this deploy). An
earlier reading of mine that "all log rotation is dead" was wrong and is
withdrawn.
