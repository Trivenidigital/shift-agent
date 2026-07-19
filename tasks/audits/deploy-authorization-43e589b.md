# Deployment-Authorization Package — main `43e589b` (2026-07-19)

**Status:** READ-ONLY preparation. Nothing deployed, installed, restarted, repaired,
or reconfigured. Awaiting explicit deployment authorization.

## 1. Current production SHA and target — WITH A RANGE CORRECTION

- **Live:** `1d9e477` — `deploy-20260719-021030-1d9e4779` (verified `ls -1t
  /opt/shift-agent/deploys/`, 2026-07-19). **PR #622 is ALREADY IN PRODUCTION** —
  deployed 2026-07-19 02:10 under the operator's prior "Merge and deploy PR"
  authorization, all smoke checks green, gateway active since (13h+ clean soak).
- **Target:** `43e589b` = origin/main tip (verified fetch).
- **Therefore `live..43e589b` = exactly ONE commit: the #623 squash.** The reviewer's
  combined-range premise is corrected with evidence; #622's behavior summary is
  included below anyway for completeness.

## 2. Commit + changed-file inventory (`1d9e477..43e589b`)

One commit, 13 files, +2035/−344:
- Product: `src/platform/approval_code_pools.py` (new kernel),
  `src/plugins/cf-router/hooks.py` (F8), `src/plugins/cf-router/actions.py`
  (catering identity fallback), 4 generator scripts (create-proposal,
  create-catering-lead, parse-menu-photo, extract-receipt), `src/platform/schemas.py`
  (1 LogEntry variant), `src/agents/shift/scripts/shift-agent-deploy.sh` (guarded
  install block).
- Non-product: 3 test files, `tasks/DEFERRED.md`, `.github/workflows/send-path-ci.yml`.

## 3. Behavior + risk, per PR

**#622 (ALREADY LIVE, 13h clean):** eod-reconcile counting fix (positive-only:
un-breaks degraded snapshots); §12b owner-alert dispatched/delivered audit rows;
sick-call + expense inline-send audit rows; stub decline prose; 24 tests. Risk was
additive-audit only; soak shows no anomalies.
**#623 (THE DEPLOY):** behavior changes only on: (a) `#XXXXX` owner-code handling in
F8 — canonical order menu-before-catering + fail-closed refusal on multi-pool match
(census: ZERO live multi-pool codes → no-op on current data); (b) code GENERATION —
all 4 generators now exclude against all 4 pools under the shared lock (new lock
acquisition per issuance; bounded, fails closed); (c) catering lead lookup — canonical
identity as priority-4 fallback (lid-cache EMPTY → no-op on current data by
construction). Everything else inert until those paths run. Risk concentration: the
lock file is new (first issuance creates it); a lock-acquisition failure REFUSES
issuance (fail-closed, visible) rather than duplicating.

## 4. Migrations / dependencies / environment / cron / services / deploy-script

- Migrations: NONE. State-file shapes unchanged.
- Dependencies: NONE added. Locking uses in-repo `safe_io.FileLock` (stdlib `fcntl`),
  NOT the pip `filelock` package.
- Environment: no new/changed env required; optional overrides
  (`SHIFT_AGENT_CODE_POOL_LOCK`, retry knobs) unset on box (verified: 0 matches).
- Cron/systemd: no unit changes. Deploy-script: guarded install/remove block for the
  kernel (self-heals on rollback tarballs).

## 5. Locking dependency importable in production runtime

Verified on box: `python3 -c "import fcntl; sys.path /opt; from safe_io import
FileLock, try_acquire_filelock_with_retry, LockUnavailable"` → OK. `fcntl` is stdlib
(present in every CPython incl. the gateway's Hermes venv python). The kernel imports
only stdlib + `safe_io`. Post-deploy smoke re-proves the import under the gateway's
exact venv interpreter (`/root/.hermes/hermes-agent/venv/bin/python`).

## 6. Resolved production paths

State dir: `/opt/shift-agent/state` (no env override on box). Lock:
`/opt/shift-agent/state/approval-code-pools.lock` — absolute, on the same local
filesystem as all four pool stores (safe_io asserts local disk).

## 7. Ownership / permissions / writability per issuing process

- Gateway (`hermes-gateway.service`): `User=shift-agent Group=shift-agent`
  (systemctl-verified). State dir `drwxr-xr-x shift-agent:shift-agent` → gateway can
  create/lock the new lock file. Issues codes via cf-router → generator subprocesses.
- Operator-invoked scripts: run as root → unrestricted.
- `state/expense-bookkeeper` is `drwx------ shift-agent` → both identities fine
  (root bypasses; gateway owns).

## 8. All four issuance paths resolve the same kernel + lock on box

All 4 generators and cf-router import via the flat `/opt/shift-agent` sys.path →
single installed `approval_code_pools.py`; lock path derives from the one shared
state-dir default; no per-script overrides (grep-verified in the shipped sources).
Post-deploy smoke asserts `__file__` identity + lock-path equality under the venv
interpreter for each entry style.

## 9. Fail-closed on dependency/lock init failure

Unconditional top-level imports in all 4 generators (CI-asserted test): missing
kernel = hard ImportError, no unlocked fallback. Full-tree tarball deploy makes
"new scripts + missing kernel" unreachable. Lock timeout raises `LockUnavailable`
BEFORE any generation (CI-proven on Linux) → no code issued, error visible.

## 10. Fresh read-only census (2026-07-19, this package)

catering=17, menu-pending=0, expense=0, shift=1 · 18 live codes · **multi-pool
collisions: 0** · **lid-cache entries: 0**. Consequences: F8 order change and
collision refusal are no-ops on current data; identity fallback is a no-op on
current data; nothing in live state exercises a changed path on day one.

## 11. No-customer-impact smoke plan (post-deploy, read-only + tmp-scoped)

1. Deploy script's built-in smoke gate (already includes cf-router compile +
   classifier sanity + catering schema/transition checks) with auto-rollback.
