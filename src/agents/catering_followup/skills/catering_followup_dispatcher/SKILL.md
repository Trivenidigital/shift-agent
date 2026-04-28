---
name: catering_followup_dispatcher
description: Use post-event for catering bookings — automated thank-you with personal touch, feedback request, repeat-booking nudge. Depends on Agent #2 Catering Lead being configured. v0.1 stub.
---

# Catering Follow-up (Agent #10) — v0.1 stub

## Phase 0 (current)

`cfg.catering_followup.enabled = False`. Self-declines.

## Phase 1 (v0.2)

Triggers when Agent #2 transitions a lead to `CLOSED` status. Sends thank-you (template) → feedback request (single-question) → schedules anniversary nudge for next year.

## Hard rules

- Initial thank-you auto-sends (template-based; cleared in cfg).
- Anniversary nudges 11 months out REQUIRE owner approval (cold templates feel artificial).
- Negative feedback ESCALATES IMMEDIATELY — never auto-respond to complaints.
