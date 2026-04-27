#!/usr/bin/env bash
# Rotate the cockpit JWT secret. Idempotent + cron-friendly.
#
# Touches ONLY /opt/shift-agent/state/.cockpit-jwt-secret (never .env)
# — narrower blast radius per design v1.1.
#
# Usage (manual):  sudo bash /opt/shift-agent/cockpit/rotate-jwt-secret.sh
# Usage (cron):    0 3 1 * * /opt/shift-agent/cockpit/rotate-jwt-secret.sh >> /opt/shift-agent/logs/jwt-rotate.log 2>&1
#
# Side effects: ALL existing JWT cookies become unverifiable; the next request
# from any logged-in browser returns 401, the SPA redirects to LoginScreen, and
# the owner re-authenticates via Pushover OTP (the only login factor that
# produces auth_method='pushover'-claim JWTs needed for sensitive routes).
# This is intentional — rotation is also the recommended forced-re-login on
# upgrade from a pre-auth_method-claim cockpit (see web/README.md).
#
# Exit codes:
#   0 — rotation succeeded, /health green
#   1 — write failed (no service change), or restart failed and rollback succeeded
#   2 — restart failed AND rollback failed (URGENT: manual intervention)
#   3 — restart succeeded but /health probe failed (cockpit degraded)

set -euo pipefail

SECRET_PATH=/opt/shift-agent/state/.cockpit-jwt-secret
BACKUP_DIR=/opt/shift-agent/state/backups
SERVICE=shift-agent-cockpit
TS=$(date -u +%Y%m%dT%H%M%SZ)
HAD_PRIOR_SECRET=0
BAK=""
TMP=""

log() { echo "[$(date -u +%FT%TZ)] rotate-jwt-secret: $*"; }

cleanup_tmp() {
  if [ -n "$TMP" ] && [ -f "$TMP" ]; then
    rm -f "$TMP" || true
  fi
}
trap cleanup_tmp EXIT

# 1. Snapshot current secret (track whether one existed)
mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"

if [ -f "$SECRET_PATH" ]; then
    HAD_PRIOR_SECRET=1
    BAK="$BACKUP_DIR/.cockpit-jwt-secret-$TS.bak"
    cp -p "$SECRET_PATH" "$BAK"
    chmod 0600 "$BAK"
    log "backed up current secret -> $BAK"
else
    log "no existing secret (first rotation)"
fi

# 2. Atomic write of new 256-bit hex secret.
# Order: chown FIRST (to set final ownership), then chmod, then mv.
# Reviewer 2 critical-2: write-phase failure MUST trigger rollback or
# leave NO orphan with wrong perms. trap above handles cleanup.
NEW=$(/usr/bin/python3 -c 'import secrets; print(secrets.token_hex(32))')
TMP=$(mktemp /opt/shift-agent/state/.cockpit-jwt-secret.XXXXXX)
printf '%s\n' "$NEW" > "$TMP"
chown shift-agent:shift-agent "$TMP"
chmod 0600 "$TMP"

if ! mv -f "$TMP" "$SECRET_PATH"; then
    log "ERROR: mv to $SECRET_PATH failed (filesystem boundary? quota?)"
    # cleanup_tmp trap will rm $TMP. No service change yet — exit 1, no rollback needed.
    exit 1
fi
TMP=""  # cleanup trap no longer needs to rm
log "wrote new secret (32 bytes hex)"

# 3. Restart cockpit; --wait blocks until ready (or fails). Rollback on failure.
if ! /usr/bin/systemctl restart --wait "$SERVICE"; then
    log "ERROR: systemctl restart --wait $SERVICE failed"
    if [ "$HAD_PRIOR_SECRET" -eq 1 ] && [ -f "$BAK" ]; then
        if mv -f "$BAK" "$SECRET_PATH" && \
           chown shift-agent:shift-agent "$SECRET_PATH" && \
           chmod 0600 "$SECRET_PATH"; then
            if /usr/bin/systemctl restart --wait "$SERVICE"; then
                log "rolled back to previous secret; service restored"
                exit 1
            else
                log "CRITICAL: rolled back secret BUT service still down — manual intervention"
                exit 2
            fi
        else
            log "CRITICAL: rollback file-write failed — manual intervention"
            exit 2
        fi
    else
        log "CRITICAL: no backup to roll back to (first rotation) — service down with new untested secret"
        exit 2
    fi
fi

# 4. Health probe
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
exit 3
