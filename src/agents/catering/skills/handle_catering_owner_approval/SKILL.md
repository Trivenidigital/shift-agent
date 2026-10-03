---
name: handle_catering_owner_approval
description: Use when the OWNER replies in their self-chat with a 5-character approval code (e.g. "#A3F2X") matching a non-terminal catering lead. Parses the owner's intent (approve / reject / edit). On approve, calls /usr/local/bin/apply-catering-owner-decision to render and deliver the validated, cents-exact quote.
---

# Handle Catering Owner Approval

The owner has responded to a pending catering quote. Interpret their intent,
then call the existing state writer. Never send a separate customer quote.
The script renders customer-visible items and money from frozen pricing inputs
or the validated discount computation. Models do not supply monetary prose.

## Step 1 — Parse the owner's reply

Extract the code with format `#[A-HJKMNPQR-Z2-9]{5}`:

```bash
CODE=$(echo "<message_text>" | grep -oE "#[A-HJKMNPQR-Z2-9]{5}" | head -1)
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

**Pin the decision in shell** so Step 5's audit-emission conditional has
the variable bound (Kimi sets exactly one of these based on the parse above):

```bash
# Pick exactly ONE of these — the one that matches the owner's verb in Step 2.
DECISION=approve   # or
DECISION=reject    # or
DECISION=edit
```

For `reject` and `edit`, skip Step 3 (no customer quote is sent) and go
directly to Step 4.

## Step 3 — On `approve`: read the pending lead

### 3a — Read state files inline

Hermes SKILLs are scripts with filesystem access — no separate
context-bundler script is needed (mirrors `parse_catering_inquiry`
SKILL Step 0 deployed pattern):

```bash
# Default LEAD_ID for the empty-LEAD_JSON branch — Step 5's audit emission
# requires a non-empty lead_id (Pydantic min_length=1). "UNKNOWN" satisfies
# the constraint while signaling the no-match path. Review-fix HIGH-4.
LEAD_ID=UNKNOWN

LEAD_JSON=$(jq -c --arg code "$CODE" '.leads[] | select(.owner_approval_code==$code)' \
    /opt/shift-agent/state/catering-leads.json)

if [ -z "$LEAD_JSON" ]; then
    # Code didn't match any AWAITING lead — apply-script will return exit 4.
    # Let apply-script handle the missing-lead error path.
    QUOTE_TEXT=""
else
    CUSTOMER_NAME=$(echo "$LEAD_JSON" | jq -r '.customer_name // "there"')
    HEADCOUNT=$(echo "$LEAD_JSON" | jq -r '.extracted.headcount // empty')
    EVENT_DATE=$(echo "$LEAD_JSON" | jq -r '.extracted.event_date // empty')
    EVENT_TIME=$(echo "$LEAD_JSON" | jq -r '.extracted.event_time // empty')
    DIETARY=$(echo "$LEAD_JSON" | jq -r '.extracted.dietary_restrictions // [] | join(", ")')
    LEAD_ID=$(echo "$LEAD_JSON" | jq -r '.lead_id')


fi
```

### 3b — Use the committed quote

Use `--quote-from-lead-state`. The script requires a real commercial pricebook
and deliverable pricing provenance. It preserves exact cents, package and item
prices, the total, headcount, event date and validity deadline. It never truncates
the committed quote at the legacy 600-character draft cap; quotes above the
stored text limit are refused before state mutation or sending.

The legacy `--quote-text-stdin` interface remains accepted for old callers and
still checks draft size, date and headcount. Its prose is not forwarded: the
same canonical quote is delivered. Do not use a draft to change price, promise
booking, or introduce items. Owner edits remain instructions for the existing
edit/finalize workflow; use an approved discount through the pricing kernel.

## Step 4 — Call apply-catering-owner-decision

For `approve`, render the committed quote:

```bash
/usr/local/bin/apply-catering-owner-decision \
    --code "$CODE" --decision approve --quote-from-lead-state \
    --sender-role "<owner|employee|customer|unknown from sender block>"
RC=$?
```

`--sender-role` is the role resolved by `identify-sender` from the v=1 sender
block at the top of the inbound. Pass it through verbatim — the script
rejects with exit 12 (privilege denied) if it isn't `owner`. This is
defense-in-depth against a screenshot-forwarded `#XXXXX` code an employee
or customer might try to abuse (B-021).

**PR-CF1 — owner-approve guard.** The apply-script REFUSES approve
(EXIT_TRUTH_GUARD_FAILED, exit code 11) when the lead has no
`customer_finalized_at` (i.e. customer never ran the finalize flow).
The script sends the owner a reprompt explaining the override path.

If the owner explicitly tells you to "approve anyway" / "send the
original quote" / "skip the finalize check" after seeing the reprompt,
re-invoke with `--skip-finalize`:

```bash
/usr/local/bin/apply-catering-owner-decision \
    --code "$CODE" --decision approve --quote-from-lead-state \
    --sender-role "<owner|employee|customer|unknown from sender block>" --skip-finalize
RC=$?
```

When the lead status is already `CUSTOMER_FINALIZED` (customer DID
finalize), the guard does NOT fire — use the regular approve form
without `--skip-finalize`. The override never bypasses pricebook/provenance gates. The lead's `selected_items` and
`pricing_inputs` are the committed selection and cents-exact pricing. The legacy
`quote_total_usd` field is rounded; never use it to compose a customer price.

For `reject` (no stdin):

```bash
/usr/local/bin/apply-catering-owner-decision \
    --code "$CODE" --decision reject --reason "<rejection reason>" \
    --sender-role "<owner|employee|customer|unknown from sender block>"
RC=$?
```

