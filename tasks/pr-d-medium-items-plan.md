# PR-D — P1.4 medium items + yaml-migration + v0.3 anchor closure

**Drift-check tag:** `extends-Hermes`

**Cadence:** medium pipeline per `tasks/todo.md` matrix (~250 LOC, multi-file feature with operational gates) — Plan → 5 reviews → fix → Design → 5 reviews → fix → Build → PR → 5 reviews → fix → merge → deploy.

**Goal:** Close 11 deferred items from `tasks/todo.md` §P1.4 + the long-standing v0.3 `CateringQuoteAttempted` docstring-vs-reality gap. Plan v2 (`tasks/p1-followups-and-v04-plan.md`) already itemized these with corrections from PR-A + PR-C reviews; this plan elevates them to a standalone PR-D plan for the medium-cadence pipeline.

---

## Hermes-first capability checklist

| Step | Hermes? | Net-new? |
|---|---|---|
| Inbound catering inquiry → SKILL preamble runs `lookup-prior-leads-by-phone` | [Hermes] PR-A wired this | — |
| Emit `lookup_invoked` audit row from SKILL preamble | [Hermes-adjacent] `log-decision-direct` chokepoint | new LogEntry variant |
| Config-load on every script | [Hermes] `safe_io.load_yaml_model` (PR #34) | migrate 4 inline callsites |
| Emit `config_load_failed` on load-time error | [Hermes-adjacent] | new LogEntry variant |
| Owner replies `<CODE> approve` → apply-script processes | [existing] `apply-catering-owner-decision` | matcher widen + idempotency anchor |
| Audit divergence when post-bridge re-load lead missing | [existing] discriminated union | new `CateringStateOutboundDivergence` variant |

Genuinely net-new: 3 LogEntry variants + 1 forward-compat shim + 4 migration callsites + 1 matcher widen + ~140 tests + 1 doc revision. ~227 src + ~190 tests + ~90 doc.

---

## Read-deployed-code evidence

| File | Why | Findings |
|---|---|---|
| `src/agents/catering/scripts/apply-catering-owner-decision` lines 246-340 | Task 8 audit-wrong-lead bug + Task 22 matcher widen | (a) `for i, l in enumerate(store.leads): if l.lead_id == lead_id_for_output: ... break` has no `else` branch — if the lead is somehow absent from re-loaded store, audit row references `store.leads[i]` (last iterated lead = WRONG `customer_phone`); (b) matcher at 251-267 filters `l.status == "AWAITING_OWNER_APPROVAL"` — OWNER_APPROVED retries hit EXIT_NOT_FOUND |
| `src/platform/schemas.py` `CateringQuoteAttempted` line 1698-1709 + FSM comment line 431 | Task 22: schema docstring claims "Written BEFORE bridge POST in the SAME lock"; FSM comment also references the anchor as deployed — but `grep -rn 'CateringQuoteAttempted' src/agents/catering/scripts/` returns ZERO writers | Closing the v0.3 docstring-vs-reality gap. Anchor key = `code` alone (idempotent on code), per PR-A R3 retry-semantics decision. |
| `src/platform/schemas.py` `CateringLeadStatus` Literal lines 390-401 | Task 8: confirm UNRECOVERABLE NOT in Literal (R2 BLOCKER fix) | 10 statuses; UNRECOVERABLE is not one. Use NEW LogEntry variant, not status transition. |
| `src/platform/schemas.py` `_BaseEntry` line 1183 + LogEntry union ~1839 | Tasks 8/9/9.5/10: shape of new variants + forward-compat shim | `_BaseEntry` has `mode='before'` validator pattern; LogEntry uses `discriminator="type"`. Adding new types = old binary's validation fails → R5 medium "rollback hazard". Forward-compat shim downgrades unknown types to `_BaseEntry`. |
| `src/agents/catering/scripts/lookup-prior-leads-by-phone` | Task 9: `lookup_invoked` emission | Read-only by design; emission via `log-decision-direct` from the SKILL preamble (`parse_catering_inquiry/SKILL.md` Step 0), NOT the script itself |
| `src/agents/catering/scripts/create-catering-lead` lines 343-347 + `apply-catering-owner-decision` lines 230-234 + `lookup-prior-leads-by-phone` `_load_config_now` + `shift-agent-smoke-test.sh` step 3 | Task 7: yaml.safe_load migration callsites | All currently use inline `yaml.safe_load(... .read_text())` + `Config.model_validate(...)`. Migrate to `safe_io.load_yaml_model`. |
| `tests/_b1_helpers.py` lines 13-26 | Task 12 v02 no-op investigation | docstring claims v02 helpers' `mod.__name__ = "__main__"` pattern "never actually executed". Pre-conftest-hoist smoke probe pins truth. |
| `tests/test_catering_v02_scripts.py` lines 31-203 | Task 11: conftest hoist scope | ~164 helper-reference callsites; `_run_create` has `customer_tz` kwarg `_b1_helpers.run_create` lacks. Reconcile. |
| `docs/catering-edge-cases.md` v3.1 (caps at C22) | Task 14: doc revision | NO C23/C25/C32/C40/C41 in current doc. Add as NEW C23 (prompt-injection), C24 (catering-vs-expense disambiguation), C25 (image+caption "menu" routing). Use existing "Deferred cases" table at ~line 524 — no in-place tombstones. |
| `tasks/p1-followups-and-v04-plan.md` v2 §"Plan-review v2 corrections" | All 11 PR-D tasks already itemized with correction trail | This plan elevates the table to commit-by-commit detail. |

---

## Branching strategy

- **Branch:** `feat/p1-medium-items` cut from current `main` HEAD (`4b498c1`)
- **Sequencing:** PR-D and PR-B can land in parallel post-PR-C per plan v2 §"Sequencing changed". PR-D first because: (a) builds out observability infrastructure (`lookup_invoked`, `config_load_failed`) that PR-B's deploy soak benefits from, (b) closes pre-existing v0.3 audit-wrong-lead bug independently of v0.4 paradigm change.

---

## Build sequence (12 commits)

| # | Commit subject | Touches | LOC |
|---|---|---|---|
| 1 | `feat(schemas): LogEntry forward-compat shim — downgrade unknown type to _BaseEntry passthrough` | `src/platform/schemas.py` (mode='before' validator on LogEntry adapter), `tests/test_log_entry_forward_compat.py` (NEW) | 15 src + 30 tests |
| 2 | `feat(schemas): CateringStateOutboundDivergence LogEntry variant for state-vs-outbound divergence` | `src/platform/schemas.py` (new `_BaseEntry` subclass + LogEntry union + `__all__`), `tests/test_catering_state_outbound_divergence.py` (NEW) | 18 src + 25 tests |
| 3 | `feat(schemas): lookup_invoked LogEntry variant for SKILL preamble observability` | `src/platform/schemas.py`, `tests/test_lookup_invoked_audit.py` (NEW) | 25 src + 30 tests |
| 4 | `feat(schemas): config_load_failed LogEntry variant — best-effort emission from config-load chokepoints` | `src/platform/schemas.py`, `tests/test_config_load_failed_audit.py` (NEW) | 22 src + 25 tests |
| 5 | `refactor(catering): migrate 4 inline yaml.safe_load callsites to safe_io.load_yaml_model` | `src/agents/catering/scripts/{create-catering-lead,apply-catering-owner-decision,lookup-prior-leads-by-phone}`, `src/agents/shift/scripts/shift-agent-smoke-test.sh` | 30 src + 5 tests |
| 6 | `feat(catering): emit config_load_failed on load-time errors (best-effort)` | Each of the 4 migrated callsites adds `_log_config_load_failed_best_effort()` helper invocation | 25 src + 8 tests |
| 7 | `fix(catering): apply-decision post-bridge re-load missing-lead audit emits CateringStateOutboundDivergence + Pushover P2 (closes audit-log-wrong-lead bug)` | `apply-catering-owner-decision` (`for: ... break` loop now has `else: ...`) | 25 src + 6 tests |
| 8 | `fix(catering): emit CateringQuoteAttempted anchor BEFORE bridge POST + matcher widen (closes long-standing v0.3 docstring-vs-reality gap)` | `apply-catering-owner-decision` matcher (251-267) accepts OWNER_APPROVED if anchor row exists with same code; emit anchor under same lock as state-mutation, before bridge POST | 35 src + 8 tests |
| 9 | `feat(catering): SKILL preamble emits lookup_invoked via log-decision-direct (best-effort)` | `src/agents/catering/skills/parse_catering_inquiry/SKILL.md` Step 0 + `tests/test_catering_skill_md.py` (extend) | 25 SKILL + 6 tests |
| 10 | `refactor(tests): hoist BridgeStub/make_env_dir/run_create/run_apply to conftest.py with v02 reconciliation` | `tests/conftest.py` + `tests/_b1_helpers.py` (re-export shim) + `tests/test_catering_v02_scripts.py` (migrate ~164 callsites) — preceded by Task 12 v02-no-op investigation result captured in commit message | 80 (mostly delete) + 5 tests |
| 11 | `test(catering): 5 test gaps from PR-A R3 — empty-status, negative-attempts, corrupt-status integration, post-bridge BUG monkeypatch, lock-parent-dir` | `tests/test_safe_io_load_status.py` + `tests/test_safe_io_filelock.py` + `tests/test_catering_oserror_surfacing.py` (extend each) | 70 tests |
| 12 | `docs(catering-edge-cases): v3.2 — drop unreachable cases, refocus C06–C13, RUNNABLE C02 + new C23/C24/C25 (prompt-injection + dispatcher routing)` | `docs/catering-edge-cases.md` + `tests/test_catering_edge_cases_doc.py` (NEW; static checks on case ID monotonicity + v3.2 changelog presence) | 90 doc + 25 tests |

Total: ~265 src + ~243 tests + ~90 doc + ~25 SKILL = ~625 lines diff.

(Plan v2 estimated 227+190+90 = 507. Higher count here reflects test breadth on the 3 new LogEntry variants + integration tests for Task 8 + Task 22.)

---

## Tactical decisions deferred to design phase

- **Task 1 (LogEntry forward-compat shim):** sibling `_BaseEntry` subclass `_UnknownLogEntry` with `type: str` (no Literal) + raw_dict capture. Forward-compat validator at LogEntry adapter level, not on individual variants.
- **Task 7 (matcher widen for `CateringQuoteAttempted`):** before the existing `[l for l in store.leads if l.owner_approval_code == code and l.status == "AWAITING_OWNER_APPROVAL"]` filter, run a tail-N-lines scan of `decisions.log` for `catering_quote_attempted` rows with the same code. If found AND status is `OWNER_APPROVED` (or `SENT_TO_CUSTOMER`), return EXIT_OK with `idempotent_replay=True` and skip bridge POST.
- **Task 9 (lookup_invoked emission):** SKILL preamble adds a 1-line `log-decision-direct` invocation after parsing the lookup script's JSON output. Best-effort via `|| true`. Schema fields: `lead_phone_canonical: E164Phone` (the canonical phone passed to lookup), `lookup_status: Literal[...]` (pulled from lookup script's stdout), `prior_lead_count: int = 0`, `last_seen_days_ago: Optional[int] = None`, `most_recent_status: Optional[CateringLeadStatus] = None`.
- **Task 10 (config_load_failed emission):** `_log_config_load_failed_best_effort(path: Path, exc: Exception, script: str)` helper added to `safe_io.py`. Called from each script's config-load exception handler. Wraps `try: ndjson_append(...)` with `except Exception: pass` to ensure audit failure doesn't shadow the config error.
- **Task 11 (conftest hoist):** Pre-hoist Task 12 smoke probe → confirm v02 tests run today. If yes, hoist preserves behavior. If no, hoist surfaces real bugs — pre-flight investigation captured in commit message.
- **Task 14 (doc revision v3.2):** changelog at top with case-ID anchors. New cases inserted between C22 and the Deferred-cases table.

