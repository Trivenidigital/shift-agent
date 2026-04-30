---
name: handle_catering_owner_approval
description: Use when the OWNER replies in their self-chat with a 5-character approval code (e.g. "#A3F2X") matching a non-terminal catering lead. Parses the owner's intent (approve / reject / edit). On approve, drafts the customer-facing quote in a single LLM turn, then calls /usr/local/bin/apply-catering-owner-decision via stdin.
---

# Handle Catering Owner Approval (Agent #2 — v0.4 LLM-drafted)

The owner has responded to a pending catering quote. Decode their intent
deterministically, draft the customer-facing quote in this same turn (on
approve), call the state writer, do not free-text-reply to the customer.

**v0.4 paradigm change:** the customer quote is drafted by the LLM in
this SKILL, not rendered from a template. Apply-script accepts the
drafted text on stdin (`--quote-text-stdin`); the previous template
path + argv flag are deleted (RCE surface eliminated, paradigm flipped
to LLM substrate).

## Step 1 — Parse the owner's reply

Extract the code with format `#[A-HJ-NP-Z2-9]{5}`:

```bash
CODE=$(echo "<message_text>" | grep -oE "#[A-HJ-NP-Z2-9]{5}" | head -1)
```

If no code matched: ask the owner to include the code from the approval
card. DO NOT guess which lead they meant; multiple inquiries may be open.

## Step 2 — Determine the decision

Look at the message_text (case-insensitive) for one of these verbs:

| Owner says | decision |
|---|---|
| "approve", "yes", "send", "ok", "go", "send it" | **approve** |
| "reject", "no", "decline", "pass" | **reject** |
| "edit", "change", "modify", or anything followed by edit text | **edit** |

If the owner's intent is ambiguous (e.g., just the code with no verb), reply:
*"Got code {CODE}. Reply with `approve`, `edit <changes>`, or `reject`."*
Do NOT default to any decision.

For `edit`: extract everything in the message AFTER the code AND the verb
as the edit text. Truncate to 1000 chars.

For `reject` and `edit`, skip directly to Step 4 (no quote drafting needed).

## Step 3 — On `approve`: read context + draft the quote (single LLM turn)

### 3a — Read state files inline

Hermes SKILLs are scripts with filesystem access — no separate
context-bundler script is needed (mirrors `parse_catering_inquiry`
SKILL Step 0 deployed pattern):

```bash
LEAD_JSON=$(jq -c --arg code "$CODE" '.leads[] | select(.owner_approval_code==$code)' \
    /opt/shift-agent/state/catering-leads.json)

if [ -z "$LEAD_JSON" ]; then
    # Code didn't match any AWAITING lead — apply-script will return exit 4.
    # Skip drafting; let apply-script handle the error path.
    QUOTE_TEXT=""
else
    CUSTOMER_NAME=$(echo "$LEAD_JSON" | jq -r '.customer_name // "there"')
    HEADCOUNT=$(echo "$LEAD_JSON" | jq -r '.extracted.headcount // empty')
    EVENT_DATE=$(echo "$LEAD_JSON" | jq -r '.extracted.event_date // empty')
    EVENT_TIME=$(echo "$LEAD_JSON" | jq -r '.extracted.event_time // empty')
    DIETARY=$(echo "$LEAD_JSON" | jq -r '.extracted.dietary_restrictions // [] | join(", ")')
    LEAD_ID=$(echo "$LEAD_JSON" | jq -r '.lead_id')

    # Filtered menu items (optional context — apply-script no longer
    # consumes this; LLM can include 2-3 sample items if helpful).
    MENU_ITEMS=$(jq -c '[.items[] | select(.available==true) | {name, category, price_usd, dietary_tags}]' \
        /opt/shift-agent/state/catering-menu.json 2>/dev/null || echo "[]")
fi
```

### 3b — Draft the customer quote (this turn)

In the SAME Kimi turn (no second LLM round-trip), produce a plain-prose
WhatsApp message addressed to the customer. Constraints:

