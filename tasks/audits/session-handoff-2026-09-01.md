# Session handoff — 2026-09-01

**Drift-check tag:** `Hermes-native` — a handoff record; no runtime code,
schema, skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Session handoff record | none found — repo convention is `tasks/audits/session-handoff-<date>.md` | use the existing convention (no code) |

Verdict: **documentation only.**

---

## CURRENT TRUTH

| | |
|---|---|
| `main` | `d132cf88` |
| deployed | `d132cf88` — **main and box are level for everything the artifact ships** |
| open PRs | 0 |
| gateway | active |
| failed systemd units | **0** (was 1, failing nightly since 2026-06-01) |
| session start | `31f610a5`, box `c6eddc4c` |

**Important qualifier:** "deployed" covers only what `shift-agent-deploy.sh`
ships, which is `src tools .commit-hash`. The cockpit backend is deployed by a
different path and is **three months stale** — see HOLDS.

## DELTA — 9 PRs merged, 3 deploys, all verified on the box

| PR | what | reaches prod? |
|---|---|---|
| #776 | rotate 10 append-target logs; ship the cockpit logrotate config | yes |
| #777 | gate every owner IDENTITY field behind the existing Pushover step-up | **merged, NOT running** |
| #778 | cross-store privileged-identity invariant (module) | yes |
| #779 | ship the cockpit logrotate config from inside the artifact | yes |
| #780 | restore 2 tests deselected from CI for six weeks | CI-only |
| #781 | enforce the invariant at the roster mutation boundary | **merged, NOT running** |
| #782 | deploy record `1ff4bba7` | docs |
| #783 | cockpit deploy-gap audit | docs |
| #784 | honour `phone_history` effective windows in lid-learn | yes, **runtime-verified** |

Deploys: `5c168839` (no-op — see below), `1ff4bba7`, `d132cf88`.

## WHAT WAS ACTUALLY FIXED IN PRODUCTION

**1. `logrotate.service` had failed every night since 2026-06-01.** A
hand-installed CRLF config; logrotate cannot parse it and skips the file. So
`cockpit-audit.log` — the *only* record of cockpit roster/config mutations,
since no cockpit write reaches `decisions.log` — had not rotated in three
months. Separately, **ten** logs written via systemd `StandardOutput=append:`
had no stanza at all; `flyer-recovery-watchdog.log` had reached **105 MB**.

Verified after deploy: config is LF, `is-failed: inactive`, **zero** failed
units, `cockpit.log.1` actually created (first rotation since June), and all
sampled new logs present in `/var/lib/logrotate/status`.

I found two of the ten by eye. **The test found the other eight**, because it
derives its target list from systemd `StandardOutput=append:` directives
rather than a hand-maintained list.

**2. lid-learn bound a stranger's LID onto an employee row.** It matched any
`phone_history` entry regardless of its window, while `Roster.find_by_phone`
and `identify-sender` both skip a closed assignment. A number an employee gave
up, now held by someone else, still matched that row — and where the row also
carries an owner-authorized phone, the stranger's LID then resolves to
**owner**. lid-learn is the *only* automated writer of `employees[].lid`, so
this needed no operator action at all.

Runtime-verified against the installed binary with a paired control:

```
EXPIRED window -> no lid captured, 0 audit rows
OPEN    window -> lid captured,    1 audit row
production roster sha unchanged
```

The negative result is meaningful only because the positive control fires.

## SECURITY WORK MERGED (see HOLDS for why it is not running)

- **#777** — `owner.lid`, `owner.self_chat_jid` and
  `owner.authorized_identities` now sit behind the same Pushover step-up as
  `owner.phone`. All four decide *who holds owner authority*; only one was
  gated. **Second defect found while testing the first, and predating it:** the
  check was an exact set intersection while `_set_dotted` writes at any depth,
  so a key of `"owner"` rewrote the whole block and bypassed the step-up for
  `owner.phone` too.
- **#778 / #781** — the cross-store invariant and its enforcement at
  `roster_session`, the single chokepoint all four cockpit roster writers
  share. Refuses only violations a mutation *introduces*, so a pre-existing bad
  row stays repairable; degrades **open** if the owner config is unreadable,
  because this guard must never be why a roster edit becomes impossible.

## HOLDS — genuine operator decisions

**1. The cockpit backend is three months behind `main`.** 24 of 25 backend
`.py` files date from 2026-05-31; 14 commits have touched `web/backend/` since.
**#777 and #781 are merged and not in force** — verified by reading the
deployed files, not inferred.

