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
    # Rollback hygiene for files introduced after older tarballs. If a
    # rollback target predates this readiness CLI, remove the previously
    # installed copy instead of leaving residue outside the tarball surface.
    if [ ! -f src/platform/scripts/credential-minimized-readiness ]; then
        rm -f /usr/local/bin/credential-minimized-readiness
    fi
    if [ ! -f src/agents/shift/scripts/pilot-readiness-check ]; then
        rm -f /usr/local/bin/pilot-readiness-check
    fi
    if [ ! -f src/platform/scripts/check-hermes-config-yaml ]; then
        rm -f /usr/local/bin/check-hermes-config-yaml
    fi

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
    # Credential-minimized readiness matrix/report. Guarded for rollback
    # compatibility with tarballs that predate this module.
    if [ -f src/platform/credential_readiness.py ]; then
        install -m 644 src/platform/credential_readiness.py /opt/shift-agent/credential_readiness.py
    else
        rm -f /opt/shift-agent/credential_readiness.py
    fi
    # Hermes config.yaml shape gate module. Guarded for rollback compatibility
    # with tarballs that predate this module.
    if [ -f src/platform/check_hermes_config_yaml.py ]; then
        install -m 644 src/platform/check_hermes_config_yaml.py /opt/shift-agent/check_hermes_config_yaml.py
    else
        rm -f /opt/shift-agent/check_hermes_config_yaml.py
    fi

    # Templates — Shift-Agent message templates (idempotent: shared dir filled by multiple agents below)
    install -d /opt/shift-agent/templates
    install -m 644 src/agents/shift/templates/* /opt/shift-agent/templates/

    # Skills → Hermes — Shift-Agent SKILL files
    #
    # ORDERING INVARIANT (DO NOT REORDER WITHOUT CARE):
    # This `--delete` rsync MUST run BEFORE all per-agent skill rsyncs
    # below (multi_location, catering, daily_brief, eod_reconcile,
    # tier-2 stubs, expense_bookkeeper). Per-agent rsyncs are ADDITIVE
    # (no `--delete`) and deposit their SKILL.md files INTO the same
    # /root/.hermes/skills/ directory. If you move this Shift rsync to
    # run AFTER the per-agent rsyncs (e.g. as a "cleanup pass"),
    # `--delete` will silently wipe every per-agent skill that was just
    # installed, leaving only Shift's own. Catastrophic + silent.
    rsync -a --delete src/agents/shift/skills/ /root/.hermes/skills/
    chown -R shift-agent:shift-agent /root/.hermes/skills/

    # PR-CF6: Hermes plugins — cf-router + any future plugins under src/plugins/.
    # Loaded by hermes-gateway at startup from ~/.hermes/plugins/<name>/.
    # cf-router replaces the F8/F9 custom watchdogs (PR-CF6) and the F7
    # catering-dispatcher-watchdog (PR-CF7) by intercepting at
    # pre_gateway_dispatch. The legacy watchdog .service / .timer / script
    # files were deleted in the 2026-05-04 canonical-cleanup; see git tags
    # pre-srilu-cleanup-2026-05-04 (F8/F9) and pre-cf7-cleanup-2026-05-04
    # (F7) if a pre-cleanup rollback ever needs them back.
    if [ -d src/plugins ]; then
        mkdir -p /root/.hermes/plugins
        rsync -a --delete src/plugins/ /root/.hermes/plugins/
        chown -R shift-agent:shift-agent /root/.hermes/plugins/
    fi

    # PR-CF7 (2026-05-04): F7 catering-dispatcher-watchdog migrated into
    # cf-router plugin's F7 path. Disable + stop the legacy systemd unit
    # if it's still running on this VPS (idempotent — `systemctl stop` /
    # `disable` are no-ops if the unit is already absent).
    systemctl stop catering-dispatcher-watchdog.service 2>/dev/null || true
    systemctl disable catering-dispatcher-watchdog.service 2>/dev/null || true
    rm -f /etc/systemd/system/catering-dispatcher-watchdog.service
    rm -f /usr/local/bin/catering-dispatcher-watchdog
    systemctl daemon-reload 2>/dev/null || true

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

    # Multi-Location Coordinator (Agent #3)
    # PR-Agent3-v0.1 (2026-05-04): SKILLs include the new
    # customer_location_query (customer-facing); Phase 1 extension to
    # multi_location_query (owner-facing); plus closest-location.py
    # script that wraps the bundled productivity/maps skill.
    if [ -d src/agents/multi_location/skills ]; then
        rsync -a src/agents/multi_location/skills/ /root/.hermes/skills/
        chown -R shift-agent:shift-agent /root/.hermes/skills/
    fi
    if compgen -G "src/agents/multi_location/scripts/*" > /dev/null; then
        install -m 755 src/agents/multi_location/scripts/* /usr/local/bin/
    fi

    # P&L Anomaly Detective (Agent #22) — scaffold-only v0.1
    if [ -d src/agents/pnl_anomaly/skills ]; then
        rsync -a src/agents/pnl_anomaly/skills/ /root/.hermes/skills/
        chown -R shift-agent:shift-agent /root/.hermes/skills/
    fi

    # Equipment & Maintenance (Agent #19) — scaffold-only v0.1
    if [ -d src/agents/equipment_maintenance/skills ]; then
        rsync -a src/agents/equipment_maintenance/skills/ /root/.hermes/skills/
        chown -R shift-agent:shift-agent /root/.hermes/skills/
    fi

    # Compliance Calendar (Agent #13)
    # PR-Agent13-v0.1 (2026-05-04): SKILLs include compliance_owner_query;
    # scripts include check-compliance-deadlines.py + mark-compliance-item-done.py;
    # template compliance_reminder.txt rendered via render-coverage-template;
    # systemd .service has @CUSTOMER_TZ@ placeholder substituted from
    # cfg.customer.timezone at install time (not yq — Reviewer A2-B1 fix:
    # PyYAML is already a deploy dependency; yq is NOT installed on srilu).
    if [ -d src/agents/compliance/skills ]; then
        rsync -a src/agents/compliance/skills/ /root/.hermes/skills/
        chown -R shift-agent:shift-agent /root/.hermes/skills/
    fi
    if compgen -G "src/agents/compliance/scripts/*" > /dev/null; then
        install -m 755 src/agents/compliance/scripts/* /usr/local/bin/
    fi
    if compgen -G "src/agents/compliance/templates/*" > /dev/null; then
        install -m 644 src/agents/compliance/templates/* /opt/shift-agent/templates/
    fi
    if compgen -G "src/agents/compliance/systemd/*.service" > /dev/null; then
        # TZ templating: read cfg.customer.timezone via PyYAML (yq is not
        # installed on srilu; PyYAML IS — already used by render-coverage-
        # template + schemas validation). Default to America/New_York if
        # config missing or unparseable (matches Triveni's reference customer).
        customer_tz=$(python3 -c "import yaml; print(yaml.safe_load(open('/opt/shift-agent/config.yaml'))['customer']['timezone'])" 2>/dev/null || echo "America/New_York")
        for svc_src in src/agents/compliance/systemd/*.service; do
            svc_name=$(basename "$svc_src")
            sed "s|@CUSTOMER_TZ@|${customer_tz}|g" "$svc_src" \
                > "/etc/systemd/system/${svc_name}"
        done
    fi
    if compgen -G "src/agents/compliance/systemd/*.timer" > /dev/null; then
        install -m 644 src/agents/compliance/systemd/*.timer /etc/systemd/system/
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
    if compgen -G "src/agents/catering/systemd/*.service" > /dev/null; then
        install -m 644 src/agents/catering/systemd/*.service /etc/systemd/system/
    fi
    if compgen -G "src/agents/catering/systemd/*.timer" > /dev/null; then
        install -m 644 src/agents/catering/systemd/*.timer /etc/systemd/system/
    fi
    install -d -o shift-agent -g shift-agent /opt/shift-agent/state/catering-menu-archive 2>/dev/null || true

    # Hermes Flyer Studio (WhatsApp flyer design workflow)
    if [ -d src/agents/flyer/skills ]; then
        rsync -a src/agents/flyer/skills/ /root/.hermes/skills/
        chown -R shift-agent:shift-agent /root/.hermes/skills/
    fi
    if [ -f src/agents/flyer/render.py ]; then
        install -m 644 src/agents/flyer/render.py /opt/shift-agent/flyer_render.py
    else
        rm -f /opt/shift-agent/flyer_render.py
    fi
    if [ -f src/agents/flyer/workflow.py ]; then
        install -m 644 src/agents/flyer/workflow.py /opt/shift-agent/flyer_workflow.py
    else
        rm -f /opt/shift-agent/flyer_workflow.py
    fi
    if [ -f src/agents/flyer/onboarding.py ]; then
        install -m 644 src/agents/flyer/onboarding.py /opt/shift-agent/flyer_onboarding.py
    else
        rm -f /opt/shift-agent/flyer_onboarding.py
    fi
    if [ -f src/agents/flyer/intake.py ]; then
        install -m 644 src/agents/flyer/intake.py /opt/shift-agent/flyer_intake.py
    else
        rm -f /opt/shift-agent/flyer_intake.py
    fi
    if [ -f src/agents/flyer/starter_briefs.py ]; then
        install -m 644 src/agents/flyer/starter_briefs.py /opt/shift-agent/flyer_starter_briefs.py
    else
        rm -f /opt/shift-agent/flyer_starter_briefs.py
    fi
    if [ -f src/agents/flyer/recovery.py ]; then
        install -m 644 src/agents/flyer/recovery.py /opt/shift-agent/flyer_recovery.py
    else
        rm -f /opt/shift-agent/flyer_recovery.py
    fi
    if [ -f src/agents/flyer/customer_copy_policy.py ]; then
        install -m 644 src/agents/flyer/customer_copy_policy.py /opt/shift-agent/flyer_customer_copy_policy.py
    else
        rm -f /opt/shift-agent/flyer_customer_copy_policy.py
    fi
    if [ -f src/agents/flyer/intent.py ]; then
        install -m 644 src/agents/flyer/intent.py /opt/shift-agent/flyer_intent.py
    else
        rm -f /opt/shift-agent/flyer_intent.py
    fi
    if [ -f src/agents/flyer/intent_training.py ]; then
        install -m 644 src/agents/flyer/intent_training.py /opt/shift-agent/flyer_intent_training.py
    else
        rm -f /opt/shift-agent/flyer_intent_training.py
    fi
    if [ -f src/agents/flyer/account.py ]; then
        install -m 644 src/agents/flyer/account.py /opt/shift-agent/flyer_account.py
    else
        rm -f /opt/shift-agent/flyer_account.py
    fi
    if [ -f src/agents/flyer/guest_order.py ]; then
        install -m 644 src/agents/flyer/guest_order.py /opt/shift-agent/flyer_guest_order.py
    else
        rm -f /opt/shift-agent/flyer_guest_order.py
    fi
    if [ -f src/agents/flyer/facts.py ]; then
        install -m 644 src/agents/flyer/facts.py /opt/shift-agent/flyer_facts.py
    else
        rm -f /opt/shift-agent/flyer_facts.py
    fi
    if [ -f src/agents/flyer/reference_extract.py ]; then
        install -m 644 src/agents/flyer/reference_extract.py /opt/shift-agent/flyer_reference_extract.py
    else
        rm -f /opt/shift-agent/flyer_reference_extract.py
    fi
    if [ -f src/agents/flyer/visual_qa.py ]; then
        install -m 644 src/agents/flyer/visual_qa.py /opt/shift-agent/flyer_visual_qa.py
    else
        rm -f /opt/shift-agent/flyer_visual_qa.py
    fi
    if [ -f src/agents/flyer/manual_queue.py ]; then
        install -m 644 src/agents/flyer/manual_queue.py /opt/shift-agent/flyer_manual_queue.py
    else
        rm -f /opt/shift-agent/flyer_manual_queue.py
    fi
    if [ -d src/agents/flyer/scripts ] && compgen -G "src/agents/flyer/scripts/*" > /dev/null; then
        install -m 755 src/agents/flyer/scripts/* /usr/local/bin/
        for flyer_binary in \
            create-flyer-project \
            update-flyer-project \
            generate-flyer-concepts \
            finalize-flyer-assets \
            handle-flyer-onboarding \
            handle-flyer-intake \
            check-flyer-reference-scope \
            store-flyer-brand-asset \
            manage-flyer-account \
            manage-flyer-guest-order \
            send-flyer-package \
            send-flyer-campaign \
            flyer-delivery-report \
            flyer-recovery-watchdog \
            flyer-recovery-preflight \
            flyer-manual-queue \
            flyer-source-edit-sla-watchdog \
            flyer-intent-training-export \
            smoke-flyer-quality; do
            if [ ! -f "src/agents/flyer/scripts/${flyer_binary}" ]; then
                rm -f "/usr/local/bin/${flyer_binary}"
            fi
        done
        # Per-binary stale cleanup for rollback tarballs that still contain a
        # Flyer scripts directory but predate this specific Phase 2 CLI.
        [ -f src/agents/flyer/scripts/smoke-flyer-quality ] || rm -f /usr/local/bin/smoke-flyer-quality
    else
        rm -f \
            /usr/local/bin/create-flyer-project \
            /usr/local/bin/update-flyer-project \
            /usr/local/bin/generate-flyer-concepts \
            /usr/local/bin/finalize-flyer-assets \
            /usr/local/bin/handle-flyer-onboarding \
            /usr/local/bin/handle-flyer-intake \
            /usr/local/bin/check-flyer-reference-scope \
            /usr/local/bin/store-flyer-brand-asset \
            /usr/local/bin/manage-flyer-account \
            /usr/local/bin/manage-flyer-guest-order \
            /usr/local/bin/flyer-delivery-report \
            /usr/local/bin/flyer-recovery-watchdog \
            /usr/local/bin/flyer-recovery-preflight \
            /usr/local/bin/flyer-manual-queue \
            /usr/local/bin/flyer-source-edit-sla-watchdog \
            /usr/local/bin/flyer-intent-training-export \
            /usr/local/bin/send-flyer-campaign \
            /usr/local/bin/send-flyer-package \
            /usr/local/bin/smoke-flyer-quality
    fi
    if compgen -G "src/agents/flyer/systemd/*.service" > /dev/null; then
        install -m 644 src/agents/flyer/systemd/*.service /etc/systemd/system/
    fi
    if compgen -G "src/agents/flyer/systemd/*.timer" > /dev/null; then
        install -m 644 src/agents/flyer/systemd/*.timer /etc/systemd/system/
    fi
    install -d -o shift-agent -g shift-agent -m 0700 /opt/shift-agent/state/flyer 2>/dev/null || true
    install -d -o shift-agent -g shift-agent -m 0700 /opt/shift-agent/state/flyer/assets 2>/dev/null || true
    install -d -o shift-agent -g shift-agent -m 0700 /opt/shift-agent/state/flyer/finals 2>/dev/null || true
    install -d -o shift-agent -g shift-agent -m 0700 /opt/shift-agent/state/flyer/marketing 2>/dev/null || true
    if [ -f src/agents/flyer/assets/Flyer.png ]; then
        install -m 0640 -o shift-agent -g shift-agent src/agents/flyer/assets/Flyer.png /opt/shift-agent/state/flyer/marketing/Flyer.png
    fi
    chown -R shift-agent:shift-agent /opt/shift-agent/state/flyer 2>/dev/null || true
    systemctl daemon-reload
    if /usr/local/lib/hermes-agent/venv/bin/python - <<'PY' 2>/dev/null
import sys, yaml
sys.path.insert(0, "/opt/shift-agent")
from schemas import Config
cfg = Config.model_validate(yaml.safe_load(open("/opt/shift-agent/config.yaml")) or {})
raise SystemExit(0 if cfg.flyer.recovery.enable_timer and cfg.flyer.recovery.mode != "off" else 1)
PY
    then
        if ! systemctl enable --now flyer-recovery-watchdog.timer; then
            echo "FAIL: flyer-recovery-watchdog.timer enable/start failed" >&2
            exit 1
        fi
        if ! systemctl is-active --quiet flyer-recovery-watchdog.timer; then
            echo "FAIL: flyer-recovery-watchdog.timer not active after enable" >&2
            exit 1
        fi
    else
        systemctl disable --now flyer-recovery-watchdog.timer 2>/dev/null || true
    fi

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

    # Deploy gate — required-SKILL presence check.
    #
    # Background (2026-05-05): an interactive Hermes session ran a curator
    # consolidation that overwrote 11 individual project SKILLs with 5
    # umbrella SKILLs (shift-agent-core, catering-management, etc.). The
    # umbrellas inherited the curator's flat description without the
    # "Always invoke this skill FIRST" forcing language that
    # dispatch_shift_agent uses, so the LLM agent stopped invoking the
    # dispatcher altogether and silently fell back to LLM-direct
    # hallucination on inbounds. No audit-chain entries, no skill_invoked
    # events — silent regression discovered only by manual end-to-end test.
    #
    # This gate fails the deploy if any project SKILL is absent post-rsync,
    # so the regression cannot reach prod silently again.
    local required_skills=(
        dispatch_shift_agent handle_sick_call handle_owner_command
        handle_candidate_response roster_lookup
        catering_dispatcher parse_catering_inquiry
        handle_catering_owner_approval handle_catering_menu_finalize
        update_catering_menu apply_catering_menu_decision
    )
    if [ -d src/agents/flyer/skills ]; then
        required_skills+=(flyer_dispatcher flyer_intake flyer_generation)
    fi
    # Rollback compatibility: creative_catering_proposals is enforced by
    # post-restart smoke for forward deploys, but old rollback tarballs may
    # predate that SKILL and must still be installable before restart.
    local missing_skills=()
    for skill in "${required_skills[@]}"; do
        if [ ! -f "/root/.hermes/skills/${skill}/SKILL.md" ]; then
            missing_skills+=("$skill")
        fi
    done
    if [ ${#missing_skills[@]} -gt 0 ]; then
        echo "FATAL: required project SKILLs missing post-rsync:" >&2
        printf '  - %s\n' "${missing_skills[@]}" >&2
        echo "" >&2
        echo "This usually means /root/.hermes/skills/ contains umbrella SKILLs" >&2
        echo "(shift-agent-core, catering-management, business-operations, etc.)" >&2
        echo "from a curator consolidation that overrode the project SKILLs." >&2
        echo "" >&2
        echo "Inspect: ls /root/.hermes/skills/" >&2
        echo "Recover: rm those umbrella dirs (back them up first), re-run deploy." >&2
        return 1
    fi
    echo "✓ deploy gate: all ${#required_skills[@]} required project SKILLs present"

    # Vision-auth smoke gate (D-015) — fail-closed.
    #
    # Background (2026-05-05): step-4 model swap left auxiliary.vision in a
    # 401 state on srilu. Silent until a real catering inbound triggered the
    # flow and parse-menu-photo crashed with HTTP 401 from OpenRouter.
    #
    # The smoke fires a 1x1 JPEG at the same OpenRouter endpoint + same
    # Authorization header shape that parse-menu-photo uses, with the same
    # default model (openai/gpt-4o-mini). Exit codes:
    #   0 — vision auth working
    #   1 — auth failure (block deploy)
    #   2 — transient (script already retried internally; block deploy too)
    if [ -x /usr/local/bin/vision-auth-smoke ]; then
        # Smoke reads /opt/shift-agent/.env itself (via SHIFT_AGENT_ENV_PATH
        # or hardcoded default). We deliberately do NOT `source` the .env
        # here — bash `set -a; .` chokes on values containing em-dashes or
        # unquoted spaces (e.g. WHATSAPP_CANONICAL_REPLY="Thank you — ...").
        if /usr/local/bin/vision-auth-smoke; then
            echo "✓ deploy gate: vision auth smoke passed"
        else
            smoke_rc=$?
            echo "FATAL: vision auth smoke failed (exit $smoke_rc)" >&2
            echo "  Vision is core to the catering menu update flow." >&2
            echo "  Likely causes:" >&2
            echo "  - OPENROUTER_API_KEY missing/placeholder in /opt/shift-agent/.env" >&2
            echo "  - auxiliary.vision misconfigured in /root/.hermes/config.yaml" >&2
            echo "  - OpenRouter outage (exit 2 = transient — try again in a few minutes)" >&2
            return 1
        fi
    else
        echo "WARN: /usr/local/bin/vision-auth-smoke not installed — skipping vision-auth gate" >&2
    fi

    # Enable + start cron timers. Run daemon-reload after all per-agent units
    # are installed so fresh tarball deploys can start newly added timers.
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --now shift-agent-tail-logger.timer 2>/dev/null || true
    systemctl enable --now shift-agent-health.timer 2>/dev/null || true
    systemctl enable --now shift-agent-health-watchdog.timer 2>/dev/null || true
    systemctl enable --now shift-agent-backup.timer 2>/dev/null || true
    systemctl enable --now shift-agent-fsck.timer 2>/dev/null || true
    systemctl enable --now send-daily-brief.timer 2>/dev/null || true
    systemctl enable --now catering-pattern-report.timer 2>/dev/null || true
    systemctl enable --now eod-reconcile.timer 2>/dev/null || true
    systemctl enable --now send-routing-accuracy-summary.timer 2>/dev/null || true
    systemctl enable --now flyer-source-edit-sla-watchdog.timer 2>/dev/null || true
    systemctl enable --now prune-expense-receipts.timer 2>/dev/null || true
    # Agent #13 Compliance Calendar (PR-Agent13-v0.1)
    systemctl enable --now check-compliance-deadlines.timer 2>/dev/null || true

    # 2026-05-04 canonical-cleanup: F8/F9 watchdog files were deleted from
    # the repo (cf-router plugin took over their role in PR-CF6). The
    # earlier "disable the legacy timers" hook here is obsolete because
    # the units never get installed by this script anymore. If you're
    # rolling back to a pre-cleanup tarball that re-installs the
    # watchdogs, manually `systemctl disable --now ...timer ...service`
    # for catering-owner-action-watchdog and shift-missed-dispatch-notifier
    # — or apply this branch on top of the rollback to re-purge.
}

snapshot_staging() {
    # Snapshot current staging-new/ contents for rollback.
    # Returns the tag name on stdout.
    local commit_hash
    commit_hash=$(cat "$STAGING/.commit-hash" 2>/dev/null | head -c 8)
    [ -z "$commit_hash" ] && commit_hash="unknown"
    local tag="deploy-$(date +%Y%m%d-%H%M%S)-${commit_hash}"

    if [ -d "$STAGING/src" ]; then
        # PR-CF5: include tools/ so rollback to a CF5+ tarball preserves the
        # state-file migrator. If tools/ is missing (pre-CF5 staging), tar
        # gracefully includes only what's present via the conditional.
        if [ -d "$STAGING/tools" ]; then
            tar czf "$DEPLOYS_DIR/${tag}.tgz" -C "$STAGING" src tools .commit-hash 2>/dev/null \
                || tar czf "$DEPLOYS_DIR/${tag}.tgz" -C "$STAGING" src tools
        else
            tar czf "$DEPLOYS_DIR/${tag}.tgz" -C "$STAGING" src .commit-hash 2>/dev/null \
                || tar czf "$DEPLOYS_DIR/${tag}.tgz" -C "$STAGING" src
        fi
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

        # Hermes venv Python is used by deploy gates that need project/runtime
        # dependencies. Define it before the first Python gate so pre-install
        # checks can run without relying on system Python.
        VENV_PY="/usr/local/lib/hermes-agent/venv/bin/python"
        if [ ! -x "$VENV_PY" ]; then
            echo "ERROR: Hermes venv Python missing or not executable at $VENV_PY" >&2
            echo "  The Hermes-agent install is incomplete - verify /usr/local/lib/hermes-agent/venv/" >&2
            echo "  No state change has been made; refusing to continue deploy." >&2
            exit 1
        fi

        # ─────────────────────────────────────────────────────────────────
        # Hermes config.yaml shape gate (M2 silent-failure closure)
        # ─────────────────────────────────────────────────────────────────
        # Asserts shift-agent-load-bearing fields in /root/.hermes/config.yaml.
        # Fail-closed BEFORE any state change.
        # Override: HERMES_CONFIG_GATE_OVERRIDE_FIELD + ..._REASON (both required).
        #
        # ORDERING: this MUST precede credential-minimized foundation gate
        # because credential_readiness.validate_cf_router() reads config.yaml for
        # the plugins:* section. A YAML parse error there silently produces a
        # misleading "cf-router disabled" foundation-gate failure; running this
        # gate first surfaces the actual problem.
        if [ ! -x "$STAGING/tools/check-hermes-config-yaml.sh" ]; then
            echo "ERROR: $STAGING/tools/check-hermes-config-yaml.sh not found or not executable." >&2
            echo "  Either the tarball is malformed or a refactor moved the script." >&2
            echo "  Refusing to deploy without the config-yaml gate." >&2
            exit 1
        fi
        echo "=== Hermes config.yaml shape gate ==="
        if ! VENV_PY="$VENV_PY" BASELINE_FILE="$STAGING/tools/hermes-config-yaml-baseline.txt" \
                "$STAGING/tools/check-hermes-config-yaml.sh" /root/.hermes/config.yaml; then
            echo "ERROR: Hermes config.yaml shape gate failed — refusing to install." >&2
            echo "  No state change has been made. See gate output above for affected fields." >&2
            exit 1
        fi

        # Credential-minimized Hermes foundation gate - external Hermes install
        # state only. This runs BEFORE state-file migration and artifact install
        # because app rollback cannot repair missing bundled Hermes skills.
        # Repo-installed cf-router is intentionally NOT validated here; deploy
        # can repair that plugin during install_artifacts(), and the strict
        # plugin check runs after install but before gateway restart.
        echo "=== Credential-minimized Hermes foundation gate ==="
        if [ -f "$STAGING/src/platform/scripts/credential-minimized-readiness" ]; then
            if ! "$VENV_PY" "$STAGING/src/platform/scripts/credential-minimized-readiness" \
                    --strict-foundation --format text; then
                echo "ERROR: credential-minimized foundation gate failed - refusing to install." >&2
                echo "  No state change has been made. Restore/install missing Hermes foundation skills first." >&2
                exit 1
            fi
        else
            echo "WARN: credential-minimized-readiness absent from staging - skipping foundation gate (rollback compatibility)" >&2
        fi

        # PR-CF5 2026-05-03: state-file migration gate. Brings legacy state
        # files (e.g. {date, sent_count} send-counter.json) up to current
        # Pydantic schemas before the new code starts reading them. Bootstrap-
        # friendly: skips with WARN if migrator script absent (rollback to
        # pre-CF5 tarball compatibility). Fail-closed otherwise.
        echo "=== state-file migration check ==="
        MIGRATOR="$STAGING/tools/migrate-state-files.py"
        # Invoke with the Hermes venv Python so pydantic + safe_io + schemas
        # imports resolve. The migrator's #!/usr/bin/env python3 shebang
        # would land on system Python which lacks pydantic.
        if [ ! -x "$MIGRATOR" ]; then
            if [ -f "$MIGRATOR" ]; then
                echo "WARN: migrator exists but is not executable — permission problem? Skipping." >&2
            else
                echo "WARN: state-file migrator absent at $MIGRATOR — skipping (tarball is pre-CF5 vintage)" >&2
            fi
        else
            "$VENV_PY" "$MIGRATOR" --check
            CHECK_RC=$?
            case "$CHECK_RC" in
                0)
                    echo "OK: all state files current; no migration needed"
                    ;;
                1)
                    # Migrations needed (or malformed override) — try to apply
                    if ! "$VENV_PY" "$MIGRATOR" --apply; then
                        echo "ERROR: state-file migration apply failed — refusing to install." >&2
                        echo "  See decisions.log for state_file_migration_failed audit row + runbook" >&2
                        echo "  in tasks/runbook-state-migration.md." >&2
                        exit 1
                    fi
                    ;;
                2)
                    # Unknown shape / corrupt JSON / non-extra load failure — operator must triage
                    echo "ERROR: state-file migration check failed (rc=2: unknown shape or corrupt state)." >&2
                    echo "  Do NOT auto-apply. See decisions.log state_file_migration_failed audit row" >&2
                    echo "  + runbook tasks/runbook-state-migration.md scenario B." >&2
                    exit 1
                    ;;
                *)
                    echo "ERROR: state-file migration check returned unexpected rc=$CHECK_RC — refusing to install." >&2
                    exit 1
                    ;;
            esac
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
        # PR-CF5: include tools/ so rollback to a CF5+ tarball preserves the
        # state-file migrator. CRITICAL — without this, every CF5+ deploy writes
        # a rollback tarball missing tools/ and the next deploy's migration gate
        # WARN-skips, silently bypassing the migration on rollback.
        if [ -d "$STAGING/tools" ]; then
            tar czf "$DEPLOYS_DIR/${NEW_TAG}.tgz" -C "$STAGING" src tools .commit-hash 2>/dev/null \
                || tar czf "$DEPLOYS_DIR/${NEW_TAG}.tgz" -C "$STAGING" src tools
        else
            tar czf "$DEPLOYS_DIR/${NEW_TAG}.tgz" -C "$STAGING" src .commit-hash 2>/dev/null \
                || tar czf "$DEPLOYS_DIR/${NEW_TAG}.tgz" -C "$STAGING" src
        fi

        install_artifacts "$STAGING"

        # Pre-restart cf-router compile gate: hooks.py is imported by the
        # gateway at startup, so a syntax error can make systemctl restart
        # fail before the post-restart smoke/rollback path gets control.
        if ! "$VENV_PY" - <<'PY' > /dev/null; then
from pathlib import Path
for p in [
    Path('/root/.hermes/plugins/cf-router/actions.py'),
    Path('/root/.hermes/plugins/cf-router/hooks.py'),
]:
    compile(p.read_text(), str(p), 'exec')
PY
            echo "FAIL: pre-restart cf-router compile gate - refusing to restart hermes-gateway" >&2
            if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                "$0" rollback "$PREV_TAG"
                # Evict the broken tarball from the rotation so next deploy
                # doesn't surface it as a candidate rollback target.
                rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
            else
                /usr/local/bin/shift-agent-notify-owner \
                    --title "Deploy FAILED at pre-restart cf-router compile gate, no prior tarball" \
                    --priority 2 \
                    "Deploy $NEW_TAG failed pre-restart cf-router actions/hooks compile check. New files installed but service still on OLD code (gateway not yet restarted). No prior tarball to roll back to - SSH immediately." 2>/dev/null || true
                # Evict the broken tarball from the rotation so it isn't surfaced
                # as a rollback candidate on the next deploy.
                rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
            fi
            exit 1
        fi

        # Pre-restart cf-router enabled-state gate. Unlike external Hermes
        # foundation skills, cf-router is repo-installed by install_artifacts(),
        # so validate it only after the staged plugin has been rsynced into
        # /root/.hermes/plugins and before hermes-gateway can import it.
        if [ -x /usr/local/bin/credential-minimized-readiness ]; then
            if ! "$VENV_PY" /usr/local/bin/credential-minimized-readiness \
                    --validate-plugin cf-router --format text > /dev/null; then
                echo "FAIL: pre-restart cf-router readiness gate - refusing to restart hermes-gateway" >&2
                if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                    "$0" rollback "$PREV_TAG"
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                else
                    /usr/local/bin/shift-agent-notify-owner \
                        --title "Deploy FAILED at pre-restart cf-router readiness gate, no prior tarball" \
                        --priority 2 \
                        "Deploy $NEW_TAG failed pre-restart cf-router enabled-state check. New files installed but service still on OLD code (gateway not yet restarted). No prior tarball to roll back to - SSH immediately." 2>/dev/null || true
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                fi
                exit 1
            fi
        fi

        # Pre-restart import gate: a missing safe_io OR audit_helpers chokepoint
        # symbol means traffic hits new code BEFORE smoke fires post-restart.
        # Run the symbol-import checks against the just-installed binary (still
        # old service) — failure path rolls back without touching live traffic.
        # PR-D1 R3-H-Gate1: chained check-audit-helpers-symbols.
        # Both gate scripts use #!/usr/bin/env python3 (system Python, no
        # pydantic). Invoke through the Hermes venv so schemas import works.
        if ! "$VENV_PY" /usr/local/bin/check-safe-io-symbols > /dev/null \
              || ! "$VENV_PY" /usr/local/bin/check-audit-helpers-symbols > /dev/null; then
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

        # Hermes runtime permission gate: run the same targeted preflight that
        # hermes-gateway.service runs at ExecStartPre. This catches ownership
        # issues before restart and avoids the old broad recursive chown over
        # all of /root/.hermes, which could be blocked by stale backup files.
        if ! /usr/local/bin/shift-agent-hermes-permissions > /dev/null; then
            echo "FAIL: Hermes runtime permissions gate — refusing to restart hermes-gateway" >&2
            if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                "$0" rollback "$PREV_TAG"
                rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
            else
                /usr/local/bin/shift-agent-notify-owner \
                    --title "Deploy FAILED at Hermes permissions gate" \
                    --priority 2 \
                    "Deploy $NEW_TAG failed before gateway restart because Hermes runtime permissions are invalid. SSH immediately." 2>/dev/null || true
                rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
            fi
            exit 1
        fi

        # Restart services (in order: tail-logger first, gateway last)
        systemctl restart shift-agent-tail-logger.timer 2>/dev/null || true
        systemctl restart shift-agent-health.timer 2>/dev/null || true
        systemctl restart hermes-gateway
        # Cockpit holds Pydantic models in memory; install_artifacts() above
        # replaced /opt/shift-agent/schemas.py + safe_io.py + shared modules,
        # but the cockpit's long-running uvicorn process keeps the OLD modules
        # loaded until restart. Skipping this step caused the 2026-05-19
        # incident where /flyer/customers returned 500 (Pydantic
        # ValidationError on a new FlyerWorkflowStatus value) until a manual
        # restart cleared the stale module cache.
        #
        # Unit-presence-gated so VPSes without the cockpit installed aren't
        # affected. Inside the gate: restart + /health probe so a real cockpit
        # failure fails the deploy + rolls back instead of being masked by
        # `|| true` — the silent-failure mode this hook exists to prevent.
        # Do not use `systemctl restart --wait` here: on main-vps it can hang
        # even after the unit is active and no jobs remain; the HTTP health
        # probe below is the readiness check.
        if systemctl list-unit-files shift-agent-cockpit.service >/dev/null 2>&1; then
            cockpit_fail_reason=""
            if ! systemctl restart shift-agent-cockpit.service; then
                cockpit_fail_reason="restart"
            else
                cockpit_healthy=0
                for _ in 1 2 3 4 5; do
                    if curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8081/health; then
                        cockpit_healthy=1
                        break
                    fi
                    sleep 1
                done
                [ "$cockpit_healthy" -ne 1 ] && cockpit_fail_reason="health probe"
                # Per-route mount probe for the manual-queue surface. The route
                # uses a conditional import (flyer_manual_queue vs
                # agents.flyer.manual_queue) that fails silently if either
                # module is missing — /health alone wouldn't catch that.
                # 401/403 here is success: it proves the route is mounted and
                # the import resolved; connection-refused or 5xx is the fail
                # mode we care about. Run only after /health passed so we
                # don't mask a plain restart fail.
                #
                # URL note (S2 deploy regression, fixed here): the cockpit
                # uvicorn at port 8081 serves routes at /flyer/... directly.
                # The /api/ prefix is added externally by Caddy when proxying
                # browser requests; it is NOT part of the uvicorn path. The
                # initial S2 deploy script used /api/flyer/manual-queue and
                # would return 404 on every subsequent deploy, blocking it.
                if [ "$cockpit_healthy" -eq 1 ] && [ -z "$cockpit_fail_reason" ]; then
                    manual_queue_code=$(curl -s -o /dev/null --max-time 2 -w '%{http_code}' http://127.0.0.1:8081/flyer/manual-queue || echo "000")
                    case "$manual_queue_code" in
                        200|401|403)
                            : # route mounted (auth gate is the only thing in our way)
                            ;;
                        *)
                            cockpit_fail_reason="manual-queue route probe (got $manual_queue_code)"
                            ;;
                    esac
                fi
            fi
            if [ -n "$cockpit_fail_reason" ]; then
                echo "FAIL: cockpit $cockpit_fail_reason failed after restart — rolling back" >&2
                if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                    "$0" rollback "$PREV_TAG"
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                else
                    /usr/local/bin/shift-agent-notify-owner \
                        --title "Deploy FAILED at cockpit $cockpit_fail_reason, no prior tarball" \
                        --priority 2 \
                        "Deploy $NEW_TAG: cockpit $cockpit_fail_reason after restart. SSH immediately." 2>/dev/null || true
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                fi
                exit 1
            fi
        fi
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

        # Hermes config.yaml shape gate on rollback path. Asymmetric posture
        # from the deploy action: WARN-skip-when-missing IS appropriate here
        # because rollback tarballs may legitimately predate the gate (deploy
        # tarballs do not — they're always freshly built from current repo).
        # Without this, a broken /root/.hermes/config.yaml (operator manually
        # edited after the prior deploy) would only be caught by smoke AFTER
        # gateway is restarted with the degraded config.
        VENV_PY="${VENV_PY:-/usr/local/lib/hermes-agent/venv/bin/python}"
        if [ -x "$STAGING/tools/check-hermes-config-yaml.sh" ]; then
            echo "=== Hermes config.yaml shape gate (rollback) ==="
            if ! VENV_PY="$VENV_PY" BASELINE_FILE="$STAGING/tools/hermes-config-yaml-baseline.txt" \
                    "$STAGING/tools/check-hermes-config-yaml.sh" /root/.hermes/config.yaml; then
                echo "ERROR: rollback config.yaml gate failed — config is broken." >&2
                echo "  This rollback would result in service restart against broken config." >&2
                echo "  To force rollback: set HERMES_CONFIG_GATE_OVERRIDE_FIELD + ..._REASON." >&2
                /usr/local/bin/shift-agent-notify-owner \
                    --priority 2 \
                    --title "Rollback BLOCKED by config.yaml gate" \
                    "Rollback to $TARGET refused: /root/.hermes/config.yaml has shape issues. SSH to triage." 2>/dev/null || true
                exit 1
            fi
        else
            echo "WARN: rollback tarball lacks config-yaml gate — proceeding (pre-merge tarball compat)" >&2
        fi

        # Re-install from restored staging
        install_artifacts "$STAGING"

        systemctl restart shift-agent-tail-logger.timer 2>/dev/null || true
        systemctl restart hermes-gateway
        # Cockpit must pick up the rolled-back schemas.py / safe_io.py too —
        # otherwise it stays on the (broken-forward) module cache. See the
        # deploy-path comment above for the failure mode that motivated this.
        # Failure here is reported loudly (Pushover P2 + exit 1) rather than
        # cascaded into another rollback — we're already in rollback.
        if systemctl list-unit-files shift-agent-cockpit.service >/dev/null 2>&1; then
            cockpit_fail_reason=""
            if ! systemctl restart shift-agent-cockpit.service; then
                cockpit_fail_reason="restart"
            else
                cockpit_healthy=0
                for _ in 1 2 3 4 5; do
                    if curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8081/health; then
                        cockpit_healthy=1
                        break
                    fi
                    sleep 1
                done
                [ "$cockpit_healthy" -ne 1 ] && cockpit_fail_reason="health probe"
            fi
            if [ -n "$cockpit_fail_reason" ]; then
                /usr/local/bin/shift-agent-notify-owner \
                    --priority 2 \
                    --title "Rollback to $TARGET — cockpit $cockpit_fail_reason failed" \
                    "Rolled back to $TARGET but shift-agent-cockpit $cockpit_fail_reason failed. Cockpit may be down or on stale modules. SSH immediately." 2>/dev/null || true
                exit 1
            fi
        fi
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
