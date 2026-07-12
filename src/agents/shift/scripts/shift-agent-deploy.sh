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
    if [ ! -f src/platform/scripts/check-skills-manifest ]; then
        rm -f /usr/local/bin/check-skills-manifest
    fi
    if [ ! -f src/platform/scripts/check-commerce-webhook-subscription ]; then
        rm -f /usr/local/bin/check-commerce-webhook-subscription
    fi
    if [ ! -f src/platform/scripts/check-commerce-stripe-livemode ]; then
        rm -f /usr/local/bin/check-commerce-stripe-livemode
    fi

    # Python modules — flat layout at /opt/shift-agent/ matches scripts' sys.path
    install -m 644 src/platform/schemas.py /opt/shift-agent/schemas.py
    install -m 644 src/platform/safe_io.py /opt/shift-agent/safe_io.py
    # No-response escalation sweep logic (imported by shift-agent-proposal-sweep). Guarded for
    # rollback compatibility with tarballs that predate this module.
    if [ -f src/platform/proposal_sweep.py ]; then
        install -m 644 src/platform/proposal_sweep.py /opt/shift-agent/proposal_sweep.py
    else
        # Rollback to a pre-sweep tarball: tear down the WHOLE no-response-sweep surface so a
        # lingering enabled timer can't fire a script whose module was just removed
        # (ModuleNotFoundError every 15 min). Mirrors the flyer / catering-dispatcher-watchdog
        # per-unit rollback cleanup.
        systemctl disable --now shift-agent-proposal-sweep.timer 2>/dev/null || true
        rm -f /opt/shift-agent/proposal_sweep.py \
              /usr/local/bin/shift-agent-proposal-sweep \
              /etc/systemd/system/shift-agent-proposal-sweep.service \
              /etc/systemd/system/shift-agent-proposal-sweep.timer
        systemctl daemon-reload 2>/dev/null || true
    fi
    install -m 644 src/platform/sender_context.py /opt/shift-agent/sender_context.py
    install -m 644 src/platform/exit_codes.py /opt/shift-agent/exit_codes.py
    install -m 644 src/platform/log_source.py /opt/shift-agent/log_source.py
    # PR-D1: audit_helpers.py — best-effort emitters for config_load_failed
    # + catering_quote_sent_lead_missing. Pre-restart gate
    # check-audit-helpers-symbols imports this module; missing here =
    # forced rollback on every deploy.
    install -m 644 src/platform/audit_helpers.py /opt/shift-agent/audit_helpers.py
    # CD v2 rollback safety — flyer_store_maintenance.py provides scrub_store_file,
    # invoked by the pre-restart scrub step below to strip any lingering
    # `creative_direction` keys from the flyer project store (durable rollback
    # guarantee independent of serialization behavior). Installed flat so the
    # scrub step's `from flyer_store_maintenance import scrub_store_file` resolves.
    # GUARDED for rollback (Codex BLOCKER A): rolling back to an older tarball that
    # predates this module must not fail install_artifacts mid-rollback — install
    # when present, else remove any stale copy on the box so the scrub step (also
    # guarded) skips cleanly.
    if [ -f src/platform/flyer_store_maintenance.py ]; then
        install -m 644 src/platform/flyer_store_maintenance.py /opt/shift-agent/flyer_store_maintenance.py
    else
        rm -f /opt/shift-agent/flyer_store_maintenance.py
    fi
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
    # Skills-integrity manifest module. The /opt copy backs the D1 gate's CLI wrapper
    # (check-skills-manifest, which _add_import_roots-imports it). Guarded for rollback.
    if [ -f src/platform/skills_manifest.py ]; then
        install -m 644 src/platform/skills_manifest.py /opt/shift-agent/skills_manifest.py
    else
        rm -f /opt/shift-agent/skills_manifest.py
    fi
    # D2 trust-domain hardening (PR #583 security follow-up): the ROOT-run watchdog reads its
    # module + manifest + allowlist + critical-list ONLY from this root-owned dir. It is owned
    # root:root (DAC) AND under /usr, which the gateway's ProtectSystem=strict makes read-only
    # (MAC) — so a shift-agent-uid adversary cannot poison the checker or its inputs (closes the
    # #583 env / throttle / manifest / checker-code poisoning bypasses). These are specific-file
    # installs into a root-owned NON-bin dir; the refined R4-H-2 test allows this (it forbids
    # only tools/* globs, tools/->/usr/local/bin, and the synthetic-retry-harness). Guarded.
    install -d -m 755 /usr/local/share/shift-agent
    if [ -f src/platform/skills_manifest.py ]; then
        install -m 644 src/platform/skills_manifest.py /usr/local/share/shift-agent/skills_manifest.py
    else
        rm -f /usr/local/share/shift-agent/skills_manifest.py
    fi
    if [ -f tools/skills-manifest.txt ]; then
        install -m 644 tools/skills-manifest.txt /usr/local/share/shift-agent/skills-manifest.txt
    else
        rm -f /usr/local/share/shift-agent/skills-manifest.txt
    fi
    if [ -f tools/skills-foundation-allowlist.txt ]; then
        install -m 644 tools/skills-foundation-allowlist.txt /usr/local/share/shift-agent/skills-foundation-allowlist.txt
    else
        rm -f /usr/local/share/shift-agent/skills-foundation-allowlist.txt
    fi
    if [ -f tools/skills-critical.txt ]; then
        install -m 644 tools/skills-critical.txt /usr/local/share/shift-agent/skills-critical.txt
    else
        rm -f /usr/local/share/shift-agent/skills-critical.txt
    fi
    # Commerce webhook-subscription deploy-gate module (slice-3.5). Imported by
    # the check-commerce-webhook-subscription wrapper. Guarded for rollback
    # compatibility with tarballs that predate this module.
    if [ -f src/platform/commerce_webhook_gate.py ]; then
        install -m 644 src/platform/commerce_webhook_gate.py /opt/shift-agent/commerce_webhook_gate.py
    else
        rm -f /opt/shift-agent/commerce_webhook_gate.py
    fi
    # Commerce Stripe livemode-match deploy-gate module (slice-3.1). Imported by
    # the check-commerce-stripe-livemode wrapper. Guarded for rollback
    # compatibility with tarballs that predate this module.
    if [ -f src/platform/commerce_livemode_gate.py ]; then
        install -m 644 src/platform/commerce_livemode_gate.py /opt/shift-agent/commerce_livemode_gate.py
    else
        rm -f /opt/shift-agent/commerce_livemode_gate.py
    fi

    # Commerce primitives package (PR #321, slice 1 library-only).
    # Guarded for rollback compatibility with tarballs that predate the package.
    # Library-only: no scripts here, no dispatcher row, no caller wiring —
    # modules sit dormant on disk until slice-2 callers wire them. Schema +
    # LogEntry additions ship via schemas.py install above.
    if [ -d src/platform/commerce ]; then
        install -d /opt/shift-agent/commerce
        install -m 644 src/platform/commerce/*.py /opt/shift-agent/commerce/
    else
        rm -rf /opt/shift-agent/commerce
    fi

    # Catering deposit helper module (PR feat/commerce-slice2-catering-deposit-caller).
    # catering-mint-deposit + apply-catering-owner-decision's deposit hook both
    # import `deposit._should_mint_deposit` etc. Without this install line the
    # imports silently fail on the VPS (script paths are /usr/local/bin/, not
    # the repo layout) — PR reviewer B-BLOCKER-1 fix. Guarded for rollback.
    if [ -f src/agents/catering/deposit.py ]; then
        install -m 644 src/agents/catering/deposit.py /opt/shift-agent/deposit.py
    else
        rm -f /opt/shift-agent/deposit.py
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
    # Platform *.timer units (alert-integrity-watchdog, check-corrupt-state) —
    # previously the platform block installed only *.service, so timers shipped
    # in-repo but never landed on the box (built-but-never-installed; census A3/C4b).
    install -m 644 src/platform/systemd/*.timer /etc/systemd/system/ 2>/dev/null || true
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
        # TZ templating: read cfg.customer.timezone through the Hermes venv,
        # not system python. If extraction fails, warn before using the
        # reference-customer fallback timezone.
        customer_tz=$("${VENV_PY:-/usr/local/lib/hermes-agent/venv/bin/python}" - <<'PY' 2>/dev/null || true
import yaml
with open('/opt/shift-agent/config.yaml', encoding='utf-8') as f:
    print(yaml.safe_load(f)['customer']['timezone'])
PY
)
        if [ -z "$customer_tz" ]; then
            echo "WARN: unable to read customer.timezone from /opt/shift-agent/config.yaml via Hermes venv; defaulting compliance timers to America/New_York" >&2
            customer_tz="America/New_York"
        fi
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
    # CD v2 brain SKILL → the ACTUAL runtime read path. flyer_context_builder.py
    # (the Creative-Director brain) reads its governing system prompt from
    # ``Path(__file__).resolve().parent / "skills" / "flyer_generation" / "SKILL.md"``
    # — i.e. /opt/shift-agent/skills/flyer_generation/SKILL.md, NOT the Hermes
    # dispatch copy under /root/.hermes/skills/ that the rsync above installs.
    # Before 2026-06-21 the deploy never refreshed this path, so the brain read a
    # stale pre-CD-v2 SKILL and could never emit campaign_narrative / hero_ref /
    # marketing_hook / offer_priority (the live render came out headline-less and
    # the cause was mis-attributed to brain nondeterminism). Install it to the
    # brain's read path, mirroring the flat-module pattern below. The post-restart
    # smoke gate (shift-agent-smoke-test.sh) asserts the installed copy carries the
    # CD v2 fields so this can never silently regress again.
    if [ -f src/agents/flyer/skills/flyer_generation/SKILL.md ]; then
        install -d -m 755 /opt/shift-agent/skills/flyer_generation
        install -m 644 src/agents/flyer/skills/flyer_generation/SKILL.md /opt/shift-agent/skills/flyer_generation/SKILL.md
    fi
    if [ -f src/agents/flyer/render.py ]; then
        install -m 644 src/agents/flyer/render.py /opt/shift-agent/flyer_render.py
    else
        rm -f /opt/shift-agent/flyer_render.py
    fi
    # Fix C premium deterministic renderer. Flat-renamed to match the
    # try/except import convention (flyer_render.py, flyer_visual_qa.py, etc.):
    # render.py + generate-flyer-concepts import it as `flyer_premium_overlay`.
    # The font bundle below MUST be installed alongside it (premium_overlay's
    # _FONT_DIR resolves /opt/shift-agent/fonts on the flat box).
    if [ -f src/agents/flyer/premium_overlay.py ]; then
        install -m 644 src/agents/flyer/premium_overlay.py /opt/shift-agent/flyer_premium_overlay.py
    else
        rm -f /opt/shift-agent/flyer_premium_overlay.py
    fi
    # Premium font bundle (SIL OFL 1.1 vendored TTFs + FONTS.md). Installed to
    # /opt/shift-agent/fonts/ — the flat-layout location premium_overlay._FONT_DIR
    # checks. Without these the premium renderer silently degrades to system
    # DejaVu / Pillow default, defeating Fix C's typography. Guarded for rollback
    # compatibility with tarballs that predate the bundle. Includes the
    # brush-script headline face Pacifico-Regular.ttf (festive-vernacular
    # register, Workstream B) — the *.ttf glob installs it, and the smoke
    # font-gate (shift-agent-smoke-test.sh, driven by _ROLE_FILES.values())
    # asserts it present + loadable, matching the Fix C precedent.
    if [ -d src/agents/flyer/fonts ] && compgen -G "src/agents/flyer/fonts/*.ttf" > /dev/null; then
        install -d -m 755 /opt/shift-agent/fonts
        install -m 644 src/agents/flyer/fonts/*.ttf /opt/shift-agent/fonts/
        if [ -f src/agents/flyer/fonts/FONTS.md ]; then
            install -m 644 src/agents/flyer/fonts/FONTS.md /opt/shift-agent/fonts/FONTS.md
        fi
    else
        rm -rf /opt/shift-agent/fonts
    fi
    if [ -f src/agents/flyer/repair.py ]; then
        install -m 644 src/agents/flyer/repair.py /opt/shift-agent/flyer_repair.py
    else
        rm -f /opt/shift-agent/flyer_repair.py
    fi
    if [ -f src/agents/flyer/intake_fields.py ]; then
        install -m 644 src/agents/flyer/intake_fields.py /opt/shift-agent/flyer_intake_fields.py
    else
        rm -f /opt/shift-agent/flyer_intake_fields.py
    fi
    if [ -f src/agents/flyer/bare_render.py ]; then
        install -m 644 src/agents/flyer/bare_render.py /opt/shift-agent/flyer_bare_render.py
    else
        rm -f /opt/shift-agent/flyer_bare_render.py
    fi
    if [ -f src/agents/flyer/campaign_scene_prompts.py ]; then
        install -m 644 src/agents/flyer/campaign_scene_prompts.py /opt/shift-agent/flyer_campaign_scene_prompts.py
    else
        rm -f /opt/shift-agent/flyer_campaign_scene_prompts.py
    fi
    # Premium Poster v1 stack (flag-gated render integration, PR #523). Imported by
    # flyer_render.py via the flat names below; the modules carry try-flat/except-
    # package shims for their sibling imports (premium_overlay/campaign_scene_prompts/
    # premium_poster_v1/flyer_art_director_oracle). Guarded for rollback compatibility.
    # Style registers (graduation commit 1; flat name matches the lazy import
    # `from style_registers import ...` in render.py + visual_qa.py).
    if [ -f src/agents/flyer/style_registers.py ]; then
        install -m 644 src/agents/flyer/style_registers.py /opt/shift-agent/style_registers.py
    else
        rm -f /opt/shift-agent/style_registers.py
    fi
    if [ -f src/agents/flyer/extraction_v2.py ]; then
        install -m 644 src/agents/flyer/extraction_v2.py /opt/shift-agent/flyer_extraction_v2.py
    else
        rm -f /opt/shift-agent/flyer_extraction_v2.py
    fi
    if [ -f src/agents/flyer/extraction_seam.py ]; then
        install -m 644 src/agents/flyer/extraction_seam.py /opt/shift-agent/flyer_extraction_seam.py
    else
        rm -f /opt/shift-agent/flyer_extraction_seam.py
    fi
    # Shared OpenRouter key resolution (census C9) — reference_extract / semantic_brief /
    # visual_qa import it flat as `flyer_openrouter_env`. Must install before/with them;
    # guarded for rollback so an older tarball (which has the inlined copies, not this
    # import) removes the flat module cleanly.
    if [ -f src/agents/flyer/openrouter_env.py ]; then
        install -m 644 src/agents/flyer/openrouter_env.py /opt/shift-agent/flyer_openrouter_env.py
    else
        rm -f /opt/shift-agent/flyer_openrouter_env.py
    fi
    if [ -f src/agents/flyer/premium_poster_v1.py ]; then
        install -m 644 src/agents/flyer/premium_poster_v1.py /opt/shift-agent/flyer_premium_poster_v1.py
    else
        rm -f /opt/shift-agent/flyer_premium_poster_v1.py
    fi
    if [ -f src/agents/flyer/premium_poster_v1_director.py ]; then
        install -m 644 src/agents/flyer/premium_poster_v1_director.py /opt/shift-agent/flyer_premium_poster_v1_director.py
    else
        rm -f /opt/shift-agent/flyer_premium_poster_v1_director.py
    fi
    if [ -f src/agents/flyer/flyer_art_director_oracle.py ]; then
        install -m 644 src/agents/flyer/flyer_art_director_oracle.py /opt/shift-agent/flyer_art_director_oracle.py
    else
        rm -f /opt/shift-agent/flyer_art_director_oracle.py
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
    # Quarantine-before-recovery chokepoint (F0197/F0208): imported flat by
    # generate-flyer-concepts + flyer_bare_render before any recovery rung
    # overwrites a QA-failed preview artifact.
    if [ -f src/agents/flyer/quarantine.py ]; then
        install -m 644 src/agents/flyer/quarantine.py /opt/shift-agent/flyer_quarantine.py
    else
        rm -f /opt/shift-agent/flyer_quarantine.py
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
    if [ -f src/agents/flyer/action_registry.py ]; then
        install -m 644 src/agents/flyer/action_registry.py /opt/shift-agent/flyer_action_registry.py
    else
        rm -f /opt/shift-agent/flyer_action_registry.py
    fi
    if [ -f src/agents/flyer/payment_state.py ]; then
        install -m 644 src/agents/flyer/payment_state.py /opt/shift-agent/flyer_payment_state.py
    else
        rm -f /opt/shift-agent/flyer_payment_state.py
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
    # creative_planner RETIRED (graduation commit 6): inert-by-construction
    # for its whole life. Conditional kept for rollback to pre-removal
    # tarballs still in rotation (the grad5 F1 standing pattern).
    if [ -f src/agents/flyer/creative_planner.py ]; then
        install -m 644 src/agents/flyer/creative_planner.py /opt/shift-agent/flyer_creative_planner.py
    else
        rm -f /opt/shift-agent/flyer_creative_planner.py
    fi
    if [ -f src/agents/flyer/creative_firewall.py ]; then
        install -m 644 src/agents/flyer/creative_firewall.py /opt/shift-agent/flyer_creative_firewall.py
    else
        rm -f /opt/shift-agent/flyer_creative_firewall.py
    fi
    if [ -f src/agents/flyer/reference_extract.py ]; then
        install -m 644 src/agents/flyer/reference_extract.py /opt/shift-agent/flyer_reference_extract.py
    else
        rm -f /opt/shift-agent/flyer_reference_extract.py
    fi
    if [ -f src/agents/flyer/semantic_brief.py ]; then
        install -m 644 src/agents/flyer/semantic_brief.py /opt/shift-agent/flyer_semantic_brief.py
    else
        rm -f /opt/shift-agent/flyer_semantic_brief.py
    fi
    if [ -f src/agents/flyer/visual_qa.py ]; then
        install -m 644 src/agents/flyer/visual_qa.py /opt/shift-agent/flyer_visual_qa.py
    else
        rm -f /opt/shift-agent/flyer_visual_qa.py
    fi
    # CD v2 module chain (source files already carry the flyer_ prefix):
    # flyer_render imports propose_creative_brief_v2 (flyer_context_builder) +
    # resolve_creative_direction (flyer_creative_resolver); both need
    # flyer_brief + flyer_brief_validator. Install at the deployed version so the
    # render path's imports resolve (else the smoke module-import probe fails).
    if [ -f src/agents/flyer/flyer_brief.py ]; then
        install -m 644 src/agents/flyer/flyer_brief.py /opt/shift-agent/flyer_brief.py
    else
        rm -f /opt/shift-agent/flyer_brief.py
    fi
    if [ -f src/agents/flyer/flyer_brief_validator.py ]; then
        install -m 644 src/agents/flyer/flyer_brief_validator.py /opt/shift-agent/flyer_brief_validator.py
    else
        rm -f /opt/shift-agent/flyer_brief_validator.py
    fi
    if [ -f src/agents/flyer/flyer_context_builder.py ]; then
        install -m 644 src/agents/flyer/flyer_context_builder.py /opt/shift-agent/flyer_context_builder.py
    else
        rm -f /opt/shift-agent/flyer_context_builder.py
    fi
    if [ -f src/agents/flyer/flyer_creative_resolver.py ]; then
        install -m 644 src/agents/flyer/flyer_creative_resolver.py /opt/shift-agent/flyer_creative_resolver.py
    else
        rm -f /opt/shift-agent/flyer_creative_resolver.py
    fi
    # Retired 2026-07-04 (graduation commit 5): the narrative referee is
    # redundant under CCA — the firewall owns safety/grounding, the composer
    # owns priority order; the referee blacklists CCA's own combo template and
    # its scoring would demote the designed offer-explicit-first ordering.
    # ROLLBACK HYGIENE (PR #546 review F1): the install branch exists ONLY for
    # rollback to pre-#546 tarballs still in rotation (KEEP_TARBALLS window) —
    # the old resolver top-imports this module and a bare rm -f would leave a
    # restored tree broken (terminal, since rollback-of-rollback is refused).
    # Dead code on forward deploys (the repo file no longer exists).
    if [ -f src/agents/flyer/flyer_narrative_quality.py ]; then
        install -m 644 src/agents/flyer/flyer_narrative_quality.py /opt/shift-agent/flyer_narrative_quality.py
    else
        rm -f /opt/shift-agent/flyer_narrative_quality.py
    fi
    # Controlled Copy Archetypes (CCA). flyer_creative_resolver hard-imports
    # compose_archetype_headlines from flyer_copy_archetypes at module load, so it MUST
    # be installed at the deployed flat path or the resolver import raises ImportError
    # and breaks CD v2 campaign-narrative composition (the smoke module-import probe
    # asserts this module loads).
    if [ -f src/agents/flyer/flyer_copy_archetypes.py ]; then
        install -m 644 src/agents/flyer/flyer_copy_archetypes.py /opt/shift-agent/flyer_copy_archetypes.py
    else
        rm -f /opt/shift-agent/flyer_copy_archetypes.py
    fi
    # CD v2 Composition Phase 1: the poster-archetype router. flyer_render guards
    # this import (falls back to message_first if absent), but install it so the
    # message_first (A) overlay template is actually reachable on the box.
    if [ -f src/agents/flyer/flyer_poster_archetype.py ]; then
        install -m 644 src/agents/flyer/flyer_poster_archetype.py /opt/shift-agent/flyer_poster_archetype.py
    else
        rm -f /opt/shift-agent/flyer_poster_archetype.py
    fi
    if [ -f src/agents/flyer/visible_contract.py ]; then
        install -m 644 src/agents/flyer/visible_contract.py /opt/shift-agent/flyer_visible_contract.py
    else
        rm -f /opt/shift-agent/flyer_visible_contract.py
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
            set-flyer-brand-asset-state \
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
            bare-flyer-render-and-send \
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
            /usr/local/bin/set-flyer-brand-asset-state \
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
            /usr/local/bin/bare-flyer-render-and-send \
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

    # Deploy gate — SKILL.md CONTENT check (pairs with the presence gate above).
    # Presence proves the SKILL.md file exists; this proves its CONTENT matches the shipped
    # sha256 manifest (tools/skills-manifest.txt), closing the silent-mutation gap where a
    # self-writing Hermes ("smarter memory edits" / curator consolidation) or an in-place
    # rewrite of a deployed SKILL.md would pass presence unnoticed. Rollback-safe: the gate
    # itself skips (WARN, not FAIL) if the manifest/helper predates this tarball.
    if [ -f tools/check-skills-manifest.sh ]; then
        if ! SKILLS_ROOT=/root/.hermes/skills bash tools/check-skills-manifest.sh verify; then
            echo "FATAL: skills-manifest content gate failed — a deployed SKILL.md drifted from" >&2
            echo "  the shipped manifest (self-writing Hermes / manual edit / stale manifest)." >&2
            echo "  Inspect: ls -la /root/.hermes/skills/ ; diff against src/agents/*/skills/." >&2
            return 1
        fi
    fi

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
    # shift-agent-proposal-sweep.timer — the no-response escalation sweep. Enable ONLY on a tarball
    # that ships the sweep (guards a rollback from re-enabling a timer whose script was removed
    # above). The timer is a harmless ~15-min config-read no-op while the sweep is GATED by
    # limits.no_response_sweep_enabled (ships FALSE). To ACTIVATE: set
    # `limits.no_response_sweep_enabled: true` in config.yaml — no redeploy, no restart; the sweep
    # re-reads config on its next fire (<=15 min). Owner then gets an alert when a candidate goes silent.
    if [ -f src/platform/proposal_sweep.py ]; then
        systemctl enable --now shift-agent-proposal-sweep.timer 2>/dev/null || true
    fi
    # shift-agent-skills-audit.timer ships INSTALLED-BUT-DISABLED (unit installed by the
    # wildcard at :208, but NOT enabled here). The watchdog now runs as ROOT reading root-owned
    # inputs (trust-domain hardening), so it IS adversary-resistant for DETECTION — but the
    # on-box flat foundation-skill layout is still unverified, so auto-enabling risks a false
    # first `extra` alert (eroding §12b alert trust). The root-run D1 deploy CONTENT gate above
    # protects every deploy regardless. Operator enables D2 after validating the allowlist on-box:
    #   ls /root/.hermes/skills/  # confirm no legit FLAT bundled skills; if any, add them to
    #   tools/skills-foundation-allowlist.txt and redeploy; then:
    #   systemctl enable --now shift-agent-skills-audit.timer
    systemctl disable shift-agent-skills-audit.timer 2>/dev/null || true
    systemctl enable --now shift-agent-backup.timer 2>/dev/null || true
    systemctl enable --now shift-agent-fsck.timer 2>/dev/null || true
    systemctl enable --now send-daily-brief.timer 2>/dev/null || true
    systemctl enable --now catering-pattern-report.timer 2>/dev/null || true
    # F8 catering-owner-action-watchdog (restored 2026-07-11, census C2). Long-
    # running poller shipped service-only (see the unit comment); enable + start
    # it here like the sibling timers. `--now` starts it; on a box without
    # owner.self_chat_jid configured it logs a WARN and no-ops until configured.
    systemctl enable --now catering-owner-action-watchdog.service 2>/dev/null || true
    systemctl enable --now eod-reconcile.timer 2>/dev/null || true
    systemctl enable --now send-routing-accuracy-summary.timer 2>/dev/null || true
    systemctl enable --now flyer-source-edit-sla-watchdog.timer 2>/dev/null || true
    # Platform alert-integrity watchdog (census A3/C3): decisions.log freshness
    # (§12a hole) + notify-failed.log dead-letter growth (§12b). Idempotent on
    # redeploy — enable --now is a no-op on an already-enabled timer.
    systemctl enable --now alert-integrity-watchdog.timer 2>/dev/null || true
    # Corrupt-state quarantine watchdog (census C4b): units shipped on main since
    # #579 but the platform block installed only *.service, so this timer never
    # landed/enabled on the box (systemctl cat returned not-found). Now installed
    # by the platform *.timer line above; enable it here. Idempotent on redeploy.
    systemctl enable --now check-corrupt-state.timer 2>/dev/null || true
    systemctl enable --now prune-expense-receipts.timer 2>/dev/null || true
    # Agent #13 Compliance Calendar (PR-Agent13-v0.1)
    systemctl enable --now check-compliance-deadlines.timer 2>/dev/null || true
    # openrouter-balance-check.timer is DELIBERATELY not enabled here — the
    # unit files are installed above but arming is a one-time operator step:
    #   systemctl daemon-reload && systemctl enable --now openrouter-balance-check.timer
    # (2026-07-06 ops-hardening; see the timer unit's comment.)

    # 2026-05-04 canonical-cleanup: F8/F9 watchdog files were deleted from the
    # repo (cf-router plugin was meant to take over in PR-CF6). F8
    # catering-owner-action-watchdog was RESTORED 2026-07-11 (census C2) and is
    # now installed (glob at :350/:356) + enabled above as a first-class
    # service-only unit. F9 shift-missed-dispatch-notifier remains removed; if
    # you roll back to a pre-cleanup tarball that re-installs IT, manually
    # `systemctl disable --now shift-missed-dispatch-notifier.{timer,service}`.
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

