#!/usr/bin/env bash
# shift-agent-deploy — tarball-based deploy with smoke-test gate + auto-rollback.
#
# Reality check (2026-04-28): the prior version of this script presumed
# /opt/shift-agent/working was a git checkout. It never was on this VPS.
# Today's deploys land tarballs into /opt/shift-agent/staging-new/ and
# install_artifacts() reads from there. This rewrite formalizes that flow.
#
# Deploy flow:
#   1. Local side: tools/build-deploy-tarball.sh produces shift-agent-deploy.tgz
#      with src/ + .commit-hash. SCP it to VPS.
#   2. VPS side: extract tarball into /opt/shift-agent/staging-new/, then run
#      this script with no args (or `deploy`).
#   3. Script snapshots existing staging-new/ as a backup tarball (for rollback),
#      runs install_artifacts(), restarts services, runs smoke test.
#   4. Smoke-test failure auto-rolls back to the previous tarball.
#
# Usage:
#   shift-agent-deploy                                  # deploy current staging-new
#   shift-agent-deploy rollback <deploy-tag>            # restore prior tarball + reinstall
#   shift-agent-deploy list                             # show available rollback targets

set -euo pipefail

ACTION="${1:-deploy}"
STAGING=/opt/shift-agent/staging-new
DEPLOYS_DIR=/opt/shift-agent/deploys
KEEP_TARBALLS=5

mkdir -p "$DEPLOYS_DIR"

