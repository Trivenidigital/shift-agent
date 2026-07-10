#!/usr/bin/env bash
# shift-agent-skills-audit — between-deploy watchdog on /root/.hermes/skills.
#
# Runs every ~15min. Alerts (throttled to once/hour) if any of:
#   * a deployed SKILL.md's CONTENT drifted from the shipped manifest (in-place edit),
#   * an unknown FLAT skill dir appeared (curator umbrella / autonomous skill write),
#   * a CRITICAL skill (skills-critical.txt) went MISSING (deletion = dispatcher-silence).
#
# WHY: the content gate (check-skills-manifest.sh) only runs at DEPLOY. Between deploys —
# which on a pinned, stable fleet can be weeks — a self-improving/curator process or a
# manual edit runs LIVE and unnoticed until the next deploy. The one self-heal (rsync
# --delete at the next deploy) is SILENT. This watchdog makes the drift LOUD at detection
# time (§12a freshness watchdog + §12b alert-at-reversal).
#
# SCOPE (honest): this catches BENIGN drift (an autonomous curator / accidental edit). It is
# NOT adversary-resistant — it runs as `shift-agent`, the SAME uid as the Hermes gateway
# (see hermes-gateway.service ReadWritePaths=/opt/shift-agent /root/.hermes), so a
# *compromised* gateway can poison this script's manifest / throttle / .env and evade it.
# That is why it ships INSTALLED-BUT-DISABLED (deploy.sh does not enable the timer) and why
# the PR files a hardening follow-up (run as root, root-owned inputs outside ReadWritePaths).
#
# Alert-only: never blocks anything. Mirrors shift-agent-health-watchdog.sh.
set -euo pipefail

# The manifest + allowlist + critical-list are read from the persistent deployed tree (where
# the deploy gate also reads its baseline) — NOT a separately-installed /opt copy — so there
# is a single source of truth and deploy.sh needn't install from tools/ (R4-H-2).
MANIFEST="${SKILLS_MANIFEST_FILE:-/opt/shift-agent/staging-new/tools/skills-manifest.txt}"
SKILLS_ROOT="${SKILLS_ROOT:-/root/.hermes/skills}"
FOUNDATION="${SKILLS_FOUNDATION_ALLOWLIST:-/opt/shift-agent/staging-new/tools/skills-foundation-allowlist.txt}"
REQUIRED="${SKILLS_REQUIRED_LIST:-/opt/shift-agent/staging-new/tools/skills-critical.txt}"
HELPER="${SKILLS_MANIFEST_HELPER:-/usr/local/bin/check-skills-manifest}"
VENV_PY="${VENV_PY:-/usr/local/lib/hermes-agent/venv/bin/python}"
ALERT_FILE=/opt/shift-agent/state/.skills-audit-last-alert-ts
ALERT_THROTTLE_SEC=3600   # one alert per hour max

# Rollback safety: silently no-op if the surface isn't installed (old tarball).
[ -f "$MANIFEST" ] || exit 0
[ -f "$HELPER" ] || exit 0
PY="$VENV_PY"
if [ ! -x "$PY" ]; then PY="$(command -v python3 || true)"; fi
[ -n "$PY" ] || exit 0

# Build the audit invocation. The base always has >=4 words, so "${AUDIT_ARGS[@]}" never
# expands empty (avoids the set -u empty-array pitfall); optional list files append only
# when present.
AUDIT_ARGS=(audit --manifest "$MANIFEST" --skills-root "$SKILLS_ROOT")
[ -f "$FOUNDATION" ] && AUDIT_ARGS+=(--foundation "$FOUNDATION")
[ -f "$REQUIRED" ]   && AUDIT_ARGS+=(--required "$REQUIRED")

JSON=$("$PY" "$HELPER" "${AUDIT_ARGS[@]}" 2>/dev/null) || true
[ -n "$JSON" ] || exit 0

CODE=$("$PY" -c "import json,sys; print(json.loads(sys.argv[1]).get('exit_code',0))" "$JSON" 2>/dev/null || echo 0)
# 0 = clean, 2 = error/manifest-missing (no alert), 1 = findings.
[ "$CODE" = "1" ] || exit 0

# Throttle.
now_s=$(date +%s)
last_alert_s=0
[ -f "$ALERT_FILE" ] && last_alert_s=$(cat "$ALERT_FILE" 2>/dev/null || echo 0)
if [ $((now_s - last_alert_s)) -lt "$ALERT_THROTTLE_SEC" ]; then exit 0; fi

_field() { "$PY" -c "import json,sys; print(','.join(json.loads(sys.argv[1]).get(sys.argv[2],[])) or '-')" "$JSON" "$1"; }
CHANGED=$(_field changed)
EXTRA=$(_field extra)
MISSREQ=$(_field missing_required)

# §12b: emit dispatched/delivered structured logs around the alert so every fire is
# traceable in journalctl regardless of delivery success.
echo "skills_audit_alert_dispatched changed=[$CHANGED] extra=[$EXTRA] missing_required=[$MISSREQ]" >&2

# Plain text — NO markdown. Skill names contain underscores (dispatch_shift_agent,
# handle_catering_owner_approval); MarkdownV1 italics would eat them (§12b).
if /usr/local/bin/shift-agent-notify-owner \
    --title "Skill files changed between deploys" \
    --priority 2 \
    "Skills-integrity watchdog: on-box SKILL drift NOT caused by a deploy. changed(content)=${CHANGED} extra(unknown-dir)=${EXTRA} missing-critical(deleted)=${MISSREQ}. A self-improving/curator process or a manual edit may have altered a deployed skill. Inspect: ls -la /root/.hermes/skills/ then diff against source; redeploy to restore. This is the 2026-05-05 curator/dispatcher failure class."; then
    echo "skills_audit_alert_delivered changed=[$CHANGED] extra=[$EXTRA] missing_required=[$MISSREQ]" >&2
    # Stamp the throttle ONLY on successful delivery, so a transient Pushover failure
    # retries next cycle instead of muting the next hour of detection.
    mkdir -p "$(dirname "$ALERT_FILE")" 2>/dev/null || true
    echo "$now_s" > "$ALERT_FILE"
else
    echo "skills_audit_alert_delivery_failed changed=[$CHANGED] extra=[$EXTRA] missing_required=[$MISSREQ]" >&2
    # Deliberately do NOT stamp the throttle — let the next 15-min cycle retry delivery.
fi
