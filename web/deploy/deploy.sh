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
scp web/deploy/rotate-jwt-secret.sh "$VPS:/tmp/rotate-jwt-secret.sh"
scp web/deploy/jwt-rotate.cron "$VPS:/tmp/jwt-rotate.cron"

ssh "$VPS" 'set -euo pipefail
    # OS deps required by health-check + cockpit (idempotent — apt-get install is a no-op if already present)
    sudo apt-get update -qq
    sudo apt-get install -y -qq jq    # required by shift-agent-health-check.sh fallback
    sudo apt-get install -y -qq e2fsprogs  # for chattr / logrotate

    # ─── Pre-merge JWT secret length sanity check ───
    # Refuses to deploy if /opt/shift-agent/.env's COCKPIT_JWT_SECRET is set but
    # too short (would brick the cockpit on first restart with the new validator).
    # Skips if env var is missing — get_settings() will auto-generate.
    if sudo grep -q "^COCKPIT_JWT_SECRET=" /opt/shift-agent/.env 2>/dev/null; then
        SECRET_LEN=$(sudo grep "^COCKPIT_JWT_SECRET=" /opt/shift-agent/.env | head -1 | cut -d= -f2- | tr -d '"\047' | wc -c)
        # wc -c counts trailing newline; subtract 1
        SECRET_LEN=$((SECRET_LEN - 1))
        if [ "$SECRET_LEN" -gt 0 ] && [ "$SECRET_LEN" -lt 64 ]; then
            echo "ABORT: COCKPIT_JWT_SECRET is $SECRET_LEN chars; cockpit validator requires >= 64 hex chars."
            echo "  Either remove the line (auto-generated) or replace with output of: python3 -c '\''import secrets; print(secrets.token_hex(32))'\''"
            exit 1
        fi
    fi

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

    # Sudoers rule: allow shift-agent to systemctl stop/start hermes-gateway (re-pair flow)
    # without a password, while keeping rest of root locked down.
    if ! sudo grep -q "shift-agent.*hermes-gateway" /etc/sudoers.d/shift-agent 2>/dev/null; then
        echo "shift-agent ALL=(root) NOPASSWD: /usr/bin/systemctl stop hermes-gateway, /usr/bin/systemctl start hermes-gateway, /usr/bin/systemctl restart hermes-gateway" | \
            sudo tee /etc/sudoers.d/shift-agent > /dev/null
        sudo chmod 0440 /etc/sudoers.d/shift-agent
        sudo visudo -c -f /etc/sudoers.d/shift-agent
    fi

    # Systemd
    sudo install -m 0644 /tmp/shift-agent-cockpit.service /etc/systemd/system/shift-agent-cockpit.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now shift-agent-cockpit

    # Logrotate
    sudo install -m 0644 /tmp/cockpit-logrotate /etc/logrotate.d/shift-agent-cockpit

    # JWT rotation script + monthly cron (idempotent)
    sudo install -m 0755 -o root -g root /tmp/rotate-jwt-secret.sh /opt/shift-agent/cockpit/rotate-jwt-secret.sh
    sudo install -m 0644 -o root -g root /tmp/jwt-rotate.cron /etc/cron.d/shift-agent-jwt-rotate

    # Caddy (manual: edit hostname first if needed)
    sudo install -m 0644 /tmp/cockpit-Caddyfile /etc/caddy/Caddyfile.cockpit
    echo "Edit /etc/caddy/Caddyfile.cockpit and integrate into main Caddyfile, then: sudo systemctl reload caddy"

    sleep 3
    curl -sS http://127.0.0.1:8080/health | head
'

echo "==> Done. Hit https://cockpit.<your-domain>/ to log in (Pushover OTP)."
