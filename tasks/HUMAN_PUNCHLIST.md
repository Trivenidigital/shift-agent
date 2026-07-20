# HUMAN_PUNCHLIST — items code cannot fix (2026-07-20)

Everything below requires the OPERATOR (credentials, real phones, money decisions,
product rulings). Each entry: what, why, and the exact next action.

## P1 — verification gates on already-shipped work

1. **Pushover un-mute** — THE final verification gate for the entire alarm layer
   shipped 2026-07-11 (daily-brief false-page fix, dead-letter reader, watchdogs)
   AND the delivery channel for any future `capture_failed`/collision alert.
   Muted since 2026-05-01; pre-mute key unrecoverable on-box. ACTION: provision
   Pushover user+app keys, then execute `docs/runbooks/pushover-unmute-runbook.md`
   (prepared 2026-07-20; execution needs separate reviewer authorization).
2. **R2A first-live-outcome canary — AUTHORIZED, pending execution.** The approved
   contract: owner's established dogfood number → existing non-terminal catering
   lead → exactly ONE headcount or food-requirement amendment → normal WhatsApp
   ingress → no date-only message, no second message, no manufactured replay, no
   new lead, no script injection, no data edit. Outcomes: clean `captured` clears
   the live-path gate; `not_applicable` does not clear it; `capture_failed` pauses
   R2B review (metadata-only investigation). Feasibility note on record
   (2026-07-20): the sender identity is also flyer customer CUST0001 with
   non-delivered flyer projects, so the routing ladder may yield `not_applicable`
   on this message — if so, that recorded outcome is reported as-is, without retry.
   ACTION: operator sends the single message; session records metadata-only
   before/after evidence.
3. **Shift coverage rehearsal** — the coverage engine has never completed a live
   cycle (all May dry-runs). Needs real WhatsApp sends from operator/employee
   phones; sessions are barred from send-tests. ACTION: schedule one real
   sick-call → proposal → approve → candidate-send rehearsal.

## P2 — money-path activations (each gated on operator config + dry-run)

4. **Catering deposit** — deposit path never executed; `deposit_pct=0` (safe).
   Before the first real ≥50-guest lead: set
   `commerce.payment_checkout_url_template`, run the deposit dry-run, re-arm
   deposit_pct. (census C3)
5. **Stripe onboarding** — activates the commerce surface: webhook subscribe +
   STRIPE_WEBHOOK_SECRET + provider=stripe + livemode expectation; the dormant
   owner-approval gate (threshold field) must be WIRED at that time (design
   contract preserved in docs/runbooks/commerce-stripe-onboarding.md + the
   schemas.py reserved-variants comment; Phase-3 item D ruling).
6. **QBO real client** — expense bookkeeper ships mock-only (RealQBOClient raises;
   fail-closed). At customer onboarding: Intuit credentials + real client work.

## P3 — product rulings pending

7. **Phase-3 rulings** (evidence + recommendations in the production-status matrix
   appendix, 2026-07-20): A `compliance_dispatcher` remove/deprecate ·
   B `multi_location_query` shelve + not-wired marker (wiring requires code-enforced
   location scoping — privacy) · C `dead_man_alert.txt` remove (contract served by
   notify-owner + proposal-sweep) · D commerce approval gate defer-to-Stripe.
   ACTION: confirm/override each; then a small implementation PR executes them.
8. **Catering canonical finalize path** (census C4) — option-picker vs auto-default
   basket; decide before real catering traffic.
9. **B1 shadow-LLM privacy ruling** — shadow egresses message text to OpenRouter;
   fine for operator's own numbers; requires an explicit ruling BEFORE any
   real-customer scope widening. Also: promotion gates (n≥30/family) are months
   away at current traffic — decide whether to keep accumulating or revisit.
10. **Dispatcher catering status-filter prose drift** (tasks/DEFERRED.md) —
    doc-only reconciliation PR when convenient; behavioral authority = deployed code.

## P4 — client/business facts (per-customer, at onboarding)

11. Per-customer config: locations list (multi_location), roster location_id
    population (required before ever wiring multi_location_query), compliance
    items, chart-of-accounts mapping (expense), supplier roster.
12. WhatsApp Business API / BSP decision — Hermes 0.14 pin blocks the 0.17 upgrade
    path; the port-or-official-API decision (2026-06-27 memory) remains open and
    gates any Hermes upgrade.
