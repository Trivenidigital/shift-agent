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
    # PR-D1: audit_helpers.py — best-effort emitters for config_load_failed
    # + catering_quote_sent_lead_missing. Pre-restart gate
    # check-audit-helpers-symbols imports this module; missing here =
    # forced rollback on every deploy.
    install -m 644 src/platform/audit_helpers.py /opt/shift-agent/audit_helpers.py

    # Templates — Shift-Agent message templates (idempotent: shared dir filled by multiple agents below)
    install -d /opt/shift-agent/templates
    install -m 644 src/agents/shift/templates/* /opt/shift-agent/templates/

    # Skills → Hermes — Shift-Agent SKILL files
    rsync -a --delete src/agents/shift/skills/ /root/.hermes/skills/
    chown -R shift-agent:shift-agent /root/.hermes/skills/

    # logs dir — bootstrap target for prune-expense.log + similar systemd
    # StandardOutput=append: writers (systemd doesn't auto-mkdir parents).
    # Python writers using safe_io.ndjson_append self-bootstrap via
    # path.parent.mkdir(parents=True, exist_ok=True), so decisions.log /
    # hermes-gateway.log don't strictly need this — but it's load-bearing
    # for the prune-expense systemd unit on a fresh VPS.
    # Mode 0750: matches createolddir 0750 in src/agents/shift/logrotate/shift-agent.
    install -d -o shift-agent -g shift-agent -m 0750 /opt/shift-agent/logs 2>/dev/null || true

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

    # Agent #21 — Expense Bookkeeper (v0.1; mocked QBOClient; ships disabled-default)
    install -m 644 src/platform/qbo_client.py /opt/shift-agent/qbo_client.py
    install -d -o shift-agent -g shift-agent -m 0700 /opt/shift-agent/state/expense-bookkeeper 2>/dev/null || true
    install -d -o shift-agent -g shift-agent -m 0700 /opt/shift-agent/state/expense-bookkeeper/receipts 2>/dev/null || true
    if [ -d src/agents/expense_bookkeeper/skills ]; then
        rsync -a src/agents/expense_bookkeeper/skills/ /root/.hermes/skills/
        chown -R shift-agent:shift-agent /root/.hermes/skills/
    fi
    if compgen -G "src/agents/expense_bookkeeper/scripts/*" > /dev/null; then
        install -m 755 src/agents/expense_bookkeeper/scripts/* /usr/local/bin/
    fi
    if compgen -G "src/agents/expense_bookkeeper/templates/*" > /dev/null; then
        install -m 644 src/agents/expense_bookkeeper/templates/* /opt/shift-agent/templates/
    fi
    if compgen -G "src/agents/expense_bookkeeper/systemd/*" > /dev/null; then
        install -m 644 src/agents/expense_bookkeeper/systemd/*.service /etc/systemd/system/ 2>/dev/null || true
        install -m 644 src/agents/expense_bookkeeper/systemd/*.timer   /etc/systemd/system/ 2>/dev/null || true
    fi

    # Enable + start cron timers
    systemctl enable --now send-daily-brief.timer 2>/dev/null || true
    systemctl enable --now eod-reconcile.timer 2>/dev/null || true
    systemctl enable --now send-routing-accuracy-summary.timer 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --now prune-expense-receipts.timer 2>/dev/null || true
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

        # Env symlink integrity gate — strict. Fail-closed if /opt/shift-agent/.env
        # is anything OTHER than a symlink to /root/.hermes/.env. Catches:
        #   - regular file replacing the symlink (tarball with .env, mv newfile .env)
        #   - symlink pointing somewhere else
        #   - missing file
        #   - target unreadable (Hermes uninstall, perms broken)
        #
        # Migration via tools/migrate-env-to-symlink.sh is REQUIRED on every
        # customer VPS as step-0 of bring-up, BEFORE the first deploy. After
        # migration runs, this gate enforces the symlink invariant forever.
        #
        # The earlier "if [ -L ... ] then check" version of this gate had inverted
        # polarity — silently passed when the symlink was REPLACED by a regular
        # file, which is exactly the failure mode the gate was supposed to catch.
        # Validation on 2026-04-28 surfaced the bug; this is the corrected version.
        #
        # No automated regression test for this gate. Manual Step-5 (deliberately
        # break the symlink, run deploy, expect fail-closed, restore) is the
        # canonical check — run it after any change to gate logic. PR #17 Low-5
        # backlogs bats infrastructure as the long-term answer.
        echo "=== Env symlink integrity gate ==="
        if [ ! -L /opt/shift-agent/.env ]; then
            echo "ERROR: /opt/shift-agent/.env is not a symlink." >&2
            if [ -e /opt/shift-agent/.env ]; then
                # `stat -c` is GNU-specific. This script is Linux-only by design
                # (Hermes runtime requires fcntl etc.); BSD/macOS would need
                # `stat -f '%HT'`. Not worth abstracting — production target
                # is the customer Linux VPS.
                echo "  got: $(stat -c '%F' /opt/shift-agent/.env)" >&2
            else
                echo "  got: missing" >&2
            fi
            echo "  expected: symlink → /root/.hermes/.env" >&2
            echo "" >&2
            echo "  Which scenario is this?" >&2
            echo "    A) Fresh customer VPS — never migrated. ls -la would show a regular" >&2
            echo "       file with handful-of-keys content matching the provisioning template." >&2
            echo "       Action: run the migration:" >&2
            echo "         sudo $STAGING/tools/migrate-env-to-symlink.sh" >&2
            echo "" >&2
            echo "    B) Post-migration breakage — symlink was replaced (mv, tarball with .env," >&2
            echo "       editor save-as-new-file). ls -la would show a regular file with" >&2
            echo "       hand-written content, OR /opt/shift-agent/.env.pre-symlink-backup-* exists." >&2
            echo "       Action: restore the symlink:" >&2
            echo "         sudo rm -f /opt/shift-agent/.env" >&2
            echo "         sudo ln -s /root/.hermes/.env /opt/shift-agent/.env" >&2
            exit 1
        fi
        ENV_TARGET=$(readlink /opt/shift-agent/.env)
        if [ "$ENV_TARGET" != "/root/.hermes/.env" ]; then
            echo "ERROR: /opt/shift-agent/.env symlink target drifted." >&2
            echo "  expected: /root/.hermes/.env" >&2
            echo "  got:      $ENV_TARGET" >&2
            echo "  Recovery: sudo ln -sf /root/.hermes/.env /opt/shift-agent/.env  &&  retry deploy" >&2
            exit 1
        fi
        if [ ! -r /opt/shift-agent/.env ]; then
            echo "ERROR: /opt/shift-agent/.env symlink target unreadable." >&2
            echo "  /root/.hermes/.env may have been deleted or permissions changed." >&2
            echo "  Recovery: ls -la /root/.hermes/.env  (check existence + perms);" >&2
            echo "            verify shift-agent user can read it." >&2
            exit 1
        fi
        echo "OK: env symlink intact ($ENV_TARGET)"

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

        # Pre-restart import gate: a missing safe_io OR audit_helpers chokepoint
        # symbol means traffic hits new code BEFORE smoke fires post-restart.
        # Run the symbol-import checks against the just-installed binary (still
        # old service) — failure path rolls back without touching live traffic.
        # PR-D1 R3-H-Gate1: chained check-audit-helpers-symbols.
        if ! /usr/local/bin/check-safe-io-symbols > /dev/null \
              || ! /usr/local/bin/check-audit-helpers-symbols > /dev/null; then
            echo "FAIL: pre-restart import gate — refusing to restart hermes-gateway" >&2
            if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                "$0" rollback "$PREV_TAG"
                # Evict the broken tarball from the rotation so next deploy
                # doesn't surface it as a candidate rollback target.
                rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
            else
                /usr/local/bin/shift-agent-notify-owner \
                    --title "Deploy FAILED at pre-restart import gate, no prior tarball" \
                    --priority 2 \
                    "Deploy $NEW_TAG failed pre-restart symbol-import check. New files installed but service still on OLD code (gateway not yet restarted). No prior tarball to roll back to — SSH immediately." 2>/dev/null || true
                # Evict the broken tarball from the rotation so it isn't surfaced
                # as a rollback candidate on the next deploy.
                rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
            fi
            exit 1
        fi

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
                # PR-D1 R4-H2: evict the broken tarball from rotation so the
                # next deploy doesn't surface it as a candidate rollback target.
                # Mirror of the pre-restart-gate eviction at the failure path
                # above; without this, ls -t shows the broken tarball first
                # and PR-D2 rollback chains backward to a pre-shim binary.
                rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
            else
                /usr/local/bin/shift-agent-notify-owner \
                    --title "Deploy FAILED, no prior tarball" \
                    --priority 2 \
                    "Deploy $NEW_TAG failed smoke test and no prior tarball exists to roll back to. Agent in uncertain state. SSH immediately." 2>/dev/null || true
                # PR-D1 R4-H2: evict the broken tarball even when no prior exists.
                rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
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

        # Re-run smoke after rollback. If the prior tarball is itself broken
        # (e.g. operator manually edited /opt/shift-agent/safe_io.py between
        # deploys), exit 1 + Pushover P2 — operator must SSH. Terminal:
        # we do NOT attempt rollback-of-rollback.
        if ! /usr/local/bin/shift-agent-smoke-test.sh; then
            /usr/local/bin/shift-agent-notify-owner \
                --priority 2 \
                --title "Rollback to $TARGET FAILED smoke — agent in uncertain state" \
                "Rollback completed but smoke test failed against $TARGET. Prior tarball may itself be broken. SSH immediately." 2>/dev/null || true
            exit 1
        fi

        /usr/local/bin/shift-agent-notify-owner \
            --title "Rolled back to $TARGET" \
            --priority 1 \
            "Rolled back from broken deploy. Smoke test passed against $TARGET." 2>/dev/null || true
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
