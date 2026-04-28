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
