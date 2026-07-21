---
name: creative_catering_proposals
description: Generate menu-grounded catering proposal options for an active catering lead. MUST invoke /usr/local/bin/create-catering-proposal-options and MUST NOT send customer messages directly or include prices/payment/booking language.
---

# Creative Catering Proposals

This is a deterministic Hermes skill for active catering leads, not a freeform
chat reply. It invokes the proposal script; it does not compose proposal item
lists and it does not render customer-visible prose.

**Plain proposal generation is cf-router's deterministic job, not yours.** An
active-lead PLAIN proposal request ("send me two sample menus") is intercepted by
cf-router BEFORE this skill runs and generated via
`create-catering-proposal-options --auto-generate-from-menu` — the script selects
catalog items itself; no LLM composes the menu. The reachable job of this skill is
the **mix-and-match recompose** (combining sections of already-SENT options). If a
plain proposal request reaches this skill at all (a fallback), invoke the SAME
deterministic generator rather than composing an item list yourself.

## Required flow

1. Read the active catering lead by explicit lead id when provided. If no lead
   id is provided, resolve the active lead from sender context in
   `/opt/shift-agent/state/catering-leads.json`.
2. Invoke the deterministic menu-grounded generator (it reads
   `catering-menu.json`, honors the lead's headcount + dietary mix, and produces
   2 options — or 3 when the request text asks for `three` / `3` — each a COMPLETE
   course-spanning menu; you supply NO options JSON):

   ```bash
   /usr/local/bin/create-catering-proposal-options \
     --lead-id <lead_id> \
     --customer-jid <customer_jid> \
     --source-message-id <inbound_message_id> \
     --request-text <request_text> \
     --auto-generate-from-menu
   ```

## Composition rules

- **Default (deterministic generation)** — do NOT compose the item list. The
  `--auto-generate-from-menu` mode selects menu-grounded options that fit the
  lead's headcount and dietary mix (e.g. a 90-non-veg / 30-veg wedding gets options
  with real non-veg and veg catalog items, not veg-only) and guarantees each option
  is a COMPLETE menu spanning courses (at least one starter/appetizer AND at least
  one main; a dessert/side when the menu offers them). This invariant lives in the
  script, not in an LLM-composed payload.
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
  `catering-menu.json` NEVER appears in an option: both the deterministic generator
  and the recompose merge draw only from the catalog, and the script rejects any
  unknown item name. The `catering_dispatcher` skill owns the plain-language refusal
  + closest-catalog-alternatives reply; this skill composes no item list of its own.

## Output contract

- Invoke `/usr/local/bin/create-catering-proposal-options` and let it own the
  menu selection + the customer-visible rendering; this skill emits NO proposal
  item lists and NO proposal prose.
- Plain generation: `--lead-id <lead_id>`, `--customer-jid <customer_jid>`,
  `--source-message-id <inbound_message_id>`, `--request-text <request_text>`, and
  `--auto-generate-from-menu` — the script selects the catalog items.
- Mix-and-match: the same four flags plus `--recompose-from-sent` (no options JSON).
- Customer-visible prose is rendered by the script from validated, catalog-exact
  item names and closed style keys — never composed here.

## Forbidden

- NEVER call send_message.
- NEVER include prices.
- NEVER include deposits, payments, Venmo, Zelle, payment rails, booking
  confirmation, customer-facing quotes, or any payment language.
- NEVER produce freeform customer-facing proposal prose.
- Do not invoke `finalize-catering-menu`; selection is handled separately by
  `select-catering-proposal`.
