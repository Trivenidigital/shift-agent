---
name: handle_catering_menu_finalize
description: MANDATORY handler invoked when a CUSTOMER (not the owner) signals readiness to finalize their catering inquiry. Trigger phrases include "send to owner for approval", "yes finalize", "Finalize Proposal LXXXX", "looks good let's confirm", "I'm ready to proceed", "submit this menu", "let's go ahead", "yes please send it". The agent MUST use the `terminal` tool to invoke /usr/local/bin/finalize-catering-menu with --auto-default flag. NEVER ask the customer for item selections. NEVER compose a reply. NEVER quote prices. The script writes the server-side default basket to lead state, transitions to CUSTOMER_FINALIZED, and sends the owner an approval card.
---

# Handle Catering Menu Finalize (Agent #2 — PR-CF1b 2026-05-12)

## STRICT MODEL INSTRUCTIONS — FOLLOW EXACTLY

You are a deterministic finalize handler. Your job is **tool invocation, not improvisation**. You **MUST** use the `terminal` tool to invoke `/usr/local/bin/finalize-catering-menu` with the `--auto-default` flag. Do not extract menu items from conversation history. Do not ask clarifying questions. Do not compose a customer-facing reply.

### Mandatory tool-call sequence

1. **FIRST — extract the lead_id from the customer's message** (use the `terminal` tool):
   The customer's "Finalize Proposal LXXXX" message contains the lead_id literal. Extract it via grep:
   ```
   echo '<message_text>' | grep -oE 'L[0-9]{4,}' | head -1
   ```
   If no `LXXXX` pattern found: customer is finalizing ambiguously without naming a lead — STOP. Do not invoke finalize-catering-menu. Output nothing.

2. **SECOND — look up that lead's approval code** (use the `terminal` tool):
   ```
   jq -r --arg lid "<extracted_lead_id>" '.leads[] | select(.lead_id==$lid and (.status=="AWAITING_OWNER_APPROVAL" or .status=="CUSTOMER_FINALIZED" or .status=="OWNER_EDITED")) | .owner_approval_code' /opt/shift-agent/state/catering-leads.json
   ```
   This returns the `owner_approval_code` (e.g. `#NJSHS`) for the active lead. If empty: the lead doesn't exist or is in a terminal state — STOP. Output nothing.

   Why look up by `lead_id` rather than `customer_phone`: the customer literally named the lead in their message ("Finalize Proposal L0005"), and `customer_phone` for LID-only senders is currently persisted as `+<lid_digits>` (a known cosmetic bug — see `tasks/hermes-v0-13-0-plugin-api-recon-2026-05-11.md`). Lookup by `lead_id` is robust against this.

3. **THIRD — invoke finalize-catering-menu with --auto-default** (use the `terminal` tool):
   ```
   /usr/local/bin/finalize-catering-menu \
     --code "<owner_approval_code from step 2>" \
     --customer-message-id "<inbound_message_id>" \
     --auto-default
   ```
   The script builds a server-side default basket (first 5 available menu items, qty=1 each), server-recomputes the total, persists `selected_items` + `quote_total_usd` to the lead, transitions status to `CUSTOMER_FINALIZED`, and sends the owner an approval card with `#XXXXX approve` instructions.

4. **FOURTH — done.** Exit code 0 = success. Output nothing further. The owner will see the approval card; the owner-side `#XXXXX approve` flow takes over from there.

### FORBIDDEN ACTIONS

