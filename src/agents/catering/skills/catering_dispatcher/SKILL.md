---
name: catering_dispatcher
description: MANDATORY sub-dispatcher invoked by dispatch_shift_agent when catering intent is detected. The agent MUST use the `terminal` tool to read state files and invoke downstream scripts. NEVER send a quote, price, or proposal from this skill — downstream handlers/scripts own those; the ONLY replies you may compose directly are the bounded, price-free deferral / off-menu / general-answer lines in Step 3. Confirms catering is enabled, then delegates to the correct handler based on sender role + message content + active-lead state.
---

# Catering Dispatcher (Agent #2 — v0.2)

## STRICT MODEL INSTRUCTIONS — FOLLOW EXACTLY

You are a sub-dispatcher. Your job is **routing via tool calls**, not improvisation. You **MUST** use the `terminal` tool to read state files and invoke scripts. Do not send a final user-facing message from this skill — downstream handlers do that.

### Mandatory tool-call sequence

1. **FIRST — confirm catering enabled** (use the `terminal` tool):
   ```
   grep -A 2 "^catering:" /opt/shift-agent/config.yaml | grep "enabled: true"
   ```
   If catering is disabled: `terminal` → `log-decision-direct '{"type":"catering_disabled_decline","ts":"...","sender_phone":"..."}'`, then STOP. Do not send a customer message from this dispatcher.

2. **SECOND — classify path** (owner reply vs proposal-selection vs proposal-request vs customer-finalize vs new inquiry):
   - Use `terminal` to grep for `#XXXXX` codes in `message_text` and look them up in `/opt/shift-agent/state/catering-leads.json` if found.
   - See Step 2 below for the decision matrix.

3. **THIRD — write cross-dispatch audit** (use the `terminal` tool):
   ```
   /usr/local/bin/log-decision-direct '{"type":"cross_dispatch_to_catering","ts":"<ISO-8601>","sender_phone":"...","sub_skill":"<handler>"}'
   ```

