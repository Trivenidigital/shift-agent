# Plan — D2 skills-audit watchdog: root trust-domain hardening

**Drift-check tag:** `extends-Hermes` — hardens our own watchdog/deploy infra. One deliberate
deviation: the watchdog runs as **root** (not `shift-agent`) — required so it does not share
the uid + writable filesystem of the Hermes gateway it polices. Compensating rationale below;
root-service precedent exists in-tree (`shift-agent-backup.service:6` `User=root`).

**Authorization:** operator "Merge PR, proceed with b and then C" (2026-07-10). Build to PR;
**PAUSE before merge** — a root systemd service change needs on-box validation first.

**Origin:** PR #583 security review (verified) — the D2 watchdog ran as `shift-agent`, the same
uid as the gateway (`hermes-gateway.service`: `User=shift-agent`, `ReadWritePaths=/opt/shift-agent
/root/.hermes`), so a compromised gateway could evade it by poisoning its `.env` config, throttle
file, manifest, OR the checker module itself (imported from adversary-writable `/opt/shift-agent`).

## Hermes-first analysis

| Step | Hermes provides? | Tag |
|---|---|---|
| Agent runtime substrate (ingest/vision/audit/dispatch) | Yes — untouched | `[Hermes]` |
| systemd service uid = root (trust boundary) | No — our ops infra | `[net-new]` |
| Root-owned manifest/module/throttle outside ReadWritePaths | No — our deploy infra | `[net-new]` |
| sha256 audit logic | reused `skills_manifest.py` (stdlib), unchanged | `[Hermes]`/reuse |
| Alert delivery | reused `notify-owner` (works as root) | `[Hermes]`/reuse |

**Hermes ecosystem check:** no Hermes/MCP primitive for "run an integrity watchdog outside the
agent's own uid." This is host-level trust-boundary hardening. Build from scratch, but reuse the
existing module + alerter + timer machinery; ~70 LOC changed, no new module. Receipt:
`skills-audit-root-hardening.json` (net_new=6, hermes=1, extends-Hermes).

## Drift-rule self-checks (deployed code Read before drafting)

- ✅ Read `src/agents/shift/systemd/shift-agent-backup.service` (the in-tree `User=root`
  oneshot precedent to mirror) before drafting the hardened `.service`.
- ✅ Read `src/agents/shift/scripts/shift-agent-notify-owner` (loads `/opt/shift-agent/config.yaml`,
  imports `safe_io`/`schemas` from `/opt`; network Pushover) — confirmed it runs as root, and that
  its `/opt` imports are the one residual (delivery, not detection) — before drafting the alert path.
- ✅ Read `src/platform/systemd/hermes-gateway.service` (`ProtectSystem=strict` +
  `ReadWritePaths=/opt/shift-agent /root/.hermes`) — confirms `/usr/local/share` + `/var/lib` are
  BOTH root-owned (DAC) AND read-only to the gateway (MAC) — before choosing install locations.
- ✅ Read `tests/test_catering_lead_reconcile.py:139` (R4-H-2 invariant) before refining it.

## The fix (closes all three #583 bypasses for DETECTION)

1. **`shift-agent-skills-audit.service`** → `User=root`/`Group=root`, **no `EnvironmentFile`**
   (so `.env` cannot inject `SKILLS_MANIFEST_FILE`), `StateDirectory=shift-agent-skills-audit`
   (root-owned `/var/lib/...` throttle, systemd-created).
2. **`shift-agent-skills-audit.sh`** → hardcode security-critical paths to root-owned, gateway-unwritable
   locations; no env override for them; run the module **directly** as
   `/usr/bin/python3 /usr/local/share/shift-agent/skills_manifest.py audit ...` (self-contained
   stdlib — does NOT import the adversary-writable `/opt/shift-agent/skills_manifest.py`).
3. **`shift-agent-deploy.sh`** (runs as root) → install root-owned copies to
   `/usr/local/share/shift-agent/`: `skills_manifest.py`, `skills-manifest.txt`,
   `skills-foundation-allowlist.txt`, `skills-critical.txt`. Guarded for rollback.
4. **R4-H-2 refinement** — the invariant test currently forbids ANY `install` line containing
   `tools/`; its docstring says only "does not glob `tools/*`". Refine to its true intent:
   forbid (a) a `tools/*` glob, (b) installing `synthetic-retry-harness`, (c) any `tools/` install
   into `/usr/local/bin` — while ALLOWING specific-file installs into root-owned non-bin dirs
   (`/usr/local/share`). Strictly stronger on the harness-leak risk; unblocks (3).

**Trust boundary achieved:** DETECTION (does drift exist?) now uses a root-run, root-owned,
self-contained checker + root-owned manifest + root-owned throttle, all under `/usr` + `/var/lib`
which are root-owned AND read-only to the gateway. A `shift-agent`-uid adversary cannot poison any
of it. **Residual (documented, deferred):** `notify-owner` imports `safe_io`/`schemas` from
`/opt/shift-agent`, so a compromised gateway can break alert DELIVERY (system-wide, loud — affects
every alert, and the §12b `dispatched` log still fires). Detection integrity is not affected.

## Task checklist
- [ ] T1 — hardened `.service` (User=root, no EnvironmentFile, StateDirectory)
- [ ] T2 — hardened watchdog script (root-owned hardcoded paths, run module directly)
- [ ] T3 — deploy.sh root-owned installs to `/usr/local/share/shift-agent/` (guarded)
- [ ] T4 — refine R4-H-2 invariant test (stronger on harness-leak; allow /usr/local/share)
- [ ] T5 — hardening invariant test (service is root + no EnvironmentFile; script reads root-owned paths)
- [ ] T6 — full `pytest` green; security-reviewer subagent confirms bypasses closed
- [ ] T7 — push, open PR, **PAUSE** (needs on-box validation: notify-owner-as-root; foundation layout)

## Out of scope (documented)
- Hardening alert DELIVERY (root-owned notify path independent of `/opt` modules) — bigger; deferred.
- Enabling the timer — still ships DISABLED (foundation-skill layout still unverified on-box).
- Namespaced / dir-level detection; D3 (0.17 config assertions).
