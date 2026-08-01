# Catering Business Studio MVP — Completion Record

**Drift-check tag:** `extends-Hermes` — deterministic seams (qualification, pricing,
lifecycle, controls, follow-ups, cockpit) on the existing Hermes substrate; Hermes
keeps conversation/classification. No new state-machine paradigm, no parallel
approval system.

**Date:** 2026-07-31 (superseded state below) · **PR:** #661

> **2026-08-01 UPDATE — production is now `d01c88a`** (deploy-20260801-201411-d01c88a6),
> which adds PR #664 (menu→pricebook adapter), #665 (legacy-pending fail-closed)
> and #667 (menu-caption routing precedence). The owner-menu ingestion workflow
> is BLOCKED at the agent turn — see
> `tasks/catering-menu-ingestion-resume-2026-08-01.md`, which is the
> authoritative resume document. Backlog items 14-20 below were added by that
> attempt.

**Original branch:** `feat/catering-studio-mvp`
**Base:** origin/main `dc7a81a2` (== deployed prod) + docs branch tip `50f9b83`
(incorporates reviewer-amendment `7345847`, exact SHA preserved).

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Conversation/classification | yes — F7 primary-mode cf-router + catering SKILLs | reuse unchanged |
| Vision/menu ingest | yes — parse-menu-photo | reuse unchanged |
| Approval codes | yes — approval_code_pools | reuse (new followup pool appended LAST) |
| Deterministic money math | none | custom (catering_pricing kernel) |
| Slot-filling qualification | none (F7 primary = LLM never sees inbound) | custom deterministic templates |
| Follow-up scheduling | none (stub self-declines) | custom owner-supervised engine |
| Owner web UI | existing cockpit (FastAPI+React) | extend with catering section |

Verdict: custom code confined to money/lifecycle/scheduling seams; scope-ops
reviewer confirmed no redundant primitive introduced.

## What shipped (81 commits)

- **M1** intake: qualification loop (max 3 rounds, 2–4 questions), widened
  deterministic extractor, `amend-catering-lead` (amendment application),
  QUALIFYING status.
- **M2** pricing: `CateringPricebook` (integer cents, INR fail-loud, placeholder
  flag), `catering_pricing.compute_quote` (pure, provenance-stamped),
  `import-catering-pricebook` + seed template, `catering_paths.py`.
- **M3** lifecycle: proposal-set transition table (enforced), validity/expiry
  (`catering-proposal-expiry-sweep`, flag OFF), deterministic acceptance →
  BOOKED, `mark-catering-lead-outcome`, owner `--discount-id`.
- **M4** controls: kill switch at bridge_* + gateway seam, automation-control on
  the gateway seam, owner pause verbs, takeover 72h auto-expiry (§12b-alerted
  post-fix), per-lead hold, `catering-control-status`.
- **M5** follow-ups: 6 types, owner-approval-card default, sweep + approve +
  create + status CLIs, installed-not-enabled timer, triple-gated OFF,
  graduation trigger documented ("10 clean owner-approved sends → `*`").
- **M6** Studio: cockpit catering section (backend router + 5 React panels),
  fresh-OTP-gated actions via shell allowlist, ledger-joined quote history.
- **M7** proof: 17-cell deterministic lifecycle E2E (`tests/test_catering_studio_e2e.py`,
  CI-collected + named send-path-ci step) + read-model companion in cockpit-ci.
- **Fix waves** (from 3-vector review): pricing provenance (`CateringPricingInputs`
  frozen at finalize; discount recomputes from committed cents), pending-review
  send gate, price-status label on every priced render, poison-amendment
  resilience, hold enforcement in the intake loop, claim-before-send +
  quiet-hours defer in follow-ups, env-gated F7 arms, chokepoint admission for
  all ten catering senders.

## Flag / dormancy inventory (behavior changes at next deploy)

| Flag | Default | Meaning |
|---|---|---|
| `CATERING_QUALIFICATION_GATE` | **ON** | F7 asks qualification questions instead of the F14 sample-menu stub. Env-revertible without deploy. **Only default-ON change.** |
| `CATERING_ACCEPTANCE_ARM` | OFF | acceptance detector arm (books leads) |
| `CATERING_AUTOMATION_CONTROL_ENABLED` + allowlist + STOP/TAKEOVER | OFF | PR#653 kernel (pre-existing) |
| `CATERING_FOLLOWUP_ENABLED` / `_ALLOWLIST` / `_AUTOSEND` | OFF/empty/OFF | follow-up engine |
| `CATERING_PROPOSAL_SWEEP_ENABLED` | OFF | proposal expiry sweep (no timer installed) |
| `catering_followup.enabled` (config) | false | trigger-site scheduling |
| Kill-switch chokepoint check | inert | fires only if operator ran `shift-agent-disable`; now affects ALL agents' sends, not 3 legacy scripts |
| `catering-mint-deposit` chokepoint admission | — | **money-path change**: send no longer refused at PR-ζ chokepoint; 12 upstream mint guards + unset template + deposit config still gate. Named per reviewer B2. |

## Rollback asymmetry (reviewer H1 — MUST READ before deploy)