1. **Plain prose ONLY.** No markdown delimiters (`*`, `_`, `~`, `` ` ``).
   No code fences. No bullet-point glyphs (use simple "- " hyphens if
   listing). Apply-script's normalizer strips markdown, but the LLM
   should produce clean prose to begin with.
2. **MUST include the literal headcount integer** if `$HEADCOUNT` is
   set. Apply-script's truth-guard rejects drafts where the headcount
   number is missing or appears only as a substring of a larger number
   (e.g., `"50,000"` doesn't count as headcount=50).
3. **MUST include the literal ISO event_date as a parenthetical** if
   `$EVENT_DATE` is set. Format: `(YYYY-MM-DD)`. Place AFTER any prose
   date so the customer sees natural language, the truth-guard sees
   the ISO. Example: *"Saturday, May 10 (2026-05-10)"*.
4. Greet by `$CUSTOMER_NAME` if non-empty; else "Hi there".
5. Reference dietary preferences if `$DIETARY` non-empty.
6. Optionally mention 2-3 sample menu items from `$MENU_ITEMS` matching
   dietary tags. Keep concise — the message goes to WhatsApp.
7. Keep total length under ~500 characters (apply-script caps at 600;
   leave headroom for normalize-strip).
8. End with a polite call-to-action ("Reply here to confirm" or similar).
9. Sign off with the lead reference: `(Ref: $LEAD_ID)`.

Store the drafted text in `$QUOTE_TEXT` (no leading/trailing whitespace).

## Step 4 — Call apply-catering-owner-decision

For `approve` (with drafted quote on stdin):

```bash
echo "$QUOTE_TEXT" | /usr/local/bin/apply-catering-owner-decision \
    --code "$CODE" --decision approve --quote-text-stdin
RC=$?
```

For `reject` (no stdin):

```bash
/usr/local/bin/apply-catering-owner-decision \
    --code "$CODE" --decision reject --reason "<rejection reason>"
RC=$?
```

For `edit` (no stdin):

```bash
/usr/local/bin/apply-catering-owner-decision \
    --code "$CODE" --decision edit --edit-text "<edit body>"
RC=$?
```

The script will:

1. Find the lead with that code in `AWAITING_OWNER_APPROVAL` status (under FileLock).
2. Transition to `OWNER_APPROVED` / `OWNER_REJECTED` / `OWNER_EDITED`.
3. Log `CateringLeadStatusChange` + `CateringOwnerDecision`.
4. On `approve`: read drafted text from stdin, normalize (strip control/format
   Unicode + markdown delimiters, cap at 600 chars), run truth-guard
   (headcount integer + ISO event_date present), send via the WhatsApp
   bridge to the customer's `<phone>@s.whatsapp.net`. On send success,
   transition to `SENT_TO_CUSTOMER` and log `CateringQuoteSent`. On send
   failure, the lead stays at `OWNER_APPROVED` (operator-visibility for
   retry; PR-D2 retry-state-machine handles bridge transients).

## Step 5 — On apply-script non-zero exit: emit failure audit

If the apply-script returns non-zero AND we provided a drafted quote, emit
a covering `catering_quote_skill_failed` audit row. Apply-script writes
its own row best-effort for `truth_guard_failed` / `missing_quote_text`,
but the SKILL emits a separate row for `apply_decision_nonzero` so the
SKILL-side path is never silent:

```bash
if [ "$RC" -ne 0 ] && [ "$DECISION" = "approve" ]; then
    log-decision-direct "$(jq -n \
        --arg ts "$(date -u -Iseconds)" \
        --arg lead_id "$LEAD_ID" \
        --arg code "$CODE" \
        --arg detail "exit=$RC" \
        '{type:"catering_quote_skill_failed",ts:$ts,lead_id:$lead_id,
          code:$code,reason:"apply_decision_nonzero",detail:$detail}')" \
        2>&1 | logger -t catering-skill-failed || true
fi
```

The `jq -n --arg` pattern eliminates shell-escape RCE: `$LEAD_ID`, `$RC`,
etc. are passed as JSON-quoted args, not interpolated into the JSON
template body. Stderr piped through `logger -t` lands in journald, never
silently swallowed.

**Read the apply-script's exit code:**

| Exit | Meaning | SKILL response |
|---|---|---|
| 0 | success — customer received the quote (approve) or state advanced (reject/edit) | reply to owner per Step 6 |
| 2 | invalid input — `--quote-text-stdin` missing on approve, or stdin empty / oversize | tell owner: *"Internal error drafting the quote — operator alerted."* (a P3 Pushover may also fire; rare) |
| 4 | code not found among AWAITING_OWNER_APPROVAL leads | tell owner: *"Code {CODE} doesn't match an active lead."* |
| 5 | schema violation on state file — DO NOT retry | tell owner: *"State file issue — operator alerted."* + Pushover P2 |
| 6 | customer-side bridge unreachable on approve | tell owner: *"Approved, but couldn't reach customer right now. Will retry."* |
| 7 | truth-guard rejected drafted quote (apply-script's `EXIT_DEPENDENCY_DOWN`) — typically headcount or ISO date missing from prose | tell owner: *"Quote drafting needs another pass — please retry the code."* (operator can also re-prompt) |
| 9 | illegal transition (lead already terminal) | tell owner: *"Lead {lead_id} already in {status} — already handled."* |

## Step 6 — Confirm to owner

After the script returns 0:

- **approve + send-OK**: *"Sent to {lead.customer_name or phone}. Lead {lead_id} → SENT_TO_CUSTOMER."*
- **approve + send-failed (exit 6)**: *"Approved, but customer send failed. Will retry — or reach them directly."*
- **reject**: *"Lead {lead_id} declined. Logged."*
- **edit**: *"Got your edits. The drafter will incorporate them."*

## Hard rules

- NEVER infer the customer's response — they haven't replied yet.
- NEVER send the quote directly from this SKILL. The apply-script's
  `_bridge_post` is the only path.
- NEVER skip logging — every owner decision is auditable per portfolio
  compliance requirements.
- NEVER use shell-interpolation inside JSON for `log-decision-direct` —
  always build the JSON via `jq -n --arg` (RCE class).
- NEVER omit the literal ISO event_date `(YYYY-MM-DD)` parenthetical
  when drafting if `$EVENT_DATE` is set — the truth-guard will reject.
- NEVER omit the literal headcount integer when drafting if `$HEADCOUNT`
  is set — the truth-guard will reject.
- An owner trying to approve a lead they ALREADY approved (status was
  `SENT_TO_CUSTOMER`): apply-script returns exit 9; tell the owner it's
  already sent. Don't re-send, don't re-draft.
