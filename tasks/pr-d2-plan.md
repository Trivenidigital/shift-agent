# PR-D2 — behavior changes (apply-catering-owner-decision rewrite + reconcile + doc v3.2)

**Drift-check tag:** `extends-Hermes` (the convention departure was in PR-D1; PR-D2 uses the shipped primitives).

**Pipeline position:** Plan ← you are here → 5-agent plan-review → fix → Design → 5-agent design-review → fix → Build (7 commits) → PR → 5-agent PR-review → fix → merge → deploy.

**Depends on:** PR #36 (PR-D1 schema infrastructure, merged 2026-04-29 squash `3f96c07`). PR-D2 branches from PR-D1-merged main; the new variants + audit_helpers + check-audit-helpers-symbols + tools/check-pr-d2-rollback-target.sh are now on main.

**Supersedes / detailed by:** `tasks/pr-d-medium-items-design.md` v2 §14.5 PR-D2 commits 1-7. This plan is the focused PR-D2-only doc; design will follow with concrete patches.

---

## Hermes-first capability checklist

| Step | Hermes? | Net-new |
|---|---|---|
| Owner replies `<CODE> approve` → apply-script processes | [Hermes] existing apply-catering-owner-decision | matcher widen + anchor reorder + retry-state-machine |
| Config-load (5 catering scripts) | [Hermes-adjacent] `safe_io.load_yaml_model` already exists from PR-C | migrate 5 inline `yaml.safe_load` callsites + emit `config_load_failed` on failure |
| Audit divergence emission | [Hermes-adjacent] `audit_helpers.log_quote_sent_lead_missing_best_effort` shipped in PR-D1 | wire into apply-script post-bridge re-load |
| Operator reconcile (state-vs-outbound divergence) | [net-new] | new `catering-lead-reconcile` operator script |
| Pushover priority=2 alert on divergence | [Hermes] `shift-agent-notify-owner` already wired in deployed apply-script | retain existing call shape; just needs to fire from new branch |
| Test fixture hoist | [tests-internal] | `tests/_shared_catering_helpers.py` sibling + conftest fixture wrappers + `_b1_helpers.py` re-export shim |

Genuinely net-new: 5 yaml migrations + 1 apply-script rewrite (matched_idx + anchor two-step + retry-state-machine + tail-scan helpers) + 1 reconcile operator script + 1 v02 probe test + 1 conftest hoist (~108 callsite migration) + 5 PR-A R3 test gaps + 1 doc revision (v3.2). ~440 LOC src + ~270 LOC tests + ~90 doc.

---

## Read-deployed-code evidence (re-confirmed post-PR-D1 merge)

| File | Why | Findings (already validated in design v2 §1) |
|---|---|---|
| `src/agents/catering/scripts/apply-catering-owner-decision` lines 220-410 | Tasks 7-8 (matched_idx + anchor reorder + retry-state-machine) | Index leak at line 397 confirmed; `_log()` helper holds inner LOG_PATH flock; existing matcher filters `status=AWAITING_OWNER_APPROVAL` only. |
| `src/platform/safe_io.py` lines 240-310 | `ndjson_append` rejects raw newlines; `load_yaml_model` raises `RuntimeError` on parse error | Confirmed via PR-D1 commit 4 audit-helpers test. |
| `src/platform/audit_helpers.py` (NEW in PR-D1) | Wire `log_quote_sent_lead_missing_best_effort` into apply-script | API: takes `lead_id, original_message_id, customer_phone_at_approve, outbound_message_id, detail, log_path` — never raises. |
| `src/platform/schemas.py` `CateringQuoteAttempted` (extended in PR-D1) | New `bridge_post_outcome: Literal["success","failed","unknown"]="unknown"` field | Two-step write contract: write outcome="unknown" before bridge POST; second row outcome="success"/"failed" after. |
| 5 inline yaml callsites: `apply-catering-owner-decision:230-236`, `create-catering-lead:343-347`, `lookup-prior-leads-by-phone:250`, `parse-menu-photo:256`, `apply-menu-update:75` | Task 5 (yaml migration) | All match pattern `Config.model_validate(yaml.safe_load(CONFIG_PATH.read_text(...)))`. Migrate to `load_yaml_model`. |
| `tests/_b1_helpers.py` lines 13-26 | Task 6 (conftest hoist) — pre-hoist v02 probe per design v2 §9.3 | docstring claims v02 helpers' `mod.__name__ = "__main__"` pattern "never actually executed". Pre-hoist probe pins the truth. |
| `tests/test_catering_v02_scripts.py` | Task 6 callsite migration | Helper-symbol callsite count to be measured pre-merge via rg per design v2 R4-H-T2 (108 was design estimate; rg from PR-D1 review showed 66 — exact number captured in commit message at build time). |
| `docs/catering-edge-cases.md` v3.1 | Task 7 (v3.2 revision) | Caps at C22; no C23/C24/C25. Revision is purely additive (no in-place tombstones); 4 already-enumerated drops handled via existing "Deferred cases" table at line ~524. |

