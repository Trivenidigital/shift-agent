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
| `deploy` (default) | Snapshot existing `staging-new` to `deploys/<tag>.tgz`, run `install_artifacts`, restart services, run smoke test, auto-rollback on failure |
| `rollback <tag>` | Extract `deploys/<tag>.tgz` into `staging-new`, run `install_artifacts`, restart services |
| `list` | Show all available rollback tarballs at `deploys/` |

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
