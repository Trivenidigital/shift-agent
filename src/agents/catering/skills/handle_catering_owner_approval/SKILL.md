---
name: handle_catering_owner_approval
description: Use when the OWNER replies in their self-chat with a 5-character approval code (e.g. "#A3F2X") matching a non-terminal catering lead. Parses the owner's intent (approve / reject / edit) and calls /usr/local/bin/apply-catering-owner-decision to transition the lead state and, on approval, send the quote to the customer.
---

# Handle Catering Owner Approval (Agent #2 — v0.2)

The owner has responded to a pending catering quote. Decode their intent
deterministically, call the state writer, do not free-text-reply to the
customer.

## Step 1 — Parse the owner's reply

The message contains a 5-char code matching format `#[A-HJ-NP-Z2-9]{5}`
(uppercase alphanumeric, no visually ambiguous chars). Extract it:

```
CODE=$(echo "<message_text>" | grep -oE "#[A-HJ-NP-Z2-9]{5}" | head -1)
```

If no code matched: ask the owner to include the code from the approval card.
DO NOT guess which lead they meant; multiple inquiries may be open.

## Step 2 — Determine the decision

Look at the message_text (case-insensitive) for one of these verbs:

| Owner says | decision |
|---|---|
| "approve", "yes", "send", "ok", "go", "send it" | **approve** |
| "reject", "no", "decline", "pass" | **reject** |
| "edit", "change", "modify", or anything followed by edit text | **edit** |

If the owner's intent is ambiguous (e.g., just the code with no verb), reply:
*"Got code {CODE}. Reply with `approve`, `edit <changes>`, or `reject`."*
Do NOT default to any decision.

For `edit`: extract everything in the message AFTER the code AND the verb
("edit", "change", etc.) as the edit text. Truncate to 1000 chars.

## Step 3 — Call apply-catering-owner-decision

```
/usr/local/bin/apply-catering-owner-decision \
  --code "<CODE>" \
  --decision <approve|reject|edit> \
  [--edit-text "<edit body>"] \
  [--reason "<rejection reason>"]
```

The script will:
1. Find the lead with that code in `AWAITING_OWNER_APPROVAL` status (under
   FileLock)
2. Transition to `OWNER_APPROVED` / `OWNER_REJECTED` / `OWNER_EDITED`
3. Log `CateringLeadStatusChange` + `CateringOwnerDecision`
4. On `approve`: send the quote text (from
   `/opt/shift-agent/templates/catering_quote_to_customer.txt`) to the
   customer's `<phone>@s.whatsapp.net`. On send success, transition to
   `SENT_TO_CUSTOMER` and log `CateringQuoteSent`. On send failure, the
   lead stays at `OWNER_APPROVED` (operator visibility for retry).

**Read the script's exit code:**
- 0: success. On approve+send-OK, the customer received the quote.
- 2: invalid input (e.g., missing edit text). Your call was malformed.
- 4: code not found among AWAITING_OWNER_APPROVAL leads. Tell the owner the
  code doesn't match an active lead.
- 5: schema violation on state file. Alert owner via Pushover, STOP.
- 6: customer-side bridge unreachable on approve. The lead is at
   OWNER_APPROVED. Tell the owner: *"Approved, but couldn't reach the
   customer right now. I'll retry."* (Retry mechanism is operator-driven
   in v0.2; auto-retry lands in v0.3.)
- 9: illegal transition (lead already in terminal state, or duplicate code).

## Step 4 — Confirm to owner

After the script returns 0:

- approve + send-OK: *"Sent to customer ({lead.customer_name or phone}). Lead {lead_id} → SENT_TO_CUSTOMER."*
- approve + send-failed (exit 6): *"Approved, but customer send failed. I'll retry — or you can reach them directly."*
- reject: *"Lead {lead_id} declined. Logged."*
- edit: *"Got your edits. The drafter will incorporate them; you'll see the updated card here."* (Note: v0.2 doesn't yet auto-redraft. The lead is in OWNER_EDITED status; cockpit shows it for manual handling. Auto-redraft lands in v0.3.)

## Hard rules

- NEVER infer the customer's response — they haven't replied yet.
- NEVER send the quote directly from this skill. The script's bridge_post
  is the only path.
- NEVER skip logging — every owner decision is auditable per portfolio
  compliance requirements.
- An owner trying to approve a lead they ALREADY approved (status was
  SENT_TO_CUSTOMER): tell them it's already sent. Don't re-send.
