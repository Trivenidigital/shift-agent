#!/usr/bin/env bash
# shift-agent-smoke-test — verify deployment integrity.
# Runs after deploy. Does NOT send any outbound messages.
# Exit 0 = all checks pass; non-zero = deploy should be rolled back.

set -euo pipefail

# Use Hermes venv Python so pydantic + safe_io + schemas resolve. System
# Python (/usr/bin/python3) lacks pydantic, which would false-fail every
# import probe below.
PY="/usr/local/lib/hermes-agent/venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "FAIL: Hermes venv Python missing or not executable at $PY" >&2
    echo "  Hermes-agent install incomplete? Verify /usr/local/lib/hermes-agent/venv/" >&2
    exit 1
fi

echo "=== Shift Agent smoke test ==="

# 1. Scripts exist and are executable
for script in \
    /usr/local/bin/identify-sender \
    /usr/local/bin/log-decision \
    /usr/local/bin/log-decision-direct \
    /usr/local/bin/create-proposal \
    /usr/local/bin/update-proposal-status \
    /usr/local/bin/send-coverage-message \
    /usr/local/bin/render-coverage-template \
    /usr/local/bin/shift-agent-notify-owner \
    /usr/local/bin/shift-agent-disable \
    /usr/local/bin/shift-agent-enable \
    /usr/local/bin/shift-agent-hermes-permissions \
    /usr/local/bin/shift-agent-tail-logger.py \
    /usr/local/bin/shift-agent-health-check.sh \
    /usr/local/bin/shift-agent-reconcile.py \
    /usr/local/bin/send-routing-accuracy-summary \
    /usr/local/bin/lookup-prior-leads-by-phone \
    /usr/local/bin/create-catering-proposal-options \
    /usr/local/bin/select-catering-proposal \
    /usr/local/bin/create-flyer-project \
    /usr/local/bin/update-flyer-project \
    /usr/local/bin/check-flyer-reference-scope \
    /usr/local/bin/generate-flyer-concepts \
    /usr/local/bin/finalize-flyer-assets \
    /usr/local/bin/handle-flyer-onboarding \
    /usr/local/bin/handle-flyer-intake \
    /usr/local/bin/store-flyer-brand-asset \
    /usr/local/bin/manage-flyer-account \
    /usr/local/bin/manage-flyer-guest-order \
    /usr/local/bin/flyer-delivery-report \
    /usr/local/bin/flyer-manual-queue \
    /usr/local/bin/send-flyer-campaign \
    /usr/local/bin/smoke-flyer-quality \
    /usr/local/bin/send-flyer-package ; do
    [ -x "$script" ] || { echo "FAIL: $script missing or not executable"; exit 1; }
done
echo "✓ All scripts present + executable"

if ! /usr/local/bin/shift-agent-hermes-permissions > /dev/null; then
    echo "FAIL: Hermes runtime permissions preflight failed"
    exit 1
fi
echo "✓ Hermes runtime permissions verified"

BRIDGE_JS="/root/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js"
if [ -f "$BRIDGE_JS" ]; then
    grep -q "app.post('/send-media'" "$BRIDGE_JS" || {
        echo "FAIL: WhatsApp bridge missing /send-media endpoint required for Flyer Studio delivery"
        exit 1
    }
    grep -q "app.post('/send-cta'" "$BRIDGE_JS" || {
        echo "FAIL: WhatsApp bridge missing /send-cta endpoint required for Flyer Studio campaign CTAs"
        exit 1
    }
    echo "✓ WhatsApp bridge exposes /send-media and /send-cta"
else
    echo "FAIL: WhatsApp bridge source not found at $BRIDGE_JS"
    exit 1
fi

# 2. Python modules importable + safe_io chokepoint symbols present
# Symbol list lives in src/platform/scripts/check-safe-io-symbols — single
# source of truth shared with shift-agent-deploy.sh pre-restart gate.
if ! "$PY" -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
import schemas, safe_io, exit_codes
import flyer_render
import flyer_workflow
import flyer_onboarding
import flyer_account
import flyer_starter_briefs
import flyer_facts
import flyer_reference_extract
import flyer_visual_qa
import flyer_manual_queue
print('schema classes:', [c for c in dir(schemas) if not c.startswith('_')][:5])
" > /dev/null; then
    echo "FAIL: Python modules don't import"
    exit 1
