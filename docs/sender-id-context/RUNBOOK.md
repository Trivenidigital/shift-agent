# RUNBOOK: Sender Identity Context Injection

Operations cheat sheet for Phase A/B/C deploys, rollback, and debugging.

## Phase definitions

| Phase | What's enabled | Risk |
|---|---|---|
| **A** | Code present, all flags off. `identify-sender` accepts LID inputs but no roster has LIDs yet (returns unknown for LID input). bridge.js has helpers but cache write is gated. | Zero behavior change. Safe to ship anytime. |
| **B** | `HERMES_INJECT_SENDER_CONTEXT=1` and `WHATSAPP_LID_CACHE_WRITE=1` in env. SKILL.md updated to parse v=1 block. Hermes restarted. | Live impact: every WhatsApp inbound now has the v=1 block prepended. Dispatcher fail-closed if block invalid. |
| **C** | Cron `shift-agent-lid-learn` installed. Roster auto-learns LIDs every 5 minutes from cache. | First-message-from-employee resolves to `unknown` until cron runs; subsequent messages resolve. |

## Phase A deploy

```
# Local
git pull origin main
bash tools/shift-agent-patches-deploy.sh main-vps

# On VPS (verifies markers, exits non-zero on drift)
sudo /opt/shift-agent/working/tools/check-shift-agent-patch.sh

# Run pytest on VPS
cd /opt/shift-agent/working && \
  sudo /opt/shift-agent/venv/bin/python3 -m pytest tests/ -q
```

Acceptance: 8/8 existing E2E + new lid+sender tests all green.

## Phase B turn-on

```
# 1. Update env files (both)
sudo sed -i '/^HERMES_INJECT_SENDER_CONTEXT=/d' /root/.hermes/.env
echo 'HERMES_INJECT_SENDER_CONTEXT=1' | sudo tee -a /root/.hermes/.env

sudo sed -i '/^WHATSAPP_LID_CACHE_WRITE=/d' /root/.hermes/.env
echo 'WHATSAPP_LID_CACHE_WRITE=1' | sudo tee -a /root/.hermes/.env

# 2. Update SKILL.md atomically with the env flip:
sudo install -m 0644 -o shift-agent -g shift-agent \
  /opt/shift-agent/working/skills/dispatch_shift_agent/SKILL.md \
  /root/.hermes/skills/dispatch_shift_agent/SKILL.md

sudo install -m 0644 -o shift-agent -g shift-agent \
  /opt/shift-agent/working/skills/handle_sick_call/SKILL.md \
  /root/.hermes/skills/handle_sick_call/SKILL.md

# 3. Restart Hermes
sudo systemctl restart hermes-gateway

# 4. Validate (check creds.json paired correctly + filter still on)
curl -sS http://127.0.0.1:3000/health
```

## Phase C cron install

```
sudo install -m 0644 -o root -g root \
  /opt/shift-agent/working/web/deploy/jobs/shift-agent-lid-learn.cron \
  /etc/cron.d/shift-agent-lid-learn

# Verify cron picked it up:
sudo systemctl status cron
ls /etc/cron.d/shift-agent-lid-learn

# First manual run to seed (roster has any cached LIDs from since-flag-flip):
sudo -u shift-agent /opt/shift-agent/venv/bin/python3 /usr/local/bin/shift-agent-lid-learn

# Confirm:
sudo grep -c "lid_learned" /opt/shift-agent/logs/decisions.log
sudo cat /opt/shift-agent/state/lid-cache.json | python3 -m json.tool
```

## Rollback (reverse order)

### From C → B (turn off cron)
```
sudo rm /etc/cron.d/shift-agent-lid-learn
```

