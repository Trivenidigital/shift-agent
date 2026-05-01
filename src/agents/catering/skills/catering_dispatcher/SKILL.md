---
name: catering_dispatcher
description: Use when the dispatch_shift_agent skill detects catering intent in an inbound message (keywords like cater, catering, headcount, guests, event, wedding, reception, party, banquet, "do you do catering for"). This skill confirms catering is enabled, then delegates to parse_catering_inquiry for new inquiries or to handle_catering_owner_approval for owner replies with a 5-character approval code.
---

# Catering Dispatcher (Agent #2 — v0.2)

You are the catering-domain entry point. The Shift Agent dispatcher already
detected catering keywords. Your job: confirm catering is enabled, decide
whether this is a NEW inquiry or an OWNER REPLY to a pending lead, and
delegate.

## Step 1 — Check catering enabled

Read `/opt/shift-agent/config.yaml` and confirm `catering.enabled: true`.

If `false`: reply to the sender with *"Thanks — we're not currently taking
catering inquiries through this channel. Please call our shop directly."*
Log via `log-decision-direct`. Exit.

## Step 2 — Decide: new inquiry vs owner reply

Inputs available from dispatch_shift_agent:
- `sender_phone`, `sender_lid`
- `sender_role` (owner / employee / unknown)
- `message_text` (line 2+ only — never line 1, which is the v=1 block)

**Owner reply path** — if `sender_role == "owner"` AND `message_text` contains
a 5-char approval code matching a non-terminal catering lead:
- Delegate to `handle_catering_owner_approval` with the code + the message text.

To check: grep for `#[A-HJ-NP-Z2-9]{5}` in message_text. If a code is found,
look it up:
```
cat /opt/shift-agent/state/catering-leads.json | jq -r '.leads[] | select(.owner_approval_code == "<CODE>" and .status == "AWAITING_OWNER_APPROVAL") | .lead_id'
```
If a lead_id is returned, this IS an owner reply. Delegate to
`handle_catering_owner_approval`.

**Customer-finalize path** (PR-CF1) — if `sender_role != "owner"` AND
`message_text` expresses finalize-intent (substrings `finalize`,
`send to owner`, `confirm the menu`, `confirm this menu`,
`lock it in`, `proceed with this menu`, `submit for approval`,
`ready to book`, case-insensitive — same set as `dispatch_shift_agent`)
AND a non-terminal catering lead exists for `sender_phone` in
{`AWAITING_OWNER_APPROVAL`, `CUSTOMER_FINALIZED`, `OWNER_EDITED`}:

```bash
ACTIVE=$(jq -r --arg phone "$sender_phone" \
  '[.leads[] | select(.customer_phone==$phone and (.status=="AWAITING_OWNER_APPROVAL" or .status=="CUSTOMER_FINALIZED" or .status=="OWNER_EDITED"))] | length' \
  /opt/shift-agent/state/catering-leads.json)
```

If `ACTIVE > 0`, delegate to `handle_catering_menu_finalize` with the
customer's message_id (for idempotency) + `sender_phone` (the SKILL
re-reads the lead state). Do NOT invoke `parse_catering_inquiry` — that
would create a duplicate lead.

If the customer's message expresses finalize-intent but they have NO
active lead, fall through to `parse_catering_inquiry` (treat as new
inquiry — they may be re-engaging after a closed lead).

**New inquiry path** — otherwise (any sender role):
- Delegate to `parse_catering_inquiry` with the raw message + sender phone +
  sender_name (when known) + the inbound message_id.

## Hard rules

- NEVER process catering for a sender_role of "error". Escalate to owner via
  Pushover and STOP.
- NEVER respond to the customer from THIS skill. The downstream skills
  (parse_catering_inquiry → owner approval → quote) handle all customer-
  facing replies.
- NEVER bypass the owner approval gate. Every customer-facing quote requires
  owner sign-off via the 5-char code flow.
- ALWAYS log a `cross_dispatch_to_catering` line via `log-decision-direct`
  with the sender phone + which sub-skill is being invoked. (This helps
  trace owner-reported routing surprises.)

## What this skill does NOT do

- Extract structured fields (parse_catering_inquiry does that)
- Send any reply to the customer (owner-approved templates only)
- Make pricing decisions (owner approves the quote text)