active_flyer_generation_pids() {
    pgrep -f '/usr/local/bin/generate-flyer-concepts|/usr/local/bin/finalize-flyer-assets|/usr/local/bin/send-flyer-package' 2>/dev/null || true
}

wait_for_flyer_generation_drain() {
    local timeout_sec="${FLYER_DEPLOY_DRAIN_TIMEOUT_SEC:-900}"
    local poll_sec="${FLYER_DEPLOY_DRAIN_POLL_SEC:-10}"
    local elapsed=0
    local pids=""

    while true; do
        pids=$(active_flyer_generation_pids | tr '
' ' ' | sed 's/[[:space:]]*$//')
        if [ -z "$pids" ]; then
            return 0
        fi
        if [ "$elapsed" -ge "$timeout_sec" ]; then
            echo "FAIL: active Flyer generation still running after ${timeout_sec}s; refusing gateway restart" >&2
            # shellcheck disable=SC2086  # intentionally expands PID list for ps.
            ps -fp $pids >&2 || true
            return 1
        fi
        echo "Waiting for active Flyer generation before gateway restart: pids=$pids elapsed=${elapsed}s/${timeout_sec}s"
        sleep "$poll_sec"
        elapsed=$((elapsed + poll_sec))
    done
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
        # Hermes venv import smoke (Pillow-regression class)
        # ─────────────────────────────────────────────────────────────────
        # The venv is owned by the hermes-agent install: a venv rebuild /
        # Hermes upgrade silently drops packages shift-agent needs but Hermes
        # does not (Pillow vanished this way; premium overlay degraded to
        # flat with no import error surfaced anywhere). Smoke-import the
        # requirements-hermes-venv.txt set through the TARGET interpreter,
        # fail-closed BEFORE any state change.
        # Override: HERMES_VENV_IMPORT_GATE_OVERRIDE_REASON (non-empty) skips
        # the hard gate (still prints the failure) — for a conscious deploy
        # onto a box mid-provisioning.
        if ! "$VENV_PY" -c "import PIL, PIL.Image, PIL.ImageDraw, PIL.ImageFont, pydantic, yaml" 2>&1; then
            if [ -n "${HERMES_VENV_IMPORT_GATE_OVERRIDE_REASON:-}" ]; then
                echo "WARN: Hermes venv import smoke FAILED but overridden: ${HERMES_VENV_IMPORT_GATE_OVERRIDE_REASON}" >&2
            else
                echo "ERROR: Hermes venv is missing required imports (PIL/pydantic/yaml)." >&2
                echo "  Reprovision: $VENV_PY -m pip install -r $STAGING/requirements-hermes-venv.txt" >&2
                echo "  No state change has been made; refusing to continue deploy." >&2
                exit 1
            fi
        fi
        # stripe is commerce-only (lazily imported; unarmed VPSes never load
        # it) — WARN, not fail-closed, so non-commerce boxes keep deploying.
        if ! "$VENV_PY" -c "import stripe" 2>/dev/null; then
            echo "WARN: Hermes venv lacks 'stripe' — fine unless this VPS arms commerce payment links." >&2
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
        elif [ -n "${ALLOW_MISSING_FOUNDATION_GATE:-}" ]; then
            # Deliberate rollback to a pre-gate artifact: the operator has acknowledged the gate's
            # absence (a pre-gate tarball also predates the foundation requirements it would check).
            echo "WARN: credential-minimized-readiness absent - skipping foundation gate (override: ALLOW_MISSING_FOUNDATION_GATE=$ALLOW_MISSING_FOUNDATION_GATE)" >&2
        else
            # BL-HERMES-12 hardening: a forward tarball ALWAYS ships this gate; its absence means a
            # malformed/incomplete artifact. Fail closed (mirrors the config-yaml shape gate above),
            # rather than silently proceeding without the foundation check. Set
            # ALLOW_MISSING_FOUNDATION_GATE=<reason> to intentionally roll back to a pre-gate artifact.
            echo "ERROR: credential-minimized-readiness absent from staging - refusing to deploy without the foundation gate." >&2
            echo "  A forward tarball always ships it; its absence indicates a malformed artifact." >&2
            echo "  To intentionally roll back to a pre-gate artifact: ALLOW_MISSING_FOUNDATION_GATE=<reason>" >&2
            exit 1
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

        if ! install_artifacts "$STAGING"; then
            echo "FAIL: install_artifacts gate failed - rolling back to $PREV_TAG" >&2
            if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                "$0" rollback "$PREV_TAG"
                rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
            else
                /usr/local/bin/shift-agent-notify-owner \
                    --title "Deploy FAILED during install_artifacts, no prior tarball" \
                    --priority 2 \
                    "Deploy $NEW_TAG failed during install_artifacts. Files may be partially installed while services still run old in-memory code. No prior tarball to roll back to - SSH immediately." 2>/dev/null || true
                rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
            fi
            exit 1
        fi

        # CD v2 durable rollback scrub. `creative_direction` is Field(exclude=True)
        # so NEW writes never persist it, but rows written before that fix may
        # still carry the key on disk and an older (rolled-back) extra="forbid"
        # loader would reject it. Strip any lingering key from the flyer project
        # store now (after install_artifacts so flyer_store_maintenance.py is
        # installed flat; before the gateway restart so the running gateway never
        # reads a store carrying the key). Idempotent + safe: with exclude=True the
        # key is never legitimately persisted, so removing it loses nothing. No-op
        # when the store file is absent (fresh VPS / flyer never used).
        #
        # GUARDED on module presence (Codex BLOCKER A): on a rollback to an older
        # tarball the guarded install above removed /opt/shift-agent/flyer_store_
        # maintenance.py, so this `import flyer_store_maintenance` would crash. Only
        # run the scrub when the module is actually present on the box; otherwise skip
        # it (a rolled-back older loader does not know the key and never wrote it).
        FLYER_STORE=/opt/shift-agent/state/flyer/projects.json
        if [ -f /opt/shift-agent/flyer_store_maintenance.py ]; then
            if [ -f "$FLYER_STORE" ]; then
                if ! "$VENV_PY" -c "import sys; sys.path.insert(0, '/opt/shift-agent'); from flyer_store_maintenance import scrub_store_file; print('scrubbed creative_direction x', scrub_store_file('$FLYER_STORE'))"; then
                    echo "FAIL: CD v2 rollback scrub of $FLYER_STORE failed — refusing to restart hermes-gateway" >&2
                    if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                        "$0" rollback "$PREV_TAG"
                        rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                    else
                        /usr/local/bin/shift-agent-notify-owner \
                            --title "Deploy FAILED at CD v2 store scrub, no prior tarball" \
                            --priority 2 \
                            "Deploy $NEW_TAG failed scrubbing creative_direction from the flyer project store. New files installed but service still on OLD code (gateway not yet restarted). No prior tarball to roll back to — SSH immediately." 2>/dev/null || true
                        rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                    fi
                    exit 1
                fi
            else
                echo "OK: CD v2 rollback scrub skipped (no flyer store at $FLYER_STORE)"
            fi
        else
            echo "skip scrub (module absent — rollback)"
        fi

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

        # Determine ONCE whether commerce is active-for-Stripe on this VPS. Both
        # the webhook-subscription gate (slice-3.5) and the livemode gate
        # (slice-3.1) use this to decide whether a MISSING gate script (pre-gate
        # or rollback tarball) is a hard-fail (commerce live on Stripe — a
        # money-safety gate must not be silently dropped) or a safe WARN-skip
        # (dormant/non-commerce). Probe errors (pre-commerce schema, unparseable
        # config) are treated as not-active.
        if "$VENV_PY" - <<'PY'
import sys, yaml
sys.path.insert(0, "/opt/shift-agent")
try:
    from schemas import CommerceConfig
    raw = (yaml.safe_load(open("/opt/shift-agent/config.yaml")) or {}).get("commerce") or {}
    cfg = CommerceConfig.model_validate(raw if isinstance(raw, dict) else {})
    active = bool(cfg.enabled and cfg.provider == "stripe")
except Exception:
    active = False
raise SystemExit(0 if active else 1)
PY
        then
            COMMERCE_ACTIVE_STRIPE=1
        else
            COMMERCE_ACTIVE_STRIPE=0
        fi

        # Pre-restart commerce webhook-subscription gate (slice-3.5). Dormant-safe:
        # exits 0 (and prints a one-line "not applicable" note) unless
        # commerce.enabled && commerce.provider == "stripe". When commerce IS
        # actively Stripe, it asserts the Stripe webhook subscription is
        # registered; if missing it fails closed — without the subscription,
        # Stripe payment_intent.succeeded events silently 404 and a paying
        # customer is never confirmed (slice-3 §13.5 A-LOW-1).
        #
        # Prefer the staging source copy so the FIRST deploy that introduces the
        # gate still runs it; fall back to the installed /usr/local/bin copy only
        # for rollback-tarball compatibility. Run via $VENV_PY so the wrapper's
        # `from schemas import CommerceConfig` resolves (pydantic lives there).
        COMMERCE_WEBHOOK_GATE="$STAGING/src/platform/scripts/check-commerce-webhook-subscription"
        [ -x "$COMMERCE_WEBHOOK_GATE" ] || COMMERCE_WEBHOOK_GATE=/usr/local/bin/check-commerce-webhook-subscription
        if [ ! -x "$COMMERCE_WEBHOOK_GATE" ]; then
            # Gate script absent => pre-gate (older) tarball or malformed deploy.
            # Skipping is safe ONLY if commerce is not active-for-Stripe (probed
            # once above). HARD-FAIL if active so a rollback cannot silently drop
            # the money-safety gate while Stripe is live (Codex review 2026-05-29,
            # escalated finding #3); WARN-skip when dormant (older-build compat).
            if [ "$COMMERCE_ACTIVE_STRIPE" = 1 ]; then
                echo "FATAL: commerce is active for Stripe but the webhook-subscription gate script is absent from staging and /usr/local/bin — refusing to deploy/restart (a rollback must not drop the money-safety gate while Stripe is live)." >&2
                if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                    "$0" rollback "$PREV_TAG"
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                else
                    /usr/local/bin/shift-agent-notify-owner \
                        --title "Deploy FAILED: commerce gate missing while Stripe active" \
                        --priority 2 \
                        "Deploy $NEW_TAG: commerce active for Stripe but the webhook-subscription gate script is missing from the tarball. New files installed but service still on OLD code. SSH immediately." 2>/dev/null || true
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                fi
                exit 1
            else
                echo "WARN: commerce webhook-subscription gate script not found (pre-gate tarball); commerce is not active-for-Stripe on this VPS so skipping is safe. If you later enable Stripe, redeploy a current tarball so the gate runs." >&2
            fi
        else
            if ! "$VENV_PY" "$COMMERCE_WEBHOOK_GATE" --config /opt/shift-agent/config.yaml; then
                echo "FAIL: pre-restart commerce webhook-subscription gate — refusing to restart hermes-gateway" >&2
                if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                    "$0" rollback "$PREV_TAG"
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                else
                    /usr/local/bin/shift-agent-notify-owner \
                        --title "Deploy FAILED at commerce webhook gate, no prior tarball" \
                        --priority 2 \
                        "Deploy $NEW_TAG failed the commerce webhook-subscription gate (commerce active for Stripe but the subscription is missing). New files installed but service still on OLD code (gateway not yet restarted). No prior tarball to roll back to — SSH immediately." 2>/dev/null || true
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                fi
                exit 1
            fi
        fi

        # Pre-restart commerce Stripe livemode-match gate (slice-3.1). Dormant-safe:
        # exits 0 unless commerce.enabled && commerce.provider == "stripe". When
        # active, asserts the Stripe API key's account livemode matches
        # commerce.stripe_livemode_expected — catches the "live key in test config"
        # (or vice versa) footgun before a customer pays (§13.5 B-MEDIUM-1).
        # Fail-closed on mismatch (exit 1) or key/API error (exit 2). Reads
        # STRIPE_API_KEY from .env itself and calls api.stripe.com via urllib (no
        # SDK); never logs the key. Same staging-preference + absent-handling as
        # the webhook gate above.
        COMMERCE_LIVEMODE_GATE="$STAGING/src/platform/scripts/check-commerce-stripe-livemode"
        [ -x "$COMMERCE_LIVEMODE_GATE" ] || COMMERCE_LIVEMODE_GATE=/usr/local/bin/check-commerce-stripe-livemode
        if [ ! -x "$COMMERCE_LIVEMODE_GATE" ]; then
            if [ "$COMMERCE_ACTIVE_STRIPE" = 1 ]; then
                echo "FATAL: commerce is active for Stripe but the livemode-match gate script is absent from staging and /usr/local/bin — refusing to deploy/restart (a rollback must not drop the money-safety gate while Stripe is live)." >&2
                if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                    "$0" rollback "$PREV_TAG"
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                else
                    /usr/local/bin/shift-agent-notify-owner \
                        --title "Deploy FAILED: commerce livemode gate missing while Stripe active" \
                        --priority 2 \
                        "Deploy $NEW_TAG: commerce active for Stripe but the livemode-match gate script is missing from the tarball. New files installed but service still on OLD code. SSH immediately." 2>/dev/null || true
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                fi
                exit 1
            else
                echo "WARN: commerce Stripe livemode-match gate script not found (pre-gate tarball); commerce is not active-for-Stripe on this VPS so skipping is safe. If you later enable Stripe, redeploy a current tarball so the gate runs." >&2
            fi
        else
            if ! "$VENV_PY" "$COMMERCE_LIVEMODE_GATE" --config /opt/shift-agent/config.yaml; then
                echo "FAIL: pre-restart commerce Stripe livemode-match gate — refusing to restart hermes-gateway" >&2
                if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
                    "$0" rollback "$PREV_TAG"
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                else
                    /usr/local/bin/shift-agent-notify-owner \
                        --title "Deploy FAILED at commerce livemode gate, no prior tarball" \
                        --priority 2 \
                        "Deploy $NEW_TAG failed the commerce Stripe livemode-match gate (key mode != stripe_livemode_expected, or Stripe unreachable). New files installed but service still on OLD code. No prior tarball to roll back to — SSH immediately." 2>/dev/null || true
                    rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
                fi
                exit 1
            fi
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

        # Restart services (in order: tail-logger first, gateway last).
        # Do not cut off long Flyer image generation/source-edit jobs mid-flight;
        # a restart sends SIGTERM through the gateway process tree and can turn a
        # real customer request into exit=-15 plus a failed WhatsApp ack.
        if ! wait_for_flyer_generation_drain; then
            /usr/local/bin/shift-agent-notify-owner                 --title "Deploy paused: active Flyer generation"                 --priority 2                 "Deploy $NEW_TAG refused to restart Hermes while Flyer generation was still active. Retry after the active job drains." 2>/dev/null || true
            exit 1
        fi
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
