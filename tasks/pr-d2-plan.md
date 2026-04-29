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

## Plan v2 — 5-agent plan-review revisions (BINDING)

This section supersedes any conflicting earlier text. Build phase reads from here first.

### v2.1 BLOCKERs (must be fixed before any build commit)

#### B-1 (R2) — Position 5/6 duplicate-quote window: post-bridge write reorder

**Problem:** plan v1 Task 3 kept the deployed write-order: bridge POST → re-acquire LEADS_LOCK → status=SENT_TO_CUSTOMER → atomic_write → `CateringLeadStatusChange` → `CateringQuoteSent` → `CateringQuoteAttempted(outcome="success")`. If the apply-script process dies between bridge-POST-success and the `CateringQuoteSent` write, retry sees anchor=unknown + no quote_sent + status=OWNER_APPROVED. Task 4 row 3 ("anchor outcome=unknown → re-attempt bridge") fires → customer gets quote a second time.

**Resolution (binding canonical write order — Tasks 3+4 build from this):** inside the SECOND LEADS_LOCK block, immediately after bridge POST returns `ok=True`:

```python
# Step 1: write CateringQuoteSent FIRST — the only retry-defeating signal.
#         Append-only NDJSON; written before any state mutation. If process
#         dies after this row but before step 2, retry's quote_sent tail-scan
#         finds it → idempotent_replay short-circuit (Task 4 row 1).
_append_log_with_outer_leadslock(
    TypeAdapter(CateringQuoteSent),
    CateringQuoteSent(type="catering_quote_sent", ts=now, lead_id=...,
                      customer_phone=customer_phone_pre_bridge,
                      outbound_message_id=mid_or_err),
)
# Step 2: write success-anchor superseding the step-6 outcome="unknown" anchor.
_append_log_with_outer_leadslock(
    TypeAdapter(CateringQuoteAttempted),
    CateringQuoteAttempted(type="catering_quote_attempted", ts=now, lead_id=...,
                           original_message_id=..., code=...,
                           bridge_post_outcome="success"),
)
# Step 3: mutate state (last because audit-rows are append-only NDJSON;
#         if this step dies, retry's quote_sent short-circuit advances state).
matched_idx = next((i for i,l in enumerate(store.leads) if l.lead_id == lead_id_for_output), None)
if matched_idx is None:
    log_quote_sent_lead_missing_best_effort(...)
    _pushover_p2(...)
    return EXIT_SCHEMA_VIOLATION
store.leads[matched_idx] = store.leads[matched_idx].model_copy(update={
    "status": "SENT_TO_CUSTOMER",
    "updated_at": customer_now(...),
})
atomic_write_json(LEADS_PATH, store)
# Step 4: status-change audit row.
_append_log_with_outer_leadslock(
    TypeAdapter(CateringLeadStatusChange),
    CateringLeadStatusChange(...from_status="OWNER_APPROVED",
                             to_status="SENT_TO_CUSTOMER",
                             actor="system", reason="customer_send_succeeded"),
)
```

This reorder makes the retry-state-machine's row 1 ("quote_sent found → idempotent") actually short-circuit at every position 5/6/7 process-death point. The bridge POST is exactly-once even under SIGKILL.

**Test coverage (per R3-H-1 below):** `tests/test_catering_apply_case_b_to_c_recovery.py` MUST contain BOTH `test_process_dies_after_anchor_before_bridge` AND `test_process_dies_after_bridge_before_success_anchor`. The second test pins the reorder.

#### B-2 (R5) — multi-VPS canary deploy strategy

**Problem:** plan v1 Deploy step says "build tarball + scp + shift-agent-deploy.sh" (singular). Triveni runs 9 VPS. Parallel rollout would consume all 9 rollback slots simultaneously on apply-script bug.

**Resolution:** add `Deploy step 0 — canary` to plan deploy section:

