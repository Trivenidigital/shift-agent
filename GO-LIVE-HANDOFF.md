# Shift Agent — Go-Live Handoff Checklist

**Deployed state on Main VPS (as of 2026-04-24 17:12 UTC):**

- ✅ Gate 6: `shift-agent` system user created; `/opt/shift-agent/` tree with correct ownership
- ✅ Gate 6b: 18 helper scripts in `/usr/local/bin/`; schemas.py + safe_io.py + exit_codes.py + runbook + config template in `/opt/shift-agent/`; 4 message templates; Python venv at `/opt/shift-agent/venv` with pydantic + pyyaml
- ✅ Gate 6c: 5 new skills installed at `/root/.hermes/skills/{dispatch_shift_agent,handle_sick_call,handle_owner_command,handle_candidate_response,roster_lookup}/SKILL.md`; `/root/.hermes` chowned to `shift-agent:shift-agent`; `/root/` chmod 711 for traversal
- ✅ Gate 7a: stub `config.yaml` with clear `PLACEHOLDER_...` markers on customer-specific fields
- ✅ Gate 11 (partial): smoke test validates every component that doesn't need customer data; Pushover fails cleanly on placeholder creds as expected
- ✅ **Safety lock engaged:** `/opt/shift-agent/state/disabled.flag` present → `send-coverage-message` will refuse outbound even if services started accidentally. All systemd units currently **disabled** (won't auto-start on reboot).

---

## YOUR hand — before go-live (in this order)

### 1. Get Pushover credentials (5 min)

1. Sign up at https://pushover.net (first 30 days free, then $5 lifetime per device).
2. Install Pushover app on the customer's phone (or your own, if you're acting as the on-call contact).
3. Get the **User Key** from the main dashboard.
4. Create an **Application** (call it "Shift Agent"), get its **API Token**.

### 2. Customer sends employees the pre-go-live notice (NON-NEGOTIABLE)

Non-optional per GDPR + ethical baseline. Customer messages their staff (group or individually) BEFORE any agent processing:

> Starting [DATE], we're trialing an automated assistant that helps coordinate coverage when someone calls in sick. When you message me you can't make a shift, your name and reason are processed by this system to help arrange coverage faster. Reply STOP here and your sick-call messages will be handled manually like before. Thanks!

**Do not proceed to step 5 unless the customer has sent this notice.** Keep a screenshot for your records.

### 3. Customer signs the 3 disclosures in the runbook

See `/opt/shift-agent/runbook.md` sections "Three disclosures":

