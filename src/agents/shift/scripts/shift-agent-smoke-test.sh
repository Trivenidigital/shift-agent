#!/usr/bin/env bash
# shift-agent-smoke-test — verify deployment integrity.
# Runs after deploy. Does NOT send any outbound messages.
# Exit 0 = all checks pass; non-zero = deploy should be rolled back.

set -euo pipefail

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
    /usr/local/bin/shift-agent-tail-logger.py \
    /usr/local/bin/shift-agent-health-check.sh \
    /usr/local/bin/shift-agent-reconcile.py \
    /usr/local/bin/send-routing-accuracy-summary \
    /usr/local/bin/lookup-prior-leads-by-phone ; do
    [ -x "$script" ] || { echo "FAIL: $script missing or not executable"; exit 1; }
done
echo "✓ All scripts present + executable"

# 2. Python modules importable
if ! python3 -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
import schemas, safe_io, exit_codes
print('schema classes:', [c for c in dir(schemas) if not c.startswith('_')][:5])
" > /dev/null; then
    echo "FAIL: Python modules don't import"
    exit 1
fi
echo "✓ Python modules importable"

# 3. Config loads and validates
if ! python3 -c "
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

# 4. Roster loads and validates (if present)
if [ -f /opt/shift-agent/roster.json ]; then
    if ! python3 -c "
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
OWNER_PHONE=$(python3 -c "
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

# 7. Pushover test — uses an unprivileged API endpoint
if ! /usr/local/bin/shift-agent-notify-owner \
        --priority -1 \
        --title "Smoke test" \
        "Shift Agent smoke test — please ignore" ; then
    echo "FAIL: Pushover notification failed — out-of-band alerts won't work"
    exit 1
fi
echo "✓ Pushover channel working"

# 8. systemd units enabled
for unit in hermes-gateway shift-agent-tail-logger.timer shift-agent-health.timer send-routing-accuracy-summary.timer; do
    if ! systemctl is-enabled --quiet "$unit"; then
        echo "FAIL: $unit not enabled"
        exit 1
    fi
done
echo "✓ systemd units enabled"

# 9. systemd unit syntax (catches typos before timer fires)
sd_verify_units=(
    /etc/systemd/system/send-routing-accuracy-summary.service
    /etc/systemd/system/send-routing-accuracy-summary.timer
    /etc/systemd/system/send-routing-accuracy-summary-failure.service
)
# Include Agent #21 prune timer if installed (catches User=/log-path typos)
if [ -f /etc/systemd/system/prune-expense-receipts.service ]; then
    sd_verify_units+=( /etc/systemd/system/prune-expense-receipts.service )
fi
if [ -f /etc/systemd/system/prune-expense-receipts.timer ]; then
    sd_verify_units+=( /etc/systemd/system/prune-expense-receipts.timer )
fi
if ! systemd-analyze verify "${sd_verify_units[@]}" 2>/tmp/sd-verify.log; then
    echo "FAIL: systemd-analyze verify reported issues:" >&2
    cat /tmp/sd-verify.log >&2
    exit 1
fi
echo "✓ systemd units verified (incl. expense-bookkeeper if installed)"

# 10. v0.3: catering schema validation against current state files
#     Catches S1 (quote_text invariant), S6 (regex unification), L0 (phone canon)
#     at smoke-time → triggers auto-rollback before customer impact.
if ! sudo -u shift-agent /opt/shift-agent/venv/bin/python -c "
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

# 11. Agent #21 Expense Bookkeeper — scripts + dirs + perms + disabled-default config
test -x /usr/local/bin/extract-receipt        || { echo "FAIL: extract-receipt missing/not-exec" >&2; exit 1; }
test -x /usr/local/bin/apply-expense-decision || { echo "FAIL: apply-expense-decision missing/not-exec" >&2; exit 1; }
test -x /usr/local/bin/prune-and-expire-expenses.py || { echo "FAIL: prune-and-expire-expenses.py missing/not-exec" >&2; exit 1; }
test -d /opt/shift-agent/state/expense-bookkeeper/receipts || { echo "FAIL: receipts dir missing" >&2; exit 1; }
recpts_perm=$(stat -c '%a' /opt/shift-agent/state/expense-bookkeeper/receipts 2>/dev/null || echo "")
[ "$recpts_perm" = "700" ] || { echo "FAIL: receipts dir perms != 700 (got: $recpts_perm)" >&2; exit 1; }
test -f /opt/shift-agent/qbo_client.py || { echo "FAIL: qbo_client.py missing" >&2; exit 1; }

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

echo ""
echo "=== All smoke checks passed ==="
exit 0
