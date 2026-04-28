#!/usr/bin/env bash
# migrate-env-to-symlink — one-time consolidation that makes
# /opt/shift-agent/.env a symlink to /root/.hermes/.env.
#
# Idempotent: safe to re-run. If already migrated, exits 0 with no changes.
# Backs up the existing /opt/shift-agent/.env to .pre-symlink-backup before
# replacing.
#
# Pre-conditions:
#   1. tools/check-env-drift.sh exits 0 (no drift on overlapping keys)
#   2. /root/.hermes/.env is the canonical file going forward
#
# Use case: run ONCE per VPS during the .env consolidation deploy. After this
# runs, both readers (Hermes' load_hermes_dotenv + shift-agent systemd
# EnvironmentFile) see the same content. Subsequent deploys verify the symlink
# integrity via shift-agent-deploy.sh's pre-flight gate.
#
# Exit codes:
#   0 — migration complete (or already done; idempotent)
#   1 — drift detected; aborted (re-run check-env-drift.sh, reconcile, retry)
#   2 — env files missing or invalid state
set -euo pipefail

HERMES_ENV=/root/.hermes/.env
SHIFT_ENV=/opt/shift-agent/.env
BACKUP=/opt/shift-agent/.env.pre-symlink-backup

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Idempotency check FIRST — if already migrated, exit clean.
if [ -L "$SHIFT_ENV" ] && [ "$(readlink "$SHIFT_ENV")" = "$HERMES_ENV" ]; then
    echo "OK: $SHIFT_ENV is already a symlink to $HERMES_ENV. Nothing to do."
    exit 0
fi

# Pre-flight 1: both files exist
[ -r "$HERMES_ENV" ] || { echo "FAIL: $HERMES_ENV missing or unreadable" >&2; exit 2; }
[ -f "$SHIFT_ENV" ]  || { echo "FAIL: $SHIFT_ENV missing or unreadable"  >&2; exit 2; }

# Pre-flight 2: drift check must pass. FAIL (not WARN) if missing — same
# "missing safety check is dangerous" policy applied to deploy.sh's pin gate.
# A migration that bypasses drift-detection is exactly the failure mode this
# work exists to prevent.
if [ ! -x "$SCRIPT_DIR/check-env-drift.sh" ]; then
    echo "FAIL: $SCRIPT_DIR/check-env-drift.sh missing or not executable." >&2
    echo "  Refusing to migrate without the drift gate." >&2
    exit 2
fi
echo "=== Pre-flight: drift check ==="
if ! "$SCRIPT_DIR/check-env-drift.sh"; then
    echo "FAIL: drift detected; aborting migration. Reconcile and retry." >&2
    exit 1
fi

# Migration step 1: copy any keys that exist ONLY in shift-agent .env into Hermes .env.
# Today these are COCKPIT_COOKIE_SECURE + KIMI_API_KEY but the script auto-detects
# rather than hard-coding so the migration handles future additions gracefully.
echo ""
echo "=== Adding shift-agent-only keys to $HERMES_ENV ==="

HERMES_KEYS=$(grep -oE "^[A-Z_][A-Z0-9_]*=" "$HERMES_ENV" | sed 's/=$//' | sort -u)
SHIFT_KEYS=$(grep -oE "^[A-Z_][A-Z0-9_]*=" "$SHIFT_ENV"  | sed 's/=$//' | sort -u)
ONLY_IN_SHIFT=$(comm -23 <(echo "$SHIFT_KEYS") <(echo "$HERMES_KEYS"))

if [ -z "$ONLY_IN_SHIFT" ]; then
    echo "  (no shift-agent-only keys to migrate)"
else
    # Append a section header so the operator can find migrated keys later.
    # Use printf for portability (some /bin/sh echo interprets backslashes).
    {
        printf '\n'
        printf '# Migrated from /opt/shift-agent/.env on %s by migrate-env-to-symlink.sh\n' "$(date -Iseconds)"
    } >> "$HERMES_ENV"
    for key in $ONLY_IN_SHIFT; do
        # Strip CRLF before append so Windows-line-ending source files don't
        # propagate \r into the canonical Hermes file (loaders may treat
        # KEY=value\r as KEY with literal \r in the value).
        line=$(grep "^${key}=" "$SHIFT_ENV" | tail -1 | tr -d '\r')
        printf '%s\n' "$line" >> "$HERMES_ENV"
        echo "  added: $key"
    done
fi

# Migration step 2: backup current shift-agent .env.
# If the predictable backup path already exists (e.g. partial-failure re-run
# scenario where migration crashed after backup but before symlink creation),
# don't overwrite — append a unix timestamp to the new backup. Preserves the
# original snapshot.
if [ -e "$BACKUP" ]; then
    BACKUP="${BACKUP}-$(date +%s)"
    echo "  (predictable backup path occupied; using timestamped path: $BACKUP)"
fi
echo ""
echo "=== Backing up $SHIFT_ENV → $BACKUP ==="
cp -p "$SHIFT_ENV" "$BACKUP"
ls -la "$BACKUP"

# Migration step 3: replace with symlink
echo ""
echo "=== Replacing $SHIFT_ENV with symlink → $HERMES_ENV ==="
rm "$SHIFT_ENV"
ln -s "$HERMES_ENV" "$SHIFT_ENV"
ls -la "$SHIFT_ENV"

# Verify symlink works for systemd's EnvironmentFile (which dereferences)
if ! [ -r "$SHIFT_ENV" ]; then
    echo "FAIL: symlink target unreadable after creation; rolling back" >&2
    rm "$SHIFT_ENV"
    cp -p "$BACKUP" "$SHIFT_ENV"
    exit 2
fi

cat <<EOF

=== MIGRATION COMPLETE ===

$SHIFT_ENV is now a symlink to $HERMES_ENV.
Backup of pre-migration shift-agent .env: $BACKUP

NEXT STEPS:
  1. Restart services that read either file:
       sudo systemctl restart hermes-gateway shift-agent-cockpit
  2. Run smoke checks (see docs/deploy.md "Verifying .env consolidation"):
       - Confirm "✓ whatsapp connected" in hermes-gateway journal within 30s
       - Confirm cockpit /api/health returns ok
       - Confirm no startup errors in journalctl since restart
  3. After 24h of clean operation, you can remove the backup:
       sudo rm $BACKUP

ROLLBACK (if anything goes wrong):
  sudo rm $SHIFT_ENV
  sudo cp $BACKUP $SHIFT_ENV
  sudo systemctl restart hermes-gateway shift-agent-cockpit
EOF
