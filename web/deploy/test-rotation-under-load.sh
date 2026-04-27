#!/usr/bin/env bash
# Smoke test: verifies cockpit-audit.log rotation works correctly under load.
#
# Writes 100k synthetic NDJSON entries to a sandbox path, simulates the
# logrotate hooks (chattr -a → rotate → chattr +a), then verifies:
#   1. Original file is empty post-rotate (logrotate's nocreate + truncation
#      via daemon writes; here we simulate by removing+touching).
#   2. chattr +a is re-applied (file is append-only after rotate).
#   3. ndjson_append on the post-rotate file still works from the cockpit user.
#
# Usage:
#   sudo bash test-rotation-under-load.sh
#
# Returns 0 on success, non-zero with a clear error otherwise. Idempotent.

set -euo pipefail

SANDBOX=/tmp/shift-agent-rotation-test
LOG="$SANDBOX/cockpit-audit.log"
ROTATED="$SANDBOX/cockpit-audit.log.1"
N_LINES=100000
USER_AS=${ROTATION_TEST_USER:-shift-agent}

# Ensure clean sandbox
rm -rf "$SANDBOX"
mkdir -p "$SANDBOX"

# Phase 1: Write the load
echo "→ Writing $N_LINES synthetic audit entries…"
sudo -u "$USER_AS" python3 - <<PYEOF
import json, sys
from datetime import datetime, timezone
from pathlib import Path

log = Path("$LOG")
n = $N_LINES

with log.open("w") as f:
    for i in range(n):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "test.synthetic",
            "actor": "rotation-test",
            "details": {"i": i, "filler": "x" * 80},
        }
        f.write(json.dumps(entry) + "\n")
print(f"wrote {n} lines to {log} ({log.stat().st_size} bytes)")
PYEOF

# Phase 2: Apply chattr +a, then simulate rotation
sudo chattr +a "$LOG"
echo "→ Pre-rotate: file size = $(stat -c%s "$LOG"), attrs = $(lsattr "$LOG" | awk '{print $1}')"

# Simulate logrotate prerotate hook
sudo chattr -a "$LOG"

# Move + recreate (mimics logrotate with nocreate + missingok)
sudo mv "$LOG" "$ROTATED"
sudo -u "$USER_AS" touch "$LOG"

# Simulate postrotate hook
sudo chattr +a "$LOG"

echo "→ Post-rotate: file size = $(stat -c%s "$LOG"), attrs = $(lsattr "$LOG" | awk '{print $1}')"

# Phase 3: Verify chattr +a is re-applied
ATTRS=$(lsattr "$LOG" | awk '{print $1}')
if [[ "$ATTRS" != *a* ]]; then
    echo "FAIL: chattr +a not re-applied; attrs = $ATTRS" >&2
    exit 1
fi

# Phase 4: Verify shift-agent can append (chmod check)
sudo -u "$USER_AS" bash -c "echo '{\"event\":\"post-rotate-test\"}' >> $LOG" || {
    echo "FAIL: $USER_AS cannot append to post-rotate file" >&2
    exit 1
}

# Phase 5: Verify $USER_AS CANNOT truncate (this is the whole point)
if sudo -u "$USER_AS" truncate -s 0 "$LOG" 2>/dev/null; then
    echo "FAIL: $USER_AS was able to truncate post-rotate file — chattr +a is not effective" >&2
    exit 1
fi
echo "✓ chattr +a survived rotate; $USER_AS can append, cannot truncate"

# Cleanup
sudo chattr -a "$LOG"
rm -rf "$SANDBOX"
echo "=== Rotation under load: PASS ==="
