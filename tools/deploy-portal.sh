#!/usr/bin/env bash
# tools/deploy-portal.sh — standalone deployer for the SMB-Agents portfolio
# portal.
#
# DECOUPLED from shift-agent-deploy.sh by design: portal HTML edits should NOT
# trigger the full agent-deploy gauntlet (Hermes pin + state migration +
# vision-auth smoke + auto-rollback). This script does ONE thing: scp the
# static HTML + systemd unit to srilu, install + reload + restart, and
# smoke-verify.
#
# Usage:
#   bash tools/deploy-portal.sh                          # default: root@srilu-vps
#   bash tools/deploy-portal.sh root@example.vps         # custom target
#   bash tools/deploy-portal.sh --skip-external-smoke    # don't curl from local
#   bash tools/deploy-portal.sh root@x.vps --skip-external-smoke
#
# Exit codes:
#   0 — deploy + smoke verify both passed
#   1 — local pre-flight failed (missing files)
#   2 — remote pre-flight failed (port :8080 already in use on target)
#   3 — internal smoke failed (curl localhost on srilu)
#   4 — external smoke failed (curl from local to srilu's public IP)

set -euo pipefail

# Hardcoded port — matches systemd unit's ExecStart. Don't add a $PORT
# override: the unit's ExecStart hardcodes 8080, so an override here would
# create a smoke-vs-service mismatch (R1-M1 design fix).
PORT=8080
EXTERNAL_IP="${EXTERNAL_IP:-89.167.116.187}"

TARGET="root@srilu-vps"
SKIP_EXTERNAL_SMOKE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-external-smoke) SKIP_EXTERNAL_SMOKE=1; shift ;;
        -h|--help)
            sed -n '2,25p' "$0"; exit 0 ;;
        *) TARGET="$1"; shift ;;
    esac
done

cd "$(dirname "$0")/.."
HTML_LOCAL="web/portal/index.html"
SVC_LOCAL="src/platform/systemd/triveni-portal.service"

echo "=== Pre-flight (local) ==="
[ -f "$HTML_LOCAL" ] || { echo "FATAL: $HTML_LOCAL missing"; exit 1; }
[ -f "$SVC_LOCAL"  ] || { echo "FATAL: $SVC_LOCAL missing"; exit 1; }
echo "✓ local files present"

echo "=== Pre-flight (remote port :$PORT availability) ==="
# R2-M2 design fix: pre-flight check before installing.
# `ss -tln | grep ":$PORT "` matches a literal ":<PORT> " in the LISTEN list.
# If something else owns :$PORT, abort clearly instead of installing into a
# guaranteed-fail-loop.
if ssh "$TARGET" "ss -tln 2>/dev/null | grep -q \":$PORT \" && ! systemctl is-active --quiet triveni-portal.service"; then
    echo "FATAL: port :$PORT on $TARGET is already in use by something other than triveni-portal.service"
    ssh "$TARGET" "ss -tlnp 2>/dev/null | grep \":$PORT \"" || true
    exit 2
fi
echo "✓ port :$PORT free (or already owned by triveni-portal.service)"

echo "=== scp to $TARGET ==="
scp -q "$HTML_LOCAL" "$TARGET:/tmp/triveni-portal-index.html"
scp -q "$SVC_LOCAL"  "$TARGET:/tmp/triveni-portal.service"
echo "✓ files staged at /tmp on remote"

echo "=== Install + restart ==="
ssh "$TARGET" 'bash -se' <<'REMOTE'
set -euo pipefail
# R2-N6 design fix: self-bootstrap /opt/shift-agent/logs/ in case this VPS
# never had shift-agent-deploy.sh run (defensive — srilu has it today).
install -d -o shift-agent -g shift-agent -m 0750 /opt/shift-agent/logs
install -d -o shift-agent -g shift-agent /opt/triveni /opt/triveni/portal
install -m 644 -o shift-agent -g shift-agent \
    /tmp/triveni-portal-index.html /opt/triveni/portal/index.html
install -m 644 /tmp/triveni-portal.service /etc/systemd/system/triveni-portal.service
systemctl daemon-reload
# R2-M1 design fix: enable + restart (NOT enable --now). enable is idempotent;
# restart picks up unit-file changes on re-deploy. enable --now alone would
# silently skip restart if the service was already enabled.
systemctl enable triveni-portal.service
systemctl restart triveni-portal.service
rm -f /tmp/triveni-portal-index.html /tmp/triveni-portal.service
# R1-M3 design fix: separate command, not in echo $() — so set -e fires if
# is-active returns non-zero (failed/inactive).
systemctl is-active --quiet triveni-portal.service
echo "✓ installed + restarted; service active"
REMOTE

echo "=== Internal smoke (ssh + curl localhost:$PORT) ==="
COUNT=$(ssh "$TARGET" "curl -s --max-time 5 http://localhost:$PORT/ | grep -c 'SMB-Agents' || true")
if [ "${COUNT:-0}" -lt 1 ]; then
    echo "FATAL: internal smoke failed; service may have started then crashed"
    ssh "$TARGET" "systemctl status triveni-portal.service --no-pager -l | tail -20" || true
    ssh "$TARGET" "journalctl -u triveni-portal.service -n 30 --no-pager" || true
    exit 3
fi
echo "✓ internal smoke: $COUNT 'SMB-Agents' hits"

if [ "$SKIP_EXTERNAL_SMOKE" -eq 1 ]; then
    echo "=== External smoke skipped (--skip-external-smoke) ==="
else
    echo "=== External smoke (local curl to $EXTERNAL_IP:$PORT) ==="
    EXT=$(curl -s --max-time 5 "http://$EXTERNAL_IP:$PORT/" | grep -c "SMB-Agents" || true)
    if [ "${EXT:-0}" -lt 1 ]; then
        # R1-M2 design fix: demote external-smoke failure to WARNING when
        # internal smoke passed. Internal-smoke success means the service IS
        # up; external failure usually means the operator's local network
        # can't reach the public IP (VPN, corporate egress, etc.).
        echo "WARN: external smoke failed (curl from local to $EXTERNAL_IP:$PORT)"
        echo "      internal smoke already passed → service IS up"
        echo "      check operator's local network reachability to $EXTERNAL_IP"
        echo "      use --skip-external-smoke to silence this in the future"
    else
        echo "✓ external smoke: $EXT 'SMB-Agents' hits"
    fi
fi

echo ""
echo "Portal live at: http://$EXTERNAL_IP:$PORT/"
echo "Logs: ssh $TARGET 'tail -f /opt/shift-agent/logs/triveni-portal.log'"
echo "Service status: ssh $TARGET 'systemctl status triveni-portal.service'"
