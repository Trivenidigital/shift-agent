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
#   ssh main-vps 'sudo tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/ && sudo /usr/local/bin/shift-agent-deploy.sh'

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
    src/ tools/ .commit-hash

# Cleanup the staging hash file
rm -f .commit-hash

SIZE=$(du -h "$TARBALL" | cut -f1)
echo "=== built $TARBALL ($SIZE) ==="
echo ""
echo "Deploy with:"
echo "  scp $TARBALL main-vps:/tmp/"
echo "  ssh main-vps 'sudo tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/ && sudo /usr/local/bin/shift-agent-deploy.sh'"
