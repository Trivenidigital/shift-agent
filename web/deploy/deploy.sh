#!/usr/bin/env bash
# Deploy script for the Shift Agent Cockpit.
#
# Run from a developer machine:
#   bash web/deploy/deploy.sh main-vps
#
# Idempotent. Safe to re-run.
set -euo pipefail

VPS="${1:-main-vps}"

echo "==> rsync backend → $VPS:/opt/shift-agent/cockpit/backend"
rsync -az --delete --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'tests' \
    web/backend/ "$VPS:/opt/shift-agent/cockpit/backend/"

echo "==> Build + rsync frontend"
pushd web/frontend > /dev/null
npm ci --silent
npm run build --silent
popd > /dev/null
rsync -az --delete web/frontend/dist/ "$VPS:/opt/shift-agent/cockpit/static/"

echo "==> Install systemd unit + Caddy + logrotate"
scp web/deploy/shift-agent-cockpit.service "$VPS:/tmp/shift-agent-cockpit.service"
scp web/deploy/Caddyfile "$VPS:/tmp/cockpit-Caddyfile"
scp web/deploy/logrotate.conf "$VPS:/tmp/cockpit-logrotate"

ssh "$VPS" 'set -euo pipefail
    # Venv
    if [ ! -d /opt/shift-agent/cockpit/venv ]; then
        /usr/bin/python3 -m venv /opt/shift-agent/cockpit/venv
        /opt/shift-agent/cockpit/venv/bin/pip install -U pip
        /opt/shift-agent/cockpit/venv/bin/pip install -e /opt/shift-agent/cockpit/backend
    else
        /opt/shift-agent/cockpit/venv/bin/pip install -e /opt/shift-agent/cockpit/backend
    fi
    sudo chown -R shift-agent:shift-agent /opt/shift-agent/cockpit

    # Audit log: chattr +a tamper resistance
    sudo touch /opt/shift-agent/logs/cockpit-audit.log
    sudo chown shift-agent:shift-agent /opt/shift-agent/logs/cockpit-audit.log
    sudo chmod 0640 /opt/shift-agent/logs/cockpit-audit.log
    sudo chattr +a /opt/shift-agent/logs/cockpit-audit.log || true

    # Runtime dir for pair sessions (mode 0700, root-created)
    sudo install -d -o shift-agent -g shift-agent -m 0700 /run/shift-agent

    # Systemd
    sudo install -m 0644 /tmp/shift-agent-cockpit.service /etc/systemd/system/shift-agent-cockpit.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now shift-agent-cockpit

    # Logrotate
    sudo install -m 0644 /tmp/cockpit-logrotate /etc/logrotate.d/shift-agent-cockpit

    # Caddy (manual: edit hostname first if needed)
    sudo install -m 0644 /tmp/cockpit-Caddyfile /etc/caddy/Caddyfile.cockpit
    echo "Edit /etc/caddy/Caddyfile.cockpit and integrate into main Caddyfile, then: sudo systemctl reload caddy"

    sleep 3
    curl -sS http://127.0.0.1:8080/health | head
'

echo "==> Done. Hit https://cockpit.<your-domain>/ to log in (Pushover OTP)."
