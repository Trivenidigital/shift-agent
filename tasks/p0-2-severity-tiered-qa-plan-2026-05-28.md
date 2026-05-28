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

The customer-facing trigger is unchanged. The new branch lives entirely inside the existing `generate-flyer-concepts` post-QA decision.

1. `[Hermes]` Customer WhatsApp inbound (image or text) → Hermes gateway routes to cf-router.
2. `[Hermes]` `cf-router/actions.py` identifies active project (or creates one) + writes intake audit.
3. `[Hermes]` `create-flyer-project` / fields collected → status → `awaiting_assets` / `generating_concepts`.
4. `[Hermes]` `generate-flyer-concepts` produces concept artifacts.
5. `[Hermes]` `run_visual_qa()` → `FlyerVisualQAReport` with `blockers: list[str]` + `status: "passed" | "failed" | "provider_unavailable"`.
6. **`[net-new]`** `classify_qa_severity(report)` → `Literal["pass", "warn", "block"]` on the same report (new field).
7. `[Hermes]` On `failed` + block-tier OR warn-tier: existing autorepair loop runs (one retry). Re-runs QA → classify again.
8. **`[net-new]`** Decision on the post-autorepair severity:
   - `pass`: today's path — `awaiting_final_approval`, send concept previews.
   - `warn`: NEW path — `delivered_with_warning`, invoke `send-flyer-package` with warn-tier customer copy.
   - `block`: today's path — `manual_edit_required` with `reason_code="visual_qa_failed"`.
9. `[Hermes]` `send-flyer-package` ships the asset + customer text through the existing WhatsApp bridge (`safe_io.bridge_post`).
10. **`[net-new]`** New `_FlyerQASeverityClassified` audit row at step 6; new `_FlyerWarnTierDelivered` audit row at step 9 for warn-tier path.
11. `[Hermes]` Customer reply with corrections → `cf-router/actions.py` active-project lookup → routes to existing `revising_design` flow.
12. **`[net-new]`** `revising_design` accepts source status `delivered_with_warning` (one-line allowlist add).

**Step count:** 12 total. `[Hermes]`: 7. `[net-new]`: 5 (steps 6, 8, 10, 12, plus the classifier dictionary itself).

**Red-flag check:** 5/12 = 42% net-new, under half. Within Hermes-first norms.

---

## 4. Drift-rule self-checks (read deployed code first)

