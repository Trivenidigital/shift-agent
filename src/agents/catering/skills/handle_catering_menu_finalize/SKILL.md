---
name: handle_catering_menu_finalize
description: Use when a CUSTOMER (not the owner) signals readiness to finalize the catering menu they've been brainstorming with the agent. Trigger phrases include "send to owner for approval", "yes finalize", "looks good let's confirm", "I'm ready to proceed", "submit this menu", "let's go ahead", "yes please send it". Looks up the active catering lead via lookup-prior-leads-by-phone, extracts the items the customer agreed to during brainstorm, and invokes /usr/local/bin/finalize-catering-menu. Owner sees the customer-curated menu card and approves separately with #XXXXX approve.
---

# Handle Catering Menu Finalize (Agent #2 — PR-CF1)

The customer has been brainstorming menu options with you and has now
signaled readiness to lock in their selections. Your job: extract the
items they agreed to, look up their lead, and invoke
`/usr/local/bin/finalize-catering-menu`. The script writes the
customer-curated menu to lead state and sends the owner an approval
card.

## When to invoke

All of the following must be true:

1. The CURRENT inbound is from a CUSTOMER (sender role checked by
   dispatcher already)
2. The message expresses finalize intent — examples:
   - "send to owner for approval"
   - "yes finalize this"
   - "looks good, let's confirm"
   - "I'm ready to proceed / book / submit"
   - "go ahead with the menu"
   - "yes please send it for approval"
3. Conversation history shows the customer has discussed specific menu
   items with you (you're not finalizing an empty inquiry)

If the customer message is ambiguous (e.g. just "yes" with no clear
referent), ask one clarifying question first — do not over-eagerly
finalize.

## Step 1 — Look up the active lead (deterministic, do not improvise)

Call:

```bash
/usr/local/bin/lookup-prior-leads-by-phone --customer-phone "<sender_phone>"
```

The script returns JSON. Read `most_recent_status` and `most_recent_lead_id`.

- If `most_recent_status` is in {`AWAITING_OWNER_APPROVAL`,
  `CUSTOMER_FINALIZED`, `OWNER_EDITED`} → there's an active lead;
  proceed.
- If `most_recent_status` is terminal (CLOSED, OWNER_REJECTED, STALE,
  NOT_CATERING, SENT_TO_CUSTOMER) → tell customer briefly: "Looks like
  this booking was already closed — would you like to start a new
  inquiry?"
- If no leads at all → tell customer: "I don't see an open catering
  inquiry on file. Could you share what you'd like to cater?"

Required because LLM recall of `(Ref: L0001)` lines is unreliable across
long conversations. The script exit 4 path also covers this safety net.

The owner approval `#XXXXX` code is on the lookup result. You'll need it
verbatim.

## Step 2 — Extract `customer_message_id` (idempotency key)

`--customer-message-id` is the bridge messageId of the customer's
current "finalize" message. This is the same field that
`create-catering-lead --message-id` uses, exposed by the dispatcher
via the `message_id` named input passed to handler skills (see
`dispatch_shift_agent` SKILL.md Step 5 "Delegate").

Use the verbatim `message_id` from the dispatcher inputs. Do NOT
synthesize from timestamp; idempotency requires the bridge's stable id.

If `message_id` is genuinely unavailable (a routing bug to flag),
fall back to `f"finalize_synth_{code}_{int(time.time()*1000)}"` and
log via stderr. Replay protection degrades to no-op for synthetic ids.

## Step 3 — Extract items from conversation

Build a JSON array of items the customer agreed to during brainstorm.
For each item:

- `name`: must EXACTLY match an item name from `/opt/shift-agent/state/catering-menu.json`. If you're unsure, re-read the menu BEFORE constructing the JSON. Misspelled / hallucinated names will fail with exit 2.
- `qty`: integer 1-500. This is the absolute order quantity (not per-guest unless stated).
- `price_usd`: integer whole-dollar from the menu. The script will validate this against the current menu and use the current price if it has changed since brainstorm started (server-authoritative; soft-fail on drift).

Example:

```json
[
  {"name": "Aloo Paratha", "qty": 30, "price_usd": 4},
  {"name": "Chicken Biryani", "qty": 1, "price_usd": 15},
  {"name": "Gulab Jamun 2pc", "qty": 50, "price_usd": 3}
]
```

## Step 4 — Compute total + invoke

```bash
TOTAL=$(/usr/local/lib/hermes-agent/venv/bin/python -c "import sys, json; print(sum(i['qty']*i['price_usd'] for i in json.loads('''<JSON>''')))")
/usr/local/bin/finalize-catering-menu \
  --code "<#XXXXX from lookup>" \
  --customer-message-id "<bridge messageId>" \
  --selected-items-json '<JSON array>' \
  --quote-total-usd $TOTAL \
  --customer-message-text "<original customer text>"
```

## Step 5 — Read exit code and respond

| Exit | Meaning | Your action |
|---|---|---|
| 0 | OK (success or replay) | NO chat reply. Customer's F14 proposal already acked; owner card now sent. |
| 2 | Invalid input | Re-read menu, re-extract items more carefully, retry ONCE. If second failure → "I had a hiccup capturing your selections — could you list the items once more?" |
| 4 | Lead not actionable | Per Step 1 status logic. Brief customer reply. |
| 6 | Bridge unreachable | State IS persisted. Tell customer: "Got it — owner will see this shortly." |
| 11 | Quote mismatch | Re-read menu prices, recompute total, retry ONCE. If second failure → exit 2 path. |

## Hard rules

- NEVER compose a chat reply on success — customer doesn't need a duplicate ack
- NEVER trust LLM recall of `#XXXXX` codes; always go through `lookup-prior-leads-by-phone`
- NEVER finalize with stale prices — if menu file is empty/missing, abort with stderr
- NEVER pass non-integer USD prices (the schema's `price_usd: int` will reject)
- NEVER finalize an empty selection (the schema requires 1-50 items)
- NEVER round customer's headcount up "to be safe" without their explicit permission — extracted fields are advisory; selected_items is what matters at finalize

## Outcome

Customer-side: their conversation continues. They've been ack'd once
(F14 proposal); they wait for owner to send the priced quote (which
will arrive via apply-catering-owner-decision after the owner replies
`#XXXXX approve`).

Owner-side: receives a card (template:
`catering_finalized_menu_to_owner.txt`) summarizing the customer's
selections + total. Owner can `#XXXXX approve` to send the priced quote
to the customer, `#XXXXX edit <changes>` to adjust before sending, or
`#XXXXX reject <reason>` to decline.

State-side: lead status transitions from `AWAITING_OWNER_APPROVAL`
(or `OWNER_EDITED`) to `CUSTOMER_FINALIZED`. Audit chain:
`catering_lead_status_change` + `catering_menu_finalized` rows in
decisions.log.
