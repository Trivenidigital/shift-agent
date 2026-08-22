# systemd drop-ins: what this repo owns, and what it deliberately does not

**Status:** 2026-08-22. Enumerated from `main-vps` at deploy `24c1f1d5`.

The box carries **19 drop-in `.conf` files across 11 `*.service.d` directories**. Until
this document, **none was tracked here and the deploy installed none.**

## Why this mattered

`/usr/local/bin/shift-agent-policy-preflight` — the gate that refuses to start the gateway
unless WhatsApp screening is live — **ships from this repo and is reinstalled on every
deploy**. The drop-in that wires it as `ExecStartPre` **did not ship**.

So a rebuilt box would have the safety gate installed on disk and never invoked. Per that
drop-in's own comment: `_load_plugin` swallows exceptions from a plugin's `register()`, and
the bundled WhatsApp adapter keeps a deferred loader registered, so a silently-failed policy
plugin hands WhatsApp back to the **stock, unscreened adapter with no error**.

This is a **reproducibility gap on a safety control, not an active incident** — deploys do
not purge drop-ins, so the live box still has it. It would only bite on a rebuild or a
disaster-recovery restore.

## The three rules the deploy follows

1. **Additive only.** Never delete a drop-in; never purge a `*.service.d` directory. Most
   drop-ins here belong to codex tooling.
2. **Never overwrite a differing file.** A box copy that differs may be a deliberate hand
   edit and nothing in the deploy can tell. Install only what is absent, leave identical
   copies alone, report the rest.
3. **Only `src/platform/systemd/<unit>.d/*.conf` is ours.** Ownership is decided per file
   below, not per directory.

## The ownership test

Not "which service does it attach to" — that is the tempting answer and it is wrong.
`hermes-gateway.service` is ours, yet two of its drop-ins are not.

The test that actually separates them:

> **Who owns the thing the drop-in wires TO, and who else writes this filename?**

A drop-in is ours when it configures our own binary or our own operational behaviour, and
nothing outside this repo writes a file by that name.

## TRACKED (2)

| File | Why it is ours |
|---|---|
| `hermes-gateway.service.d/30-shift-agent-policy-preflight.conf` | Wires **our** binary, shipped from **our** repo, onto **our** service, to enforce **our** safety control. Nothing external writes this name. Unambiguous. |
| `hermes-gateway.service.d/20-drain-timeout.conf` | `TimeoutStopSec=240s` — operational tuning of our service's shutdown drain, so in-flight WhatsApp work finishes. No external dependency, no external writer. |

The repo copy of `30-shift-agent-policy-preflight.conf` is **byte-identical** to the box copy
(`sha256 33c02036…`), so tracking it is provably a no-op on the current box.

### Known difference: `20-drain-timeout.conf`

The box copy uses **CRLF** line endings (`5b53 6572 7669 6365 5d0d 0a…`, 32 bytes); the repo
copy uses LF (30 bytes). Semantically identical — systemd accepts both — but **not
byte-identical**, so rule 2 applies: the deploy reports it and does **not** overwrite.

That is deliberate. The alternative — normalising line endings before comparing — would let
the deploy claim a match while the box quietly kept Windows line endings in a config file.
Resolution is a one-time manual step (delete the box copy and let the next deploy install
the LF version) and is an **operator decision**, not something a deploy should do silently.

Until then the deploy prints one `dropin DIFFERS` line per run for this file. That nag is the
intended behaviour, not a defect.

## NOT TRACKED, and why (3 on repo-tracked services)

| File | Service | Why not |
|---|---|---|
| `hermes-gateway.service.d/10-telegram-onfailure.conf` | ours | Wires `OnFailure=codex-systemd-failure-alert@%n.service` — a **codex-owned unit this repo does not define**. Codex tooling writes this same filename across the fleet, so tracking it would put our deploy and codex's tooling in a fight over one path. Note it is **not** the fleet template: the codex copies also chain `codex-systemd-auto-remediate@`, and this one is alert-only, which looks like a deliberate local choice worth preserving — another reason not to have a deploy rewriting it. |
| `shift-agent-cockpit.service.d/10-telegram-onfailure.conf` | ours (`web/deploy/`) | Byte-identical to the codex fleet template (`sha256 6ae06e4e…`). Same reasoning. |
| `flyer-recovery-watchdog.service.d/10-codex-worker-root.conf` | ours | **See the finding below — this one is not a filing decision.** |
| `hermes-gateway.service.d/20-flyer-integrated-poster.conf` | ours | `Environment=FLYER_ALLOW_INTEGRATED_POSTER=1` — genuinely ours by content, but **provably redundant**: `src/platform/systemd/hermes-gateway.service:17` already sets the identical value in the base unit. Tracking it would commit a duplicate. A rebuilt box gets the flag from the base unit regardless. Flagged as a cleanup candidate, not tracked. |

## OUT OF BOUNDS (14)

Everything under `codex-*.service.d` (7 services, 12 files) and `nginx.service.d` (1 file).
These configure a different tool's services, or a system service this repo does not define.
Not ours to track, and not ours to remove.

## ⚠ FINDING — the flyer recovery watchdog runs as root, and the repo does not say so

`/etc/systemd/system/flyer-recovery-watchdog.service.d/10-codex-worker-root.conf`:

```ini
[Service]
User=root
Group=root
Environment=HOME=/root
```

The repo's own unit (`src/agents/flyer/systemd/flyer-recovery-watchdog.service:8-10`) sets
`User=shift-agent`, `Group=shift-agent`, `Environment=HOME=/opt/shift-agent`.

**So this watchdog currently runs as root on the live box, and nothing in this repo records
that.** The drop-in was created by codex tooling (the worker-draft path runs as root), dated
2026-05-24.

It is **deliberately not tracked**. Committing it would make root the recorded default for a
watchdog that the repo says runs unprivileged — a privilege decision that belongs to the
operator, not to a lane closing a reproducibility gap. Left in place (rule 1: additive only,
never delete), reported here.

**Two ways to resolve, operator's call:**
1. The escalation is needed → track it, with the reason written down, so a rebuild reproduces
   it deliberately rather than by accident.
2. It is not needed → remove the drop-in on the box so the watchdog drops back to
   `shift-agent`, and confirm the recovery path still works unprivileged.

Doing neither leaves the box and the repo disagreeing about who a live watchdog runs as.

## Adding a drop-in later

Put it in `src/platform/systemd/<unit>.service.d/<NN>-<name>.conf`, run the ownership test
above, and add a row to the TRACKED table with the reasoning. The deploy picks it up with no
code change. If the box already has a file by that name and the bytes differ, the deploy will
refuse to overwrite and say so — that is working as intended.
