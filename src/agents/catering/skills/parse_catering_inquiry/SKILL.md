---
name: parse_catering_inquiry
description: Use when catering_dispatcher determines this is a NEW catering inquiry from a customer. Extract structured fields (event_date, headcount, menu, dietary, contact) from the free-text inquiry, then call /usr/local/bin/create-catering-lead to write state and trigger the owner approval flow. Does NOT reply to the customer.
---

# Parse Catering Inquiry (Agent #2 — v0.2)

You receive a free-text catering inquiry. Extract whatever structured fields
the customer provided. Pass them to the deterministic state writer. Do not
guess, do not invent, do not reply to the customer.

## Step 1 — Extract structured fields

From the message_text (line 2+ of the inbound, excluding the sender block),
extract these fields. Set ANY field you can't determine from the message to
`null` (or empty list). Do NOT guess.

```json
{
  "headcount": <int 1-10000 or null>,
  "event_date": "<YYYY-MM-DD or null>",
  "event_time": "<HH:MM 24h or null>",
  "menu_preferences": [<list of menu items mentioned, or empty>],
  "off_menu_items": [<list of specific dishes the customer named that AREN'T on your current menu, or empty>],
  "dietary_restrictions": [<list: vegetarian, vegan, jain, halal, kosher, gluten-free, etc. or empty>],
  "delivery_or_pickup": "<delivery | pickup | unknown>",
  "budget_hint_usd": <int >=0 or null>,
  "notes": "<short string of anything else relevant>"
}
```

**`menu_preferences` vs `off_menu_items`**: `menu_preferences` is soft categories (vegetarian, spicy, north-indian). `off_menu_items` is specific dishes the customer named that you cannot find on the current menu — owner needs these to decide whether to add ad-hoc or decline. When in doubt, leave both empty.

**Examples:**

Input: "Need catering for 50 people on June 15 wedding reception, budget around $1000, vegetarian please. Can you do butter chicken and lamb biryani?"
Output: `{"headcount": 50, "event_date": "2026-06-15", "event_time": null, "menu_preferences": [], "off_menu_items": ["butter chicken", "lamb biryani"], "dietary_restrictions": ["vegetarian"], "delivery_or_pickup": "unknown", "budget_hint_usd": 1000, "notes": "wedding reception"}`

Input: "Hi, do you do catering for ~20 ppl?"
Output: `{"headcount": 20, "event_date": null, "event_time": null, "menu_preferences": [], "off_menu_items": [], "dietary_restrictions": [], "delivery_or_pickup": "unknown", "budget_hint_usd": null, "notes": ""}`

Input: "Anyone there?"
Output: `{"headcount": null, "event_date": null, "event_time": null, "menu_preferences": [], "off_menu_items": [], "dietary_restrictions": [], "delivery_or_pickup": null, "budget_hint_usd": null, "notes": ""}`

## Step 2 — Call create-catering-lead

Pass the extracted JSON + the inbound metadata to the state writer:

```
/usr/local/bin/create-catering-lead \
  --customer-phone "<sender_phone>" \
  --customer-name "<sender_name OR empty string>" \
  --raw-inquiry "<message_text — first 1000 chars>" \
  --message-id "<inbound message_id>" \
  --fields-json '<the JSON dict above>'
```

The script will:
1. Validate the JSON against the Pydantic schema (rejects negative headcount, bad date, etc.)
2. Check idempotency on (customer_phone, message_id) — replays are no-ops
3. Mint a new lead_id (L0001, L0002, ...) and a unique 5-char approval code
4. Write the lead to `/opt/shift-agent/state/catering-leads.json` (atomic + flock)
5. Log `CateringLeadCreated` + `CateringLeadStatusChange(NEW→AWAITING_OWNER_APPROVAL)` + `CateringOwnerApprovalRequested`
6. Send the approval card to the owner's self-chat via the bridge

**Read the script's exit code:**
- 0: success — lead saved, owner card sent (or saved with `card_sent: false` if bridge unreachable; reply to caller noting owner needs to check cockpit)
- 2: catering disabled — should not happen if catering_dispatcher routed correctly; log + STOP
- 2 (invalid input): your fields_json failed validation. Re-extract more conservatively (set bad fields to null) and retry ONCE.
- 5: schema violation on existing state file — alert owner via Pushover, STOP, do not retry.
- 6: owner card couldn't send (bridge issue). Lead is saved. Run shift-agent-notify-owner with title="Catering card delivery failed" and the lead_id.

## Step 3 — Acknowledge customer (sparingly)

DO NOT send the customer a quote. DO NOT promise pricing.

You may send ONE brief acknowledgment if the inquiry was clear enough to
process: *"Thanks — we got your inquiry, we'll be back to you shortly."*

If extraction was unclear (e.g., no headcount, no date, vague intent), you
may instead reply: *"Thanks for reaching out. To help, can you share the
date and headcount?"* — but DO NOT loop on this. ONE clarifying reply max,
then escalate to the owner via the lead state.

## Hard rules

- NEVER guess fields. Empty/null is correct when unsure.
- NEVER skip the script call — without it there's no audit trail and no
  owner approval flow.
- NEVER use the customer's WhatsApp profile name as `customer_name`. Profile
  names are unreliable (shared phones, group chats, fake names). Pass
  empty string if you only have what's in the message body.
- NEVER quote a price, even if budget_hint_usd was provided. Pricing is
  owner-only.
