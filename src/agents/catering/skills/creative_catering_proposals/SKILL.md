---
name: creative_catering_proposals
description: Generate menu-grounded catering proposal options for an active catering lead. MUST invoke /usr/local/bin/create-catering-proposal-options and MUST NOT send customer messages directly or include prices/payment/booking language.
---

# Creative Catering Proposals

This is a deterministic Hermes skill for active catering leads, not a freeform
chat reply. It prepares constrained proposal option JSON for the proposal
script; it does not render customer-visible prose.

## Required flow

1. Read the active catering lead by explicit lead id when provided. If no lead
   id is provided, resolve the active lead from sender context in
   `/opt/shift-agent/state/catering-leads.json`.
2. Read `/opt/shift-agent/state/catering-menu.json`.
3. Produce 2 proposal options by default. Produce 3 options only when the
   customer's request text asks for `three` or `3`.
4. Use exact menu item names from `catering-menu.json`. Do not invent dishes,
   aliases, prices, packages, or payment terms.
5. Pipe JSON options on stdin to:

   ```bash
   printf '%s\n' '<options_json>' | /usr/local/bin/create-catering-proposal-options \
     --lead-id <lead_id> \
     --customer-jid <customer_jid> \
     --source-message-id <inbound_message_id> \
     --request-text <request_text> \
     --options-json -
   ```

## Output contract

- Send only machine-readable JSON on stdin to
  `/usr/local/bin/create-catering-proposal-options`.
- Invoke the script with `--lead-id <lead_id>`,
  `--customer-jid <customer_jid>`,
  `--source-message-id <inbound_message_id>`,
  `--request-text <request_text>`, and `--options-json -`.
- Include validated menu item names and closed style keys only.
- Customer-visible prose must be rendered by the script from validated item
  names and closed style keys.

## Forbidden

- NEVER call send_message.
- NEVER include prices.
- NEVER include deposits, payments, Venmo, Zelle, payment rails, booking
  confirmation, customer-facing quotes, or any payment language.
- NEVER produce freeform customer-facing proposal prose.
- Do not invoke `finalize-catering-menu`; selection is handled separately by
  `select-catering-proposal`.