---

## Branching strategy

- **Branch:** `feat/p1-medium-items-pr-d2` cut from main HEAD (post-PR-D1 merge `3f96c07`).
- **Sequencing pin (per design v2 §14.2 H2):** PR-D1 merged ✅ → PR-D2 branches from PR-D1-merged main → PR-B branches from PR-D2-merged main.
- **Pre-deploy gate:** operator runs `tools/check-pr-d2-rollback-target.sh <vps> 3f96c07` before PR-D2 deploy. Aborts unless PREV_TAG carries the PR-D1 SHA.

---

## Build sequence (7 commits per design v2 §14.5)

| # | Commit subject | Touches |
|---|---|---|
| 1 | `refactor(catering): migrate 5 inline yaml.safe_load callsites to load_yaml_model + emit config_load_failed on failure` | `src/agents/catering/scripts/{apply-catering-owner-decision,create-catering-lead,lookup-prior-leads-by-phone,parse-menu-photo,apply-menu-update}` + `tests/test_catering_config_migration.py` (NEW) |
| 2 | `fix(catering): apply-decision post-bridge re-load — matched_idx idiom + emit catering_quote_sent_lead_missing on missing lead + customer_phone_pre_bridge capture` | `apply-catering-owner-decision` + `tests/test_catering_apply_post_bridge_missing_lead.py` (NEW) |
| 3 | `feat(catering): apply-decision write-anchor BEFORE bridge POST + bridge_post_outcome two-step write + tail-scan helpers` | `apply-catering-owner-decision` + `tests/test_catering_apply_anchor_outcome.py` (NEW) |
| 4 | `feat(catering): apply-decision retry-state-machine — tail-scan checks both anchor AND quote_sent (closes v0.3 docstring-vs-reality gap)` | `apply-catering-owner-decision` + `tests/test_catering_apply_idempotent_replay.py` (NEW) |
| 5 | `test(catering): v02 probe — confirm helpers execute pre-conftest-hoist (rg-pinned callsite count in body)` | `tests/test_v02_probe.py` (NEW). Commit body captures probe outcome + rg count. |
| 6 | `refactor(tests): hoist BridgeStub + run_create + run_apply to tests/_shared_catering_helpers.py + conftest fixtures + _b1_helpers re-export + callsite-grep regression test` | `tests/_shared_catering_helpers.py` (NEW), `tests/conftest.py` (extend), `tests/_b1_helpers.py` (slim to re-export), `tests/test_catering_v02_scripts.py` (migrate callsites), `tests/test_helper_migration_complete.py` (NEW) |
| 7 | `feat(catering): catering-lead-reconcile operator script + 5 PR-A R3 test gaps + Case-B-to-C end-to-end recovery test + docs/catering-edge-cases.md v3.2 + decisions.log compact-JSON format test` | `src/agents/catering/scripts/catering-lead-reconcile` (NEW), `tests/test_catering_lead_reconcile.py` (NEW), `tests/test_catering_apply_case_b_to_c_recovery.py` (NEW), `tests/test_safe_io_load_status.py` (extend), `tests/test_safe_io_filelock.py` (extend), `tests/test_lookup_skill_md.py` (extend), `tests/test_decisions_log_format.py` (NEW), `docs/catering-edge-cases.md` (v3.2), `tests/test_catering_edge_cases_doc.py` (NEW) |

Total: ~440 src + ~270 tests + ~90 doc + ~25 SKILL = ~825 lines diff.

---

## Tactical decisions (already pinned in design v2 §14, restated for plan-review focus)

