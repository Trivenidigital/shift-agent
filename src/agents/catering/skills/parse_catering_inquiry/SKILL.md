---
name: parse_catering_inquiry
description: Use when catering_dispatcher determines this is a NEW catering inquiry from a customer. Extract structured fields (event_date, headcount, menu, dietary, contact) from the free-text inquiry, call /usr/local/bin/create-catering-lead to write state and trigger the owner approval flow. The script handles the customer acknowledgment automatically.
---

# Parse Catering Inquiry (Agent #2 — v0.4 / F10)

You receive a free-text catering inquiry. Extract whatever structured fields
the customer provided. Pass them to the deterministic state writer. The
state writer also handles the customer acknowledgment automatically — you
do NOT send any text reply yourself.

## ⚠️ CRITICAL HARD RULES — read these first, override everything below ⚠️

These rules are **absolute** and **non-negotiable**. They override any
"helpful" instinct to ask follow-up questions or to compose chat replies.

1. **NEVER ASK THE CUSTOMER FOR ANY INFORMATION.** Not phone, not name,
   not date, not headcount, not anything. The customer's message is the
   ONLY input you act on. If a field is missing, leave it `null` in
   `fields_json` — owner can follow up via cockpit. **In particular: the
   customer's phone is in `sender_phone` (the message metadata) — you
   ALREADY HAVE IT. Asking the customer "what is your phone number?"
   when their message arrived FROM that phone is a critical UX failure.**

2. **NEVER COMPOSE A CHAT REPLY TO THE CUSTOMER.** No "Thank you for your
   inquiry...", no "I'll prepare a quote...", no "Could you please
   share...". Those replies bypass the bridge filter, leak system internals,
   and damage trust. The script `create-catering-lead` sends the canonical
   prefixed acknowledgment automatically — that is the ONLY message the
   customer should see.

3. **NEVER MIX SHIFT-AGENT RESPONSES INTO THIS FLOW.** This SKILL is for
   CUSTOMER catering inquiries. Phrases like "Got it. Take care, we'll
   handle the shift." come from the shift-agent's sick-call handler and
   MUST NOT appear in customer chats. If you find yourself about to write
   shift-related text to a customer, STOP — that's a SKILL-mixing error.

4. **The script call in Step 2 IS the work.** Steps 0 and 1 are inputs
   to it. Step 3 is now empty (collapsed into Step 2 server-side).
   If you've called create-catering-lead and it returned exit 0, you are
   DONE. Output nothing further to the chat.

If the inquiry text is too vague to extract any fields (no headcount, no
date, no event hint), still call create-catering-lead with all-null fields
and a `notes` value documenting the ambiguity. Owner reviews via cockpit.
Do not ask the customer to clarify.

## Inputs received from catering_dispatcher

## Inputs received from catering_dispatcher

The dispatcher delegates to this SKILL with these named inputs:

- `sender_phone` — already E.164-validated by `validate-sender-block`. Use
  VERBATIM in any subprocess call below. Do NOT reformat, normalize, or
  derive a phone from `message_text`. Phone is metadata-only.
- `sender_name` — from identify-sender (when known). Profile names are
  unreliable (shared phones, group chats). Pass empty string when absent.
- `message_text` — body line 2+ of the inbound (line 1 is the v=1 block,
  already stripped by the dispatcher).
- `message_id` — Meta WhatsApp message id; idempotency key.

## Step 0 — Lookup prior leads (preamble)

**Hard rule:** Step 0 runs BEFORE Step 1 every time. It is a deterministic
helper, do not improvise. Skipping Step 0 produces a degraded extraction
(no soft-prior signal for returning customers, no dietary inheritance hint).

Unlike `validate-sender-block` in `dispatch_shift_agent` (which fails closed
on error), this lookup is **fall-open by design** — soft priors are advisory,
not authoritative. A failed lookup proceeds to Step 1 with no priors.

(Audit signal note: this lookup currently produces no `decisions.log` entry;
soak-monitoring is journald-only. A `lookup_invoked` LogEntry variant is
tracked as a P1.4 follow-up — see `tasks/todo.md`.)

Run this exactly:

```
/usr/local/bin/lookup-prior-leads-by-phone --customer-phone "<sender_phone>"
```

The script prints a JSON dict to stdout. Parse it. Read `lookup_status`:

