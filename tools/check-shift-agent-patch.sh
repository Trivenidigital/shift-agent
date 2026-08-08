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
# Second patched JS file since 0.19.1: bridge.js imports it, and our
# shift-agent-button-response-body block lives here rather than in bridge.js.
# FOLLOW_UP: unlike bridge.js this file is NOT content-pinned by
# BRIDGE_POST_PATCH_SHA256, so a change here is caught only by the marker checks
# below, not by a hash. Adding a second pin would need a baseline-format change.
BRH=$H/scripts/whatsapp-bridge/bridge_helpers.js

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

# Hermes is a git checkout. On srilu /root/.hermes/hermes-agent is a symlink
# to /usr/local/lib/hermes-agent with a root-owned .git/, which trips git's
# "dubious ownership" check when shift-agent runs git -C. Pass safe.directory
# inline so any owner mapping works without per-VPS operator config. We also
# resolve the symlink with `readlink -f` so safe.directory matches the real
# path git complains about.
H_REAL=$(readlink -f "$H" 2>/dev/null || echo "$H")
CURRENT_COMMIT=$(sudo -u shift-agent git -c "safe.directory=$H_REAL" -C "$H" rev-parse HEAD 2>/dev/null \
    || git -c "safe.directory=$H_REAL" -C "$H" rev-parse HEAD 2>/dev/null \
    || echo "unknown")

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
        echo "If intentional (e.g. you re-ran tools/patch-hermes.py against a new" >&2
        echo "upstream Hermes), update BRIDGE_POST_PATCH_SHA256 in" >&2
        echo "tools/hermes-patch-baseline.txt and commit + ship a new tarball." >&2
        exit 1
    fi
fi

# ─────────────────────────────────────────────────────────────────
# 3. Policy architecture (plugin + preflight) + bridge markers (fail-closed)
# ─────────────────────────────────────────────────────────────────

# ARCHITECTURE NOTE (2026-08-07, Hermes 0.19.1). The Python-side patches that
# used to be injected into gateway/run.py and gateway/platforms/whatsapp.py were
# SUPERSEDED by the shift-agent-policy Hermes plugin (src/plugins/shift-agent-policy/),
# whose stated purpose is to keep the Hermes checkout stock. On 0.19.1 the WhatsApp
# platform RELOCATED to plugins/platforms/whatsapp/adapter.py and
# gateway/platforms/whatsapp.py NO LONGER EXISTS -- so every former $WA marker
# assertion attested an architecture that is gone and could never pass again. They
# are replaced here by checks against the CURRENT architecture. The bridge.js
# (JS-side) patches are unchanged and are still asserted, below and in section 2.

# 3a. Canonical policy implementation must exist in the tree being deployed. If it
# does not, the deploy would rsync --delete it off the box and leave WhatsApp
# UNSCREENED, so this is fail-closed.
POLICY_SRC="$SCRIPT_DIR/../src/plugins/shift-agent-policy"
[ -f "$POLICY_SRC/policy.py" ] || fail "missing $POLICY_SRC/policy.py -- the screening policy plugin is absent from this tree; deploying would remove it and relay UNSCREENED WhatsApp traffic"
[ -f "$POLICY_SRC/plugin.yaml" ] || fail "missing $POLICY_SRC/plugin.yaml -- the plugin would not be discovered by Hermes"
[ -f "$POLICY_SRC/__init__.py" ] || fail "missing $POLICY_SRC/__init__.py -- the plugin package would not import"
grep -q "class ScreenedWhatsAppAdapter" "$POLICY_SRC/policy.py" || fail "$POLICY_SRC/policy.py does not define ScreenedWhatsAppAdapter (outbound egress screen absent)"
grep -q "front_brain_screen_gateway_send" "$POLICY_SRC/policy.py" || fail "$POLICY_SRC/policy.py does not reference front_brain_screen_gateway_send (outbound screen would not be consulted)"
grep -q "pre_gateway_dispatch" "$POLICY_SRC/policy.py" || fail "$POLICY_SRC/policy.py does not register pre_gateway_dispatch (inbound sender-context defence absent)"

