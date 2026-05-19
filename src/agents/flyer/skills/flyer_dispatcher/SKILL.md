---
name: flyer_dispatcher
description: Route WhatsApp flyer requests through Hermes Flyer Studio state machine. Handles intake, one-shot design generation, revisions, exact APPROVE final approval, and delivery handoff.
---

# Flyer Dispatcher

## Hermes-first analysis

Use Hermes for WhatsApp ingress, sender validation, image-cache paths, skill
dispatch, state/audit conventions, and the bridge media endpoint. Net-new work
is flyer workflow state, brand-kit memory, revision history, deterministic
asset rendering, quality checks, and delivery package orchestration.

## State machine

Valid states:

- `intake_started`
- `collecting_required_info`
- `awaiting_assets`
- `generating_concepts`
- `awaiting_concept_selection` (legacy multi-concept projects only)
- `revising_design`
- `awaiting_final_approval`
- `finalizing_assets`
- `delivered`
- `completed`

## Inputs from `dispatch_shift_agent`

Expect: `sender_phone`, `sender_lid`, `sender_role`, `sender_name`,
`message_text`, `message_shape`, `message_id`, and optional `image_path`.

## Dispatch rules

1. If `cfg.flyer.enabled` is false, politely say Flyer Studio is not enabled.
2. Customer-bound project creation, generation, approval, finalization, and
   delivery MUST be handled by the deterministic `cf-router` pre-gateway path.
   That path owns account status, quota/guest-order reservation, manual-review
   state, and visual-QA gates. If a normal WhatsApp flyer request reaches this
   SKILL, treat it as a routing miss: send a short Flyer Studio recovery reply
   and do not call project/render/delivery scripts directly.
3. Operator-only diagnostics may inspect state, but must not call
   `/usr/local/bin/create-flyer-project`, `/usr/local/bin/generate-flyer-concepts`,
   `/usr/local/bin/finalize-flyer-assets`, or `/usr/local/bin/send-flyer-package`
   for a customer delivery unless the caller is an operator using an explicit
   break-glass runbook.
4. If a customer asks for status, reply that the request is being routed through
   Flyer Studio and ask them to send `STATUS` again if no update arrives.
5. If a customer sends `APPROVE` here, do not finalize. Reply that the approval
   must be handled by Flyer Studio's tracked project flow and ask them to resend
   `APPROVE` after the project preview message.

Always audit state transitions through script chokepoints. Do not send final
assets until the customer has replied exact `APPROVE`.
