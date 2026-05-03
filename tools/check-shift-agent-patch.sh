#!/usr/bin/env bash
# Verify shift-agent patches are present and Hermes hasn't drifted.
# Exit 1 (fail-closed) on any drift. Called from shift-agent-deploy.sh as
# the first gate before install_artifacts runs.
#
# Pin baseline: tools/hermes-patch-baseline.txt (KEY=VALUE format).
#
# Override mechanism (for legitimate Hermes upgrades):
#   HERMES_PIN_OVERRIDE=<new_target_hash>      — required, full 40-char commit hash
#   HERMES_PIN_OVERRIDE_REASON="<reason>"      — required, free text logged for audit
# Both must be set. The override does NOT auto-update the baseline file —
# operator must update tools/hermes-patch-baseline.txt + commit as a follow-up,
# or the next deploy fails again. This is intentional friction.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASELINE_FILE="$SCRIPT_DIR/hermes-patch-baseline.txt"

H=/root/.hermes/hermes-agent
RUN=$H/gateway/run.py
WA=$H/gateway/platforms/whatsapp.py
BR=$H/scripts/whatsapp-bridge/bridge.js

fail() { echo "FAIL: $1" >&2; exit 1; }
warn() { echo "WARN: $1" >&2; }
info() { echo "  $1" >&2; }

# ─────────────────────────────────────────────────────────────────
# 0. Load baseline pin (commit hash, version, bridge.js sha256)
# ─────────────────────────────────────────────────────────────────

[ -r "$BASELINE_FILE" ] || fail "baseline pin file missing: $BASELINE_FILE"

# Source-style read (ignore comment + blank lines).
# Normalizes:
#   - whitespace including \r (defends against CRLF — recurring repo gotcha)
#   - surrounding double or single quotes (KEY="abc" and KEY=abc are
#     semantically identical to dotenv loaders, but raw string compare against
#     `git rev-parse HEAD` would false-positive on a quoted baseline value)
# Without this, a stray \r or quoted value would fail-close the deploy with
# no visible diff in operator output — exactly the failure mode this gate is
# supposed to surface, not produce.
_read_pin() {
    grep "^${1}=" "$BASELINE_FILE" \
        | head -1 \
        | cut -d= -f2- \
        | tr -d '[:space:]' \
        | sed -E 's/^"(.*)"$/\1/; s/^'\''(.*)'\''$/\1/'
}
PINNED_COMMIT=$(_read_pin HERMES_COMMIT)
PINNED_VERSION=$(_read_pin HERMES_VERSION)
PINNED_BRIDGE_SHA=$(_read_pin BRIDGE_POST_PATCH_SHA256)

[ -n "$PINNED_COMMIT" ] || fail "baseline missing HERMES_COMMIT field"
[ -n "$PINNED_BRIDGE_SHA" ] || fail "baseline missing BRIDGE_POST_PATCH_SHA256 field"

# ─────────────────────────────────────────────────────────────────
# 1. Hermes commit hash check (fail-closed, override-able)
# ─────────────────────────────────────────────────────────────────

# Hermes is a git checkout owned by shift-agent — read as that user to avoid
# git's safe.directory protection.
CURRENT_COMMIT=$(sudo -u shift-agent git -C "$H" rev-parse HEAD 2>/dev/null || echo "unknown")

