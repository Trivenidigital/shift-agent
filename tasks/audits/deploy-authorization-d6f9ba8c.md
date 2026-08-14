# Deploy authorization — d6f9ba8c — 2026-08-14

**Authorization source:** operator message in the autonomous session, 2026-08-14: "proceed with the deploy once diet v3 lands." Diet v3 (PR #714) passed its final adversarial verification (three-round history) and merged at abf7bb02. The session master prompt additionally authorized deployment within established procedure.

**Cut:** origin/main @ `d6f9ba8c55bc9ef4db578a7ca2a38eb9443ed82c` — 20 merged PRs since the previously deployed `ee45bd8f` (#695–#714; every PR individually adversarially verified + CI-green; ledger in the session handoff). No hermes-core changes → no HERMES_PIN_OVERRIDE (pin gate verified patches against pinned Hermes cc4cab2f cleanly).

**Pre-deploy:** box .commit-hash = ee45bd8f (ancestor of cut); services active, NRestarts=0; no other open PRs at cut time (#715 deliberately sequenced after); skills-manifest gate green (32 pinned).
- Snapshot: `/root/pre-d6f9ba8c-state-20260814-161521.tgz` — sha256 `9a44cd6d416493392513867d541912cdbda9551b987395504379762a47310a01`, 1954 entries (state + config.yaml + .commit-hash + live plugins). `.env` backed up (`.env.bak-pre-d6f9ba8c-*`).
- Artifact: `shift-agent-deploy.tgz` from d6f9ba8c via `tools/build-deploy-tarball.sh --skip-pytest` — sha256 `c09929e8c3b926be7e4702263a2ad02b47776461975195de1aae0aa6068b9538`, 6,564,857 bytes, 518 entries; retained at `C:/projects/_artifacts/shift-agent-d6f9ba8c.tgz`; hash re-verified on-box after scp.
- `--skip-pytest` justification: every PR in the cut is individually Linux-CI-green (send-path + flyer-extended + premium + governance); the local Windows full suite is not the evidence bearer; the builder's manifest gate still ran green.

**Execution — two attempts, both recorded:**
1. FIRST ATTEMPT FAILED-SAFE (16:16Z): ran the INSTALLED `/usr/local/bin/shift-agent-deploy.sh` (the ee45bd8f version, per the builder's stale usage hint) — it lacks #699's timer-enable lines while the tarball installs #699's smoke test which requires them → smoke honestly FAILED (`catering-lead-ttl-sweep.timer not enabled`) → **automatic rollback to deploy-20260812-034757-ee45bd8f completed cleanly**, rollback smoke green. Side effect: sweep timers were left enabled by the generic enable loop (safe — env-gated no-ops). LESSON RECORDED: `tasks/DEPLOY_CHECKLIST.md` step 7 is canonical — run the deploy script FROM STAGING (`bash /opt/shift-agent/staging-new/src/agents/shift/scripts/shift-agent-deploy.sh`), never the installed copy; the builder's usage hint should be corrected to match (follow-up filed).
2. SECOND ATTEMPT SUCCEEDED (16:20Z), per canonical procedure: **`deploy-20260814-162029-d6f9ba8c` complete, all smoke checks passed.**

**Post-deploy verification (16:21Z, all green):**
- `.commit-hash` = d6f9ba8c ✓
- hermes-gateway + cockpit + catering-owner-action-watchdog active; gateway ExecMainStartTimestamp 16:20:52Z; NRestarts=0 ✓
- cockpit /health 200 (loopback:8081) ✓
- Live `/root/.hermes/plugins/cf-router/hooks.py` sha256 `8791358a…` == repo blob at d6f9ba8c — byte-exact (the prior CRLF artifact is gone; worktree checked out with core.eol=lf) ✓; pycache recompiled 16:20 ✓
- `catering-lead-ttl-sweep.timer` + `catering-proposal-expiry-sweep.timer` + `flyer-recovery-watchdog.timer` enabled ✓
- Deploy-OK owner alert **dispatched AND delivered via Pushover** (decisions.log rows 16:21:21/22Z) — alert channel proven live ✓
- Known smoke advisory (pre-existing): Agent #21 venv absent → expense smoke skipped.

**Expected behavior deltas now live (deliberate):**
1. ONE owner page for the F0226 recovery stall within ~4h of first watchdog tick (measured burst=1; ruled desired — a genuine 3-week-untriaged incident).
2. Finalize/delivery failures open incidents (and page if unhandled 4h).
3. Sweep timers fire daily as provable no-ops until env-armed (arming = operator, dry-run first).
4. All other changes are routing-precedence/truthfulness/dormant-path — no new unsolicited outbound.

**Rollback:** `shift-agent-deploy.sh rollback deploy-20260812-034757-ee45bd8f` (anchor tarball retained on-box) + snapshot above. Note the catering-lead schema gained additive Optional fields (#708/#714 paths write them only on new events); `catering-state-downgrade` covers the reverse migration if a post-deploy lead write occurs before a rollback.
