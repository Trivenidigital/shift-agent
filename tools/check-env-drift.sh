#!/usr/bin/env bash
# check-env-drift — verify overlapping keys in /root/.hermes/.env and
# /opt/shift-agent/.env have identical values, before consolidating to a
# symlink. Prints sha256 hash + length per drifted key (no secrets to
# stdout/log) so the operator can identify which file is canonical.
#
# Use case: run BEFORE migrate-env-to-symlink.sh. If drift detected, the
# operator reconciles manually, then re-runs this until clean (exit 0).
# Once the symlink is in place this script becomes redundant — the files
# are tautologically the same.
#
# Exit codes:
#   0 — no drift (safe to proceed with migration)
#   1 — drift detected (operator must reconcile)
#   2 — one or both env files missing
set -euo pipefail

HERMES_ENV=/root/.hermes/.env
SHIFT_ENV=/opt/shift-agent/.env

[ -r "$HERMES_ENV" ] || { echo "FAIL: $HERMES_ENV missing or unreadable" >&2; exit 2; }
[ -r "$SHIFT_ENV" ]  || { echo "FAIL: $SHIFT_ENV missing or unreadable"  >&2; exit 2; }

# Already a symlink? Drift impossible by definition; nothing to check.
if [ -L "$SHIFT_ENV" ] && [ "$(readlink "$SHIFT_ENV")" = "$HERMES_ENV" ]; then
    echo "OK: $SHIFT_ENV is already a symlink to $HERMES_ENV — files are tautologically in sync."
    exit 0
fi

# Extract values for a key (exact match, last assignment wins). Normalizes:
#   - trailing whitespace including \r (CRLF tolerance)
#   - surrounding double or single quotes (Hermes' Python dotenv loader and
#     shell export both strip these, so KEY="foo" and KEY=foo are semantically
#     identical — comparing them as drift would false-positive on operators
#     who quote one file and not the other)
_value_of() {
    local file="$1" key="$2"
    grep "^${key}=" "$file" 2>/dev/null \
        | tail -1 \
        | cut -d= -f2- \
        | tr -d '\r' \
        | sed 's/[[:space:]]\+$//' \
        | sed -E 's/^"(.*)"$/\1/; s/^'\''(.*)'\''$/\1/'
}

# All keys present in either file (union)
HERMES_KEYS=$(grep -oE "^[A-Z_][A-Z0-9_]*=" "$HERMES_ENV" 2>/dev/null | sed 's/=$//' | sort -u)
SHIFT_KEYS=$(grep -oE "^[A-Z_][A-Z0-9_]*=" "$SHIFT_ENV"  2>/dev/null | sed 's/=$//' | sort -u)
COMMON_KEYS=$(comm -12 <(echo "$HERMES_KEYS") <(echo "$SHIFT_KEYS"))

DRIFT_COUNT=0
echo "=== Comparing overlapping keys (in BOTH files) ==="
echo ""

for key in $COMMON_KEYS; do
    h_val=$(_value_of "$HERMES_ENV" "$key")
    s_val=$(_value_of "$SHIFT_ENV"  "$key")

    if [ "$h_val" = "$s_val" ]; then
        : # match — no output (silent on agreement keeps the report short)
    else
        DRIFT_COUNT=$((DRIFT_COUNT + 1))
        h_hash=$(printf '%s' "$h_val" | sha256sum | cut -c1-12)
        s_hash=$(printf '%s' "$s_val" | sha256sum | cut -c1-12)
        h_len=${#h_val}
        s_len=${#s_val}
        echo "DRIFT DETECTED on key: $key"
        printf '  %-30s sha256:%s... (length %d)\n' "$HERMES_ENV value:" "$h_hash" "$h_len"
        printf '  %-30s sha256:%s... (length %d)\n' "$SHIFT_ENV value:"  "$s_hash" "$s_len"
        echo ""
    fi
done

if [ "$DRIFT_COUNT" -eq 0 ]; then
    N=$(echo "$COMMON_KEYS" | wc -w)
    echo "OK: all $N overlapping keys match. Safe to proceed with migrate-env-to-symlink.sh."
    exit 0
fi

N=$(echo "$COMMON_KEYS" | wc -w)
cat >&2 <<EOF

ACTION REQUIRED: $DRIFT_COUNT of $N overlapping key(s) have drifted between the
two .env files. Reconcile manually before running migrate-env-to-symlink.sh.
Decide which file is canonical:

  - If $HERMES_ENV is correct, copy values to $SHIFT_ENV
  - If $SHIFT_ENV is correct, copy values to $HERMES_ENV

Then re-run this script. Hash format above identifies values by length +
prefix without leaking secrets — placeholder values are usually visibly
shorter than real ones.
EOF
exit 1
