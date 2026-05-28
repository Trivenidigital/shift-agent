# P0 #2 — Severity-tiered Flyer visual QA + warn-only draft delivery path

**Date:** 2026-05-28
**Branch:** `plan/p0-2-severity-tiered-qa-2026-05-28` (off `origin/main` HEAD `f7ad477`)
**Drift-check tag:** `extends-Hermes`
**New primitives introduced:** None. Adds one new project status (`delivered_with_warning`), one new field on `FlyerVisualQAReport` (`severity`), one helper module function (`classify_qa_severity`), one customer-copy template + formatter, and two `LogEntry` audit-row variants — all on existing substrate.

---

## 1. The problem (customer view)

Today's Flyer Studio QA is binary: any `blockers: list[str]` from `visual_qa.run_visual_qa()` flips `status="failed"`, autorepair retries once, and if autorepair fails the project lands in `manual_edit_required` with `reason_code="visual_qa_failed"`. The customer gets no draft.

**The empirical failure class (F0108, F0109, 2026-05-28):**

| Project | Blocker observed | Customer reality |
|---|---|---|
| F0108 | `visible wrong business/brand: Laksmi'S Kitchen` | The flyer correctly says *Lakshmi's Kitchen* in the headline; a single second instance is mistyped as *LAKSMI'S KITCHEN*. The draft is shippable; the customer can spot the typo and reply with a fix in 5s. |
| F0109 | `missing required visible fact: location, item:4:name, item:5:name` | Real defects (truncated address, duplicated/garbled item names). Customer would not ship as-is. Today's behavior (manual queue) is correct for this case. |

Both cases hit the same binary `failed` path. F0108 is fail-closed for a customer-recoverable defect. The product loses the "99% autonomous customer-request completion" claim every time we treat F0108-shaped issues like F0109-shaped issues.

**The fix:** classify blocker strings into `pass / warn / block` severity. `pass` ships unchanged. `warn` ships the draft to the customer with a short correction prompt (no human-in-the-loop). `block` keeps today's manual-queue behavior. Autorepair stays in the loop before manual.

---

