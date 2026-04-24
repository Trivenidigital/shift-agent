#!/usr/bin/env bash
# shift-agent-health-check — periodic (5-min) health probe.
# Verifies gateway, bridge, OpenRouter, pending-proposal aging, disk, and ping healthchecks.io on success.
# On ANY failure: Pushover alert via shift-agent-notify-owner.
# Touches state/last-health-check-ts on success (watchdog reads).

set -euo pipefail

STATE_DIR=/opt/shift-agent/state
HEALTH_LOG=$STATE_DIR/health.log
LAST_TS_FILE=$STATE_DIR/last-health-check-ts
LAST_ALERT_FILE=$STATE_DIR/.last-alert-ts
CONFIG=/opt/shift-agent/config.yaml
ALERT_THROTTLE_SEC=1800  # don't spam: at most one alert per 30min per check

mkdir -p "$STATE_DIR"

failures=()
info=()

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

# 4. Bridge /health endpoint
if ! health_json=$(curl -s --max-time 5 http://127.0.0.1:3000/health 2>/dev/null); then
    failures+=("bridge /health unreachable")
else
    # Parse status if jq available
    if command -v jq &>/dev/null; then
        status=$(echo "$health_json" | jq -r '.status // empty' 2>/dev/null || echo "")
        if [ "$status" != "connected" ]; then
            failures+=("bridge status: ${status:-unknown}")
        fi
    fi
fi

# 5. OpenRouter reachable (if key set)
if [ -f /opt/shift-agent/.env ]; then
    OR_KEY=$(grep -E "^OPENROUTER_API_KEY=" /opt/shift-agent/.env | cut -d= -f2- | tr -d '"'"'"' ' || true)
    if [ -n "$OR_KEY" ]; then
        if ! curl -s --max-time 10 -H "Authorization: Bearer $OR_KEY" \
               https://openrouter.ai/api/v1/auth/key | grep -q '"data"'; then
            failures+=("OpenRouter unreachable or key invalid")
        fi
    fi
fi

# 6. Pending proposals aging past TTL
if [ -f /opt/shift-agent/state/pending.json ]; then
    stale=$(python3 <<'PY' 2>/dev/null || echo 0
import json, sys, datetime
from datetime import timezone
try:
    with open("/opt/shift-agent/state/pending.json") as f:
        store = json.load(f)
    now = datetime.datetime.now(timezone.utc)
    stale = 0
    for p in store.get("proposals", {}).values():
        if p.get("status") == "awaiting_owner_approval":
            ts = datetime.datetime.fromisoformat(p.get("last_updated_ts").replace("Z","+00:00"))
            if (now - ts).total_seconds() > 4 * 3600:
                stale += 1
    print(stale)
except Exception:
    print(0)
PY
)
    if [ "$stale" -gt 0 ]; then
        failures+=("$stale pending proposals aged past TTL (4h)")
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
    # Ping healthchecks.io if configured
    if [ -f "$CONFIG" ]; then
        hc_url=$(grep -E "^\s*healthchecks_io_url:" "$CONFIG" | cut -d: -f2- | tr -d '"'"'"' ' || true)
        if [ -n "$hc_url" ] && [ "$hc_url" != '""' ]; then
            curl -s --max-time 10 "$hc_url" >/dev/null 2>&1 || true
        fi
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
    || true
    echo "$now_s" > "$LAST_ALERT_FILE"
fi

# Also append an InvariantViolation-style entry to decisions.log for audit
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
/usr/local/bin/log-decision-direct "$(cat <<JSON
{"type":"health_check_failure","ts":"$TS","check":"composite","detail":"$(echo "$summary" | tr '"' "'")"}
JSON
)" || true

exit 1