# 3b. The canonical preflight must ship in this tree. It is the gateway's own
# ExecStartPre refusal gate; without it a silently-unloaded plugin would fall back
# to the stock UNSCREENED adapter with no error.
PREFLIGHT_SRC="$SCRIPT_DIR/../src/agents/shift/scripts/shift-agent-policy-preflight"
[ -f "$PREFLIGHT_SRC" ] || fail "missing $PREFLIGHT_SRC -- the policy preflight is not in this tree; the gateway would start without proving screening is live"

# 3c. RUNTIME proof. Invoke the deployed deterministic preflight rather than
# duplicating its logic: it asserts (A) the screen is importable, (B) the plugin is
# enabled AND loaded, (C) the pre_gateway_dispatch sender-context hook is
# registered, and (D) 'whatsapp' resolves to OUR ScreenedWhatsAppAdapter -- i.e.
# that the last-writer-wins override actually took effect at runtime. Non-zero exit
# is fail-closed here exactly as it is for systemd.
PREFLIGHT_BIN=/usr/local/bin/shift-agent-policy-preflight
if [ -x "$PREFLIGHT_BIN" ]; then
    if PREFLIGHT_OUT=$("$PREFLIGHT_BIN" 2>&1); then
        info "policy preflight PASSED (screening live: plugin loaded, hook registered, ScreenedWhatsAppAdapter resolved)."
    else
        echo "$PREFLIGHT_OUT" >&2
        fail "shift-agent-policy preflight FAILED -- WhatsApp screening is not provably live (see REFUSE lines above)"
    fi
else
    fail "$PREFLIGHT_BIN missing or not executable -- cannot prove WhatsApp screening is live"
fi

# 3d. Bridge.js (JS-side) patch markers -- UNCHANGED by the plugin migration and
# still the only screen on the CTA / sender-id bridge path.
[ -f "$BR" ] || fail "missing target file $BR"
grep -q "BEGIN shift-agent-sender-id" "$BR" || fail "$BR missing BEGIN shift-agent-sender-id marker"
grep -q "END shift-agent-sender-id" "$BR" || fail "$BR missing END shift-agent-sender-id marker"
grep -q "BEGIN shift-agent-cta-buttons" "$BR" || fail "$BR missing BEGIN shift-agent-cta-buttons marker"
grep -q "END shift-agent-cta-buttons" "$BR" || fail "$BR missing END shift-agent-cta-buttons marker"

PLATFORM=/opt/shift-agent

# Bridge.js template-bypass patch — OBSOLETE in Hermes >= 0.12.0 (the
# upstream chatter filter the patch extended was removed). The patch
# script (tools/patch-bridge-filter.py) was deleted in the 2026-05-04
# canonical-cleanup; see git tag pre-srilu-cleanup-2026-05-04 if a
# pre-0.12 rollback ever needs the patch back. The marker check below
# only runs when the legacy chatter-filter symbols are still present.
if grep -qE "owner_bypass|FILTER_OWNER_JID" "$BR" 2>/dev/null; then
    grep -q "BEGIN shift-agent-template-bypass" "$BR" || fail "$BR missing BEGIN shift-agent-template-bypass marker (Hermes <0.12.0 chatter filter present)"
    grep -q "END shift-agent-template-bypass" "$BR" || fail "$BR missing END shift-agent-template-bypass marker"
fi

# ─────────────────────────────────────────────────────────────────
# 4. Anchor proximity — bridge.js markers near expected upstream symbols
# ─────────────────────────────────────────────────────────────────

# Only the bridge.js anchor survives the plugin migration: run.py and
# whatsapp.py no longer carry our markers (see the architecture note in section 3),
# so their proximity checks were removed rather than left to fail permanently.

# bridge.js: messageQueue.push inject site.
# Threshold 200 -> 260 (2026-08-07). The 200 was calibrated against the Hermes
# 0.14 bridge layout. On the attested 0.19.1 bridge the first BEGIN marker (the
# module-level sender-id helper block) sits at line 207 and messageQueue.push at
# line 409 — delta 202, i.e. two lines over a threshold that upstream growth had
# simply outrun. The patch is NOT misplaced: this bridge hashes to the attested
# BRIDGE_POST_PATCH_SHA256, which is reproducible by applying our own patch
# scripts to the pristine 0.19.1 bridge, so 202 is exactly where our algorithm
# puts the block. Widened with headroom rather than removed: once the content SHA
# pin above passes, this check is largely redundant, but it still earns its place
# at the NEXT re-baseline, when the SHA legitimately changes and we want a cheap
# signal that the block landed somewhere sensible rather than at the far end of
# the file.
BB=$(grep -n "BEGIN shift-agent-sender-id" "$BR" | head -1 | cut -d: -f1)
BA=$(grep -n "messageQueue.push" "$BR" | head -1 | cut -d: -f1)
[ -n "$BB" ] && [ -n "$BA" ] || fail "$BR missing BEGIN marker or anchor symbol"
DIFF3=$(( BB > BA ? BB - BA : BA - BB ))
[ "$DIFF3" -le 260 ] || fail "$BR BEGIN marker drifted from anchor (delta=$DIFF3 lines)"