install_artifacts() {
    local src_root="$1"
    cd "$src_root"

    # Scripts: platform shared (identify-sender, validate-sender-block, log-decision*)
    # + Shift-Agent-specific (shift-agent-*, send-coverage-message, etc.).
    install -m 755 src/platform/scripts/* /usr/local/bin/
    install -m 755 src/agents/shift/scripts/* /usr/local/bin/

    # Python modules — flat layout at /opt/shift-agent/ matches scripts' sys.path
    install -m 644 src/platform/schemas.py /opt/shift-agent/schemas.py
    install -m 644 src/platform/safe_io.py /opt/shift-agent/safe_io.py
    install -m 644 src/platform/sender_context.py /opt/shift-agent/sender_context.py
    install -m 644 src/platform/exit_codes.py /opt/shift-agent/exit_codes.py
    install -m 644 src/platform/log_source.py /opt/shift-agent/log_source.py

    # Templates — Shift-Agent message templates (idempotent: shared dir filled by multiple agents below)
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

    # Daily Brief agent (Agent #4)
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

    # EOD Reconciliation agent (Agent #5)
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

    # Catering Lead (Agent #2)
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
    install -d -o shift-agent -g shift-agent /opt/shift-agent/state/catering-menu-archive 2>/dev/null || true

    # Tier 2 agents — SKILL-only stubs
    for tier2_agent in inventory supplier vip catering_followup hiring compliance employee_docs cash_ar sales_tax; do
        if [ -d "src/agents/${tier2_agent}/skills" ]; then
            rsync -a "src/agents/${tier2_agent}/skills/" /root/.hermes/skills/
        fi
    done
    chown -R shift-agent:shift-agent /root/.hermes/skills/

    # Enable + start cron timers
    systemctl enable --now send-daily-brief.timer 2>/dev/null || true
    systemctl enable --now eod-reconcile.timer 2>/dev/null || true
}

snapshot_staging() {
    # Snapshot current staging-new/ contents for rollback.
    # Returns the tag name on stdout.
    local commit_hash
    commit_hash=$(cat "$STAGING/.commit-hash" 2>/dev/null | head -c 8)
    [ -z "$commit_hash" ] && commit_hash="unknown"
    local tag="deploy-$(date +%Y%m%d-%H%M%S)-${commit_hash}"

    if [ -d "$STAGING/src" ]; then
        tar czf "$DEPLOYS_DIR/${tag}.tgz" -C "$STAGING" src .commit-hash 2>/dev/null \
            || tar czf "$DEPLOYS_DIR/${tag}.tgz" -C "$STAGING" src
    fi
    echo "$tag"
}

rotate_deploys() {
    # Keep the N most recent tarballs; delete older.
    cd "$DEPLOYS_DIR"
    # shellcheck disable=SC2012  # ls is intentional for mtime sort
    ls -t deploy-*.tgz 2>/dev/null | tail -n +"$((KEEP_TARBALLS+1))" | xargs -r rm
}

list_deploys() {
    if [ -d "$DEPLOYS_DIR" ] && compgen -G "$DEPLOYS_DIR/deploy-*.tgz" > /dev/null; then
        # shellcheck disable=SC2012
        ls -lht "$DEPLOYS_DIR/"deploy-*.tgz | awk '{print $9, $5, $6, $7, $8}'
    else
        echo "(no deploys recorded)"
    fi
}

case "$ACTION" in
    deploy)
        if [ ! -d "$STAGING/src" ]; then
            echo "ERROR: $STAGING/src not found. Did you scp + extract the deploy tarball?" >&2
            echo "  Local side: tools/build-deploy-tarball.sh && scp shift-agent-deploy.tgz main-vps:/tmp/" >&2
            echo "  VPS side:   sudo tar xzf /tmp/shift-agent-deploy.tgz -C $STAGING/" >&2
            exit 2
        fi

        # Hermes pin gate — fail-closed before any state change. Catches silent
        # drift: Hermes commit moved, bridge.js content changed, or our patch
        # markers no longer anchored where we expect. Override mechanism for
        # legitimate Hermes upgrades documented in the check script.
        # Tightened from WARN to FAIL on missing script (per PR #17 reviewer's
        # Low-4): once tarballs reliably ship tools/, a missing check script
        # means tarball corruption or a refactor that moved the script — both
        # cases where silently bypassing the gate is dangerous.
        if [ ! -x "$STAGING/tools/check-shift-agent-patch.sh" ]; then
            echo "ERROR: $STAGING/tools/check-shift-agent-patch.sh not found or not executable." >&2
            echo "  Either the tarball is malformed or a refactor moved the script." >&2
            echo "  Refusing to deploy without the pin gate." >&2
            exit 1
        fi
        echo "=== Hermes pin gate ==="
        if ! "$STAGING/tools/check-shift-agent-patch.sh"; then
            echo "ERROR: Hermes pin verification failed — refusing to install." >&2
            echo "  No state change has been made. See output above for details." >&2
            exit 1
        fi

        # Env symlink integrity gate — verify /opt/shift-agent/.env still points
        # where we set it up during the consolidation migration. Catches: Hermes
        # setup re-run that recreates the file, manual editor truncation via
        # `> /root/.hermes/.env`, redirect that broke the symlink. Same fail-closed
        # pattern as the Hermes pin gate; exits before install_artifacts so no
        # rollback is needed.
        # If /opt/shift-agent/.env is a regular file (pre-migration state), this
        # gate is a no-op — only enforced once the symlink exists.
        if [ -L /opt/shift-agent/.env ]; then
            echo "=== Env symlink integrity gate ==="
            ENV_TARGET=$(readlink /opt/shift-agent/.env)
            if [ "$ENV_TARGET" != "/root/.hermes/.env" ]; then
                echo "ERROR: /opt/shift-agent/.env symlink target drifted." >&2
                echo "  expected: /root/.hermes/.env" >&2
                echo "  got:      $ENV_TARGET" >&2
                echo "  Recovery: ls -la /opt/shift-agent/.env, fix target, retry." >&2
                exit 1
            fi
            if [ ! -r /opt/shift-agent/.env ]; then
                echo "ERROR: /opt/shift-agent/.env symlink target unreadable." >&2
                echo "  /root/.hermes/.env may have been deleted or permissions changed." >&2
                exit 1
            fi
            echo "OK: env symlink intact ($ENV_TARGET)"
        fi

        COMMIT_HASH=$(cat "$STAGING/.commit-hash" 2>/dev/null | head -c 8 || echo "unknown")
        NEW_TAG="deploy-$(date +%Y%m%d-%H%M%S)-${COMMIT_HASH}"
        PREV_TAG=$(ls -t "$DEPLOYS_DIR/"deploy-*.tgz 2>/dev/null | head -1 | xargs -n1 basename 2>/dev/null | sed 's/\.tgz$//' || echo "none")

        echo "Deploying $NEW_TAG (prev rollback target: $PREV_TAG)"

        # Snapshot current staging as the new tarball BEFORE install (so the tarball
        # we'd roll back to is the source we're about to install — symmetric with
        # rollback's "extract tarball into staging then install_artifacts" flow).
        tar czf "$DEPLOYS_DIR/${NEW_TAG}.tgz" -C "$STAGING" src .commit-hash 2>/dev/null \
            || tar czf "$DEPLOYS_DIR/${NEW_TAG}.tgz" -C "$STAGING" src

        install_artifacts "$STAGING"

        # Restart services (in order: tail-logger first, gateway last)
        systemctl restart shift-agent-tail-logger.timer 2>/dev/null || true
        systemctl restart shift-agent-health.timer 2>/dev/null || true
        systemctl restart hermes-gateway
        sleep 5

        # Smoke test gate
        if ! /usr/local/bin/shift-agent-smoke-test.sh; then
            echo "SMOKE TEST FAILED — rolling back to $PREV_TAG" >&2
            if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                "$0" rollback "$PREV_TAG"
            else
                /usr/local/bin/shift-agent-notify-owner \
                    --title "Deploy FAILED, no prior tarball" \
                    --priority 2 \
                    "Deploy $NEW_TAG failed smoke test and no prior tarball exists to roll back to. Agent in uncertain state. SSH immediately." 2>/dev/null || true
            fi
            exit 1
        fi

        rotate_deploys

        /usr/local/bin/shift-agent-notify-owner \
            --title "Deploy OK" \
            --priority -1 \
            "Deployed $NEW_TAG successfully." 2>/dev/null || true
        echo "Deploy $NEW_TAG complete."
        ;;

    rollback)
        TARGET="${2:?need target tag to rollback to (run: shift-agent-deploy.sh list)}"
        TARBALL="$DEPLOYS_DIR/${TARGET}.tgz"
        if [ ! -f "$TARBALL" ]; then
            echo "ERROR: tarball not found: $TARBALL" >&2
            echo "Available targets:" >&2
            list_deploys >&2
            exit 2
        fi

        echo "Rolling back to $TARGET ($TARBALL)"

        # Restore source tree to staging-new
        rm -rf "$STAGING/src" "$STAGING/.commit-hash"
        tar xzf "$TARBALL" -C "$STAGING/"

        # Re-install from restored staging
        install_artifacts "$STAGING"

        systemctl restart shift-agent-tail-logger.timer 2>/dev/null || true
        systemctl restart hermes-gateway
        sleep 5

        /usr/local/bin/shift-agent-notify-owner \
            --title "Rolled back to $TARGET" \
            --priority 1 \
            "Rolled back from broken deploy. Re-run smoke test if needed." 2>/dev/null || true
        echo "Rollback to $TARGET complete."
        ;;

    list)
        echo "Available deploys at $DEPLOYS_DIR:"
        list_deploys
        ;;

    *)
        echo "usage: $0 [deploy|rollback <tag>|list]" >&2
        exit 2
        ;;
esac
