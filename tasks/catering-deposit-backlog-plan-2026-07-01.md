# Catering Deposit Follow-up — Drift-Corrected Scoped Plan (2026-07-01)

**Drift-check tag:** `extends-Hermes`

**New primitives introduced:** none. Every remaining item reuses deployed
substrate (commerce slice-1 primitives, the `decisions.log` audit chokepoint,
JSON-on-disk state, the FlyerAdmin/CommerceOrders Cockpit pattern, the existing
table-freshness watchdog pattern).

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deposit link mint/send | Deployed `catering-mint-deposit` + commerce slice-1 primitives | Reuse; no new payment substrate |
| Deposit audit rows | Existing `decisions.log` chokepoint + `CateringDepositLink{Sent,Failed}` / `CateringDepositPaid` LogEntry variants | Reuse deployed audit stream |
| Deposit-pending Cockpit view | Existing `web/frontend/src/sections/CommerceOrders.tsx` + `FlyerAdmin.tsx` read-only admin pattern | Extend deployed dashboard; no new infra |
| Freshness watchdog | Existing table-freshness daemon / heartbeat pattern (§12a) | Reuse when fire-rate is meaningful |
| Follow-up agent #10 | Existing `catering_followup` stub (`cfg.catering_followup.enabled=False`, self-declines) | Promote in place; no new agent scaffold |

Awesome-Hermes-Agent ecosystem check: no turnkey deposit-lifecycle / payment-
reconciliation skill exists (verified against `tasks/skills-roadmap.md` gaps —
QBO/Stripe/DocuSign write are all confirmed net-new). The deposit path is repo-
native commerce work; reuse the deployed slice-1/2 substrate.

---

## §1 — Drift re-check vs the 2026-05-29 backlog

The follow-up backlog (`tasks/commerce-slice2-catering-deposit-followup-backlog.md`)
was captured from PR #324 reviewer round 1 on **2026-05-29**. Re-checking each
item against `origin/main` @ `48fe224` + deployed main-vps state (2026-07-01):

| Backlog item | 2026-05-29 status | 2026-07-01 actual state | Evidence |
|---|---|---|---|
| **B-HIGH-1** deposit-onboarding runbook | deferred to docs PR | ✅ **DONE** | `docs/runbooks/commerce-deposit-onboarding.md` covers template config (Step 2), test fixtures/smoke (Step 4), kill switch `deposit_pct=0` (Steps 3+7), `url_status="unconfigured"` audit signal (Step 5), and references PR #321/#324/#327 |
| **A-LOW-5** tz-aware ts in `_emit_deposit_link_failed` | open (naive `utcnow()`) | ✅ **DONE** | `catering-mint-deposit:126` uses `(now or datetime.now(timezone.utc))`; grep `utcnow` across `src/agents/catering/` = **empty** |
| **A-MEDIUM-5** tz-aware docstring comment | open | ✅ **moot** | Code is already tz-aware everywhere; no naive/aware mixing left to document |
| **Cockpit "Deposit-pending leads" tab** | carried from slice-1 | ⏸ **slice-3-gated** | `commerce-deposit-onboarding.md` lists it under the "What slice 3 will add" section; premature now (0 deposit rows, no `deposit_status` on any lead — see §2) |
| **Agent #10 deposit-awareness** (B-MEDIUM-3) | slice-2.5 | ⏸ **slice-3-gated + separate agent** | `catering_followup` is a self-declining stub (`enabled=False`); the CONFIRMED transition is defined as post-slice-3 in the backlog itself. Promoting it is cross-agent scope |
| **§12a freshness watchdog** (A-MEDIUM-3) | acceptable to defer | ⏸ **slice-3-gated (premature)** | `commerce-deposit-onboarding.md` ("What slice 3 will add") defers it to slice 3 "when fire rate becomes meaningful (webhook receiver)". At 0 rows / ~weekly-at-most pilot cadence there is no meaningful staleness threshold — a watchdog now would false-alarm continuously (see §3) |
| **A-MEDIUM-2** `CateringLeadStatusChange` for deposit transitions | deferred | ⚠ **deliberate design deferral — do not reverse autonomously** | `schemas.py:2085-2087` documents "NO `catering_lead_status_change` row is emitted … `catering_deposit_link_sent` IS the canonical audit row." Backlog says change only "if reviewers later request." Reversing a documented audit-semantics decision is a reviewer/operator judgment call, not a low-risk win |
| **Per-lead deposit override** | deferred indefinitely | ⏸ out of scope | Unchanged |
| **`intent_mint_failed` Pushover escalation** (B-MEDIUM-2 partial) | conditional on observed pattern | ⏸ **N/A** | 0 `catering_deposit_link_failed` rows in production → no pattern to escalate |