# Flyer Studio delivery depends on native media send support. Fail before
# deploy if the pinned Hermes bridge lacks the companion endpoint used by
# safe_io.bridge_send_media().
grep -q "app.post('/send-media'" "$BR" || fail "$BR missing POST /send-media endpoint (flyer media delivery would silently fail)"
grep -q "app.post('/send-cta'" "$BR" || fail "$BR missing POST /send-cta endpoint (flyer campaign buttons would silently disappear)"
grep -q "quick_reply" "$BR" || fail "$BR /send-cta endpoint is not using WhatsApp quick replies"
# Button-response inbound extraction moved to bridge_helpers.js in Hermes 0.19.1
# (bridge.js imports from './bridge_helpers.js'), and our patch injects the
# shift-agent-button-response-body block there rather than into bridge.js. Grepping
# bridge.js for it therefore failed on a correctly-patched tree -- the capability is
# present, the file assumption was stale. Assert it where it actually lives, and
# guard our marker with it so the block cannot be dropped silently.
[ -f "$BRH" ] || fail "$BRH missing (bridge helper module absent; button-response extraction cannot be present)"
grep -q "buttonsResponseMessage" "$BRH" || fail "$BRH missing button-response inbound text extraction (interactive button replies would arrive with no usable text)"
grep -q "BEGIN shift-agent-button-response-body" "$BRH" || fail "$BRH missing BEGIN shift-agent-button-response-body marker (our button-response patch is absent)"
grep -q "END shift-agent-button-response-body" "$BRH" || fail "$BRH missing END shift-agent-button-response-body marker"

# PR-CF6: cf-router plugin requires the pre_gateway_dispatch hook surface in
# gateway/run.py. If Hermes upstream renames or removes the hook, the plugin's
# register() call silently no-ops and our owner #XXXXX interception stops
# working. Verify the hook name is still present in gateway/run.py.
grep -q "pre_gateway_dispatch" "$RUN" || fail "$RUN missing pre_gateway_dispatch hook surface (cf-router plugin would silently fail)"

# ─────────────────────────────────────────────────────────────────
# 4b. Governed transport-budget evidence-enablement harness (AMENDMENT 3)
# ─────────────────────────────────────────────────────────────────
# Repo-only, DEFAULT-OFF. Fail-closed so the immutable RC can NOT be built with
# missing / partial wiring. Verifies: every inserted marker exists EXACTLY ONCE
# post-patch; the startup + shutdown + response-dispatch + provider-boundary hooks
# are all present; and the default-OFF closure (no import-time arming — a socket /
# diagnostic path exists ONLY behind the explicit start()/stop() hooks + the
# GATEWAY_TRANSPORT_EVIDENCE_ENABLED flag). The marker block is only enforced once
# the patch is present, so a pre-harness tree is not required to carry it.
if grep -q "BEGIN shift-agent-transport-evidence-probe" "$RUN" 2>/dev/null; then
    # Single source of truth: the full accept/reject matrix lives in the factored
    # tools/check-transport-evidence-patch.sh (functionally tested by
    # tests/test_transport_evidence_deploy_gate.py). Invoke it with the resolved
    # paths; it fail-closes (non-zero) on any missing/duplicate marker, missing
    # hook/anchor, a non-default-OFF module block, or a version-skew module gap.
    RUN="$RUN" WA="$WA" PLATFORM="$PLATFORM" \
        bash "$SCRIPT_DIR/check-transport-evidence-patch.sh" \
        || fail "transport-evidence harness patch verification failed (see above)"
    info "transport-evidence harness markers + hooks verified (default-OFF)."
fi

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
