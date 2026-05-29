# Commerce Slice-2 Catering Deposit Caller — follow-up backlog

**Drift-check tag:** `extends-Hermes`

Captured from PR #324 reviewer round 1 (2026-05-29). All BLOCKERs + HIGHs + most MEDIUMs applied in PR. Items below are non-blocking deferrals — pick up post-merge or in slice-2.5 / slice-3.

## Operator-runbook updates (B-HIGH-1, deferred to docs PR)

The new deposit hook requires the operator to configure `cfg.commerce.payment_checkout_url_template` before flipping `cfg.catering.deposit_pct > 0`. Without it, every qualifying lead gets the "Payment link is not configured yet" fallback copy (correct fail-closed behaviour, but uninformative for customers).

**Needed:**
- Add a section to `docs/runbooks/production-pilot-shift-catering-daily-brief.md` (or a new `docs/runbooks/commerce-deposit-onboarding.md`) covering:
  - How to set `cfg.commerce.payment_checkout_url_template` (manual provider link, e.g., Stripe Payment Link manual URL or Razorpay Hosted Payment Page)
  - Test fixtures the operator can use to validate the template renders correctly
  - The kill switch: `cfg.catering.deposit_pct=0` disables the entire hook
  - The audit-log signal: search for `catering_deposit_link_sent` rows with `url_status="unconfigured"` to detect missing config
- Update the deploy runbook section that mentions PR #321 to also reference #324.

## Cockpit Commerce view (carried from slice-1 followup backlog)

A "Deposit-pending leads" Cockpit tab would surface:
- Leads where `deposit_status="awaiting_payment"` (operator can ping the customer)
- Leads where `deposit_status="unconfigured"` (operator needs to fix the template)
- Leads where `catering_deposit_link_failed` rows appear (operator forensics)

Extends the existing Flyer-Admin pattern; no new dashboard infra.

## Catering follow-up agent #10 integration (B-MEDIUM-3, slice-2.5)

Agent #10 is currently a stub (`cfg.catering_followup.enabled=False` default). When it's promoted from stub to v0.2, it must:
- Read `lead.deposit_status` to know when a lead is awaiting deposit payment
- NOT pester the customer with "any update on your booking?" when deposit_status="awaiting_payment" (customer is waiting for THEMSELVES to pay)
- Optionally send a polite deposit reminder after 24h if still awaiting_payment
- After slice 3 lands, transition lead.status to CONFIRMED when deposit_status="paid"

## §12a freshness watchdog (A-MEDIUM-3 from slice-1 + slice-2 review)

New state files (commerce/carts, orders, payment_intents, payment_references) ship without freshness SLO + watchdog per §12a. Acceptable in slice 2 because:
- Catering deposits are the only writer
- Fire rate is low (1 mint per qualifying approval, ~weekly cadence at pilot)

In slice 3 (webhook receiver), fire rate becomes meaningful → add a watchdog at that PR.

## CateringLeadStatusChange emission for deposit transitions (A-MEDIUM-2, deferred)

Current design uses `catering_deposit_link_sent` as the canonical audit row for deposit transitions (no `catering_lead_status_change` emission because lead.status itself doesn't change). This is documented in the schema docstring but means operator-side dispatcher-accuracy and lead-history tools won't see the deposit-fields-landing event as a status change.

Alternative if reviewers later request: emit `CateringLeadStatusChange` with `from_status=to_status="SENT_TO_CUSTOMER"`, `actor="system"`, `reason="deposit_link_minted"` for audit completeness. Small schema docstring update + 1 emission line + 1 test.

## Per-lead deposit override (deferred indefinitely)

The current design uses `cfg.catering.deposit_pct` globally. A future enhancement could allow per-lead override via WhatsApp ("set deposit 30%"). Out of scope for slice 2; pick up only if operator demand.

## Audit-row `intent_mint_failed` Pushover escalation (B-MEDIUM-2 partial)

Slice 2 fix: on `intent_mint_failed` we now cancel the orphan order. Operator still gets journald-only signal. If pattern observed in production canary, escalate to Pushover P1 like `bridge_send_failed`.

## A-LOW-5 — tz-aware ts in `_emit_deposit_link_failed` fallback

`_emit_deposit_link_failed` falls back to `datetime.utcnow()` (naive) instead of `datetime.now(timezone.utc)`. Audit chokepoint accepts both but mixing them across the stream is a wart. Cosmetic.

## A-MEDIUM-5 — tz-aware in audit emit helper docstring

Add explicit comment near `_emit_deposit_link_failed` clarifying that the `now` default is `datetime.now(timezone.utc)` (not `utcnow()`).
