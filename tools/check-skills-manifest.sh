#!/usr/bin/env bash
# check-skills-manifest — deploy-time fail-closed CONTENT gate over /root/.hermes/skills.
#
# Pairs with the required-SKILL PRESENCE gate in shift-agent-deploy.sh: presence proves
# a SKILL.md FILE exists; this proves its CONTENT matches the shipped sha256 manifest
# (tools/skills-manifest.txt). Runs as root, after the rsyncs.
#
# HONEST NOTE on independent value: because this runs AFTER `rsync -a` has just written the
# skills from the same tarball that produced the manifest, at deploy time it largely
# RE-ASSERTS content that was just installed (matches by construction). Its independent
# catches are narrow but real: (a) a same-size+same-mtime on-box mutation that rsync's
# quick-check skipped, (b) a mutated skill from a DISABLED/additively-rsynced agent not
# overwritten this deploy, (c) rsync corruption/partial writes. The substantive
# between-deploy protection against curator/self-writer drift is the D2 watchdog
# (shift-agent-skills-audit), not this gate. See the PR "Threat model & limitations".
#
# Subcommands:
#   build             regenerate tools/skills-manifest.txt from src/agents (dev-side)
#   verify (default)  fail-closed deploy gate against live /root/.hermes/skills
#
# Rollback safety (mirrors presence-gate rollback compat): if the helper or the manifest
# file is absent — e.g. an old rollback tarball predating this gate — WARN + SKIP, never
# fail-closed. The gate only ever fails on a POSITIVELY detected content mismatch.
#
# Override (two-variable attestation, mirrors check-shift-agent-patch.sh §override):
#   SKILLS_MANIFEST_GATE_OVERRIDE_SKILL=<exact-skill-name>   — must be in this run's drift set
#   SKILLS_MANIFEST_GATE_OVERRIDE_REASON="<reason>"          — free text, audited
# Both required; stale-shell-variable bypass is rejected as ATTESTATION MISMATCH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST_FILE="${SKILLS_MANIFEST_FILE:-$SCRIPT_DIR/skills-manifest.txt}"
SKILLS_ROOT="${SKILLS_ROOT:-/root/.hermes/skills}"
VENV_PY="${VENV_PY:-/usr/local/lib/hermes-agent/venv/bin/python}"

fail() { echo "FAIL: $1" >&2; exit 1; }
warn() { echo "WARN: $1" >&2; }
info() { echo "  $1" >&2; }

# Locate the Python helper: staging first, then installed. Use -f (not -x): the helper
# is always invoked via "$PY $HELPER", so the exec bit is irrelevant — and relying on it
# would silently skip the gate if tar-extract on the VPS dropped the mode.
HELPER=""
for candidate in \
    "$SCRIPT_DIR/../src/platform/scripts/check-skills-manifest" \
    "/usr/local/bin/check-skills-manifest"; do
    if [ -f "$candidate" ]; then HELPER="$candidate"; break; fi
done

# Pick a python: prefer the Hermes venv (on-box), else python3/python (dev/staging).
PY="$VENV_PY"
if [ ! -x "$PY" ]; then PY="$(command -v python3 || command -v python || true)"; fi

# ── build subcommand (dev-side): regenerate the committed baseline ────────────
if [ "${1:-verify}" = "build" ]; then
    [ -n "$HELPER" ] || fail "check-skills-manifest helper not found"
    [ -n "$PY" ] || fail "no python interpreter found for build"
    exec "$PY" "$HELPER" build --out "$MANIFEST_FILE"
fi

# ── verify subcommand: fail-closed deploy gate ────────────────────────────────
# Rollback safety: skip (not fail) when the gate surface isn't shipped.
if [ -z "$HELPER" ]; then
    warn "check-skills-manifest helper absent — skipping content gate (rollback compat)"
    exit 0
fi
if [ ! -f "$MANIFEST_FILE" ]; then
    warn "skills-manifest.txt absent ($MANIFEST_FILE) — skipping content gate (rollback compat)"
    exit 0
