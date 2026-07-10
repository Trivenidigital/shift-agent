# Plan — Skills-Integrity Guard (D1 manifest gate + D2 between-deploy watchdog)

**Drift-check tag:** `extends-Hermes` — adds custom deploy-gate + watchdog infrastructure
on top of Hermes, mirroring our OWN existing gate patterns (`check-shift-agent-patch.sh`,
`check-hermes-config-yaml.sh`, `shift-agent-health-watchdog.sh`). No Hermes convention is
fought; no Hermes substrate is reinvented.

**Authorization:** overnight autonomous run, operator envelope = "Push + open PR" ceiling +
"proceed on everything, document." No merge, no deploy, no live-VPS action. See
`tasks/skills-integrity-guard-assumptions.md` for the running decision ledger and
`tasks/skills-integrity-guard-approvals.md` for the recorded-authorization trail.

**Origin:** overnight research (this session) found our skill-mutation defenses are
**deploy-time-only and content-blind**, with a silent self-heal (`rsync --delete`) that
never alerts — a live §12a/§12b gap independent of the 0.17 "self-writing skills" feature.
This PR closes D1 (content integrity) + D2 (between-deploy detection). D3 (config
assertions for the 0.17 self-writing flags) is **deferred** — it needs the flag names read
on-box, which is out of the unattended envelope.

---

## Hermes-first analysis

Per-step `[Hermes]` / `[net-new]` tagging of the capability:

| Step | Hermes provides? | Tag |
|---|---|---|
| Agent ingest/vision/approval/audit substrate | Yes (existing) — not touched by this work | `[Hermes]` |
| Deploy-time integrity gate over shipped SKILLs | No — this is our tarball-deploy infra | `[net-new]` (mirrors our own `check-*` gates) |
| Between-deploy filesystem watchdog on `/root/.hermes/skills/` | No — our systemd-timer infra | `[net-new]` (mirrors `shift-agent-health-watchdog`) |
| sha256 content pinning | No Hermes primitive; stdlib `hashlib` | `[net-new]` (mirrors `bridge.js` sha pin) |
| Alert delivery + audit row | Yes — reuse `notify-owner` + `log-decision-direct` | `[Hermes]` (reused, not rebuilt) |

**Hermes skill hub / ecosystem check:** no bundled or community Hermes/MCP skill covers
"verify our shipped SKILL.md files were not mutated on a customer VPS between deploys." This
is deploy-pipeline integrity for *our* single-tenant fleet, not an agent capability. Verdict:
**build from scratch**, but every piece is a mirror of an existing in-tree gate/watchdog —
zero new architectural surface. `/hermes-check` receipt: `skills-integrity-guard.json`
(net_new=7, hermes=2, tag=extends-Hermes).

**Deployed-pattern checklist compliance:**
- Storage/audit: plain-text logs + `notify-owner`; **no** SQLite/Postgres; **no** new
  `LogEntry` variant in v1 (avoids `schemas.py` churn — typed variant deferred, documented).
- Gate idiom: fail-closed + two-variable attestation override + dual-channel audit, exactly
  mirroring the two existing gates.
- Rollback safety: missing manifest / missing helper on-box → **warn + skip**, never
  fail-closed (old rollback tarballs predate this gate; must stay installable).

## Drift-rule self-checks (deployed code Read before drafting)

- ✅ Read `src/agents/shift/scripts/shift-agent-deploy.sh` (presence gate at lines 707–752,
  `rsync -a --delete` at 153, flyer-recovery timer-enable idiom at 668–678) before drafting
  the D1 insertion point and the D2 timer-enable block.
- ✅ Read `tools/check-shift-agent-patch.sh` (sha256 idiom at line 142; two-variable
  `HERMES_PIN_OVERRIDE` + dual-channel audit at 73–135) before drafting the gate + override.
- ✅ Read `tools/check-hermes-config-yaml.sh` and `src/platform/scripts/check-hermes-config-yaml`
  (bash→python→baseline three-layer, `_add_import_roots` staging wrapper) before drafting the
  `check-skills-manifest` three-layer structure.
- ✅ Read `src/agents/shift/scripts/shift-agent-health-watchdog.sh` + `.service` + `.timer`
  (staleness → throttle-file → `notify-owner --priority 2 || true`; oneshot service;
  `OnUnitActiveSec=900`) before drafting the D2 watchdog + units.
