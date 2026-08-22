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
    # Run the deploy script FROM STAGING. The installed /usr/local/bin copy is
    # written BY a deploy, so it is the PREVIOUS release's logic; a tarball that
    # changes shift-agent-deploy.sh must not be deployed by the code it replaces.
    #
    # `[ -f ]` + `bash`, NOT `[ -x ]`: the script is tracked mode 100644, so a
    # tarball built on Linux carries no x-bit and `[ -x ]` would silently select
    # the installed copy — the exact fallback this exists to avoid — while still
    # exiting 0. Only `install_artifacts` chmods it 755, and only at the
    # destination. This matches shift-agent-deploy.sh's own `bash "$CLOSURE_CHECK"`
    # and tasks/DEPLOY_CHECKLIST.md, both of which are already mode-safe.
    #
    # `ssh -n` because ssh inherits this loop's stdin, which is the VPS list:
    # without it the first host's ssh drains the remaining hosts and the loop
    # exits 0 having deployed exactly one VPS.
    ssh -n "$vps" 'S=/opt/shift-agent/staging-new/src/agents/shift/scripts/shift-agent-deploy.sh;         [ -f "$S" ] || S=/usr/local/bin/shift-agent-deploy.sh;         cd /opt/shift-agent && bash "$S"'         > "$SMOKE_OUT" 2>&1 || {
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
