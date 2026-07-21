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

## Composition rules

- **Default** — produce menu-grounded options that fit the lead's headcount and
  dietary mix (e.g. a 90-non-veg / 30-veg wedding gets options with real non-veg
  and veg catalog items, not veg-only). Each option MUST be a COMPLETE menu that
  spans courses — include at least one starter/appetizer AND at least one main
  (add a dessert/side when the menu offers them). Never send an option that is
  all mains or all appetizers; a customer choosing "option 1's starters" later
  can only do so if option 1 actually has a starter.
- **Mix-and-match / recomposition** — when the customer asks to combine sections
  of already-SENT options (e.g. "option 1 starters with the option 2 mains",
  "keep option 2's mains, option 1's desserts"), do NOT compose the item list
  yourself — that risks silently dropping a section. Invoke the script's
  deterministic recomposition mode, which pulls the named sections verbatim from
  the SENT options and validates that the delivered menu contains exactly the
  requested sections:

  ```bash
  /usr/local/bin/create-catering-proposal-options \
    --lead-id <lead_id> \
    --customer-jid <customer_jid> \
    --source-message-id <inbound_message_id> \
    --request-text "<the customer's exact combination, e.g. 'option 1 starters with option 2 mains'>" \
    --recompose-from-sent
  ```

  Pass the customer's combination phrasing through in `--request-text`; do NOT
  add `--options-json`. If the request does not cleanly resolve (an option number
  that was never sent, a section the named option lacks, or fewer than two
  sections named), the script sends ONE clarifying question instead of a
  best-guess merge — you compose nothing in that case.
- **Off-menu items** — an item the customer named that is NOT in
  `catering-menu.json` NEVER appears in an option (the script rejects unknown
  item names anyway). The `catering_dispatcher` skill owns the plain-language
  refusal + closest-catalog-alternatives reply; this skill only ever emits
  catalog-exact item names.
- Every `item_names` entry MUST be an exact name from `catering-menu.json`.

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
