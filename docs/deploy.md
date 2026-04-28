# Deploy flow — SMB-Agents on VPS

**Status:** v1, 2026-04-28 — formalizes tarball-based deploy that has been used informally since the project's start.

## TL;DR

```bash
# Local side
./tools/build-deploy-tarball.sh

# Send + deploy
scp shift-agent-deploy.tgz main-vps:/tmp/
ssh main-vps 'sudo tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/ && sudo /usr/local/bin/shift-agent-deploy.sh'
```

That's it. Smoke test runs automatically; auto-rollback fires on smoke-test failure.

## Why tarball, not git-on-VPS

The VPS does not have a git checkout. There's no `/opt/shift-agent/.git-repo` and `/opt/shift-agent/working/` is a fossil from initial setup that's never been touched by deploy.sh. Today's deploys (PRs #14, #15) ran via `scp + sudo install` commands by hand. This flow formalizes that pattern.

Two practical reasons:

1. **No network-to-GitHub dependency at deploy time.** The VPS doesn't need a deploy key; `git pull` can't fail mid-deploy due to GitHub flakiness.
2. **Easier to deploy a specific commit.** Just check that commit out locally, build a tarball from that tree, ship it. No "is the VPS on the right branch" question.

If/when the portfolio grows to multi-VPS fleet management ("deploy this commit to all 9 customer VPSes simultaneously"), git-on-VPS may become attractive — but that's a multi-location-coordinator concern, not a single-VPS one.

## Components

### `tools/build-deploy-tarball.sh` (local)

- Runs pytest (skippable with `--skip-pytest`)
- Captures `git rev-parse HEAD` into a `.commit-hash` file
- Tars `src/` + `.commit-hash` into `shift-agent-deploy.tgz`
- Excludes `__pycache__/`, `*.pyc`, `.pytest_cache/`

### `/usr/local/bin/shift-agent-deploy.sh` (VPS, installed by `install_artifacts`)

Three actions:

| Action | Effect |
|---|---|
| `deploy` (default) | **Run Hermes pin gate** (`tools/check-shift-agent-patch.sh`); fail-closed exit if Hermes commit / bridge.js sha256 / patch markers drift. Then snapshot existing `staging-new` to `deploys/<tag>.tgz`, run `install_artifacts`, restart services, run smoke test, auto-rollback on smoke failure |
| `rollback <tag>` | Extract `deploys/<tag>.tgz` into `staging-new`, run `install_artifacts`, restart services |
| `list` | Show all available rollback tarballs at `deploys/` |

**Hermes pin gate.** The deploy gate (first action above) reads `tools/hermes-patch-baseline.txt` and verifies:
- `HERMES_COMMIT` matches `git -C /root/.hermes/hermes-agent rev-parse HEAD`
- `BRIDGE_POST_PATCH_SHA256` matches `sha256sum scripts/whatsapp-bridge/bridge.js`
- BEGIN/END markers for `shift-agent-sender-id` (in 3 files) + `shift-agent-template-bypass` (in bridge.js) are present and within line-distance of expected anchor symbols

Any drift fail-closes the deploy before `install_artifacts` runs — no state change, no rollback needed. For legitimate Hermes upgrades, set both `HERMES_PIN_OVERRIDE=<current_full_commit_hash>` and `HERMES_PIN_OVERRIDE_REASON="..."`. The override does NOT auto-update the baseline; operator updates `tools/hermes-patch-baseline.txt` + commits + ships a new tarball as a follow-up, or the next deploy fails-closed again. Intentional friction.

### `/opt/shift-agent/` filesystem layout (post-deploy)

```
/opt/shift-agent/
├── staging-new/                  # current source tree (ephemeral, replaced each deploy)
│   ├── src/                      # extracted from tarball
│   └── .commit-hash              # commit ref for this deploy
├── deploys/                      # backup tarballs for rollback (last 5 kept)
│   ├── deploy-20260428-180000-abc12345.tgz
│   └── ...
├── schemas.py                    # installed by install_artifacts
├── safe_io.py
├── sender_context.py
├── exit_codes.py
├── log_source.py
├── templates/
├── state/                        # runtime state (NOT touched by deploy)
├── logs/                         # decisions.log + SHA chain (NOT touched by deploy)
├── config.yaml                   # customer-specific (NOT touched by deploy)
├── roster.json                   # customer-specific (NOT touched by deploy)
└── .env                          # customer-specific (NOT touched by deploy)
```

## Rollback

Two paths, depending on what broke:

### 1. Smoke test failed during this deploy (automatic)

`shift-agent-deploy.sh` calls itself with `rollback <prev-tag>` automatically. Pushover notifies owner of failure + rollback target. No manual intervention needed.

### 2. Behavioral regression discovered later (manual)

```bash
ssh main-vps 'sudo /usr/local/bin/shift-agent-deploy.sh list'
# Pick the deploy tag that was last known-good
ssh main-vps 'sudo /usr/local/bin/shift-agent-deploy.sh rollback deploy-20260428-160000-abc12345'
```

Rollback re-extracts the prior tarball into `staging-new/` and re-runs `install_artifacts`. Idempotent.