| `lookup_status` | What it means | What to do |
|---|---|---|
| `ok` | Phone matched ≥1 prior lead | Use `most_recent_status`, `last_seen_days_ago`, `most_recent_dietary_restrictions`, AND `most_recent_notes` (Agent #32 v0.1) as **soft priors** for Step 1 extraction (e.g., if current message omits dietary but prior had `vegetarian`, you MAY default to vegetarian — never override explicit current-message content; if prior `most_recent_notes` says "extra-spicy preference" you MAY treat that as soft context for ambiguous current-message language like "make it like usual"). DO NOT echo any prior detail to the customer. |
| `no_match` | Phone unknown — first-time customer | Standard new-inquiry flow. |
| `missing_file` | Leads store not yet present (clean install) | Standard new-inquiry flow. |
| `lock_timeout` | Writer is mid-update; lookup couldn't acquire lock in 3s | Standard new-inquiry flow. Do NOT retry. |
| `corrupt` / `io_error` | State unreadable (alert path) | Standard new-inquiry flow. Script exits non-zero + emits stderr; operator gets visibility via journald. |
| any other status / stdout not parseable as JSON / script exited unexpectedly | Treat as unavailable | Proceed to Step 1 with no priors. Do NOT retry. Do NOT mention to the customer. |

**Hard rule:** the four prior-customer fields (`prior_lead_count`,
`last_seen_days_ago`, `most_recent_status`, `most_recent_dietary_restrictions`)
are extraction priors only. They MUST NEVER appear in any string sent to the
customer or written to `--raw-inquiry`. They never leave this SKILL's reasoning.

**Hard rule (Agent #32 v0.1 addition):** `most_recent_notes` is for Step 1's
extraction CONTEXT only. The LLM MUST NOT echo any portion of
`most_recent_notes` back into its Step 1 extraction output's `notes` field
— that would persist priors to lead state via `--raw-inquiry`, violating
the leak-guard rule above. If the prior says "regular customer prefers
extra-spicy", that may shape your understanding of "we want it like
usual" in the new message, but does NOT appear in the new lead's `notes`
field. The new lead's `notes` reflects ONLY what THIS message says.

The acknowledgment in Step 3 stays standard regardless of `lookup_status`. Do
NOT differentiate the customer-facing acknowledgment based on prior records —
phone numbers are frequently shared between household members and predecessor
roles in this customer segment, and continuity-of-identity assertions are a
trust hazard.

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

## Step 3 — DONE (acknowledgment is server-side)

**As of F6 (2026-05-01), `create-catering-lead` sends the customer
acknowledgment automatically with the bridge `template_bypass` prefix
prepended server-side.** You do NOT need to call send-catering-ack or
write any chat reply — the script handled it.

If Step 2 returned exit 0, you are DONE. Output nothing further.

If Step 2 returned exit 6 (owner card couldn't send), the lead is still
saved but the owner didn't get the card. Run shift-agent-notify-owner
with title="Catering card delivery failed" and the lead_id. Still do
NOT write any chat reply to the customer.

(Historical note: before F6, the customer ack was a separate step that
the LLM had to remember to call. That created two failure modes — LLM
forgot the prefix, or LLM skipped the call entirely. Both were observed
in production. F6 collapsed the ack into the lead-create script so the
ack ALWAYS fires whenever a lead is created. The send-catering-ack
script still exists as a fallback for edge cases but you should not
need to call it.)

## Hard rules

- NEVER guess fields. Empty/null is correct when unsure.
- NEVER skip Step 2 (create-catering-lead) — without it there's no audit
  trail, no owner approval flow, AND no customer ack.
- NEVER ask the customer for any information (phone, name, contact, date,
  headcount). All metadata is in `sender_phone`/`sender_name`/`message_id`;
  all extracted fields are best-effort from `message_text`. Missing field
  → null in fields_json, owner reviews via cockpit.
- NEVER compose a chat reply to the customer. F6 in create-catering-lead
  sends the canonical prefixed acknowledgment. Anything else you write
  bypasses the bridge filter, leaks system prose, and damages trust.
- NEVER respond with shift-agent text ("Got it. Take care, we'll handle
  the shift.") in catering chats. That's a SKILL-mixing error.
- NEVER use the customer's WhatsApp profile name as `customer_name`. Profile
  names are unreliable (shared phones, group chats, fake names). Pass
  empty string if you only have what's in the message body.
- NEVER quote a price, even if budget_hint_usd was provided. Pricing is
  owner-only.
