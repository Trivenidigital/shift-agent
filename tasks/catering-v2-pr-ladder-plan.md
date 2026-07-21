# Catering Agent v2 — PR ladder plan (post-audit, reviewer-ruled)

**Drift-check tag:** `extends-Hermes` — Hermes owns conversation/classification
(dispatcher SKILL + creative_catering_proposals); custom code is confined to
deterministic seams: routing escape, identity comparison, ledger, calculator,
config validation. No state machine, no new approval system, no orchestration.

**Status:** PR-A GO (reviewer 2026-07-21). PR-B..E staged behind it.
**Audit basis:** Phase 0 audit (this session) — brief's transcript-reconstruction
premise falsified; substrate corrections accepted (WhatsApp #-codes not Telegram,
per-VPS JSON not Honcho, systemd timers not Hermes cron, durable state exists).

## Hermes-first capability checklist

| Step | Tag / evidence | net-new LOC |
|---|---|---|
| 1. Classify inquiry vs follow-up vs proposal-request conversationally | `[Hermes]` — `catering_dispatcher` SKILL "SECOND — classify path" already routes proposal-request | 0 |
| 2. Extract structured inquiry fields (headcount/date/type, incl. images) | `[Hermes]` — `parse_catering_inquiry` vision+text extraction | 0 |
| 3. Compose 2 distinct menu-grounded proposal options | `[Hermes]` — `creative_catering_proposals` (catalog-only, price-free contract) | 0 |
| 4. Owner approval of quotes/amendments | `[Hermes]` — `#XXXXX` codes + `apply-catering-owner-decision` (approve/reject/edit); PR-B only adds version ref to the card | ~20 |
| 5. Owner menu updates (photo → structured) | `[Hermes]` — `parse-menu-photo` + `update_catering_menu` + `apply-menu-update` | 0 |
| 6. Customer selection + finalize | `[Hermes]` — `select-catering-proposal` + `finalize-catering-menu` | 0 |
| 7. F7 proposal-request escape + fresh-vs-stale discriminator | `[net-new]` — cf-router deterministic pre-LLM seam; Hermes never sees suppressed messages, so the escape must live in the intercept | ~150 |
| 8. Lead TTL / stale-lead expiry | `[net-new]` — mirror the `proposal_sweep.py` pattern (pure duck-typed helper + legal-terminal status via chokepoint + owner alert); first VERIFY whether the flag-OFF owner-approval expiry sweep (PRs #589-592 batch) already covers catering leads and extend it if so | ~80 |
| 9. Retained immutable quote versions + deterministic diff | `[net-new]` — R2A-sidecar-shaped ledger; no Hermes/community skill covers durable money audit | ~200 |
| 10. Deterministic price calculator + 5-field commercial config | `[net-new]` — LLM must never do money arithmetic; catalog exists, calculator doesn't | ~250 |

Red-flag check: 6/10 steps `[Hermes]`, net-new confined to deterministic
money/routing/lifecycle seams — consistent with the scope-type declaration
("Hermes handles conversation ~60%; custom work is durable audit, reversibility,
structured invariants only"). Ecosystem check: no Hermes/awesome-hermes skill
covers quote ledgers or tenant commercial config; verdict — steps 7–10 are
custom by necessity.

## Drift-rule self-checks

- ✅ Read `src/platform/schemas.py` (CateringLead at 2216 — quote_text/quote_version/quote_total_usd/deposit block; CateringProposalOption/Set/Store at 2399–2447; CateringLeadStatus Literal at 502) before drafting the ledger/discriminator scope.
- ✅ Read `src/plugins/cf-router/hooks.py` (F7 primary follow-up capture + `f7_primary_followup_suppressed` at ~5525–5552; P1-1 escape-gate pattern at 4226) before drafting the PR-A escape.
- ✅ Read `src/agents/catering/skills/catering_dispatcher/SKILL.md` (proposal-request / proposal-selection classify rows) and `src/agents/catering/skills/creative_catering_proposals/SKILL.md` (price-free catalog-only contract) before scoping behavior changes.
- ✅ Read `src/platform/proposal_sweep.py` (find_stale_sent_proposals / find_expired_awaiting_proposals — SHIFT proposals, not catering; what transfers is the pattern: stdlib pure helper, legal-terminal transition via the update-status chokepoint, owner alert) before scoping the lead TTL.
- ✅ Read `src/agents/catering/deposit.py` (_should_mint_deposit kill-switch + BL-CATER-03 per-guest floor at 44–72; round-half-up cents math at 75–86) before scoping the commercial config and calculator.

## Ruled decisions (binding)

1. **Fresh-vs-stale rule (PR-A):** deterministic field comparison, not LLM vibes.
   Inquiry-shaped message (multi-signal catering classify, NO amendment phrasing)
   that CONTRADICTS the open lead on date / headcount / venue → auto-open NEW
   lead + one-line cross-reference note to the old one. Ambiguous (inquiry-shaped
   but no contradicting identity fields) → one-line clarification. Amendment-
   phrased messages (update/change/revise/instead/make-it) → existing R2A
   capture path UNCHANGED (pin the R2A canary text as regression).
2. **Lead TTL (PR-A):** fold into the lifecycle guard; verify-then-extend the
   existing dormant expiry-sweep machinery rather than parallel-building. Stale
   `AWAITING_OWNER_APPROVAL` leads auto-expire to a legal terminal status with
   §12b owner notification at the write site.
3. **INR (PR-C):** config field lands now, rendering deferred; `currency: INR`
   must FAIL LOUD (refuse start / hard warn) — never silently render through
   dollar-cents math.
4. **PR-B:** owner approval card MUST carry the quote version reference.
5. **PR-C:** price-status label on every price-bearing reply enforced by a
   grep-style acceptance test, not convention.
6. Keep the two added scenarios: guest-mix composition (90/30 veg split),
   stale-lead cell.

## Ladder

- **PR-A (GO):** F7 proposal-request escape + fresh-vs-stale discriminator +
  lead TTL. Pins the 2026-07-21 13:59 incident replay
  (`f7_primary_followup_suppressed` on L0017) as the mandatory test.
- **PR-B:** retained immutable quote versions (R2A-sidecar shape), deterministic
  diff, render-from-committed, version-bearing approval card.
- **PR-C:** deterministic calculator (exact / estimated / pending-validation),
  5-field commercial config + formatting helper, price-status labels.
- **PR-D:** SKILL.md behavior pass (clarify-before-quote, negotiate-with-scope,
  escalation triggers, dietary/allergy, max-2 follow-ups, courtesy conversion,
  confirmation artifact).
- **PR-E:** 12-scenario acceptance suite asserting against the ledger + the two
  added scenarios.

Cadence per rung: implement → independent verify → CI → review → gated merge →
local-validation-before-VPS (standing deploy pattern).
