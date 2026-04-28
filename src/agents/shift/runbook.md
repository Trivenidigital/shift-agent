# Shift Agent — Customer Runbook

Welcome. This document covers day-to-day operation, roster maintenance, and troubleshooting for the Shift Agent. Keep it bookmarked.

---

## What this agent does

The Shift Agent listens to your WhatsApp for sick-call messages from your employees. For each one:

1. Acknowledges the employee warmly
2. Looks up your roster + schedule
3. Finds candidates who can cover the absent employee's shift
4. Sends you (via your WhatsApp "Message Yourself" chat) a structured proposal with a 5-character approval code
5. When you reply with the code, sends the coverage request to the candidate on your behalf
6. When the candidate replies YES/NO, notifies you of the outcome

It runs 24/7 best-effort but this is a **beta**. If it's offline, you'll get a Pushover alert and you handle that sick call manually.

---

## Daily use

### You receive a proposal in your self-chat

The message looks like:

> New sick call:
>
> Ravi Kumar is out 2026-04-29 (fever).
> Shift: 09:00-17:00 cashier
>
> Coverage candidate: Anjali Iyer
> Reason: Only cashier-capable employee not already scheduled; shares English with Ravi
>
> Proposed message to Anjali:
> > Hi Anjali, Ravi is out tomorrow with fever. Can you cover the 09:00-17:00 cashier shift? Reply YES or NO. Thanks! — Sally
>
> Reply **#A3F2X** to approve + send, or **"DENY #A3F2X"** to reject.

Your options:

- **`#A3F2X`** — approves. Agent sends the coverage message to Anjali. You'll be notified when she responds.
- **`DENY #A3F2X`** — rejects. No message sent. You handle it manually.
- **`CANCEL #A3F2X`** — same effect as DENY. Use for "never mind."
- **`STATUS`** — list all pending proposals.
- **`KILL`** — disable the agent entirely (emergency use).

### You receive the candidate's response

After a candidate replies YES/NO to your coverage message, you get a short confirmation in your self-chat:

> Anjali accepted coverage for 2026-04-29 09:00-17:00 cashier. Original absence: Ravi Kumar (fever). All set.

---

## Editing your roster

**File:** `/opt/shift-agent/roster.json` on the VPS.

Edit via SSH:

```bash
ssh main-vps
sudo -u shift-agent vim /opt/shift-agent/roster.json
```

### Adding a new employee

Add an entry to the `employees` array:

```json
{
  "id": "e007",
  "name": "New Hire",
  "nickname": "NH",
  "role": "cashier",
  "phone": "+19045559999",
  "languages": ["en"],
  "can_cover_roles": ["cashier"],
  "status": "active"
}
```

Then add `+19045559999` to `WHATSAPP_ALLOWED_USERS` in `/opt/shift-agent/.env`:

```
WHATSAPP_ALLOWED_USERS=+918522041562,+17329837841,+19045559999,...
```

Restart gateway: `sudo systemctl restart hermes-gateway`.

### Removing an employee (termination)

**Don't delete the entry** — set `status` to `terminated`. This preserves audit history.

```json
{
  "id": "e001",
  "status": "terminated",
  ...
}
```

Also remove their phone from `WHATSAPP_ALLOWED_USERS`.

### Updating a phone number

**Don't overwrite `phone`** — move the old number to `phone_history`:

```json
{
  "id": "e001",
  "phone": "+19045551111",      // new
  "phone_history": [
    {"phone": "+19045550101", "effective_from": "2024-01-01T00:00:00Z", "effective_to": "2026-04-25T00:00:00Z"}
  ]
}
```

Nightly fsck will flag dangling references in the audit log if anything goes wrong.

### Updating the schedule

The `schedule` object is keyed by date (YYYY-MM-DD). Add or modify entries:

```json
"schedule": {
  "2026-04-29": [
    {"employee_id": "e001", "shift": "09:00-17:00", "role": "cashier"}
  ]
}
```

**After every roster/schedule edit:** the changes take effect on the NEXT inbound message. No restart needed. But if you want to verify your edits:

```bash
sudo -u shift-agent /usr/local/bin/shift-agent-smoke-test.sh
```

---

## Understanding alerts

All out-of-band alerts come through **Pushover** on your phone. You'll see messages like:

- **"Agent offline"** — gateway crashed. Handle manually until resolved.
- **"Cap exceeded"** — daily outbound cap hit. Send manually or wait.
- **"Send FAILED"** — specific proposal couldn't reach candidate. Check your WhatsApp.
- **"Invariant check failed"** — nightly health check found inconsistency. Non-urgent but check logs.
- **"Deploy OK" / "Deploy FAILED"** — release cycle events.

Pushover priority 2 = emergency, rings even on silent. Priority 1 = high, normal alert. Priority -1 = informational, quiet.

---

## Emergency procedures

### Stop the agent entirely

SSH in and run:

```bash
sudo /usr/local/bin/shift-agent-disable "owner_emergency"
```

You'll get a Pushover confirmation. All sick-calls will go unprocessed until you re-enable. This is safer than leaving a misbehaving agent running.

### Re-enable

```bash
sudo /usr/local/bin/shift-agent-enable
```

### Full revert (unlink from WhatsApp)

On your PRIMARY phone's WhatsApp: Settings → Linked Devices → remove the Hermes linked device. Your WhatsApp now operates fully manually. The VPS services can stay running but are effectively dormant.

---

## Common issues

### "I'm not receiving proposals in my self-chat"

1. Verify your `self_chat_jid` in `config.yaml` — it may need re-populating after a re-pair.
2. Run `sudo -u shift-agent /usr/local/bin/shift-agent-smoke-test.sh`.
3. Check Pushover — any alerts?
4. SSH in: `journalctl -u hermes-gateway -n 50`.