1. **Step 0**: pick lowest-traffic VPS as canary (suggest the most recently quiet by `dispatcher-accuracy-report --days 1`).
2. **Step 1**: operator runs `tools/check-pr-d2-rollback-target.sh <canary-vps> 3f96c07`.
3. **Step 2**: deploy PR-D2 to canary; **60-minute** soak with synthetic-retry probe (per R5-H-2 below) at minute 5.
4. **Step 3**: only if canary clears all soak watchlist signals AND synthetic-retry probe succeeds, bulk-deploy to remaining 8 VPS staggered ~2 min apart (so smoke-fail rollback per VPS doesn't lose more than 1 VPS at a time to in-flight rollback).
5. **Step 4**: 20-min soak per non-canary VPS (per design v2 §14.3 M9; canary already burned the longer window).

### v2.2 HIGH

#### R2-HIGH-1 — N=5000 + 24h binding at Triveni-scale

**Resolution:** raise tail-scan defaults: `max_lines=5000` stays, but `max_age_hours` raised from 24 → **96** (covers Friday-quote-Monday-approve weekend window). New observability: emit `tail_scan_truncated` audit row whenever scan hits `max_lines` without `max_age_hours` exhaustion. Helper signature update:

```python
def _tail_scan_anchor(
    log_path: Path, code: str,
    max_lines: int = 5000,
    max_age_hours: float = 96.0,  # was 24 in design v2 §14.3
) -> Optional[CateringQuoteAttempted]:
    """... see design v2. PR-D2 plan v2 R2-HIGH-1: raised max_age_hours to
    96 (4-day weekend window) to handle Friday-quote-Monday-approve
    legitimate retry. Emits tail_scan_truncated row if max_lines hit
    before max_age_hours exhaustion (signals fleet-scale capacity drift)."""
```

NEW LogEntry variant **deferred to PR-D3** (small scope; doesn't block PR-D2). For PR-D2 instead: `tail_scan_truncated` is emitted as `_UnknownLogEntry`-style row via direct `ndjson_append` with `{"type": "tail_scan_truncated", "ts": ..., "code": ..., "max_lines": ...}`. Soak watchlist greps for it. **Decision: instead, tail_scan helpers log via `sys.stderr.write` only (which lands in journald) — no NDJSON emission.** Avoids growing the discriminated union further in PR-D2.

#### R2-HIGH-2 — Idempotent_replay status-advance LEADS_LOCK contract pin

**Resolution:** add explicit pin to plan Task 4 tactical decision:

> Task 4 row 1 (quote_sent found): status advance happens INSIDE the same LEADS_LOCK block that performs the tail-scan. Sequence: `tail-scan(under_lock) → if quote_sent found → matched_idx via next() → mutate store.leads[matched_idx] → atomic_write_json → emit CateringLeadStatusChange(actor="system", reason="idempotent_replay_recovered") → release lock`. NEVER advance status without the lock; NEVER emit the audit row before atomic_write succeeds.

#### R3-HIGH-1 — Case B-then-C needs 2 tests

**Resolution:** plan Task 7 `tests/test_catering_apply_case_b_to_c_recovery.py` description updated from "1 test" to "2 tests":

1. `test_process_dies_after_anchor_before_bridge`: monkeypatch `_bridge_post` to raise `SystemExit` AFTER anchor=unknown written, BEFORE HTTP call. Retry: real bridge. Assert `BridgeStub.requests == 1` (one POST total in run 2), tail-scan finds anchor=success after both runs, single `catering_quote_sent` row.
2. `test_process_dies_after_bridge_before_success_anchor`: monkeypatch the post-bridge step to die AFTER bridge returns success, BEFORE step 1 of the reorder (the `CateringQuoteSent` write). Retry: bridge POST NOT re-attempted (because reorder ensures this scenario can't actually happen — but test verifies the no-quote_sent + anchor=unknown path correctly resumes from Case A step 6 rather than re-POSTing the same code). Specifically, the second test must demonstrate that with the post-bridge reorder, the window is closed: the `CateringQuoteSent` row is the FIRST audit-row written after bridge return, so no death-window leaves anchor=unknown + bridge-was-actually-successful + no-quote_sent.

#### R3-HIGH-2 — stronger v02 probe assertion

**Resolution:** plan Task 5 probe strengthened. Probe inserts a sentinel side-effect inside one of the v02 helpers' import path:

```python
# tests/test_v02_probe.py
def test_v02_helpers_main_body_executes(tmp_path: Path, monkeypatch):
    """Stronger than design v2 §9.3: not just hasattr(mod, 'main') but
    proves the body of main() (or its loaded module) executes during
    the v02 import pattern. If the helpers' importlib pattern truly
    'never executed' (per _b1_helpers.py docstring claim), this fails."""
    # Insert sentinel via monkeypatching a known import target
    sentinel = tmp_path / "v02_executed_sentinel"
    # Use one of the v02 test bodies (subprocess invocation of create-catering-lead)
    # The script's `if __name__ == "__main__": sys.exit(main())` block runs only if
    # mod.__name__ is set to "__main__" before exec_module — which is the v02 pattern.
    # Monkeypatch a function in the module path that, if called, writes the sentinel.
    # ... (probe implementation written at build time; the assertion is sentinel.exists()
    #     after running one v02 test fixture in subprocess)
    # If sentinel exists: v02 helpers DID execute → hoist preserves real behavior.
    # If absent: docstring claim was correct → hoist surfaces real bugs (flag in commit).
```

Commit message records observation either way.

#### R5-H-1 — bypassable rollback gate + 9-VPS amplification

**Resolution:** **out-of-scope for PR-D2 build phase**, but plan must explicitly document mitigation:

- The R4-H-2 broken-tarball eviction (PR-D1) means: if operator skips the gate AND no other deploy lands between PR-D1 and PR-D2, rollback is safe.
- With CANARY strategy (B-2 above), risk is concentrated on canary VPS only; bulk-deploy follows clean canary, reducing window for inter-deploy hotfix.
- For 9-VPS-scale enforcement: track as PR-D3 follow-up — embed expected-PREV-SHA in tarball metadata + `shift-agent-deploy.sh` refuses if PREV_TAG SHA doesn't match. Estimated 30 LOC + 1 test.

Plan adds explicit follow-up note in §"Out-of-scope (PR-B + PR-D3 carryover)".

#### R5-H-2 — 20-min soak misses retry path

**Resolution:** add synthetic retry probe to canary VPS soak (B-2 step 2 minute 5):

```bash
# tools/synthetic-retry-probe.sh — runs ONCE at minute 5 of canary soak
# 1. Create test catering lead via direct script invocation (--test-mode flag).
# 2. Simulate owner-approve.
# 3. SIGKILL apply-script process between anchor-write and bridge POST.
# 4. Trigger retry (re-invoke apply-decision with same code).
# 5. Assert: bridge_post_outcome transitions unknown→success in audit, exactly
#    one catering_quote_sent row, no duplicate customer message via WhatsApp
#    bridge.
# 6. Cleanup: delete synthetic test lead.
```

Probe ships as a new tool in PR-D2 commit 7. **20-min soak per non-canary VPS** (B-2 step 4) does NOT run the probe (overhead too high for 9-VPS bulk).

#### R5-H-3 — live-state migration: in-flight lead at OWNER_APPROVED with no anchor

**Problem:** PR-D2 ships while a lead is mid-approve under old code: state=OWNER_APPROVED, no `CateringQuoteAttempted` anchor, no `CateringQuoteSent`. The matcher (line 256-258) filters status=`AWAITING_OWNER_APPROVAL` only → EXIT_NOT_FOUND on retry → quote never sent.

**Resolution:** extend Task 4 retry-state-machine to handle this case explicitly. Decision tree updated:

```python
quote_sent = _tail_scan_quote_sent(LOG_PATH, lead_id, ...)
anchor = _tail_scan_anchor(LOG_PATH, code, ...)

if quote_sent is not None:
    # Row 1: idempotent_replay (covers R5-H-3 if quote_sent was somehow recorded)
    ...
elif anchor is not None and anchor.bridge_post_outcome == "success":
    # Row 2: bridge succeeded but quote_sent missing — synthesize CateringQuoteSent
    # (post-reorder this is unreachable in normal flow; defensive only)
    ...
elif anchor is not None and anchor.bridge_post_outcome in ("failed", "unknown"):
    # Row 3: bridge may have failed — re-attempt
    ...
elif (code matches a lead with status="OWNER_APPROVED" or "OWNER_EDITED") and anchor is None:
    # Row 4 NEW (R5-H-3): in-flight lead at OWNER_APPROVED under old code
    # at PR-D2 deploy moment. Treat as fresh attempt: write anchor outcome="unknown",
    # bridge POST, then post-bridge sequence per B-1 reorder.
    # No backfill script needed — apply-script self-heals on retry.
    sys.stderr.write(f"recovery: retry on OWNER_APPROVED lead with no anchor "
                     f"(PR-D2 live-state migration) — proceeding as fresh attempt\n")
    # ... resume Case A step 6
else:
    # Row 5 (existing fresh-attempt path): no match
    return EXIT_NOT_FOUND
```

Test in Task 4 file: `test_owner_approved_no_anchor_self_heals_on_retry`.

### v2.3 MEDIUM

#### R1-M-3 — naming consistency note

Plan v1 §"Tactical decisions" Task 7 mentions `CateringLeadManuallyReconciled` (correct, post-PR-D1). Design v2 §8 body still references the pre-rename `CateringLeadManualReconcile`; design v2 §14.2 R5-H-2 supersedes. Plan v2 explicitly aligns with the post-rename: `CateringLeadStatusChange(actor="operator")` AND `CateringLeadManuallyReconciled` (PR-D1 schema, 2 audit rows per reconcile invocation).

#### R2-MED-1 — Position 2 retry path under-specified

**Resolution:** addressed in R5-H-3 row 4 above. Position 2 (status=OWNER_APPROVED + no anchor + no quote_sent) maps to row 4 of the updated decision tree.

#### R3-M-1 — reconcile script same-state idempotency case (9th case)

**Resolution:** plan Task 7 reconcile test count grows from 8 to **9 cases**. New case: `test_reconcile_refuses_same_target_status` — operator passes `--target-status SENT_TO_CUSTOMER` against a lead already at SENT_TO_CUSTOMER. Refuse with EXIT_INVALID_INPUT + stderr "lead already in target status". Avoids zero-delta audit-log churn.

#### R3-M-2 — format invariant test parametrized over all variants

**Resolution:** `tests/test_decisions_log_format.py` parametrizes over `_KNOWN_LOG_ENTRY_TYPES` with the same minimal-fields fixture map planned for the design v2 §9.2 R3-M-Smoke1 smoke gate. Fixture map inlined in the test file.

#### R5-M-1 — soak watchlist gaps

**Resolution:** plan Deploy step 6 watchlist extended:

- Pairing check: `awk 'BEGIN{FS=","} /catering_quote_attempted.*outcome="failed"/ {failed[$0]=1} /catering_quote_attempted.*outcome="success"/ {delete failed[$0]} END{for(k in failed) print k}'` — any failed without a superseding success row within the same scan window = apply-script crashed during retry.
- Threshold: any single `catering_quote_sent_lead_missing` occurrence pages operator (not just visible in tail).
- Apply-script exit-code rate: `journalctl -u hermes-gateway --since "20m ago" | grep -E "EXIT_(SCHEMA_VIOLATION|DEPENDENCY_DOWN|NOT_FOUND)" | grep -i catering | wc -l` — non-zero rate triggers investigation.

### v2.4 LOW

R1-L-1, R1-L-2, R3-L-1 (tombstone integrity in doc test), R5-L (cosmetic LOC math) — all addressed inline in tactical decisions or accepted no-op.

### v2.5 Updated build sequence

Same 7 commits as plan v1 §Build sequence; tactical changes within commits per §v2.1 + §v2.2:

| # | Commit subject | What changed in v2 |
|---|---|---|
| 1 | yaml migration | (no change) |
| 2 | matched_idx + customer_phone_pre_bridge | (no change) |
| 3 | anchor two-step write + tail-scan helpers | tail-scan `max_age_hours=96` (R2-H-1); helpers emit stderr `tail_scan_truncated` line on cap-hit |
| 4 | retry-state-machine | **5-row decision tree** (was 4): added row 4 OWNER_APPROVED-no-anchor self-heal (R5-H-3); status-advance lock contract pinned (R2-H-2); post-bridge write reorder (B-1) |
| 5 | v02 probe | strengthened to sentinel-based assertion (R3-H-2) |
| 6 | conftest hoist | (no change) |
| 7 | reconcile + tests + doc | reconcile gains 9th same-state-refuse test (R3-M-1); 2 Case-B-then-C tests (R3-H-1); format test parametrized (R3-M-2); synthetic-retry probe tool (R5-H-2); doc tombstone integrity check (R3-L-1) |

### v2.6 Updated deploy sequence (canary)

| Step | Action |
|---|---|
| 0 | Pick canary VPS (lowest-traffic) |
| 1 | Operator runs `tools/check-pr-d2-rollback-target.sh <canary> 3f96c07` |
| 2 | Deploy PR-D2 to canary; 60-min soak; synthetic-retry probe at minute 5 |
| 3 | If canary clears: bulk-deploy remaining 8 VPS staggered 2-min apart |
| 4 | 20-min soak per non-canary VPS (no synthetic probe) |

### v2.7 Out-of-scope (PR-B + PR-D3 carryover)

- PR-B: `lookup_invoked` LogEntry variant + SKILL preamble emission (per design v2 M12)
- PR-D3: non-bypassable rollback gate via tarball metadata (R5-H-1 follow-up)
- PR-D3: `tail_scan_truncated` LogEntry variant (R2-H-1 — NDJSON observability instead of stderr-only)

### Status: PLAN-REVIEWED, design phase BLOCKED until B-1 + B-2 lock down in design

Plan-review surfaced 2 BLOCKERs that prevent design phase from advancing safely. Resolution paths documented above. Design phase should:
1. Apply B-1 fix (write-order reorder) as binding canonical sequence in §6.3.
2. Apply B-2 fix (canary VPS strategy) as binding deploy plan.
3. Apply 9 HIGH + 6 MEDIUM tactical updates per §v2.2 + §v2.3.
4. Then dispatch 5 design-reviewers.

---

## Status (v1, superseded by §v2): PLAN-DRAFTED, ready for 5-agent plan review

Reviewers should focus on:
1. **Drift correctness** — extends-Hermes is correct (no convention departure in PR-D2). Verify against deployed-code evidence.
2. **Retry-state-machine completeness** — does the 4-row decision tree in Task 4 cover every survivable process-death position in Cases A/B/C? Particularly: process death after second LEADS_LOCK release but before stdout flush.
3. **Test pyramid sufficiency** — does the 7-file test plan catch every failure mode the apply-script rewrite introduces? Are 4+5+5+5 cases enough for Tasks 2/3/4?
4. **Scope split honesty vs design v2 §14.5** — anything that should be in PR-D2 missing? Anything that belongs in PR-B leaked in?
5. **Yaml migration completeness** — 5 callsites OR 4? Re-grep the deployed code in case PR-D1 introduced a 6th callsite (PR-D1 was schema-only so should have been 0, but verify).
