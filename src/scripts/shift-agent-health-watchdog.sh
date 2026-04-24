#!/usr/bin/env bash
# shift-agent-health-watchdog — second-tier watchdog.
# Runs every 15min. Alerts if the main health-check itself has stopped updating its timestamp.

set -euo pipefail

LAST_TS_FILE=/opt/shift-agent/state/last-health-check-ts
WATCHDOG_ALERT_FILE=/opt/shift-agent/state/.watchdog-last-alert-ts
STALE_THRESHOLD_SEC=900   # 15min — if health-check hasn't updated in this long, alert
ALERT_THROTTLE_SEC=3600   # one alert per hour max

now_s=$(date +%s)

if [ ! -f "$LAST_TS_FILE" ]; then
    # First run scenario — don't alert; let health-check initialize
    exit 0
fi

last_s=$(cat "$LAST_TS_FILE" 2>/dev/null || echo 0)
age=$((now_s - last_s))

if [ "$age" -lt "$STALE_THRESHOLD_SEC" ]; then
    exit 0
fi

# Throttle
last_alert_s=0
[ -f "$WATCHDOG_ALERT_FILE" ] && last_alert_s=$(cat "$WATCHDOG_ALERT_FILE" 2>/dev/null || echo 0)
if [ $((now_s - last_alert_s)) -lt $ALERT_THROTTLE_SEC ]; then
    exit 0
fi

/usr/local/bin/shift-agent-notify-owner \
    --title "Health check itself has stopped" \
    --priority 2 \
    "Watchdog: the Shift Agent health-check hasn't run in ${age}s (threshold ${STALE_THRESHOLD_SEC}s). The agent may be in a degraded state without alerting. SSH to the VPS and run 'systemctl status shift-agent-health.timer'." \
|| true
echo "$now_s" > "$WATCHDOG_ALERT_FILE"
