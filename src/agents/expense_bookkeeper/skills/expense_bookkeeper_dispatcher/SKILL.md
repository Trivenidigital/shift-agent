---
name: expense_bookkeeper_dispatcher
description: Use when an inbound message is an owner receipt photo, an `#XXXXX total.cc` reply matching a non-terminal expense lead, or `undo E####`. Routes to parse_receipt_photo for new images, handle_expense_owner_approval for owner replies. Confirms cfg.expense_bookkeeper.enabled before acting.
---

# Expense Bookkeeper Dispatcher (Agent #21)

You are the expense-domain entry point. The Shift Agent dispatcher already
classified this message as expense-shaped. Your job: confirm the agent is
enabled + the sender is the owner, then delegate.

## Step 1 — Check enabled

Read `/opt/shift-agent/config.yaml` and confirm `expense_bookkeeper.enabled: true`.

If `false`: reply *"Expense capture isn't enabled for this account."* Log via
`log-decision-direct` with type `expense_disabled_reply`. Exit.

## Step 2 — Re-verify sender is owner

The parent dispatcher already routed by sender_role, but defensively re-check:
`sender_role` from inputs MUST be `owner`. If not, reply politely (e.g.
*"Please ask the owner to send receipts directly."*) and log
`expense_non_owner_declined`.

## Step 3 — Branch

Inputs: `sender_phone`, `sender_lid`, `message_text`, `message_shape`, `image_path`
(when image), `original_message_id`.

**Image inbound** (`message_shape` is `image_only` or `image_with_caption` AND
`image_path` is set):

- Delegate to `parse_receipt_photo` with `image_path`, `sender_phone`,
  `sender_lid`, `original_message_id`.

**Owner reply with `#XXXXX` code** (regex `#[A-HJKMNPQR-Z2-9]{5}` matches
`message_text`):

- Delegate to `handle_expense_owner_approval` with the full message text and
  sender_phone. (The script parses the format — see its SKILL.md.)

**Owner reply with `undo E####`** (regex `^\s*undo\s+E\d{4,}( force)?\s*$`,
case-insensitive):

- Delegate to `handle_expense_owner_approval` with the message text and
  sender_phone — the same handler script branches on the verb.

**Otherwise:**

- Reply *"Send a receipt photo to start. Reply '#CODE 12.34' to approve, or 'undo E####' to reverse."*
- Log `expense_dispatcher_no_match`. Exit.

## Hard rules

- NEVER process if `sender_role != "owner"`.
- NEVER respond to a non-owner customer from this skill.
- NEVER bypass the owner approval gate — every QBO push requires the
  `#CODE total.cc` echo with both code AND amount matching.
- ALWAYS log `cross_dispatch_to_expense_bookkeeper` via `log-decision-direct`
  with which sub-skill is being invoked. Mirrors catering_dispatcher precedent.

## What this skill does NOT do

- Vision extraction (parse_receipt_photo invokes the script)
- Code+amount validation (apply-expense-decision enforces)
- QBO push (apply-expense-decision invokes the QBOClient)
