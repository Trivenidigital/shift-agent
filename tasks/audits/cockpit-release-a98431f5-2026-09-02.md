# Cockpit release `a98431f5` — the live step-up bypass is CLOSED in production

**Drift-check tag:** `Hermes-native` — a release record; no runtime code,
schema, skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Release provenance record | none found — repo convention is `tasks/audits/` | use the existing convention (no code) |

Verdict: **documentation only.**

---

**Released:** `a98431f5` at 2026-09-02T01:52:35Z
**Receipt:** `/opt/shift-agent/cockpit/RELEASE_RECEIPT.json`
**Snapshot / rollback:** `/opt/shift-agent/cockpit/releases/snapshot-20260902T015226Z`
**Previous backend:** `/opt/shift-agent/cockpit/backend.old-20260902T015226Z`

## What is now closed

The deployed cockpit ran the plain set intersection

```python
sensitive = settings.sensitive_config_fields & body.fields.keys()
```

with `jwt_ttl_hours = 24`. A session holding any valid `hjwt` cookie up to 24
hours old — no freshness, no OTP — could `PATCH /config {"fields": {"owner":
{...}}}`. The key `"owner"` does not intersect the string `"owner.phone"`, so
the step-up was never demanded and `_set_dotted` replaced the whole owner
block. `is_owner_chat()` fast-paths on `owner.self_chat_jid` with no roster
cross-check, so that handed WhatsApp-side owner authority to an
attacker-controlled chat id.

Verified independently after the release, by reading the deployed file rather
than trusting the release script's own report:

```
config.py:79  sensitive = _sensitive_touched(body.fields.keys())
```

and the live sensitive set now contains `owner.phone`, `owner.lid`,
`owner.self_chat_jid`, `owner.authorized_identities`.

## The release transaction, and what it caught

The old `web/deploy/deploy.sh` was replaced (#787-#789) with
`PREFLIGHT -> SNAPSHOT -> STAGE -> VALIDATE -> CUTOVER -> VERIFY`, rolling back
automatically on any failed gate.

**It earned itself on the first run.** Staged validation refused to cut over:

```
ModuleNotFoundError: No module named 'privileged_identity'
VALIDATION FAILED — live tree untouched, staged copy removed.
```

#778 added `src/platform/privileged_identity.py`; #781 imported it from the
cockpit. Platform modules install from an **explicit per-file list**, and the
new module was never added — so the cockpit would have raised ImportError on
startup, taking down the operator's control surface during a security deploy.
Fixed in #790, which also widened
`test_deploy_platform_install_completeness.py` to scan `web/backend/` — that
guard existed for this exact class and was blind to the cockpit.

Three failures surfaced only by running it, none visible to CI:

| # | failure | why CI could not see it |
|---|---|---|
| 1 | `rsync: command not found` | the script assumed a Linux/Mac dev box |
| 2 | unbalanced quote at `deploy.sh:40` | `bash -n` was never run on it |
| 3 | `privileged_identity` not installed | the completeness guard did not scan `web/` |

Any one of these was enough to keep the cockpit undeployed. All three had to be
fixed before a single release could run.

## Delta-aware, not a ritual replay

The old script replayed every historical install step on every run. Measured
against the live box, this release skipped what already matched:

```
SKIP apt-get: jq and chattr already present
SKIP unit: deployed bytes already match
SKIP rotate-script: deployed bytes already match
SKIP logrotate: deployed bytes already match
SKIP caddy fragment: caddy inactive on this box (nginx serves)
INSTALLED cron
```

Only one file actually needed installing. The Caddy step in particular was
carried from a layout this box does not use — nginx serves `127.0.0.1:8080`.

## Verification — `/health 200` was not accepted as evidence

Against the **live** tree:

```
PASS gated: owner.phone
PASS gated: owner.lid
PASS gated: owner.self_chat_jid
PASS gated: owner.authorized_identities
PASS ancestor key owner is caught
PASS descendant key is caught
PASS control: owner.name still patchable
PASS control: customer.name still patchable
PASS cockpit still bound to loopback only
PASS .env unchanged
PASS config.yaml unchanged
```

The last two controls are load-bearing: a gate that refused **everything**
would pass the four positive checks. `owner.name` and `customer.name` remaining
patchable is what proves it discriminates.

One defect in the gate itself was caught before the first run: `LIVE_SEC=$?`
after a pipeline reports `sed`, not python, so that gate could never fail
(#788). Now read via `${PIPESTATUS[0]}` with the python block exiting non-zero
on any failed assertion.

## Residual

The cockpit backend is now current with `main`. The three-month hand-maintained
drift recorded in `cockpit-backend-deploy-gap-2026-09-01.md` is resolved, and
its root cause is understood: the deploy script could not parse
(`bash -n` fails, apostrophe inside a single-quoted ssh block, commit
`5df57702`, 2026-04-27) and also required `rsync` on the developer machine.
That is why the cockpit was maintained by hand — `backend.old/`,
`backend.backup-pr166-*/`, and the CRLF logrotate config were all symptoms of
the same unrunnable path.

`ufw` remains inactive. The cockpit's loopback binding is still the only layer
between this surface and the network.
