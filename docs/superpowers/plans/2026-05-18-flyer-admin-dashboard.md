**Drift-check tag:** extends-Hermes

# Flyer Studio Admin Dashboard Plan

**New primitives introduced:** one authenticated cockpit section, one Flyer admin API router, and narrowly scoped audited operator mutations.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp campaign delivery | Existing Hermes bridge plus `send-flyer-campaign` script | Use it; dashboard invokes the existing sender path rather than reimplementing WhatsApp delivery. |
| Flyer customer/project state | Existing Flyer JSON stores and Pydantic schemas | Use them with `safe_io` locks/backups. |
| Customer support/admin UI | No deployed Hermes skill for Flyer Studio operator cockpit | Build inside existing cockpit. |
| CSV ingestion | Existing cockpit roster CSV guard pattern | Reuse and adapt for campaign target import. |
| Audit trail | Existing cockpit audit log | Use it for every dashboard mutation. |

Awesome-Hermes ecosystem verdict: no ready-made Flyer Studio admin dashboard primitive applies; this is an operator surface over existing Hermes/Flyer primitives.

## Build scope

- Add backend `/flyer` routes for summary, customer search/detail, project search, guest-order/one-time user visibility, campaign CSV preview, dry-run/send campaign actions, and trial quota reset/extension.
- Add safe state helpers for Flyer stores: load under lock, write with validation, create timestamped backups before mutation.
- Add campaign target validation: CSV upload or pasted phone list, formula-injection protection, E.164 normalization, duplicate detection, and paid-customer/suppression warnings.
- Add frontend `Flyer Studio` cockpit tab with Overview, Customers, Campaigns, Projects, and Guest Orders panels.
- Require explicit operator reason for state-changing actions and audit them.

## Acceptance checks

- Backend tests cover segment counts, CSV validation, quota reset, trial extension, and campaign dry-run behavior.
- Frontend build succeeds and the new section appears in desktop and mobile nav.
- Existing Flyer focused tests remain green enough to protect state-schema compatibility.
- Deploy cockpit to `main-vps` and verify `/health` plus live Flyer summary endpoint.