if [ "$CURRENT_COMMIT" != "$PINNED_COMMIT" ]; then
    if [ -n "${HERMES_PIN_OVERRIDE:-}" ]; then
        # Override active — verify it matches current commit exactly
        if [ "$HERMES_PIN_OVERRIDE" != "$CURRENT_COMMIT" ]; then
            fail "HERMES_PIN_OVERRIDE=$HERMES_PIN_OVERRIDE does not match current Hermes commit $CURRENT_COMMIT (must re-type the actual current hash to attest review)"
        fi
        if [ -z "${HERMES_PIN_OVERRIDE_REASON:-}" ]; then
            fail "HERMES_PIN_OVERRIDE set but HERMES_PIN_OVERRIDE_REASON missing — both required"
        fi
        warn "Hermes drift override accepted (THIS RUN ONLY — unset HERMES_PIN_OVERRIDE after this deploy to avoid sticky-shell-var surprise on a later unrelated deploy)"
        info "  pinned:  $PINNED_COMMIT"
        info "  current: $CURRENT_COMMIT"
        info "  reason:  $HERMES_PIN_OVERRIDE_REASON"
        info ""
        info "  TO MAKE PERMANENT: update tools/hermes-patch-baseline.txt with"
        info "    HERMES_COMMIT=$CURRENT_COMMIT"
        info "  and the new BRIDGE_POST_PATCH_SHA256, then commit + ship a new tarball."
        info "  Without that, the NEXT deploy will fail-close again."

        # Audit override events durably. Two-channel: a local fallback file
        # (always succeeds, no dependencies) AND log-decision-direct (best-effort,
        # may fail if binary missing or schema rejects). Don't gate audit on
        # either alone — overrides are the single most important event to
        # record because they bypass the gate's primary protection.
        TS=$(date -Iseconds)
        OVERRIDE_LOG=/opt/shift-agent/logs/pin-overrides.log
        mkdir -p "$(dirname "$OVERRIDE_LOG")" 2>/dev/null || true
        # Local fallback first — plain text, no dependencies, append-only.
        printf '%s pinned=%s current=%s reason=%q\n' \
            "$TS" "$PINNED_COMMIT" "$CURRENT_COMMIT" "$HERMES_PIN_OVERRIDE_REASON" \
            >> "$OVERRIDE_LOG" 2>/dev/null || true
        # Structured audit second. Build the JSON via python3 -c json.dumps so
        # the reason string is properly escaped (handles backslashes, newlines,
        # quotes, control chars — naive sed-escape misses backslashes + \n).
        if [ -x /usr/local/bin/log-decision-direct ] && command -v python3 >/dev/null; then
            ENTRY=$(python3 -c '
import json, sys
print(json.dumps({
    "type": "agent_state_change",
    "ts": sys.argv[1],
    "to_state": "enabled",
    "reason": f"hermes_pin_override pinned={sys.argv[2]} current={sys.argv[3]} reason={sys.argv[4]}",
}))
' "$TS" "$PINNED_COMMIT" "$CURRENT_COMMIT" "$HERMES_PIN_OVERRIDE_REASON" 2>/dev/null) || ENTRY=""
            if [ -n "$ENTRY" ]; then
                /usr/local/bin/log-decision-direct "$ENTRY" 2>/dev/null || true
            fi
        fi
    else
        echo "FAIL: Hermes commit drift detected." >&2
        echo "  pinned (in tools/hermes-patch-baseline.txt): $PINNED_COMMIT" >&2
        echo "  current (live VPS):                          $CURRENT_COMMIT" >&2
        echo "" >&2
        echo "Our patches were authored against the pinned commit. A different commit" >&2
        echo "may have moved bridge.js / gateway code such that patches silently no-op." >&2
        echo "" >&2
        echo "If this is a deliberate Hermes upgrade and you've verified the new commit" >&2
        echo "is compatible with our patches, re-run with:" >&2
        echo "  HERMES_PIN_OVERRIDE=$CURRENT_COMMIT \\\\" >&2
        echo "  HERMES_PIN_OVERRIDE_REASON=\"...\" \\\\" >&2
        echo "  $0" >&2
        exit 1
    fi
fi

# ─────────────────────────────────────────────────────────────────
# 2. Bridge.js content sha256 check (fail-closed)
# ─────────────────────────────────────────────────────────────────

[ -f "$BR" ] || fail "missing target file $BR"
ACTUAL_BRIDGE_SHA=$(sha256sum "$BR" | cut -d' ' -f1)

if [ "$ACTUAL_BRIDGE_SHA" != "$PINNED_BRIDGE_SHA" ]; then
    # If override was active above, allow this too (reasonable: new Hermes
    # commit usually means new bridge.js content too).
    if [ -n "${HERMES_PIN_OVERRIDE:-}" ]; then
        warn "bridge.js sha256 drift accepted under HERMES_PIN_OVERRIDE"
        info "  pinned:  $PINNED_BRIDGE_SHA"
        info "  current: $ACTUAL_BRIDGE_SHA"
    else
        echo "FAIL: bridge.js sha256 drift detected." >&2
        echo "  pinned:  $PINNED_BRIDGE_SHA" >&2
        echo "  current: $ACTUAL_BRIDGE_SHA" >&2
        echo "" >&2
        echo "Either (a) Hermes upstream changed bridge.js, or (b) our patches were" >&2
        echo "re-applied with different output, or (c) bridge.js was manually edited." >&2
        echo "" >&2
        echo "If intentional (e.g. you re-ran patch-bridge-filter.py with logic changes)," >&2
        echo "update BRIDGE_POST_PATCH_SHA256 in tools/hermes-patch-baseline.txt and" >&2
        echo "commit + ship a new tarball." >&2
        exit 1
    fi
fi

# ─────────────────────────────────────────────────────────────────
# 3. Patch markers present in all 3 target files (fail-closed)
# ─────────────────────────────────────────────────────────────────

for f in "$RUN" "$WA" "$BR"; do
    [ -f "$f" ] || fail "missing target file $f"
    grep -q "BEGIN shift-agent-sender-id" "$f" || fail "$f missing BEGIN shift-agent-sender-id marker"
    grep -q "END shift-agent-sender-id" "$f" || fail "$f missing END shift-agent-sender-id marker"
done

# Bridge.js also has the template-bypass patch (added by tools/patch-bridge-filter.py).
grep -q "BEGIN shift-agent-template-bypass" "$BR" || fail "$BR missing BEGIN shift-agent-template-bypass marker"
grep -q "END shift-agent-template-bypass" "$BR" || fail "$BR missing END shift-agent-template-bypass marker"

# ─────────────────────────────────────────────────────────────────
# 4. Anchor proximity — markers near expected upstream symbols
# ─────────────────────────────────────────────────────────────────

# run.py: INJECT-SITE marker (last BEGIN, near _prepare_inbound_message_text)
RB=$(grep -n "BEGIN shift-agent-sender-id" "$RUN" | tail -1 | cut -d: -f1)
RA=$(grep -n "_prepare_inbound_message_text" "$RUN" | head -1 | cut -d: -f1)
[ -n "$RB" ] && [ -n "$RA" ] || fail "$RUN missing BEGIN marker or anchor symbol"
DIFF=$(( RB > RA ? RB - RA : RA - RB ))
[ "$DIFF" -le 60 ] || fail "$RUN BEGIN marker drifted from anchor (delta=$DIFF lines)"

# whatsapp.py: _resolve_sender_context helper
WB=$(grep -n "BEGIN shift-agent-sender-id" "$WA" | head -1 | cut -d: -f1)
WA_=$(grep -n "_build_message_event\|_resolve_sender_context" "$WA" | head -1 | cut -d: -f1)
[ -n "$WB" ] && [ -n "$WA_" ] || fail "$WA missing BEGIN marker or anchor symbol"
DIFF2=$(( WB > WA_ ? WB - WA_ : WA_ - WB ))
[ "$DIFF2" -le 50 ] || fail "$WA BEGIN marker drifted from anchor (delta=$DIFF2 lines)"

# bridge.js: messageQueue.push inject site
BB=$(grep -n "BEGIN shift-agent-sender-id" "$BR" | head -1 | cut -d: -f1)
BA=$(grep -n "messageQueue.push" "$BR" | head -1 | cut -d: -f1)
[ -n "$BB" ] && [ -n "$BA" ] || fail "$BR missing BEGIN marker or anchor symbol"
DIFF3=$(( BB > BA ? BB - BA : BA - BB ))
[ "$DIFF3" -le 200 ] || fail "$BR BEGIN marker drifted from anchor (delta=$DIFF3 lines)"

# PR-CF6: cf-router plugin requires the pre_gateway_dispatch hook surface in
# gateway/run.py. If Hermes upstream renames or removes the hook, the plugin's
# register() call silently no-ops and our owner #XXXXX interception stops
# working. Verify the hook name is still present in gateway/run.py.
grep -q "pre_gateway_dispatch" "$RUN" || fail "$RUN missing pre_gateway_dispatch hook surface (cf-router plugin would silently fail)"

# ─────────────────────────────────────────────────────────────────
# 5. Hermes Python module version (informational warn only)
# ─────────────────────────────────────────────────────────────────

# Different signal from commit hash — version may stay 0.11.0 across many
# commits. Warn-only because commit hash is the authoritative pin.
if [ -n "$PINNED_VERSION" ] && [ -x "$H/venv/bin/python" ]; then
    CURRENT_VERSION=$("$H/venv/bin/python" -c \
        "import hermes_agent; print(hermes_agent.__version__)" 2>/dev/null || echo "unknown")
    if [ "$PINNED_VERSION" != "$CURRENT_VERSION" ]; then
        warn "Hermes version drift expected=$PINNED_VERSION current=$CURRENT_VERSION (informational; commit-hash pin is authoritative)"
    fi
fi

echo "OK: shift-agent patches verified against pinned Hermes ${PINNED_COMMIT:0:8}."
