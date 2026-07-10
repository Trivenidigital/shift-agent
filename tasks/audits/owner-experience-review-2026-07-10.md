# Owner-Experience Product Review — Flyer Studio · Shift Agent · Catering Agent

*2026-07-10. Method: three independent reviewers, each immersed as the SMB owner (+ customer/staff
lenses) of one agent, grounded in the real deployed skills, message templates, scripts, and
golden/incident-replay test scenarios. Critical, product-first, non-defensive.*

**Scope honesty:** grounded in deployed **code + real message copy + test scenarios**, NOT weeks of
live VPS conversation logs (no live-box access this run). Live `config.yaml` (which paths are active
per customer) is operator-owned and not in-tree; both shipped paths were judged as source.

---

## 1. Executive summary

| Agent | Score | One-line |
|---|---|---|
| **Flyer Studio** | **7/10** | A trustworthy *fact-safety* machine wrapped around an *unenforced* aesthetic bar; makes flyers but doesn't distribute them. |
| **Shift Agent** | **5/10** | Happy path is 8–9, but every non-happy path (no reply / decline / I ignore it) **silently dead-ends with no escalation**, while the copy promises it's watching. |
| **Catering Agent** | **3.5/10** | Impressive money plumbing around one rotten load-bearing number: `quote_total_usd` is never scaled to headcount, so every real deposit is structurally wrong. Safe only because payment is fail-closed. |

The infrastructure quality across all three is genuinely high — fail-closed gates, owner-only
privilege checks, idempotency, audit chains, prompt-injection hardening, jargon-scrubbed
customer copy. The gaps are **not** in the plumbing. They're in a single shared blind spot.

## 2. The cross-agent systemic finding (read this first)

**All three agents wire their lifecycle only for the happy path where a human replies. The
no-reply / stale / unactioned state has no owner — and in two of three, the copy actively
promises a monitoring that does not exist.** This is precisely the §12a/§12b silent-failure class
this codebase's own discipline is built to prevent, appearing at the product layer:

- **Flyer** — computes `would_i_post` / `appetite_appeal` critique, then **logs it and moves on**. "We measured the thing that matters and told no one."
- **Shift** — a `sent` proposal that gets no reply **never transitions or escalates**; meanwhile the owner is told *"I'll let you know when they respond"* and *"expires in 4 hours"* — both false.
- **Catering** — a missed owner-approval card leaves the lead in `AWAITING_OWNER_APPROVAL` forever; **no stale-lead re-nudge exists**; a missed card = a silently lost booking (highest-margin revenue).

Fixing this one class — *give the stale/no-reply state an owner, and make the copy true* — is the
single highest-leverage theme across the suite, and it's cheap (sweeps + one surfaced signal).

---

## 3. Per-agent reviews

### 3a. Flyer Studio — 7/10
**Trustable:** facts are never typed by the AI — the model emits a textless background + fact
*references*; deterministic code overlays every price/name/date, then OCRs the finished image and
verifies each locked fact is visible and nothing fabricated is (`flyer_generation/SKILL.md:99-104`;
`visual_qa.py:1945-2064`; 20+ fabrication tests). Fails **closed** (block-tier → manual review, not a
send). The owner is **structurally the last human** — nothing auto-publishes; finals return only to
the owner's own WhatsApp. Best-in-class multilingual + festival art direction for this community.
**#1 must-change:** the premium-poster critique scores `message_clarity`/`appetite_appeal`/**`would_i_post`**
but is **log-only, never gates** — a composite 1.0 ("ugly") poster is still composed and delivered
(`test_flyer_premium_poster_v1_critique.py:141-148`). The system guards facts and abandons brand.
**Other:** approval accepts 9 aliases though the SKILL claims "exact APPROVE" (`actions.py:1699-1711`);
preview-approved→final-QA-fail surprise (F0065); no "why this concept" explainability; regional-glyph
garble leans on plainer fallback; guided-intake interview is dormant (546 bypass rows).