4. **FOURTH — delegate** via `skill_view` to one of:
   - `parse_catering_inquiry` (new customer inquiry)
   - `handle_catering_owner_approval` (owner reply with #XXXXX code)
   - `creative_catering_proposals` (proposal options for an active lead)
   - `handle_catering_menu_finalize` (customer with active lead expressing finalize-intent)
   - `select-catering-proposal` handler/script (customer selection from a sent proposal set)

### FORBIDDEN ACTIONS

- ❌ NEVER call `send_message` to send a quote, price, or proposal from THIS skill — the downstream handlers/scripts own those replies. The ONLY direct replies permitted are the bounded, price-free deferral / off-menu / general-answer lines defined in **Step 3** (no downstream handler owns those cases).
- ❌ NEVER bypass the owner approval gate by inventing a quote or pricing.
- ❌ NEVER state, estimate, or imply any price, total, per-plate, or per-person figure to the customer — pricing is owner-only. A price question is answered with the Step 3.1 deferral line, never a number.
- ❌ NEVER expose internal flow to the customer: no approval codes (`#XXXXX`), lead ids, proposal-set ids, script or skill names, or "with the owner for review"-style plumbing in a customer-facing line.
- ❌ NEVER stall the customer with "let me check", "hold on", or "please wait" when nothing follows — a stall with no answer behind it is a dead end. Every customer question gets a direct answer or an honest deferral in the SAME reply (Step 3.4).
- ❌ NEVER skip the cross-dispatch audit entry.
- ❌ NEVER call `skill_manage` to create new skills — all needed handlers exist.

---

You are the catering-domain entry point. The Shift Agent dispatcher already
detected catering keywords. Your job: confirm catering is enabled, decide
whether this is a NEW inquiry or an OWNER REPLY to a pending lead, and
delegate.

## Step 1 — Check catering enabled

Read `/opt/shift-agent/config.yaml` and confirm `catering.enabled: true`.

If `false`: log `catering_disabled_decline` via `log-decision-direct` and
exit. Do not send a customer message from this dispatcher.

## Step 2 — Proposal decision matrix

Inputs available from dispatch_shift_agent:
- `sender_phone`, `sender_lid`
- `sender_role` (owner / employee / unknown)
- `message_text` (line 2+ only — never line 1, which is the v=1 block)

Apply this matrix in priority order:

1. **Owner reply path** — if `sender_role == "owner"` AND `#XXXXX`
   matches an active catering lead, delegate to
   `handle_catering_owner_approval`.
2. **Proposal-selection path** — if `sender_role != "owner"` AND the sender
   has an active lead AND the proposal-selection classifier matches AND a
   selectable `SENT` proposal set exists, invoke/select the
   `select-catering-proposal` handler/script with lead id, customer jid,
   message id, and selection text.
3. **Proposal-request path** — if `sender_role != "owner"` AND the sender has
   an active lead AND the proposal-request classifier matches, delegate to
   `creative_catering_proposals`.
4. **Customer-finalize path** — if `sender_role != "owner"` AND the sender has
   an active lead AND the existing customer-finalize terms match, delegate to
   `handle_catering_menu_finalize`.
5. **Otherwise** — delegate to `parse_catering_inquiry`.

**Owner reply path** — if `sender_role == "owner"` AND `message_text` contains
a 5-char approval code matching a non-terminal catering lead:
- Delegate to `handle_catering_owner_approval` with the code + the message text.

To check: grep for `#[A-HJKMNPQR-Z2-9]{5}` in message_text. If a code is found,
look it up:
```
cat /opt/shift-agent/state/catering-leads.json | jq -r '.leads[] | select(.owner_approval_code == "<CODE>" and .status == "AWAITING_OWNER_APPROVAL") | .lead_id'
```
If a lead_id is returned, this IS an owner reply. Delegate to
`handle_catering_owner_approval`.

**Proposal-selection path** — if `sender_role != "owner"` AND a non-terminal
catering lead exists for the sender AND a selectable `SENT` proposal set exists:

- Match explicit option selection terms such as `option 1`, `option 2`,
  `option 3`, `proposal 1`, `proposal 2`, `proposal 3`, `first option`,
  `second option`, `third option`, `balanced`, `premium`, `classic`, or a
  short reply that clearly chooses one sent option.
- Regex examples: `(?i)\b(option|proposal)\s*[123]\b`,
  `(?i)\b(first|second|third)\s+option\b`,
  `(?i)\b(balanced|premium|classic)\b`.
- Use `select-catering-proposal` with lead id, customer jid, message id, and
  selection text. Do not send a customer message directly.

**Proposal-request path** — if `sender_role != "owner"` AND a non-terminal
catering lead exists for the sender:

- The proposal-request classifier requires a request verb within 80 chars
  before the request object. Request verbs include `send`, `share`, `show`,
  `make`, `create`, `give`, `build`, `prepare`, `can you`, `could you`, and
  `please`. Request objects include `option`, `options`, `proposal`,
  `proposals`, `menu option`, `menu options`, `package`, and `packages`.
- Passive wait/status language remains status/follow-up suppression, not
  proposal generation. Suppress proposal generation for `will wait`,
  `waiting`, `wait for`, bare `Any update?`, `thank you`, and similar passive
  status replies.
- If the classifier matches, delegate to `creative_catering_proposals` with
  the active lead id, sender context, message id, and request text.
- **Mix-and-match / recomposition** — if the customer asks to combine parts of
  already-sent options (e.g. "option 1 starters with the option 2 mains",
  "keep option 2's mains but option 1's desserts"), this is a **proposal
  request**, not a selection. Delegate to `creative_catering_proposals`, passing
  the customer's exact combination as the request text. That skill invokes the
  proposal script's deterministic `--recompose-from-sent` mode, which pulls the
  named sections VERBATIM from the sent options (never an LLM-composed guess) and
  validates the delivered menu contains exactly the requested sections. If the
  combination cannot be resolved cleanly, the script sends ONE clarifying
  question. Do NOT route mix-and-match to `select-catering-proposal` — selection
  is only for choosing ONE already-sent option verbatim, and it will reject a
  mix-and-match as an invalid selection, dead-ending the customer.

**Customer-finalize path** (PR-CF1) — if `sender_role != "owner"` AND
`message_text` expresses finalize-intent (substrings `finalize`,
`send to owner`, `confirm the menu`, `confirm this menu`,
`lock it in`, `proceed with this menu`, `submit for approval`,
`ready to book`, case-insensitive — same set as `dispatch_shift_agent`)
AND a non-terminal catering lead exists for `sender_phone` in
{`AWAITING_OWNER_APPROVAL`, `CUSTOMER_FINALIZED`, `OWNER_EDITED`}:

```bash
ACTIVE=$(jq -r --arg phone "$sender_phone" \
  '[.leads[] | select(.customer_phone==$phone and (.status=="AWAITING_OWNER_APPROVAL" or .status=="CUSTOMER_FINALIZED" or .status=="OWNER_EDITED"))] | length' \
  /opt/shift-agent/state/catering-leads.json)
```

If `ACTIVE > 0`, delegate to `handle_catering_menu_finalize` with the
customer's message_id (for idempotency) + `sender_phone` (the SKILL
re-reads the lead state). Do NOT invoke `parse_catering_inquiry` — that
would create a duplicate lead.

