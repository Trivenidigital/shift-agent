# Deploy record — `0fff37a6`

**Drift-check tag:** `Hermes-native` — a deploy record; no runtime code, schema,
skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deploy provenance record | none found — this repo's convention is `tasks/audits/deploy-authorization-<sha>.md` | use the existing convention (no code) |

Verdict: **documentation only.**

---

**Tag:** `deploy-20260823-052415-0fff37a6` · **DEPLOY_EXIT=0**
**Contents:** PR #755 (operator-truth observability) + PR #756 (phase-1
reader-side forward-compat). `.commit-hash` in the artifact = `0fff37a6…`.

Built with `--skip-pytest`; the monolithic gate has never been green in this
repo (documented co-residency class), and the substitute is the curated CI,
**8/8 green on each merged head**.

## The irreversible step, taken first

PR #755's receipt writer uses `mv -f`, and the June receipt was the **only copy
anywhere on the box** of 11 module SHA-256 hashes from a past flyer deploy.
Snapshotted before deploying:

    /opt/shift-agent/_archive/DEPLOY_RECEIPT.pre-automation-20260823.json
    3150 bytes, mtime preserved (Jun 6), 8 module_sha256 entries + fixes_AB1B2 + fix_C

Verified intact after the deploy.

## Runtime verification — written so it could fail

Both new behaviours were verified against a **predicted shape chosen so a wrong
outcome would be visible**, not against "the file exists".

**Receipt.** Freshness is deliberately *not* the discriminator — the stale file
was also present and would also have looked "there". The discriminator is that
`mv -f` replaces wholesale, so **any surviving June key means some other write
path produced the file**:

| check | result |
|---|---|
| keys | exactly `[commit, cross_check, generated_by, hand_edits, installed_at_utc, operational_errors]` |
| `commit == .commit-hash` | **True**, `0fff37a63b24` |
| `generated_by` | `shift-agent-deploy.sh install_artifacts()` |
| **surviving June keys** | **empty set** |
| `.DEPLOY_RECEIPT.json.tmp` | absent — the atomic rename completed |
| `operational_errors` | names `/opt/shift-agent/logs/hermes-gateway.log` and explicitly says NOT journalctl |

**Drop-in classification.** This line exists only on the deploy's stdout and is
written to no log, so the deploy was `tee`'d — otherwise verifying it would have
cost another deploy. Decisive as a *pair*:

    dropin EOL-ONLY   hermes-gateway.service.d/20-drain-timeout.conf — differs ONLY in line endings; not overwriting.
      To adopt the repo copy: re-run the deploy with SHIFT_AGENT_NORMALIZE_DROPIN_EOL=1
    note: 1 tracked drop-in(s) differ only in line endings (cosmetic; …)

— `EOL-ONLY` present, **no** `dropin DIFFERS` for that file, and **no** drop-in
`WARN:` line. `SHIFT_AGENT_NORMALIZE_DROPIN_EOL` was deliberately left unset, so
the classification itself could be observed; the box file is still 32 bytes CRLF.

The three `WARN:` lines that do appear are pre-existing and unrelated (Hermes
version drift, absent `stripe` in the venv, unknown `_config_version` key).

## Phase-1 property, verified before merge

PR #756 is reader-side only, and the ordering only works if it emits nothing.
Confirmed by reading the merged tree rather than the PR description: the
absorbing variant exists solely in `schemas.py`, `log-decision-direct` refuses it
by `isinstance`, and cf-router constructs `CfRouterIntercepted(...)` directly at
`actions.py:2200` — never through the union. So phase 1 writes no new row and a
rollback can strand none. Phase 2 (widening the Literal, retiring the
`_KNOWN_DROPPED_REASONS` ratchet) is a separate PR, only after this is proven in
runtime.

## Post-deploy state

| | |
|---|---|
| services | `hermes-gateway` active, `shift-agent-cockpit` active |
| bridge | connected, queue 0 |
| catering leads | 20 — distribution **identical** to pre-deploy |
| deposits | `deposit_pct: 0` |
| smoke | all checks passed, 78 menu items, catering schema + transition table OK |

Pre-existing warning unchanged: Agent #21 venv absent, so expense-bookkeeper
smoke checks skip.

## Rollback

Anchor `deploy-20260823-033350-5a216767`. No schema tag added and no Literal
widened, so none of the four rollback categories applies. The receipt's else-arm
removes it on a rollback to a release predating the label, so a rollback cannot
strand a receipt naming an uninstalled commit.

## Unchanged

No live WhatsApp send · transport-evidence harness not executed · no timer
enabled or disabled · TTL sweep unarmed · no OpenRouter credit · Flyer watchdog
runtime user untouched · `hermes update` not run.
