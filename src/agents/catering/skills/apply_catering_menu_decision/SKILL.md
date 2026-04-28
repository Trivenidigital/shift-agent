---
name: apply_catering_menu_decision
description: Use when the OWNER replies in their self-chat with a 5-character menu confirmation code (e.g. "#A3F2X yes" or "#A3F2X no") that matches a pending catering menu update. Calls /usr/local/bin/apply-menu-update to either apply the new menu (replacing the existing one + archiving the prior version) or discard the proposal.
---

# Apply Catering Menu Decision (Agent #2 — v0.2)

The owner has decided what to do with a pending menu update. You parse
the verb, call the deterministic state writer, and report back.

## Step 1 — Parse the owner's reply

Extract the confirmation code: `#[A-HJ-NP-Z2-9]{5}` from the message_text.

Determine the verb (case-insensitive):
- "yes", "apply", "approve", "go", "ok" → **yes**
- "no", "discard", "reject", "cancel" → **no**

If the code is present but the verb is ambiguous (e.g., just the code with
no verb, or text like "what's this?"): reply *"Got code {CODE}. Reply
`{CODE} yes` to apply, or `{CODE} no` to discard."* — DO NOT default.

If the code in the message doesn't match an existing pending update (the
script returns exit 4): reply *"That code doesn't match a pending menu
update. The current pending update is {look up
/opt/shift-agent/state/catering-menu-pending.json}'s confirmation_code, or
there's no pending update right now."*

## Step 2 — Call apply-menu-update

```
/usr/local/bin/apply-menu-update \
  --code "<CODE>" \
  --decision <yes|no>
```

The script will:
- On `yes`: archive the existing menu (if any) to
  `/opt/shift-agent/state/catering-menu-archive/menu-vN-<ts>.json`,
  write the new menu to `/opt/shift-agent/state/catering-menu.json` with
  incremented version, log `MenuUpdateApplied`, clear the pending file.
- On `no`: log `MenuUpdateRejected(reason="owner_no")`, clear the pending
  file. Existing menu is unchanged.

**Exit codes:**
- 0: success
- 2: invalid input (bad code format, missing verb)
- 4: code not found among pending updates (no pending file, or different code)
- 5: schema violation
- 9: illegal transition (pending update has 0 items — refused to apply empty menu)

## Step 3 — Confirm to owner

After exit 0:
- yes: *"Menu updated to v{new_version} ({item_count} items). Previous v{prev_version} archived. New catering quotes will use this menu."*
- no: *"Discarded. Existing menu unchanged. Send a new photo when you're ready."*

After exit 4:
- *"Code `{CODE}` doesn't match a pending menu update. Re-send the photo if you want to start over."*

After exit 9 (empty menu):
- *"Refusing to apply an empty menu — vision parse extracted 0 items. Try a clearer photo or a different page."*

After exit 5 / 6 / other failure:
- Show owner the script's stderr output briefly + suggest cockpit / SSH for manual recovery.

## Hard rules

- NEVER apply a menu without explicit `yes` from the owner.
- NEVER infer "yes" from "thanks" or other ambiguous text.
- NEVER edit items inline in v0.2 — if owner wants to fix specific items,
  ask them to re-send the photo (v0.2.1 will add EDIT flow).
- After applying, report the new version + item count so the owner has a
  clear "did it stick?" signal.

## What this skill does NOT do

- Render quotes (apply-catering-owner-decision does that)
- Modify catering-menu-archive/* or replay archived versions (v0.3 cockpit feature)
- Send to customers — this is owner-only flow