- **Task 1 (yaml migration):** each callsite gets `try: cfg = load_yaml_model(CONFIG_PATH, Config) except (FileNotFoundError, RuntimeError, ValidationError) as e: log_config_load_failed_best_effort(CONFIG_PATH, e); sys.stderr.write(...); return EXIT_SCHEMA_VIOLATION`. Existing `import yaml` block + `Config.model_validate(yaml.safe_load(...))` deletes.
- **Task 2 (matched_idx + customer_phone_pre_bridge):** insert `customer_phone_pre_bridge = lead.customer_phone` at apply-script line 301 (inside the FIRST LEADS_LOCK block, before lock release). Replace post-bridge for-loop at lines 378-385 with `next(...)` idiom; if matched_idx is None, call `log_quote_sent_lead_missing_best_effort` + Pushover P2 + return `EXIT_SCHEMA_VIOLATION`.
- **Task 3 (anchor two-step write contract):** write `CateringQuoteAttempted(bridge_post_outcome="unknown")` AT END of the FIRST LEADS_LOCK block, BEFORE bridge POST. After bridge POST returns (success or failed), the SECOND LEADS_LOCK block writes the second anchor row with the actual outcome. NDJSON append-only: tail-scan picks the LATEST matching row.
- **Task 4 (retry-state-machine):** replace "no AWAITING_OWNER_APPROVAL match → EXIT_NOT_FOUND" with the 3-row decision tree from design v2 §14.1 B-T1:
  1. If `quote_sent` row exists for `lead_id` → idempotent_replay=True, no bridge POST. Advance status to SENT_TO_CUSTOMER if still OWNER_APPROVED + emit recovered status row.
  2. Else if `anchor` exists with `outcome="success"` → emit synthesized `CateringQuoteSent(outbound_message_id="_recovered_<original>")`, advance status, idempotent.
  3. Else if `anchor` exists with `outcome in ("failed","unknown")` → re-attempt bridge POST (resume from Case A step 8).
  4. Else → existing fresh-attempt path.
  Tail-scan helpers `_tail_scan_anchor` + `_tail_scan_quote_sent` with N=5000 + 24h timestamp bound.
- **Task 5 (v02 probe):** runs BEFORE the hoist commit. Probe asserts `False` is reachable in v02 helpers' import path. Commit message captures result + rg count.
- **Task 6 (conftest hoist):** sibling-file pattern (`_shared_catering_helpers.py`) — NOT auto-loaded by conftest.py. `tests/conftest.py` adds fixtures that wrap the sibling-module helpers. `_b1_helpers.py` becomes a 3-line re-export. Callsite-grep regression test asserts `test_catering_v02_scripts.py` has zero `from _b1_helpers` imports post-migration.
- **Task 7 (reconcile script + test gaps + doc v3.2 + format test):** consolidated commit.
  - `catering-lead-reconcile` script per design v2 §8: arg-parser + LEADS_LOCK + status-validation + emit `CateringLeadStatusChange(actor="operator")` + `CateringLeadManuallyReconciled` (PR-D1 schema).
  - 5 PR-A R3 test gaps from `tasks/todo.md` lines 67-73: (a) `assert_load_status_clean` empty/whitespace, (b) negative attempts/sleep clamps, (c) corrupt-status integration, (e) lock-parent-dir, (f) ast-based LOOKUP_STATUS_*. Note: (d) was duplicate, removed.
  - `tests/test_catering_apply_case_b_to_c_recovery.py` — design v2 §14.1 B-T1: process death between anchor-write and bridge-POST, then retry, assert exactly-once bridge POST + outcome=success after.
  - `tests/test_decisions_log_format.py` — design v2 R3-H-Awk1: pin compact-JSON output (`"type":"x"` no space) so future Pydantic version drift surfaces here, not in the operational runbook awk command.
  - `docs/catering-edge-cases.md` v3.2 — design v2 §14.4 + plan v3 Task 14: changelog at top + 4 new cases (C23-C25) inserted between C22 and Deferred-cases table. NO in-place tombstones; existing Deferred-cases table at line ~524 absorbs the 4 already-enumerated drops.

---

## Out-of-scope (PR-B)

Per design v2 §14.2 M12: `lookup_invoked` LogEntry variant + SKILL preamble emission moved to PR-B (pairs with v0.4 deploy soak observability).

---

## Test plan (gated)