fi
# Wrap check-safe-io-symbols in "$PY" for the same reason as the other
# Python invocations: the script's #!/usr/bin/env python3 shebang would
# land on system Python, which lacks pydantic. Works today only because
# safe_io.py lazy-imports pydantic — guard against future changes.
if ! "$PY" /usr/local/bin/check-safe-io-symbols > /dev/null; then
    echo "FAIL: safe_io chokepoint symbols missing — run check-safe-io-symbols for details"
    exit 1
fi
echo "✓ Python modules importable (incl. safe_io chokepoint symbols)"

# 2a. Credential-minimized readiness report. Informational only: the strict
# external-foundation gate runs pre-install in shift-agent-deploy.sh, where a
# missing Hermes bundled skill can abort before app state changes. Post-restart
# smoke must not be the first strict check for external Hermes install state.
if ! sudo -u shift-agent "$PY" /usr/local/bin/smoke-flyer-quality --final-package > /dev/null; then
    echo "FAIL: Flyer quality deterministic smoke failed"
    exit 1
fi
echo "Flyer quality deterministic smoke passed"

REF_SMOKE_DIR="$(mktemp -d /tmp/flyer-reference-smoke.XXXXXX)"
cleanup_ref_smoke() { rm -rf "$REF_SMOKE_DIR"; }
trap cleanup_ref_smoke EXIT
mkdir -p "$REF_SMOKE_DIR/assets"
printf 'fake image bytes' > "$REF_SMOKE_DIR/menu.png"
cat > "$REF_SMOKE_DIR/config.yaml" <<'YAML'
schema_version: 1
customer:
  name: Smoke
  location_id: smoke
  timezone: America/New_York
owner:
  name: Owner
  phone: "+19045550000"
limits: {}
alerting:
  pushover_user_key: k
  pushover_app_token: t
backup:
  gpg_recipient_email: owner@example.com
flyer:
  enabled: true
  draft_image_model: deterministic-renderer
  draft_image_quality: low
  concept_count: 1
YAML
chown -R shift-agent:shift-agent "$REF_SMOKE_DIR"
if ! sudo -u shift-agent env FLYER_STATE_ROOT="$REF_SMOKE_DIR" "$PY" /usr/local/bin/create-flyer-project \
    --customer-phone +19045550123 \
    --message-id smoke-reference-menu \
    --raw-request "Create a flyer for Smoke Menu. Contact +19045550123. Create a flyer from this attached menu." \
    --reference-media-path "$REF_SMOKE_DIR/menu.png" \
    --state-path "$REF_SMOKE_DIR/projects.json" \
    --customer-state-path "$REF_SMOKE_DIR/customers.json" \
    --asset-dir "$REF_SMOKE_DIR/assets" \
    --defer-reference-extraction > "$REF_SMOKE_DIR/create.json"; then
    echo "FAIL: Flyer deferred reference create smoke failed"
    exit 1
fi
REF_ASSET_PATH="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["assets"][0]["path"])' "$REF_SMOKE_DIR/create.json")"
printf 'Idly $7\nDosa $8\n' > "${REF_ASSET_PATH}.ocr.txt"
if ! sudo -u shift-agent env FLYER_STATE_ROOT="$REF_SMOKE_DIR" FLYER_REFERENCE_ALLOW_SIDECAR=1 FLYER_QA_ALLOW_SIDECAR=1 "$PY" /usr/local/bin/generate-flyer-concepts \
    --project-id F0001 \
    --state-path "$REF_SMOKE_DIR/projects.json" \
    --asset-dir "$REF_SMOKE_DIR/assets" \
    --config-path "$REF_SMOKE_DIR/config.yaml" > "$REF_SMOKE_DIR/generate.json"; then
    echo "FAIL: Flyer deferred reference generate smoke failed"
    exit 1
