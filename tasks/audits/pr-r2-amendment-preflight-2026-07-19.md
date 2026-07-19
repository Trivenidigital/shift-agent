# PR-R2 Amendment-Loop Preflight — READ-ONLY (2026-07-19)

**Status:** preflight only. Nothing implemented, no extraction enabled, no production
amendment created, no data altered. HEAD = `a851583` (current production).
**Locked pipeline (binding):** raw customer message → immutable capture → Hermes
structured proposal → deterministic validation → before/after diff → EXISTING owner
approval → idempotent commit. Hermes never writes to the lead.
**Method:** subagent current-state extraction + lead verification of every
load-bearing claim by direct execution/read (classifier probes re-run; gap comment,
capture surfaces, caps confirmed at file:line).

## 1. Current production amendment message path

Amendments from a customer with an active non-terminal lead take one of THREE paths:
- **Headcount/food-signal amendments** ("actually 280 guests", "make it vegetarian"):
  probe-verified signals `headcount:280` / `food_keyword` → F7 follow-up signal →
  Branch B (hooks.py:4980→5019+) → hard-coded canonical reply ("Your inquiry {lead_id}
  is with the owner… Reply here if you need to adjust") → skip. Text goes nowhere.
- **Date-only amendments** ("move the date to Aug 2"): probe-verified ZERO catering
  signals ("date" absent from `_EVENT_KEYWORDS`, actions.py:1547-1551) → F7 never
  runs → generic LLM path (non-deterministic; may reach the dispatcher SKILL).
- **LLM/dispatcher path** (F7 off or fallthrough): catering_dispatcher SKILL.md:64-147
  has NO amendment row → path 5 `parse_catering_inquiry` → `create-catering-lead`
  with a fresh message_id → **DUPLICATE LEAD** (new L-id, new #code, new owner card).

## 2. Exact loss/flatten points

- Branch B drop: hooks.py:5077-5085 (canonical reply; text unparsed, unattached);
  the in-code KNOWN GAP comment (hooks.py:5021-5036) names the fix script
  `amend-catering-lead` — which does not exist (grep-confirmed).
- Suppression audit carries no text (`cf_router_intercepted.detail` = lead id+status).
- Raw text DOES survive today, truncated + unassociated: `cf_router_raw_body.body_head`
  = text[:400] (actions.py:1378) written pre-routing — a recovery/backfill source.
- Parallel drop (out of PR-R2 scope, recorded): owner "edit" → OWNER_EDITED stores
  `edit_text[:1000]` only in an audit row; the SKILL-promised "drafter" doesn't exist.

## 3. Lead + approval-card state transitions (current)

Statuses (schemas.py:502-518) NEW…STALE with `CATERING_TRANSITIONS` (:532-567) — no
amend/re-extract edge exists. Owner card rendered at TWO sites: create-catering-lead
:239-348 (template `catering_approval_card_to_owner.txt`; shows raw_inquiry[:300] +
extracted summary + #code) and finalize-catering-menu :226-315 (template
`catering_finalized_menu_to_owner.txt`; headcount/date/dietary read from
`lead.extracted` → STALE if an amendment was never applied; the deposit hook
(apply-catering-owner-decision:888) also computes from these values).
approve: {AWAITING, CUSTOMER_FINALIZED, OWNER_APPROVED} → OWNER_APPROVED →
SENT_TO_CUSTOMER on bridge success; CF1 truth-guard refuses approve-before-finalize
without --skip-finalize. reject/edit: {AWAITING, CUSTOMER_FINALIZED} → OWNER_REJECTED
/ OWNER_EDITED.

## 4. Existing duplicate/replay/idempotency behavior

- cf-router inbound dedup: sha256(chat_id∥message_key), TTL 3600s, stateful, only
  when a native message id exists (actions.py:6382-6400).
- create-catering-lead: idempotent on `original_message_id` ALONE (:504-527; its
  docstring claims (phone, message_id) — drift noted). An amendment has a different
  message_id → treated as new inquiry → the duplicate-lead mode.
- apply-catering-owner-decision: same-#code replay → tail-scan for
  `catering_quote_sent` → idempotent short-circuit, no re-send (:533-565).
- finalize: idempotent on `last_finalize_message_id` (:616-620).

## 5. Proposed immutable raw-capture record + retention

**Design decision: SIDECAR store, not a CateringLead field.** `CateringLead` is
`extra="forbid"`; adding a field breaks rollback readers (a lead written with the new
field bricks the store under the previous schema — the exact m2-class hazard flagged
at #622 review). A sidecar (`/opt/shift-agent/state/catering-amendments.json`, own
Pydantic store + FileLock, keyed by lead_id) is rollback-clean: old code never opens
it; PR-R1's lock-init pattern covers ownership.
Record (append-only; raw_text never mutated; only `status` transitions):
`{amendment_id "A####", lead_id, ts, message_id, raw_text ≤1000 (mirrors
raw_inquiry precedent), source: f7_branch_b|dispatcher, status:
captured|proposed|unparseable|applied|rejected_by_owner|superseded|out_of_window,
proposal?: CateringAmendmentProposal, applied_ts?, owner_decision_ref?}`.
Capture happens FIRST in Branch B (before the canonical reply) so the data-loss half
closes even if extraction never runs. Retention: rides the lead store's lifecycle —
no new retention regime, no new PII class (same content class as raw_inquiry).

## 6. Hermes proposal schema

`CateringAmendmentProposal` — `extra="ignore"` (LLM-output shape, deployed pattern):
optional deltas mirroring `CateringLeadExtractedFields` (headcount 1-10000,
event_date YYYY-MM-DD + calendar validator, event_time HH:MM, menu_preferences,
dietary_restrictions, delivery_or_pickup, budget_hint_usd) +
`unsupported_fragments: list[str]` + `ambiguous: list[{fragment, candidates}]` +
`confidence: float`. **Confidence is telemetry ONLY — it is excluded from every
authorization decision by construction: no threshold gates anything; the ONLY
authorization is the existing owner `#XXXXX` approval.** Extraction runs in a
`catering_amendment` SKILL (mirrors parse_catering_inquiry: SKILL → JSON via stdin →
validated script). Hermes never touches the lead or the sidecar directly.

## 7. Deterministic field validation + transition rules

Validation reuses the exact `CateringLeadExtractedFields` validators (same ranges,
same calendar check) applied to proposed deltas; unknown/invalid delta → that delta
rejected, fragment preserved as unsupported. Transition rules:
- Amendment capture allowed while lead ∈ {AWAITING_OWNER_APPROVAL,
  CUSTOMER_FINALIZED, OWNER_EDITED}; later statuses → record status=out_of_window +
  polite deterministic customer reply + owner notice (no auto-apply after quote/
  deposit exist).
- Capture NEVER changes lead status. A validated proposal re-issues the owner card
  (same lead, SAME existing #code) with the diff section.
- **Apply happens only at owner approve** (the idempotent commit): approve applies
  proposed deltas into `lead.extracted` atomically under the leads lock in the same
  operation that transitions the status, so finalize-card and deposit computations
  downstream naturally read amended values. Reject → amendment rejected_by_owner,
  lead untouched.

## 8. Before/after diff shown to the owner

Appended section on the existing card templates (rendered by the deterministic
script from validated data — never LLM prose):
```
AMENDMENTS PENDING (2):
  headcount : 235 → 280      ("actually 280 not 235", 07-19 14:02)
  dietary   : — → vegetarian ("make it vegetarian", 07-19 14:05)
  UNPARSED  : "my cousin might bring a cake" (kept verbatim; not applied)
Approve #K7Q2M = original + the changes above. Reject #K7Q2M = keep original.
```
Every changed field shows before → after with its raw fragment; unchanged fields
listed; unparsed/ambiguous fragments surfaced verbatim.

## 9. Unsupported / ambiguous / conflicting-field behavior

- Unsupported fragments: preserved on the record + surfaced verbatim on the card.
  Never silently discarded (contract requirement).
- Ambiguous (e.g. two candidate dates): NOT proposed as a delta; surfaced on the
  card + one deterministic clarification question to the customer.
- Conflicting sequential amendments: later supersedes earlier (earlier record
  status=superseded, BOTH retained; card shows the effective set).
- Conflict inside one message (two headcounts): treated as ambiguous.
- Amendment vs pending owner edit: both shown on the card; owner decision wins.

## 10. Idempotency key + repeated-message rules

- Capture key: `(lead_id, message_id)`; native-id absent → sha256(normalized text)
  fallback (mirrors cf-router dedup derivation). Replay → return existing
  amendment_id, no new record.
- Apply key: amendment status gate (only `proposed` applies) + the existing
  owner-decision replay tail-scan; a replayed approve after apply → idempotent
  short-circuit, no double-apply (statuses `applied` are terminal for the record).

## 11. Audit trail + privacy/logging

New LogEntry variants (union additions, append-only log):
`catering_amendment_captured {lead_id, amendment_id, message_id, source, text_len}`,
`catering_amendment_proposed {…, fields_changed: [names], deltas: numeric/enum values}`,
`catering_amendment_applied / _rejected / _out_of_window / _unparseable`.
Privacy: RAW TEXT never enters decisions.log (it lives on the sidecar, same content
class as raw_inquiry on the lead today); audit rows carry ids, field NAMES, and
business values (headcount numbers/dates) only; no phone numbers beyond what existing
rows carry; the existing `cf_router_raw_body` 400-char capture is unchanged.

## 12. Failure + rollback behavior

- Capture write failure (Branch B): fail-VISIBLE — audit `…capture_failed` row +
  fall through to the LLM dispatcher (conversation, not silent drop). Never blocks
  the canonical reply path fatally.
- Hermes extraction failure/timeout: record stays `captured`; raw text safe; a
  re-extraction sweep or next inbound retriggers; nothing lost, nothing applied.
- Validation failure: `unparseable`, fragment surfaced on card.
- Apply failure mid-commit: single atomic store write under the leads lock —
  all-or-nothing; owner replay safe (item 10).
- **Deploy rollback: clean by design** — sidecar + new audit variants only; old code
  never reads the sidecar; `_UnknownLogEntry` shim tolerates new audit rows; zero
  CateringLead schema change (the decisive reason for the sidecar).

## 13. Synthetic replay matrix (test plan)

create → amend(headcount) → proposed → card diff → approve → applied + quote/deposit
from amended values · amend(date-only) via dispatcher row → captured (no duplicate
lead) · amend after SENT_TO_CUSTOMER → out_of_window + notices · duplicate amendment
message (same message_id) → single record · two sequential conflicting amendments →
supersede chain, card shows effective set · in-message conflict → ambiguous →
clarification · owner rejects with pending amendment → lead intact, record
rejected_by_owner · replayed approve after applied → idempotent no-op · extraction
timeout → captured-only, raw retained · capture-write failure → visible audit +
LLM fallthrough · Branch-B capture with F7 proposal branch active → proposal paths
unaffected · stale/terminal lead → polite decline, no record mutation of old leads.

## 14. Proposed PR-R2 files + scope

- `src/platform/schemas.py` — sidecar store schema + `CateringAmendmentProposal` +
  LogEntry variants (NO CateringLead change)
- NEW `src/agents/catering/scripts/amend-catering-lead` — capture + propose-validate +
  card-re-issue + apply-at-approve entry points (the exact script hooks.py:5011 names)
- NEW `src/agents/catering/skills/catering_amendment/SKILL.md` — Hermes extraction →
  proposal JSON via stdin (mirrors parse_catering_inquiry)
- `src/plugins/cf-router/hooks.py` — Branch B capture call (before canonical reply)
- `src/agents/catering/skills/catering_dispatcher/SKILL.md` — amendment routing row
  (active-lead + non-proposal/finalize text → amendment path; closes the
  duplicate-lead mode)
- `src/agents/catering/scripts/apply-catering-owner-decision` — apply-at-approve hook
  (idempotent commit of proposed deltas)
- card templates — AMENDMENTS PENDING section
- tests — the full matrix above (~300-400 LOC tests; product ~250-350 LOC)
**Scope note:** hooks.py + dispatcher SKILL + apply-decision are PRODUCT routing/
approval surfaces — PR-R2 is a product PR and reopens focused product review by
definition (expected; it was never under the ops umbrella).

## 15. Findings that bear on implementation safety

- **(Closed by design) CateringLead forbid-schema rollback hazard** → sidecar store.
- **(Requires decision) date-only amendments cannot reach Branch B** — two closure
  options: (a) dispatcher SKILL amendment row only (Hermes classifies — pure
  Hermes-maximal; covers the LLM path but Branch-B-suppressed messages with signals
  still need the Branch-B capture), or (b) additionally add `date/reschedule` tokens
  to the F7 follow-up signal set (small deterministic widening of an active-lead-
  scoped condition — arguably permitted as "structured-token/state guard", but it IS
  a keyword addition; flagged for the reviewer's ruling). Recommendation: (a) + (b)
  minimal, because Branch B fires before the dispatcher for signal-bearing texts.
- **(Out of scope, recorded)** the OWNER_EDITED "drafter" gap and the
  `create-catering-lead` idempotency docstring drift → tasks/DEFERRED.md candidates.
- **(Interaction)** `_should_start_new_lead_over_active` (hooks.py:4932-4940) can
  still mint a new lead on strong new-inquiry signals over CUSTOMER_FINALIZED/
  OWNER_APPROVED — correct for genuine new events; the amendment row must be ordered
  AFTER proposal/finalize checks and BEFORE new-lead-over-active to avoid eating
  genuine new inquiries (ordering cell added to the replay matrix).
- **No finding makes implementation unsafe** under the locked pipeline + sidecar
  design; the two rulings requested are the date-token option and confirmation that
  PR-R2 proceeds as a product PR under focused product review.

---

# PR-R2A DESIGN DELTA (pre-implementation record, 2026-07-19; reviewer-mandated)

Split ruling accepted: **R2A = immutable capture only** (no Hermes extraction, no
dispatcher/SKILL changes, no date keywords, no owner-card changes, no approval-code
creation, no `lead.extracted` mutation, no deposit/quote recomputation, no
OWNER_EDITED handling, no `cf_router_raw_body` backfill — those rows are forensic
evidence only). **R2B remains planned-unauthorized**; its date-only ruling (semantic
dispatcher row, no deterministic date tokens, fallthrough observability, never
silently choosing between multiple eligible leads) is recorded for the R2B plan.

> **REVISION 2 (2026-07-19, reviewer's binding refinements):** sections 1-5 below
> are REVISED per the six-point ruling; the original text they replace is
> superseded. R2A implementation authorized only against THIS revision.

## 1. Exact sidecar schema — REVISED (write-side strict, read-side preservation-safe)

```python
class CateringAmendmentRecord(BaseModel):        # WRITE-side model; append-only
    model_config = ConfigDict(extra="forbid")    # strict for records R2A CREATES
    amendment_id: str            # "A" + zero-padded seq, assigned under store lock
    lead_id: str                 # canonical CateringLead id (REQUIRED — no orphans)
    sender_ref: str              # canonical identity key when resolvable; else the
                                 # raw phone/chat_id exactly as the router resolved it
    source_transport: str        # e.g. "whatsapp" — from the gateway envelope
    message_id: str              # native inbound id; "" when transport gives none
    envelope_fingerprint: str    # canonical transport-envelope fingerprint:
                                 # sha256(stable sender id ∥ provider timestamp ∥
                                 # body length ∥ raw_text_sha256); "" if underivable
    raw_text: str                # BOUNDED PREFIX, max 16,384 chars (NOT "complete")
    raw_text_truncated: bool     # True iff inbound exceeded the bound
    raw_text_original_length: int
    raw_text_sha256: str         # sha256 of the COMPLETE inbound text, computed
                                 # BEFORE truncation (integrity + fallback key)
    captured_at: datetime
    source: Literal["f7_branch_b"]
    status: str                  # R2A writes "captured"; READ side accepts ANY
                                 # string (forward-compat, see store representation)
    base_extracted_sha256: str   # sha256(canonical JSON of lead.extracted at capture)
    proposal_ref: Optional[str] = None           # R2B-only, ships as None
    approval_code_ref: Optional[str] = None      # R2B-only (existing #code ref;
                                                 # R2A/R2B never mint codes)
    disposition: Optional[str] = None            # R2B-only
    disposition_ts: Optional[datetime] = None    # R2B-only
```
**Store representation — preservation-safe (reviewer-mandated forward-compat,
a documented deliberate divergence from the extra="forbid" state-store norm):**
the on-disk store is `{"schema_version": 1, "next_seq": N, "records": [...]}` and
R2A loads `records` as **tolerant dictionaries**. **Preservation guarantee =
SEMANTIC preservation** (terminology corrected per reviewer — the store parses JSON
and re-serializes, so byte-for-byte is not claimed): every existing unknown field
remains present; unknown statuses unchanged; nested future fields unchanged;
string/boolean/null/integer/structured values retain their types and values; no
existing record is normalized through a strict schema; appending an R2A record
never strips or rewrites future R2B content. R2A validates ONLY the record it is
about to APPEND (via the strict write-side model above) and **fails visibly rather
than rewrite anything it cannot round-trip semantically**. Enforced by test: an R2A writer appending
to a store seeded with synthetic future statuses + unknown fields must leave every
pre-existing byte of those records unchanged.

**R2B obligation recorded now:** a record with `raw_text_truncated=true` MUST be
routed to manual handling in R2B — the stored prefix is never treated as the
authoritative amendment.

## 2. Idempotency-key definition — REVISED (three-tier hierarchy)

All duplicate detection AND insertion run INSIDE the sidecar lock.
- **Primary:** `(source_transport, lead_id, native_message_id)` when a native id
  exists — a match at ANY status → idempotent return of the existing amendment_id.
- **Secondary:** the canonical **transport-envelope fingerprint** (stable sender id
  ∥ provider timestamp ∥ body length ∥ complete-text sha256) — covers transports/
  replays where the native id is absent or unstable; a fingerprint match at ANY
  status → idempotent return.
- **Final fallback (best-effort, documented):** `(lead_id, sender_ref,
  raw_text_sha256)` within a **bounded replay window of 24 hours** (constant,
  documented in-module), checked against matching records **across ALL statuses**
  inside that window. Outside the window, identical text is a new amendment.

## 3. Lock path + lock-order analysis

- Dedicated lock: `/opt/shift-agent/state/catering-amendments.json.lock`
  (safe_io.FileLock; same fcntl domain as the fleet).
- **Precreated by the deploy-script's existing `initialize_approval_code_lock`-
  adjacent init** (one added line using the SAME O_EXCL/0660/shift-agent contract +
  dual-identity verify) — the PR-R1 root-first lesson applied at birth. (Deploy
  script edit is disclosed R2A scope; capture's only writer is the gateway
  [shift-agent] so even without precreation service-first order holds, but we do
  not rely on ordering luck.)
- **Lock order: the sidecar lock is a LEAF.** Capture reads the lead LOCKLESSLY
  (same as every existing reader, e.g. find_active_catering_lead_by_sender), then
  acquires ONLY the sidecar lock for re-check+append+write. It is NEVER held with
  the leads lock, the code-pool lock, or any other lock — zero interaction with
  PR-R1's global→pool ordering. No lock is held across any Hermes/model call,
  notification, or network work (none exist on the capture path at all).

## 4. Atomic-write, crash-recovery + FAILURE BEHAVIOR — REVISED (no LLM fallthrough)

Writes: `safe_io.atomic_write_json` (temp + rename + fsync) under the sidecar
FileLock; reads via tolerant load (see §1) with clean-load assertion. Interrupted
writes cannot corrupt (rename atomicity); orphan temps swept by the existing
`sweep_orphan_temps`. Duplicate delivery under concurrency: the lock serializes;
the loser's in-lock re-check finds the winner's record → idempotent return.

**On ANY capture failure (lock unavailable, load failure, validation failure,
write failure, filesystem-contract rejection):**
- the existing store is PRESERVED (never overwritten on a failed load/validate);
- metadata-only failure observability is emitted
  (`catering_amendment_capture_failed {lead_id, reason, text_len}` — ids and
  lengths only, never raw text in any general-purpose log);
- NO lead is created or mutated;
- routing does NOT continue to generic LLM handling — the intercept returns a
  **deterministic retry response** to the customer (fixed template: the update was
  not recorded, please resend shortly) and suppresses the LLM;
- capture success is NEVER claimed (no `catering_amendment_captured` row, no
  canonical "reply here to adjust" wording that implies the adjustment was taken).

## 4b. Filesystem contract — NEW (explicit, reviewer-mandated)

- Sidecar data path: `/opt/shift-agent/state/catering-amendments.json` —
  owner/group `shift-agent:shift-agent`, mode `0640` (safe_io atomic-write default;
  re-established on every atomic replacement because the temp file is created by
  the writer with that mode and renamed into place).
- Lock path: `/opt/shift-agent/state/catering-amendments.json.lock` — precreated by
  the reviewed deploy initializer under its existing contract
  (`shift-agent:shift-agent`, `0660`, O_EXCL creation, dual-identity verification).
- **R2A writer identity: `shift-agent` ONLY** (the gateway process via cf-router).
  No root-side writer exists in R2A, and **no claim is made that root and the
  gateway can both safely replace the sidecar data file** — atomic replacement by
  root would re-own the file `root:0640` and lock the gateway out of subsequent
  writes; any root-side writer requires separate R2B review.
- Capture-time filesystem validation (each write, before use; all failures follow
  §4's failure behavior): pathname must not be a symlink (lstat-first), must be a
  regular file when present, owner/group must be `shift-agent:shift-agent`, mode
  must be in {0640, 0660}; the PARENT directory must be the canonical state dir —
  not a symlink, owned `shift-agent:shift-agent`, not world-writable. Absent data
  file → created via the atomic temp+rename path (never `open(path, "w")` in
  place). Unsafe anything → refuse, preserve, retry-response.

## 5. Retention + privacy policy

Records ride the lead-store lifecycle — no separate purge/retention regime in R2A
(terminal leads are retained today; their amendment records retain alongside;
encrypted daily backups already cover the state dir). Privacy: complete raw_text
lives ONLY in the sidecar (same content class as `raw_inquiry` on the lead);
general-purpose logs receive `text_len` and ids only — **no raw amendment text in
decisions.log or journals**; the pre-existing `cf_router_raw_body` 400-char row is
unchanged and explicitly NOT treated as authoritative.

## 6. Exact capture entry points (R2A)

EXACTLY ONE: `hooks.py` F7 Branch B follow-up-suppression arm (:5077-5085 today),
invoked immediately BEFORE `send_canonical_followup_reply` — capture-first-then-
reply. The proposal-selection/request arms and all other routes are untouched.
Capture is non-blocking for routing: success → canonical reply proceeds as today;
failure → audit + `return None` (LLM fallthrough).

## 7. Behavior when no canonical lead resolves

Structurally unreachable at the R2A entry (Branch B executes only with a resolved
`active_lead`). Defensive contract anyway: missing/empty lead_id → NO record is
written (no orphans, per requirement), `catering_amendment_capture_failed`
(reason=no_lead) audited, existing behavior proceeds unchanged.

## 8. Synthetic duplicate + concurrency matrix (R2A tests)

same native id twice sequential → one record · same native id concurrent (two
processes, shared lock — Linux multiprocess harness reusing PR-R1's pattern) → one
record, loser idempotent · same text no-native-id twice → one record · same text
different leads → two records · different texts same lead → two records, seq
monotonic · crash-sim between lock and write (kill worker) → store loads clean,
no partial record · corrupt store → fail-visible + deterministic retry response +
store preserved (NO fallthrough, NO rewrite) · restart persistence → dedup survives
(store IS the dedup state) · every capture-failure mode (lock, load, validate,
write, filesystem-contract rejection) → metadata-only audit + retry response, no
lead created/mutated, no success claim · **forward-compat preservation: R2A writer
appends to a store seeded with synthetic FUTURE statuses + unknown fields →
pre-existing records byte-unchanged** · envelope-fingerprint dedup (no native id) ·
24h-window fallback dedup across ALL statuses, and expiry outside the window ·
truncation cell: >16,384-char inbound → prefix + truncated flag + original length +
complete-text hash · Windows-runnable equivalents via the fcntl-stub toolkit where
possible.

## 9. Exact proposed R2A files

- `src/platform/schemas.py` — the two models above + 2 LogEntry variants
  (`catering_amendment_captured` {lead_id, amendment_id, message_id, source,
  text_len}, `catering_amendment_capture_failed` {lead_id, reason})
- NEW `src/platform/catering_amendments.py` — sidecar repository (load/append/
  idempotency; flat-installed)
- `src/plugins/cf-router/hooks.py` — the single Branch B capture call (~15 LOC)
- `src/agents/shift/scripts/shift-agent-deploy.sh` — guarded flat-install line for
  the new module + sidecar-lock precreation line inside the existing init function
- NEW `tests/test_catering_amendment_capture.py` (+ Linux concurrency cases)
NOTHING else. Estimated product LOC ≈ 120-160.

## 10. Rollback behavior

Old code never opens the sidecar or its lock → both are inert files after any
rollback; the guarded deploy block removes the module for pre-R2A tarballs; records
persist harmlessly for a future re-deploy; no migrations; new audit variants
tolerated by the `_UnknownLogEntry` shim. No CateringLead change exists to unwind.

## 11. Exclusion confirmation + CORRECTED scope statement

**Scope, stated precisely: R2A closes durable capture ONLY for the known Branch-B
suppression path.** It does NOT fix: the date-only F7 bypass; dispatcher
duplicate-lead creation; Hermes extraction; owner approval or apply; stale quote or
deposit behavior; OWNER_EDITED. Those remain open production defects until R2B (or
their own deferred tracks).

R2A contains NO dispatcher/SKILL change, NO Hermes/extraction call, NO approval-code
creation or reference, NO `lead.extracted` or any lead mutation, NO owner-card or
template change, NO deposit/quote logic, NO OWNER_EDITED handling, NO backfill from
`cf_router_raw_body` (forensic evidence only), NO date-keyword additions. The only
behavioral deltas in production: (a) Branch-B-suppressed amendment texts are durably
captured before the unchanged canonical reply; (b) on capture FAILURE the customer
receives the deterministic retry response instead of the canonical reply (per §4 —
no silent success claims, no LLM fallthrough).

## Approvals log

- 2026-07-19: reviewer authorized this preflight READ-ONLY under the locked
  architecture. Implementation NOT authorized; extraction NOT enabled; no data
  touched. PR-R3/R4, Pushover, runtime/data changes: held.
- 2026-07-19: reviewer accepted the preflight; approved the sidecar decision; split
  PR-R2 into R2A (capture-only, authorized after this design delta is recorded) and
  R2B (planned, UNAUTHORIZED until R2A is merged + reviewed in production or an
  approved test environment); date-only ruling for R2B recorded (semantic dispatcher
  row, no date tokens, fallthrough observability). This design delta = the required
  pre-implementation record; returned to reviewer before any code.
