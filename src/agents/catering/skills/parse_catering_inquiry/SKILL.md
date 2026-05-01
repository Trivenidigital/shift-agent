---
name: parse_catering_inquiry
description: Use when catering_dispatcher determines this is a NEW catering inquiry from a customer. Extract structured fields (event_date, headcount, menu, dietary, contact) from the free-text inquiry, then call /usr/local/bin/create-catering-lead to write state and trigger the owner approval flow. Does NOT reply to the customer.
---

# Parse Catering Inquiry (Agent #2 — v0.2)

You receive a free-text catering inquiry. Extract whatever structured fields
the customer provided. Pass them to the deterministic state writer. Do not
guess, do not invent, do not reply to the customer.

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
| `ok` | Phone matched ≥1 prior lead | Use `most_recent_status`, `last_seen_days_ago`, `most_recent_dietary_restrictions` as **soft priors** for Step 1 extraction (e.g., if current message omits dietary but prior had `vegetarian`, you MAY default to vegetarian — never override explicit current-message content). DO NOT echo any prior detail to the customer. |
| `no_match` | Phone unknown — first-time customer | Standard new-inquiry flow. |
| `missing_file` | Leads store not yet present (clean install) | Standard new-inquiry flow. |
| `lock_timeout` | Writer is mid-update; lookup couldn't acquire lock in 3s | Standard new-inquiry flow. Do NOT retry. |
| `corrupt` / `io_error` | State unreadable (alert path) | Standard new-inquiry flow. Script exits non-zero + emits stderr; operator gets visibility via journald. |
| any other status / stdout not parseable as JSON / script exited unexpectedly | Treat as unavailable | Proceed to Step 1 with no priors. Do NOT retry. Do NOT mention to the customer. |

**Hard rule:** the four prior-customer fields (`prior_lead_count`,
`last_seen_days_ago`, `most_recent_status`, `most_recent_dietary_restrictions`)
are extraction priors only. They MUST NEVER appear in any string sent to the
customer or written to `--raw-inquiry`. They never leave this SKILL's reasoning.

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

## Step 3 — Acknowledge customer (sparingly)

DO NOT send the customer a quote. DO NOT promise pricing.

**Reply MUST be prefixed with the agent header** to bypass the WhatsApp
bridge's outbound filter. Without this prefix, replies matching LLM-narration
patterns (`Thanks for`, `I'll`, `I understand`, etc.) are silently dropped
by the bridge as `reason=announcement`. The header triggers `template_bypass`
in the bridge. See `bridge.js:133` for the exact regex match:

```
/^⚕ \*[A-Za-z][A-Za-z ]*\*\n[─\-]+\n/
```

Important constraint: agent name MUST be `[A-Za-z ]+` (letters and spaces
only). No digits, no hyphens, no punctuation in the agent name slot.

**EXACT format below — copy these examples verbatim, including the REAL
newline characters between lines.** The triple-backtick code fences below
contain literal newline characters (U+000A). When you emit the message,
press Enter at each line break — do NOT emit the two-character escape
sequence `\` + `n` as text. The bridge regex requires real newlines.

You may send ONE brief acknowledgment if the inquiry was clear enough to
process:

```
⚕ *Catering Agent*
────────────
Thanks — we got your inquiry, we'll be back to you shortly.
```

If extraction was unclear (e.g., no headcount, no date, vague intent), you
may instead reply with the same prefix:

```
⚕ *Catering Agent*
────────────
Thanks for reaching out. To help, can you share the date and headcount?
```

DO NOT loop on this. ONE clarifying reply max, then escalate to the owner
via the lead state.

**Hard rule on the prefix:** the bridge filter is defense-in-depth against
LLM internal monologue (`I'll process X`, `Let me check Y`). The
`⚕ *Catering Agent*` header signals "this is a deliberate customer-facing
message, not LLM thinking-aloud." Skipping the prefix = customer never
receives the reply.

## Hard rules

- NEVER guess fields. Empty/null is correct when unsure.
- NEVER skip the script call — without it there's no audit trail and no
  owner approval flow.
- NEVER use the customer's WhatsApp profile name as `customer_name`. Profile
  names are unreliable (shared phones, group chats, fake names). Pass
  empty string if you only have what's in the message body.
- NEVER quote a price, even if budget_hint_usd was provided. Pricing is
  owner-only.