The new code writes 10 additional fields + 2 new statuses onto every lead it
touches (flags cannot prevent this). A lead written post-deploy makes
`catering-leads.json` unreadable by the PREVIOUS tarball (`extra="forbid"` +
status Literals). **Tarball rollback is NOT a catering rollback once one lead is
written** — recovery is hand-editing the store or rolling forward. Pre-existing
one-way-door pattern (deposit_* did the same), widened here.

## Deferred backlog (MEDIUM/LOW, tracked)

1. G2-true-drop: wire automation-control into `_SHIFT_DROP_SEND` via
   patch-hermes.py (gateway seam currently substitutes a bounded template).
2. §12a: freshness watchdog for catering-followups.json — REQUIRED before
   arming `CATERING_FOLLOWUP_ENABLED`.
3. G5: owner takeover requires a pre-existing coded lead (pause inherits).
4. Followup `approved_sent` codes resolvable but not pool-reserved (L1).
5. `booked_this_month` counts CLOSED declines (CLOSED overload; use
   customer_acceptance to split).
6. `clean_free_text_answer("-80")` accepted as venue (isdigit vs signed).
7. Suppressed-reason column in Studio FollowupsPanel; suppressed-followup counter.
8. `send-daily-brief` has no BOOKED line; pattern-report doesn't count EXPIRED sets.
9. Security LOW-1/LOW-2: SKILL row role-forwarding note; role gates on 3 operator scripts.
10. `--dry-run` sweep still writes audit rows; MIN_QUESTIONS_PER_ROUND unused;
    `round_cap_reached` mislabel on exhausted question pool.
11. OutboundRefusedDisabled.proposal_id invariant relaxed (optional pattern).
12. G8 send-coverage-message local bridge_post bypass; G9 outbound idempotency
    convention-only; G12 harness draft-cap-50 derived-not-constant.

13. FLYER (main-inherited, needs a flyer session): cockpit
    test_flyer_admin manual-queue-complete cells broken by #621's src-side
    manual_queue.py changes (passed 2026-07-07, cockpit-ci never ran since);
    temporarily deselected in cockpit-ci.yml with a pointer here — fix the
    operator-text-manifest path routing in the flyer test/writer and remove
    the deselects.

### Added 2026-08-01 (menu-ingestion activation attempt)

14. **BLOCKER (open) — the agent turn never invokes `update_catering_menu`.**
    A correctly-ceded owner menu photo yields a conversational reply and
    `parse-menu-photo` never runs; `menu_update_proposed` count in the whole
    production audit log is 0. Front-brain CONVERSE was tested and DISPROVEN as
    the cause. Full state, evidence and the next probe:
    `tasks/catering-menu-ingestion-resume-2026-08-01.md`.
15. **HIGH — ungrounded operational-success claim.** The model told the owner
    *"successfully recorded"* with no receipt, no state change, no audit row;
    it passed the outbound screen twice (`verdict: passed`, no fallback). Fix as
    an invariant — a claimed operational success must be backed by a verified
    action result or durable receipt — NOT by blacklisting the phrase. Blocks
    restoring the owner chat to converse mode and blocks pilot activation.
16. **HIGH — zero per-turn agent observability.** The gateway journal carries
    only systemd session lines: no skill-selection or tool-call logging exists,
    so item 14 cannot be diagnosed from evidence. Add before (or alongside) the
    next attempt.
17. **MEDIUM — non-reproducible deploy artifact builder.**
    `tools/build-deploy-tarball.sh` embeds tar mtimes + per-run gzip state, so
    two builds of one commit differ (cost one deployment halt). Fix in its own
    narrow PR: `--sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner`,
    `gzip -n`. Until then: NEVER delete a deploy worktree before the artifact is
    installed; retain the exact built `.tgz`.
18. **MEDIUM — front-brain precedence for owner media SKILL triggers.** Was
    scoped as the durable fix while converse was the suspect; converse is now
    exonerated, so re-scope only if item 14's probe implicates the conversational
    tier. Do not build it speculatively.
19. **MEDIUM (pre-existing, surfaced live) — F7 proposal-request path fires on
    owner text.** An owner text message matched lead L0020 and deterministically
    generated + SENT 2 proposal options (`catering-proposals.json` mutated,
    recipient was the owner's own number). Behaviour predates this work; review
    whether an owner-authored message should be able to trigger a customer-shaped
    proposal send at all.
20. **LOW — owner-alert channel unproven.** Pushover credentials are valid but
    the account has NO ACTIVE DEVICE (trial lapsed unlicensed), so the API
    accepts and silently drops; the WhatsApp fallback therefore never fires and
    §12b pages are lost. Owner-waived 2026-08-01. One curl re-test closes it
    after a licence purchase.

## Phase 2 backlog (directive §12)

Deposits/payments (deposit machinery exists, dormant), contract acceptance,
proactive marketing, multi-location, CRM, profitability accounting, autonomous
pricing changes, unsupervised operation, analytics beyond pipeline counters,
role/permission systems, visual redesign. Plus: LLM-assisted intake (would
require relaxing F7 primary-mode — architectural decision).

## Human-gated items (unchanged by this work)

Stage A contained admission (same-person phone/LID proof + third-identity denial
probe outstanding), transport-budget enable, control-socket arming, any real
send, deploy + restart, pilot start. Progressive-edit evidence design frozen at
`50f9b83`; cap-50 remains NOT live-proven (per amendment, no such claim is made).