### From B → A (disable runtime injection)
```
sudo sed -i 's/^HERMES_INJECT_SENDER_CONTEXT=.*/HERMES_INJECT_SENDER_CONTEXT=0/' /root/.hermes/.env
sudo sed -i 's/^WHATSAPP_LID_CACHE_WRITE=.*/WHATSAPP_LID_CACHE_WRITE=0/' /root/.hermes/.env

# Revert SKILL.md (use git to fetch the pre-Phase-B version):
sudo git -C /opt/shift-agent/working show <pre-merge-sha>:skills/dispatch_shift_agent/SKILL.md \
  | sudo tee /root/.hermes/skills/dispatch_shift_agent/SKILL.md > /dev/null
sudo git -C /opt/shift-agent/working show <pre-merge-sha>:skills/handle_sick_call/SKILL.md \
  | sudo tee /root/.hermes/skills/handle_sick_call/SKILL.md > /dev/null

sudo systemctl restart hermes-gateway
```

### From A → no patch (full revert)

CRITICAL ORDER: strip `lid` from state files BEFORE reverting `schemas.py`.
Otherwise `extra="forbid"` validation breaks every roster/config read.

```
# 1. Strip lid from roster.json
sudo -u shift-agent jq 'del(.employees[].lid)' /opt/shift-agent/roster.json \
  > /tmp/roster.tmp && sudo -u shift-agent mv /tmp/roster.tmp /opt/shift-agent/roster.json

# 2. Strip lid from config.yaml owner
sudo -u shift-agent yq -i 'del(.owner.lid)' /opt/shift-agent/config.yaml

# 3. Now safe to revert schemas.py to the pre-feature commit:
sudo git -C /opt/shift-agent/working revert <feature-merge-sha>

# 4. Re-deploy:
sudo install -m 0644 -o shift-agent -g shift-agent \
  /opt/shift-agent/working/schemas.py /opt/shift-agent/schemas.py

# 5. Stop bridge from writing cache (already done in B→A step above):
# 6. Optionally: revert Hermes patches by deleting BEGIN/END blocks
#    (or running tools/patch-hermes.py in reverse — manual).
```

## Common issues

### "wrong-name greeting" returns

Symptom: agent greets employee by their WhatsApp profile name instead of
roster name (e.g., "Got it, Srini" when sender is Anjali).

Cause: dispatch_shift_agent SKILL.md not updated, OR
`HERMES_INJECT_SENDER_CONTEXT=0`, OR Kimi ignored the v=1 block.

Diagnosis:
```
# Confirm flag is on
sudo grep HERMES_INJECT_SENDER_CONTEXT /root/.hermes/.env

# Confirm SKILL.md has the new dispatch parsing rules
sudo grep -c "validate-sender-block" /root/.hermes/skills/dispatch_shift_agent/SKILL.md

# Confirm the bridge is sending senderPhone/senderLid
sudo tail -50 /root/.hermes/whatsapp/bridge.log | grep -E "fromMe|senderPhone|senderLid"
```

### Cache file gets large

Run lid-learn manually:
```
sudo -u shift-agent /opt/shift-agent/venv/bin/python3 /usr/local/bin/shift-agent-lid-learn
```

If it doesn't shrink, the cache contains entries for unknown phones (not in
roster). Investigate via:
```
sudo cat /opt/shift-agent/state/lid-cache.json | python3 -m json.tool
```

### Roster validation error after upgrade

If `roster.json` validates fine but `schemas.py` rejects with `extra
fields not permitted`, you have a partially-rolled-back state. Run the
rollback Step 1+2 above to strip `lid` from state files, then re-validate.

## Manual one-shot test

After Phase B+C, run end-to-end against a fresh employee:

```
# 1. Pick an employee whose lid is NOT in roster yet:
sudo grep -L "lid" /opt/shift-agent/roster.json && echo "no lids set yet"

# 2. Send a sick call from that employee's phone to owner WA.

# 3. Watch /opt/shift-agent/state/lid-cache.json — should gain a pair
#    within ~1 second of the message arriving.

# 4. Wait up to 5 min for cron, then:
sudo cat /opt/shift-agent/roster.json | python3 -c \
  "import sys,json; r=json.load(sys.stdin); [print(e['id'], e.get('lid')) for e in r['employees']]"

# 5. Send a SECOND sick call from the same phone. This time the roster
#    has the LID, so identify-sender resolves immediately.
```
