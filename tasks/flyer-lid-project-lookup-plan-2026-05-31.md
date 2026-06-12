**Drift-check tag:** extends-Hermes

# Flyer LID-Only Project Lookup

## New primitives introduced

- No new substrate. This removes phone-only early returns so existing Flyer account lookup can use `primary_chat_id` for LID-only WhatsApp senders.

## Hermes-first analysis

Hermes already owns sender identity, WhatsApp ingress, LID parsing, and the `identify-sender` fallback. Flyer code already owns customer/project account matching through `find_flyer_customer_by_sender()` and `_flyer_account_phones()`.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Sender identity / LID resolution | Existing Hermes `identify-sender` substrate | Reuse existing `chat_id` and customer `primary_chat_id`; no new identity substrate. |
| Project ownership lookup | Existing Flyer account/project selectors | Extend selectors to call existing account-phone resolver even when inbound phone is absent. |
| Customer messaging | Existing deterministic status/revision copy | No copy change. |

Awesome Hermes Agent ecosystem check: no external Hermes skill is needed for this local ownership-selector bug.

## Drift check

- `find_flyer_customer_by_sender(phone, chat_id)` already supports `phone=None` when `primary_chat_id == chat_id`.
- `_flyer_account_phones(phone, chat_id)` already pulls public/business/onboarded/authorized phone numbers from the resolved customer.
- `find_active_flyer_project_by_sender`, `find_flyer_project_by_id_for_sender`, and `find_latest_flyer_project_for_status_by_sender` currently return before that resolver when `phone` is missing.

## Plan

- [x] Add RED regression for LID-only active/latest/id project lookup.
- [x] Remove phone-only early returns while preserving missing project file / missing project id guards.
- [x] Run focused routing/project tests.
- [x] Multi-vector review.
- [ ] Full verification, PR, merge, deploy.

## Review notes

- Structural/ownership review: no blocking issues. Confirmed `_flyer_account_phones()` remains the ownership source, duplicate `primary_chat_id` matches return none through existing customer lookup, and missing identity/project-file/project-id edge cases remain closed.
- Hermes/drift/customer-routing review: no blocking issues. Confirmed no new identity substrate, active/status selectors remain distinct, and exact lookup still requires `row.customer_phone in account_phones`.
- Local Claude review timed out without findings; not used as evidence.
