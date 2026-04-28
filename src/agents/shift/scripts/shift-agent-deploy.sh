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

    # Daily Brief agent (Agent #4) — scripts + systemd + templates
    [ -d src/agents/daily_brief/scripts ] && install -m 755 src/agents/daily_brief/scripts/* /usr/local/bin/
    [ -d src/agents/daily_brief/systemd ] && install -m 644 src/agents/daily_brief/systemd/*.service /etc/systemd/system/ 2>/dev/null || true
    [ -d src/agents/daily_brief/systemd ] && install -m 644 src/agents/daily_brief/systemd/*.timer /etc/systemd/system/ 2>/dev/null || true
    [ -d src/agents/daily_brief/templates ] && install -m 644 src/agents/daily_brief/templates/* /opt/shift-agent/templates/

    systemctl daemon-reload

    # Enable + start Daily Brief timer (idempotent — won't re-enable if already on)
    systemctl enable --now send-daily-brief.timer 2>/dev/null || true
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