## Env file consolidation (post-2026-04-28)

`/opt/shift-agent/.env` is a **symlink to `/root/.hermes/.env`**. Single canonical file, one source of truth for both readers (Hermes' `load_hermes_dotenv()` Python loader + shift-agent systemd `EnvironmentFile=`).

### Why

Pre-consolidation, the two files drifted independently and yesterday's auth-key gotcha (placeholder in one, real key in the other) cost real hours. The two readers are deliberate (different override semantics), but the two FILES are not — they were redundant, and redundancy without sync IS the failure mode.

### Layout

```
/root/.hermes/.env                      # CANONICAL — edit this
/opt/shift-agent/.env                   # symlink → /root/.hermes/.env
/opt/shift-agent/.env.pre-symlink-backup  # one-time backup from migration day
```

### Editing rules

- **Always edit `/root/.hermes/.env`**, never the symlink path. (Most editors handle symlinks correctly, but redirect-to-file via `> /root/.hermes/.env` truncates the target which is fine; redirect-to-file via `> /opt/shift-agent/.env` may break the symlink depending on the operator's shell.)
- After editing, **restart** services that need to pick up the change — `systemctl reload` will NOT reread `EnvironmentFile=`. systemd parses env files at unit *activation* (each `start`/`restart`), then injects them into the process's environment block; the long-running process never re-reads. Same for Hermes' Python `load_dotenv()` — it runs once at module import. So:
  - `sudo systemctl restart hermes-gateway shift-agent-cockpit` after any `.env` change
  - `reload` is a no-op for env-file changes; don't be misled if it succeeds silently
- The `tools/check-env-drift.sh` script (used during the original consolidation) still runs but trivially exits 0 once the symlink is in place. Useful to confirm the symlink is intact.

### Pre-flight gate in `shift-agent-deploy.sh`

Every deploy verifies symlink integrity before `install_artifacts` runs. If `/opt/shift-agent/.env` is no longer a symlink to `/root/.hermes/.env`, the deploy fails-closed before any state change. Catches: Hermes setup re-run that recreated the file, accidental editor truncation, target-path drift, tarball that contained a `.env` file replacing the symlink.

**Manual regression check after any change to gate logic** (PR #19's lesson — code-reading missed the original polarity bug; only "deliberately break, expect fail-closed" caught it):

```bash
# 1. Replace symlink with regular file (simulates breakage)
ssh main-vps 'sudo mv /opt/shift-agent/.env /opt/shift-agent/.env.symlink-saved && \
              sudo bash -c "echo OPENROUTER_API_KEY=fake > /opt/shift-agent/.env"'

# 2. Run a deploy — MUST fail-closed before install_artifacts
ssh main-vps 'sudo /usr/local/bin/shift-agent-deploy.sh' ; echo "exit: $?"
# Expected: exit 1, error pointing at this section, no service state change

# 3. Restore symlink
ssh main-vps 'sudo rm /opt/shift-agent/.env && \
              sudo mv /opt/shift-agent/.env.symlink-saved /opt/shift-agent/.env'

# 4. Verify deploy now passes
ssh main-vps 'sudo /usr/local/bin/shift-agent-deploy.sh'
```

This is currently a manual procedure; bats infrastructure for automated bash-gate tests is backlogged (per PR #17 Low-5).

### Verifying after a `.env` change (smoke test)

After editing `/root/.hermes/.env` and restarting services:

```bash
# Check 1: Hermes-gateway connected to WhatsApp (proves auth keys loaded).
# IMPORTANT: Hermes' app-level log lives in /root/.hermes/logs/agent.log,
# NOT in journalctl (journalctl shows only systemd lifecycle events for
# hermes-gateway). Looking in the wrong place was a smoke-check bug.
ssh main-vps 'sudo tail -200 /root/.hermes/logs/agent.log | grep "✓ whatsapp connected" | tail -1'
# Verify timestamp is within ~10s of your restart command.

# Check 2: cockpit responds (proves systemd EnvironmentFile loaded with COCKPIT_COOKIE_SECURE)
ssh main-vps 'curl -sf http://localhost:8080/api/health'

# Check 3: no startup errors in journalctl
ssh main-vps 'sudo journalctl -u hermes-gateway -u shift-agent-cockpit --since "30 seconds ago" --no-pager | grep -iE "error|auth.*invalid|missing.*env" | grep -v "code -15"'
```

The `code -15` filter excludes the expected systemd-restart-shutdown signal (the previous gateway process exiting cleanly is logged as `code -15`; without that filter, every clean restart looks like a failure).

### Customer-VPS bring-up: migration is step-0 (REQUIRED before first deploy)

On a fresh customer VPS, `/opt/shift-agent/.env` is a regular file from initial provisioning. The deploy script's symlink-integrity gate is **strict** — it fails-closed if `/opt/shift-agent/.env` is not a symlink. So migration MUST happen before the first deploy attempts to run.

**Prerequisite:** Hermes runtime must be installed and `/root/.hermes/.env` must exist before this sequence. If `/root/.hermes/.env` is missing, `check-env-drift.sh` exits 2 ("env files missing") and the migration cannot proceed. Order on a brand-new VPS: install Hermes → populate `/root/.hermes/.env` and `/opt/shift-agent/.env` from the customer-config templates → THEN run step-0 below.

```bash
# Step 0 — extract a tarball into staging (no install yet)
scp shift-agent-deploy.tgz <customer-vps>:/tmp/
ssh <customer-vps> 'sudo tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/'

# Step 1 — verify drift state. Must show OK (all keys match) before proceeding.
ssh <customer-vps> 'sudo /opt/shift-agent/staging-new/tools/check-env-drift.sh'

# Step 2 — run the migration. Idempotent; safe to re-run.
ssh <customer-vps> 'sudo /opt/shift-agent/staging-new/tools/migrate-env-to-symlink.sh'

# Step 3 — restart services to pick up the unified env
ssh <customer-vps> 'sudo systemctl restart hermes-gateway shift-agent-cockpit'

# Step 4 — only NOW run the deploy. The symlink-integrity gate will pass.
ssh <customer-vps> 'sudo /usr/local/bin/shift-agent-deploy.sh'
```

If `check-env-drift.sh` reports drift, reconcile manually (the script prints sha256 hash + length per drifted key so the operator can identify placeholder-vs-real without leaking secrets) then re-run.

If the operator skips migration and runs deploy directly, the symlink-integrity gate fails-closed with a clear error pointing at this section.

The Triveni production VPS was migrated on 2026-04-28 and is post-step-0 going forward. Future customer VPSes follow the sequence above.

## One-time post-merge cleanups (per-VPS)

Some PRs ship a state change that requires a one-shot cleanup on the VPS *after* the deploy lands. Listed here so a future operator setting up a new customer VPS, or someone reading deploy.md cold, sees them all in one place.

Each item: which PR introduced it, what command(s) to run, what the system looks like afterward.

### `decisions.log.sha256*` removal (PR #20, 2026-04-28)

The audit-log SHA-256 chain was decoration (~3% writer coverage, no verifier). Removed code-side; orphan files on VPS need cleanup.

```bash
ssh main-vps 'sudo rm -f /opt/shift-agent/logs/decisions.log.sha256 \
                          /opt/shift-agent/logs/decisions.log.sha256.lock'
```

Idempotent — safe to re-run; `rm -f` is a no-op if files are already gone. After run: `decisions.log` writes continue normally via `safe_io.ndjson_append`; no `.sha256` accumulating.

### `.env.pre-symlink-backup` retention window (PR #18, 2026-04-28)

`migrate-env-to-symlink.sh` creates a backup of the pre-migration `.env` for rollback safety. After 24h+ of clean operation, the backup can be removed:

```bash
ssh main-vps 'sudo rm /opt/shift-agent/.env.pre-symlink-backup'
```

If you re-ran migration partial-failure-then-recover, you may have timestamped backups (`.pre-symlink-backup-<unix-ts>`) — same removal pattern, list first to verify:

```bash
ssh main-vps 'ls -la /opt/shift-agent/.env.pre-symlink-backup*'
```

## Limits + known caveats

1. **State files are NOT in the deploy.** `pending.json`, `roster.json`, `catering-leads.json`, `decisions.log`, `config.yaml`, `.env` are customer-specific and never touched by `install_artifacts`. Schema migrations to those files need a separate one-shot script.

2. **Removal of files is not symmetric across rollback.** If deploy N adds `/usr/local/bin/new-script` and you roll back to N-1's tarball, `new-script` lingers (the rollback tarball doesn't reference it, so install_artifacts doesn't restore it, so it's not removed). It's unreferenced and harmless, but the filesystem is not bit-for-bit identical to the prior state. If a clean rollback matters (e.g., a security removal), do it manually after the rollback.

3. **`KEEP_TARBALLS=5`** in `shift-agent-deploy.sh`. Older deploys are deleted to bound disk usage. Tune via the script if needed.

4. **Hermes version is not pinned by this script.** That's the next critical-tier item per `docs/hermes-alignment.md`. The bridge.js patches in `tools/patch-bridge-filter.py` will silently break on upstream Hermes upgrades until that's fixed.

5. **No VPS-side pytest gate.** Smoke test verifies installed scripts + Python module imports + config validation. It does NOT re-run the unit suite. Pytest runs as a gate in `build-deploy-tarball.sh` on the local side; if you skip it via `--skip-pytest`, you've skipped the gate.

## Future improvements (NOT in scope of this rewrite)

- **CI builds the tarball.** Today the local-side step runs on the developer's machine. Moving it to GitHub Actions on `main` push gives reproducibility + reduces "developer's local has uncommitted changes" risk.
- **Smoke test runs the unit suite.** Re-running pytest on VPS catches "tarball includes a file but the tarball was built with an older test suite" drift.
- **Manifest-aware rollback.** Each deploy writes a manifest of installed files; rollback uses the manifest to remove files added in the rolled-back deploy. Closes the asymmetry caveat.
- **Multi-VPS fleet deploy.** Single command deploys the same tarball to all customer VPSes. Sits with agent #3 (multi-location) territory.