2. `venv-python -c "import approval_code_pools; print(module __file__, CODE_POOL_CANONICAL_ORDER)"`.
3. `resolve_code("#ZZZZZ")` dry-run (nonexistent code → None; touches nothing).
4. Lock acquire/release against a TMP state dir (never the real lock during smoke).
5. NO sends, NO state writes, NO customer-visible action.

## 12. Post-deploy log/metric checks

Gateway active + journal clean; decisions.log flowing (brief/eod heartbeat rows);
ZERO `approval_code_collision_detected` rows expected (any row = surfaced legacy
collision → operator attention, not rollback); first organic issuance writes its
pool row normally + creates the lock file; alert-integrity watchdog stays quiet.

## 13. Rollback

`shift-agent-deploy.sh rollback deploy-20260719-021030-1d9e4779` (tarball retained,
verified present). Guarded block removes the kernel module; old scripts never import
it. Data behavior: NO migrations to unwind; state shapes unchanged; any new-variant
audit rows already appended (collision/owner-alert/sick-call/expense-reply) are
tolerated by older readers via the `_UnknownLogEntry` forward-compat shim
(append-only log). Rollback is data-safe and self-healing.

## 14. #622 ↔ #623 interaction risk

Complementary, no conflict: #623's collision alert flows through the owner-notify
chokepoint that #622 newly instrumented (dispatched/delivered rows) — a collision
would now be doubly visible. Disjoint LogEntry variants; disjoint behavior surfaces;
#622 has 13h clean live soak. No shared-file merge hazards remain (both are in main).

## 15. Nothing rides along

Code-only tarball from clean `43e589b`. No runtime flags, no notification settings
(Pushover stays muted), no data repairs, no DEFERRED-item reconciliation, no env
edits. The build's pytest gate + skills-manifest lockfile enforce tarball integrity.

## Cross-user lock proof (reviewer-mandated, 2026-07-19) — FAILED root-first; deploy held

Probes run on the box with the production filesystem, `/opt/shift-agent` safe_io
FileLock, system python3 (the generators' interpreter), disposable lock files in
`/opt/shift-agent/state`, `runuser -u shift-agent` for the service identity. Both
identities report umask `022`; FileLock opens `O_RDWR|O_CREAT, 0o640`.

- **PROBE 1 root-first: FAIL.** root acquires+releases (file `root:root 0640`);
  shift-agent then gets `PermissionError EACCES` at open — cannot acquire. The
  failure is an exception BEFORE any lock body (fail-closed, no unlocked fallback),
  but gateway issuance would be REFUSED until the file is fixed.
- **PROBE 2 service-first: PASS both ways.** shift-agent creates
  (`shift-agent:shift-agent 0640`); root then acquires+releases fine (DAC bypass).
- **PROBE 4 canonical path:** `/opt/shift-agent/state/approval-code-pools.lock`
  does not exist yet (kernel undeployed). Context: EVERY existing sibling
  `*.json.lock` in state is `shift-agent:shift-agent` — the de-facto fleet pattern
  (gateway created them first historically), which is why this pre-existing
  asymmetry has never bitten.
- Bounded timeout verified (`try_acquire_filelock_with_retry`, attempts=2); neither
  identity has any unlocked fallback path.
- Disposable probe files removed; canonical path untouched.

**Verdict per pass condition: DO NOT DEPLOY as-is.**

**Smallest correction (operational, deploy-script only — the shape the reviewer
pre-approved for operational review):** deploy-time precreation of the canonical
lock as the service identity, idempotent and inode-preserving:

```bash
# install_artifacts(): canonical approval-code lock must be owned by the service
# identity BEFORE any issuance path can create it as root (probe 2026-07-19:
# root-first creation yields root:root 0640 -> shift-agent EACCES -> issuance
# refused). Mode 0660 documented: owner+group rw; root bypasses DAC regardless.
LOCK=/opt/shift-agent/state/approval-code-pools.lock
if [ ! -e "$LOCK" ]; then
    install -o shift-agent -g shift-agent -m 0660 /dev/null "$LOCK"
else
    chown shift-agent:shift-agent "$LOCK"; chmod 0660 "$LOCK"   # never replaces the inode
fi
```

Post-deploy verification: re-run both disposable probes + acquire/release the
canonical lock under BOTH identities with the exact FileLock code. (Mode 0660
chosen over the fleet's 0640 to also cover a future non-root operator in the
shift-agent group; 0640 would suffice today because root bypasses DAC — flagging
both for the reviewer's mode ruling.)

**Phrasing revision per reviewer:** section 10's conclusion is restated as: "the
newly protected conflict cases are absent in the current census" — imports, F8
resolution order, and issuance-path lock behavior DO change at deployment even
with an empty cache and zero collisions.

## Approvals log

- 2026-07-19: reviewer closeout of #623 + instruction to prepare this package
  (read-only). Deployment: PENDING explicit authorization. PR-R2 preflight may be
  prepared read-only; PR-R3/R4 held.
- 2026-07-19: reviewer-mandated cross-user lock proof executed (disposable probes
  only). Root-first FAILS → deploy HELD; operational correction proposed above,
  awaiting reviewer ruling. No product code, runtime config, or data touched.