---

## Out-of-scope (PR-B)

v0.4 LLM-drafted customer quote — separate full-pipeline cycle. PR-D's `lookup_invoked` audit becomes part of PR-B's deploy soak observability.

---

## Self-review

- [x] Spec coverage: every PR-D task from plan v2 enumerated.
- [x] Drift-check tag at top + read-deployed-code evidence + Hermes-first checklist.
- [x] No references to `extra="ignore"` on schemas (PR-A R1 correction held).
- [x] No references to `UNRECOVERABLE` status (R2 BLOCKER fix held — uses NEW LogEntry variant).
- [x] `CateringQuoteAttempted` matcher widen specified (R2 finding addressed).
- [x] Forward-compat shim for LogEntry rollback hazard (R5 finding addressed).
- [x] Conftest hoist preceded by v02 no-op investigation per R3.

---

## Plan v3 — 5-agent plan-review revisions (3 BLOCKERs + 19 findings applied)

This section supersedes any conflicting earlier text. Apply at design phase.

### BLOCKERs (must be fixed before any build commit)

| # | BLOCKER | Resolution |
|---|---|---|
| B1 | **Forward-compat shim mechanism broken** (R1) — `LogEntry = Annotated[Union[...], Field(discriminator="type")]` raises `union_tag_invalid` BEFORE any adapter-level `mode='before'` validator runs. The proposed shim does not work as designed. | Replace `Field(discriminator="type")` with Pydantic v2 callable `Discriminator(callable_picker)` form: `LogEntry = Annotated[Union[...], Discriminator(_pick_log_entry_tag)]` where `_pick_log_entry_tag(v) -> str` returns `"_unknown_"` for unrecognized `type` values. Add `_UnknownLogEntry(_BaseEntry)` with `type: Literal["_unknown_"]` + `raw: dict` capture. Add a Task 1 acceptance test that round-trips a known-good 30-variant fixture PLUS a synthetic `{"type": "future_type_xyz", ...}` line — assert all 30 typed AND unknown → `_UnknownLogEntry`. |
| B2 | **Anchor write-ordering + retry-semantics undefined** (R2 — TWO blockers) — Task 7 says "before bridge POST" but doesn't pin (a) atomic ordering with state-mutation, (b) what `idempotent_replay=True` does to lead status. Without explicit spec, the fix trades duplicate-quote for permanently-stuck-at-OWNER_APPROVED. | Pin canonical order in design: (1) atomic_write_json(LEADS_PATH, store) with status=OWNER_APPROVED, (2) ndjson_append CateringQuoteAttempted anchor INLINE inside same FileLock (no separate `flock(LOG_PATH)` re-acquire — `LEADS_LOCK` already serializes), (3) emit existing audit rows, (4) release LEADS_LOCK, (5) bridge POST. On retry: anchor present + status=OWNER_APPROVED → previous run died between bridge and second-lock; retry SKIPS bridge POST AND advances status=SENT_TO_CUSTOMER + writes CateringQuoteSent with `outbound_message_id="_recovered_<original_message_id>"`. Anchor present + status=SENT_TO_CUSTOMER → fully complete; return EXIT_OK no-op. Anchor absent → existing matcher behavior. |
| B3 | **Forward-compat shim deploys WITH variants** (R5) — bundling shim + new variants in one tarball means rollback restores prior tarball that has neither shim nor known-variant types → validation fails on already-written rows. | Split into TWO deploys: (a) PR-D-pre = shim only (commit 1) + soak ≥24h so prior tarball-of-record post-deploy ALSO has the shim; (b) PR-D = variants + apply-script + remaining commits. OR document operationally that rollback after any new-variant emission requires manual decisions.log triage with explicit operator runbook. |

