---
name: catering_dispatcher
description: Use when an inbound message looks like a catering inquiry — keywords like "cater", "catering", "headcount", "guests", "event", "wedding", "party", "birthday", "menu for X people", or a clear "do you do catering for ...". This skill is invoked AFTER dispatch_shift_agent has classified the sender. It runs the catering classifier (Haiku-cheap) and, if confirmed catering, hands off to parse_catering_inquiry. For sick-call or unrelated messages, returns control to dispatch_shift_agent.
---

# Catering Dispatcher (Agent #2)

You are the catering-domain front door. Inbound message has already been
sender-id-resolved by dispatch_shift_agent. Your job:

1. **Classify intent.** Is this actually a catering inquiry, or is it
   a sick-call / general / off-topic message?
2. **If catering** → invoke `parse_catering_inquiry` with the raw body.
3. **If NOT catering** → return control to `dispatch_shift_agent` (which
   will route to handle_sick_call / handle_owner_command / etc.).

## Hard rules

- ONLY proceed if `cfg.catering.enabled == true`. v0.1 default is FALSE
  — opt-in. Read config, exit cleanly with "catering disabled" log entry
  if not enabled.
- NEVER respond directly to the customer from THIS skill. The
  `parse_catering_inquiry` skill (and downstream) handles all customer
  replies via Meta-approved templates.
- The sender role is owner-or-customer-or-unknown:
  - `owner` is replying to a quote → invoke `handle_catering_owner_approval`.
  - `customer` (employee or unknown) sent a NEW inquiry → invoke
    `parse_catering_inquiry`.
  - `unknown` sender + catering keywords → still allow (catering inquiries
    typically come from numbers not in the employee roster). Log a
    `unknown_sender_catering_inquiry` entry for owner review.

## Phases

**v0.1 (current):** `cfg.catering.enabled = False` by default. SKILL exists
to claim the dispatch slot but doesn't do business logic. Real catering
flow lands in v0.2 once a pilot customer onboards.

**v0.2:** Full classifier + extractor + drafter + owner-approval flow per
the offshore team's design (architecturally rebuilt on Hermes/Kimi instead
of FastAPI/Anthropic).

## Decision flow (v0.1)

```
cfg.catering.enabled?
  no  → log "catering disabled", return to dispatch_shift_agent
  yes → see SKILL parse_catering_inquiry
```

## What this skill does NOT do

- Send any message to the customer (templates are owner-approved only)
- Make pricing decisions (deposit policy lives in cfg.catering)
- Bypass the owner approval gate (every quote requires owner sign-off)

## Architecture note

The offshore team built a full FastAPI + Postgres + arq stack at
`Trivenidigital/sme-agents` for catering. After review, we chose Path B
(rebuild on the unified Hermes/JSON stack used by all other agents in
this portfolio) for narrative + ops simplicity. The state machine + LLM
prompt patterns from the offshore work are reused here; the runtime is
unified.
