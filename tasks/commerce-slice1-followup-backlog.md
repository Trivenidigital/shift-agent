# Commerce Slice 1 — non-blocking follow-up backlog

**Drift-check tag:** `extends-Hermes`

Captured from Phase G PR reviewer findings (2026-05-28). All BLOCKERs, HIGHs, and most MEDIUMs landed in PR #321 with code changes. Items below are non-blocking deferrals — pick up in slice 2 or a dedicated follow-up PR.

## Deferred LOWs (Reviewer A)

- **LOW-3 — `mark_attempted` doesn't update intent.status**: by design (event-only, not state). Docstring already implies it; consider adding an explicit comment "this is event-only; status stays 'minted' until mark_sent" to prevent a future "fix" that adds a status transition.
- **LOW-4 — `CommerceOrder.payment_intent_id` / `payment_reference` default to `""` not `Optional[str] = None`**: cosmetic divergence from Flyer precedent. Migrate during a schema-touch PR; not worth a standalone change.
- **LOW-5 — `CommerceCart` model-level validator (phone-or-lid) has no direct unit test**: the new `test_commerce_cart_requires_sender_identity` in `test_commerce_logentry_variants.py` covers this. Considered addressed.

## Deferred LOWs (Reviewer B)

- **LOW-1 — `CommerceOwnerApprovalThresholdUnconfigured` exists but is unused in slice 1**: defined for slice-2 caller wiring (catering deposit will be the first caller). Acceptable scaffolding. Add a `commerce.payment_link.assert_approval_threshold_configured(cfg)` helper in the slice-2 PR so callers cannot forget to call it.

## Slice 2 design tasks (carried forward from PRD v2 §12)

- Real provider integration: Stripe / Razorpay / UPI / Zelle / Cash App (operator-choice)
- Webhook receiver daemon + HMAC signature verification + currency-mismatch handling
- Catering deposit caller wiring (Catering Agent #2 `send_deposit_link` Phase 2 skill)
- Cockpit Commerce view (extends existing Flyer-Admin pattern)
- `commerce_catalog` primitive (gated on first-customer flow with active Inventory #6)
- Dispatcher matrix amendment (gated on first customer flow with no other owning agent)
- Tax/fee calculation module (slice 1: `tax_cents=0, fee_cents=0`)
- Decimal-based fractional quantities + explicit rounding-mode policy
- `last_inbound_at` primitive-side population for 24h Meta window gating
- FDA-import-flagged category handling
- `commerce.payment_link.assert_approval_threshold_configured(cfg)` helper (Reviewer B LOW-1)
- `safe_io.flock_state_path(path)` context manager for webhook concurrency (Reviewer A MEDIUM-1)

## Test-shape improvements

- Migrate `test_refused_category_is_idempotent_on_retry` if a future dedup PR lands (currently pins "2 refusals on retry" — change-detection assertion).
- Add an `commerce.payment_link.assert_payment_url_renderable` lint scan to slice 2 caller PRs.

## Compliance signoff (gating slice 2)

- Operator must verify the 2026 Meta WhatsApp Commerce Policy clause that applies to conversational ordering of restricted animal-product categories (raw meat).
- Operator must sign off on the full compliance matrix in `tasks/hermes-commerce-prd-v2.md §6` before any caller wiring lands.
