#!/usr/bin/env bash
# shift-agent-backup — nightly gpg-encrypted backup.
# Stops tail-logger timer during tar to avoid partial-line snapshots.
# Copies baileys_auth to /tmp first to avoid tarring a live session.

set -euo pipefail

CONFIG=/opt/shift-agent/config.yaml
BACKUP_DIR=/opt/shift-agent/backups
STAMP=$(date +%F-%H%M)
TAR_PATH=$BACKUP_DIR/$STAMP.tar.gz
GPG_PATH=$TAR_PATH.gpg
SESSION_COPY=/tmp/shift-session-$$

mkdir -p "$BACKUP_DIR"

# Extract settings from config.yaml
GPG_RECIPIENT=$(grep -E "^\s*gpg_recipient_email:" "$CONFIG" | sed 's/^[^:]*:\s*//; s/^"//; s/"$//; s/^'"'"'//; s/'"'"'$//')
RETENTION_DAYS=$(grep -E "^\s*retention_days:" "$CONFIG" | awk '{print $2}')
S3_BUCKET=$(grep -E "^\s*s3_bucket:" "$CONFIG" | sed 's/^[^:]*:\s*//; s/^"//; s/"$//' || echo "")
: "${RETENTION_DAYS:=30}"

if [ -z "${GPG_RECIPIENT:-}" ] || [ "$GPG_RECIPIENT" = '""' ]; then
    echo "ERROR: gpg_recipient_email not configured — cannot encrypt backup" >&2
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup misconfigured" \
        --priority 1 \
        "Nightly backup skipped: gpg_recipient_email not set in config.yaml" || true
    exit 1
fi

# Cleanup on any exit
cleanup() {
    rm -rf "$SESSION_COPY" "$TAR_PATH"
    # Always try to restart the tail-logger timer even on failure
    systemctl start shift-agent-tail-logger.timer 2>/dev/null || true
}
trap cleanup EXIT

# Stop tail-logger timer for consistency
systemctl stop shift-agent-tail-logger.timer || true
sleep 2  # let any in-flight run finish

# Snapshot baileys_auth (live session) to /tmp
if [ -d /root/.hermes/whatsapp/session ]; then
    cp -a /root/.hermes/whatsapp/session "$SESSION_COPY"
else
    mkdir -p "$SESSION_COPY"  # empty placeholder
fi

# Tar the snapshot + agent state
if ! tar czf "$TAR_PATH" \
        -C / \
        opt/shift-agent/config.yaml \
        opt/shift-agent/roster.json \
        opt/shift-agent/state \
        opt/shift-agent/logs \
        -C /tmp "shift-session-$$" 2>/tmp/backup-tar-errors-$$; then
    echo "ERROR: tar failed:" >&2
    cat /tmp/backup-tar-errors-$$ >&2
    rm -f /tmp/backup-tar-errors-$$
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup FAILED (tar)" \
        --priority 2 \
        "Nightly backup tar failed. Check /tmp/backup-tar-errors for details." || true
    exit 1
fi
rm -f /tmp/backup-tar-errors-$$

# Verify expected files are in the archive
required_count=$(tar -tzf "$TAR_PATH" 2>/dev/null | grep -c "opt/shift-agent/config.yaml\|opt/shift-agent/roster.json\|opt/shift-agent/state\|opt/shift-agent/logs" || echo 0)
if [ "$required_count" -lt 4 ]; then
    echo "ERROR: backup tar missing required files" >&2
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup FAILED (incomplete)" \
        --priority 2 \
        "Nightly backup archive is missing required files. Inspect $TAR_PATH before it's deleted." || true
    exit 1
fi

# Encrypt via GPG pubkey
if ! gpg --batch --yes --trust-model always --recipient "$GPG_RECIPIENT" \
        --output "$GPG_PATH" --encrypt "$TAR_PATH"; then
    echo "ERROR: gpg encrypt failed" >&2
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup FAILED (gpg)" \
        --priority 2 \
        "Nightly backup gpg encryption failed. Check that $GPG_RECIPIENT key is imported: 'gpg --list-keys'." || true
    exit 1
fi

# Round-trip test: can we decrypt back? (only works if recipient's private key is ON this host — NOT recommended; so skip test if not possible)
# We accept this trade-off: pubkey encryption means the private key is OFF the VPS, so we can't round-trip-test here.
# Instead: verify the .gpg file is non-trivial in size.
gpg_size=$(stat -c%s "$GPG_PATH")
if [ "$gpg_size" -lt 1024 ]; then
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup suspicious (tiny)" \
        --priority 2 \
        "Nightly backup gpg file is only ${gpg_size} bytes — suspicious. Verify manually." || true
    exit 1
fi

# Delete plaintext
rm -f "$TAR_PATH"

# Optional S3 sync
if [ -n "${S3_BUCKET:-}" ] && [ "$S3_BUCKET" != '""' ] && command -v aws >/dev/null 2>&1; then
    aws s3 sync "$BACKUP_DIR" "s3://$S3_BUCKET/shift-agent-backups/" \
        --exclude "*" --include "*.tar.gz.gpg" 2>/dev/null || {
        /usr/local/bin/shift-agent-notify-owner \
            --title "S3 sync failed" \
            --priority 0 \
            "Backup succeeded locally but S3 sync failed. Local copy is intact." || true
    }
fi

# Retention
find "$BACKUP_DIR" -name "*.tar.gz.gpg" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true

echo "Backup OK: $GPG_PATH ($gpg_size bytes)"