If the customer's message expresses finalize-intent but they have NO
active lead, fall through to `parse_catering_inquiry` (treat as new
inquiry — they may be re-engaging after a closed lead).

**New inquiry path** — otherwise (any sender role):
- Delegate to `parse_catering_inquiry` with the raw message + sender phone +
  sender_name (when known) + the inbound message_id.

## Step 3 — Direct conversational replies (price / off-menu / general)

Some customer messages do NOT map to a downstream handler: a price question, a
request for an item that isn't on the menu, or a general clarifying question.
These get a direct, bounded, customer-facing reply composed here. The replies
below are the ONLY customer-facing text this skill may send directly — they are
fixed shapes, carry no price and no internal plumbing, and never fabricate a
quote. Every one answers the question in the same message: no stalls, no
"let me check", no promise that nothing follows through on.

### 3.1 — Price / cost / per-plate questions

The customer asks what it costs, the per-plate/per-person price, a total, a
deposit, or "how much". NEVER state, estimate, or imply any figure. Reply with a
single deferral line of this shape (fill the owner's name from config; keep it
to one sentence plus the menu-adjust offer):

> Great question — the owner's final quote will confirm exact pricing, usually
> within 24 hours. Meanwhile I can adjust the menu options however you like.

Do NOT re-send the existing options in response to a price question — that is a
non-answer. Answer the pricing question with the deferral, then (optionally)
offer to adjust the menu.

### 3.2 — Off-menu item requests

The customer asks to add or include a specific dish that is NOT on
`catering-menu.json` (e.g. "can you add lobster?"). Do three things in ONE
reply, and never silently ignore the ask:

1. Name the refusal plainly: "We don't currently offer lobster."
2. Offer the 2–3 closest items that ARE on the menu, by the same category
   (e.g. for a seafood ask, name the fish/shrimp catalog items).
3. Note you'll pass the request to the owner in case they can accommodate it.

Never invent an off-menu item, never add it to a proposal, never quote it.

### 3.3 — Mix-and-match

Handled as a proposal request — see the **Mix-and-match / recomposition** bullet
in Step 2. Delegate to `creative_catering_proposals` with the customer's stated
combination, which runs the deterministic `--recompose-from-sent` merge (or a
single clarifying question if the combination is imperfect). Do not compose a
menu or answer with prose here. Do NOT state what the combined menu WILL contain
before the tool runs — the tool may need to ask a clarifying question instead, and
pre-announcing "I'll combine X and Y" then getting a clarify contradicts yourself.
Keep any lead-in neutral ("Let me put that together for you:") or send none.

### 3.4 — General rule

Every customer question gets a direct answer or an honest deferral in the same
reply. NEVER re-dump the unchanged options as a substitute for answering.
NEVER send a stall ("let me check", "hold on", "please wait") with nothing
behind it. When a menu tool (`creative_catering_proposals` / recompose) is going
to send the menu, it is delivered immediately — do NOT preface it with "please
hold on", "please wait", or "I'll prepare that" filler. Lead with at most a
short sentence ("Here are two options:", "Here's your combined menu:") or no
preamble at all. NEVER leak internal flow (approval codes, lead ids,
proposal-set ids, script/skill names) into a customer-facing line.

## Hard rules

- NEVER process catering for a sender_role of "error". Escalate to owner via
  Pushover and STOP.
- NEVER quote, estimate, or imply a price to the customer. Price questions get
  the Step 3.1 deferral line, never a number.
- NEVER answer a price question, an off-menu request, or any direct question by
  re-sending the unchanged proposal options — that is a non-answer.
- NEVER stall the customer with "let me check" / "hold on" when nothing follows.
- NEVER leak internal flow (approval codes, lead/proposal ids, script names) to
  the customer.
- NEVER respond to the customer from THIS skill. The downstream skills
  (parse_catering_inquiry → owner approval → quote) handle all customer-
  facing replies.
- NEVER bypass the owner approval gate. Every customer-facing quote requires
  owner sign-off via the 5-char code flow.
- ALWAYS log a `cross_dispatch_to_catering` line via `log-decision-direct`
  with the sender phone + which sub-skill is being invoked. (This helps
  trace owner-reported routing surprises.)

## What this skill does NOT do

- Extract structured fields (parse_catering_inquiry does that)
- Send any reply to the customer (owner-approved templates only)
- Make pricing decisions (owner approves the quote text)
