---
name: handle_catering_owner_approval
description: Use when the OWNER replies in their self-chat with a 5-character approval code (e.g. "#A3F2X") referencing a pending catering quote. Looks up the lead in catering-leads.json by approval_code, applies APPROVE / EDIT / REJECT decision, sends approved quote to customer (with template) or stores edits and re-drafts. Mirrors the Shift Agent's approval-code pattern in handle_owner_command.
---

# Handle Catering Owner Approval (Agent #2)

The owner responds to a pending catering quote. Decode their intent,
update the lead, and either send the quote to the customer or capture
their edits.

## Hard rules

- ONLY accept replies from the owner (verified upstream).
- ONLY act on quotes in `AWAITING_OWNER_APPROVAL` status. Other statuses
  → log and ignore.
- Owner reply MUST contain the 5-char approval code that was in the
  approval card (e.g. `#A3F2X`). No code → ask for it; never guess.
- After APPROVE → send to customer via Meta-approved template; log
  `CateringQuoteSent`; transition to `SENT_TO_CUSTOMER`.

## Decision matrix

| Owner reply contains | Action | New status |
|---|---|---|
| Code + "approve" / "yes" / "send" | Send quote to customer | SENT_TO_CUSTOMER |
| Code + "edit" / edit text | Capture edits; re-draft | OWNER_EDITED → re-extract → AWAITING_OWNER_APPROVAL |
| Code + "reject" / "no" | Decline lead | OWNER_REJECTED |
| Code only, no verb | Reply asking for clarification | (no change) |
| Code missing | Reply asking for code | (no change) |

## Phases

**v0.1 (current):** SKILL exists but won't fire because catering is opt-in.

**v0.2:** Full implementation with code matching, lead update under
FileLock, customer-facing template send, audit log entries.

## Architecture note

Reuses Shift Agent's `_BaseProp.approval_code: ProposalCode` regex
(`^#[A-Z0-9]{5}$`). Codes are unique across pending proposals AND
catering leads (single namespace at `state/approval-codes.txt`) — the
collision-detector (already in `shift-agent-fsck.py`) covers both.
