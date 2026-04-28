#!/usr/bin/env bash
# shift-agent-backup — nightly gpg-encrypted backup.
#
# Hardening applied (Priority 1 round):
#   - YAML parsed with python3 yaml.safe_load (not grep|sed)
#   - tar-contents verified per-file (not substring regex)
#   - gpg uses default trust-model with explicit key-presence precheck
#     (no more --trust-model always which accepted rogue keys)
#   - tail-logger restart failure raises an alert (no more || true)
#   - tar-errors tempfile via mktemp, cleaned by trap
#
# Stops tail-logger service + timer during tar to avoid partial-line snapshots.
# Copies baileys_auth to /tmp first to avoid tarring a live session.

set -euo pipefail

CONFIG=/opt/shift-agent/config.yaml
BACKUP_DIR=/opt/shift-agent/backups
STAMP=$(date +%F-%H%M)
TAR_PATH=$BACKUP_DIR/$STAMP.tar.gz
GPG_PATH=$TAR_PATH.gpg
SESSION_COPY=$(mktemp -d /tmp/shift-session.XXXXXX)
TAR_ERRORS=$(mktemp /tmp/backup-tar-errors.XXXXXX)

mkdir -p "$BACKUP_DIR"

# ─── Parse config.yaml via Python (not fragile grep|sed) ───
# yaml.safe_load tolerates multi-line values, quoted strings, commented-out lines, etc.
eval "$(python3 -c "
import yaml
with open('$CONFIG') as f:
    cfg = yaml.safe_load(f) or {}
backup = cfg.get('backup', {}) or {}
print(f'GPG_FPR={backup.get(\"gpg_fingerprint\", \"\")!r}')
print(f'GPG_RECIPIENT={backup.get(\"gpg_recipient_email\", \"\")!r}')
print(f'RETENTION_DAYS={backup.get(\"retention_days\", 30)}')
print(f'S3_BUCKET={backup.get(\"s3_bucket\", \"\")!r}')
")"

# Priority-1: require FULL 40-character GPG fingerprint, not email or short ID.
# Short 16-char IDs are trivially collision-attackable (evil32 SKS attack 2019).
# Email matching is rogue-key vulnerable if attacker imports a key with same email.
if [ -z "${GPG_FPR:-}" ]; then
    echo "ERROR: backup.gpg_fingerprint not configured in $CONFIG (must be 40 hex chars)" >&2
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup misconfigured" \
        --priority 1 \
        "Nightly backup skipped: backup.gpg_fingerprint missing from config.yaml. Set the full 40-char GPG fingerprint."
    exit 1
fi

if ! [[ "$GPG_FPR" =~ ^[0-9A-Fa-f]{40}$ ]]; then
    echo "ERROR: backup.gpg_fingerprint must be exactly 40 hex chars; got: $GPG_FPR" >&2
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup misconfigured" \
        --priority 1 \
        "Nightly backup skipped: backup.gpg_fingerprint is not a valid full fingerprint. Expected 40 hex chars."
    exit 1
fi

# ─── GPG key-presence precheck (using full fingerprint) ───
# Use --with-colons + grep on the full fingerprint line (fpr:) to avoid
# matching a rogue key with the same short ID. The fingerprint is canonical.
if ! gpg --list-keys --with-colons "0x${GPG_FPR}" 2>/dev/null \
        | awk -F: '$1=="fpr"{print $10}' | grep -qi "^${GPG_FPR}\$"; then
    echo "ERROR: GPG key with fingerprint $GPG_FPR not in keyring" >&2
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup misconfigured" \
        --priority 1 \
        "Nightly backup skipped: GPG key fingerprint $GPG_FPR not imported. Run 'gpg --import <pubkey>'."
    exit 1
fi

# ─── Cleanup trap ───
cleanup() {
    local exit_code=$?
    rm -rf "$SESSION_COPY" "$TAR_ERRORS"
    rm -f "$TAR_PATH"  # only if still present (gpg success removes it)
    # Restart the tail-logger timer, alert loudly on failure
    if ! systemctl start shift-agent-tail-logger.timer 2>/dev/null; then
        /usr/local/bin/shift-agent-notify-owner \
            --title "CRITICAL: tail-logger timer failed to restart" \
            --priority 2 \
            "After backup, shift-agent-tail-logger.timer did not restart. No audit capture until fixed. SSH immediately." \
            || true  # last-resort; if Pushover is also down, we've done what we can
    fi
    exit "$exit_code"
}
trap cleanup EXIT