**Net:** the two lowest-risk items (B-HIGH-1 runbook, A-LOW-5 tz-aware fix) are
already shipped. Everything else is either slice-3-gated, a deliberate design
deferral, or conditional on production signal that has not fired.

---

## §2 — Live runtime finding (operator decision, NOT a code change)

Read-only inspection of deployed `/opt/shift-agent/config.yaml` +
`decisions.log` + `catering-leads.json` on main-vps (2026-07-01):

```
catering.deposit_pct: 0.25          # deposit hook is ARMED (>0)
catering.deposit_threshold_guests: 50
commerce.payment_checkout_url_template: <unset → "">   # UNCONFIGURED
catering_deposit_link_* audit rows: 0                  # has NEVER fired
deposit_status on any lead: none set
```

The deposit hook is **fully wired and armed** (`deposit.py` at
`/opt/shift-agent/deposit.py`, `catering-mint-deposit` binary + apply-script hook
present from the Jun 30 deploy) but the checkout URL template is **unconfigured**.

**Consequence:** the next qualifying lead (headcount ≥ 50 AND `quote_total_usd > 0`
AND owner-approved AND quote sent) will mint a commerce order + payment intent and
send the customer the fail-closed copy `"Payment link is not configured yet. We'll
send it when it's ready."` (byte-exact, regression-locked in
`test_catering_deposit_copy_invariants.py`). It cannot crash or roll back the
quote-send (hook "NEVER raises; failures are non-fatal", `apply-catering-owner-
decision:901`).

**Severity: LOW-MEDIUM** — and worse than "confusing copy" because of the coupled
**unconfigured-send dead-end** (adversarial-review MEDIUM, confirmed first-hand):