| Layer | Pre-merge gate |
|---|---|
| Schema (PR-D1 already shipped) | full suite green (374 currently) |
| Yaml migration (Task 1) | per-script subprocess test confirming load_yaml_model failure path emits `config_load_failed` + returns EXIT_SCHEMA_VIOLATION |
| Apply-script rewrite (Tasks 2-4) | 3 dedicated test files: post-bridge missing-lead (4 cases), anchor outcome two-step (5 cases), idempotent replay (5 cases per §6.3 + Case B/C end-to-end from §14.1 B-T1) |
| Conftest hoist (Tasks 5-6) | v02 probe + post-hoist callsite-grep regression test + full v02 suite green |
| Reconcile script (Task 7) | 8 cases per design v2 §8 (forbidden transitions, missing lead, corrupt store, happy path, audit-row content, invalid status, idempotent rerun rejection, --dry-run) |
| Doc v3.2 (Task 7) | static checks: case ID monotonicity + v3.2 changelog presence + 4 new cases present |
| Format invariant | `TypeAdapter(LogEntry).dump_json(...)` produces compact JSON (no space after colon) |
| Full suite | 374 + ~70 new tests = ~444 expected pass |

Smoke gate (per design v2 §9.2 R3-M-Smoke1) iterates ALL ~58 known LogEntry variants with minimal-fields fixture map — NOT in PR-D2 scope; that's a separate smoke-test extension.

---

## Deploy plan

1. Merge PR-D2 to main, tag `pr-d2-pre-deploy-<sha>`.
2. **Operator runs `tools/check-pr-d2-rollback-target.sh <vps> 3f96c07` first** — refuses unless PREV_TAG matches PR-D1 SHA.
3. Build tarball + scp + `shift-agent-deploy.sh`.
4. Pre-restart import gate runs both `check-safe-io-symbols` AND `check-audit-helpers-symbols`. Failure rolls back to PR-D1 (which has the shim → safe).
5. **20-min soak** (per design v2 §14.3 M9): apply-script live state-load path changes; longer than 5-min default to catch cross-script lock-ordering edge cases.
6. Soak watchlist:
   - `tail -f /opt/shift-agent/logs/decisions.log | grep -E '"catering_quote_sent_lead_missing"|"catering_quote_attempted"|"config_load_failed"'`
   - Watch for any `bridge_post_outcome="unknown"` row that doesn't get superseded within 30 seconds — indicates apply-script process death between anchor-write and post-bridge phase.
   - `journalctl -u hermes-gateway -f | grep -E 'BUG|EXIT_SCHEMA_VIOLATION|EXIT_DEPENDENCY_DOWN'`

---

## Self-review

- [x] Drift-check tag at top + Hermes-first checklist + read-deployed-code evidence.
- [x] No new SaaS-style infrastructure (no SQLite/Postgres/queues).
- [x] All audit-row writes go through `safe_io.ndjson_append` + `<path>.lock` flock or audit_helpers best-effort wrapper.
- [x] Forward-compat shim from PR-D1 is unmodified (PR-D2 only USES, never EXTENDS the union).
- [x] Tail-scan N=5000 + 24h timestamp bound (per design v2 §14.3 R2-MED-1).
- [x] `customer_phone_pre_bridge` capture site explicitly pinned to apply-script line 301 area.
- [x] M12 carryover: `lookup_invoked` deferred to PR-B.
- [x] Operator runbook (design v2 §7) referenced for divergence flow.
- [x] Test pyramid covers Case B-then-C end-to-end (the design-review BLOCKER B-T1 fix).

---

## Status: PLAN-DRAFTED, ready for 5-agent plan review

Reviewers should focus on:
1. **Drift correctness** — extends-Hermes is correct (no convention departure in PR-D2). Verify against deployed-code evidence.
2. **Retry-state-machine completeness** — does the 4-row decision tree in Task 4 cover every survivable process-death position in Cases A/B/C? Particularly: process death after second LEADS_LOCK release but before stdout flush.
3. **Test pyramid sufficiency** — does the 7-file test plan catch every failure mode the apply-script rewrite introduces? Are 4+5+5+5 cases enough for Tasks 2/3/4?
4. **Scope split honesty vs design v2 §14.5** — anything that should be in PR-D2 missing? Anything that belongs in PR-B leaked in?
5. **Yaml migration completeness** — 5 callsites OR 4? Re-grep the deployed code in case PR-D1 introduced a 6th callsite (PR-D1 was schema-only so should have been 0, but verify).
