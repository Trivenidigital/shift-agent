#!/usr/bin/env bash
# check-hermes-config-yaml — fail-closed shape gate over /root/.hermes/config.yaml.
# Pairs with the PR #17 Hermes commit-pin gate and PR #18 .env symlink gate.
#
# Closes the M2 silent-failure surface: typo'd Hermes config keys silently fall
# back to defaults because `hermes config check` / `hermes doctor` do not
# validate YAML shape (verified live 2026-05-16).
#
# Override mechanism (two-variable, attestation-required; mirrors PR #17):
#   HERMES_CONFIG_GATE_OVERRIDE_FIELD=<exact-field-name>  — required
#   HERMES_CONFIG_GATE_OVERRIDE_REASON="<reason>"         — required
# Field name MUST match one of the actual failure-causing fields in this run;
# stale-shell-variable bypass is rejected as ATTESTATION MISMATCH.
#
# Maintenance: when Hermes upgrades, top-level keys may shift. Update
# tools/hermes-config-yaml-baseline.txt's KNOWN_TOP_LEVEL_KEYS by running:
#   python3 -c "import yaml; print(','.join(sorted(yaml.safe_load(open('/root/.hermes/config.yaml')))))"
# Commit + ship a new tarball.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASELINE_FILE="${BASELINE_FILE:-$SCRIPT_DIR/hermes-config-yaml-baseline.txt}"
CONFIG_PATH="${1:-/root/.hermes/config.yaml}"
VENV_PY="${VENV_PY:-/usr/local/lib/hermes-agent/venv/bin/python}"

# Locate the Python helper. Search staging first, then installed location.
HELPER=""
for candidate in \
    "$SCRIPT_DIR/../src/platform/scripts/check-hermes-config-yaml" \
    "/usr/local/bin/check-hermes-config-yaml"; do
    if [ -x "$candidate" ]; then HELPER="$candidate"; break; fi
done
[ -n "$HELPER" ] || { echo "FAIL: check-hermes-config-yaml helper not found in staging or /usr/local/bin" >&2; exit 2; }

fail() { echo "FAIL: $1" >&2; exit 1; }
warn() { echo "WARN: $1" >&2; }
info() { echo "  $1" >&2; }

# Invoke helper ONCE; helper emits BOTH JSON (stdout) AND text (stderr).
# This eliminates the TOCTOU window where calling the helper twice would
# let the config file change between JSON-call and text-call.
# Capture stdout into $JSON; let stderr stream through to operator terminal.
JSON=$("$VENV_PY" "$HELPER" --json --baseline "$BASELINE_FILE" "$CONFIG_PATH") || HELPER_RC=$?
HELPER_RC="${HELPER_RC:-0}"

if [ -z "$JSON" ]; then
    echo "FAIL: helper produced no JSON output (helper_rc=$HELPER_RC)" >&2
    exit 2
fi