### 3b. Shift Agent — 5/10
**Trustable:** deterministic gateway intercept for sick-calls (no LLM dependency); a **hardened send
gate** (re-resolves phone from roster, refuses unless `approved`, daily cap, idempotency key, refuses
to treat an unparseable 200 as success — `send-coverage-message:108-122`); crash reconciler won't
double-text staff. Happy-path loop closes cleanly.
**#1 must-change (the silent uncovered shift):** **no timer/cron transitions a `sent` proposal to
`no_response_timeout`** — the machinery exists (`update-proposal-status:156`, `schemas.py:3177`) but
is only reachable by manual CLI; nothing invokes it. Owner approves → candidate never sees WhatsApp →
proposal sits `sent` forever → 6pm shift empty, **no alert**. The approval reply promises *"I'll let
you know when they respond"* (`handle_owner_command/SKILL.md:32`) — it only reacts to an inbound
YES/NO; silence is invisible. `pending_proposal_ttl_hours: 4` has **zero readers** (the "expires 4h"
promise is decorative). The daily brief's `proposals_no_response` counter is **permanently zero**.
**Other (real):** the coverage message leaks a coworker's health reason (*"Ravi is out (health: fever)"*,
`coverage_message_to_candidate.txt:3`); dates render as ISO `2026-04-29` not "tomorrow (Wed)"; the
ranker always picks the same person (no fairness ledger); outbound copy is English-only; `KILL` sits
in every routine proposal footer; shift approvals lack the deterministic gateway intercept catering has.

### 3c. Catering Agent — 3.5/10
**Trustable:** owner-only privilege gate on money+menu writes (rejects non-owner `#XXXXX` *before*
touching state); **deposit double-charge guard, fail-closed**; deposit fail-closed on unconfigured URL
(byte-exact copy, tested); state-vs-outbound divergence detected + escalated; menu never live without
owner yes; customer-copy leak invariants tested; prompt-injection hardening on the drafted quote.
**#1 must-change (BLOCKER — the deposit math):** `quote_total_usd` is **never scaled to headcount**.
Both finalize paths build the basket at qty=1 (`finalize-catering-menu:373-403` — comment literally
*"No headcount scaling"*; `select-catering-proposal:186-202`). So a 200-guest event → "internal
estimate ~$52" → **deposit ~$13 auto-fires** off `quote_total_usd × 0.25` the instant the owner
approves (`apply-catering-owner-decision:888-892`). Only safe today because the payment URL is
unconfigured. **Do not enable a live Stripe URL until this is fixed.**
**Other (real):** the *"Customer FINALIZED their menu"* card lists the first-5 items the customer
never chose (misleading); the customer quote has **no price-correctness check** (headcount+date only);
retail sample prices are auto-sent pre-approval (`create-catering-lead:630-650`); **no stale-lead
re-nudge**; the `edit` path is a WhatsApp **dead-end** (`OWNER_EDITED` excluded from the approve
matcher, `:438-444`); menu preview truncated to 8/category (approve prices you never saw); INR→USD
currency default (`parse-menu-photo:107`) is a diaspora landmine; ⚕ caduceus emoji header on every
customer message.

---

## 4. Suite-level #1 risk

**The Catering deposit-total bug is the single most dangerous finding in the suite.** It is currently
*latent* (fail-closed on an unconfigured payment URL), which is exactly why it's dangerous — the whole
downstream money path faithfully computes a wrong deposit from one rotten number, and the only thing
standing between it and a real mis-charge is a config value someone might flip. **Recommendation:
keep Stripe/live-payment disabled for catering until `quote_total_usd` is headcount-aware or
owner-set-per-lead AND the deposit requires explicit per-lead confirmation of the actual dollar
amount.** (Note: PR #582 shipped a Stripe/live-payment *enablement checklist* — this bug must be a
hard gate on that checklist.)

## 5. Prioritized roadmap (suite)

