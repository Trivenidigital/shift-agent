#!/usr/bin/env bash
# tools/check-pr-d2-rollback-target.sh — PR-D2 deploy preflight gate.
#
# Resolves design v2 §14.1 B-RB1 (R3 BLOCKER): shift-agent-deploy.sh selects
# PREV_TAG via `ls -t deploys/*.tgz | head -1` (most-recent mtime), NOT by
# soak duration. The 24h PR-D1 soak is operationally irrelevant to tarball
# selection — any intermediate deploy or KEEP_TARBALLS=5 rotation can break
# the chain that PR-D2 rollback depends on.
#
# This gate is invoked manually by the operator before running
# `shift-agent-deploy.sh` for PR-D2. It refuses if the most-recent prior
# tarball does NOT carry the expected PR-D1 SHA. Operator pins the
# expected SHA at deploy time:
#
#   tools/check-pr-d2-rollback-target.sh <vps-host> <pr-d1-sha-short>
#
# Exits 0 if PREV_TAG matches; exits 1 otherwise (operator must ship
# PR-D1 again, or accept the rollback risk explicitly).
#
# Two-step Windows pattern: ssh output to file, then cat the file —
# Bash tool on Windows cannot capture SSH stdout directly.
set -euo pipefail

VPS_HOST="${1:?usage: $0 <vps-host> <expected-pr-d1-sha>}"
EXPECTED_SHA="${2:?usage: $0 <vps-host> <expected-pr-d1-sha>}"

OUT_FILE="${PR_D2_GATE_OUT:-.pr_d2_gate.txt}"

ssh "$VPS_HOST" '
  set -euo pipefail
  PREV=$(ls -t /opt/shift-agent/deploys/deploy-*.tgz 2>/dev/null | head -1 || true)
  if [ -z "$PREV" ]; then
    echo "ABORT: no prior tarball in /opt/shift-agent/deploys/" >&2
    exit 1
  fi
  PREV_SHA=$(basename "$PREV" .tgz | sed "s/^deploy-//")
  echo "PREV_TARBALL=$PREV"
  echo "PREV_SHA=$PREV_SHA"
' > "$OUT_FILE" 2>&1 || {
    cat "$OUT_FILE" >&2
    exit 1
}

cat "$OUT_FILE"

PREV_SHA=$(grep '^PREV_SHA=' "$OUT_FILE" | cut -d= -f2 | tr -d '[:space:]')

if [ "$PREV_SHA" = "$EXPECTED_SHA" ] || [ "${PREV_SHA#$EXPECTED_SHA}" != "$PREV_SHA" ]; then
    echo "PR-D2_ROLLBACK_TARGET_OK: PREV_SHA=$PREV_SHA matches expected $EXPECTED_SHA"
    exit 0
fi

echo "ABORT: PREV_SHA=$PREV_SHA does NOT match expected PR-D1 SHA $EXPECTED_SHA" >&2
echo "       PR-D2 rollback would NOT restore the shim-bearing tarball." >&2
echo "       Re-deploy PR-D1, or accept the rollback risk explicitly." >&2
exit 1