- ❌ NEVER ask the customer for menu items, dietary preferences, or any other information. The auto-default basket exists precisely so no LLM-side conversation is required.
- ❌ NEVER quote a price, share the total, or describe what was selected. Pricing is owner-only; the owner approves the basket via #XXXXX flow.
- ❌ NEVER compose a customer-facing reply via `send_message`. The script's owner-card path is the only outbound here.
- ❌ NEVER hallucinate "Proposal #1", "Proposal #2", "Proposal #3" or invent menu options. The menu is `/opt/shift-agent/state/catering-menu.json` — never invent items.
- ❌ NEVER use `--selected-items-json` or `--quote-total-usd` flags without explicit user-side selection (which under v0.1 doesn't exist yet — always use `--auto-default`).
- ❌ NEVER ask "clarifying questions" before finalizing. Past-version SKILL behavior allowed this; v2 explicitly forbids it because clarifying questions violate the parse_catering_inquiry HARD RULES too.

### Few-Shot Example

Inbound message from customer (Bangaru, LID-only sender):
```
[shift-agent-sender v=1 platform=whatsapp phone=null lid="201975216009469@lid" fromMe=false chat_id="201975216009469@lid"]
Finalize Proposal L0005
```

Inputs from `catering_dispatcher`:
- sender_phone: `null`
- sender_lid: `201975216009469@lid`
- message_text: `Finalize Proposal L0005`
- message_id: `<wa_msg_id>`

**Step 1 — terminal call** (extract lead_id from message):
```
echo 'Finalize Proposal L0005' | grep -oE 'L[0-9]{4,}' | head -1
```
→ `L0005`

**Step 2 — terminal call** (look up the lead's owner_approval_code by lead_id):
```
jq -r --arg lid "L0005" '.leads[] | select(.lead_id==$lid and (.status=="AWAITING_OWNER_APPROVAL" or .status=="CUSTOMER_FINALIZED" or .status=="OWNER_EDITED")) | .owner_approval_code' /opt/shift-agent/state/catering-leads.json
```
→ `#NJSHS`

**Step 3 — terminal call** (invoke script with --auto-default):
```
/usr/local/bin/finalize-catering-menu --code "#NJSHS" --customer-message-id "<wa_msg_id>" --auto-default
```
→ Exit 0. Lead L0005 status transitions `AWAITING_OWNER_APPROVAL` → `CUSTOMER_FINALIZED`. Owner approval card resent to owner self-chat with the server-default basket.

**Step 4 — done.** No further action.

---

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

Required because LLM recall of `(Ref: L0001)` lines and `#XXXXX` codes
is unreliable across long conversations. Read the lead state file
directly via `jq` — mirrors `handle_catering_owner_approval` Step 3a:

```bash
LEAD_JSON=$(jq -c --arg phone "$SENDER_PHONE" \
  '[.leads[] | select(.customer_phone==$phone and (.status=="AWAITING_OWNER_APPROVAL" or .status=="CUSTOMER_FINALIZED" or .status=="OWNER_EDITED"))] | sort_by(.created_at) | reverse | .[0] // empty' \
  /opt/shift-agent/state/catering-leads.json)

if [ -z "$LEAD_JSON" ]; then
    # No active lead. Run lookup-prior-leads-by-phone to determine
    # whether the customer has terminal leads (closed) or none at all,
    # so we can give a precise reply.
    LOOKUP=$(/usr/local/bin/lookup-prior-leads-by-phone --customer-phone "$SENDER_PHONE")
    PRIOR_COUNT=$(echo "$LOOKUP" | jq -r '.prior_lead_count')
    if [ "$PRIOR_COUNT" = "0" ]; then
        # Reply: "I don't see an open catering inquiry on file. Could
        # you share what you'd like to cater?"
        :
    else
        # Reply: "Looks like this booking was already closed — would
        # you like to start a new inquiry?"
        :
    fi
    exit 0  # don't invoke finalize-catering-menu
fi

# Active lead found. Extract the fields the finalize script needs.
LEAD_ID=$(echo "$LEAD_JSON" | jq -r '.lead_id')
CODE=$(echo "$LEAD_JSON" | jq -r '.owner_approval_code')
LEAD_STATUS=$(echo "$LEAD_JSON" | jq -r '.status')
```

The `lookup-prior-leads-by-phone` script's return shape does NOT
include `most_recent_lead_id` or `owner_approval_code` — use the `jq`
pattern above (deployed pattern from owner-approval SKILL) to retrieve
them from the state file directly.

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
  --code "$CODE" \
  --customer-message-id "<bridge messageId>" \
  --selected-items-json '<JSON array>' \
  --quote-total-usd $TOTAL
```

## Step 5 — Read exit code and respond

| Exit | Meaning | Your action |
|---|---|---|
| 0 | OK (success or replay) | NO chat reply. Customer's F14 proposal already acked; owner card now sent. |
| 2 | Invalid input | Re-read menu, re-extract items more carefully, retry ONCE. If second failure → "I had a hiccup capturing your selections — could you list the items once more?" |
| 4 | Lead not actionable | Per Step 1 status logic. Brief customer reply. |
| 6 | Bridge unreachable | State IS persisted. Tell customer: "Got it — owner will see this shortly." |
| 11 | Quote mismatch | Re-read menu prices, recompute total, retry ONCE. If second failure → exit 2 path. |
| 13 | Active proposal (census C4) | This lead already has menu options sent for the customer to choose from. Do NOT retry `--auto-default`. Reply: "You've got menu options to choose from — just reply with the option number you'd like." The customer's pick routes through `select-catering-proposal`. |

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

## Troubleshooting (PR-CF2)

Use these greps when an operator reports a finalize-flow surprise.
All paths assume `/opt/shift-agent/logs/decisions.log`.

### "Customer says they finalized but owner saw no card"

```bash
# 1. Confirm the dispatcher routed the message
grep '"type":"dispatcher_routed"' /opt/shift-agent/logs/decisions.log \
  | grep -i 'finalize\|handle_catering_menu_finalize' | tail -5

# 2. Confirm the script ran for that customer
grep '"type":"catering_menu_finalized"' /opt/shift-agent/logs/decisions.log \
  | jq -c 'select(.customer_phone=="<phone>") | {ts, outcome, owner_card_outbound_id, replay, suppressed}' \
  | tail -5
```

- `dispatcher_routed` missing → routing regression: dispatch_shift_agent
  did not match the finalize-intent keyword set, OR catering_dispatcher
  fell through to parse_catering_inquiry. Re-check the customer's
  message text against the keyword list in dispatch_shift_agent SKILL.
- `catering_menu_finalized.outcome=finalized` AND `owner_card_outbound_id=""`
  → bridge POST failed (Pushover P2 should have fired). Lead is at
  CUSTOMER_FINALIZED; re-deliver via cockpit operator action.
- `catering_menu_finalized.suppressed=true` → cooldown-suppressed
  replay within 60s of the previous send. The original card was
  delivered; verify owner saw it.

### "Customer's quote doesn't match what the menu shows"

```bash
# Find the finalize row for that lead
grep '"type":"catering_menu_finalized"' /opt/shift-agent/logs/decisions.log \
  | jq -c 'select(.lead_id=="L0001") | {ts, server_recompute_usd, llm_passed_total_usd, price_drift_detected, item_count}'
```

- `price_drift_detected=true` → menu prices changed between brainstorm
  and finalize. The card includes a `price_drift_note` line.
- `outcome=rejected_quote_mismatch` → drift exceeded `min(5%, $25)`
  tolerance. State unchanged. Customer needs to re-finalize after
  the LLM re-reads the current menu.

### "Owner approved but got a 'reply with --skip-finalize' message"

```bash
grep '"reason":"owner_approve_without_customer_finalize"' \
  /opt/shift-agent/logs/decisions.log | jq -c '{ts, lead_id, code}'
```

The customer never ran the finalize flow; the apply-script guard fired.
Owner must override via cockpit (operator action), not WhatsApp —
the SKILL parser cannot extract argparse flags from natural-language
WhatsApp replies.

### "Customer is changing their mind every minute (re-finalize storm)"

```bash
grep '"reason":"customer_re_finalized_menu"' \
  /opt/shift-agent/logs/decisions.log | jq -c '{ts, lead_id}' | tail -10
```

Each row is a customer-side mind-change. The state machine handles
re-finalize via the CUSTOMER_FINALIZED → CUSTOMER_FINALIZED self-edge;
the 60s cooldown rate-limits owner-card resends. If the rate is
genuinely problematic, pause the LLM-side proposal loop until the
customer settles.

### Audit-row reference

| Variant | When emitted | Key fields |
|---|---|---|
| `catering_lead_status_change` | every status transition (incl. CUSTOMER_FINALIZED self-edge on re-finalize) | `from_status`, `to_status`, `reason` |
| `catering_menu_finalized` (`outcome=finalized`) | every successful finalize OR replay | `replay`, `suppressed`, `price_drift_detected`, `customer_message_id`, `prior_total_usd` (set on re-finalize) |
| `catering_menu_finalized` (`outcome=rejected_quote_mismatch`) | quote total drift > tolerance | `server_recompute_usd`, `llm_passed_total_usd`, `customer_message_id` |
| `catering_quote_skill_failed` (`reason=owner_approve_without_customer_finalize`) | apply-script guard fires on owner approve before customer finalize | `code`, `lead_id` |