# Parse JSON via inline Python (jq not installed on srilu — see deploy.sh:200
# precedent for the same pattern). Argv positional passing — values are NEVER
# interpolated into the Python source string, so shell metacharacters in the
# JSON are safe.
EXIT_CODE=$("$VENV_PY" -c "
import json, sys
print(json.loads(sys.argv[1])['exit_code'])
" "$JSON")

case "$EXIT_CODE" in
    0)
        echo "OK: /root/.hermes/config.yaml shape gate passed."
        exit 0
        ;;
    2)
        echo "FAIL: could not parse Hermes config.yaml. See helper output above." >&2
        exit 2
        ;;
    1)
        # Fail-closed. Check for valid two-variable override.
        OVR_FIELD="${HERMES_CONFIG_GATE_OVERRIDE_FIELD:-}"
        OVR_REASON="${HERMES_CONFIG_GATE_OVERRIDE_REASON:-}"
        if [ -n "$OVR_FIELD" ] && [ -n "$OVR_REASON" ]; then
            # Attestation: the named field must be in the failure list.
            FIELD_MATCH=$("$VENV_PY" -c "
import json, sys
data = json.loads(sys.argv[1])
fields = set(data.get('missing_required', []))
for w in data.get('wrong_shape', []):
    fields.add(w.get('field', ''))
print('1' if sys.argv[2] in fields else '0')
" "$JSON" "$OVR_FIELD")
            if [ "$FIELD_MATCH" = "1" ]; then
                warn "Hermes config.yaml gate override accepted (THIS RUN ONLY — unset HERMES_CONFIG_GATE_OVERRIDE_* after this deploy to avoid sticky-shell-var surprise)"
                info "  field:   $OVR_FIELD"
                info "  reason:  $OVR_REASON"
                info ""
                info "  TO FIX PERMANENTLY: edit /root/.hermes/config.yaml,"
                info "  then re-run this gate to confirm clean."

                # Dual-channel audit. Log ALL failing fields, not just the
                # attested one — operator may attest field A while field B was
                # ALSO failing; the audit record captures the complete failure
                # set. Both channels use `2>/dev/null || true` for disk-full
                # tolerance (matches check-shift-agent-patch.sh §audit precedent;
                # known limitation: if BOTH channels fail, the override is
                # accepted silently).
                TS=$(date -Iseconds)
                OV_LOG=/opt/shift-agent/logs/config-gate-overrides.log
                mkdir -p "$(dirname "$OV_LOG")" 2>/dev/null || true
                ALL_FAILS=$("$VENV_PY" -c "
import json, sys
data = json.loads(sys.argv[1])
fields = list(data.get('missing_required', []))
fields += [w.get('field', '') for w in data.get('wrong_shape', [])]
print(','.join(f for f in fields if f))
" "$JSON")
                printf '%s field=%s all_failures=%s reason=%q\n' \
                    "$TS" "$OVR_FIELD" "$ALL_FAILS" "$OVR_REASON" \
                    >> "$OV_LOG" 2>/dev/null || true

                # Use ConfigGateOverride (not AgentStateChange) so
                # dispatcher-accuracy-report queries don't conflate gate-overrides
                # with agent enable/disable events. NOTE: on the very first
                # deploy of this PR, log-decision-direct on disk uses the OLD
                # schemas.py (no ConfigGateOverride variant yet — install_artifacts
                # hasn't run); validation will fail and this channel silently
                # drops to the plain-text fallback. Subsequent deploys are clean.
                if [ -x /usr/local/bin/log-decision-direct ] && command -v "$VENV_PY" >/dev/null; then
                    ENTRY=$("$VENV_PY" -c "
import json, sys
print(json.dumps({
    'type': 'config_gate_override',
    'ts': sys.argv[1],
    'field': sys.argv[2],
    'all_failures': sys.argv[3],
    'reason': sys.argv[4],
}))
" "$TS" "$OVR_FIELD" "$ALL_FAILS" "$OVR_REASON" 2>/dev/null) || ENTRY=""
                    if [ -n "$ENTRY" ]; then
                        /usr/local/bin/log-decision-direct "$ENTRY" 2>/dev/null || true
                    fi
                fi
                exit 0
            else
                echo "FAIL: HERMES_CONFIG_GATE_OVERRIDE_FIELD=$OVR_FIELD does NOT match" >&2
                echo "  any field in this run's actual failures (missing_required + wrong_shape)." >&2
                echo "  ATTESTATION MISMATCH — override REJECTED." >&2
                echo "  Either fix the config or set OVERRIDE_FIELD to the actual failing field." >&2
                exit 1
            fi
        elif [ -n "$OVR_FIELD" ] || [ -n "$OVR_REASON" ]; then
            echo "FAIL: HERMES_CONFIG_GATE_OVERRIDE incomplete — both" >&2
            echo "  HERMES_CONFIG_GATE_OVERRIDE_FIELD and" >&2
            echo "  HERMES_CONFIG_GATE_OVERRIDE_REASON must be set and non-empty." >&2
            exit 1
        fi
        # No override; fail-closed
        echo "FAIL: /root/.hermes/config.yaml shape gate detected fail-closed issues above." >&2
        echo "  To bypass for one deploy: set BOTH" >&2
        echo "    HERMES_CONFIG_GATE_OVERRIDE_FIELD=<exact-failing-field-name>" >&2
        echo "    HERMES_CONFIG_GATE_OVERRIDE_REASON=\"<reason>\"" >&2
        exit 1
        ;;
    *)
        echo "FAIL: helper returned unexpected exit_code=$EXIT_CODE" >&2
        exit 2
        ;;
esac
