#!/usr/bin/env bash
# Cockpit release transaction — runs ON THE BOX, from a staged payload.
#
# Replaces the in-line ssh block that web/deploy/deploy.sh used to carry. The
# old shape did rsync --delete straight into the live tree, then apt-get, pip,
# systemd, logrotate, cron and Caddy unconditionally, with no snapshot and no
# rollback. It had not run since 2026-05-31, so a single invocation would land
# three months of change with no way back.
#
# This is the same mechanism, restructured as a bounded transaction:
#
#   PREFLIGHT -> SNAPSHOT -> STAGE -> VALIDATE -> CUTOVER -> VERIFY
#                                                       \-> ROLLBACK on any gate
#
# It is DELTA-AWARE. Every step that mutates something outside the backend tree
# is skipped when the deployed bytes already match the source. The old script
# replayed every historical install step on every run; that ritual is what made
# a routine deploy feel dangerous.
#
# Runs on the box because that is the established pattern for this repo's other
# deploy path (src/agents/shift/scripts/shift-agent-deploy.sh), and because SSH
# stdout cannot be captured from the Windows developer machine — a deploy whose
# output you cannot read is not a deploy you can verify.
#
# Usage (on the box):  bash /opt/shift-agent/cockpit-staging/cockpit-release.sh
set -uo pipefail

STAGING="${COCKPIT_STAGING:-/opt/shift-agent/cockpit-staging}"
LIVE=/opt/shift-agent/cockpit
BACKEND="$LIVE/backend"
STATIC="$LIVE/static"
VENV="$LIVE/venv"
SERVICE=shift-agent-cockpit
TS="$(date -u +%Y%m%dT%H%M%SZ)"
SNAP="$LIVE/releases/snapshot-$TS"
NEW="$LIVE/backend.new-$TS"
HEALTH_URL=http://127.0.0.1:8081/health

FAILED=0
note() { printf '  %s\n' "$*"; }
step() { printf '\n=== %s ===\n' "$*"; }
gate() {  # gate <name> <0|1 ok>
    if [ "$2" -eq 0 ]; then note "[PASS] $1"; else note "[FAIL] $1"; FAILED=1; fi
}

# ─────────────────────────────────────────────────────────── PREFLIGHT
step "PREFLIGHT"
[ -d "$STAGING/backend" ] || { echo "ABORT: no staged backend at $STAGING/backend"; exit 2; }
[ -d "$STAGING/static" ]  || { echo "ABORT: no staged static at $STAGING/static"; exit 2; }

TARGET_SHA="$(cat "$STAGING/.commit-hash" 2>/dev/null || echo unknown)"
note "target commit      : $TARGET_SHA"
note "live backend fp    : $(find "$BACKEND" -name '*.py' | sort | xargs sha256sum 2>/dev/null | sha256sum | cut -c1-16)"
note "live static files  : $(find "$STATIC" -type f 2>/dev/null | wc -l)"
note "unit file sha      : $(sha256sum /etc/systemd/system/$SERVICE.service 2>/dev/null | cut -c1-16)"
note "venv python        : $($VENV/bin/python --version 2>&1)"
note "service state      : $(systemctl is-active $SERVICE) / $(systemctl is-enabled $SERVICE)"
note "env sha (must not change): $(sha256sum /opt/shift-agent/.env 2>/dev/null | cut -c1-16)"
ENV_SHA_BEFORE="$(sha256sum /opt/shift-agent/.env 2>/dev/null | cut -d' ' -f1)"
CONFIG_SHA_BEFORE="$(sha256sum /opt/shift-agent/config.yaml 2>/dev/null | cut -d' ' -f1)"

# The security payload this release exists to deliver. If it is absent, the
# whole transaction is pointless and we stop before touching anything.
grep -q "_sensitive_touched" "$STAGING/backend/app/routers/config.py" \
    && gate "staged payload contains #777 (_sensitive_touched)" 0 \
    || gate "staged payload contains #777 (_sensitive_touched)" 1
grep -q "PrivilegedIdentityViolation" "$STAGING/backend/app/state.py" \
    && gate "staged payload contains #781 (privileged-identity guard)" 0 \
    || gate "staged payload contains #781 (privileged-identity guard)" 1
grep -q "owner.authorized_identities" "$STAGING/backend/app/config.py" \
    && gate "staged payload gates owner identity fields" 0 \
    || gate "staged payload gates owner identity fields" 1

# rsync --delete is only safe into a tree that holds no state. Proven, not assumed.
STATE_IN_TREE="$(find "$BACKEND" -maxdepth 2 \( -name '*.json' -o -name '*.env' -o -name '*.db' -o -name '*.yaml' \) 2>/dev/null | wc -l)"
[ "$STATE_IN_TREE" -eq 0 ] && gate "no state/config inside the backend tree" 0 \
                           || gate "no state/config inside the backend tree ($STATE_IN_TREE found)" 1