### "Employee says they messaged in sick but I never got a proposal"

1. Check `/opt/shift-agent/logs/decisions.log` — search for their phone number or message content:
   ```bash
   grep "+19045550101" /opt/shift-agent/logs/decisions.log
   ```
2. If the `raw_inbound` entry is present but no `proposal_created`, the LLM may have misclassified. Escalate to me.
3. If no `raw_inbound` either, the allowlist may not include their number. Check `/opt/shift-agent/.env`.

### "I approved a proposal but the candidate never got the message"

1. Check pending.json: `cat /opt/shift-agent/state/pending.json | python3 -m json.tool | grep -A10 "P0042"`.
2. Status should be `sent` — if it's `send_failed`, try `RETRY #CODE` from your self-chat.
3. If it's stuck in `reconciling`, wait 5 min then ask for help — the reconciler at next boot handles this.

---

## What's stored and where

- **`/opt/shift-agent/roster.json`** — your employees + schedule. Plaintext.
- **`/opt/shift-agent/config.yaml`** — your settings (phone numbers, limits, Pushover keys). Plaintext, mode 600.
- **`/opt/shift-agent/state/pending.json`** — current in-flight proposals. Rebuilt as proposals are processed.
- **`/opt/shift-agent/logs/decisions.log`** — append-only audit of every sick-call event. Rotated daily, kept 30 days + archived.
- **`/opt/shift-agent/backups/`** — nightly GPG-encrypted backups. Retained 30 days.
- **`/opt/shift-agent/.env`** — API keys (Pushover, OpenRouter). Mode 600.

---

## Three disclosures — please read and acknowledge

### 1. Baileys (WhatsApp) ToS risk

The Shift Agent uses an unofficial WhatsApp client (Baileys). WhatsApp's Terms of Service prohibit unofficial clients, and Meta has historically restricted or banned numbers detected as using them.

**Your WhatsApp number could be restricted by Meta.** If this happens:

- You'll lose the ability to use WhatsApp on this number temporarily or permanently
- The kill-switch (`KILL` command) removes the Hermes linked device immediately
- This is a known risk of the beta; we can move to Twilio's WhatsApp Business API for a production release (requires 3-14 days for Meta to verify your business)

**Acknowledgment:** I understand my WhatsApp number could be restricted by Meta due to use of Baileys. Signature: __________________ Date: __________

### 2. Audit log integrity

`/opt/shift-agent/logs/decisions.log` is the source of truth for sick-call / coverage decisions. Operational integrity:

- **Append-only writes** via `safe_io.ndjson_append` (flock + atomic write + fsync) — concurrent writers can't corrupt each other's entries.
- **File perms `0640 shift-agent:shift-agent`** — only `shift-agent` user (and root) can write.
- **Daily rotation** via logrotate to `/var/log/shift-agent-archive/`, 30-day retention.
- **Off-server backups** via the existing backup pattern (see deploy.md).

**No cryptographic tamper-evidence.** A SHA-256 chain file at `decisions.log.sha256` existed previously but was decoration — only ~3% of writers updated it, and there was no verifier. Removed 2026-04-28 to honestly reflect the deployed integrity story rather than claim a feature we don't have. If a future compliance need emerges (regulator audit, formal customer dispute defense), the chokepoint pattern in `safe_io.ndjson_append` makes adding a real chain straightforward — see `docs/hermes-alignment.md` Part 1 for the architecture sketch.

**For labor disputes:** the audit log is corroborating evidence, not authoritative. The customer's WhatsApp message screenshots are the primary evidence; this log provides context for what the agent did.

**Acknowledgment:** I understand the audit log is append-only with file-perm protection, not cryptographically tamper-evident. I will treat it as corroborating evidence, not authoritative. Signature: __________________ Date: __________

> *Note for any acknowledgment signed before 2026-04-28:* the prior text claimed "checksum-protected (SHA-256 chain)." Investigation that day found the chain was decoration (~3% writer coverage, no verifier) and was removed in PR #20. Any earlier acknowledgment is superseded by the text above; re-sign at next opportunity.

### 3. Employee notification requirement

Your employees' WhatsApp messages to you — including their names and reasons for absence — are processed by this agent. Under data privacy best practices (and GDPR if applicable), you must notify employees of this processing.

**Before the agent goes live, send your employees (group message or individually):**

> Starting [DATE], we're trialing an automated assistant that helps me coordinate coverage when someone calls in sick. When you message me that you can't make a shift, your name and the reason you share are processed by this system to help me find coverage faster. If you'd prefer to opt out, reply STOP here and your sick-call messages will be handled manually like before. Thanks!

**Acknowledgment:** I have sent this notice (or equivalent) to my employees before the agent goes live. Signature: __________________ Date: __________

---

## Getting help

During the beta period, contact me directly (srinivas.yalavarthi@gmail.com) for any:

- Production incidents (agent offline > 30min, stuck reconciling, wrong-person-messaged)
- Roster editing questions
- Feature requests / scope expansion
- Billing / OpenRouter credit questions

Response time: best-effort, business hours.

---

## Deployment info (reference)

- **VPS:** Hetzner, single 3.7GB RAM Ubuntu 24.04 node (identifier on file)
- **LLM:** Kimi K2-thinking via OpenRouter
- **WhatsApp:** Baileys (unofficial client) linked to your phone as a linked device
- **Alerts:** Pushover (primary), WhatsApp self-chat (secondary)
- **Backup:** Nightly GPG-encrypted tarball, 30-day retention
- **Model cost:** approximately $0.05 per sick call processed ($20/month cap configured on OpenRouter)