fi
if ! "$PY" - "$REF_SMOKE_DIR/projects.json" <<'PY' > /dev/null; then
import json, sys
project = json.load(open(sys.argv[1], encoding="utf-8"))["projects"][0]
values = {fact["value"] for fact in project.get("locked_facts", [])}
assert {"Idly", "$7", "Dosa", "$8"}.issubset(values)
assert project["reference_extractions"][0]["status"] == "ok"
PY
    echo "FAIL: Flyer deferred reference facts smoke failed"
    exit 1
fi
trap - EXIT
cleanup_ref_smoke
echo "Flyer deferred reference extraction smoke passed"

if ! sudo -u shift-agent "$PY" /usr/local/bin/flyer-delivery-report --json > /dev/null; then
    echo "FAIL: Flyer delivery report failed"
    exit 1
fi
echo "Flyer delivery report smoke passed"

if [ -x /usr/local/bin/credential-minimized-readiness ]; then
    "$PY" /usr/local/bin/credential-minimized-readiness --format text || true
fi

# 2a.1 Production-pilot readiness report. Informational only: customer
# onboarding data can intentionally be absent on rehearsal VPSes. This surfaces
# the blocking rows without making every non-onboarded deploy fail.
if [ -x /usr/local/bin/pilot-readiness-check ]; then
    "$PY" /usr/local/bin/pilot-readiness-check --text || true
fi

# 2b. cf-router plugin (PR-CF6 + PR-CF7) — verify the plugin's hooks +
# actions modules import cleanly and the F7 classifier is reachable.
# A syntax error or broken import in the plugin would otherwise pass
# all other checks and only manifest at first inbound traffic.
if [ -d /root/.hermes/plugins/cf-router ]; then
    if ! "$PY" - <<'PY' > /dev/null; then
from pathlib import Path
for p in [
    Path('/root/.hermes/plugins/cf-router/actions.py'),
    Path('/root/.hermes/plugins/cf-router/hooks.py'),
]:
    compile(p.read_text(), str(p), 'exec')
PY
        echo "FAIL: cf-router plugin actions.py/hooks.py compile check failed"
        exit 1
    fi
    if ! "$PY" -c "
import sys, importlib.util
sys.path.insert(0, '/opt/shift-agent')
spec_a = importlib.util.spec_from_file_location(
    'cf_router_smoke_actions',
    '/root/.hermes/plugins/cf-router/actions.py',
)
ma = importlib.util.module_from_spec(spec_a)
spec_a.loader.exec_module(ma)
# Sanity: classifier reachable + correct signature
ok, signals = ma.classify_catering('catering for 50 people event next Saturday food delivered')
assert ok is True, f'classifier regressed (positive case failed): signals={signals}'
ok2, _ = ma.classify_catering('hi')
assert ok2 is False, 'classifier regressed (too-short case)'
flyer_ok, flyer_signals = ma.classify_flyer_intent('Need flyer for Ugadi Specials with food style')
assert flyer_ok is True, f'flyer classifier regressed: signals={flyer_signals}'
generic_flyer_ok, _ = ma.classify_flyer_intent('Need catering for 80 people event Saturday food delivered')
assert generic_flyer_ok is False, 'flyer classifier stole generic catering'
print('cf-router plugin: actions.py importable + classifiers OK')
" > /dev/null; then
        echo "FAIL: cf-router plugin actions.py broken — would silently fail at first inbound"
        exit 1
    fi
    echo "✓ cf-router plugin compiles + actions importable + classifier sanity"
else
    echo "⚠  cf-router plugin not installed — skipping plugin smoke check"
fi

# 2c. Agent #3 closest-location.py importable + CLI parses (PR-Agent3-v0.1)
if [ -x /usr/local/bin/closest-location.py ]; then
    if ! "$PY" /usr/local/bin/closest-location.py --help > /dev/null 2>&1; then
        echo "FAIL: closest-location.py --help failed (Agent #3 v0.1)"
        exit 1
    fi
    echo "✓ closest-location.py importable + CLI parses"
else
    echo "⚠  closest-location.py not installed — Agent #3 closest-store path will fail at first inbound"
fi