- ✅ Read `tools/hermes-config-yaml-baseline.txt` (KEY=VALUE + `#` comment header) before
  drafting `tools/skills-manifest.txt`.
- ✅ Read `tests/test_catering_v02_scripts.py` (Windows-skip via `fcntl`; subprocess-invoke
  pattern) before deciding the core module stays stdlib-only so unit tests run cross-platform.

---

## The gap being closed (evidence)

- Presence gate (`shift-agent-deploy.sh:735-739`) checks SKILL.md **existence only** — an
  in-place rewrite of `dispatch_shift_agent/SKILL.md` (the exact 2026-05-05 curator failure
  mode) passes. **[G2]**
- No runtime watchdog on the skills dir — `shift-agent-health-watchdog.sh` watches only the
  health-check timestamp. A skill written between deploys runs live until the next deploy.
  **[G1]**
- The one self-heal (`rsync -a --delete`, `:153`) removes a rogue skill only at deploy and
  **never alerts** — a §12b automated-reversal with no operator notification. **[G3]**

## Design

Three-layer, mirroring the config gate:

- `src/platform/skills_manifest.py` — **stdlib-only** logic: `build_manifest`, `parse/format`,
  `scan_live_skills` (flat dirs only → foundation namespaces excluded), `verify` (deploy gate),
  `audit` (watchdog). Pure functions → in-process pytest runs on Windows.
- `src/platform/scripts/check-skills-manifest` — executable CLI (`build`/`verify`/`audit`),
  import-root wrapper mirroring `check-hermes-config-yaml`.
- `tools/check-skills-manifest.sh` — bash deploy-gate wrapper (fail-closed + override + audit).
- `tools/skills-manifest.txt` — committed baseline (lockfile: build fails if stale).
- `src/agents/shift/scripts/shift-agent-skills-audit.sh` + `.service` + `.timer` — D2 watchdog.

**Manifest scope decisions (logged):** hash `SKILL.md` only (matches presence-gate scope;
dir-level hashing = future hardening); key = flat skill-dir name (matches on-box flat
namespace); duplicate flat name with differing content across agents → **build fails**
(ambiguity = latent last-rsync-wins bug; enforced invariant + test).

**Split that keeps deploys safe:**
- **D1 deploy gate** = fail-closed **content** check on manifest entries **that are present**
  on-box (conditional-deploy safe: absent = not-deployed agent, presence gate owns that;
  foundation skills = namespaced, not in manifest, skipped). Closes G2.
- **D2 watchdog** = alert-only (never blocks a deploy): `changed` (in-place mod between
  deploys) + `extra` (flat non-manifest, non-foundation skill dir = autonomous/umbrella
  write). Closes G1 + G3.

**Foundation-allowlist residual:** extra-detection excludes namespaced dirs by construction;
a `tools/skills-foundation-allowlist.txt` holds any confirmed *flat* bundled-skill names.
Starts minimal — exact on-box foundation layout needs a VPS check (out of envelope), so
extra-detection is alert-only and the allowlist is operator-tunable. Documented residual.

---

## Task checklist

- [ ] T1 — `skills_manifest.py` stdlib module (TDD: write `tests/test_skills_manifest.py` first)
- [ ] T2 — `src/platform/scripts/check-skills-manifest` CLI wrapper
- [ ] T3 — `tools/check-skills-manifest.sh` bash deploy-gate wrapper (fail-closed + override)
- [ ] T4 — generate + commit `tools/skills-manifest.txt` baseline
- [ ] T5 — wire manifest **build-check** into `tools/build-deploy-tarball.sh` (lockfile friction)
- [ ] T6 — wire manifest **verify** into `shift-agent-deploy.sh` after presence gate (rollback-safe)
- [ ] T7 — D2 watchdog script + `.service` + `.timer` + deploy.sh enable block
- [ ] T8 — `tools/skills-foundation-allowlist.txt` + docs
- [ ] T9 — full `pytest tests/ -q` green; adversarial multi-vector subagent review (§8)
- [ ] T10 — push branch, open PR with self-review + assumptions ledger + deferred-D3 note

## Out of scope (documented, not built)
- D3 config assertions (needs on-box 0.17 flag names).
- Typed `LogEntry` variant for skills-audit alerts (avoids schemas.py churn; v1 uses
  plain-text + notify-owner; follow-up).
- Dir-level (not just SKILL.md) hashing.
- Any live-VPS action: installing the timer, deploying, editing on-box config.
