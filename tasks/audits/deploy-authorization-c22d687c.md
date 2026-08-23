# Deploy record — `c22d687c`

**Drift-check tag:** `Hermes-native` — a deploy record; no runtime code, schema,
skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deploy provenance record | none found — repo convention is `tasks/audits/deploy-authorization-<sha>.md` | use the existing convention (no code) |

Verdict: **documentation only.**

---

**Tag:** `deploy-20260823-222200-c22d687c` · **DEPLOY_EXIT=0**
**Contents:** PR #764 only — P1-A, copied-state rehearsals are credential-sterile
by construction.

## Why this shipped

A copied-state rehearsal copied the real production `config.yaml` — live Pushover
credentials included — into a `--network host` container running production
scripts. Nothing fired, and that was verified, but it was luck: the same scripts
reach `notify_owner_with_fallback`, which did fire in production minutes later.
Copied state is not safe merely because the files are copies, if the copied
credentials still authorise real external actions.

Two hardcoded endpoints were the sharpest edge: `shift-agent-notify-owner` had
`api.pushover.net` and the live bridge on `:3000` baked in, so a rehearsal that
had carefully repointed `HERMES_BRIDGE_URL` still sent from that script. Both are
now env-overridable **with the same production defaults**.

## The requirement that mattered most: production must be UNAFFECTED

Verified on the box after the deploy, not reasoned about:

| check | result |
|---|---|
| `SHIFT_AGENT_REHEARSAL` set in production? | **no** |
| `rehearsal_mode_active()` | **False** — guard dormant |
| a real-shaped credential reads as sterile? | **no** (guard cannot misfire on real alerts) |
| an empty / `None` value reads as sterile? | yes |
| `.env` sets `PUSHOVER_API_URL` or `HERMES_BRIDGE_URL`? | **neither** — deployed defaults unchanged |

**Positive control, produced by the deploy itself:** two real owner alerts were
dispatched AND delivered over pushover during this deploy — "Smoke test" and
"Deploy OK" — with the guard shipped. Real alerting works.

That control matters because the guard's failure mode is silent: "no external
call happened" and "nothing ran at all" look identical from the outside.

## A near-miss in my own verification

My first check for post-deploy alerts compared a UTC `datetime.now()` against the
box's `-04:00` timestamps, so recent rows appeared absent and it looked like the
guard had suppressed real alerts. It had not. Recorded because the wrong
conclusion was one sentence away, and the shape — a comparison that is wrong in a
way that produces a plausible answer — is the recurring one.

## Post-deploy state

`hermes-gateway` active · bridge connected, queue 0 · `.commit-hash` =
`c22d687c…` · smoke checks all passed. Pre-existing unrelated warning: Agent #21
venv absent.

Baseline check on the suites that exercise the real send paths:
`test_catering_v02_scripts.py` runs **34 passed / 0 skipped** on both this branch
and `origin/main` — identical, so the guard did not silently convert coverage into
skips. The lifecycle E2E's 14 skips are its documented skip-unless-`REQUIRED`
behaviour, and CI sets that flag.

## Rollback

Anchor `deploy-20260823-221056-24f5ba66`. Additive guard with no schema tag, no
Literal widened, no new state object; the endpoint constants keep their previous
values as defaults, so a rollback restores byte-identical behaviour.

## Unchanged

No customer message · deposits `deposit_pct: 0` · no timer enabled · no lead
touched · `parse-menu-photo` untouched.
