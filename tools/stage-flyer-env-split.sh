#!/usr/bin/env bash
# stage-flyer-env-split — REPORT-ONLY stager for moving non-secret FLYER_*
# config lines out of /root/.hermes/.env (the "line-8 class": operational
# flags living in the secrets file).
#
# ── FINDING (2026-07-06): BLOCKED UPSTREAM for gateway-consumed vars ─────────
# There are exactly two readers of FLYER_* configuration on a customer VPS:
#   1. The Hermes gateway (`load_hermes_dotenv`) reads /root/.hermes/.env and
#      passes its environment to every skill-spawned script — this is how
#      FLYER_PREMIUM_POSTER_V1, allowlists, kill-switches etc. reach the
#      render/QA/send scripts at runtime.
#   2. shift-agent systemd units read the SAME file through the
#      /opt/shift-agent/.env symlink (EnvironmentFile=).
# Hermes 0.14 (PINNED — see patch-baseline gate; upgrades blocked) has no
# "source an additional env file" hook, and patching one in would widen the
# fail-closed patch surface for a cosmetic win. Therefore FLYER_* vars that
# any gateway-spawned skill reads MUST stay in /root/.hermes/.env until either
# (a) Hermes grows a second-env-file hook upstream, or (b) the fleet moves to
# a Hermes version whose config.yaml can carry per-skill env.
#
# What this script therefore does — and deliberately does NOT do:
#   - Classifies every FLYER_* line in /root/.hermes/.env as secret-shaped
#     (name matches KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL) or non-secret.
#   - Writes a PROPOSED split into a staging directory:
#       <staging>/flyer-flags.env       (non-secret FLYER_* lines)
#       <staging>/dot-env.proposed      (.env with those lines removed)
#     plus instructions for the systemd-only alternative (a second
#     `EnvironmentFile=-/opt/shift-agent/flyer-flags.env` line in units —
#     which still does NOT cover gateway-spawned readers, hence report-only).
#   - NEVER touches /root/.hermes/.env, the symlink, or any unit file.
#     There is intentionally NO --apply mode.
#
# GOTCHA carried from 2026-07-02: /opt/shift-agent/.env is a SYMLINK to
# /root/.hermes/.env. Any future applier must edit the TARGET, never sed -i
# the symlink (that replaces the link with a detached copy and the deploy
# gate fail-closes).
#
# Usage:
#   tools/stage-flyer-env-split.sh [staging-dir]   # default: /tmp/flyer-env-split.<ts>
#
# Exit codes: 0 report written; 2 .env missing/unreadable.
set -euo pipefail

HERMES_ENV="${HERMES_ENV_PATH:-/root/.hermes/.env}"
STAGING="${1:-/tmp/flyer-env-split.$(date +%Y%m%d%H%M%S)}"

[ -r "$HERMES_ENV" ] || { echo "FAIL: $HERMES_ENV missing or unreadable" >&2; exit 2; }
mkdir -p "$STAGING"
chmod 700 "$STAGING"

FLAGS_OUT="$STAGING/flyer-flags.env"
ENV_OUT="$STAGING/dot-env.proposed"
: > "$FLAGS_OUT"
: > "$ENV_OUT"

secret_count=0
flag_count=0
# `|| [ -n "$line" ]`: read returns non-zero on a final unterminated line even
# though it populated $line -- without this, a .env lacking a trailing newline
# silently loses its last entry from BOTH proposed files (review M3).
while IFS= read -r line || [ -n "$line" ]; do
    name="${line%%=*}"
    case "$line" in
        FLYER_*=*)
            case "$name" in
                *KEY*|*TOKEN*|*SECRET*|*PASSWORD*|*CREDENTIAL*)
                    secret_count=$((secret_count + 1))
                    printf '%s\n' "$line" >> "$ENV_OUT"
                    ;;
                *)
                    flag_count=$((flag_count + 1))
                    printf '%s\n' "$line" >> "$FLAGS_OUT"
                    ;;
            esac
            ;;
        *)
            printf '%s\n' "$line" >> "$ENV_OUT"
            ;;
    esac
done < "$HERMES_ENV"

echo "── stage-flyer-env-split report ──────────────────────────────────────"
echo "source:                $HERMES_ENV"
echo "non-secret FLYER_*:    $flag_count line(s) -> $FLAGS_OUT"
echo "secret-shaped FLYER_*: $secret_count line(s) (kept in .env)"
echo "proposed .env:         $ENV_OUT"
echo
echo "VERDICT: blocked upstream — Hermes 0.14 (pinned) only reads $HERMES_ENV,"
echo "and gateway-spawned skills inherit FLYER_* from the gateway environment."
echo "Applying this split would silently strip flags from every skill-spawned"
echo "script. DO NOT apply until Hermes supports an additional env file, or a"
echo "specific var is proven consumed ONLY by systemd units (then: install"
echo "flyer-flags.env under /opt/shift-agent + add a second EnvironmentFile="
echo "line to those units — a separate, reviewed change)."
echo "No live file was modified."
