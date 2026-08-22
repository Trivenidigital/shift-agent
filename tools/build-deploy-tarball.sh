#!/usr/bin/env bash
# build-deploy-tarball — package the repo's deployable surface into a tarball
# the VPS shift-agent-deploy.sh script can extract into /opt/shift-agent/staging-new/.
#
# Output: ./shift-agent-deploy.tgz at the repo root.
# Contents: src/ + .commit-hash (one-line plain text with git rev-parse HEAD).
#
# Usage:
#   ./tools/build-deploy-tarball.sh
#   ./tools/build-deploy-tarball.sh --skip-pytest   # don't run pytest before tarballing
#
# Then SCP and deploy:
#   scp shift-agent-deploy.tgz main-vps:/tmp/
#   ssh main-vps 'sudo tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/ \
#     && sudo bash /opt/shift-agent/staging-new/src/agents/shift/scripts/shift-agent-deploy.sh'
#
# Run the deploy script FROM STAGING via `bash`, not the installed
# /usr/local/bin copy. The installed copy is written BY a deploy, so it is the
# PREVIOUS release's logic; a tarball that changes shift-agent-deploy.sh would
# otherwise be deployed by the code it replaces (the 2026-08-14 failed-safe
# rollback).
#
# The script is tracked 100755 BECAUSE it re-execs itself as `"$0" rollback`
# on every gate-failure path; a non-executable copy breaks auto-rollback on a
# deploy that already failed. `bash` here is defence-in-depth for the entrypoint
# only -- it does NOT cover that re-exec, so the mode is the real invariant.
# tasks/DEPLOY_CHECKLIST.md is already mode-safe this way, and also sets the
# HERMES_PIN_OVERRIDE that main-vps requires.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

SKIP_PYTEST=0
for arg in "$@"; do
    case "$arg" in
        --skip-pytest) SKIP_PYTEST=1 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# Refuse to tarball uncommitted changes — deploys must be reproducible from
# git history. Use --skip-pytest to override pytest only.
if ! git diff --quiet HEAD 2>/dev/null; then
    echo "WARN: working tree has uncommitted changes — tarball will include them but commit-hash will point at HEAD" >&2
    echo "      consider committing first for traceable deploys" >&2
fi

# Pytest gate (skippable)
if [ "$SKIP_PYTEST" -eq 0 ]; then
    echo "=== running pytest ==="
    if ! python -m pytest tests/ -q > /tmp/build-deploy-pytest.log 2>&1; then
        echo "PYTEST FAILED — refusing to build tarball." >&2
        echo "log: /tmp/build-deploy-pytest.log" >&2
        tail -30 /tmp/build-deploy-pytest.log >&2
        exit 1
    fi
    tail -1 /tmp/build-deploy-pytest.log
fi

# Skills-manifest lockfile check. tools/skills-manifest.txt must match a fresh build of
# src/agents; otherwise a SKILL.md was edited without regenerating the manifest, which would
# fail-close the deploy-time content gate (check-skills-manifest.sh). Fail here — on the dev
# box, cheaply — rather than on the customer VPS. Not gated by --skip-pytest: it's a
# consistency check on the artifact, not a test run.
echo "=== checking skills-manifest is current ==="
PYBIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYBIN" ]; then
    echo "WARN: no python found — skipping skills-manifest lockfile check" >&2
elif ! "$PYBIN" src/platform/skills_manifest.py build --check tools/skills-manifest.txt; then
    echo "SKILLS-MANIFEST STALE — refusing to build tarball." >&2
    echo "  A SKILL.md changed without regenerating the manifest." >&2
    echo "  Fix: ./tools/check-skills-manifest.sh build   (then commit tools/skills-manifest.txt)" >&2
    exit 1
fi

# Capture commit hash for traceability + as the deploy tag
COMMIT_HASH=$(git rev-parse HEAD)
echo "$COMMIT_HASH" > .commit-hash
echo "=== commit: ${COMMIT_HASH:0:8} ==="

# Build the tarball. Exclude __pycache__/ + *.pyc (deployed Python is rebuilt
# from source on first import). Include .commit-hash at the tarball root so
# it lands beside src/ in /opt/shift-agent/staging-new/.
TARBALL="$REPO_ROOT/shift-agent-deploy.tgz"
echo "=== building $TARBALL ==="
tar czf "$TARBALL" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.pytest_cache' \
    src/ tools/ requirements-hermes-venv.txt .commit-hash

# Cleanup the staging hash file
rm -f .commit-hash

SIZE=$(du -h "$TARBALL" | cut -f1)
# Emit the artifact sha256 so authorization can bind to the BYTES, not just the
# self-declared .commit-hash label (RC-review R7/R8 + synthesis condition 2): the
# operator captures this and verifies it pre-extraction as the runbook gate.
ARTIFACT_SHA256=$(sha256sum "$TARBALL" | awk '{print $1}')
echo "=== built $TARBALL ($SIZE) ==="
echo "=== artifact sha256: $ARTIFACT_SHA256 ==="
echo ""
echo "Deploy with:"
echo "  scp $TARBALL main-vps:/tmp/"
echo "  ssh main-vps 'sudo tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/ \\"
echo "     && sudo bash /opt/shift-agent/staging-new/src/agents/shift/scripts/shift-agent-deploy.sh'"
echo ""
echo "  FROM STAGING via bash, not /usr/local/bin: the installed copy is the"
echo "  PREVIOUS release deploy logic. The script is tracked 100755 because it"
echo "  re-execs itself by path on rollback; bash is defence-in-depth for the"
echo "  entrypoint and does not cover that re-exec."
echo "  main-vps also requires HERMES_PIN_OVERRIDE - see tasks/DEPLOY_CHECKLIST.md."