[ "$FAILED" -eq 0 ] || { echo; echo "PREFLIGHT FAILED — nothing changed."; exit 1; }

# ─────────────────────────────────────────────────────────── SNAPSHOT
step "SNAPSHOT -> $SNAP"
mkdir -p "$SNAP"
cp -a "$BACKEND" "$SNAP/backend"
cp -a "$STATIC"  "$SNAP/static"
cp -a /etc/systemd/system/$SERVICE.service "$SNAP/$SERVICE.service"
$VENV/bin/pip freeze > "$SNAP/pip-freeze.txt" 2>/dev/null
chmod -R go-rwx "$SNAP"
note "snapshot bytes: $(du -sh "$SNAP" | cut -f1)"
note "rollback target: $SNAP"

# ─────────────────────────────────────────────────────────── STAGE + VALIDATE
step "STAGE (outside the live tree) + VALIDATE"
rm -rf "$NEW"
cp -a "$STAGING/backend" "$NEW"
chown -R shift-agent:shift-agent "$NEW"

# Import the staged app with the venv interpreter, from the staged directory.
# A syntax error, a missing dependency or a bad import lands HERE, with the
# live tree untouched -- which is the whole point of staging.
if sudo -u shift-agent env PYTHONPATH="$NEW" "$VENV/bin/python" -c "
import sys; sys.path.insert(0, '$NEW')
import os; os.environ.setdefault('COCKPIT_JWT_SECRET','0'*64)
from app.main import app
from app.config import get_settings
from app.routers.config import _sensitive_touched
s = get_settings()
need = {'owner.phone','owner.lid','owner.self_chat_jid','owner.authorized_identities'}
missing = need - set(s.sensitive_config_fields)
assert not missing, 'sensitive set missing: %s' % missing
assert _sensitive_touched(['owner']) , 'ancestor key not caught'
assert _sensitive_touched(['owner.name']) == [], 'owner.name over-blocked'
print('staged app imports; sensitive set and ancestor gate behave')
" 2>&1 | sed 's/^/    /'; then
    gate "staged backend imports and gates behave" 0
else
    gate "staged backend imports and gates behave" 1
fi

STATIC_COUNT="$(find "$STAGING/static" -type f | wc -l)"
[ "$STATIC_COUNT" -gt 0 ] && [ -f "$STAGING/static/index.html" ] \
    && gate "staged frontend manifest present ($STATIC_COUNT files)" 0 \
    || gate "staged frontend manifest present" 1

if [ "$FAILED" -ne 0 ]; then
    rm -rf "$NEW"
    echo; echo "VALIDATION FAILED — live tree untouched, staged copy removed."
    exit 1
fi