- The unconfigured send *succeeds* at the bridge (customer gets the "not configured
  yet" promise), so `catering-mint-deposit` runs `mark_sent` and persists
  `deposit_payment_intent_id` + `deposit_status="unconfigured"` (`:362-379`).
- Re-invoke then short-circuits at `noop: already_minted` (`:184`). There is **no
  `--force`/`--remint` flag** and **no wired `unconfigured→awaiting_payment`
  transition**, so configuring the template later does NOT auto-deliver the real link.
- Net: every qualifying lead in the armed-but-unconfigured window becomes a **manual
  void+clear+remint** case, and the promise "we'll send it when it's ready" cannot be
  auto-kept.

**Fixed this pass (docs-only):** `docs/runbooks/commerce-deposit-onboarding.md` — the
Step 5/6 remediation ("re-invoke") was inaccurate for this case; corrected + added
Step 6a (manual remediation) + an ordering callout recommending kill-switch-first.

**Recommended operator action (out of scope for this branch — config/deploy):**
1. **If deposits are not ready:** set the kill switch `cfg.catering.deposit_pct: 0`
   (Step 7) **before** the next qualifying lead. **Strongly preferred** — it prevents
   creating any unfulfillable unconfigured intent. **or**
2. **If ready to accept deposits:** configure `cfg.commerce.payment_checkout_url_template`
   per Step 2 first, *then* leave `deposit_pct=0.25`.

Flagged per §12b (an armed automated customer-facing action the operator may not
realize is live). This branch does not change runtime config or deploy.

---

## §3 — Why no code slice is implemented in this pass

Per the operator's "implement a slice only if it is clearly low-risk AND
implementation-ready AND does not mix unrelated work", none of the residual items
qualifies right now:

- **Cockpit deposit-pending tab** — premature: there are 0 deposit rows and no
  `deposit_status` values to render. Building a tab over empty state, ahead of the
  slice-3 data model, invites rework. Runbook already scopes it to slice 3.
- **§12a freshness watchdog** — actively wrong now: §12a requires a defined
  expected write-rate + staleness threshold. Commerce deposit state legitimately
  has 0 writes for weeks at pilot cadence, so any threshold produces false
  staleness alerts. The correct §12a moment is slice-3's webhook receiver, when
  fire-rate becomes meaningful. Deferring is the §12a-aligned choice, not a gap.
- **Agent #10 deposit-awareness** — a different, currently-disabled agent
  (`catering_followup`, `enabled=False`) whose CONFIRMED transition is defined as
  post-slice-3. Promoting it is cross-agent scope creep (an explicit stop
  condition).
- **A-MEDIUM-2 audit emission** — reversing a documented design decision; reviewer/
  operator call, not autonomous.

Implementing any of these now would violate drift discipline (CLAUDE.md §7a
partial-match) or the "no cross-agent scope creep" rule. The disciplined outcome
is a clean readiness report + this drift-corrected plan, holding implementation
until slice 3 is greenlit.

---

## §3a — Residual code-level findings (from adversarial review; captured, not fixed this pass)

Surfaced by the two adversarial subagent reviews; each is a *code* change to the
deposit path, so held out of the docs-only PR and out of autonomous scope (money-
adjacent → operator sign-off + review). Small but real:

1. **`subprocess_timeout` is a silent-failure surface (§12b-adjacent).** The parent
   `apply-catering-owner-decision` `TimeoutExpired` handler logs the 30 s deposit-hook
   kill to **stderr/journald only** — it does **not** emit a `catering_deposit_link_failed`
   row, even though `"subprocess_timeout"` is a declared reason in the
   `CateringDepositLinkFailed` Literal (`schemas.py:5678`, currently a dead enum value).
   Worse, a timeout can land *after* a successful bridge POST but *before* the lead
   persists → a blind operator re-invoke can double-send + leave an un-voided intent.
   **Micro-slice (PR-OBS):** emit `catering_deposit_link_failed(reason="subprocess_timeout")`
   in the timeout handler (+ 1 test). Turns the gap into an audit row; makes the Step-5
   grep workflow honest. ~5 LOC + 1 test; still money-adjacent → review before ship.
2. **`extra="forbid"` on `CateringLead` is not rollback-safe (new→old).** A deposit-
   bearing lead read by an older binary would `ValidationError` (each lead in
   `leads[]` is strictly validated even though the store wrapper is `extra="ignore"`).
   Pre-existing pattern (same as `selected_items`/`quote_total_usd`); only matters if a
   post-deposit binary rollback is a real operational scenario. No action unless
   rollback-across-schema-versions becomes a live plan.

---

## §4 — Smallest-PR slice sequence for WHEN slice 3 is greenlit

Slice 3 = real payment provider integration + webhook receiver (needs operator
decisions on provider, credentials, signature scheme — see
`memory/project_commerce_primitives_decision.md`). Once greenlit, execute in this
order (each independently reviewable):

0. **PR-S3.0 — deposit remint/void operator tool (near-term-eligible, ahead of the
   rest).** Adds a guarded `catering-mint-deposit --remint <id>` (or a sibling
   `catering-deposit-void`) that voids the stale intent, clears the lead's
   `deposit_*` anchors, and re-mints against the now-configured template — turning
   Step 6a into one command and closing the unconfigured dead-end (§2). **Touches the
   money/commerce path → NOT autonomous;** requires operator sign-off + multi-vector
   review (money-flow + state-mutation + replay). This is the highest-value item for
   the *current* live risk and does not depend on the webhook.
1. **PR-S3.1 — webhook receiver + `deposit_status="paid"` transition + `CateringDepositPaid` emission.**
   The `CateringDepositPaid` variant + `commerce_payment_confirmed` skill already
   exist (`schemas.py:5691`, `commerce_payment_confirmed/SKILL.md`); wire the
   receiver. Highest-risk (money + external signature) → first, with adversarial
   review. Prereq for the rest.
2. **PR-S3.2 — §12a freshness watchdog on `state/commerce/*.json`.**
   Now that webhook fire-rate is meaningful, add the freshness SLO + watchdog to
   the existing table-freshness daemon list. Small, additive, monitoring-only.
3. **PR-S3.3 — Cockpit "Deposit-pending leads" tab.**
   Read-only extension of `CommerceOrders.tsx`/`FlyerAdmin.tsx`: surface leads by
   `deposit_status` (awaiting_payment / unconfigured) + `catering_deposit_link_failed`
   forensics. No new dashboard infra; implementer confirms the backend commerce
   read endpoint first.
4. **PR-S3.4 — Agent #10 deposit-awareness** (only after #10 is promoted from stub
   by its own track). Guard against pestering `awaiting_payment` leads; optional
   24h deposit reminder; transition to CONFIRMED on `deposit_status="paid"`.
5. **PR-S3.5 (optional) — A-MEDIUM-2 `CateringLeadStatusChange` emission**, only if
   reviewers request audit-completeness for deposit transitions.

Each PR: TDD (red→green), subagent review before open, no flyer touch, no deploy.

---

## §5 — Recommendation

- **Now:** ship the readiness verdict (`tasks/catering-production-readiness-2026-07-01.md`).
  Hold all deposit code work — no low-risk implementation-ready slice exists.
- **Operator action:** decide deposit posture for the armed-but-unconfigured config
  (§2) — configure template or set `deposit_pct=0`.
- **Trigger to resume:** slice-3 greenlight + payment-provider decision → execute
  §4 in order.
