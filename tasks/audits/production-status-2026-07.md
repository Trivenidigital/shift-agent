# Production-Status Matrix — Phase 0 of Production-Readiness Sprint (2026-07-18)

**Status:** Phase 0 COMPLETE — read-only. Awaiting operator approval of tier
assignments + Phase 1/2 scope before any test or fix is written.
**Plan:** `tasks/production-readiness-sprint-plan.md` (approved 2026-07-18).
**Base:** origin/main `6426eb6` (#621, deployed `deploy-20260718-022124-6426eb6a`).
**Method:** delta on the feature-liveness census 2026-07-11 (verdicts carried forward,
post-census merges #603–#621 folded in) + two parallel read-only sweeps (long-tail
12 agents; shift/brief/eod/expense/commerce) + lead verification of every load-bearing
claim (eod bug confirmed by direct read; balance-timer discrepancy resolved on box:
enabled+active, fired daily — census was right, sweep claim wrong).

## Verdict legend

READY (green at its tier) · READY-DORMANT (green, flag-off by design; activation
trigger documented) · NEEDS-FIX (specific defect/gap named) · UNTESTED (no behavioral
coverage) · DEPRECATE-CANDIDATE (recommend shelve/delete; operator sign-off required).

---

## TIER A — money / external-state / customer-visible sends

| Agent / surface | Dispatch | External actions | Audit | Approval gate | Send mechanism | Tests | Verdict |
|---|---|---|---|---|---|---|---|
| **flyer** (3 skills + cf-router spine) | dispatcher row + ~20 cf-router intercepts (live pilot, `*` wildcard graduated) | customer flyer delivery, WhatsApp | full (683+ intercepts, QA/referee/twin-QA fail-closed) | owner-review path on managed; outbound lint + front-brain screen armed | screened: fact-safety QA + visible-contract + outbound lint | 115+ files; E2E audit + remediation deployed 2026-07-18 | **READY** (reference standard; pilot-scoped by design) |
| **catering** (8 skills + scripts) | dispatcher rows + cf-router F7 primary | customer quotes/proposals, owner cards, menu writes | full chain (lead→proposal→approve→finalize all audited) | `#XXXXX` owner approval on send-to-customer; $3/guest floor; truth-guard | templates + script-rendered (validated item names); no free LLM to customer | 28 files incl. proposal suite | **NEEDS-FIX (unproven)** — full chain ran end-to-end exactly once, dogfood-only (census C1); deposit path never executed (C3); dual finalize paths need canonical ruling (C4); `create-catering-proposal-options` missing enabled-gate (census note) |
| **shift coverage engine** (5 skills + 6 scripts) | dispatcher rows + cf-router F9 sick-call fast-path | employee acks, candidate coverage sends, owner cards | strong on engine (proposal_created/status_change/Outbound* full set) — gaps below | candidate send HARD-gated on owner `#XXXXX` (verified: refuses unless status==approved) | templates for candidate+owner cards; free-LLM employee acks; f-string acks on script path | engine scripts well-tested (e2e lifecycle, sweep, reconcile, chokepoint); SKILL prose + roster_lookup untested | **NEEDS-FIX** — (a) sick-call script's own sends have no per-send audit rows; (b) owner-notify chokepoint writes NO row on successful delivery (most owner alerts unaudited); (c) live cycle never run (S3 rehearsal = operator-owned); (d) `dead_man_alert.txt` orphaned (no renderer) |
| **eod_reconcile** | timer 15-min self-gated 22:00 | owner Pushover only | eod_snapshot / eod_pushover_sent / eod_skipped | none needed (owner-only, no money) | deterministic in-script f-string | Linux-only script tests; iteration path untested | **NEEDS-FIX (verified bug)** — `eod-reconcile:128` iterates `pending.proposals` keys not `.values()`; any non-empty pending store → AttributeError → bare-except → counters zeroed + degraded flag. Same bug fixed on daily-brief side (send-daily-brief:312) but not here. Also: failed Pushover writes no audit row |
| **expense_bookkeeper** (3 skills + 3 scripts) | dispatcher rows, flag-gated (`enabled=False`) | vision API calls; MOCK QBO only (RealQBOClient raises — verified); owner sends | extensive (receipt→extraction→approval→push/undo all audited) — 4 inline replies unaudited | exemplary: `#XXXXX` + exact-amount echo + $50 force-gate + dedup force-gate + 24h undo window | templates for cards; free-LLM owner nudges; ~8 inline f-string replies | apply-decision extensive; dispatcher routing + prune script UNTESTED; inline sends untested | **READY-DORMANT** (activation = onboarding + real QBO client) with named gaps: prune-and-expire has zero tests incl. its §12b expiry alert; 4 unaudited inline sends |
| **commerce surface** (webhook skill + 5 scripts + 4 modules) | Stripe webhook (unwired) + deploy gates | signature-verified webhook; state flips; customer confirmation send | commerce_* rows via audit chokepoint; mark_confirmed emits no row itself | **none on mint/confirm** — approval machinery (threshold field, awaiting_approval status, LogEntry variants) EXISTS in schema but is UNWIRED; mint is threshold-gated only | deterministic f-strings, no LLM | webhook/livemode/link scripts tested | **READY-DORMANT** — fail-closed posture verified (no caller passes provider="stripe"; real mint unreachable today). LATENT GAP recorded: wire the owner-approval gate before the stripe provider is ever enabled |

## TIER B — owner/customer-deliverable content, no money

| Agent / surface | Dispatch | External actions | Send mechanism | Tests | Verdict |
|---|---|---|---|---|---|
| **daily_brief** | timer 15-min self-gated 07:00 | owner self-chat WhatsApp + Pushover alerts | body = fixed template (`daily_brief.txt`), no LLM; alert bodies f-string | good suite; E2E Linux-only | **READY** — healthy + sent daily; Pushover un-mute remains the operator-owned final verification gate (census) |
| **compliance** (owner_query + cron sender) | dispatcher regex row (owner+flag) + daily 06:00 timer | owner reminders; mark-done mutates state | template-rendered reminders; bounded-format LLM replies | 67 tests (behavioral + schema) | **READY** — most-hardened long-tail surface (3-layer idempotency, fail-closed, heartbeat). Accepted deviation: mark-done uses owner-role + phrasing, not `#XXXXX` (reversible, owner-initiated) |
| **multi_location / customer_location_query** | dispatcher regex row (unknown sender, gated on locations) | sends store list to unknown customers; geocodes their typed address via Nominatim | bounded format from deterministic script output; PII-disciplined (no address logged, coords rounded) | 39 tests | **READY** — note recorded: external geocode on unauthenticated input is accepted for a store-locator; OSRM stubbed (Haversine only) |

## TIER C — internal utility / dormant stubs

| Skill | State | Tests | Verdict |
|---|---|---|---|
| roster_lookup (shift) | read-only, in-process | none behavioral | **UNTESTED** — smoke test wanted (Tier A engine depends on it) |
| cash_ar, employee_docs, hiring, inventory, sales_tax, supplier, vip (7 stubs) | `enabled=False` self-decline, no dispatch row | config-defaults only | **READY-DORMANT** (roadmap placeholders per census B4 ruling; each needs owner + activation trigger at onboarding) |
| equipment_maintenance, pnl_anomaly (2 stubs) | same, but DO write `*_declined` audit on decline | schema-only | **READY-DORMANT** (the 2 best-behaved stubs) |
| catering_followup | stub; its Agent#2→CLOSED trigger hook was never wired | schema-only | **READY-DORMANT**; note: SKILL prose promises an auto-thank-you no wiring implements |
| **compliance_dispatcher** (stub) | superseded by compliance_owner_query + cron; NO route | none | **DEPRECATE-CANDIDATE** — dead weight, two-skills-one-wired |
| **multi_location_query** (owner cross-location) | **ORPHANED — no dispatch row**; owner queries fall through to handle_owner_command | schema only | **DEPRECATE-CANDIDATE or WIRE** (operator call) — if wired, its DEGRADED MODE (returns ALL employees when roster lacks location_id) must become code-enforced, not prose |

---

## New findings this Phase 0 (beyond census carry-forward)

1. **[VERIFIED BUG] eod-reconcile dict-keys iteration** (`src/agents/eod_reconcile/scripts/eod-reconcile:128`) — see Tier A row. Smallest fix in the sprint; twin of an already-fixed bug.
2. **Owner-notify chokepoint is audit-silent on success** (`shift-agent-notify-owner`): every owner alert funnels through it; successful delivery writes no decisions.log row, so "no alert rows" is ambiguous between delivered-fine and never-fired (§12b dispatched/delivered-pair rule violated at the fleet's central alert chokepoint).
3. **Unaudited send inventory** (external sends with no per-send audit row): handle-shift-sick-call employee-ack + owner-proposal; 4 of ~8 apply-expense-decision inline replies; eod failed-Pushover; daily-brief Pushover alert bodies; commerce customer confirmation.
4. **Free-form-LLM send inventory** (claims-screen relevance): employee/candidate acks (sick-call, candidate-response), owner nudges (owner_command, expense dispatcher, receipt-photo errors). None are customer-facing business-claim surfaces; all customer-facing sends fleet-wide are template/script-bounded or flyer-screened. **Claims-gate conclusion: no unscreened free-text path to customers exists today** (the census's "unlinted LLM replies to strangers on unmatched messages" exposure is now governed by the front-brain outbound lint, pilot-scoped).
5. **Commerce approval machinery unwired** (latent, dormant — see Tier A row).
6. **Stub decline-audit inconsistency**: 8 of 10 stubs self-decline silently; equipment_maintenance + pnl_anomaly write `*_declined` rows. Zero-LOC fix (SKILL prose).
7. **Orphans:** multi_location_query (no route), compliance_dispatcher stub, `dead_man_alert.txt` (no renderer).
8. **No-test list:** roster_lookup, expense dispatcher routing, prune-and-expire-expenses (incl. its §12b alert), commerce `mark_confirmed` caller-audit, eod non-empty-pending path.
9. **Windows dev-box blind spot:** daily-brief/eod/catering-script E2E tests are Linux-only skipif — CI is the real gate (already proven by the July batch incident; noted, not a defect).

## Census carry-forward blockers (unchanged status, operator-owned)

- **Pushover un-mute** = final verification gate for the (already-deployed) alarm-layer fixes; until keys are provisioned and one full daily cycle observed, deployed ≠ pilot-ready on the alert path.
- **Shift coverage rehearsal** (real sends; standing no-send rule bars sessions).
- **Catering deposit dry-run + `commerce.payment_checkout_url_template`** before first real ≥50-guest lead; **C4 canonical-finalize-path ruling**.
- **B1 shadow-LLM promotion**: data-gated (n≥30/family, ≥95% agreement); privacy ruling required before any real-customer scope widening.

## Proposed Phase 1/2 scope (needs your approval — nothing started)

**Fix batch (product code, within the ≤150 LOC cap):** F0-1 eod `.values()` bug + regression test; F0-2 owner-notify success-row (`*_alert_dispatched/_delivered` pair at the chokepoint); F0-3 sick-call script per-send audit rows; F0-4 expense inline-reply audit rows (4 sites). All through existing `ndjson_append`/LogEntry patterns.
**Test batch (Phase 1):** shared fixtures (tmp-path-only per safe_io guard); structural tests for Tier A/B gaps named above (expense dispatcher routing, prune-and-expire, roster_lookup smoke, eod non-empty-pending, commerce mark_confirmed).
**Prose batch (0 LOC):** stub decline-audit lines in 8 SKILL.mds; catering_followup trigger-hook honesty note.
**Operator decisions requested:** (1) compliance_dispatcher stub — deprecate? (2) multi_location_query — wire (with code-enforced location scoping) or shelve? (3) dead_man_alert.txt — delete or wire? (4) commerce owner-approval gate — wire now (dormant) or defer to Stripe-onboarding with a blocking note in the deploy gate?

## Approvals log

- 2026-07-18: operator "go" on sprint plan (session dd4a8de7) — authorized Phase 0 (read-only). This matrix is its deliverable. Phases 1–4: PENDING approval of the scope above.

---

# FINAL SPRINT UPDATE (2026-07-20) — Phase 4 closeout

**Shipped + LIVE since the Phase-0 matrix (all merged AND deployed; production =
`69195c5`, deploy-20260719-223246-69195c5b):**
- #622 sprint fix batch: eod counting fix, §12b owner-alert dispatched/delivered
  rows, sick-call + expense send audits, stub decline prose, 24 tests.
- #623 PR-R1 routing invariants: approval-code pool kernel (canonical order, fail-
  closed collision refusal, atomic scan-and-commit across all 4 generators),
  catering canonical-identity fallback.
- #624 canonical-lock deploy initialization (dual-identity fd-verified).
- #625 PR-R2A immutable Branch-B amendment capture (sidecar; four proven outcomes).

**Verdict changes vs Phase 0:**
- eod_reconcile: NEEDS-FIX → **READY** (bug fixed + regression-tested + live).
- shift: audit-gap items (a)/(b) CLOSED (sick-call send rows + owner-notify
  dispatched/delivered pair live); remaining NEEDS-FIX driver = the operator-owned
  coverage rehearsal only.
- expense_bookkeeper: READY-DORMANT, gaps narrowed (inline replies audited; prune
  lifecycle + §12b alert now tested).
- catering: NEEDS-FIX (unproven) → NEEDS-FIX (unproven) but the amendment DATA-LOSS
  half is closed (R2A live); C3/C4 remain operator-owned; P1-1 flyer-swallow and
  duplicate-lead modes remain OPEN pending R2B.
- Fleet-wide: code-pool collision class closed at generation AND lookup; owner-alert
  chokepoint no longer audit-silent.

**Deliverables:** `tasks/DEPLOY_CHECKLIST.md` + `tasks/HUMAN_PUNCHLIST.md` written
2026-07-20 (the sprint's three-file operator interface, with this matrix). Deferred
debt: `tasks/DEFERRED.md`.

## Phase-3 decision appendix (reviewer-mandated evidence; full detail in session
## evidence report 2026-07-20 — all four verified at HEAD 69195c5)

**A — compliance_dispatcher stub → REMOVE (or formally deprecate).** References:
the SKILL itself, the skills-manifest hash line, audit docs only. Production
reachability ZERO — dispatcher matrix routes compliance to compliance_owner_query
(SKILL.md:101); live path = owner_query + check-compliance-deadlines cron. Wiring
would duplicate the live path. Removal cost LOW: delete SKILL dir + regenerate
tools/skills-manifest.txt (deploy gate otherwise fail-closes). Files: the SKILL dir,
skills-manifest.txt.

**B — multi_location_query → SHELVE + explicit NOT-WIRED marker.** References: the
SKILL, manifest line, credential_readiness.py capability entry, sibling-SKILL prose,
CrossLocationQuery schema. Reachability ZERO (no dispatch row; owner text falls to
handle_owner_command). Promise: owner cross-location queries — but its DEGRADED MODE
(roster without location_id → returns ALL employees) is prose-only; safe wiring
REQUIRES code-enforced location scoping + roster location_id population + a
configured multi-location customer (none exist). Shelve cost LOW (marker edit +
manifest regen). Files: the SKILL.md (marker), skills-manifest.txt; wiring would add
a dispatcher row + scoping helper.

**C — dead_man_alert.txt → REMOVE.** Zero functional references (only audit docs);
render-coverage-template CAN render it but no caller ever passes it; the
health-alert/dead-man contract it promises is ALREADY served by
shift-agent-notify-owner (health-check:151, health-watchdog:33) and
shift-agent-proposal-sweep. Not in the skills manifest → no gate impact. Files:
delete the template only.

**D — commerce owner-approval gate → DEFER to Stripe onboarding; preserve contract
in docs.** The machinery is scaffolded end-to-end (threshold field schemas.py:2598,
awaiting_approval status, 2 reserved LogEntry variants schemas.py:6409/6415 +
union :6782, exception, LEGAL_TRANSITIONS edges) and invoked by ZERO code — dormant
by design ("operator-only paths land in slice 2+", order_state.py:7-8). Do NOT
remove (money-path schema churn). Preserve: paragraph in
docs/runbooks/commerce-stripe-onboarding.md naming the activation trigger (provider
live + operator sets the threshold) + the existing reserved-variants comment.
Files: the runbook (docs paragraph only).

**Execution of A-D: NOT performed — awaiting operator confirmation of the rulings
(HUMAN_PUNCHLIST item 7); then one small implementation PR.**

## Approvals log (additions)
- 2026-07-20: reviewer authorized docs-only R2B plan + Phase-4 docs + Phase-3
  evidence appendix + Pushover runbook (prepare-only) + a controlled R2A canary
  (blocked on feasibility finding; reviewer ruling pending). This update + the two
  deliverable files are that documentation work. No implementation performed.