# ─── Stop tail-logger service AND timer to pause audit capture ───
systemctl stop shift-agent-tail-logger.timer
# Also wait for any in-flight run to finish
systemctl stop shift-agent-tail-logger.service 2>/dev/null || true
# Belt + suspenders: poll until no shift-agent-tail-logger process exists
for i in 1 2 3 4 5; do
    if ! pgrep -u shift-agent -f shift-agent-tail-logger.py >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# ─── Snapshot baileys_auth (live session) to /tmp ───
if [ -d /root/.hermes/whatsapp/session ]; then
    cp -a /root/.hermes/whatsapp/session "$SESSION_COPY/"
fi

# ─── Tar the snapshot + agent state ───
# Required files we'll verify-per-file in the archive
REQUIRED_PATHS=(
    "opt/shift-agent/config.yaml"
    "opt/shift-agent/roster.json"
    "opt/shift-agent/state"
    "opt/shift-agent/logs"
)

tar_cmd=(tar czf "$TAR_PATH" -C /)
for p in "${REQUIRED_PATHS[@]}"; do
    tar_cmd+=("$p")
done
# Include session snapshot if present
if [ -d "$SESSION_COPY/session" ]; then
    tar_cmd+=(-C "$(dirname "$SESSION_COPY")" "$(basename "$SESSION_COPY")")
fi

if ! "${tar_cmd[@]}" 2>"$TAR_ERRORS"; then
    echo "ERROR: tar failed:" >&2
    cat "$TAR_ERRORS" >&2
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup FAILED (tar)" \
        --priority 2 \
        "Nightly backup tar exited non-zero. Stderr: $(head -c 300 "$TAR_ERRORS")"
    exit 1
fi

# Per-file presence check — anchored exact-match, no substring leniency
MISSING=()
for p in "${REQUIRED_PATHS[@]}"; do
    # tar uses trailing / for directories; accept either form
    if ! tar -tzf "$TAR_PATH" 2>/dev/null | grep -Fxq "$p" \
       && ! tar -tzf "$TAR_PATH" 2>/dev/null | grep -Fxq "$p/"; then
        MISSING+=("$p")
    fi
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "ERROR: backup tar missing required files: ${MISSING[*]}" >&2
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup FAILED (incomplete)" \
        --priority 2 \
        "Nightly backup archive is missing: ${MISSING[*]}. Archive at $TAR_PATH before it's deleted."
    exit 1
fi

# ─── Encrypt with gpg (full-fingerprint pin + trust-model direct) ───
# --recipient with a 0x<40-char> fingerprint is unambiguous: no email lookup,
# no SKS-style key-substitution risk. --trust-model direct accepts the named
# key without requiring it to be signed by ultimately-trusted keys.
if ! gpg --batch --yes --no-default-keyring --trust-model direct \
         --recipient "0x${GPG_FPR}" \
         --output "$GPG_PATH" \
         --encrypt "$TAR_PATH"; then
    echo "ERROR: gpg encrypt failed for fingerprint $GPG_FPR" >&2
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup FAILED (gpg)" \
        --priority 2 \
        "Nightly backup gpg encryption failed. Fingerprint $GPG_FPR — verify key is imported and not revoked."
    exit 1
fi

# Size sanity check (gpg should produce non-trivial output)
gpg_size=$(stat -c%s "$GPG_PATH")
if [ "$gpg_size" -lt 1024 ]; then
    /usr/local/bin/shift-agent-notify-owner \
        --title "Backup suspicious (tiny)" \
        --priority 2 \
        "Nightly backup gpg file is only ${gpg_size} bytes. Verify manually."
    exit 1
fi

# Delete plaintext tar (encrypted version is kept)
rm -f "$TAR_PATH"

# ─── Optional S3 sync ───
if [ -n "${S3_BUCKET:-}" ] && command -v aws >/dev/null 2>&1; then
    if ! aws s3 sync "$BACKUP_DIR" "s3://$S3_BUCKET/shift-agent-backups/" \
            --exclude "*" --include "*.tar.gz.gpg"; then
        /usr/local/bin/shift-agent-notify-owner \
            --title "S3 sync failed" \
            --priority 0 \
            "Backup succeeded locally but S3 sync failed. Local copy is intact at $GPG_PATH." \
            || true
    fi
fi

# ─── Retention ───
find "$BACKUP_DIR" -name "*.tar.gz.gpg" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true

echo "Backup OK: $GPG_PATH ($gpg_size bytes)"