## 2. Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp customer-direction send | yes — existing `send-flyer-package` operator-script + Hermes WhatsApp bridge | reuse; warn-tier copy goes through same send path |
| Vision OCR + blocker extraction | yes — existing `run_visual_qa` via OpenRouter `gpt-4o-mini` | reuse; severity is a pure classifier over existing `blockers: list[str]` |
| Customer-copy lint surface | yes — existing `customer_copy_policy.py` (`BANNED_CUSTOMER_COPY_TERMS`, `FORBIDDEN_COMPLETION_VERBS`, `scan_customer_text`) | reuse; warn-tier template must pass both lints |
| Audit chain | yes — `log-decision-direct` + `LogEntry` discriminated union in `schemas.py` | reuse; add `_FlyerQASeverityClassified` + `_FlyerWarnTierDelivered` variants |
| Atomic state writes | yes — `safe_io.atomic_write_json` + `bridge_post` chokepoint | reuse; severity-branch state writes go through these helpers |
| Customer revision reply routing | yes — existing `revising_design` status + active-project lookup in `cf-router/actions.py` | reuse; revision from `delivered_with_warning` reuses same path |
| Autorepair classification + retry | yes — `classify_flyer_qa_for_autorepair` + autorepair loop in `recovery.py` (PR #308) | reuse; severity branch slots AFTER autorepair-failed |

**Awesome Hermes Agent ecosystem check:** No external Hermes/community skill covers Flyer-specific QA severity. All substrate is in-tree. Adding the severity classifier upstream to Hermes would not help — the blocker strings are this project's domain language, not a Hermes capability.

---

## 3. End-to-end flow (post-PR), step by step

**Architecture decision 2026-05-28 (operator):** the canonical preview send mechanism today is `send_flyer_concept_previews()` at `src/plugins/cf-router/actions.py:3995`, invoked by cf-router AFTER `generate-flyer-concepts` subprocess returns (`actions.py:3948`). The script itself doesn't send — it only writes state. Warn-tier delivery preserves this division: `generate-flyer-concepts` writes the `delivered_with_warning` state + warning payload + audit row; cf-router's post-subprocess branch reads the new status and calls `send_flyer_concept_previews()` with a warn-tier `customer_text` override. The script doesn't gain a new outbound; the cf-router send mechanism stays canonical for both pass + warn paths.

`send-flyer-package` is **not** used for warn-tier delivery. That script is final-package scoped and enforces `status == "finalizing_assets"` at line 139 → SystemExit on concept-preview state.

1. `[Hermes]` Customer WhatsApp inbound (image or text) → Hermes gateway routes to cf-router.
2. `[Hermes]` `cf-router/actions.py` identifies active project (or creates one) + writes intake audit.
3. `[Hermes]` `create-flyer-project` / fields collected → status → `awaiting_assets` / `generating_concepts`.
4. `[Hermes]` cf-router invokes `generate-flyer-concepts` subprocess (`actions.py:3948`).
5. `[Hermes]` `generate-flyer-concepts` produces concept artifacts + calls `run_visual_qa()` → `FlyerVisualQAReport` with `blockers: list[str]` + `status: "passed" | "failed" | "provider_unavailable"`.
6. **`[net-new]`** `classify_qa_severity(report, project)` → `Literal["pass", "warn", "block"]` on the same report (new `severity` field).
7. `[Hermes]` On `failed` + block-tier OR warn-tier: existing autorepair loop (`recovery.py:classify_flyer_qa_for_autorepair` + retry) runs. Re-runs QA → classify again.
8. **`[net-new]`** Severity branch INSIDE `generate-flyer-concepts` writes state only — no send:
   - `pass`: today's path — writes `awaiting_concept_selection` / `awaiting_final_approval`.
   - `warn`: NEW path — writes `delivered_with_warning` + populates `project.warning` payload + writes `_FlyerWarnTierDelivered` audit row.
   - `block`: today's path — writes `manual_edit_required` with `reason_code="visual_qa_failed"`.
9. `[Hermes]` cf-router's post-subprocess branch (around `actions.py:3948-3995` today) reads the new status. **`[net-new]`** New conditional: if status == `delivered_with_warning`, build warn-tier customer text from `project.warning.blockers` via `build_warn_tier_customer_text()`, then call `send_flyer_concept_previews(chat_id, project_id, customer_text=warn_text)`. Otherwise today's pass-path body runs as-is.
10. `[Hermes]` `send_flyer_concept_previews()` ships preview images + customer text through `bridge_send_media`. **`[net-new]`** Function gains an optional `customer_text: Optional[str] = None` parameter (defaults to existing pass-path text).
11. **`[net-new]`** New `_FlyerQASeverityClassified` audit row at step 6 (from the script); `_FlyerWarnTierDelivered` audit row at step 8 (also from the script, BEFORE the send fires — it records the decision to deliver-with-warning, not the bridge result). Existing bridge-send audit rows cover step 10 unchanged.
12. `[Hermes]` Customer reply with corrections → `cf-router/actions.py` active-project lookup → routes to existing `revising_design` flow via `FLYER_TRANSITIONS`.
13. **`[net-new]`** `FLYER_TRANSITIONS` matrix extended: `delivered_with_warning → revising_design` allowed.

**Step count:** 13 total. `[Hermes]`: 8. `[net-new]`: 5 (steps 6, 8, 9-conditional, 10-param, 11, 13). The classifier dictionary + warn-tier copy are infrastructure, not steps.

**Red-flag check:** 5/13 = 38% net-new, under half. Within Hermes-first norms.

---

## 4. Drift-rule self-checks (read deployed code first)

| Work type | File read | Evidence |
|---|---|---|
| Schema work | `src/platform/schemas.py` lines 1707-1735 | `FlyerVisualQAReport` already has `extra="forbid"`, `status: FlyerVisualQAStatus`, `blockers: list[str]`. Adding `severity: Literal["pass","warn","block"] = "pass"` is one field, additive, backward-compatible. `FlyerManualReview` adjacent; not changed. |
| Visual QA module | `src/agents/flyer/visual_qa.py` lines 335-554 | `run_visual_qa()` produces blocker strings via `_unrequested_operational_claim_blockers`, `visible_wrong_brand_blockers`, locked-fact loop, source-contract loop, placeholder/regional/quality-note checks. Severity classifier is pure-function over the resulting `report.blockers` strings. |
| Script proposal | `src/agents/flyer/scripts/generate-flyer-concepts` lines 540-841 | QA call at line 578 (`run_visual_qa`), autorepair loop lines 621-799 (`classify_flyer_qa_for_autorepair`), failure-path manual_edit_required write at lines 823-841 (confirmed by reviewer 3 against `f7ad477`). The 3-way severity branch slots in at lines ~823 BEFORE the manual_edit_required write. |
| Customer-copy lint | `src/agents/flyer/customer_copy_policy.py` lines 1-103 | `BANNED_CUSTOMER_COPY_TERMS` (includes `operator`, `manual_edit_required`, `reason_code`, `provider`), `FORBIDDEN_COMPLETION_VERBS` (`sent`, `confirmed`, `applied`, `scheduled`, `processed`, etc.). Warn-tier copy must avoid all. `scan_customer_text` is the existing lint entry point. NOTE: `scan_customer_text` does NOT call `lint_no_unverified_completion` (intentional peers, docstring 75-78) — warn-tier template must be tested against BOTH. |
| Recovery / autorepair | `src/agents/flyer/recovery.py` (1020 LOC; PR #308 autorepair) | `classify_flyer_qa_for_autorepair`, `plan_flyer_autorepair`, `repair_instruction_is_safe` already exist. Severity branch is downstream of autorepair, not a replacement. `classify_stale_manual_project` (lines 451-502) scans ONLY `status == "manual_edit_required"` + `manual_review.status == "queued"` — `delivered_with_warning` will NOT trigger watchdog escalation (clean by design). |
| Send path | `src/plugins/cf-router/actions.py` lines 3934-3995 — `send_flyer_concept_previews()` | Canonical concept-preview send chokepoint. Invoked by cf-router after `generate-flyer-concepts` subprocess returns (line 3948). Used by both pass + warn paths. Warn-tier adds an optional `customer_text` param. `send-flyer-package` is **not** used here — it is final-package scoped (`status == "finalizing_assets"` check at line 139) and would SystemExit on concept-preview state. |
| State Literal + transitions | `src/platform/schemas.py` lines 637-650 (`FlyerWorkflowStatus`) and 850-859 (`FLYER_TRANSITIONS`) | Status Literal is named `FlyerWorkflowStatus` (NOT `FlyerProjectStatus` — corrected from earlier draft). Current members: intake_started, collecting_required_info, awaiting_assets, manual_edit_required, generating_concepts, awaiting_concept_selection, revising_design, awaiting_final_approval, finalizing_assets, delivered, completed, closed_no_send. `FLYER_TRANSITIONS` is an explicit matrix; `is_flyer_transition_allowed` enforces it. Adding `delivered_with_warning` requires explicit inbound + outbound edges in the matrix (see §5b). Plus `__all__` exports at lines 4889 + 4894 reference the type alias. |
| cf-router send mechanism | `src/plugins/cf-router/actions.py` lines 3934-4076 | `_send_initial_processing_ack` + subprocess invoke at 3948 + `send_flyer_concept_previews` at 3995 + `bridge_send_media` at 4060. This is the post-subprocess branch where the warn-tier send-driver lives. New code adds one conditional + customer_text plumbing. |
| Cockpit filter | `web/frontend/src/sections/FlyerAdmin.tsx` lines 594-711 (Manual Queue filter pattern) | The Manual Queue tab has filter pills (`queueFilterReason`, `queueFilterPhone`, etc.). The Projects tab has NO existing project-status filter pill row — Commit 5 must BUILD a new filter, not extend an existing one. Manual Queue filter pattern is the closest template to mirror. `delivered_with_warning` projects are OUTSIDE `manual_edit_required` so the Manual Queue endpoint will not surface them by default. |

**Drift findings caught by reviewer pass (incorporated above):** (1) `FlyerWorkflowStatus` not `FlyerProjectStatus`; (2) `FLYER_TRANSITIONS` is the state-gate, not a cf-router source-status allowlist; (3) canonical concept-preview send is `send_flyer_concept_previews` in cf-router, not `send-flyer-package`; (4) cockpit Projects tab has no existing status filter row. All findings rolled into the plan. Tag remains `extends-Hermes`.

---

## 5. Severity classifier — first-cut dictionary

The classifier is a pure function `classify_qa_severity(report: FlyerVisualQAReport) -> Literal["pass", "warn", "block"]`. It matches `report.blockers` strings against pattern tables.

| Blocker string pattern | Severity | Reason |
|---|---|---|
| (no blockers) | `pass` | Ship unchanged |
| `placeholder text is visible in generated flyer` | `block` | Embarrassing draft, not customer-recoverable |
| `English-only flyer contains regional/non-English script` | `block` | Policy violation |
| `unrequested operational claim visible: {claim}` | `block` | Misleads customer (e.g., unrequested "free delivery" claim) |
| `ocr/vision text unavailable for generated artifact` | `block` | Can't verify safety; substrate failure (vision OCR failures arrive as a provider_note appended to THIS blocker, via `visual_qa.py:483` — they do not appear as a separate `"vision OCR failed: ..."` blocker. Earlier draft of this row was incorrect — reviewer 3 caught.) |
| `replaced source text still visible: {forbidden}` | `block` | Old brand/phone bleeding through |
| `visible wrong business/brand: {name}` where `name` is a Levenshtein-close variant of the project's `business_name` (typo) | `warn` | Recoverable (F0108 case) |
| `visible wrong business/brand: {name}` where `name` is distinct (different word) | `block` | Wrong customer entirely |
| `missing required visible fact: business_name` | `block` | Identity bleed risk |
| `missing required visible fact: location` | `warn` | Customer recognizes address by reading + can reply with correction (F0109 case for one missing fact, but combined with item-name corruption it crosses to block — see combination rule below) |
| `missing required visible fact: contact_info` | `warn` | Recoverable |
| `missing required visible fact: schedule` | `warn` | Recoverable |
| `missing required visible fact: promotion_end` | `warn` | Recoverable |
| `missing required visible fact: item:N:name` | `warn` | Single mistyped item is recoverable |
| placeholder-keyword in quality_notes (e.g., "garbled", "unreadable") | `block` | Vision saw corruption beyond a single typo |

**Brand-typo gate (operator decision 2026-05-28: distance alone is insufficient — gate with normalized-token-overlap + prefix evidence).** Single Levenshtein-close hit does NOT classify warn. The decision tree is AND-of-three:

1. `editdistance(normalize(extracted_brand), normalize(project.business_name)) <= 2`, AND
2. `len(tokens(extracted) & tokens(project_brand)) / len(tokens(project_brand)) >= 0.5` (≥50% token overlap), AND
3. `common_prefix_len >= 4` OR `token_overlap >= 0.75` (prefix-OR-strong-overlap evidence).

**Boundary operator pin (reviewer 2 #5):** all comparisons in the gate use `>=` (not `>`) — the worked F0108 example sits exactly on the 0.5 overlap boundary and must classify warn, not block. Implementation must include a test fixture pinning this: `overlap == 0.5` AND `editdistance == 2` AND `common_prefix_len == 4` → warn (boundary). Also a single-token-brand fixture: `tokens(project_brand)` of size 1 (e.g., `"Lakshmi"`) — overlap denominator = 1, so any non-match → 0.0, any match → 1.0; the 0.5 boundary never arises but the gate must still terminate correctly.

Worked examples:
- `Laksmi'S Kitchen` vs `Lakshmi's Kitchen`: distance 1, tokens {laksmis, kitchen} vs {lakshmis, kitchen} overlap=0.5 (`kitchen` matches), prefix=`Laks`=4 chars → **all 3 pass (with `>=`) → warn**.
- `Laxmi Mart` vs `Lakshmi's Kitchen`: tokens {laxmi, mart} vs {lakshmis, kitchen} overlap=0 → **gate 2 fails → block** (also distance >2 if calculated, so gate 1 fails too).
- `Aria` vs `Aria` (4-char brand, single-typo `Arla`): distance 1, tokens {arla} vs {aria} overlap=0 → **gate 2 fails → block**. Short brands stay block-by-default, which is correct.

Implementation: `_is_brand_typo(extracted: str, project_brand: str) -> bool` in `visual_qa.py`, pure-function, ~25 LOC.

---

**Combination rule (operator decisions 2026-05-28).** Each warn-tier blocker carries TWO attributes in the dictionary: `is_core_promise: bool` AND `is_brand_identity: bool`. The rule (evaluated top-down, first match wins):

```
if any block-tier blocker                                       → block
elif sum(core-promise warn) >= 2                                → block   # core-promise escalation
elif any(brand_identity warn) AND any(event_essential warn)     → block   # brand+event combo escalation (reviewer 2 #2)
elif sum(all warn) >= 3                                         → block   # count cap
elif any warn-tier                                              → warn
else                                                            → pass
```

**Core-promise fact classes** (the things the customer is implicitly promising in the flyer; missing 2+ materially misleads the audience):
- `item:N:name` — menu items are the promotional offer itself
- `business_name` (already block-tier as missing; brand-typo warn-tier inherits this when it's the only customer-identity signal)
- `location` AND `contact_info` both missing → escalate (no way for the customer to find the business)

**Brand-identity warn class:** any `visible wrong business/brand: ...` blocker that the brand-typo gate classified as warn. (There is exactly one source of brand-identity warns.)

**Event-essential warn class:** `missing required visible fact: schedule`, `missing required visible fact: promotion_end`, `missing required visible fact: location`. Reasoning: a flyer with a misspelled brand AND no event time / no promotion deadline / no location is structurally worse than count=2 suggests — the customer is being asked to publish a defective draft that looks self-undermining (name botched + viewers can't act on it).

Examples against F0108/F0109 + reviewer 2 edge case:
- **F0108** (1 brand-typo warn, no core-promise, no event-essential warn) → `warn` (delivered). ✓
- **F0109** (3 missing facts: location, item:4:name, item:5:name) → 2 core-promise (item:4, item:5) → `block` via core-promise escalation. Count 3 ALSO trips cap. Either path → `block`. ✓
- **Reviewer 2 #2 edge case**: 1 brand-typo + 1 missing-schedule → brand-identity AND event-essential → `block` via combo escalation. ✓ (Earlier draft incorrectly classified this `warn`.)
- Hypothetical: 1 brand-typo + 1 missing contact_info → brand-identity but contact_info is NOT event-essential → falls through to count cap (2 < 3) → `warn`. (Owner gets draft with name typo + missing phone — recoverable; phone is recognizable-by-absence.)
- Hypothetical: 2 missing item:N:names → 2 core-promise → `block` even though count is only 2.

Levenshtein-close detection plus core-promise + brand-identity + event-essential classification are all pure-function helpers, no model calls.

The full mapping table lives in `visual_qa.py` as `BLOCK_TIER_PATTERNS` + `WARN_TIER_PATTERNS` tuples of `(regex, label, is_core_promise, is_brand_identity, is_event_essential)`. Classifier walks `report.blockers`, applies the gates above in order.

---

## 5b. State model — `FlyerWorkflowStatus` extension + `FLYER_TRANSITIONS` matrix edits + warning payload

**Operator decision 2026-05-28: extend `FlyerWorkflowStatus` Literal AND store warning details on a separate payload field.** Status alone answers "what happened to the customer request?"; the payload captures the audit trail (blockers, severity, customer copy sha) that the cockpit displays.

> **Naming correction (reviewer 1 + 3 finding):** the type alias is `FlyerWorkflowStatus`, not `FlyerProjectStatus` (defined at `src/platform/schemas.py:637-650`; referenced at `schemas.py:1830,3449,3450,4889,4894`, `src/agents/flyer/workflow.py:14`, `src/agents/shift/scripts/shift-agent-deploy.sh:956`). Earlier draft used the wrong identifier; all occurrences in the plan now corrected.

**Step 1 — extend `FlyerWorkflowStatus` Literal** (`schemas.py:637-650`):

Add `"delivered_with_warning"` to the Literal member list. `__all__` exports already cover the alias name; no extra export needed.

**Step 2 — extend `FLYER_TRANSITIONS` matrix** (`schemas.py:850-859`). NEW EDIT not in earlier draft. The matrix is explicit; `is_flyer_transition_allowed` rejects undeclared transitions, so omitting any of these will SystemExit the warn-tier path.

| Add edge | From | To | Reason |
|---|---|---|---|
| ✓ | `generating_concepts` | `delivered_with_warning` | warn-tier branch writes directly here from QA decision |
| ✓ | `delivered_with_warning` | `revising_design` | customer reply with corrections re-enters revision flow |
| ✓ | `delivered_with_warning` | `awaiting_final_approval` | customer reply "OK" routes to approval (see §9 Q1) |
| ✓ | `delivered_with_warning` | `closed_no_send` | operator override (future PR — included for completeness so the matrix doesn't need re-editing later) |

Out-of-scope transitions (do NOT add): `awaiting_final_approval → delivered_with_warning`, `revising_design → delivered_with_warning`. Warn-tier is reachable only from `generating_concepts` in v1 (the QA decision point). Later revisions land in `revising_design` and re-run QA from there.

**Step 3 — add `FlyerWarningSummary` model** (`src/platform/schemas.py`):

```python
class FlyerWarningSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["warn"]                                   # always "warn"; block goes to manual_review
    blockers: list[str] = Field(default_factory=list, max_length=50)
    customer_text: str = Field(default="", max_length=2000)     # exact copy delivered
    customer_text_sha256: str = Field(default="", max_length=64)
    delivered_at: datetime
    asset_id: str = Field(default="", max_length=40)            # which preview was sent
    classifier_version: str = Field(default="v1", max_length=20)
```

Added to `FlyerProject` model as `warning: Optional[FlyerWarningSummary] = None`. Independent of `manual_review` (which stays bound to `manual_edit_required`). On `delivered_with_warning` transition, `warning` is populated. On `revising_design` re-entry from `delivered_with_warning`, `warning` is preserved for that revision pass (see §9 Q3 for clearance-timing decision) — cockpit reads it for the "what blockers prompted this revision" panel.

Rationale: manual_review is a queue-state primitive (operator action pending). Warning-summary is an outcome record (autonomous delivery completed with caveats). Different lifecycles, different consumers — separate fields keep the cockpit query trivial (`SELECT * WHERE status='delivered_with_warning'`) and the audit chain clean (`_FlyerWarnTierDelivered` row stores the same blockers/sha for replay).

**Stale-project recovery posture (reviewer 3 #8):** `recovery.py:451-502` `classify_stale_manual_project` only scans `status == "manual_edit_required"` + `manual_review.status == "queued"` — `delivered_with_warning` projects will NOT be picked up by the watchdog. A project stuck in this status forever (customer never replies) is OUT OF SCOPE for this PR. An SLA timeout watchdog for `delivered_with_warning` is a follow-up; this PR explicitly accepts the gap with the note in §10. (Rationale: warn-tier delivery still produces an audit row + cockpit row, so operators have visibility even without an alarm; SLA tuning needs real fire-rate data.)

---

## 6. Warn-tier customer copy template

**Constraints (verified against `customer_copy_policy.py` lines 15-103):**

- Must not contain any of `BANNED_CUSTOMER_COPY_TERMS`: `queued project`, `created flyer project`, `Request processing`, `Project F`, `Requested edit:`, `Original customer request`, `Authorized relationship`, `source-preserving workflow`, `source-preserving edit`, `operator`, `manual_edit_required`, `provider`, `reason_code`.
- Must not match `FORBIDDEN_COMPLETION_VERB_RE`: `processed`, `completed`, `upgraded`, `downgraded`, `changed`, `confirmed`, `sent`, `approved`, `paid`, `posted`, `pushed`, `applied`, `scheduled`, `booked`, `cancelled`, `canceled`, `refunded`.
- Must not match `CUSTOMER_COPY_FORBIDDEN_RE` (project IDs, internal terms).

**Template draft (~140 chars body + correction summary).** Refined per reviewer 2 #3 to force conscious confirmation rather than passive acceptance:

> Here's your flyer draft 📎
>
> We noticed a small detail you may want to fix:
> {correction_summary}
>
> Reply with the correction and we'll redo the design.
> Reply OK if you've checked {correction_summary_short} and it's acceptable as drawn.

Where `{correction_summary_short}` is a sub-clause derived from the same blocker translation (e.g., "the spelling near the bottom" or "the missing event time") — keeps the OK-reply explicitly tied to what was flagged, so the customer can't dismiss without acknowledging which defect they're accepting.

**Verb safety:** "noticed", "fix", "Reply", "redo", "use", "checked", "drawn" — none in `FORBIDDEN_COMPLETION_VERBS`. ("redo" is conditional on customer reply; "drawn" is a non-completion verb describing the artifact's current state.) No banned terms.

**Lint-coverage extension (reviewer 2 #4 + reviewer 3 #3):** the warn-tier template + correction summary MUST be tested against BOTH `scan_customer_text` AND `lint_no_unverified_completion` — they are intentional peers per the docstring at `customer_copy_policy.py:75-78`. The plan's earlier verb-safety claim covered only `scan_customer_text`. Commit 2 tests now assert both.

**Correction summary formatter:** `format_warn_tier_correction_summary(blockers: list[str], project: FlyerProject) -> str` translates blocker strings into customer-language sentences:

| Blocker string | Customer sentence |
|---|---|
| `visible wrong business/brand: Laksmi'S Kitchen` (typo variant) | `the spelling of "Lakshmi's Kitchen" near the bottom` |
| `missing required visible fact: location` | `the location address isn't showing` |
| `missing required visible fact: schedule` | `the event time isn't showing` |
| `missing required visible fact: item:N:name` | `one menu item name didn't come through correctly` |

Returns a single-line summary joining up to 2 most-severe items. Lives in `customer_copy_policy.py` so it inherits the lint regime; `scan_customer_text` over the rendered template + summary asserts zero hits.

---

## 7. Build sequence (5 commits, ~395 LOC)

Each commit is small enough to review on its own and ships green tests. Architecture: `generate-flyer-concepts` writes state + warning payload; `cf-router/actions.py` drives the send via existing `send_flyer_concept_previews()` with an optional `customer_text` override.

### Commit 1 — `feat(flyer): severity classifier + FlyerWorkflowStatus extension + FLYER_TRANSITIONS + warning payload schema`
**Files:** `src/agents/flyer/visual_qa.py`, `src/platform/schemas.py`, `tests/test_flyer_visual_qa.py`, `tests/test_flyer_schemas.py`.
**Source (~130 LOC):**
- Add `BLOCK_TIER_PATTERNS` + `WARN_TIER_PATTERNS` constants. Each warn entry carries `(regex, label, is_core_promise, is_brand_identity, is_event_essential)`.
- Add `WARN_TIER_COMBINATION_LIMIT` (=3) + `CORE_PROMISE_ESCALATION_LIMIT` (=2) constants.
- Add `_is_brand_typo(extracted: str, project_brand: str) -> bool` — AND-of-3 gate (distance ≤2, token overlap ≥0.5, prefix ≥4 OR overlap ≥0.75). ALL comparisons use `>=` not `>`.
- Add `_normalize_brand_for_match` + `_brand_tokens` pure-function helpers.
- Add `classify_qa_severity(report: FlyerVisualQAReport, *, project: FlyerProject) -> Literal["pass","warn","block"]` applying block-first → core-promise escalation → brand-identity+event-essential escalation → count cap → warn → pass.
- Have `run_visual_qa()` call `classify_qa_severity` and set new `report.severity` field before returning.
- Add `severity: Literal["pass","warn","block"] = "pass"` to `FlyerVisualQAReport` schema.
- **Add `"delivered_with_warning"` to `FlyerWorkflowStatus` Literal at `schemas.py:637-650`.** (Correct identifier; not `FlyerProjectStatus`.)
- **Extend `FLYER_TRANSITIONS` matrix at `schemas.py:850-859`** with four new edges: `generating_concepts → delivered_with_warning`, `delivered_with_warning → revising_design`, `delivered_with_warning → awaiting_final_approval`, `delivered_with_warning → closed_no_send`.
- Add `FlyerWarningSummary` model + `warning: Optional[FlyerWarningSummary] = None` on `FlyerProject` (model_config already has `extra="forbid"`).

**Tests (~60 LOC):**
- Empty blockers → `pass`.
- Single placeholder blocker → `block`.
- Single missing-location blocker → `warn`.
- Single brand-typo passing all 3 gates → `warn`.
- **Boundary fixture (reviewer 2 #5):** distance=2, overlap=exactly 0.5, prefix=exactly 4 → `warn` (asserts `>=` semantics).
- **Single-token brand fixture:** `project.business_name = "Lakshmi"` → tokens of size 1; matching variant → overlap 1.0 → warn; non-matching → overlap 0.0 → block.
- Single wrong-brand (token-overlap=0) → `block`.
- Levenshtein-close but token-overlap-fails (short-brand `Aria` vs `Arla`) → `block`.
- 2 item:N:name warns → `block` (core-promise escalation, count below cap).
- **Reviewer 2 #2 combo escalation:** 1 brand-typo + 1 missing-schedule → `block` (brand-identity AND event-essential).
- 1 brand-typo + 1 missing-contact_info → `warn` (brand-identity but no event-essential, count below cap).
- 4 mixed warns → `block` (count cap).
- 2 warn + 1 block → `block`.
- `FlyerWarningSummary` round-trip + `extra="forbid"` enforcement.
- `FlyerProject` with `warning=None` (default) + `warning=<populated>` both validate.
- **`FLYER_TRANSITIONS` test:** `is_flyer_transition_allowed("generating_concepts", "delivered_with_warning")` returns True; reverse returns False.

### Commit 2 — `feat(flyer): warn-tier customer copy template + correction summary formatter`
**Files:** `src/agents/flyer/customer_copy_policy.py`, `tests/test_flyer_customer_copy_policy.py` (new file or extend existing).
**Source (~25 LOC):**
- Add `WARN_TIER_DRAFT_HEADER` constant.
- Add `format_warn_tier_correction_summary(blockers: list[str], project: FlyerProject) -> tuple[str, str]` returning `(full_summary, short_summary)` — the short form goes into the "Reply OK if you've checked …" sentence (reviewer 2 #3 refinement).
- Add `build_warn_tier_customer_text(blockers, project) -> str` that composes header + full summary + OK-confirm line with short summary.

**Tests (~30 LOC):**
- Verify rendered output passes `scan_customer_text` (zero hits).
- **Verify rendered output passes `lint_no_unverified_completion`** (reviewer 2 #4 + 3 #3 — peer to scan_customer_text, both must pass).
- Verify rendered output does NOT match `FORBIDDEN_COMPLETION_VERB_RE`.
- Verify summary translates each warn blocker pattern.
- Verify summary clamps to top-2 most-severe.
- **Verify short-summary clause is non-empty + appears verbatim in OK-confirm line** (reviewer 2 #3).
- **Warn-recovery revision ack variant (reviewer 2 #7):** add `format_warn_recovery_revision_ack(blockers, project) -> str` for the customer's reply-with-fix → revision flow. Test that the copy does NOT presuppose prior-draft-was-clean tone; assert specific phrasing like "got your update — redrawing now with this fix" rather than generic `revising_design` ack. Lint-clean against both scans.

### Commit 3 — `feat(flyer): warn-tier severity branch in generate-flyer-concepts (writes state only)`
**Files:** `src/agents/flyer/scripts/generate-flyer-concepts`, `tests/test_flyer_generate_concepts.py`.
**Source (~50 LOC):**
- At the post-autorepair decision point (lines ~823 today), branch on `report.severity`:
  - `pass`: today's path (unchanged — writes `awaiting_concept_selection` / `awaiting_final_approval`).
  - `warn`: NEW path — `_is_flyer_transition_allowed("generating_concepts", "delivered_with_warning")` (defensive), then `current.model_copy(update={"status": "delivered_with_warning", "warning": FlyerWarningSummary(...), "assets": ..., "qa_reports": ..., "updated_at": now})`, `atomic_write_text(state_path, ...)`, then writes `_FlyerWarnTierDelivered` audit row via `_audit_append`.
  - `block`: today's `manual_edit_required` + `visual_qa_failed` path (unchanged).
- **No outbound send call inside generate-flyer-concepts.** The script returns 0 with a stdout JSON marker (`{"project_id": ..., "delivered_with_warning": true, "warning_blockers": [...]}`) so cf-router knows to take the warn-tier send branch.

**Tests (~50 LOC) — subprocess + sidecar QA fixture:**
- F0108-shape (single brand-typo warn): asserts state `delivered_with_warning` + `warning` payload populated + `_FlyerWarnTierDelivered` audit row + stdout JSON marker present + NO outbound bridge call from the script.
- F0109-shape (core-promise escalation): asserts state `manual_edit_required` (block path unchanged).
- Pass-shape: unchanged behavior.
- **`FLYER_TRANSITIONS` enforcement:** mutate state to invalid source-status, assert transition rejection raises clean error (no partial write).

### Commit 4 — `feat(cf-router): warn-tier send branch + customer_text override on send_flyer_concept_previews`
**Files:** `src/plugins/cf-router/actions.py`, `tests/test_cf_router_flyer_routing.py` (or closest existing routing test).
**Source (~60 LOC):**
- Modify `send_flyer_concept_previews(chat_id, project_id, customer_text: Optional[str] = None)` (`actions.py:3995`): if `customer_text` is provided, use it as the caption / accompanying text on the first bridge_send_media call; otherwise existing default copy.
- In the post-subprocess branch (around `actions.py:3948`): after the subprocess returns and state is re-read, branch on `project.status`:
  - `awaiting_concept_selection` / `awaiting_final_approval`: today's path — call `send_flyer_concept_previews(chat_id, project_id)` (no override).
  - `delivered_with_warning`: NEW — read `project.warning.blockers`, call `build_warn_tier_customer_text(...)`, then `send_flyer_concept_previews(chat_id, project_id, customer_text=warn_text)`.
  - `manual_edit_required`: today's path (unchanged).
- Customer revision reply on `delivered_with_warning`: the existing active-project lookup + revision-intent classifier path takes over (no new code here; the `FLYER_TRANSITIONS` edge from Commit 1 is what unlocks it).

**Tests (~60 LOC) — cf-router replay:**
- Replay subprocess returning `delivered_with_warning` state: asserts `send_flyer_concept_previews` called WITH `customer_text=<warn body>`.
- Replay subprocess returning `awaiting_concept_selection`: asserts called WITHOUT `customer_text` (default path).
- Customer reply with revision intent on `delivered_with_warning` project: asserts active-project lookup finds project + routes to `revising_design` (uses new `FLYER_TRANSITIONS` edge from Commit 1).

### Commit 5 — `feat(flyer-cockpit): build Projects-tab status filter row + delivered_with_warning panel + audit-only operator flag`
**Files:** `web/frontend/src/sections/FlyerAdmin.tsx`, `web/frontend/src/sections/__tests__/FlyerAdmin.test.tsx` (or closest existing test file), `src/platform/scripts/flyer-operator-flag-warn-tier` (new minimal CLI), `src/platform/schemas.py` (one more LogEntry variant).
**Source (~80 LOC TSX + ~30 LOC backend):**
- **Build new Projects-tab filter row** mirroring the Manual Queue filter pattern at `FlyerAdmin.tsx:594-711`. The Projects tab has NO existing status-filter UI (reviewer 3 #7 — earlier draft incorrectly assumed "extend"). New filter state: `projectsFilterStatus` (multi-select), `projectsFilterPhone`, `projectsFilterProjectId`.
- New rendering branch in the project-list row: when `project.status === "delivered_with_warning"`, show a small amber badge with blocker count + expand-on-click panel showing the warning payload (`blockers`, `customer_text` exact copy delivered, `delivered_at`).
- **Audit-only operator flag (reviewer 2 #6):** add a "Flag this warning for follow-up" button on the panel. Button click does NOT mutate project state. It POSTs to an existing audit-write endpoint (or invokes a thin CLI) that writes a `_FlyerOperatorFlaggedWarnTier` audit row with `project_id`, `flagged_by_operator_id`, `flagged_at`, `note: Optional[str]`. No state transition, no manual_edit_required reroute. The full mutation path (warn → manual queue) remains deferred.
- Add `_FlyerOperatorFlaggedWarnTier` to the `LogEntry` union in `schemas.py` (small addition to Commit 5 rather than Commit 4 because it lives with the cockpit feature).

**Tests (~40 LOC):**
- Filter row renders + filters by `delivered_with_warning`.
- Warning details panel renders blocker list + customer copy when expanded.
- "Flag for follow-up" button click writes `_FlyerOperatorFlaggedWarnTier` audit row (mocked endpoint).
- Read-only assertion: clicking the flag button does NOT change `project.status` or `project.warning`.
- `_FlyerOperatorFlaggedWarnTier` round-trip + `extra="forbid"`.

### Commit 6 (formerly part of Commit 4) — `feat(flyer): _FlyerQASeverityClassified + _FlyerWarnTierDelivered audit variants`
**Files:** `src/platform/schemas.py`, `tests/test_flyer_schemas.py`.
**Source (~30 LOC):**
- Add two `LogEntry` discriminated-union members (subclass `_BaseEntry`, `type: Literal["..."]`).
- `_FlyerQASeverityClassified`: project_id, asset_id, severity, blocker_count, classifier_version, classified_at.
- `_FlyerWarnTierDelivered`: project_id, asset_id, severity, blockers, customer_text_sha256, delivered_at.

**Tests (~20 LOC):**
- Round-trip: model_validate → model_dump.
- `extra="forbid"` enforced.
- Discriminator routing: `type` field deserializes to right subclass.

> Note: Commit 6 should land BEFORE Commit 3 in implementation order — Commit 3 writes these audit rows, so the schema variants must exist first. Numbering preserved to align with build narrative; actual sequencing is 1 → 6 → 2 → 3 → 4 → 5.

---

## 8. Test plan (cross-commit assertions)

| Test layer | Asserts | File |
|---|---|---|
| Pure-function | classifier dictionary + boundary-operator pin + combo escalation | `tests/test_flyer_visual_qa.py` |
| Pure-function | warn-tier copy passes BOTH `scan_customer_text` AND `lint_no_unverified_completion`; warn-recovery revision ack variant | `tests/test_flyer_customer_copy_policy.py` |
| Schema | `FlyerWorkflowStatus` extension does not break existing serialized projects; `FLYER_TRANSITIONS` new edges allowed; `FlyerWarningSummary` round-trip | `tests/test_flyer_schemas.py` |
| Subprocess | F0108-shape → state `delivered_with_warning` + `warning` payload + stdout JSON marker + NO outbound send | `tests/test_flyer_generate_concepts.py` |
| Subprocess | F0109-shape → state `manual_edit_required` (block path preserved) | `tests/test_flyer_generate_concepts.py` |
| Subprocess | Pass-shape → today's behavior unchanged | existing tests remain green |
| cf-router | `send_flyer_concept_previews(customer_text=...)` warn-tier call dispatches with warn body | `tests/test_cf_router_flyer_routing.py` |
| cf-router | `awaiting_concept_selection` returning state takes default-body path | `tests/test_cf_router_flyer_routing.py` |
| cf-router | revision intent on `delivered_with_warning` routes to `revising_design` via new `FLYER_TRANSITIONS` edge | `tests/test_cf_router_flyer_routing.py` |
| Cockpit | new Projects-tab filter row renders + filters by `delivered_with_warning` | `web/frontend/src/sections/__tests__/FlyerAdmin.test.tsx` |
| Cockpit | warning details panel renders blocker list + customer copy | same |
| Cockpit | "Flag for follow-up" button writes audit row without state mutation | same |
| Schema | Three new LogEntry variants (`_FlyerQASeverityClassified`, `_FlyerWarnTierDelivered`, `_FlyerOperatorFlaggedWarnTier`) round-trip cleanly | `tests/test_flyer_schemas.py` |
| Smoke (deploy gate) | `shift-agent-smoke-test.sh` imports `classify_qa_severity` + `_is_brand_typo` + `build_warn_tier_customer_text` to verify symbols are loadable on VPS post-deploy | `src/agents/shift/scripts/shift-agent-smoke-test.sh` |

**Regression discipline:** every existing `tests/test_flyer_visual_qa.py`, `tests/test_flyer_generate_concepts.py`, and `tests/test_cf_router_flyer_routing.py` test must remain green. Severity defaults to `pass` (no `warn`/`block` mappings hit) for existing test fixtures; the binary `failed` → manual_edit_required path is preserved for everything classified `block`, which is everything failing today's tests. cf-router default-body path is preserved for all non-`delivered_with_warning` states.

---

## 9. Open questions for design phase

1. **Customer reply parsing for warn-tier delivery:** "OK" / "looks good" / "approve" — does this transition to `awaiting_final_approval` then `approved`, or directly to `approved`? cf-router has existing approval parsing in `actions.py` — confirm at design phase that it handles `delivered_with_warning` source status. The `FLYER_TRANSITIONS` matrix (Commit 1) allows `delivered_with_warning → awaiting_final_approval`, so the existing approval-parser path is plausible; design phase should pin the exact reply-classifier change (if any).
2. **Audit-row backfill for currently-stuck projects:** Should we run a one-shot job to re-classify currently-`manual_edit_required` projects by severity and auto-deliver the warn-tier ones? Lean: defer to post-PR follow-up; not in scope here.
3. **Warning-summary clearance timing:** Plan §5b keeps `project.warning` populated across `revising_design` re-entry so the cockpit "what blockers prompted this revision" panel can render it. Clearance happens at the next successful QA pass (severity returns to `pass`). Design phase should confirm: should "successful re-QA" (block-tier resolved but new warn-tier blockers present) clear the prior warning payload, or merge? Lean: replace (so warning payload always reflects the most recent QA pass).

(Decisions 2026-05-28: Levenshtein threshold = AND-of-3 gate with `>=` semantics; combination rule = core-promise escalation + brand-identity+event-essential combo + count cap 3; state model = `FlyerWorkflowStatus` Literal extension + `FLYER_TRANSITIONS` matrix edits + separate `warning` payload; cockpit visibility = visible with new Projects-tab filter + audit-only operator flag mutation; send architecture = Option B (generate-flyer-concepts writes state, cf-router drives `send_flyer_concept_previews` with `customer_text` override). All operator-resolved; not open.)

---

## 10. Out of scope

- Changes to the autorepair classifier in `recovery.py` (`classify_flyer_qa_for_autorepair`) — PR #308 territory.
- Changes to the deterministic-text-layer (P0 #3) — separate PR.
- Changes to the autonomous-retry-from-prior-draft path (P0 #4) — separate PR.
- New customer-copy strings beyond the warn-tier template + warn-recovery revision ack variant — P1 #5 territory.
- Request-to-preview-delivered SLA metric — P1 ops, separate plan.
- **SLA timeout watchdog for stuck `delivered_with_warning` projects (reviewer 3 #8)** — explicit defer. `recovery.py:451-502` `classify_stale_manual_project` only scans `manual_edit_required` projects, so warn-tier deliveries that never get a customer reply will not be auto-recovered. This PR explicitly accepts the gap because (a) warn-tier projects produce an audit row + cockpit row so operators have visibility, (b) SLA tuning needs real fire-rate data before being defensible, (c) the audit-only operator-flag mutation in Commit 5 unblocks operator escalation if needed. Follow-up PR adds the timeout watchdog once fire-rate evidence accumulates (recommend ≥10 warn-tier deliveries observed before tuning).
- Full operator-to-manual-queue rerouting mutation from the cockpit — Commit 5 ships only the audit-only "Flag for follow-up" mutation (reviewer 2 #6); the full state-transition mutation deferred.
- One-shot backfill of currently-stuck `manual_edit_required` projects — design-phase Q2.

---

## 11. Review section (to be filled at PR time)

(Reserved for post-build evidence: actual LOC, test counts, replay outputs, deploy smoke results, customer-completion-rate delta vs baseline.)
