# Flyer Studio Mobile App V1 Follow-Up

**Drift-check tag:** extends-Hermes

**Status:** Follow-up note. No implementation is included here.

**Disposition:** The untracked draft `docs/superpowers/specs/2026-05-19-flyer-studio-mobile-v1-design.md` is a standalone Flyer Studio mobile app concept, not the Cockpit P2-6 mobile emergency/operator view.

**New primitives introduced:** mobile app channel adapter, app login identities, canonical Flyer conversation history, app response delivery, app device/push registration, app-store subscription entitlement sync, and mobile customer UI.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Existing Flyer creation/revision/approval/final delivery | yes - deployed Flyer project state, renderer, approval flow, delivery helpers, and cf-router | reuse Flyer core |
| WhatsApp customer channel | yes - deployed Hermes gateway, sender validation, WhatsApp bridge, and Flyer routing | keep as one channel |
| Operator cockpit emergency/mobile view | yes - existing cockpit/admin surface and Flyer manual queue support | keep separate from customer mobile app |
| Customer mobile app channel | none in deployed Hermes/Flyer stack | track separately as future app-channel work |
| Native app payments/push | none in deployed Hermes/Flyer stack | future connector/provider work, not pilot-hardening scope |

awesome-hermes-agent ecosystem check: existing Hermes/Flyer primitives cover the agent core, but no ready-made customer mobile app, app-store receipt sync, or app push channel replaces the proposed mobile edge. Verdict: preserve as a separate product follow-up, not as cockpit UI work.

## Decision

Do not attach the mobile app draft to Cockpit P2-6. Cockpit P2-6 should remain an operator emergency/mobile web surface for managing manual queues and production incidents. The mobile app draft is customer-facing, subscription/channel-oriented, and introduces app identities, push, app-store entitlements, and conversation history.

## Follow-Up Scope

- Keep the mobile app draft out of pilot-hardening PRs unless the user explicitly asks to productize the customer mobile app.
- When revisited, start from a fresh plan that reads current Flyer schemas, account/quota code, cf-router channel assumptions, and any cockpit P2-6 docs that exist at that time.
- First implementation slice should be discovery/spec only: customer channel model, account linking, entitlement sync, and delivery routing. Do not start with React Native UI until the channel/account contract is reviewed.

## Deferred Items

- Decide whether mobile app sign-in requires phone OTP first or supports email-first pending accounts.
- Decide whether canonical conversation history is required for WhatsApp history backfill or only for app-channel messages going forward.
- Investigate Apple/Google subscription receipt validation and push notification provider choices before estimating code.
