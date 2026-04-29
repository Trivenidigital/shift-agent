---
name: parse_receipt_photo
description: Use after expense_bookkeeper_dispatcher routes a new owner receipt image. Invokes the deterministic extract-receipt script which copies the image to managed storage, computes perceptual hash, vision-extracts line items + total, classifies personal-vs-business, generates an approval code, persists the lead, and returns the rendered approval card to send to the owner.
---

# Parse Receipt Photo (Agent #21)

You are invoked with `image_path`, `sender_phone`, `sender_lid` (optional),
and `original_message_id`. Your job is to invoke the extraction script and
forward the resulting approval card to the owner.

## Step 1 — Invoke the script

```bash
/usr/local/bin/extract-receipt \
  --image-path "{{image_path}}" \
  --source-image-id "{{original_message_id}}" \
  --owner-phone "{{sender_phone}}" \
  --sender-lid "{{sender_lid|empty}}"
```

Read the JSON stdout. Possible exit codes:

- `0` — extracted; JSON has `expense_id`, `approval_code`, `approval_card_text`,
  `extraction_confidence`, `image_phash`, `duplicate_of`. Send the
  `approval_card_text` to the owner via the bridge.
- `5` — vision response failed schema validation. Reply: *"I couldn't read
  this receipt clearly. Please send a clearer photo, or describe the expense
  in text."* Log via `log-decision-direct` type=`expense_extraction_low_confidence`.
- `6` — OpenRouter / vision model unavailable. Reply: *"My vision service is
  temporarily unavailable. Please try again in a few minutes."* Log type=
  `expense_extraction_failed`.
- `7` — duplicate detected. JSON includes `duplicate_of` and the approval
  card already carries the dedup-detected banner. Send the card.
- `9` — idempotency hit (same `original_message_id` already processed).
  JSON returns the existing `expense_id`. Do NOT send a fresh card —
  reply *"Already processing receipt {{expense_id}}; check earlier message."*

## Step 2 — Send the approval card

Use the bridge `_bridge_post`-equivalent or the existing send helper. Quote-reply
the inbound image message when possible (catering precedent).

## Hard rules

- NEVER bypass the script and call OpenRouter directly. The script enforces
  managed-dir copy, perceptual-hash dedup, schema validation, audit entries.
- NEVER alter the rendered `approval_card_text` — it is generated from a
  template with the right field set per plan §4d.
- NEVER push to QBO from this skill. Push only happens after owner approval
  via `handle_expense_owner_approval`.
