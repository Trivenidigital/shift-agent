# DEPLOY_CHECKLIST — shift-agent → main-vps (canonical, 2026-07-20)

One deploy session ships `origin/main` HEAD to main-vps (46.62.206.192) as a full-tree
tarball replacement. Deploys are OPERATOR-AUTHORIZED, one at a time, recorded.

## Pre-deploy gate (every deploy)
1. `git fetch origin` → target = `origin/main` HEAD; working tree CLEAN at that SHA
   (checkout config: `core.autocrlf=false`, `core.eol=lf` — set 2026-07-19; if a file
   diverges from its blob, rewrite with `git show HEAD:<f>` bytes before building).
2. Live SHA: `ssh root@main-vps 'ls -1t /opt/shift-agent/deploys/ | head -1'` — MUST be
   an ancestor of the target (no accidental rollback) and you ship the WHOLE
   `live..target` diff. Review that range; nothing unauthorized rides along
   (no `.env`/flag/data files; grep the range).
3. Concurrent-session check: no other session mid-deploy (deploy-coordination rule).
4. Census sanity where the range touches money/routing: approval-code pools
   collision-free; relevant state files healthy (read-only probes).

## Build
5. `bash tools/build-deploy-tarball.sh` — runs the FULL pytest gate + skills-manifest
   lockfile check; refuses on failure. Output: `shift-agent-deploy.tgz` (src/ +
   .commit-hash).

## Ship + execute
6. `scp shift-agent-deploy.tgz root@46.62.206.192:/tmp/`
7. ```
   ssh root@46.62.206.192 'tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/ \
     && export HERMES_PIN_OVERRIDE=1e71b7180e5b4e84905b9a3086cf9cecca139562 \
     && export HERMES_PIN_OVERRIDE_REASON="<PRs + SHA + why>" \
     && bash /opt/shift-agent/staging-new/src/agents/shift/scripts/shift-agent-deploy.sh'
   ```
   The pin override is REQUIRED on this box (Hermes pinned 0.14; see
   reference-hermes-core-patch-deploy-mechanics). Deploys touching Hermes core
   (whatsapp.py / bridge.js / run.py / patch-hermes.py) need that runbook FIRST.
   SSH output: two-step pattern (redirect to file, Read the file).

## What the deploy script itself does (do not re-implement)
- Pin gate (fail-closed) → snapshot/rollback tarball → install_artifacts (scripts →
  /usr/local/bin; platform modules FLAT → /opt/shift-agent/*.py; plugins rsync →
  **/root/.hermes/plugins/** — the AUTHORITATIVE gateway plugin path;
  /opt/shift-agent-source is a stale non-runtime copy) → **lock initializations
  BEFORE restart** (approval-code-pools.lock + catering-amendments.json.lock: O_EXCL
  0660 shift-agent, dual-identity FileLock fd-verification; FATAL aborts pre-restart;
  the amendments DATA file is never precreated — gateway-first-writer contract) →
  service restart (hermes-gateway + timers) → built-in smoke suite with AUTO-ROLLBACK.

## Post-deploy verification (read-only)
8. Newest `/opt/shift-agent/deploys/` entry = target SHA; `systemctl is-active
   hermes-gateway` = active; `journalctl -u hermes-gateway -p err --since -10min`
   clean.
9. Module resolution for anything the range added: import under BOTH interpreters
   (system `python3` for scripts; `/root/.hermes/hermes-agent/venv/bin/python` for
   the gateway) with `sys.path.insert(0, "/opt/shift-agent")`, assert `__file__`.
10. Range-specific read-only checks per that deploy's authorization package
    (tasks/audits/deploy-authorization-<sha>.md is the template).
11. Known benign smoke warnings: Pushover-muted skip (until un-mute executes);
    Agent #21 venv absent.

## Environment / units / migrations
- Env: on-box `.env` is a SYMLINK to /root/.hermes/.env — never `sed -i` the symlink;
  edit the target; back up first. NO env changes ride a code deploy without their own
  authorization.
- Systemd: units install from src/agents/*/systemd; `openrouter-balance-check.timer`
  enabled+active (verified 2026-07-19).
- Migrations: none exist in this system; state-file shape changes must be
  rollback-analyzed in the PR (forbid-schema additions are the hazard class — see
  the R2A sidecar decision).

## Rollback
12. `shift-agent-deploy.sh rollback <prior deploy tag>` (tags in /opt/shift-agent/
    deploys/). Guarded per-module blocks self-heal (older tarballs remove modules
    they predate). PRESERVE: both canonical locks and any catering-amendments.json
    created by live traffic (nothing removes them; old code ignores them).
    decisions.log new-variant rows are tolerated by the _UnknownLogEntry shim.

## Record-keeping
13. Approvals-log row (authorization source + SHA + deploy tag) in the relevant
    audit doc; memory update; the deploy log file retained.
