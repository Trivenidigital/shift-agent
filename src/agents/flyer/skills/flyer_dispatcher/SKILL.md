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
2. If no active project exists and the text matches flyer intent, call
   `/usr/local/bin/create-flyer-project`.
3. If required fields are missing, delegate to `flyer_intake`.
4. If the state is `awaiting_assets` and an image/document is present, store
   the asset and continue.
5. If the state is `generating_concepts`, call
   `/usr/local/bin/generate-flyer-concepts`. It generates one best design,
   selects `C1`, and moves directly to `awaiting_final_approval`.
6. If a legacy project is in `awaiting_concept_selection`, interpret `1`,
   `2`, `3`, or natural equivalents as the selected concept.
7. If the state is `revising_design`, append the customer revision and
   regenerate previews.
8. If the state is `awaiting_final_approval`, only exact `APPROVE` advances to
   `finalizing_assets`. Anything else is a revision request.
9. If the state is `finalizing_assets`, call
   `/usr/local/bin/finalize-flyer-assets` then
   `/usr/local/bin/send-flyer-package`.
10. Delivery scripts use `bridge_send_media` through `send-flyer-package`; do
    not hand-write WhatsApp media payloads in the SKILL.

Always audit state transitions through script chokepoints. Do not send final
assets until the customer has replied exact `APPROVE`.
