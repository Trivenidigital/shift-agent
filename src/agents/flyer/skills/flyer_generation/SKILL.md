---
name: flyer_generation
description: Generate one best flyer design, manage revisions, and prepare final assets for Hermes Flyer Studio.
---

# Flyer Generation

## Production rule

Do not ask the image model to render critical text. Dates, phone numbers,
addresses, prices, QR labels, Telugu, Hindi, Spanish, and business names must be
rendered by the server-side compositor.

Use image generation for the single best customer-facing design, not for three
parallel options. The final flyer package must pass quality checks before
delivery.

## Model policy

- Draft design: configured `draft_image_model`, default one high-quality
  generation. Avoid multi-option generation unless the operator explicitly
  configures `concept_count > 1`.
- Final export: reuse the approved selected design and resize/package it for
  WhatsApp, Instagram post, Instagram story, and printable PDF. Do not spend
  image credits again on approval.

## Revision policy

Summarize each revision into concrete design changes. Preserve multi-round
revision memory. If a request affects critical text, update structured fields
first, then regenerate assets.