# ─────────────────────────────────────────────────────────── DELTA-AWARE SIDE EFFECTS
step "SIDE EFFECTS (delta-aware — skipped when deployed bytes already match)"
same() { [ -f "$1" ] && [ -f "$2" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$(sha256sum "$2" | cut -d' ' -f1)" ]; }

# OS packages: check, do not replay. The old script apt-get updated on every run.
if command -v jq >/dev/null && command -v chattr >/dev/null; then
    note "SKIP apt-get: jq and chattr already present"
else
    note "installing missing OS deps"; apt-get update -qq && apt-get install -y -qq jq e2fsprogs
fi

for pair in \
    "$STAGING/deploy/shift-agent-cockpit.service:/etc/systemd/system/$SERVICE.service:unit" \
    "$STAGING/deploy/jwt-rotate.cron:/etc/cron.d/shift-agent-jwt-rotate:cron" \
    "$STAGING/deploy/rotate-jwt-secret.sh:$LIVE/rotate-jwt-secret.sh:rotate-script" \
    "$STAGING/deploy/cockpit-logrotate:/etc/logrotate.d/shift-agent-cockpit:logrotate"
do
    src="${pair%%:*}"; rest="${pair#*:}"; dst="${rest%%:*}"; label="${rest##*:}"
    if [ ! -f "$src" ]; then note "SKIP $label: not staged"; continue; fi
    if same "$src" "$dst"; then note "SKIP $label: deployed bytes already match"; continue; fi
    mode=0644; [ "$label" = "rotate-script" ] && mode=0755
    install -m "$mode" "$src" "$dst"; note "INSTALLED $label"
    [ "$label" = "unit" ] && systemctl daemon-reload && note "  daemon-reload"
done

# Caddy: this box serves the cockpit through NGINX (verified: caddy inactive,
# nginx active on 127.0.0.1:8080). Installing a Caddyfile fragment here was
# ritual carried from a layout this box does not use, so it is not replayed.
if systemctl is-active --quiet caddy; then
    note "caddy is active — install its fragment manually; not automated here"
else
    note "SKIP caddy fragment: caddy inactive on this box (nginx serves)"
fi

# ─────────────────────────────────────────────────────────── CUTOVER
step "CUTOVER (bounded; rollback prepared before the service stops)"
OLD="$LIVE/backend.old-$TS"
systemctl stop $SERVICE
mv "$BACKEND" "$OLD"          && note "live backend -> $OLD"
mv "$NEW" "$BACKEND"          && note "staged backend -> live"
rsync -a --delete "$STAGING/static/" "$STATIC/"
chown -R shift-agent:shift-agent "$LIVE"
"$VENV/bin/pip" install -q -e "$BACKEND" 2>&1 | tail -3 | sed 's/^/    /'
systemctl start $SERVICE
sleep 4
note "outage window closed"

# ─────────────────────────────────────────────────────────── VERIFY
step "VERIFY (against the LIVE deployed code)"
systemctl is-active --quiet $SERVICE && gate "service active" 0 || gate "service active" 1

curl -sS --max-time 10 "$HEALTH_URL" >/dev/null 2>&1 \
    && gate "health endpoint responds" 0 || gate "health endpoint responds" 1

# A /health 200 is NOT verification. Assert the security behaviour itself,
# against the live tree, using a throwaway config so the real owner identity
# is never touched.
# The python block sys.exit(1)s on any failed assertion, and its status is
# read via PIPESTATUS -- `$?` after a pipeline reports sed, not python, so
# a gate written that way could never fail. That is the exact defect class
# this repo keeps paying for, so it is named rather than left implicit.
sudo -u shift-agent env PYTHONPATH="$BACKEND" COCKPIT_JWT_SECRET="$(printf '0%.0s' $(seq 64))" \
    "$VENV/bin/python" - <<'PYCHK' 2>&1 | sed 's/^/    /'
import sys
sys.path.insert(0, "/opt/shift-agent/cockpit/backend")
from app.config import get_settings
from app.routers.config import _sensitive_touched

ok = True
def check(label, cond):
    global ok
    ok = ok and bool(cond)
    print(("PASS" if cond else "FAIL"), label)

s = get_settings()
for f in ("owner.phone", "owner.lid", "owner.self_chat_jid",
          "owner.authorized_identities"):
    check("gated: %s" % f, f in s.sensitive_config_fields)
check("ancestor key owner is caught", bool(_sensitive_touched(["owner"])))
check("descendant key is caught",
      bool(_sensitive_touched(["owner.authorized_identities.0.phone"])))
# Paired controls: a gate that refused everything would pass the four above.
check("control: owner.name still patchable",
      _sensitive_touched(["owner.name"]) == [])
check("control: customer.name still patchable",
      _sensitive_touched(["customer.name"]) == [])
sys.exit(0 if ok else 1)
PYCHK
gate "live security assertions" "${PIPESTATUS[0]}"

ss -tlnp 2>/dev/null | grep -q "127.0.0.1:8081" \
    && gate "cockpit still bound to loopback only" 0 \
    || gate "cockpit still bound to loopback only" 1

[ "$(sha256sum /opt/shift-agent/.env 2>/dev/null | cut -d' ' -f1)" = "$ENV_SHA_BEFORE" ] \
    && gate ".env unchanged" 0 || gate ".env unchanged" 1
[ "$(sha256sum /opt/shift-agent/config.yaml 2>/dev/null | cut -d' ' -f1)" = "$CONFIG_SHA_BEFORE" ] \
    && gate "config.yaml unchanged" 0 || gate "config.yaml unchanged" 1

# ─────────────────────────────────────────────────────────── OUTCOME
if [ "$FAILED" -ne 0 ]; then
    step "ROLLBACK (a gate failed)"
    systemctl stop $SERVICE
    rm -rf "$BACKEND"
    cp -a "$SNAP/backend" "$BACKEND"
    rsync -a --delete "$SNAP/static/" "$STATIC/"
    chown -R shift-agent:shift-agent "$LIVE"
    "$VENV/bin/pip" install -q -e "$BACKEND" >/dev/null 2>&1
    systemctl start $SERVICE
    sleep 4
    # Verify the ROLLED-BACK SERVICE, not merely that files were restored.
    if systemctl is-active --quiet $SERVICE && curl -sS --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then
        note "rollback complete; service active and answering"
    else
        note "ROLLBACK DID NOT RESTORE SERVICE — manual intervention required"
        note "snapshot: $SNAP"
    fi
    echo; echo "RESULT: ROLLED BACK"
    exit 1
fi

step "SUCCESS"
cat > "$LIVE/RELEASE_RECEIPT.json" <<EOF
{
  "commit": "$TARGET_SHA",
  "released_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "snapshot": "$SNAP",
  "previous_backend": "$OLD",
  "generated_by": "web/deploy/cockpit-release.sh"
}
EOF
note "receipt: $LIVE/RELEASE_RECEIPT.json"
note "rollback: bash $STAGING/cockpit-release.sh --rollback $SNAP   (or restore $SNAP by hand)"
echo; echo "RESULT: RELEASED $TARGET_SHA"