**Quick wins (days)**
- **Shift:** no-response escalation sweep → alert owner + one-tap "try next person" *(← recommended first slice, §6)*.
- **Shift:** stop leaking the health reason to coworkers; humanize dates; remove `KILL` from the routine footer.
- **Catering:** owner-card warning when `quote_total_usd / headcount` is implausibly low (`< ~$8/guest`); retitle "Customer FINALIZED" → "Draft basket (auto-filled — review)"; drop/flag the auto-sent retail sample prices; swap the ⚕ header.
- **Flyer:** surface the `would_i_post` critique to the owner in the preview ("I'm not fully happy with this look — retry?"); reconcile the approval-alias doc gap.
- **Flyer + Shift + Catering:** make the copy true or delete it (the "I'll let you know" / "expires 4h" / monitoring promises).

**High-impact slices (weeks)**
- **Catering:** headcount-aware `quote_total_usd` + per-lead owner confirmation of the deposit amount before any send *(the BLOCKER root fix — hard gate for live payments)*.
- **Catering:** fix the `edit` dead-end; stale-lead re-nudge sweep; show the full extracted menu, not 8/category.
- **Shift:** no-response next-candidate command; show top 2–3 candidates; localize outbound copy.
- **Flyer:** gate (not just log) on aesthetic critique; add "why this concept"; run final-grade QA before the owner is asked to approve.

**Foundation**
- A **shared "stale-state sweep + owner-alert" primitive** — all three agents need the same "find records stuck in state X older than TTL → transition + notify" mechanism; build once (this session's watchdog pattern), reuse across shift/catering/flyer.
- Give shift approvals the same deterministic gateway intercept catering has (don't ride "approve → staff messaged" on LLM reliability).

**Long-term**
- Catering per-guest pricing model. Flyer as a *sender* (owner-consented broadcast) not just a maker. Shift fairness ledger + staff opt-out. Flyer guided-intake interview.

## 6. Recommended first build slice: Shift Agent no-response escalation sweep

**Why this one first** (over the more-severe catering BLOCKER): it maximizes value × safety ×
buildability for an unattended run. It fixes a *core functional failure* (the silent uncovered shift)
AND makes two false promises true, it's a *complete thin vertical slice* (detect stale `sent` →
transition → alert owner with a next action), and it is **additive, owner-alert-only — no staff
auto-message, no coverage-logic change, no money** — mirroring the existing `shift-agent-health-watchdog`
pattern. The catering deposit BLOCKER is more severe but its root fix is a *pricing-model design
change* needing owner input (not a safe unattended first slice), and it's fail-closed today; it is the
loud **#1 high-impact roadmap item** and the recommended *next* slice, gated behind live-payment enablement.

**Ship pattern:** config flag default-OFF + timer installed-disabled (matches premium-poster +
skills-audit); operator enables per-customer after review. Full design in the slice's plan doc.

## 7. Final verdict

- **Flyer Studio** — rely on it to *make* flyers with the standing rule (which it enforces) that you
  preview every one; not a hands-off marketer. Path to 9: make `would_i_post` hold/flag, add "why this
  concept", prove clean regional rendering, run final-QA before approval.
- **Shift Agent** — supervised first-responder only, **not unattended**: it drafts a clean proposal and
  closes the loop on a fast yes, but goes silent on no-reply/decline while telling you it's handling it.
  Path to 8: the no-response sweep + honest TTL copy.
- **Catering Agent** — **do not run for real money unattended today**; keep live payments off. The infra
  deserves a 7; the unscaled `quote_total_usd` caps it at 3.5. Path to 7: headcount-aware total +
  per-lead deposit confirmation + quote price-validation + stale-lead re-nudge.
- **Suite** — three well-engineered happy paths that share one product-level blind spot (the unowned
  stale state) and one dangerous latent money bug (catering deposit). Close those and this becomes a
  suite an owner can genuinely trust with staff, customers, and money.
