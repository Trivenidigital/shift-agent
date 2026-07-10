#!/usr/bin/env bash
# shift-agent-skills-audit — between-deploy watchdog on /root/.hermes/skills. Runs as ROOT.
#
# Runs every ~15min. Alerts (throttled to once/hour) if any deployed SKILL.md content drifted,
# an unknown FLAT skill dir appeared, or a CRITICAL skill went missing (deletion).
#
# TRUST BOUNDARY (the point of the root-hardening, PR #583 security review): every
# security-critical input is HARDCODED to a root-owned location that the Hermes gateway
# CANNOT write — owned root:root (DAC) AND under /usr or /var/lib, which the gateway's
# ProtectSystem=strict makes read-only (MAC). Nothing is read from .env or env overrides. The
# checker module is run DIRECTLY from its root-owned copy (self-contained stdlib) so a
# shift-agent-uid adversary cannot rewrite the checker either. A compromised gateway therefore
# cannot evade DETECTION.
#
# ALERT DELIVERY drops to the shift-agent uid (via runuser) — NOT root. notify-owner imports
# safe_io/schemas from the adversary-writable /opt/shift-agent, so importing it AS ROOT would be
# a root-RCE vector (rewrite /opt/shift-agent/safe_io.py, cause drift, get root when the alert
# fires). Dropping privileges means a compromised gateway can at worst DoS its own alert
# (system-wide + loud; the §12b *_dispatched log still fires as root). Detection integrity is
# fully root-hardened and unaffected.
#
# Alert-only: never blocks anything.
set -euo pipefail

# Hardcoded root-owned, gateway-unwritable inputs (NOT env-overridable — that was the bypass).
SHARE=/usr/local/share/shift-agent
MODULE="$SHARE/skills_manifest.py"                 # root-owned, self-contained (stdlib) — run directly
MANIFEST="$SHARE/skills-manifest.txt"
FOUNDATION="$SHARE/skills-foundation-allowlist.txt"
REQUIRED="$SHARE/skills-critical.txt"
SKILLS_ROOT=/root/.hermes/skills                    # the SUBJECT of the check (adversary-writable = correct)
PY=/usr/bin/python3                                 # module is stdlib-only; no venv needed
THROTTLE_DIR="${STATE_DIRECTORY:-/var/lib/shift-agent-skills-audit}"  # systemd-provided, root-owned
ALERT_FILE="$THROTTLE_DIR/last-alert-ts"
ALERT_THROTTLE_SEC=3600   # one alert per hour max

# Rollback safety: no-op if the hardened surface isn't installed (old tarball predating it).
[ -f "$MODULE" ] || exit 0
[ -f "$MANIFEST" ] || exit 0
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || exit 0

# Base has >=4 words so "${AUDIT_ARGS[@]}" never expands empty (set -u safe).
AUDIT_ARGS=(audit --manifest "$MANIFEST" --skills-root "$SKILLS_ROOT")
[ -f "$FOUNDATION" ] && AUDIT_ARGS+=(--foundation "$FOUNDATION")
[ -f "$REQUIRED" ]   && AUDIT_ARGS+=(--required "$REQUIRED")

JSON=$("$PY" "$MODULE" "${AUDIT_ARGS[@]}" 2>/dev/null) || true
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

# §12b: dispatched/delivered structured logs around the alert (traceable in journalctl).
echo "skills_audit_alert_dispatched changed=[$CHANGED] extra=[$EXTRA] missing_required=[$MISSREQ]" >&2

# Plain text — NO markdown (underscores in skill names; §12b).
MSG="Skills-integrity watchdog: on-box SKILL drift NOT caused by a deploy. changed(content)=${CHANGED} extra(unknown-dir)=${EXTRA} missing-critical(deleted)=${MISSREQ}. A self-improving/curator process or a manual edit may have altered a deployed skill. Inspect: ls -la /root/.hermes/skills/ then diff against source; redeploy to restore. This is the 2026-05-05 curator/dispatcher failure class."

# Drop to the shift-agent uid for delivery (see the RESIDUAL note above) — never run
# notify-owner as root, since it imports adversary-writable /opt code. runuser is the standard
# root->user tool (util-linux, present on all systemd hosts). Fail SAFE: if it is somehow
# unavailable, SKIP delivery + log loudly rather than deliver as root.
if command -v runuser >/dev/null 2>&1; then
    DROP="runuser -u shift-agent --"
else
    DROP=""
fi

if [ -z "$DROP" ]; then
    echo "skills_audit_alert_delivery_skipped_no_runuser changed=[$CHANGED] extra=[$EXTRA] missing_required=[$MISSREQ]" >&2
elif $DROP /usr/local/bin/shift-agent-notify-owner \
    --title "Skill files changed between deploys" \
    --priority 2 \
    "$MSG"; then
    echo "skills_audit_alert_delivered changed=[$CHANGED] extra=[$EXTRA] missing_required=[$MISSREQ]" >&2
    # Stamp throttle ONLY on successful delivery (transient failure retries next cycle).
    mkdir -p "$THROTTLE_DIR" 2>/dev/null || true
    echo "$now_s" > "$ALERT_FILE"
else
    echo "skills_audit_alert_delivery_failed changed=[$CHANGED] extra=[$EXTRA] missing_required=[$MISSREQ]" >&2
fi
