#!/usr/bin/env bash
# Rotate the cockpit JWT secret (idempotent; cron-friendly).
#
# Touches ONLY /opt/shift-agent/state/.cockpit-jwt-secret — never .env.
# (Per design v1.1 — narrower blast radius than .env edits.)
#
# Usage (manual):
#   sudo bash /opt/shift-agent/cockpit/rotate-jwt-secret.sh
#
# Usage (cron, monthly):
#   0 3 1 * * /opt/shift-agent/cockpit/rotate-jwt-secret.sh >> /opt/shift-agent/logs/jwt-rotate.log 2>&1
#
# After rotation: all existing sessions become invalid; the owner re-logs-in
# via Pushover OTP or TOTP (whichever is configured).
#
# Exit 0 on success, non-zero with explicit reason. Verifies the cockpit
# came back up via /auth/me before claiming success — addresses Reviewer 3
# 90-day-prediction (silent service failure after rotation).

set -euo pipefail

SECRET_PATH=/opt/shift-agent/state/.cockpit-jwt-secret
BACKUP_DIR=/opt/shift-agent/state/backups
SERVICE=shift-agent-cockpit
TS=$(date -u +%Y%m%dT%H%M%SZ)

log() { echo "[$(date -u +%FT%TZ)] rotate-jwt-secret: $*"; }

# 1. Snapshot current secret to backups (mode 0600, root-owned)
mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"

if [ -f "$SECRET_PATH" ]; then
    BAK="$BACKUP_DIR/.cockpit-jwt-secret-$TS.bak"
    cp -p "$SECRET_PATH" "$BAK"
    chmod 0600 "$BAK"
    log "backed up current secret -> $BAK"
else
    log "no existing secret to back up"
fi

# 2. Generate new 256-bit hex secret + atomic write
NEW=$(/usr/bin/python3 -c 'import secrets; print(secrets.token_hex(32))')
TMP=$(mktemp /opt/shift-agent/state/.cockpit-jwt-secret.XXXXXX)
echo "$NEW" > "$TMP"
chmod 0600 "$TMP"
chown shift-agent:shift-agent "$TMP"
mv -f "$TMP" "$SECRET_PATH"
log "wrote new secret (32 bytes hex)"

# 3. Restart cockpit; --wait blocks until the new process is ready (or fails).
if ! /usr/bin/systemctl restart --wait "$SERVICE"; then
    log "ERROR: systemctl restart --wait $SERVICE failed; rolling back"
    if [ -f "${BAK:-}" ]; then
        mv -f "$BAK" "$SECRET_PATH"
        chown shift-agent:shift-agent "$SECRET_PATH"
        chmod 0600 "$SECRET_PATH"
        /usr/bin/systemctl restart --wait "$SERVICE" || true
        log "rolled back to previous secret"
    fi
    exit 1
fi

# 4. Health probe — give the cockpit 10s to bind, then verify.
sleep 2
for i in 1 2 3 4 5; do
    if /usr/bin/curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8080/health; then
        log "/health green after restart (attempt $i)"
        log "OK: rotation complete; owner must re-login (all prior sessions invalidated)"
        exit 0
    fi
    sleep 2
done

log "ERROR: /health probe failed after restart; cockpit may be degraded — manual intervention required"
exit 2
