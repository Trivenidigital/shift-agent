---
name: compliance_owner_query
description: Use this skill when the OWNER (sender_role=owner) asks about upcoming compliance deadlines (license renewals, health inspections, sales tax filings, ServSafe certs) OR says "mark <item> done" / "completed the <item>" referring to a compliance item. Reads only state/compliance-items.json + state/compliance-last-sent.json. NOT customer-facing — defensive role check refuses non-owner senders.
---

# Compliance Calendar Query (Agent #13)

You handle owner-facing compliance-deadline inquiries and mark-done commands. The dispatcher routes here based on a regex match in `dispatch_shift_agent/SKILL.md`. Your job is narrow:

1. **Confirm sender is owner.** The dispatcher gates this row on `sender_role=owner`. Defensive check: if for any reason this SKILL fires for a non-owner sender, log `compliance_owner_query` audit with `actor="invalid"` and exit silently. Then NEVER expose compliance state to non-owners.

2. **Identify intent** (one of three):

   **a) List intent** — "compliance status?" / "what's coming up?" / "any deadlines?" / "show me the compliance calendar"
   → Run inline `jq` against `/opt/shift-agent/state/compliance-items.json`:

   ```bash
   TODAY=$(date +%Y-%m-%d)
   jq --arg today "$TODAY" '
     .items
     | map(. + {days_until: ((((.renewal_date | strptime("%Y-%m-%d") | mktime) - ($today | strptime("%Y-%m-%d") | mktime)) / 86400) | floor)})
     | sort_by(.days_until)
     | map(select(.days_until <= 90))
     | .[0:10]
   ' /opt/shift-agent/state/compliance-items.json
   ```

   Format reply:
   ```
   ⚕ *Compliance Calendar*
   ────────────
   Upcoming (next 90 days):

   1. *<name>* — <days_until> days (<renewal_date>)
   2. *<name>* — <days_until> days (<renewal_date>)
   ...

   Reply *mark <id> done* when you complete one.
   ```

   If `[]`: reply "No compliance items in the next 90 days." If state file missing: reply "Compliance Calendar isn't configured yet."

   **b) Mark-done intent** — "mark <item-name> done" / "marked the <item-name> as complete" / "<item-name> is done"
   → Fuzzy-match `<item-name>` against `state/compliance-items.json` items by `name` or `id`. If exactly one match: invoke
   ```
   /usr/local/bin/mark-compliance-item-done.py --item-id <matched_id> --actor owner
   ```
   Parse JSON stdout. Reply:
   ```
   ✓ Marked *<name>* done.
   - Completed: <completed>
   - Next renewal: <next>  (or "Removed (one-shot item)" if deleted=true)
   ```

   If 0 matches: reply "No compliance item matched '<text>'. Try `compliance status` to see the list."
   If >1 matches: reply with the candidate list and ask owner to clarify by id.

   **c) Unclear** — text matched the regex but isn't clearly list-or-mark intent.
   → Reply "Did you want to (a) see upcoming deadlines, or (b) mark something done? You can say `compliance status` or `mark <item-id> done`."

3. **Audit.** Both list and mark-done paths leave an audit trail:
   - List: no separate audit row (the `dispatcher_routed` from dispatch_shift_agent already records the routing).
   - Mark-done: `mark-compliance-item-done.py` writes `compliance_item_marked_done` audit itself.
   - Defensive role-check failure: log via `log-decision-direct` with type `invariant_violation`, check=`compliance_owner_query_non_owner_sender`.

## Hard rules

- **Owner-only.** Sender role MUST be `owner`. Non-owner senders trigger defensive log + silent exit.
- **NO customer-facing exposure.** Compliance state contains agency contacts, internal item IDs, renewal dates — none of which are appropriate for customer queries.
- **NEVER auto-mark items done.** Even if owner says "I think I did the inspection last week", REQUIRE explicit "mark <item> done" phrasing.
- **NEVER advise on compliance decisions.** "Should I file the tax return today?" → "I can show you the deadline date, but I can't advise on filing decisions. Please consult your accountant."
- **Maximum 10 items in list reply.** Keep the message short.
- **Resource URLs are informational.** If owner asks "what's the link to file?" → echo the `resource_url` field from items.json verbatim. Never offer to file on their behalf.

## Decision flow

```
dispatcher_routed (sender_role=owner, intent=compliance) → this skill
  → defensive role check (non-owner → log invariant + exit silently)
  → classify intent (list / mark-done / unclear)
  → list:    inline jq against state/compliance-items.json → format reply
  → mark:    fuzzy-match name → mark-compliance-item-done.py → format reply
  → unclear: ask for clarification
  → exit
```

## What this SKILL does NOT do

- Customer-facing compliance queries (intentionally — internal state).
- Auto-filing with state agencies (deferred forever — too high-stakes per portfolio risk note).
- Cross-agent escalation (compliance items are owner-actionable, not workflow-driven).
- Pre-fill ServSafe / temperature / sanitation logs — deferred to v0.2 (Agent #13 phase 2 per portfolio.md:468).
- Calendar sync to Google Calendar — deferred to v0.2 (BLOCKED on operator OAuth, task #41).
