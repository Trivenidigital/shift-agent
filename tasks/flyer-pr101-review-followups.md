# Flyer PR #101 Review Follow-ups

**Drift-check tag:** extends-Hermes

**New primitives introduced:** none. This is backlog only.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Flyer guest-order state | Existing Flyer Studio JSON state plus `safe_io` helpers | Reuse; no new storage primitive |
| Flyer audit events | Existing `cf-router` audit chokepoint | Reuse; add clearer reason labels later |
| LID/account identity | Existing `identify-sender`, Flyer customer profile, and cf-router lookup | Reuse; review threat model before redesign |
| Address parsing | Existing onboarding parser | Extend only if onboarding failures appear outside current customer states |

Awesome Hermes Agent ecosystem check: no external Hermes skill applies to these code-review cleanup items. Verdict: keep as local Flyer Studio hardening backlog.

## Review Items

- [ ] Tighten one-time guest activation fallback lookup.
  - Reviewer note: `activate_guest_order` can call `find_open_order_by_sender(sender_phone)` without `chat_id` when `order_id` is omitted.
  - Risk: cross-chat order match if the same sender has multiple open orders.
  - Proposed fix: require `chat_id` for no-`order_id` activation, or make the CLI pass `chat_id` and query by sender plus chat.

- [ ] Split active-customer-ready audit reason from onboarding audit.
  - Reviewer note: `_send_flyer_active_customer_ready` currently emits `reason="flyer_onboarding"`.
  - Risk: cockpit/audit queries conflate a normal active-account redirect with real onboarding.
  - Proposed fix: emit `reason="flyer_active_customer_ready"` and add a tiny routing/audit regression.

- [ ] Revisit LID backfill quota-reservation identity.
  - Reviewer note: `_try_flyer_active_project_intercept` can pass a customer-record phone to quota reservation when inbound sender is LID-only.
  - Risk: debatable in the current single-owner model, but it deserves a threat-model pass before broader multi-user rollout.
  - Proposed fix: decide whether reservations should bind to customer account id, resolved sender phone, or primary chat id.

- [ ] Broaden business-address abbreviation parsing only if live onboarding needs it.
  - Reviewer note: `_parse_business_address` recognizes the currently active Triveni states but not all US state abbreviations.
  - Risk: low today; could create false repair prompts for new states.
  - Proposed fix: replace the narrow state-abbrev list with a full US state abbreviation set when onboarding expands.