# 2d. Agent #13 check-compliance-deadlines.py + mark-compliance-item-done.py
# importable + CLI parses (PR-Agent13-v0.1)
if [ -x /usr/local/bin/check-compliance-deadlines.py ]; then
    if ! "$PY" /usr/local/bin/check-compliance-deadlines.py --help > /dev/null 2>&1; then
        echo "FAIL: check-compliance-deadlines.py --help failed (Agent #13 v0.1)"
        exit 1
    fi
    echo "✓ check-compliance-deadlines.py importable + CLI parses"
    # Heartbeat freshness probe: < 28h since last tick (24h schedule + 4h slack
    # for reboot/Persistent catchup) — Reviewer B-v2 H3 fix.
    HB="/opt/shift-agent/state/compliance-last-cron-tick.json"
    if [ -f "$HB" ]; then
        last_tick=$("$PY" -c "import json; print(json.load(open('$HB'))['last_tick_utc'])" 2>/dev/null || echo "")
        if [ -n "$last_tick" ]; then
            age_h=$("$PY" -c "
from datetime import datetime, timezone
last = datetime.fromisoformat('$last_tick'.replace('Z', '+00:00'))
delta = datetime.now(tz=timezone.utc) - last
print(int(delta.total_seconds() / 3600))
" 2>/dev/null || echo "999")
            if [ "$age_h" -gt 28 ]; then
                echo "⚠  compliance heartbeat is ${age_h}h old (>28h) — cron may have stopped"
            else
                echo "✓ compliance heartbeat fresh (${age_h}h old)"
            fi
        fi
    fi
fi
if [ -x /usr/local/bin/mark-compliance-item-done.py ]; then
    if ! "$PY" /usr/local/bin/mark-compliance-item-done.py --help > /dev/null 2>&1; then
        echo "FAIL: mark-compliance-item-done.py --help failed (Agent #13 v0.1)"
        exit 1
    fi
    echo "✓ mark-compliance-item-done.py importable + CLI parses"
fi

# 2e. Creative Catering Proposals (Task 8)
test -f /root/.hermes/skills/creative_catering_proposals/SKILL.md || {
    echo "FAIL: creative_catering_proposals SKILL.md missing" >&2
    exit 1
}
echo "✓ creative_catering_proposals SKILL present"

# 3. Config loads and validates (shift-agent app config at /opt/shift-agent/config.yaml)
if ! "$PY" -c "
import sys, yaml
sys.path.insert(0, '/opt/shift-agent')
from schemas import Config
with open('/opt/shift-agent/config.yaml') as f:
    cfg = Config.model_validate(yaml.safe_load(f))
print(f'config ok: customer={cfg.customer.name}, tz={cfg.customer.timezone}')
" ; then
    echo "FAIL: config.yaml does not validate against Config schema"
    exit 1
fi
echo "✓ config.yaml validates"

# 3a. Hermes config.yaml shape gate (distinct surface: /root/.hermes/config.yaml).
# Two stated purposes:
#   (1) regression guard on the gate binary itself (catches install_artifacts drift)
#   (2) second warning channel for WARN-level issues (unknown keys, sub-key typos)
# Fail here triggers the existing smoke→auto-rollback path.
#
# FAIL-CLOSED on missing binary post-forward-deploy: deploy-side install
# pipeline guarantees presence at /usr/local/bin/. Absence at smoke means
# install_artifacts() drift — exactly the regression class this smoke step
# exists to catch. (Rollback to a pre-merge tarball would run an OLDER smoke
# script that doesn't have step 3a, so the asymmetry is self-consistent.)
if [ ! -x /usr/local/bin/check-hermes-config-yaml ]; then
    echo "FAIL: /usr/local/bin/check-hermes-config-yaml not installed — install_artifacts() regression"
    exit 1
fi
# Single helper invocation: capture stdout (JSON envelope) AND stderr (human
# text) from the SAME call, so we never re-invoke the helper on failure (would
# reintroduce a TOCTOU window where config.yaml could change between the
# JSON-probe call and the diagnostic call). Parse exit code from the envelope.
HERMES_CFG_STDERR_FILE=$(mktemp)
HERMES_CFG_JSON=$("$PY" /usr/local/bin/check-hermes-config-yaml --json /root/.hermes/config.yaml 2>"$HERMES_CFG_STDERR_FILE" || true)
if ! "$PY" -c "
import json, sys
try:
    sys.exit(0 if json.loads(sys.argv[1]).get('ok') else 1)
except Exception:
    sys.exit(1)
" "$HERMES_CFG_JSON" 2>/dev/null; then
    echo "FAIL: Hermes config.yaml shape gate (smoke-side) reported issues"
    cat "$HERMES_CFG_STDERR_FILE" >&2 || true
    rm -f "$HERMES_CFG_STDERR_FILE"
    exit 1
fi
rm -f "$HERMES_CFG_STDERR_FILE"
echo "✓ Hermes config.yaml shape gate (smoke-side)"

# 4. Roster loads and validates (if present)
if [ -f /opt/shift-agent/roster.json ]; then
    if ! "$PY" -c "
import sys, json
sys.path.insert(0, '/opt/shift-agent')
from schemas import Roster
with open('/opt/shift-agent/roster.json') as f:
    r = Roster.model_validate(json.load(f))
print(f'roster ok: {len(r.employees)} employees, {len(r.schedule)} days scheduled')
" ; then
        echo "FAIL: roster.json does not validate against Roster schema"
        exit 1
    fi
    echo "✓ roster.json validates"
else
    echo "⚠  roster.json not present yet (customer data pending)"
fi

# 5. identify-sender works on the owner's own phone
# Use Python to parse YAML; bash+awk+tr quoting here is fragile.
OWNER_PHONE=$("$PY" -c "
import yaml, sys
try:
    with open('/opt/shift-agent/config.yaml') as f:
        cfg = yaml.safe_load(f)
    print(cfg.get('owner', {}).get('phone', ''))
except Exception as e:
    sys.stderr.write(f'(owner phone extraction failed: {e})')
" 2>/dev/null)

if [ -n "$OWNER_PHONE" ] && [ "$OWNER_PHONE" != "+10000000000" ]; then
    result=$(/usr/local/bin/identify-sender "$OWNER_PHONE")
    if ! echo "$result" | grep -q '"role":\s*"owner"'; then
        echo "FAIL: identify-sender does not classify owner phone correctly: $result"
        exit 1
    fi
    echo "✓ identify-sender recognizes owner"
fi

# 6. render-coverage-template works
if ! /usr/local/bin/render-coverage-template coverage_message_to_candidate --fields-json '{
    "candidate_name":"Test Candidate",
    "absent_employee_name":"Test Absent",
    "absent_date_human":"tomorrow",
    "absent_reason_short":"test",
    "absent_shift":"09:00-17:00",
    "absent_role":"cashier",
    "owner_name":"Test Owner"
}' > /dev/null; then
    echo "FAIL: render-coverage-template failed on sample input"
    exit 1
fi
echo "✓ render-coverage-template works"

# 7. Pushover test — uses an unprivileged API endpoint.
# Skip with WARN if alerting credentials are intentionally muted (operator
# placeholder pattern: keys starting with "MUTED_..."). Used on dev VPS where
# alerts are silenced. Real-credential VPS still get a real-channel probe
# and fail-close on credential breakage.
PUSHOVER_KEY=$("$PY" -c "
import sys, yaml; sys.path.insert(0, '/opt/shift-agent')
with open('/opt/shift-agent/config.yaml') as f:
    cfg = yaml.safe_load(f) or {}
print((cfg.get('alerting') or {}).get('pushover_user_key', ''))
" 2>/dev/null)
if [[ "$PUSHOVER_KEY" == MUTED_* ]]; then
    echo "⚠  Pushover credentials muted (key=$PUSHOVER_KEY) — skipping channel probe (dev VPS)"
elif ! /usr/local/bin/shift-agent-notify-owner \
        --priority -1 \
        --title "Smoke test" \
        "Shift Agent smoke test — please ignore" ; then
    echo "FAIL: Pushover notification failed — out-of-band alerts won't work"
    exit 1
else
    echo "✓ Pushover channel working"
fi

# 8. systemd units enabled
for unit in \
    hermes-gateway \
    shift-agent-tail-logger.timer \
    shift-agent-health.timer \
    shift-agent-health-watchdog.timer \
    shift-agent-backup.timer \
    shift-agent-fsck.timer \
    send-daily-brief.timer \
    catering-pattern-report.timer \
    send-routing-accuracy-summary.timer; do
    if ! systemctl is-enabled --quiet "$unit"; then
        echo "FAIL: $unit not enabled"
        exit 1
    fi
done
echo "✓ systemd units enabled"

# 9. systemd unit syntax (catches typos before timer fires)
sd_verify_units=(
    /etc/systemd/system/catering-pattern-report.service
    /etc/systemd/system/catering-pattern-report.timer
    /etc/systemd/system/send-daily-brief.service
    /etc/systemd/system/send-daily-brief.timer
    /etc/systemd/system/send-routing-accuracy-summary.service
    /etc/systemd/system/send-routing-accuracy-summary.timer
    /etc/systemd/system/send-routing-accuracy-summary-failure.service
)
# Include Agent #21 prune timer if installed AND its venv is present.
# systemd-analyze verify checks ExecStart paths exist at verify time
# (independent of any ConditionPathIsExecutable directive); skip the unit
# if the agent-21 venv (/opt/shift-agent/venv/bin/python) is absent —
# the unit's runtime Condition* directives will then no-op safely.
if [ -f /etc/systemd/system/prune-expense-receipts.service ] \
   && [ -x /opt/shift-agent/venv/bin/python ]; then
    sd_verify_units+=( /etc/systemd/system/prune-expense-receipts.service )
fi
if [ -f /etc/systemd/system/prune-expense-receipts.timer ] \
   && [ -x /opt/shift-agent/venv/bin/python ]; then
    sd_verify_units+=( /etc/systemd/system/prune-expense-receipts.timer )
fi
if ! systemd-analyze verify "${sd_verify_units[@]}" 2>/tmp/sd-verify.log; then
    # systemd-analyze sometimes emits warnings (e.g. "Unknown key name X
    # in section Y, ignoring" for directives unsupported by an older
    # systemd) and exits non-zero. Filter for actual ERROR-class lines
    # before fail-closing the smoke test; pure warnings are informational.
    #
    # IMPORTANT: the warning pattern is "Unknown key name <X>, ignoring".
    # Filter MUST be the AND of both tokens — `Unknown key name.*ignoring` —
    # not the OR `Unknown key name|ignoring`. The OR form would silently
    # drop legitimate error lines like "Failed to parse X, ignoring" or
    # "Executable path not absolute, ignoring", letting real failures
    # bypass the gate.
    if grep -vE "Unknown key name.*ignoring" /tmp/sd-verify.log | grep -qE "[Ee]rror|not executable|not found|[Ff]ailed"; then
        echo "FAIL: systemd-analyze verify reported issues:" >&2
        cat /tmp/sd-verify.log >&2
        exit 1
    fi
    echo "⚠  systemd-analyze emitted warnings (no errors):" >&2
    cat /tmp/sd-verify.log >&2
fi
echo "✓ systemd units verified (incl. expense-bookkeeper if installed)"

# 10. v0.3: catering schema validation against current state files
#     Catches S1 (quote_text invariant), S6 (regex unification), L0 (phone canon)
#     at smoke-time → triggers auto-rollback before customer impact.
if ! sudo -u shift-agent "$PY" -c "
import json, sys, pathlib
sys.path.insert(0, '/opt/shift-agent')
from schemas import CateringLeadStore, MenuPendingUpdate, is_catering_transition_allowed
leads_p = pathlib.Path('/opt/shift-agent/state/catering-leads.json')
if leads_p.exists():
    CateringLeadStore.model_validate(json.loads(leads_p.read_text()))
pending_p = pathlib.Path('/opt/shift-agent/state/catering-menu-pending.json')
if pending_p.exists():
    MenuPendingUpdate.model_validate(json.loads(pending_p.read_text()))
assert not is_catering_transition_allowed('CLOSED', 'NEW'), 'CLOSED is terminal — must not allow NEW'
assert is_catering_transition_allowed('NEW', 'EXTRACTING'), 'NEW->EXTRACTING happy-path'
assert is_catering_transition_allowed('AWAITING_OWNER_APPROVAL', 'OWNER_APPROVED'), 'approve flow'
print('catering schema + transition table validated')
" 2>&1; then
    echo "FAIL: catering schema validation" >&2
    exit 1
fi
echo "✓ catering schema + transition table"

# 11+12. Agent #21 Expense Bookkeeper checks — only run when the agent's
# venv is present. Agent #21 ships disabled-default and its venv at
# /opt/shift-agent/venv/ is created by the operator's bootstrap step
# (see tasks/agent-21-bootstrap.md). On VPS where Agent #21 isn't
# enabled (srilu, fresh installs, demo environments), skip these checks
# with a WARN — the file-presence checks below still run.
if [ -x /opt/shift-agent/venv/bin/python ]; then
    # 11a. Files + perms (always run — these don't need the venv)
    test -x /usr/local/bin/extract-receipt        || { echo "FAIL: extract-receipt missing/not-exec" >&2; exit 1; }
    test -x /usr/local/bin/apply-expense-decision || { echo "FAIL: apply-expense-decision missing/not-exec" >&2; exit 1; }
    test -x /usr/local/bin/prune-and-expire-expenses.py || { echo "FAIL: prune-and-expire-expenses.py missing/not-exec" >&2; exit 1; }
    test -d /opt/shift-agent/state/expense-bookkeeper/receipts || { echo "FAIL: receipts dir missing" >&2; exit 1; }
    recpts_perm=$(stat -c '%a' /opt/shift-agent/state/expense-bookkeeper/receipts 2>/dev/null || echo "")
    [ "$recpts_perm" = "700" ] || { echo "FAIL: receipts dir perms != 700 (got: $recpts_perm)" >&2; exit 1; }
    test -f /opt/shift-agent/qbo_client.py || { echo "FAIL: qbo_client.py missing" >&2; exit 1; }

    # 11b. Schema + config validation (needs Agent-21 venv)
    if ! sudo -u shift-agent /opt/shift-agent/venv/bin/python -c "
import json, sys, pathlib, yaml
sys.path.insert(0, '/opt/shift-agent')
from schemas import Config, ExpenseLeadStore, EXPENSE_TRANSITIONS, is_expense_transition_allowed
cfg = Config.model_validate(yaml.safe_load(open('/opt/shift-agent/config.yaml').read()))
assert cfg.expense_bookkeeper.enabled is False, 'expense_bookkeeper MUST ship disabled (got True)'
assert cfg.expense_bookkeeper.qbo_client_mode == 'mock', 'qbo_client_mode MUST be mock in v0.1'
leads_p = pathlib.Path('/opt/shift-agent/state/expense-bookkeeper/leads.json')
if leads_p.exists():
    ExpenseLeadStore.model_validate(json.loads(leads_p.read_text()))
assert is_expense_transition_allowed('AWAITING_OWNER_APPROVAL', 'APPROVED_PENDING_PUSH')
assert not is_expense_transition_allowed('REVERSED', 'PUSHED')
print('expense_bookkeeper schema + config + transitions validated')
" 2>&1; then
        echo "FAIL: expense_bookkeeper schema/config validation" >&2
        exit 1
    fi
    echo "✓ expense_bookkeeper config + schema + dirs"

    # 12. End-to-end prune-and-expire config-load path
    smoke_out=$(sudo -u shift-agent /opt/shift-agent/venv/bin/python /usr/local/bin/prune-and-expire-expenses.py --dry-run 2>&1)
    if ! echo "$smoke_out" | grep -q "^SMOKE_OK$"; then
        fail_line=$(echo "$smoke_out" | grep "^SMOKE_FAIL:" | head -1)
        [ -n "$fail_line" ] && echo "$fail_line" >&2
        echo "FAIL: prune-and-expire-expenses --dry-run missing OK marker (config-load regression?)" >&2
        echo "$smoke_out" >&2
        exit 1
    fi
    echo "✓ prune-and-expire-expenses --dry-run config-load path"
else
    echo "⚠  Agent #21 venv (/opt/shift-agent/venv/) absent — skipping expense-bookkeeper smoke checks"
fi

echo ""
echo "=== All smoke checks passed ==="
exit 0
