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
   `catering-menu.json`, classifies the lead's stated diet, and produces 2 options —
   or 3 when the request text asks for `three` / `3` — each a COMPLETE
   course-spanning menu; you supply NO options JSON). Headcount is NOT an input to
   item selection; it only drives the owner-side indicative pricing ledger:

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
  `--auto-generate-from-menu` mode selects menu-grounded options and guarantees each
  option is a COMPLETE menu rather than a single course: it spans at least three
  sections (or as many as the menu can serve this lead, when the menu offers fewer),
  and it includes a main course whenever the menu has one at all. Both are enforced
  fail-closed for `veg_only` and `mixed` leads — an option that falls short is
  REFUSED and the owner is notified, never sent. These invariants live in the script,
  not in an LLM-composed payload.
- **Diet handling (what the script actually enforces)** — the script classifies the
  lead as one of `veg_only` / `non_veg_only` / `mixed` / `unknown` from
  `extracted.dietary_restrictions` plus the raw inquiry text, and then:
  - `veg_only` (all-vegetarian, Jain, vegan, temple event) — non-veg items are
    excluded from the candidate pool entirely, and a fail-closed guard refuses to
    send any option containing a non-veg item. The completeness rule above still
    applies, so if the menu's only mains are non-veg the proposal is REFUSED rather
    than sent as a side-and-dessert spread. Vegan and Jain fold into `veg_only`
    because the menu schema carries no vegan/Jain flag, so those leads still need
    owner review before send.
  - A lead that mentions BOTH diets in its text ("half veg half chicken") is
    `mixed`, not vegetarian — the script will not drop meat the customer asked for.
  - `mixed` (e.g. a 90-non-veg / 30-veg wedding) — every option gets BOTH real
    non-veg and veg catalog items, enforced fail-closed.
  - `non_veg_only` and `unknown` — both diets stay available; an unstated diet is
    never guessed, and the owner reviews before send.
  Do NOT claim to the owner or the customer that the script honors any dietary
  detail beyond this — headcount, allergies, and no-onion/no-garlic are NOT inputs
  to item selection.
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

  The recompose path enforces the same vegetarian-only rule as generation. If the
  lead is `veg_only` and the requested combination would pull a non-veg item
  forward out of an option sent earlier, the script REFUSES and alerts the owner
  rather than sending. It does not quietly drop the item, because a mix-and-match
  merge is verbatim by contract — substituting a dish behind the customer's back
  would be worse than a refusal the owner can act on. Do not retry the same
  combination; the owner resolves it.
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
