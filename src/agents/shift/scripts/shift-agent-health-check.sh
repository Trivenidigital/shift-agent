#!/usr/bin/env bash
# shift-agent-health-check — periodic (5-min) health probe.
#
# Hardening applied (Priority 1 round):
#   - `jq` replaced with Python fallback (was silently skipping bridge-status check)
#   - pending.json stale check: explicit error propagation (was `|| echo 0` masking corruption)
#   - OpenRouter check: check HTTP 200 explicitly, not substring "data"
#   - healthchecks.io URL extraction via Python (not fragile grep|sed)
#
# Touches state/last-health-check-ts on success (watchdog reads).

set -euo pipefail

STATE_DIR=/opt/shift-agent/state
HEALTH_LOG=$STATE_DIR/health.log
LAST_TS_FILE=$STATE_DIR/last-health-check-ts
LAST_ALERT_FILE=$STATE_DIR/.last-alert-ts
CONFIG=/opt/shift-agent/config.yaml
ALERT_THROTTLE_SEC=1800  # at most one alert per 30min per check

mkdir -p "$STATE_DIR"

failures=()

# 1. Gateway active
if ! systemctl is-active --quiet hermes-gateway; then
    failures+=("hermes-gateway not active")
fi

# 2. Tail-logger timer active
if ! systemctl is-active --quiet shift-agent-tail-logger.timer; then
    failures+=("tail-logger timer not active")
fi

# 3. Bridge port
if ! ss -tln 2>/dev/null | grep -q ":3000 "; then
    failures+=("bridge port 3000 not listening")
fi

# 4. Bridge /health endpoint (Python-only; no jq dependency)
health_status=$(python3 -c "
import json, urllib.request, sys
try:
    with urllib.request.urlopen('http://127.0.0.1:3000/health', timeout=5) as resp:
        if resp.status != 200:
            print(f'http_{resp.status}')
            sys.exit(0)
        body = json.loads(resp.read().decode())
        print(body.get('status', 'missing_status_field'))
except Exception as e:
    print(f'err:{type(e).__name__}')
" 2>/dev/null)
if [ "$health_status" != "connected" ]; then
    failures+=("bridge /health status: ${health_status:-empty}")
fi

# 5. OpenRouter reachable (if key set) — HTTP-code check, not substring
if [ -f /opt/shift-agent/.env ]; then
    # shellcheck disable=SC1091
    OR_KEY=$(python3 -c "
import os, re
for line in open('/opt/shift-agent/.env'):
    line = line.strip()
    if line.startswith('OPENROUTER_API_KEY='):
        val = line.split('=', 1)[1]
        val = val.strip('\"').strip(\"'\")
        print(val)
        break
" 2>/dev/null)
    if [ -n "$OR_KEY" ] && [ "$OR_KEY" != "PLACEHOLDER_fill_me_in" ]; then
        http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
                        -H "Authorization: Bearer $OR_KEY" \
                        https://openrouter.ai/api/v1/auth/key 2>/dev/null || echo "000")
        if [ "$http_code" != "200" ]; then
            failures+=("OpenRouter /auth/key returned HTTP $http_code")
        fi
    fi
fi

# 6. Pending proposals aging past TTL — explicit error propagation
if [ -f /opt/shift-agent/state/pending.json ]; then
    stale_result=$(python3 <<'PY'
import json, sys, datetime
from datetime import timezone
try:
    with open("/opt/shift-agent/state/pending.json") as f:
        store = json.load(f)
    now = datetime.datetime.now(timezone.utc)
    stale = 0
    for p in store.get("proposals", {}).values():
        if p.get("status") == "awaiting_owner_approval":
            ts_str = p.get("last_updated_ts", "")
            if not ts_str:
                continue
            try:
                ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now - ts).total_seconds() > 4 * 3600:
                stale += 1
    print(stale)
except Exception as e:
    print(f"ERROR:{type(e).__name__}:{e}")
PY
)
    if [[ "$stale_result" == ERROR:* ]]; then
        failures+=("pending.json check raised: ${stale_result#ERROR:}")
    elif [ "$stale_result" -gt 0 ] 2>/dev/null; then
        failures+=("$stale_result pending proposals aged past TTL (4h)")
    fi
fi

# 7. Disk free
avail_kb=$(df /opt | tail -1 | awk '{print $4}')
if [ "$avail_kb" -lt 5242880 ]; then
    failures+=("disk free on /opt below 5GB: ${avail_kb}KB")
fi

# ── Report ──
now_ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
if [ ${#failures[@]} -eq 0 ]; then
    echo "$now_ts OK" >> "$HEALTH_LOG"
    date -u +%s > "$LAST_TS_FILE"
    # Ping healthchecks.io if configured (via Python, not grep|sed)
    hc_url=$(python3 -c "
import yaml
try:
    with open('$CONFIG') as f:
        cfg = yaml.safe_load(f) or {}
    print(cfg.get('alerting', {}).get('healthchecks_io_url', ''))
except Exception:
    print('')
" 2>/dev/null)
    if [ -n "$hc_url" ]; then
        curl -s --max-time 10 "$hc_url" >/dev/null 2>&1 || true
    fi
    exit 0
fi

# Log failures
summary=$(printf '%s; ' "${failures[@]}")
echo "$now_ts FAIL: $summary" >> "$HEALTH_LOG"

# Throttle alerts
now_s=$(date +%s)
last_alert_s=0
[ -f "$LAST_ALERT_FILE" ] && last_alert_s=$(cat "$LAST_ALERT_FILE" 2>/dev/null || echo 0)
if [ $((now_s - last_alert_s)) -ge $ALERT_THROTTLE_SEC ]; then
    /usr/local/bin/shift-agent-notify-owner \
        --title "Agent health issues" \
        --priority 1 \
        "Shift Agent unhealthy: $summary. Check with 'systemctl status hermes-gateway' and 'journalctl -u hermes-gateway -f'." \
        || true  # Pushover itself down — append_notify_failed already captures
    echo "$now_s" > "$LAST_ALERT_FILE"
fi

# Also append HealthCheckFailure entry to decisions.log via typed helper
# (Python-constructed JSON to avoid bash-heredoc injection hazards)
python3 <<PY || true
import json, subprocess, datetime
from datetime import timezone
entry = {
    "type": "health_check_failure",
    "ts": datetime.datetime.now(timezone.utc).isoformat(),
    "check": "composite",
    "detail": """$summary"""[:500],
}
subprocess.run(
    ["/usr/local/bin/log-decision-direct", json.dumps(entry)],
    check=False, timeout=10,
)
PY

exit 1
