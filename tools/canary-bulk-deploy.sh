#!/usr/bin/env bash
# tools/canary-bulk-deploy.sh — staggered halt-on-failure deploy across 8 VPS.
#
# PR-D2 commit 7 / design v2 §6 + §9.2 R5-H1.
#
# Operator runs this AFTER canary VPS clears 60-min soak + synthetic-retry
# probe. Bulk-deploys remaining 8 VPS with halt-on-failure semantics:
# each VPS's smoke must clear before the next deploy launches.
#
# Single-VPS rollback is the bound — a failed smoke takes down 1 VPS,
# not 4 as in a naive parallel rollout.
#
# Usage:
#   tools/canary-bulk-deploy.sh <vps-list-file>
# where <vps-list-file> contains one VPS hostname per line.

set -euo pipefail

VPS_LIST_FILE="${1:?usage: $0 <vps-list-file>}"

if [ ! -r "$VPS_LIST_FILE" ]; then
    echo "ABORT: cannot read $VPS_LIST_FILE" >&2
    exit 1
fi

# Two-step SSH-to-file pattern (Windows-bash compat per CLAUDE.md)
SMOKE_OUT=".canary_smoke.txt"

while IFS= read -r vps; do
    [ -z "$vps" ] && continue
    [[ "$vps" =~ ^# ]] && continue  # skip comments
    echo "=== deploying to $vps ==="

    # Per-VPS deploy. Tarball already on canary; assume operator has scp'd
    # to each remaining VPS as part of the wider deploy SOP.
    ssh "$vps" 'cd /opt/shift-agent && /usr/local/bin/shift-agent-deploy.sh' \
        > "$SMOKE_OUT" 2>&1 || {
        echo "ABORT: $vps deploy failed (see $SMOKE_OUT)" >&2
        cat "$SMOKE_OUT" >&2
        exit 1
    }

    # shift-agent-deploy.sh is synchronous and exits non-zero if its smoke gate
    # fails or rolls back. A zero exit here is the smoke-clear signal; do not
    # poll a sidecar status file that the deploy script does not own/write.
    echo "$vps: deploy + smoke OK"

    # 2-min cooldown only AFTER smoke clear (NOT in the polling loop)
    echo "$vps: cooldown 120s before next VPS"
    sleep 120
done < "$VPS_LIST_FILE"

echo "CANARY_BULK_DEPLOY_OK"
