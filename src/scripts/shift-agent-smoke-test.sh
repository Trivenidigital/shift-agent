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
    /usr/local/bin/shift-agent-reconcile.py ; do
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
for unit in hermes-gateway shift-agent-tail-logger.timer shift-agent-health.timer; do
    if ! systemctl is-enabled --quiet "$unit"; then
        echo "FAIL: $unit not enabled"
        exit 1
    fi
done
echo "✓ systemd units enabled"

echo ""
echo "=== All smoke checks passed ==="
exit 0