| Work type | File read | Evidence |
|---|---|---|
| Schema work | `src/platform/schemas.py` lines 1707-1735 | `FlyerVisualQAReport` already has `extra="forbid"`, `status: FlyerVisualQAStatus`, `blockers: list[str]`. Adding `severity: Literal["pass","warn","block"] = "pass"` is one field, additive, backward-compatible. `FlyerManualReview` adjacent; not changed. |
| Visual QA module | `src/agents/flyer/visual_qa.py` lines 335-554 | `run_visual_qa()` produces blocker strings via `_unrequested_operational_claim_blockers`, `visible_wrong_brand_blockers`, locked-fact loop, source-contract loop, placeholder/regional/quality-note checks. Severity classifier is pure-function over the resulting `report.blockers` strings. |
| Script proposal | `src/agents/flyer/scripts/generate-flyer-concepts` lines 540-840 | QA call at line 578 (`run_visual_qa`), autorepair loop lines 620-787 (`classify_flyer_qa_for_autorepair`), failure-path manual_edit_required write at lines 825-830. The 3-way severity branch slots in at lines ~810-830 BEFORE the manual_edit_required write. |
| Customer-copy lint | `src/agents/flyer/customer_copy_policy.py` lines 1-103 | `BANNED_CUSTOMER_COPY_TERMS` (includes `operator`, `manual_edit_required`, `reason_code`, `provider`), `FORBIDDEN_COMPLETION_VERBS` (`sent`, `confirmed`, `applied`, `scheduled`, `processed`, etc.). Warn-tier copy must avoid all. `scan_customer_text` is the existing lint entry point. |
| Recovery / autorepair | `src/agents/flyer/recovery.py` (1020 LOC; PR #308 autorepair) | `classify_flyer_qa_for_autorepair`, `plan_flyer_autorepair`, `repair_instruction_is_safe` already exist. Severity branch is downstream of autorepair, not a replacement. |
| Send path | `src/agents/flyer/scripts/send-flyer-package` (443 LOC) | Canonical send chokepoint. Warn-tier delivery invokes it with an extra customer-text parameter (already supported via existing `--customer-text` / equivalent flag — design phase to confirm exact flag). No second send path is invented. |

**No drift detected.** Every changed file already exists; every new primitive is additive on a deployed pattern. Tag remains `extends-Hermes`.

---

## 5. Severity classifier — first-cut dictionary

The classifier is a pure function `classify_qa_severity(report: FlyerVisualQAReport) -> Literal["pass", "warn", "block"]`. It matches `report.blockers` strings against pattern tables.

| Blocker string pattern | Severity | Reason |
|---|---|---|
| (no blockers) | `pass` | Ship unchanged |
| `placeholder text is visible in generated flyer` | `block` | Embarrassing draft, not customer-recoverable |
| `English-only flyer contains regional/non-English script` | `block` | Policy violation |
| `unrequested operational claim visible: {claim}` | `block` | Misleads customer (e.g., unrequested "free delivery" claim) |
| `ocr/vision text unavailable for generated artifact` | `block` | Can't verify safety; substrate failure |
| `replaced source text still visible: {forbidden}` | `block` | Old brand/phone bleeding through |
| `vision OCR failed: ...` (quality_notes) | `block` | Substrate failure |
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

Worked examples:
- `Laksmi'S Kitchen` vs `Lakshmi's Kitchen`: distance 1, tokens {laksmis, kitchen} vs {lakshmis, kitchen} overlap=0.5 (`kitchen` matches), prefix=`Laks`=4 chars → **all 3 pass → warn**.
- `Laxmi Mart` vs `Lakshmi's Kitchen`: tokens {laxmi, mart} vs {lakshmis, kitchen} overlap=0 → **gate 2 fails → block** (also distance >2 if calculated, so gate 1 fails too).
- `Aria` vs `Aria` (4-char brand, single-typo `Arla`): distance 1, tokens {arla} vs {aria} overlap=0 → **gate 2 fails → block**. Short brands stay block-by-default, which is correct.

Implementation: `_is_brand_typo(extracted: str, project_brand: str) -> bool` in `visual_qa.py`, pure-function, ~25 LOC.

---

**Combination rule (operator decision 2026-05-28: count alone is insufficient — escalate on core-promise fact class).** Each warn-tier blocker carries an `is_core_promise: bool` attribute in the dictionary. The rule:

```
if any block-tier blocker         → block
elif sum(core-promise warn) >= 2  → block   # core-promise escalation
elif sum(all warn) >= 3           → block   # count cap
elif any warn-tier                → warn
else                              → pass
```

**Core-promise fact classes** (these are the things the customer is implicitly promising in the flyer; missing 2+ materially misleads the audience):
- `item:N:name` — menu items are the promotional offer itself
- `business_name` (already block-tier as missing; brand-typo warn-tier inherits this when it's the only customer-identity signal)
- `location` AND `contact_info` both missing → escalate (no way for the customer to find the business)

Examples against F0108/F0109:
- **F0108** (1 brand-typo warn, no core-promise warns) → `warn` (delivered). ✓
- **F0109** (3 missing facts: location, item:4:name, item:5:name) → 2 core-promise (item:4, item:5) → `block` via escalation rule. Total count 3 ALSO trips cap. Either path → `block`. ✓
- Hypothetical: 1 brand-typo + 1 missing schedule → 0 core-promise warns, 2 total → `warn`. Customer sees draft + correction prompt.
- Hypothetical: 2 missing item:N:names → 2 core-promise → `block` even though count is only 2.

Levenshtein-close detection plus core-promise classification are both pure-function helpers, no model calls.

The full mapping table lives in `visual_qa.py` as `BLOCK_TIER_PATTERNS` + `WARN_TIER_PATTERNS` tuples of `(regex, label, is_core_promise)`. Classifier walks `report.blockers`, applies the gates above in order.

---

## 5b. State model — `delivered_with_warning` + warning payload

**Operator decision 2026-05-28: extend `FlyerProjectStatus` Literal AND store warning details on a separate payload field.** Status alone answers "what happened to the customer request?"; the payload captures the audit trail (blockers, severity, customer copy sha) that the cockpit displays.

**Schema additions to `src/platform/schemas.py`:**

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

Added to `FlyerProject` model as `warning: Optional[FlyerWarningSummary] = None`. Independent of `manual_review` (which stays bound to `manual_edit_required`). On `delivered_with_warning` transition, `warning` is populated; on `revising_design` re-entry from `delivered_with_warning`, `warning` is cleared (cockpit reads from audit log for history).

Rationale: manual_review is a queue-state primitive (operator action pending). Warning-summary is an outcome record (autonomous delivery completed with caveats). Different lifecycles, different consumers — separate fields keeps the cockpit query trivial (`SELECT * WHERE status='delivered_with_warning'`) and the audit chain clean (`_FlyerWarnTierDelivered` row stores the same blockers/sha for replay).

---

## 6. Warn-tier customer copy template

**Constraints (verified against `customer_copy_policy.py` lines 15-103):**

- Must not contain any of `BANNED_CUSTOMER_COPY_TERMS`: `queued project`, `created flyer project`, `Request processing`, `Project F`, `Requested edit:`, `Original customer request`, `Authorized relationship`, `source-preserving workflow`, `source-preserving edit`, `operator`, `manual_edit_required`, `provider`, `reason_code`.
- Must not match `FORBIDDEN_COMPLETION_VERB_RE`: `processed`, `completed`, `upgraded`, `downgraded`, `changed`, `confirmed`, `sent`, `approved`, `paid`, `posted`, `pushed`, `applied`, `scheduled`, `booked`, `cancelled`, `canceled`, `refunded`.
- Must not match `CUSTOMER_COPY_FORBIDDEN_RE` (project IDs, internal terms).

**Template draft (~120 chars body + correction summary):**

> Here's your flyer draft 📎
>
> We noticed a small detail you may want to fix:
> {correction_summary}
>
> Reply with the correction and we'll redo the design. Reply OK to use this version as-is.

**Verb safety:** "noticed", "fix", "Reply", "redo", "use" — none in `FORBIDDEN_COMPLETION_VERBS`. ("redo" is a non-completion verb — it implies redoing the design, not completing an action.) No banned terms.

**Correction summary formatter:** `format_warn_tier_correction_summary(blockers: list[str], project: FlyerProject) -> str` translates blocker strings into customer-language sentences:

| Blocker string | Customer sentence |
|---|---|
| `visible wrong business/brand: Laksmi'S Kitchen` (typo variant) | `the spelling of "Lakshmi's Kitchen" near the bottom` |
| `missing required visible fact: location` | `the location address isn't showing` |
| `missing required visible fact: schedule` | `the event time isn't showing` |
| `missing required visible fact: item:N:name` | `one menu item name didn't come through correctly` |

Returns a single-line summary joining up to 2 most-severe items. Lives in `customer_copy_policy.py` so it inherits the lint regime; `scan_customer_text` over the rendered template + summary asserts zero hits.

---

## 7. Build sequence (5 commits, ~340 LOC)

Each commit is small enough to review on its own and ships green tests.

### Commit 1 — `feat(flyer): severity field + classifier + warning payload schema`
**Files:** `src/agents/flyer/visual_qa.py`, `src/platform/schemas.py`, `tests/test_flyer_visual_qa.py`, `tests/test_flyer_schemas.py`.
**Source (~110 LOC):**
- Add `BLOCK_TIER_PATTERNS` + `WARN_TIER_PATTERNS` + `WARN_TIER_COMBINATION_LIMIT` (=3) + `CORE_PROMISE_ESCALATION_LIMIT` (=2) constants.
- Add `_is_brand_typo(extracted: str, project_brand: str) -> bool` — AND-of-3 gate (distance ≤2, token overlap ≥0.5, prefix ≥4 OR overlap ≥0.75).
- Add `_normalize_brand_for_match` + `_brand_tokens` pure-function helpers.
- Add `classify_qa_severity(report: FlyerVisualQAReport, *, project: FlyerProject) -> Literal["pass","warn","block"]` applying block-first → core-promise escalation → count cap → warn → pass.
- Have `run_visual_qa()` call `classify_qa_severity` and set new `report.severity` field before returning.
- Add `severity: Literal["pass","warn","block"] = "pass"` to `FlyerVisualQAReport` schema.
- Add `"delivered_with_warning"` to `FlyerProjectStatus` Literal.
- Add `FlyerWarningSummary` model + `warning: Optional[FlyerWarningSummary] = None` on `FlyerProject`.

**Tests (~50 LOC):**
- Empty blockers → `pass`.
- Single placeholder blocker → `block`.
- Single missing-location blocker → `warn`.
- Single brand-typo passing all 3 gates → `warn`.
- Single wrong-brand (token-overlap=0) → `block`.
- Levenshtein-close but token-overlap-fails (short-brand case) → `block`.
- 2 item:N:name warns → `block` (core-promise escalation, count below cap).
- 1 brand-typo + 1 missing-schedule → `warn` (no core-promise hit, count below cap).
- 4 mixed warns → `block` (count cap).
- 2 warn + 1 block → `block`.
- `FlyerWarningSummary` round-trip + `extra="forbid"` enforcement.
- `FlyerProject` with `warning=None` (default) + `warning=<populated>` both validate.

### Commit 2 — `feat(flyer): warn-tier customer copy template + correction summary formatter`
**Files:** `src/agents/flyer/customer_copy_policy.py`, `tests/test_flyer_customer_copy_policy.py` (new test file or extend existing).
**Source (~15 LOC):**
- Add `WARN_TIER_DRAFT_HEADER` constant.
- Add `format_warn_tier_correction_summary(blockers: list[str], project: FlyerProject) -> str`.
- Add `build_warn_tier_customer_text(blockers, project) -> str` that composes header + summary.

**Tests (~25 LOC):**
- Verify rendered output passes `scan_customer_text` (zero hits).
- Verify rendered output does NOT match `FORBIDDEN_COMPLETION_VERB_RE`.
- Verify summary translates each warn blocker pattern.
- Verify summary clamps to top-2 most-severe.

### Commit 3 — `feat(flyer): 3-way send branch in generate-flyer-concepts on QA severity`
**Files:** `src/agents/flyer/scripts/generate-flyer-concepts`, `tests/test_flyer_generate_concepts.py`.
**Source (~50 LOC):**
- At the post-autorepair decision point (around lines 810-830 today), branch on `report.severity`:
  - `pass`: today's path (unchanged).
  - `warn`: invoke `send-flyer-package` with `--customer-text "$(build_warn_tier_customer_text ...)"`, populate `project.warning = FlyerWarningSummary(...)`, transition project to `delivered_with_warning`, write `_FlyerWarnTierDelivered` audit row.
  - `block`: today's `manual_edit_required` + `visual_qa_failed` path (unchanged).
- Add `delivered_with_warning` to the revision-routing source-status allowlist (one line).
- On revision-entry from `delivered_with_warning`: clear `project.warning` (cockpit reads audit log for warning history).

**Tests (~50 LOC) — replay tests with sidecar QA fixture:**
- F0108-shape (brand typo, single warn blocker): asserts `delivered_with_warning` + `warning` payload populated + `_FlyerWarnTierDelivered` audit row + send-flyer-package called with warn-tier text.
- F0109-shape (core-promise escalation OR count cap): asserts `manual_edit_required` (block path).
- Clean pass: unchanged behavior.
- Revision entry: asserts `warning` cleared on transition to `revising_design`.

### Commit 4 — `feat(flyer): _FlyerQASeverityClassified + _FlyerWarnTierDelivered audit variants`
**Files:** `src/platform/schemas.py`, `tests/test_flyer_schemas.py`.
**Source (~30 LOC):**
- Add two `LogEntry` discriminated-union members (subclass `_BaseEntry`, `type: Literal["..."]`).
- `_FlyerQASeverityClassified`: project_id, asset_id, severity, blocker_count, classifier_version, classified_at.
- `_FlyerWarnTierDelivered`: project_id, asset_id, severity, blockers, customer_text_sha256, delivered_at.

**Tests (~20 LOC):**
- Round-trip: model_validate → model_dump.
- `extra="forbid"` enforced.
- Discriminator routing: `type` field deserializes to right subclass.

### Commit 5 — `feat(flyer-cockpit): delivered_with_warning filter pill + warning details panel`
**Files:** `web/frontend/src/sections/FlyerAdmin.tsx`, `web/frontend/src/sections/__tests__/FlyerAdmin.test.tsx` (or equivalent existing test file).
**Source (~50 LOC TSX):**
- Add `delivered_with_warning` to the status filter dropdown / pill row.
- New rendering branch in the project-list row: when `project.status === "delivered_with_warning"`, show a small amber badge with the count of blockers and an expand-on-click panel showing the warning payload (`blockers`, `customer_text` exact copy delivered, `delivered_at`).
- Operator's expand-click does NOT mutate state (read-only); explicit "request operator follow-up" button transitions to `manual_edit_required` if needed (out-of-scope here; default action is "acknowledge" which writes a `_FlyerOperatorAcknowledgedWarning` audit row — also deferred).

**Tests (~20 LOC):**
- Filter pill renders the count of projects with `status=delivered_with_warning`.
- Warning details panel renders blocker list + customer copy when expanded.
- Read-only assertion: no mutation calls on expand.

---

## 8. Test plan (cross-commit assertions)

| Test layer | Asserts | File |
|---|---|---|
| Pure-function | classifier dictionary, combination cap | `tests/test_flyer_visual_qa.py` |
| Pure-function | warn-tier copy passes lints | `tests/test_flyer_customer_copy_policy.py` |
| Subprocess | F0108-shape → delivered_with_warning + send invoked | `tests/test_flyer_generate_concepts.py` |
| Subprocess | F0109-shape → manual_edit_required (block path preserved) | `tests/test_flyer_generate_concepts.py` |
| Subprocess | Pass-shape → today's behavior unchanged | existing tests must remain green |
| Schema | Two new LogEntry variants round-trip cleanly | `tests/test_flyer_schemas.py` |
| Smoke (deploy gate) | `shift-agent-smoke-test.sh` adds a call to `classify_qa_severity` to verify the symbol is importable on VPS post-deploy | `src/agents/shift/scripts/shift-agent-smoke-test.sh` |

**Regression discipline:** every existing `tests/test_flyer_visual_qa.py` and `tests/test_flyer_generate_concepts.py` test must remain green. Severity defaults to `pass` (no `warn`/`block` mappings hit) for the existing test fixtures; the binary `failed` → manual_edit_required path is preserved for everything classified `block`, which is everything failing today's tests.

---

## 9. Open questions for design phase

1. **Customer reply parsing for warn-tier delivery:** "OK" / "looks good" / "approve" — does this transition to `awaiting_final_approval` then `approved`, or directly to `approved`? cf-router has existing approval parsing in `actions.py` — confirm at design phase that it handles `delivered_with_warning` source status. If not, one-line allowlist add.
2. **Audit-row backfill for currently-stuck projects:** Should we run a one-shot job to re-classify currently-`manual_edit_required` projects by severity and auto-deliver the warn-tier ones? Lean: defer to post-PR follow-up; not in scope here.
3. **Warning-summary clearance timing:** Commit 3 clears `project.warning` on `revising_design` entry. Should we instead keep it until the next QA pass (so the operator can see "this revision was prompted by these blockers")? Lean: keep it — clearance can happen at the next successful QA pass instead. Decide at design phase.

(Decisions 2026-05-28: Levenshtein threshold = AND-of-3 gate; combination rule = core-promise escalation + count cap 3; state model = Literal extension + separate `warning` payload; cockpit visibility = visible. All four operator-resolved; not open.)

---

## 10. Out of scope

- Changes to the autorepair classifier in `recovery.py` (`classify_flyer_qa_for_autorepair`) — that's PR #308 territory and works as-is.
- Changes to `cf-router/actions.py` beyond the one-line source-status allowlist for revision routing.
- Changes to the deterministic-text-layer (P0 #3) — that's a separate PR.
- Changes to the autonomous-retry-from-prior-draft path (P0 #4) — separate PR.
- New customer-copy strings beyond the warn-tier template — P1 #5 territory.
- Request-to-preview-delivered SLA metric — P1 ops, separate plan.
- "Operator acknowledged this warning" mutation in the cockpit — read-only panel in this PR; mutation deferred.
- One-shot backfill of currently-stuck `manual_edit_required` projects — design-phase Q2.

---

## 11. Review section (to be filled at PR time)

(Reserved for post-build evidence: actual LOC, test counts, replay outputs, deploy smoke results, customer-completion-rate delta vs baseline.)