### High-priority findings

| # | Finding | Resolution |
|---|---|---|
| H1 | Cadence mismatch — 625 LOC + 3 LogEntry variants + observability surface = **full pipeline** per matrix, not medium. | Reclassify as full pipeline OR split: PR-D1 (audit-wrong-lead + matcher widen + yaml migration; ~250 LOC, medium) + PR-D2 (3 LogEntry variants + lookup_invoked SKILL emission + doc revision; full). Recommend split. |
| H2 | PR-D vs PR-B sequencing — schemas.py merge conflicts guaranteed if parallel. | Pin sequential: PR-D first, PR-B from main post-PR-D-merge. PR-D ships forward-compat shim that PR-B's added schema fields rely on for rollback safety. |
| H3 | Audit-wrong-lead bug shape (R4) — actual bug is for-loop index leak (`i` retains last-iteration value when no match), not just missing `else`. Reference at line 397: `store.leads[i].customer_phone`. | Track match explicitly: `matched_idx = next((i for i,l in enumerate(store.leads) if l.lead_id == lead_id_for_output), None)`; if None: emit divergence + Pushover P2 + return EXIT_SCHEMA_VIOLATION. Index off `matched_idx`. Eliminates index-leak class entirely. |
| H4 | No recovery tooling for `CateringStateOutboundDivergence` (R2) — Pushover P2 fires but operator has to hand-edit JSON. | Add 13th commit: `feat(catering): catering-lead-reconcile script — operator-driven status correction for state-vs-outbound divergence`. Takes `--lead-id LXXXX --target-status SENT_TO_CUSTOMER --reason "<text>"`, holds LEADS_LOCK, advances status, emits `CateringLeadStatusChange(actor="operator")` + new `CateringLeadManualReconcile` audit row. ~40 src + 8 tests. |
| H5 | `lookup_invoked` emission via `log-decision-direct \|\| true` swallows stderr (R2). | Replace `\|\| true` with `2>&1 \| logger -t catering-skill-lookup-invoked \|\| true` so stderr lands in journald. Document fallback in plan + SKILL prompt comment. |
| H6 | Smoke step 2 doesn't validate LogEntry round-trip (R5) — Pydantic discriminated-union validation is lazy; bad Literal collision compiles fine. | Smoke addition: `from schemas import LogEntry; for v in [lookup_invoked, config_load_failed, catering_state_outbound_divergence, _unknown_]: TypeAdapter(LogEntry).validate_python({"type": v, "ts": "2026-01-01T00:00:00Z", ...minimal_fields})`. Catches discriminator collisions before restart-rollback window. |
| H7 | Pre-restart gate doesn't enumerate `load_yaml_model` etc. (R5). | Update `src/platform/scripts/check-safe-io-symbols` REQUIRED_SYMBOLS list as part of commit 5: ensures `load_yaml_model` is verified pre-restart (it's already there from PR-C). Add `_log_config_load_failed_best_effort` if it lives in safe_io (R4 says it shouldn't — see M3 below). |
| H8 | Anchor-only short-circuit creates stuck-loop failure (R5) — anchor write succeeds, bridge POST fails, retry sees anchor → skips bridge → never sends. | Anchor records require `bridge_post_outcome: Optional[Literal["success","failed","unknown"]]` field. Matcher requires anchor AND outcome=success for idempotent_replay. Anchor + outcome=failed/unknown → retry attempts bridge again. |

### Medium-priority findings

| # | Finding | Resolution |
|---|---|---|
| M1 | `CateringStateOutboundDivergence` missing `original_message_id` (R1). | Add `original_message_id: str = Field(min_length=1)`. |
| M2 | Naming violates `Catering<Verb><Noun>` pattern (R4). | Rename to `CateringQuoteSentStateMissing`. Type literal: `catering_quote_sent_state_missing`. |
| M3 | `_log_config_load_failed_best_effort` doesn't belong in safe_io.py (R4) — module is filesystem/lock primitives, no schema imports. | Place in NEW `src/platform/audit_helpers.py` (or co-located in schemas.py as a free function). |
| M4 | config_load_failed needs UTC fallback (R2) — when cfg fails to load, `customer_now()` has no tz source. | Helper signature: `_log_config_load_failed_best_effort(path, exc)` — uses `datetime.now(timezone.utc)` always. Document in helper docstring. |
| M5 | Tail-N matcher-widen scan unbounded (R1+R4). | Pin N=500. Falls through to existing EXIT_NOT_FOUND on no-match (avoids re-POST loop). |
| M6 | Test count inflation 3-6x reference (R3). | Trim Tasks 1/9/10 to ~8 tests each (24 total instead of 85). Reclaim budget for Task 11 conftest hoist (currently underweight). |
| M7 | v02 callsite count 108 not 164 (R3). | Update plan with accurate count. |
| M8 | Task 11/12 ordering inversion (R3). | Renumber: Task 11 = v02 probe (separate commit, observable outcome documented), Task 12 = conftest hoist with strategy informed by probe. |
| M9 | Soak should be 20-min not 5 (R5) — apply-script live state-load path changes. | Bump deploy soak to 20-min (PR-A precedent). |
| M10 | `_log()` helper takes separate `flock(LOG_PATH)` (R2) — anchor + state not atomic. | Replace `_log()` call with direct `ndjson_append(LOG_PATH, ...)` inside LEADS_LOCK (LEADS_LOCK dominates; LOG_PATH.lock is for cross-script concurrent appenders, redundant when outer lock held). |
| M11 | Re-export shim Windows portability (R2) — `_b1_helpers.py` imports yaml + http.server at module level. | Either delete `_b1_helpers.py` outright AND migrate test_catering_b1_cases.py imports in same commit, OR hoist to NEW `tests/_shared_catering_helpers.py` (sibling, no conftest auto-load coupling). Prefer the latter. |
| M12 | Defer `lookup_invoked` to PR-B (R4) — it pairs with v0.4 deploy soak naturally. | Move Tasks 3 + 9 from PR-D to PR-B. PR-D scope drops to ~570 LOC, 10 commits. |

### Low-priority findings

| # | Finding | Resolution |
|---|---|---|
| L1 | Convention departure note for forward-compat shim (R1). | Add one-line "Convention departure" callout explaining shim is first deployment of unknown-tag passthrough. |
| L2 | Drift-tag stay `extends-Hermes` despite shim novelty (R1). | Confirmed — shim is additive infrastructure, doesn't fight a Hermes pattern. |
| L3 | Vestigial "drop 2 cross-tenant cases" reference (R3+R4). | Tombstone removed; doc revision is purely additive (C23/C24/C25) plus 4 already-enumerated drops. |
| L4 | `_log_config_load_failed_best_effort` script param redundant (R4). | Drop `script` param; helper computes via `Path(sys.argv[0]).name`. |
| L5 | `lookup_invoked` field rename clarity (R1) — `lead_phone_canonical` is SKILL's own canonical phone, not a lookup-output. | Add comment explaining the field is the phone PASSED TO lookup, not echoed back. |

---

## Status: PLAN-REVIEWED, design-phase BLOCKED until B1-B3 resolved

Plan-review surfaced 3 BLOCKERs that prevent design phase from advancing safely. Resolution paths documented above. Fresh-session continuation should:

1. Apply B1 fix (callable Discriminator + _UnknownLogEntry); design Task 1's acceptance test as the FIRST design-phase verification
2. Apply B2 fix (canonical anchor-write-ordering + retry-state-advance contract)
3. Apply B3 decision (split deploy OR document operational rollback runbook)
4. Apply H1 split decision (PR-D1 + PR-D2 OR full pipeline for PR-D-as-is)
5. Address H2-H8 + M1-M12 in design doc
6. Then dispatch 5 design-reviewers; expect surgical revisions only after design captures BLOCKER fixes

Plan + design + build + PR + 5-reviews + merge + deploy for PR-D = ~35-40% of fresh-session context. PR-B (v0.4) requires its own session afterward.

PR-B's plan + design starting point lives in `tasks/p1-followups-and-v04-plan.md` v2 §"Tactical decisions for PR-B design phase" — already incorporates 12 reviewer-flagged corrections from the prior cycle.