- Baileys / WhatsApp ToS risk (customer's number could be restricted by Meta)
- Audit log checksum-only immutability (not admissible as sole evidence in labor disputes)
- Employee notification requirement (must be sent per step 2)

Physical or electronic signature + date on each. Email to yourself for the record.

### 4. Import your GPG public key on the VPS (2 min)

The private key **MUST NOT** be on the VPS. Import only the public key:

```bash
# On your local machine, export the pubkey you want backups encrypted to
gpg --armor --export your-email@example.com > /tmp/shift-agent-backup-pubkey.asc

# Copy to VPS and import as shift-agent user
scp /tmp/shift-agent-backup-pubkey.asc main-vps:/tmp/
ssh main-vps 'sudo -u shift-agent gpg --import /tmp/shift-agent-backup-pubkey.asc && rm /tmp/shift-agent-backup-pubkey.asc'

# Verify
ssh main-vps 'sudo -u shift-agent gpg --list-keys your-email@example.com'
```

### 5. Populate `config.yaml` + `.env` with real values

```bash
ssh main-vps 'sudo -u shift-agent nano /opt/shift-agent/config.yaml'
```

Replace every `PLACEHOLDER_*` marker:
- `customer.name` — customer's business name
- `customer.location_id` — your internal identifier for this location
- `owner.name` — customer's full name
- `owner.phone` — customer's WhatsApp number in E.164 format (e.g. `+19045550100`, no dashes)
- `alerting.pushover_user_key` — from step 1
- `alerting.pushover_app_token` — from step 1
- `backup.gpg_recipient_email` — email associated with the imported pubkey (step 4)

Keep `limits.max_outbound_per_day: 2` for the first 48h (blast-radius cap). Raise to 6 after green rehearsal.

Then populate `.env`:

```bash
ssh main-vps 'sudo -u shift-agent nano /opt/shift-agent/.env'
```

Replace `OPENROUTER_API_KEY=PLACEHOLDER_...` with the real key (the same one already used by Hermes, or a new dedicated one).

### 6. Populate `roster.json` with customer data

When customer returns the roster questionnaire (45 employees), format per the schema in `src/schemas.py` → `Roster`:

```bash
ssh main-vps 'sudo -u shift-agent tee /opt/shift-agent/roster.json' <<'EOF'
{
  "location": {"id": "loc_xxx_01", "name": "...", "timezone": "America/New_York"},
  "employees": [
    {"id": "e001", "name": "...", "nickname": "...", "role": "cashier",
     "phone": "+19045550101", "languages": ["en","te","hi"],
     "can_cover_roles": ["cashier","floor"], "status": "active"}
    // ... 44 more
  ],
  "schedule": {
    "2026-04-28": [
      {"employee_id": "e001", "shift": "09:00-17:00", "role": "cashier"}
      // ...
    ]
  }
}
EOF
```

Validate immediately:
```bash
ssh main-vps '/opt/shift-agent/venv/bin/python3 -c "
import sys, json; sys.path.insert(0, \"/opt/shift-agent\")
from schemas import Roster
r = Roster.model_validate(json.load(open(\"/opt/shift-agent/roster.json\")))
print(f\"roster OK: {len(r.employees)} employees, {len(r.schedule)} days scheduled\")"'
```

### 7. Populate WhatsApp allowlist

Add every employee's phone + customer's phone to `WHATSAPP_ALLOWED_USERS` in `.env`:

```bash
ssh main-vps 'echo "WHATSAPP_ALLOWED_USERS=$(sudo -u shift-agent python3 -c "
import yaml, json
cfg = yaml.safe_load(open(\"/opt/shift-agent/config.yaml\"))
roster = json.load(open(\"/opt/shift-agent/roster.json\"))
phones = [cfg[\"owner\"][\"phone\"]] + [e[\"phone\"] for e in roster[\"employees\"] if e.get(\"status\")==\"active\"]
print(\",\".join(phones))
")" >> /opt/shift-agent/.env'
```

### 8. Run smoke test — must exit 0

```bash
ssh main-vps 'bash /usr/local/bin/shift-agent-smoke-test.sh'
# Expected: all ✓, final line "=== All smoke checks passed ==="
```

If any line starts with `FAIL:`, stop and debug before proceeding.

### 9. Unpair the current burner, pair customer's WhatsApp

On the current burner's phone: WhatsApp → Settings → Linked Devices → remove any Hermes entry.

Then clear the session + pair fresh:

```bash
ssh main-vps 'sudo -u shift-agent rm -rf /root/.hermes/whatsapp/session/*'
ssh main-vps 'sudo -u shift-agent bash -c "export HOME=/opt/shift-agent HERMES_HOME=/root/.hermes; hermes whatsapp"'
# → scan the QR with the CUSTOMER's primary phone
# → wait for "paired" confirmation
# → verify: ls -la /root/.hermes/whatsapp/session/creds.json
```

### 10. Remove safety flag, enable services, smoke test again

```bash
ssh main-vps '
sudo -u shift-agent rm /opt/shift-agent/state/disabled.flag
systemctl enable --now shift-agent-tail-logger.timer
systemctl enable --now shift-agent-health.timer
systemctl enable --now shift-agent-health-watchdog.timer
systemctl enable --now shift-agent-backup.timer
systemctl enable --now shift-agent-fsck.timer
systemctl enable shift-agent-reconcile.service
systemctl enable --now hermes-gateway
sleep 10
bash /usr/local/bin/shift-agent-smoke-test.sh
'
```

### 11. Staging rehearsal (BEFORE customer's real employees message)

From YOUR personal phone → customer's burner-now-primary WhatsApp:

```
Test message — not a real sick call
```

- Nothing should happen (allowlist should drop YOUR phone unless you added it). If your phone IS in the allowlist for testing, agent will process and decline with "I don't recognize this sender" if you're not in roster.

Then from an employee's REAL phone (or simulate — but better real):
```
Boss, I'm <Name>, can't come tomorrow, sick
```

Walk through the full loop:
- Agent acknowledges the employee
- Owner sees the proposal with a `#XXXXX` code in their self-chat
- Owner replies with the code
- Candidate receives the coverage message
- Candidate replies YES
- Owner gets confirmation

Check `decisions.log` after: `sudo -u shift-agent tail /opt/shift-agent/logs/decisions.log | python3 -c "import json, sys; [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin]"`.

### 12. If rehearsal green — you're live

Raise `max_outbound_per_day` in `config.yaml` to the real value (recommend 6 for 45-employee roster) and `systemctl reload hermes-gateway`.

Customer is now in production.

---

## Safety rails (in place, verified)

1. `disabled.flag` currently set → agent refuses outbound even if accidentally started before step 10.
2. All systemd units currently **disabled** → no auto-start on reboot until you explicitly enable.
3. `max_outbound_per_day: 2` in config → max blast radius is 2 outbound msgs/day until you raise it.
4. `shift-agent-notify-owner` requires Pushover creds → agent **refuses to start** if creds are placeholders (Pydantic validator rejects).
5. `send-coverage-message` re-validates candidate phone from `roster.json` on every send → LLM cannot invent a phone number.

## Emergency stop

At any time:
```bash
ssh main-vps 'sudo /usr/local/bin/shift-agent-disable "owner_emergency"'
```
Stops all services, sets disabled flag, sends Pushover alert to owner. Restore with `shift-agent-enable` when resolved.

## Rollback path

Full revert (unlink WhatsApp + stop agent, data preserved):
```bash
ssh main-vps '
systemctl stop hermes-gateway shift-agent-tail-logger.timer shift-agent-health.timer shift-agent-health-watchdog.timer shift-agent-backup.timer shift-agent-fsck.timer
systemctl disable hermes-gateway shift-agent-tail-logger.timer shift-agent-health.timer shift-agent-health-watchdog.timer shift-agent-backup.timer shift-agent-fsck.timer
touch /opt/shift-agent/state/disabled.flag
'
# Then on customer's phone: WhatsApp → Linked Devices → remove Hermes entry
```

`decisions.log` + `pending.json` + `roster.json` all preserved in `/opt/shift-agent/` for dispute / post-mortem.

## Nightly automated safety

- Backup: `tar + gpg --recipient` at 02:00 local → `/opt/shift-agent/backups/YYYY-MM-DD.tar.gz.gpg`
- Fsck: cross-file invariant check at 03:00 local → violations logged + Pushover alert

Both run as systemd timers once you enable them in step 10.

---

**Remaining known issues** (all Phase 1, not blocking beta):

- Health-check uses `jq` — install before go-live: `ssh main-vps 'apt-get install -y jq'`
- GPG `--trust-model always` in backup.sh — Phase 1: pin to fingerprint
- YAML parsed via grep|sed in backup.sh — works for simple values; Phase 1: yaml.safe_load
- `_revert_everything` uses stale proposal snapshot — low-probability race; Phase 1: re-read under lock
- No automated test suite — Phase 1: 6-8h pytest per PR-review-04
- `create-proposal` log-write after pending-lock release — race window; Phase 1: reorder

See `review-notes/PR-SYNTHESIS-post-f1806f0.md` for full context on what's deferred.
