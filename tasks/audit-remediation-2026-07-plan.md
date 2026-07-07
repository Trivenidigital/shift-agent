# Audit Remediation — 2026-07

**Drift-check tag:** `extends-Hermes` — hardens deployed patterns (approval-code chokepoint, audit-log locking, corrupt-state fail-safe, kill-switch parsing) without introducing new substrate. No new storage engine, no new approval namespace, no Hermes-convention divergence.

**Branch:** `fix/audit-remediation-2026-07` (worktree `C:\projects\sme-agents-audit-remediation`, off `origin/main` @ 931c8d9).
**Boundary held:** implement + test + subagent-review + PR. **No deploy to live VPS** (operator-gated; money-path deploys stay gated per standing rule + the audit's own "before Stripe goes live" framing).

## Hermes-first capability checklist (per-step)

This is platform hardening of in-repo code, not agent-building — Hermes owns none of these steps, so the axis is `[harden-existing]` not `[Hermes]`/`[net-new]`. Tagged per hook format (`[net-new]` = custom in-repo code Hermes does not provide):

| Step | Tag | Note |
|---|---|---|
| 1. Unify `generate_unique_code` across 4 agents | `[net-new]` | approval-code gen is per-repo `#XXXXX` logic, NOT the Hermes approval-workflow primitive |
| 2. Add FileLock to decisions.log writers | `[net-new]` | `safe_io`/repo audit substrate; Hermes does not own the lock discipline |
| 3. Alert at corrupt-state quarantine site | `[net-new]` | `safe_io.safe_load_json` fail-safe; in-repo |
| 4. Permissive kill-switch parse | `[net-new]` | flyer env-flag parsing; in-repo |
| 5. Recovery `send_uncertain` idempotency + enable-gate | `[net-new]` | flyer recovery watchdog; in-repo |
| 6. Sick-call date-parse crash guard | `[net-new]` | shift handler; in-repo |
| 7. Move proposal audit append inside lock | `[net-new]` | shift script; in-repo |
| 8. python-jose floor bump + lockfile | `[net-new]` | web/backend deps; in-repo |
| 9. Broaden flyer CI | `[net-new]` | CI config; in-repo |

Red-flag check: >½ `[net-new]` normally means a missed Hermes capability — here inverted because this is hardening, not capability-building. The one real check (is approval-code gen a missed Hermes primitive?) resolves NO: it's deployed per-agent in-repo (`create-proposal:63`).

## Drift-rule self-checks (read-deployed-code evidence)

- ✅ Read `src/agents/shift/scripts/create-proposal` (generate_unique_code at line 63, the collision-retry chokepoint template) before drafting the S2-6 unification.
- ✅ Read `src/agents/catering/scripts/parse-menu-photo` (`_generate_unique_code` at line 115 — confirmed NO store/collision check) before tagging S2-6 a real defect.
- ✅ Read `src/platform/safe_io.py` (safe_load_json quarantine at 194-203; FileLock at 90; ndjson_append at 278) before drafting S2-7/S2-8.
- ✅ Read `src/agents/shift/scripts/update-proposal-status` (dump_model in PENDING_LOCK at 178 vs ndjson_append in LOG_LOCK at 190-191) before drafting the S3 lock-gap fix.
- ✅ Read `tasks/commerce-slice1-followup-backlog.md` (flock_state_path at :30) and `tasks/commerce-slice2-catering-deposit-followup-backlog.md` (watchdog :36, escalation :56) before DEFERRING S2-2/S2-3/S2-4.

## Disposition of every S1/S2 finding (verified against current origin/main)

| Finding | Verified @ origin/main | Disposition | Rationale |
|---|---|---|---|
| **S1-1** deposit double-charge crash window | present | **IMPLEMENT NOW — interim fail-closed guard** (own branch) | Decision subagent A: persist-before-send is WRONG (trades double-send for silent no-send). Interim = refuse 2nd mint when a non-terminal intent already exists for the lead (keyed on `originating_message_id=catering_deposit_{lead_id}`) + P1 alert + non-zero exit. Full auto-resend recovery DEFERRED to slice-3 (trigger below). |
| **S2-2** commerce store unlocked RMW | present | **DEFER — already backlogged** | `commerce-slice1-followup-backlog.md:30` (`flock_state_path`, scoped to slice-3 webhook concurrency). Not load-bearing until concurrent writer lands. |
| **S2-3/S2-5** money alert no durable fallback / mark_confirmed_failed no P1 | present | **DEFER — already backlogged** | `commerce-slice2-...backlog.md:56` — journald-only accepted in slice-2; escalate to Pushover "if pattern observed in production canary." |
| **S2-4** no commerce fsck/freshness watchdog | present (no commerce timer) | **DEFER — already backlogged** | `commerce-slice2-...backlog.md:36-42` — "acceptable in slice 2… add a watchdog at the slice-3 webhook PR." |
| **S2-6** 4 parallel approval-code generators; parse-menu-photo has NO collision check | present | **PARTIALLY closed** | Fixed: parse-menu-photo (zero-check → cross-pool) + extract-receipt (dict-iteration bug that silently skipped shift codes) + invariant test. RESIDUAL (regression reviewer): create-proposal + create-catering-lead stay OWN-pool-only (can shadow a sibling-pool code, ~N/28.6M — accepted). Full cross-pool unification deferred pending the inline-vs-shared-helper design decision (reviewer-a HIGH A1 mandates inline). NOT "fully closed". |
| **S2-7** decisions.log writers bypass FileLock | present | **IMPLEMENT (partial)** | flyer `account.py`/`manual_queue.py` → lock now. `commerce/audit.py` aligns with backlogged `flock_state_path` intent — lock now (low-risk, additive). |
| **S2-8** corrupt state silently quarantined, no alert | present | **IMPLEMENT NOW** | self-contained platform fail-safe; fires today w/o Stripe (disk error / bad edit). Review flagged S1-latent. |
| **S2-9** kill-switch fails OPEN on non-`"1"` | present (both files) | **IMPLEMENT NOW** | safety-critical, tiny, low-conflict. Panic switch that doesn't panic. |
| **S2-10** recovery `send_uncertain` re-send + no `cfg.flyer.enabled` gate | present | **IMPLEMENT NOW** | flyer send-safety; inert only via default `mode=off`. |
| **S2-11** ~74 flyer tests in no CI | present | **IMPLEMENT NOW** | CI config; guards the most-active agent. |
| **S2-12** fleet provisioning `curl\|bash` unpinned root | present (spec doc) | **DEFER — spec-stage** | `docs/fleet-provisioning.md` "not yet implemented." Trigger: when `install.sh`/provisioning is authored. |
| **S2-13** python-jose floor < CVE line; no lockfile | present (`>=3.3`, no lock) | **IMPLEMENT NOW** | floor bump `>=3.4.0` safe regardless of live version; add lockfile follow-up. |
| **S2-14** approval TTL/dead-man escalation has no in-tree driver | present | **DEFER — verify first** | driver may be the `.pyc`-only cf-router watchdog (Hermes-owned). Confirm existence before building a duplicate. |

### S3 batch (implement alongside, low-risk correctness)
- `handle-shift-sick-call:114` unguarded `date.fromisoformat` on impossible-date regex → crash drops absence. **IMPLEMENT.**
- `update-proposal-status:178/191` state write in `PENDING_LOCK`, audit append outside → gap. **IMPLEMENT** (mirror `create-proposal:154`).
- `flyer/account.py:951` `except Exception: pass` + unlocked `ndjson_append`. **IMPLEMENT** (with S2-7).
- `flyer/manual_queue.py:878` bare-substring `project_id=` needle → misroute. **IMPLEMENT** (`json.loads` compare).

## Systemic rule (the real finding)
Docs assert invariants the code doesn't enforce. Remediation adds, per fixed invariant, one test that FAILS if the invariant is violated. S2-6 is the template: unify `generate_unique_code` + a collision/uniqueness test that fails if any caller regresses to no-check.

## Sequencing (PRs by blast radius, each subagent-reviewed before finalize)
1. **PR-1 safe-core** (platform+shift+catering, no flyer/commerce-money): S2-6, S2-8, S3 sick-call, S3 update-proposal-status.
2. **PR-2 flyer-safety** (isolated from ~15 in-flight flyer branches): S2-9, S2-10, S2-7 flyer writers, S3 account.py, S3 manual_queue.
3. **PR-3 flyer-CI**: S2-11.
4. **PR-4 deps**: S2-13.
5. **S1-1**: per decision subagent.
6. **Deferred-with-trigger record**: S2-2/3/4/5/12/14 → cross-ref existing backlog; no code.

## Review outcomes + deferred triggers

**PR-1 multi-vector review (2 deep-reasoner subagents, distinct lenses): NO BUG-FOUND; safe to land.** Corrections applied: parse-menu-photo advisory reads switched to raw `json.loads` (a `load_model` scan would quarantine a corrupt sibling file as a side-effect); extract-receipt terminal-status list aligned (+NOT_CATERING); invariant-test docstring made honest about the text-grep gap + partial S2-6 closure; plan disposition corrected to PARTIAL closure.

**S2-8 decision (correctness reviewer): Option B, NOT A.** Do not inject `notify_owner_with_fallback` into `safe_load_json` (hot pure-IO primitive reached by every agent + unit tests; would page on advisory scans and pollute test_safe_io). Instead: a timer that globs the state root for `*.corrupt-*` and alerts once per new artifact; optional hybrid fast-path in `assert_load_status_clean` for state-mutating writers.

**Written trigger — S1-1 full recovery (record; BLOCKING prerequisite of slice-3):**
> Replace the interim fail-closed guard with lead-keyed get-or-create reuse (reuse the existing non-terminal intent + re-send its link instead of refusing) as a BLOCKING prerequisite of the slice-3 PR that enables `cfg.commerce.provider="stripe"` or a per-order dynamic `payment_checkout_url_template`. NOT gated on "if observed in canary" — a correctness bug must not wait to be observed. Re-send MUST be status-aware: never void an already-`sent` intent on transient re-send failure. Co-locate with the §12a watchdog (commerce-slice2-...backlog.md:36) + flock (commerce-slice1-...backlog.md:30) items already triggered to that PR.

## Approvals log (per standing rule 2026-07-02: recorded approval or it didn't happen)

| Action | Authorization source | Status | When |
|---|---|---|---|
| Create worktrees + branches `fix/audit-remediation-2026-07`, `fix/audit-flyer-safety-2026-07` off origin/main | User directive: "implement all necessary audit findings autonomously" | APPROVED-BY-DIRECTIVE (interpretive) | 2026-07-07 |
| Implement PR-A code (parse-menu-photo collision, extract-receipt dict-fix, sick-call guard, update-proposal-status lock, invariant test, python-jose floor) | Same directive | APPROVED-BY-DIRECTIVE | 2026-07-07 |
| Implement PR-B flyer code (kill-switch ×2, manual_queue misroute, account.py audit lock+stderr) | Same directive | APPROVED-BY-DIRECTIVE | 2026-07-07 |
| Local commit to the two feature branches | Directive "implement autonomously" + no-auto-commit rule "task-level authorization counts" | APPROVED-BY-DIRECTIVE (interpretive) | pending |
| Push to shared remote / open PRs | — | **NOT YET AUTHORIZED** — outward-facing; awaiting explicit operator go | — |
| Merge / deploy to live VPS | — | **NOT YET AUTHORIZED** — money-path deploy stays operator-gated (audit "before Stripe goes live") | — |
| DEFER S2-2/2-3/2-4/2-5 (money-path) to existing slice-3 backlog triggers | §7a drift-check found these already backlogged with written triggers | APPROVED-BY-CONVENTION | 2026-07-07 |