For `edit` (no stdin):

```bash
/usr/local/bin/apply-catering-owner-decision \
    --code "$CODE" --decision edit --edit-text "<edit body>" \
    --sender-role "<owner|employee|customer|unknown from sender block>"
RC=$?
```

The script will (actual execution order — important for owner mental model
when failure paths fire mid-flow). Review-fix HIGH-2:

1. Find the lead with that code in `AWAITING_OWNER_APPROVAL` status (under FileLock).
2. **On `approve`, before persisting any change**, validate pricebook and
   frozen pricing provenance, then render the complete cents-exact quote. Missing
   or pending prices refuse with exit 18; no customer send or lead mutation occurs.
3. Persist the exact approved text, deadline and `OWNER_APPROVED` state, and
   record the owner decision and send-attempt audit.
4. Send the canonical quote through the existing bridge. Success records
   `SENT_TO_CUSTOMER`. Uncertain delivery must never be retried automatically.
   A definite failure can retry the same approved text and original deadline;
   changed terms, missing/expired deadlines or an omitted approved discount
   refuse with exit 18. Re-finalize when a genuinely new quote is required.
5. For `reject` / `edit`: similar — find lead, transition to
   `OWNER_REJECTED` / `OWNER_EDITED`, log decision, no stdin involved.

## Step 5 — On apply-script non-zero exit: emit failure audit

If approval returns non-zero and the lead was resolved, emit
a covering `catering_quote_skill_failed` audit row. Apply-script writes
its own row best-effort for `truth_guard_failed` / `missing_quote_text`,
but the SKILL emits a separate row for `apply_decision_nonzero` so the
SKILL-side path is never silent:

```bash
if [ "$RC" -ne 0 ] && [ "$DECISION" = "approve" ] && [ -n "$LEAD_ID" ]; then
    AUDIT_JSON=$(jq -n \
        --arg ts "$(date -u -Iseconds)" \
        --arg lead_id "$LEAD_ID" \
        --arg code "$CODE" \
        --arg detail "exit=$RC" \
        '{type:"catering_quote_skill_failed",ts:$ts,lead_id:$lead_id,
          code:$code,reason:"apply_decision_nonzero",detail:$detail}')
    LDD_OUT=$(log-decision-direct "$AUDIT_JSON" 2>&1)
    LDD_RC=$?
    # Review-fix M3: capture log-decision-direct's exit code separately
    # so a real schema regression (returns 5) doesn't get silently
    # masked. Surface to operator via journald.
    if [ "$LDD_RC" -ne 0 ]; then
        echo "WARN: log-decision-direct returned $LDD_RC for SKILL audit row: $LDD_OUT" \
            | logger -t catering-skill-failed
    fi
fi
```

The `jq -n --arg` pattern eliminates shell-escape RCE: `$LEAD_ID`, `$RC`,
etc. are passed as JSON-quoted args, not interpolated into the JSON
template body. The captured `$AUDIT_JSON` is then passed as a single
argv to `log-decision-direct` (argv-only interface, verified at
`src/platform/scripts/log-decision-direct:34-43`).

**Read the apply-script's exit code:**

| Exit | Meaning | SKILL response |
|---|---|---|
| 0 | inspect JSON: delivery may be confirmed, already delivered, or uncertain with resend refused | report only the recorded outcome; never retry uncertain delivery |
| 2 | invalid input — missing quote mode, invalid legacy draft, or committed quote above the storage limit | tell owner: *"The quote could not be prepared — operator review is needed."* (a P3 Pushover may also fire; rare) |
| 4 | code not found among AWAITING_OWNER_APPROVAL leads | tell owner: *"Code {CODE} doesn't match an active lead."* |
| 5 | schema violation on state file — DO NOT retry | tell owner: *"State file issue — operator alerted."* + Pushover P2 |
| 6 | customer-side bridge unreachable on approve — DEPENDENCY_DOWN; PR-D2 retry-state-machine handles this | tell owner: *"Approved, but delivery is unconfirmed. Operator review is needed."* |
| 9 | illegal transition (lead already terminal) | tell owner: *"Lead {lead_id} already in {status} — already handled."* |
| 11 | finalize guard or legacy draft sanity check refused approval | explain the refusal; do not retry the bridge or bypass price gates |
| 18 | missing/pending commercial pricing, changed retry terms or expired quote | resolve the prices/terms and re-finalize if required; do not resend automatically |

## Step 6 — Confirm to owner

Read both the exit code and JSON delivery fields:

- **approve + send-OK**: *"Sent to {lead.customer_name or phone}. Lead {lead_id} → SENT_TO_CUSTOMER."*
- **approve + failed or uncertain send**: *"Approved, but delivery is unconfirmed. Operator review is needed before another send."*
- **reject**: *"Lead {lead_id} declined. Logged."*
- **edit**: *"Got your edits. The quote needs revision before approval."*

## Hard rules

- NEVER infer the customer's response — they haven't replied yet.
- NEVER send the quote directly from this SKILL. The apply-script's
  `_bridge_post` is the only path.
- NEVER skip logging — every owner decision is auditable per portfolio
  compliance requirements.
- NEVER use shell-interpolation inside JSON for `log-decision-direct` —
  always build the JSON via `jq -n --arg` (RCE class).
- NEVER substitute prose, rounded totals or newly inferred items for the committed quote.
- NEVER extend a failed quote's validity on retry or retry an uncertain send.
- An owner trying to approve a lead they ALREADY approved (status was
  `SENT_TO_CUSTOMER`): apply-script returns exit 9; tell the owner it's
  already sent. Don't re-send, don't re-draft.
