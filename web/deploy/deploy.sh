#!/usr/bin/env bash
# Deploy the Shift Agent Cockpit.
#
#   bash web/deploy/deploy.sh [vps-host]
#
# This is now a thin driver: it builds the frontend, ships a staged payload,
# and invokes the release TRANSACTION on the box
# (web/deploy/cockpit-release.sh), which does preflight -> snapshot -> stage ->
# validate -> cutover -> verify, and rolls back automatically on any failed
# gate.
#
# WHY IT CHANGED. The previous version rsync --delete'd straight into the live
# backend and static trees as its first action, then ran apt-get, pip, systemd,
# logrotate, cron and a Caddy fragment unconditionally, with no snapshot and no
# rollback. It had not been run since 2026-05-31, so one invocation would land
# three months of accumulated change with no way back -- against the service
# that IS the operator's control surface.
#
# The work now happens on the box for two reasons: it matches the established
# pattern of this repo's other deploy path
# (src/agents/shift/scripts/shift-agent-deploy.sh), and SSH stdout cannot be
# captured from the Windows developer machine, so an in-line ssh block produces
# a deploy whose output you cannot read.
#
# Idempotent. Safe to re-run. Delta-aware: side effects whose deployed bytes
# already match the source are skipped rather than replayed.
set -euo pipefail

VPS="${1:-main-vps}"
STAGING=/opt/shift-agent/cockpit-staging
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

COMMIT="$(git rev-parse --short HEAD)"
echo "==> Releasing cockpit at $COMMIT to $VPS"

# ── build the frontend from the CURRENT source ──────────────────────────────
# Built here, not on the box: the box has no node toolchain. The old script
# also did this, but then rsync --delete'd the result straight over the live
# static tree -- so a stale or failed build downgraded production with nothing
# to roll back to. Now the output is staged and the live tree is snapshotted
# first.
echo "==> Building frontend"
pushd web/frontend > /dev/null
npm ci --silent
npm run build --silent
popd > /dev/null
[ -f web/frontend/dist/index.html ] || { echo "ABORT: frontend build produced no dist/index.html"; exit 1; }

# ── ship the staged payload ─────────────────────────────────────────────────
echo "==> Staging payload to $VPS:$STAGING"
ssh "$VPS" "rm -rf $STAGING && mkdir -p $STAGING/deploy"

# tar-over-ssh rather than rsync: rsync is NOT present on a Windows developer
# machine, and the previous script assumed a Linux/Mac dev box -- one more
# reason it could not run from here. tar ships with Git Bash and with every
# POSIX host, so this works from either. The box still uses rsync for the
# static cutover, where it IS installed and --delete semantics matter.
ssh "$VPS" "mkdir -p $STAGING/backend $STAGING/static"
tar czf - --exclude=__pycache__ --exclude=.pytest_cache --exclude=tests \
    -C web/backend . | ssh "$VPS" "tar xzf - -C $STAGING/backend"
tar czf - -C web/frontend/dist . | ssh "$VPS" "tar xzf - -C $STAGING/static"

scp -q web/deploy/shift-agent-cockpit.service "$VPS:$STAGING/deploy/"
scp -q web/deploy/jwt-rotate.cron            "$VPS:$STAGING/deploy/"
scp -q web/deploy/rotate-jwt-secret.sh       "$VPS:$STAGING/deploy/"
scp -q web/deploy/Caddyfile                  "$VPS:$STAGING/deploy/"
# Single source of truth with the shift-agent deploy, which installs the same
# file from its own artifact. Two copies would drift, and the drift is
# invisible until logrotate fails to parse one of them.
scp -q src/agents/shift/logrotate/shift-agent-cockpit "$VPS:$STAGING/deploy/cockpit-logrotate"
scp -q web/deploy/cockpit-release.sh         "$VPS:$STAGING/"
ssh "$VPS" "echo '$COMMIT' > $STAGING/.commit-hash"

# ── run the transaction on the box ──────────────────────────────────────────
echo "==> Running release transaction on $VPS"
echo "    (preflight -> snapshot -> stage -> validate -> cutover -> verify,"
echo "     with automatic rollback on any failed gate)"
ssh "$VPS" "bash $STAGING/cockpit-release.sh"

echo
echo "==> Done. The cockpit is loopback-bound; reach it via an SSH tunnel:"
echo "      ssh -L 8080:127.0.0.1:8080 $VPS   then  http://localhost:8080/"