Not deployed autonomously because `web/deploy/deploy.sh` runs
`rsync -az --delete web/frontend/dist/`, which wipes or downgrades the deployed
frontend when `dist/` is absent or stale locally. That is a destructive
production action. It also `apt-get install`s, restarts the service, and does
not parse under `bash -n` at `origin/main` (pre-existing).

Severity is bounded: the cockpit is loopback-only (nginx `127.0.0.1:8080`,
uvicorn `127.0.0.1:8081`, only SSH exposed). **`ufw` is inactive**, so that
containment rests entirely on bind addresses.

Recommendation: deploy, building `web/frontend/dist/` first. Full record in
`cockpit-backend-deploy-gap-2026-09-01.md`.

**2. Record `lid` on the authorized identity.** The cross-store invariant
cannot distinguish `e008`'s own LID from a stranger's LID written onto `e008`'s
row, because config records that principal by phone only — nothing binds a LID
to it. Closing that is a **data** change, not a logic change, and a heuristic
guess would be worse than the gap. A test pins the gap *and* its paired
positive, so the day the data exists the check fires and the limitation test
fails loudly.

**3. `cockpit-ci` is path-gated on `web/backend/**`.** A src-side change can
break a cockpit test and CI will not run until someone touches `web/`. That
gate is why two tests sat deselected for six weeks. Widening it has its own
cost; not changed.

## DEFECT CLASSES — the reusable part

**A green observation that is true for the wrong reason** remained the dominant
mechanism. Every instance below had passed:

| green observation | what was actually true |
|---|---|
| deploy exits 0, 17/17 smoke checks, config correct in repo and referenced by the deploy script | `web/` is not in the deploy artifact, so the install line could never fire and **the deploy changed nothing** |
| "a malformed owner LID is refused, so my validation works" | `LidLearned.new_lid` already refused it; **removing my validation changed nothing** |
| "the two flyer tests are main-inherited product breakage" | the production code had a `FLYER_STATE_ROOT` override all along; the harness never set it |
| `grep -c "deselect"` returns 2, so deselects remain | both hits were in my own comment |
| my auth-probe returned "not owner" for every case | the synthetic config failed validation; **nothing ran** |
| 7 guard tests pass in isolation | `get_settings.cache_clear()` minted a new `Settings` while `app.state` kept the old one; the fixture wrote where the code did not read |
| `grep -c WHATSAPP_LID_CACHE_WRITE` returns 1 | that counts the key's *presence*, not its value |

The through-line: **"the deploy script installs it" is not "the file reaches
the box", and "the test passes" is not "the mechanism ran."** Verify the
deployed artifact and the executed path, not the merge and not the exit code.

**A description that names an OUTCOME survives its mechanism being wrong; one
that names the MECHANISM fails visibly.** "Production state files are never
opened for write" sat in a docstring while the script wrote to production,
because the sentence had no mechanism behind it to break.

**Two invariants I wrote were wrong and the tests caught them**, which is the
system working: `copytruncate` is not required for `StandardOutput=append:`
(these are all `Type=oneshot`), and an active `phone_history` entry holding the
owner's phone is an *attribution* defect, not a privilege escalation — measured
against the real resolver, which returns `["employee"]` to the attacker by both
identifier forms.

## NOT COMPLETED — stated plainly

The **suite-wide agent readiness matrix** and the **routing census** were
dispatched to research agents that hit a session limit and then did not report.
No matrix was produced, so **no agent statuses were recomputed this session**
and none should be inferred from this document. That remains the largest open
item and is the right next lane.

## NEXT 24 HOURS, ordered by expected readiness gain

1. **Deploy the cockpit backend** (operator decision above). Two merged
   security fixes are inert until then.
2. **Recompute the agent matrix and routing census** from the current tree and
   runtime — the work this session did not finish. No headline count without
   the matrix beneath it.
3. **Record `lid` on the authorized identity**, closing the one stitch shape
   stored state cannot currently decide.
4. **`ufw` is inactive.** The loopback containment that bounds every cockpit
   finding has no second layer.
5. Remaining census findings not yet actioned: no cockpit mutation reaches
   `decisions.log`; `whatsapp.py` swallows a failed `self_chat_jid` write and
   reports success anyway; `import_csv` silently strips `lid` and
   `phone_history` fleet-wide.
