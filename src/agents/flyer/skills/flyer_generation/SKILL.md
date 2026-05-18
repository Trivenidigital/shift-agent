---
name: flyer_generation
description: Generate one best flyer design, manage revisions, and prepare final assets for Hermes Flyer Studio.
---

# Flyer Generation

## Production rule

Use controlled direct generation for real image-model concepts. First build the
structured flyer facts, then ask the image model to render a complete finished
poster with the exact business name, schedule, item names, prices, address, and
phone number. Do not send vague background-only prompts for production customer
flyers.

The deterministic/Pillow renderer remains the low-cost smoke fallback. The
server-side compositor may still be used for deterministic fallback assets or
legacy raw-background exports, but customer-grade model concepts should arrive
from the image model as integrated poster compositions.

For Telugu, Hindi, Malayalam, Tamil, Kannada, Gujarati, Marathi, Punjabi, Spanish, or mixed-language flyers, ask for large readable
regional-language typography and avoid missing-glyph placeholder boxes.

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
- Reference images/templates: preserve the visual hierarchy and brand feel, but
  replace stale readable facts with the current structured flyer facts.

## Revision policy

Summarize each revision into concrete design changes. Preserve multi-round
revision memory. If a request affects critical text, update structured fields
first, then regenerate assets.
