---
name: handle_expense_owner_approval
description: Use when the owner replies with `#XXXXX 12.34` (approve), `#XXXXX 12.34 force` (force-approve past threshold/dedup), `#XXXXX reject`, `undo E####`, or `undo E#### force`. Invokes apply-expense-decision script which validates the code+amount, transitions the lead, calls the QBOClient (mock in v0.1), and notifies the owner.
---

# Handle Expense Owner Approval (Agent #21)

You receive an owner reply text. Branch on the verb extracted from the message.

## Decision branches

Parse the message to detect one of:

- **Approve** — `#CODE 12.34` (or `12.34 #CODE`, with optional `$` prefix and
  comma separator). Both the code AND a 2-decimal amount are required.
- **Force-approve** — same as approve plus literal `force` word.
- **Reject** — `#CODE reject`.
- **Undo** — `undo E####` (optional `force`).
- **Malformed** — anything else, OR missing decimals, OR bare `force`/`reject`.

Note: the script enforces the regex strictly. Don't pre-filter in the SKILL —
just pass the raw text and the script handles all error paths with friendly
nudges to the owner.

## Step 1 — Invoke the script

For approve/force/reject:

```bash
/usr/local/bin/apply-expense-decision \
  --raw-message "{{message_text}}" \
  --sender-phone "{{sender_phone}}" \
  --sender-lid "{{sender_lid|empty}}"
```

For undo:

```bash
/usr/local/bin/apply-expense-decision \
  --raw-message "{{message_text}}" \
  --sender-phone "{{sender_phone}}" \
  --sender-lid "{{sender_lid|empty}}"
```

Same script entry point; the script parses verb from `--raw-message`.

## Step 2 — Read the JSON stdout

Possible exit codes:

- `0` — applied (push succeeded, reject finalized, undo voided, OR amount-
  mismatch nudge sent — all of these are non-failure outcomes from the
  user's perspective; the script sent the right reply).
- `2` — invalid input (e.g. malformed message). Script already sent friendly
  nudge to the owner.
- `4` — code/expense_id not found. Log silently (`expense_dispatcher_no_match`)
  — owner may have sent unrelated text.
- `5` — schema violation. Pushover the owner; log; surface to operator.
- `7` — owner amount mismatch (no push, friendly nudge sent by script).
- `8` — undo outside reversibility window (script sent the force-instruction
  message).
- `9` — illegal status transition. Pushover; log; reconcile-required flag set.
- `10` — QBO push failure. Script set status=PUSH_FAILED and notified owner.

## Hard rules

- The SKILL does NOT parse the message — the script does. Do not pre-validate
  amount or code; pass `--raw-message` verbatim.
- Owner re-auth on undo is checked by the script (sender_phone vs
  cfg.owner.phone). Do not duplicate that check here.
- NEVER send a confirmation message from this skill — the script sends
  ALL owner-facing replies (approval card, mismatch nudge, push confirmation,
  threshold-exceeded, dedup-detected, undo-out-of-window, etc.).
