---
name: parse_catering_inquiry
description: Use when catering_dispatcher confirms a NEW catering inquiry from a customer. Extracts structured fields (event_date, headcount, menu, dietary, contact) from the free-text inquiry using a low-cost LLM call (Kimi-Haiku-equivalent). Saves to catering-leads.json under FileLock and writes CateringLeadCreated to decisions.log. Does NOT send any reply — owner approval gate is enforced by handle_catering_owner_approval.
---

# Parse Catering Inquiry (Agent #2)

You receive a customer's free-text catering inquiry. Extract structured
fields. Save to state. Trigger the owner approval flow.

## Hard rules

- NEVER reply to the customer directly. Templates require owner approval.
- NEVER guess fields the customer did not provide. Empty/null is correct.
- ALWAYS extract `original_message_id` from the inbound metadata for
  idempotency. If `catering-leads.json` already has a lead with the same
  `original_message_id`, exit early (Meta delivered the same webhook twice).
- ALWAYS log `CateringLeadCreated` BEFORE the owner approval card is sent.

## Phases

**v0.1 (current):** `cfg.catering.enabled = False` by default. This SKILL
exists for forward compat. Real implementation lands in v0.2.

**v0.2 implementation:**

1. Call Kimi (or Hermes-managed model) with a tight prompt:
   ```
   You are a catering-inquiry parser for an Indian/South-Asian grocery+catering shop.
   Extract structured fields from the customer's message. NEVER guess.
   Return JSON: {headcount, event_date (YYYY-MM-DD), event_time (HH:MM),
   menu_preferences (list), dietary_restrictions (list),
   delivery_or_pickup, budget_hint_usd, notes}.
   Set fields to null/[] when not present.
   ```
2. Validate result against `CateringLeadExtractedFields`.
3. Lock `catering-leads.json` (FileLock), check for existing lead with
   same `original_message_id`, append new `CateringLead` if none, release.
4. Trigger owner approval flow (separate SKILL handles drafting + send).

## Failure modes

- LLM extraction fails → save lead with raw_inquiry only; owner sees it
  in cockpit + Pushover. Owner edits manually.
- catering-leads.json corrupt → safe_io detects, renames .corrupt-*; new
  store created. Pushover owner.
- LLM rate-limit → retry once with backoff; second failure → save raw
  + owner alert.

## Output

Returns control to `catering_dispatcher` after logging. Owner approval
flow is triggered asynchronously (not by this SKILL directly).