fi
[ -n "$PY" ] || fail "no python interpreter found for skills-manifest gate"

# Single invocation: helper emits JSON (stdout) + human text (stderr), sets exit code.
JSON=$("$PY" "$HELPER" verify --manifest "$MANIFEST_FILE" --skills-root "$SKILLS_ROOT") || true
[ -n "$JSON" ] || fail "skills-manifest helper produced no JSON output"

EXIT_CODE=$("$PY" -c "import json,sys; print(json.loads(sys.argv[1])['exit_code'])" "$JSON")

case "$EXIT_CODE" in
    0)
        echo "OK: /root/.hermes/skills content matches shipped manifest."
        exit 0
        ;;
    2)
        fail "skills-manifest gate could not run (manifest/parse error above)."
        ;;
    1)
        OVR_SKILL="${SKILLS_MANIFEST_GATE_OVERRIDE_SKILL:-}"
        OVR_REASON="${SKILLS_MANIFEST_GATE_OVERRIDE_REASON:-}"
        if [ -n "$OVR_SKILL" ] && [ -n "$OVR_REASON" ]; then
            MATCH=$("$PY" -c "
import json, sys
print('1' if sys.argv[2] in json.loads(sys.argv[1]).get('changed', []) else '0')
" "$JSON" "$OVR_SKILL")
            if [ "$MATCH" = "1" ]; then
                warn "skills-manifest gate override accepted (THIS RUN ONLY — unset after deploy)"
                info "skill:  $OVR_SKILL"
                info "reason: $OVR_REASON"
                # Dual-channel audit (mirrors check-shift-agent-patch.sh): plain-text
                # fallback always; log-decision-direct best-effort.
                TS=$(date -Iseconds)
                OV_LOG=/opt/shift-agent/logs/skills-manifest-overrides.log
                mkdir -p "$(dirname "$OV_LOG")" 2>/dev/null || true
                printf '%s skill=%s reason=%q\n' "$TS" "$OVR_SKILL" "$OVR_REASON" \
                    >> "$OV_LOG" 2>/dev/null || true
                if [ -x /usr/local/bin/log-decision-direct ]; then
                    ENTRY=$("$PY" -c "
import json, sys
print(json.dumps({'type':'agent_state_change','ts':sys.argv[1],'to_state':'enabled',
  'reason':f'skills_manifest_gate_override skill={sys.argv[2]} reason={sys.argv[3]}'}))
" "$TS" "$OVR_SKILL" "$OVR_REASON" 2>/dev/null) || ENTRY=""
                    [ -n "$ENTRY" ] && /usr/local/bin/log-decision-direct "$ENTRY" 2>/dev/null || true
                fi
                exit 0
            fi
            fail "SKILLS_MANIFEST_GATE_OVERRIDE_SKILL=$OVR_SKILL is NOT in this run's drift set — ATTESTATION MISMATCH; override REJECTED."
        elif [ -n "$OVR_SKILL" ] || [ -n "$OVR_REASON" ]; then
            fail "skills-manifest override incomplete — set BOTH _SKILL and _REASON (non-empty)."
        fi
        echo "FAIL: /root/.hermes/skills content drift from shipped manifest (see above)." >&2
        echo "  A deployed SKILL.md was modified on-box (self-writing Hermes / manual edit)," >&2
        echo "  OR the shipped manifest is stale (was tools/skills-manifest.txt regenerated?)." >&2
        echo "  To bypass ONE deploy: set BOTH" >&2
        echo "    SKILLS_MANIFEST_GATE_OVERRIDE_SKILL=<exact-drifted-skill>" >&2
        echo "    SKILLS_MANIFEST_GATE_OVERRIDE_REASON=\"<reason>\"" >&2
        exit 1
        ;;
    *)
        fail "skills-manifest helper returned unexpected exit_code=$EXIT_CODE"
        ;;
esac
