#!/usr/bin/env bash
# shift-agent-deploy — git-tagged deploy with auto-rollback on smoke-test failure.
#
# Usage:
#   shift-agent-deploy                  # deploy current branch HEAD
#   shift-agent-deploy rollback <tag>   # revert to a prior tagged deploy

set -euo pipefail

ACTION="${1:-deploy}"
REPO_DIR=/opt/shift-agent/.git-repo    # bare clone for deploy; separate from /opt/shift-agent working files
WORKING_COPY=/opt/shift-agent/working
TAG_PREFIX="deploy-"

install_artifacts() {
    cd "$WORKING_COPY"
    # Scripts: platform shared (identify-sender, validate-sender-block, log-decision*)
    # + Shift-Agent-specific (shift-agent-*, send-coverage-message, etc.).
    # Both land flat in /usr/local/bin/ — systemd ExecStart paths unchanged.
    install -m 755 src/platform/scripts/* /usr/local/bin/
    install -m 755 src/agents/shift/scripts/* /usr/local/bin/
    # Python modules — schemas + platform/{safe_io, sender_context, exit_codes}.
    # VPS layout stays flat at /opt/shift-agent/ so scripts' sys.path inserts
    # don't change. Repo layout is now src/platform/ for shared modules.
    install -m 644 src/platform/schemas.py /opt/shift-agent/schemas.py
    install -m 644 src/platform/safe_io.py /opt/shift-agent/safe_io.py
    install -m 644 src/platform/sender_context.py /opt/shift-agent/sender_context.py
    install -m 644 src/platform/exit_codes.py /opt/shift-agent/exit_codes.py
    install -m 644 src/platform/log_source.py /opt/shift-agent/log_source.py
    # Templates — Shift-Agent message templates
    install -d /opt/shift-agent/templates
    install -m 644 src/agents/shift/templates/* /opt/shift-agent/templates/
    # Skills → Hermes — Shift-Agent SKILL files
    rsync -a --delete src/agents/shift/skills/ /root/.hermes/skills/
    chown -R shift-agent:shift-agent /root/.hermes/skills/
    # systemd units — platform (hermes-gateway) + shift-agent specific
    install -m 644 src/platform/systemd/*.service /etc/systemd/system/ 2>/dev/null || true
    install -m 644 src/agents/shift/systemd/*.service /etc/systemd/system/ 2>/dev/null || true
    install -m 644 src/agents/shift/systemd/*.timer /etc/systemd/system/ 2>/dev/null || true
    # logrotate — Shift-Agent
    [ -f src/agents/shift/logrotate/shift-agent ] && install -m 644 src/agents/shift/logrotate/shift-agent /etc/logrotate.d/

    # Daily Brief agent (Agent #4) — scripts + systemd + templates.
    # `if; then` form (NOT `&& || true`) so install failures fail the deploy
    # loudly. Glob safety: skip the install if the dir is empty.
    if [ -d src/agents/daily_brief/scripts ] && compgen -G "src/agents/daily_brief/scripts/*" > /dev/null; then
        install -m 755 src/agents/daily_brief/scripts/* /usr/local/bin/
    fi
    if compgen -G "src/agents/daily_brief/systemd/*.service" > /dev/null; then
        install -m 644 src/agents/daily_brief/systemd/*.service /etc/systemd/system/
    fi
    if compgen -G "src/agents/daily_brief/systemd/*.timer" > /dev/null; then
        install -m 644 src/agents/daily_brief/systemd/*.timer /etc/systemd/system/
    fi
    if compgen -G "src/agents/daily_brief/templates/*" > /dev/null; then
        install -m 644 src/agents/daily_brief/templates/* /opt/shift-agent/templates/
    fi

    systemctl daemon-reload

    # EOD Reconciliation agent (Agent #5) — script + systemd
    if [ -d src/agents/eod_reconcile/scripts ] && compgen -G "src/agents/eod_reconcile/scripts/*" > /dev/null; then
        install -m 755 src/agents/eod_reconcile/scripts/* /usr/local/bin/
    fi
    if compgen -G "src/agents/eod_reconcile/systemd/*.service" > /dev/null; then
        install -m 644 src/agents/eod_reconcile/systemd/*.service /etc/systemd/system/
    fi
    if compgen -G "src/agents/eod_reconcile/systemd/*.timer" > /dev/null; then
        install -m 644 src/agents/eod_reconcile/systemd/*.timer /etc/systemd/system/
    fi

    # Multi-Location Coordinator (Agent #3) — SKILL-only in v0.1
    if [ -d src/agents/multi_location/skills ]; then
        rsync -a src/agents/multi_location/skills/ /root/.hermes/skills/
        chown -R shift-agent:shift-agent /root/.hermes/skills/
    fi
    # Catering Lead (Agent #2) — v0.2: SKILLs + scripts + templates
    if [ -d src/agents/catering/skills ]; then
        rsync -a src/agents/catering/skills/ /root/.hermes/skills/
        chown -R shift-agent:shift-agent /root/.hermes/skills/
    fi
    if compgen -G "src/agents/catering/scripts/*" > /dev/null; then
        install -m 755 src/agents/catering/scripts/* /usr/local/bin/
    fi
    if compgen -G "src/agents/catering/templates/*" > /dev/null; then
        install -m 644 src/agents/catering/templates/* /opt/shift-agent/templates/
    fi
    # Ensure catering-menu state dirs exist
    install -d -o shift-agent -g shift-agent /opt/shift-agent/state/catering-menu-archive 2>/dev/null || true
    # Tier 2 agents (Agents 6, 7, 9, 10, 12, 13, 14, 15, 16) — SKILL-only stubs.
    # All default cfg.<agent>.enabled=False; opt-in per customer.
    for tier2_agent in inventory supplier vip catering_followup hiring compliance employee_docs cash_ar sales_tax; do
        if [ -d "src/agents/${tier2_agent}/skills" ]; then
            rsync -a "src/agents/${tier2_agent}/skills/" /root/.hermes/skills/
        fi
    done
    chown -R shift-agent:shift-agent /root/.hermes/skills/

    # Enable + start Daily Brief timer + EOD timer. Loud on failure.
    systemctl enable --now send-daily-brief.timer
    systemctl enable --now eod-reconcile.timer
}

case "$ACTION" in
    deploy)
        if [ ! -d "$WORKING_COPY" ]; then
            echo "ERROR: $WORKING_COPY not found. First deploy requires manual setup." >&2
            exit 2
        fi
        cd "$WORKING_COPY"
        PREV_TAG=$(git describe --tags --abbrev=0 --match "${TAG_PREFIX}*" 2>/dev/null || echo "none")
        git fetch origin
        git checkout main
        git pull --ff-only origin main
        NEW_TAG="${TAG_PREFIX}$(date +%Y%m%d-%H%M%S)"
        git tag "$NEW_TAG"
        echo "Deploying $NEW_TAG (prev: $PREV_TAG)"

        install_artifacts

        # Restart services (in order: tail-logger can restart any time; gateway last)
        systemctl restart shift-agent-tail-logger.timer || true
        systemctl restart shift-agent-health.timer || true
        systemctl restart hermes-gateway
        sleep 5

        # Smoke test
        if ! /usr/local/bin/shift-agent-smoke-test.sh; then
            echo "SMOKE TEST FAILED — rolling back to $PREV_TAG" >&2
            if [ "$PREV_TAG" != "none" ]; then
                "$0" rollback "$PREV_TAG"
            else
                /usr/local/bin/shift-agent-notify-owner \
                    --title "Deploy FAILED, no prior tag" \
                    --priority 2 \
                    "Deploy $NEW_TAG failed smoke test and no prior tag exists to roll back to. Agent is in an uncertain state. SSH immediately."
            fi
            exit 1
        fi

        /usr/local/bin/shift-agent-notify-owner \
            --title "Deploy OK" \
            --priority -1 \
            "Deployed $NEW_TAG successfully."
        echo "Deploy $NEW_TAG complete."
        ;;
    rollback)
        TARGET="${2:?need target tag to rollback to}"
        cd "$WORKING_COPY"
        git checkout "$TARGET"
        install_artifacts
        systemctl restart hermes-gateway
        /usr/local/bin/shift-agent-notify-owner \
            --title "Rolled back to $TARGET" \
            --priority 1 \
            "Rolled back from broken deploy to $TARGET."
        echo "Rollback to $TARGET complete."
        ;;
    *)
        echo "usage: $0 [deploy|rollback <tag>]" >&2
        exit 2
        ;;
esac
