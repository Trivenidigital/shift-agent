---
name: catering_followup_dispatcher
description: Use post-event for catering bookings — automated thank-you with personal touch, feedback request, repeat-booking nudge. Depends on Agent #2 Catering Lead being configured. v0.1 stub.
---

# Catering Follow-up (Agent #10) — v0.1 stub

## Phase 0 (current)

`cfg.catering_followup.enabled = False`. Self-declines.

When invoked while disabled, log `agent_declined` with `agent="catering_followup"` + `reason="agent_disabled"` via `log-decision-direct` before the decline reply.

## Phase 1 (v0.2)

Triggers when Agent #2 transitions a lead to `CLOSED` status. Sends thank-you (template) → feedback request (single-question) → schedules anniversary nudge for next year.

> **Honesty note (corrected 2026-10-09):** nothing invokes THIS scaffold, and
> nothing should — the follow-up runtime lives in Catering Studio, not here.
> `src/platform/catering_followups.py` schedules `post_event_feedback` (which
> admits `CLOSED` leads), `event_approaching`, `final_headcount_due`,
> `proposal_unanswered`, `incomplete_qualification` and owner-created
> `owner_reminder` follow-ups from the trigger sites in `amend-catering-lead` and
> `apply-catering-owner-decision`; `catering-followup-sweep` cards them to the
> owner behind three independent default-off gates
> (`cfg.catering_followup.enabled`, `CATERING_FOLLOWUP_ENABLED=1`,
> `CATERING_FOLLOWUP_ALLOWLIST`); `approve-catering-followup` sends only the
> exact carded text. The timer is installed but deliberately NOT enabled by the
> deploy (see its unit comment). Arming is an operator decision per VPS. This
> file stays a Phase-0 self-declining stub; do not build follow-up logic here
> (catering-studio directive, "Follow-up policy" row).

## Hard rules

- Initial thank-you auto-sends (template-based; cleared in cfg).
- Anniversary nudges 11 months out REQUIRE owner approval (cold templates feel artificial).
- Negative feedback ESCALATES IMMEDIATELY — never auto-respond to complaints.
