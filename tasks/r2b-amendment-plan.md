# PR-R2B Plan — Amendment Proposal + Owner-Approved Apply (DOCS-ONLY, 2026-07-20)

**Drift-check tag:** `extends-Hermes` — Hermes owns judgment (semantic amendment
classification, structured extraction, bounded clarification); deterministic code owns
validation, state transitions, idempotent mutation, and the UNCHANGED owner-approval
flow. Receipt: `tasks/.hermes-check-receipts/r2b-amendment-proposal-apply-loop.json`.

**New primitives introduced:** `amend-catering-lead` script (propose/validate/apply
entries — the script hooks.py's gap comment has named since 2026-05-12),
`catering_amendment` SKILL (extraction), one dispatcher amendment row, card-diff
template section. Everything else reuses R2A's sidecar + existing approval machinery.

**Status: PLANNED-UNAUTHORIZED.** Implementation is held pending (a) a clean
normal-traffic soak of R2A, (b) review of the first live R2A outcome (controlled
canary contract pending reviewer ruling on the feasibility finding of 2026-07-20),
and (c) focused review of THIS plan. Nothing in this document authorizes code.

## The locked pipeline (binding, reviewer-ratified)

raw customer message → immutable capture (R2A, LIVE) → Hermes structured proposal →
deterministic validation → before/after diff → EXISTING owner approval → idempotent
atomic commit. Confidence is telemetry only and never authorizes an action. Hermes
never writes to the lead or the sidecar.

## Reviewer rulings incorporated (recorded 2026-07-19/20)

- Date-only bypass closure = the **semantic `catering_amendment` dispatcher row**;
  NO deterministic date-keyword tokens in this phase; add fallthrough observability
  so misses are measurable.
- The dispatcher must distinguish: amendment / new inquiry / ambiguous lead
  reference / terminal-or-stale lead / unauthorized sender — and must NEVER silently
  choose between multiple eligible leads (ambiguity → clarification, not selection).
- Apply preconditions (every one, before any mutation): re-read the current lead;
  verify the proposal's base revision hash (`base_extracted_sha256`); re-run field
  validation; re-run workflow-state validation; reject stale proposals; prevent
  repeated approval from applying twice.
- A deterministic state/field transition matrix is REQUIRED (validators alone are
  insufficient), explicitly covering: quote-issued, deposit-calculated,
  deposit-paid, cancelled, terminal, and multiple-active-lead cells.
- The finalized owner card and deposit logic consume committed amended state ONLY
  after approval; price-affecting changes must never silently preserve a stale
  quote or deposit (a price-affecting applied amendment forces quote/deposit
  recomputation through the existing paths, or blocks with an explicit owner note).
- `raw_text_truncated=true` records → MANUAL handling; the stored prefix is never
  treated as the authoritative amendment.
- The yield surface is a **bounded single-turn protocol**: deterministic ambiguity
  detection → ONE Hermes clarification or structured dispatch proposal →
  deterministic dispatcher validation → existing safety/approval flow; with loop
  prevention (one clarification per message chain), timeout/failure fallback to the
  deterministic clarification, stale-context TTL (4h, mirroring quote-echo), kill
  switch (env flag), scoped enablement (rides front-brain admission), and metrics
  for corrections, dispatcher rejections, unresolved yields, latency, and critical
  errors.
- OWNER_EDITED's missing consumer stays a SEPARATE deferred item (tasks/DEFERRED.md);
  R2B does not touch it. `cf_router_raw_body` remains forensic-only.

## Hermes-first capability checklist

| # | Step | Tag | Net-new LOC |
|---|---|---|---|
| 1 | Amendment arrives (Branch-B captured — R2A live; or LLM/dispatcher path) | `[Hermes]` | 0 |
| 2 | Dispatcher row semantically classifies (amendment / new / ambiguous / stale / unauthorized), never-silent-choice, fallthrough telemetry | `[Hermes]` classification; glue | ~10 |
| 3 | Dispatcher-path amendments enter the SAME R2A capture (source=dispatcher) | `[net-new]` | ~15 |
| 4 | `catering_amendment` SKILL extracts proposal JSON (deltas + unsupported + ambiguous + confidence) | `[Hermes]` — structured extraction | 0 (SKILL.md) |
| 5 | `amend-catering-lead --propose`: deterministic validation (reuse CateringLeadExtractedFields validators), record proposal on sidecar (status=proposed, base hash) | `[net-new]` | ~90 |
| 6 | Owner card re-issued with before→after diff + unparsed fragments (existing templates + EXISTING #code) | `[net-new]` | ~60 |
| 7 | Owner approves/rejects via unchanged F8/apply-catering-owner-decision | `[Hermes]` — approval workflow | 0 |
| 8 | Apply-at-approve: preconditions + transition matrix + idempotent atomic commit into lead.extracted; sidecar → applied; downstream reads committed state | `[net-new]` | ~110 |
| 9 | Reject → rejected_by_owner; truncated → manual route | `[net-new]` | ~25 |
| 10 | Bounded single-turn clarification protocol (controls above) | `[net-new]` glue; asking is `[Hermes]` | ~40 |
| 11 | Metadata-only audit at every stage; no raw text in general logs | `[Hermes]` — audit chain (+~20 schema) | ~20 |

Red-flag check: 6/11 net-new; intelligence steps are Hermes-owned; net-new is the
contracts glue Hermes must not own. Product ≈ 360 LOC + 2 SKILL prose; tests 500-700.

## Drift-rule self-checks

- ✅ Read `src/plugins/cf-router/hooks.py` (F7 Branch-B arm incl. the R2A capture
  rewrite, the owner-guard at :5001-5004, `_should_start_new_lead_over_active`
  ordering) before scoping the dispatcher-row ordering cell
- ✅ Read `src/plugins/cf-router/actions.py` (`find_active_catering_lead_by_sender`
  :374-437 with the PR-R1 canonical fallback) before scoping multi-lead ambiguity
- ✅ Read `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` (matrix + pool
  section) before drafting the amendment-row placement
- ✅ Read `src/platform/safe_io.py` (FileLock/atomic primitives + parent-mkdir
  behavior) before the apply-commit design
- Implementation-time reads (mandatory, before code): full
  `apply-catering-owner-decision`, `handle_catering_owner_approval/SKILL.md`, both
  card templates, `schemas.py` CateringLead + CATERING_TRANSITIONS region,
  `catering_amendments.py` (extend, don't fork).

## Deterministic transition matrix (normative core; full table finalized at implementation review)

| Lead state at APPLY time | Price-affecting delta | Non-price delta |
|---|---|---|
| AWAITING_OWNER_APPROVAL | apply + card re-issued (no quote exists yet) | apply |
| CUSTOMER_FINALIZED (quote-issued) | apply + FORCE quote recompute via existing finalize path; owner card flags the change | apply + card note |
| OWNER_APPROVED (pre-send transient) | REJECT stale (approval raced the amendment); owner notified | REJECT stale |
| deposit-calculated (deposit_* set, unpaid) | apply + force deposit recompute; never silently keep stale deposit | apply + note |
| deposit-paid | REJECT auto-apply → manual route (money already moved) | manual route |
| SENT_TO_CUSTOMER / CLOSED / REJECTED / STALE (terminal-ish) | out_of_window (R2A semantics preserved) | out_of_window |
| Multiple eligible leads for sender | NEVER silent-choose → bounded clarification | same |
Base-hash mismatch (lead.extracted changed since proposal) → stale-reject + re-propose
from the retained raw text. Double approval → idempotent no-op (applied is terminal).

## Build sequence (each PR carries its own test cells; all UNAUTHORIZED until gates clear)

- **R2B-1** schema + `amend-catering-lead --propose` + card diff (~170 LOC)
- **R2B-2** dispatcher amendment row + dispatcher-path capture entry + fallthrough
  telemetry (~25 LOC + prose; product review covers routing change)
- **R2B-3** apply-at-approve + transition matrix (~135 LOC; the money-adjacent PR —
  multi-vector review expected)
- **R2B-4** bounded clarification protocol + kill switch + metrics (~40 LOC)

## Test matrix (normative)

Every transition-matrix cell · base-hash staleness · double-approve idempotency ·
multi-lead never-silent-choice · truncated→manual · unsupported/ambiguous surfacing ·
date-only amendment via dispatcher row (no duplicate lead) · fallthrough telemetry ·
yield loop-prevention/timeout/kill-switch/stale-TTL · price-affecting recompute cells
(quote + deposit) · privacy (no raw text in general logs) · replay-grid carryover from
the routing validation for the amendment rows.

## Approvals log

- 2026-07-20: reviewer authorized DOCS-ONLY R2B planning; this plan written under all
  recorded contracts. Implementation, extraction enablement, dispatcher changes:
  HELD pending R2A soak + first-live-outcome review + focused review of this plan.
