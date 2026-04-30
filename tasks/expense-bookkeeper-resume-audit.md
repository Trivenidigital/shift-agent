**Drift-check tag:** `extends-Hermes` — this audit is *about* an existing `extends-Hermes` agent. No code proposed; only scope/cut recommendations grounded in deployed reality.

# Expense Bookkeeper — Resume Audit (2026-04-30)

**Date:** 2026-04-30
**Audited branch:** `main` at `179e80b` (post PR #41 platform-helpers consolidation)
**Audit purpose:** apply the three CLAUDE.md enforcement mechanisms to Agent #21 before resuming any v0.2 work; identify what to cut from already-shipped code AND from backlog.
**Audit method:** read every spec doc + every shipped artifact; cross-reference against catering precedent.

---

## TL;DR

Expense Bookkeeper v0.1 shipped autonomously across PRs #30, #32, #34, #35, #41 — total **~2,120 LOC code + ~2,170 LOC tests + ~2,260 LOC process docs**. The three enforcement mechanisms find the agent **substantially compliant**: drift-tags on 5 of 7 docs, all Part-1 deployed-pattern checks pass, audit chokepoint discipline correct, MockQBOClient is a clean net-new substrate addition.

**Three concrete findings:**

1. **Hermes-first ratio held.** Net-new = QBOClient Protocol + parser + window/undo logic. Plan claimed 1:3.75 net-new:Hermes; reality lands closer to **1:2.5** because cents-precision/dedup/state-machine logic that *looked* Hermes-carryable on paper had to be implemented as bespoke Python. This is **correct** — money-moving discipline is intrinsically net-new per CLAUDE.md.

2. **Two minor drift-tag gaps** (low severity): `v01-overnight-report.md` and `v02-followups.md` lack the drift-tag header. One bug-fix candidate.

3. **6th-lens ("is the scope itself needed?") finds ~3% avoidable scope already shipped, ~40% deferable from backlog.** The biggest cut signal: **without a paying QBO customer, the entire push-path code (~60% of agent LOC) is uncalibrated**. Hardening it further is speculative.

**Resume recommendation:** ❌ **Do NOT resume v0.2 build now.** Three pre-conditions for resume — none currently met:
- (a) Customer-discovery commitment from a paying SMB
- (b) QBO sandbox creds + OAuth scope decision
- (c) 10–50 real receipt photos for OCR/parser regression

If user wants forward motion, only **two ~50 LOC follow-ups** are worth doing now (regression tests for plan §4g #11 + #16). Everything else waits for customer signal.

---

## Mechanism #1 — Hermes-first checklist re-applied

Per CLAUDE.md mandatory step: list every step the agent takes; mark `[Hermes]` or `[net-new]`; effort = net-new only.

### Re-graded matrix (against v01-plan §3 + shipped reality)

| Step | Plan-time grade | Shipped reality | Verdict |
|---|---|---|---|
| Owner WhatsApp inbound + media routing | `[Hermes]` | Used dispatcher amendment; ✅ no custom plumbing | Confirmed `[Hermes]` |
| Sender identity (`validate-sender-block` + `identify-sender`) | `[Hermes]` | SKILL prose explicitly references both helpers | Confirmed `[Hermes]` |
| Idempotency on `original_message_id` | `[Hermes]` | extract-receipt step 3, scans existing leads | Confirmed `[Hermes]` |
| Image cache → managed dir copy + perms | `[Hermes]` (substrate convention) | `_atomic_copy_image` 50 LOC inline (O_NOFOLLOW + O_EXCL) | **Re-graded `[net-new]`** — plan-time grade was wrong; this is genuine money-handling-grade defense, not Hermes substrate |
| Vision extraction (OpenRouter call) | `[Hermes]` | `_call_vision` 36 LOC inline; mirrors parse-menu-photo | Confirmed `[Hermes]` (substrate-consumer pattern) |
| Personal-vs-business classification (LLM prompt) | `[Hermes]` | `_classify_text` 38 LOC inline | Confirmed `[Hermes]` |
| Pure-Python dHash | `[Hermes]` (`imagehash` swapout) | `_dhash_from_bytes` 21 LOC inline + `_hamming` 9 LOC | **Re-graded `[net-new]`** — small but genuine; PIL fallback documented as edge case |
| 5-char approval code generation | `[Hermes]` (alphabet reuse) | `_generate_unique_code` 14 LOC + `_collect_active_codes` 47 LOC cross-state-file collision check | `[Hermes]` for alphabet/regex; `[net-new]` for cross-state-file scan (~50 LOC) |
| Approval card render | `[Hermes]` (template engine) | `_build_approval_card` 80 LOC orchestration + 10 templates | `[Hermes]` infrastructure; the template content/logic is `[net-new]` UX work |
| Code+amount parser | `[net-new]` | 4 anchored regexes + `parse_owner_message` 48 LOC | Confirmed `[net-new]` |
| State-machine transitions | `[Hermes]` (table pattern) | `EXPENSE_TRANSITIONS` table + `_transition` helper + 64 parametrized tests | `[Hermes]` pattern; `[net-new]` table-population (~80 LOC) |
| QBOClient Protocol + Mock | `[net-new]` | `qbo_client.py` 205 LOC | Confirmed `[net-new]`, **substrate-quality** |
| Audit chain (15 LogEntry variants) | `[Hermes]` | All 15 subclass `_BaseEntry`, written via `ndjson_append` chokepoint | Confirmed `[Hermes]` (~140 LOC of typed schema additions) |
| Reversibility window check | `[net-new]` | Inside `_handle_undo` (~40 LOC of window logic) | Confirmed `[net-new]` |
| Owner re-auth on undo (phone OR LID) | `[net-new]` (security) | Inside `_handle_undo` | Confirmed `[net-new]` |
| Threshold-routing UX | `[net-new]` (UX) | extract-receipt step 9 template selection + apply-decision force-validation | Confirmed `[net-new]` |
| Dedup detection + force-override | `[net-new]` (UX) | dHash hamming + `duplicate_of` field + force flow | Confirmed `[net-new]` |
| Orphan-detection (PUSH crash recovery) | `[net-new]` (B2 reviewer-lifted from v0.2) | `scan_orphan_pushes` in `audit_helpers.py` (post PR #41) | `[Hermes]` (now platform); `[net-new]` was justified for v0.1 partial-failure safety |
| Receipt retention (prune + systemd timer) | `[net-new]` (PII discipline) | 143 LOC + 2 systemd units | Confirmed `[net-new]` |
| Daily Brief integration (audit-log consumer) | `[Hermes]` | Existing Daily Brief consumes `decisions.log` | Confirmed `[Hermes]` |
| Pushover alerting | `[Hermes]` | Existing alerting pipeline | Confirmed `[Hermes]` |

### Re-graded tally

- **Plan claimed:** 4 net-new items (~5–7 days), ratio 1:3.75
- **Shipped reality:** ~10 distinct net-new surfaces totaling ~700 LOC (parser, QBOClient.py, atomic image copy, dHash, cross-state collision scan, undo window, undo re-auth, threshold UX, dedup UX, retention/prune, orphan-detect)
- **Re-graded ratio:** ~1:2.5 net-new:Hermes-carried by line count
- **Verdict:** plan was honest about *count* of items but optimistic about *line cost* per item. Actual shipped substrate-carry is closer to 60% than 75%.

**Why this matters:** the original 1:3.75 framing led to the 5–7 day estimate. Actual elapsed engineering was higher. Future estimates for money-moving agents should default to ~1:2.5 substrate-carry until proven otherwise.

### Verdict

✅ **Hermes-first compliance: PASS.** Every shipped surface is either substrate-consumption or money-moving-discipline net-new. **Zero scope-bloat.** No greenfield re-invention of substrate.

---

## Mechanism #2 — Drift-rule audit

Per `docs/hermes-alignment.md` Parts 1+3.

### Part 1 deployed-pattern checklist

| Pattern | Compliance | Evidence |
|---|---|---|
| Storage: JSON-on-disk + `safe_io.atomic_write_json` + `fcntl.flock` | ✅ | `apply-expense-decision` + `extract-receipt` use `safe_io.FileLock` + `atomic_write_json` on `state/expense-bookkeeper/leads.json` |
| NDJSON audit log via `LogEntry` discriminated union | ✅ | 15 entry types subclass `_BaseEntry`; written through `safe_io.ndjson_append` chokepoint |
| Approval codes: 5-char `#XXXXX` from canonical 28.6M alphabet | ✅ | `ProposalCode` regex reused (`schemas.py:843`); `_generate_unique_code` uses canonical alphabet |
| Cross-agent code-pool collision check | ✅ | `_collect_active_codes` scans catering-leads + catering-menu-pending + pending in addition to expense leads |
| Schemas: Pydantic v2 + explicit `model_config` | ✅ | `extra="forbid"` on all state schemas; `extra="ignore"` on `ReceiptExtraction` (LLM output, matches CateringLeadExtractedFields precedent); `Literal[...]` for `ExpenseLeadStatus` |
| Sender identity by phone OR LID | ✅ | SKILL prose references `validate-sender-block` + `identify-sender`; undo flow accepts either match |
| Tests: subprocess-equivalent for scripts; pure-function in-process | ✅ | `test_expense_bookkeeper_apply_decision.py` uses importlib injection (matches catering precedent); pure-function tests in `_state.py`, `_qbo_mock.py`, `_parser.py`, `_guardrails.py` run in-process Windows-runnable |
| Dispatcher routing: amend `dispatch_shift_agent` matrix; write `dispatcher_routed` audit BEFORE delegating | ✅ | 3 matrix rows added; `cross_dispatch_to_expense_bookkeeper` audit obligation in SKILL hard rules |
| Image inputs: copy from `/opt/shift-agent/.hermes/image_cache/` to managed `state/<agent>/...` (`0700`/`0600`) | ✅ | `_atomic_copy_image` does this with O_NOFOLLOW + O_EXCL hardening |
| Per-customer-VPS isolation | ✅ | No cross-VPS state sharing; single-tenant assumed |
| YAML config loaded with `safe_load_yaml`-equivalent (NOT `safe_load_json` which auto-quarantines on parse error) | ✅ | PR #34 added `safe_io.load_yaml_model`; all 3 expense scripts use it |

### Part 3 read-deployed-code working agreement

Plan v2.1 §11 + Design v2 §0 both explicitly list deployed files read before drafting (7 files for design alone). **Compliance verified by reviewer reads** that caught 3 places where plan parroted assumptions without verification:
- `generate_unique_code` is per-script-inline, not platform helper (reviewer A1)
- `qbo_client.py` install path is flat (`/opt/shift-agent/qbo_client.py`), not `platform/` subdir (reviewer E3)
- Test pattern is importlib injection, not sed-patching (reviewer D1)

All three corrected pre-build. ✅

### Drift-tag self-disclosure audit

| Doc | Drift-tag present? | Verdict |
|---|---|---|
| `expense-bookkeeper-v01-plan.md` | ✅ `extends-Hermes` | OK |
| `expense-bookkeeper-v01-design.md` | ✅ `extends-Hermes` | OK |
| `expense-bookkeeper-v01-audit-report.md` | ✅ `N/A (audit, not proposal)` | Defensible |
| `expense-bookkeeper-v01-overnight-report.md` | ❌ MISSING | **Minor drift** — status report; defensibly N/A but should declare so explicitly |
| `expense-bookkeeper-v02-followups.md` | ❌ MISSING | **Minor drift** — this IS a backlog spec; should carry `extends-Hermes` since most items extend platform |
| `expense-bookkeeper-audit-bugs-plan.md` | ✅ `extends-Hermes` | OK |
| `expense-yaml-load-fix-plan.md` | ✅ `extends-Hermes` | OK |

### Verdict

✅ **Drift-rule compliance: PASS with two micro-gaps.** No Part-1 pattern violations. Read-deployed-code commitment honored. Two doc-header omissions worth fixing in a single 2-line PR if anyone touches those files anyway. **Not worth a dedicated PR.**

### Reasonable platform-helper gaps (not violations, but observations)

These showed up as inline-script code where a platform helper *might* fit but didn't yet:
- **`_dhash_from_bytes` + `_hamming`** (~30 LOC) — only one consumer (extract-receipt). Lift only if a 2nd consumer appears (e.g. menu-image dedup). Per drift-rules-v2, "pure-function logic lives inside the script files." No action.
- **`_generate_unique_code` + `_collect_active_codes`** (~62 LOC) — reviewer A1 explicitly said do NOT lift; 3 existing call sites have parallel inline copies (parse-menu-photo, create-catering-lead, create-proposal). A separate refactor PR would unify all 4. Out of scope for v0.1.
- **`_call_vision`** (~36 LOC) — duplicates parse-menu-photo's substrate-consumption shape. Could become `platform/openrouter_vision.py` for both consumers. Defensible as inline-per-script for v0.1 (catering convention). Lift candidate when a 3rd vision consumer arrives.

---

## Mechanism #3 — 6th-lens scope review ("could Hermes already do this — is the scope itself needed?")

The five existing review lenses (security / drift / schema / truth-guard / deploy) take scope as given. This 6th lens questions the scope itself.

### What was justified — keep

- **MockQBOClient + RealQBOClient stub split + Protocol** (qbo_client.py 205 LOC). Money-moving substrate; this is the *one* surface where v0.1 absolutely needed shape-pinning before customer onboarding. Keep entire surface.
- **Code+amount parser with 4 anchored regexes** (~80 LOC). The defense against owner-types-wrong-amount is the entire UX-discipline argument; cannot be deferred. Keep.
- **Reversibility window + force flow + undo re-auth.** Same — core UX discipline. Keep.
- **Audit chain with 15 LogEntry types.** Forensic trail is non-negotiable for money flow. Keep.
- **State-machine table + 64 parametrized tests.** Single source of truth + exhaustive coverage; reviewer-lifted to v0.1. Keep.
- **Atomic image copy with O_NOFOLLOW + O_EXCL.** Reviewer-b HIGH B1 lift; symlink-replace defense. Keep.
- **Cross-state-file code-pool collision check.** Reviewer A2 HIGH lift; prevents silent cross-agent routing. Keep.

### What was avoidable in v0.1 — small cuts available

| Surface | LOC | 6th-lens question | Recommended action |
|---|---|---|---|
| `routed_to: Literal["whatsapp", "cockpit_v01_paper"]` enum + extract-receipt:693 conditional | ~10 LOC | Cockpit is v0.2 paper-spec; placeholder enum value pollutes today's audit log | **CUT** — drop `cockpit_v01_paper`; keep `routed_to: Literal["whatsapp"]` only; re-add when cockpit ships |
| 3 `expense_force_required_*.txt` templates | ~30 LOC | Re-prompt UX when owner replies without `force` after threshold/dedup. Could merge into 1 template with field substitution | **KEEP** — UX wording differs; merge cost ≈ benefit; not worth the churn |
| `prune-and-expire-expenses.py` + 2 systemd units | ~143 LOC | Could have been v0.1.1 patch after first real receipts accumulate | **KEEP** — PII discipline upfront is correct; receipts ARE going to land if any customer onboards |
| Orphan detection (now in `audit_helpers.scan_orphan_pushes`) | ~70 LOC pre-PR#41, ~30 LOC post | Reviewer-b B2 lifted from v0.2 to v0.1 for partial-failure safety | **KEEP** — PR #41 already deduplicated to platform; current state is correct |

**Total cut signal in already-shipped code: ~10 LOC** (`cockpit_v01_paper` enum + conditional). The rest of the 2,120 LOC is correctly scoped for money-moving v0.1.

### What could have been deferred but wasn't worth deferring

- **10 templates instead of 5.** All exist; each is small (~5–15 LOC). UX is sharper with explicit copy per branch. Keep.
- **15 LogEntry types instead of 8.** Each justified by a specific audit-trail use case. Keep.
- **`reconcile_required` flag on `ExpenseLead`.** One field; reviewer-justified. Keep.

### The bigger 6th-lens question — *is the agent itself the right thing to be working on?*

This is the lens-application that actually changes the recommendation:

- v0.1 is feature-complete for an unsold agent.
- 60% of the LOC sits behind a `cfg.expense_bookkeeper.enabled = false` flag and exercises a `MockQBOClient`.
- The QBO push path has zero customer feedback because **no customer has onboarded with QBO sandbox creds.**
- Real-receipt OCR has not been smoke-tested on physical receipts (Catering's menu-pipeline E2E proves the *substrate* works, not that *receipt-specific* layouts are read accurately).

**Net: v0.2 work is speculative until at least one of these three signals lands:**
1. Paying SMB customer commits to expense capture
2. QBO sandbox account + Intuit Developer SDK + OAuth scope decision
3. 10–50 real receipt photos collected for OCR + parser regression

Without those, *any* further work on Expense Bookkeeper is gold-plating.

---

## Backlog comparison — what is still pending

### From `expense-bookkeeper-v02-followups.md` (8 items)

| # | Item | Status | Cut? |
|---|---|---|---|
| V02-1 | Null-byte/control-char defense across `sender_lid`, `qbo_account`, `rejection_reason` | Pending | **CUT** — fields are operator/LLM-set, not owner-input; Pydantic JSON-escape covers log-safety; YAGNI |
| V02-2 | `sender_phone` → `Optional[E164Phone]` + at-least-one-of validator (mirror RawInbound) | Pending (~200 LOC) | **CUT** — YAGNI; do it ONLY if multi-customer onboarding surfaces a phone-format inconsistency |
| V02-3 | Lift `_check_orphans` to platform | ✅ DONE in PR #41 (`scan_orphan_pushes`) | N/A |
| V02-4 | Token-redactor: `state=` / `code_verifier=` outside URL context | Pending | **DEFER** — only matters when RealQBOClient lands; gate on (b) above |
| V02-5 | `image_path` `os.path.realpath` symlink-resolve | Pending | **CUT** — single-tenant VPS architecture; only relevant in multi-tenant scenario that isn't planned |
| V02-6 | `expense_lookup` SKILL (analog of `lookup-prior-leads-by-phone`) | Pending | **DEFER** — customer-demand-driven; gate on (a) above |
| V02-7 | Pre-existing dispatcher regex unification (`#[A-HJ-NP-Z2-9]` vs canonical `#[A-HJKMNPQR-Z2-9]`) | Pending (~5 file edits) | **CUT** — no functional impact; only do if piggybacking on a related dispatcher PR |
| V02-8 | jq-syntax-validity assertion in audit test (Linux-only) | Pending | **CUT** — Windows test env doesn't have jq; string-presence test sufficient |

### From plan §4g edge cases (5 deferred from v0.1)

| # | Item | Status | Cut? |
|---|---|---|---|
| #2 | Typo'd code → silent (could-be-unrelated-message) | Already silent in dispatcher | **CUT** — already correct behavior; no test needed |
| #7 | Sum-mismatch resolution (line items sum != total_cents) | Schema accepts; owner-confirmed total wins | **CUT** — owner-confirmed-truth defense already covers; explicit test would be ceremony |
| #9 | Vendor name normalization | LLM-only; no platform helper | **DEFER** — surface only if a customer flags ambiguous vendor matching |
| #11 | Approval-code collision regenerate test | Helper exists (`_generate_unique_code` retry-on-collision); not explicitly tested | **KEEP** — small (~20 LOC test); cheap insurance |
| #16 | Multi-receipt batch test (5 photos in 30s) | Architecture supports it (each becomes own lead); not E2E-tested | **KEEP** — small (~30 LOC test); low-risk forensic value |

### From overnight-report's "Recommended Monday work"

| Priority | Item | Status |
|---|---|---|
| P0 | VPS config bootstrap on `46.62.206.192` | Pending (separate ops task, not code) |
| P0 | Fill Pushover keys + GPG email on test VPS | Pending (separate ops task, not code) |
| P1 | Lift `_check_orphans` to platform | ✅ DONE in PR #41 |
| P1 | Tighten `test_undo_within_window_succeeds` (test-bug nit) | **CUT** — LOW severity; only-if-touched |
| P2 | Misc reviewer follow-ups | Mostly cut per above |

### V0.2+ scope (from plan §2 deferred list)

| Item | Cut? |
|---|---|
| Real `RealQBOClient` (Intuit Developer SDK + OAuth + token refresh) | **GATED on customer signal (b)** — do nothing until QBO sandbox creds land |
| Cockpit web UI for above-threshold review | **GATED on customer signal (a)** — paying customer + actual above-threshold volume |
| Voice notes / multi-language / multi-page PDF / multi-currency | **CUT until requested** — pure spec-driven YAGNI |
| Self-improvement loop / learned classification | **CUT** — speculative; doesn't fit current per-VPS architecture cleanly |
| Owner-edit-before-push (`#CODE edit text`) | **DEFER** — small scope; add after first 100 real approvals |
| Per-location auto-tagging (depends on Multi-Location Coordinator data) | **GATED on Agent #6 first** |
| Tax-jurisdiction reasoning | **CUT** — QBO has its own tax setup; no reason to duplicate |
| Family-member receipt forwarding | **DEFER** — surface only on customer feedback |
| Vendor creation in QBO chart | **DEFER** — let owner do it manually until volume justifies automation |

---

## Final cut analysis — concrete recommendations

### Cuts from already-shipped code (very minor)

1. **Drop `cockpit_v01_paper` enum value + extract-receipt:693 conditional** — ~10 LOC. Re-add the enum value when cockpit actually ships. **Net: 1 commit, ~5 min work, ship as opportunistic cleanup if any v0.2 PR touches `ExpenseOwnerApprovalRequested`.**

2. **Add drift-tags to v01-overnight-report.md and v02-followups.md** — 2 lines. **Net: 1 commit, ~2 min work.**

(Total: ~12 LOC + 2 doc-header lines. Not worth a dedicated PR. Bundle if any other expense-touching PR happens.)

### Cuts from v02-followups backlog (5 of 8 items)

- **CUT V02-1** (null-byte across all string fields) — YAGNI
- **CUT V02-2** (E164 sender_phone refactor) — YAGNI, 200 LOC for no functional change
- **CUT V02-5** (realpath symlink hardening) — single-tenant N/A
- **CUT V02-7** (dispatcher regex unification) — no functional impact
- **CUT V02-8** (jq-syntax test) — Windows-incompatible

**Remaining backlog after cuts: 3 items, all gated on external signals**
- V02-4 (token-redactor extension) — gated on RealQBOClient build
- V02-6 (expense_lookup SKILL) — gated on customer demand
- §4g #11 + #16 (collision regenerate + multi-receipt batch tests) — small follow-up worth doing now

### Cuts from v0.2 spec backlog (most of it)

Anything not gated on (a) paying customer, (b) QBO sandbox creds, or (c) real-receipt smoke set is **YAGNI** and should not be on the active roadmap. Specifically: voice notes, multi-language, multi-page, multi-currency, learned classification, tax-jurisdiction, family forwarding, vendor auto-creation.

### What to do next — three options

**Option A (recommended): STOP. Do nothing on Expense Bookkeeper until customer signal.**
Spend resume-time on agents that have customer signal already (Catering PR-B v3 MVP gated on PR-D3 soak completion ~2026-05-01 13:00 UTC, or backlog promotion of #11 Festival & Peak Prep).

**Option B: do the two ~50 LOC follow-up tests.**
- §4g #11 — `test_approval_code_collision_regenerate` (~20 LOC)
- §4g #16 — `test_multi_receipt_batch_independent_leads` (~30 LOC)
Plus the two-line drift-tag fixes. **Total ~55 LOC, single small PR, ~1h work end-to-end including 5-agent review.** Minor forensic value; doesn't change customer outcomes.

**Option C: full v0.2 build (NOT recommended).**
Includes RealQBOClient, cockpit UI scaffolding, vendor-normalization helper. ~1–1.5 weeks of work. **Speculative without customer + sandbox creds.** Blocked on (a)+(b) anyway; doing it now means rework when reality lands.

---

## Verdict on enforcement-mechanism efficacy

| Mechanism | Caught | Missed |
|---|---|---|
| Hermes-first checklist | Plan honestly graded ratio at 1:3.75; reviewers re-graded 2 items at plan stage; reality landed at 1:2.5 | LOC cost per net-new item was under-estimated; future plans should use 1:2.5 default for money-moving agents |
| Drift-rule audit (Part 1+3) | All deployed-pattern compliance checks pass; 7 deployed files explicitly read pre-build; 3 plan-time assumptions corrected by reviewer reads | 2 doc-header drift-tag omissions (overnight-report, v02-followups) |
| 6th-lens scope review | Caught the bigger question — *is the agent itself the right thing to be working on?* — and identified ~10 LOC of avoidable shipped scope + 5 of 8 backlog items as cuttable | Not applied at plan-time; would have flagged `cockpit_v01_paper` enum if applied then |

**Net efficacy: HIGH.** The three mechanisms together produce a tight, defensible scope envelope. The biggest finding (resume gating) is exactly the kind of thing the 6th lens is designed to surface but the other five lenses cannot.

---

## What this audit did NOT cover

- **Live VPS testing.** Test VPS (`46.62.206.192`) lacks `config.yaml`; smoke gate correctly auto-rolls-back. Code-level audit only.
- **Real receipt OCR accuracy.** Catering's menu pipeline proves substrate; receipt-specific layouts (faded thermal, handwritten) untested.
- **Real-QBO API testing.** v0.1 ships `MockQBOClient`; `RealQBOClient` raises `NotImplementedError` by design.
- **Load / concurrency.** Single-receipt scenarios only; high-volume not exercised.

These are deliberate v0.1 cuts, documented in plan v2.1 and design v2.

---

## Hermes-first checklist (mandatory per CLAUDE.md, applied to this audit doc itself)

This audit IS itself a doc-write under `tasks/`, so the per-step checklist applies:

| Step | `[Hermes]` / `[net-new]` |
|---|---|
| Read shipped code + spec docs | `[Hermes]` (Read tool) |
| Cross-reference deployed patterns | `[Hermes]` (file inspection) |
| Re-grade Hermes-first matrix | `[net-new]` (analysis, no code) |
| Apply drift-rule checklist | `[net-new]` (analysis, no code) |
| Apply 6th-lens questions | `[net-new]` (analysis, no code) |
| Compare backlog vs deployed | `[net-new]` (analysis, no code) |
| Produce cut recommendations | `[net-new]` (analysis, no code) |

**Net-new tally: 5 (all analysis, zero code).** This is an audit, not a build proposal — the [net-new] items here are the audit *output*, not engineering work. The hook accepted this doc because it carries the drift-tag and Hermes-first heading.

---

*Audit complete. Ready for user decision: Option A (stop), Option B (~50 LOC follow-ups), or Option C (full v0.2 — not recommended without customer signal).*
