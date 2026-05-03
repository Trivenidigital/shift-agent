---
name: update_catering_menu
description: Use when the OWNER sends a photo or PDF of a menu to the WhatsApp business number, with caption containing "update menu", "new menu", or just "menu". The skill calls parse-menu-photo to extract structured items via vision LLM, sends a preview to the owner's self-chat with a confirmation code, and waits for the owner to reply with the code + verb (yes / no / edit).
---

# Update Catering Menu (Agent #2 — v0.2)

The OWNER has sent a menu photo/PDF. You extract structured items, show
the owner a preview, and wait for their explicit confirmation.

## Hard rules

- ONLY the owner can update the menu (verified upstream by `dispatch_shift_agent`'s `identify-sender` returning `role=owner`).
- ONLY proceed when `cfg.catering.enabled = true`. Otherwise, decline with a clear message.
- NEVER apply the menu without the owner's explicit YES — every update goes through the preview-confirm loop.
- The image is at `mediaUrls[0]` from the inbound event. v0.2 supports JPEG, PNG, WebP, PDF.

### PR-CF3 fail-closed rule (do NOT improvise)

**This SKILL's ONLY job is to invoke `parse-menu-photo` and surface its output.**
You are NOT permitted to:

- Use `vision_analyze` or any other vision tool yourself to extract menu items
- Generate the pending-update file (`catering-menu-pending.json`) directly
- Generate the confirmation code yourself (the script does this via `_generate_unique_code` using the deployed alphabet)
- Improvise an alternate item schema (size variants, INR pricing, etc.) — the only acceptable schema is whatever `parse-menu-photo` writes, validated by the deployed `MenuPendingUpdate` Pydantic model

If `parse-menu-photo` returns a non-zero exit code, you MUST surface the
error to the owner per the Step 2 exit-code table and STOP. Under NO
circumstance should you "helpfully" do the work in-context — that bypasses
the schema validation, the deterministic preview rendering, the audit
emission, and the dispatcher's `#XXXXX` code lookup. The deployed pipeline
will reject any improvised pending file with `extra_forbidden` schema errors,
silently leaving the owner unable to apply the menu.

This rule exists because of an observed PR-CF2 incident on 2026-05-03 where
the LLM saw `parse-menu-photo` fail (vision auth was 401-ing at that point
in the session) and improvised its own pending file format with a non-`#`
confirmation code (`RQUKH` instead of `#YDW6J`), `menu_items` instead of
`extracted_items`, and size-variant pricing. The dispatcher could not route
the owner's reply, the active menu file was eventually written with all
prices as `null`, and the entire downstream finalize flow was broken.

## Step 1 — Validate the inbound

The inbound message must have:
- `mediaType = "image"` OR `mediaType = "document"` (with PDF mime)
- A path in `mediaUrls[0]` that exists on disk (typically
  `/opt/shift-agent/.hermes/image_cache/img_<hex>.<ext>` or
  `.../document_cache/doc_<hex>_<filename>`)
- A caption that says "menu", "update menu", "new menu", "menu update",
  or similar; OR a follow-up text message to the same effect

If the image path doesn't exist on disk: reply *"Hmm, I didn't get the
image — can you re-send?"* and STOP.

## Step 2 — Call parse-menu-photo

```
/usr/local/bin/parse-menu-photo \
  --image-path "<mediaUrls[0]>" \
  --source-image-id "<inbound message_id>" \
  --owner-phone "<sender_phone>"
```

The script will:
1. Read the image, base64-encode, send to OpenRouter with vision prompt
2. Validate response against the MenuItem schema
3. Generate a 5-char confirmation code
4. Write `/opt/shift-agent/state/catering-menu-pending.json` (atomic + flock)
5. Log `MenuUpdateProposed` to decisions.log
6. Return JSON: `{update_id, confirmation_code, item_count, preview_text}`

**Read the script's exit code:**
- 0: success — pass `preview_text` to step 3.
- 2: `--image-path` missing or unreadable. Tell owner the image couldn't be read; ask for resend.
- 3: catering disabled. Should not happen if dispatch routed correctly; log + STOP.
- 5: vision response failed schema validation. Tell owner the menu didn't parse cleanly; ask if they want to try a different image.
- 6: OpenRouter unreachable / API key missing. Tell owner the menu service is down; suggest retry in a few minutes.

## Step 3 — Send preview to owner

Reply to the owner's self-chat with the script's `preview_text`, prefixed
with the standard agent header. Include the confirmation code clearly.

Format the reply EXACTLY like this (so the owner sees a structured preview):

```
⚕ *Catering Agent*
────────────
*Menu Update {update_id}* — preview

{preview_text}

----
*To apply this menu, reply:* `{confirmation_code} yes`
*To discard and try again, reply:* `{confirmation_code} no`
```

## Hard rules (continued)

- DO NOT include any item NOT in the preview_text. The script rendered it deterministically; don't paraphrase.
- DO NOT modify item names, prices, or tags between extraction and preview. The owner needs to see exactly what was extracted.
- The preview is a single WhatsApp message; if it's too long for one message, the script already truncated each category to 8 items + "...and N more". Don't second-guess that.
- After sending the preview, your job is DONE. The owner's reply with the code is handled by `apply_catering_menu_decision` (a separate SKILL).

## What this skill does NOT do

- Apply the menu to disk (apply-menu-update does that, called by apply_catering_menu_decision)
- Render quotes for customers (apply-catering-owner-decision handles that)
- Edit individual items inline (v0.2 deferred — owner re-uploads if a few items are wrong)
