# Retrospective — Portal migration to srilu (PR #81)

**Drift-check tag:** `extends-Hermes`

**Date:** 2026-05-10
**PR:** [#81](https://github.com/Trivenidigital/shift-agent/pull/81) — squash-merged as `875db7f`
**Live URL:** `http://89.167.116.187:8080/`
**Branch:** `feat/portal-migration-srilu` (deleted post-merge)
**Pipeline:** plan → 2 plan-reviewers → design → 2 design-reviewers → build → PR → 3 PR-reviewers → merge → deploy → retro (this doc)

---

## What shipped

- `src/platform/systemd/triveni-portal.service` — Type=simple unit running `python3 -m http.server 8080 --directory /opt/triveni/portal --bind 0.0.0.0` as `shift-agent` user. Hardening matches `hermes-gateway.service` convention: `NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths=/opt/shift-agent/logs`, `ProtectHome=read-only`, `PrivateTmp`. Final unit is 32 LOC.
- `tools/deploy-portal.sh` — standalone deployer (~130 LOC bash). scp + install + systemctl restart + dual smoke-verify (internal curl + external curl with WARN demotion). Decoupled from `shift-agent-deploy.sh` so portal HTML edits never trigger the agent-deploy gauntlet.
- `tasks/portal-main-vps-retirement.md` — tracker doc for ≥7-day soak then retire main-vps portal at `46.62.206.192:8080/portal/`.
- 3 task docs (plan + design + this retro) + 2 hermes-check receipts.

**Total LOC:** 597 across 7 files (588 from base commit + 9 LOC reviewer fixes in the second commit).

---

## Live deploy verification

```
=== Pre-flight (remote port :8080 availability) ===
✓ port :8080 free
=== Install + restart ===
Created symlink /etc/systemd/system/multi-user.target.wants/triveni-portal.service → ...
✓ installed + restarted; service active
=== Internal smoke (ssh + curl localhost:8080) ===
✓ internal smoke: 3 'SMB-Agents' hits
=== External smoke (local curl to 89.167.116.187:8080) ===
✓ external smoke: 3 'SMB-Agents' hits
```

main-vps portal at `46.62.206.192:8080/portal/` confirmed still serving (HTTP 200, 3 SMB-Agents hits) — slow-decommission invariant intact.

---

## Pipeline outcomes by stage

| Stage | Reviewers | BLOCKERs | MEDIUMs | Outcome |
|---|---|---|---|---|
| Plan v1 | 2 | 0 | 1 | M1: agent-deploy coupling → pivoted to standalone script |
| Design v2 | 2 | 1 | 6 | B1: `ProtectSystem=strict` w/o `ReadWritePaths` would EROFS at startup. Fixed in service file. |
| PR build | 3 (orthogonal vectors) | 0 | 4 (R1) + 0 (R2) | R1-M1+M2+M3 applied as second commit; R1-M4 deferred; R2 (prod-state) all 11 assumptions verified PASS on srilu; R3 (scope) flagged GitHub-Pages but operator-directive settled host constraint. |

---

## What worked

- **R2-B1 caught a real BLOCKER at design time.** Without `ReadWritePaths=/opt/shift-agent/logs`, the systemd unit would have crash-looped on first `restart` because `ProtectSystem=strict` mounts `/opt` read-only and `StandardOutput=append:` would EROFS. Fix landed in build, deploy succeeded first attempt.
- **Three-vector PR review (CLAUDE.md §8 — structure + prod-state + scope/judgment) produced orthogonal findings.** R1 caught code-correctness MEDIUMs, R2 verified runtime assumptions, R3 challenged the scope itself. Three same-axis reviewers (e.g., three "code-reviewer" agents) would have converged on style/lint and missed the structural and scope axes entirely.
- **Two-step SSH pattern (`ssh > file ; Read file`) worked cleanly** for all 11 R2 prod-state assumptions and the deploy itself. No empty-stdout pitfalls.
- **The `--skip-external-smoke` flag (R1-M2)** isn't exercised this run (external worked) but documents the operator-network failure mode for future runs.
- **R1 reviewer fixes (M1+M2+M3) cleaned up real semantic problems** that wouldn't have crashed the deploy but would have produced confusing failure modes later. Stop-before-port-check + is-active-before-rm + drop-no-op-StartLimitBurst are all small improvements that compound.

---

## What was avoidable / what to learn

### 1. The "must be on srilu" constraint was never validated upfront

R3 (scope reviewer) flagged this: the plan compares python `http.server` vs nginx for hosting on srilu, but never compares "host on srilu" vs "host on a free public CDN" (GitHub Pages, Cloudflare Pages, Vercel). For a single-file static-HTML portfolio brochure with no fetch / API / WebSocket traffic, public CDN is the textbook choice — zero infra, free HTTPS, push-to-deploy from `main`.

**Why it didn't matter for THIS PR:** the operator directive was explicit ("Migrate portal to Srilu"), so the constraint was settled by operator intent. R3's critique is valid as forward-looking architectural input, not as PR-blocking feedback.

**Why it matters going forward:** the next time a similar "host this content somewhere" task comes up, the plan should ask the user *what the actual constraint is* (host control? IP-locking? authentication path? simple reachability?) BEFORE choosing infrastructure. The 1-question discovery dialog is cheap; ~600 LOC of self-imposed infra is not.

**Captured rule:** before any "deploy static content" task, ask the user whether public CDN hosting is acceptable. If they say srilu/own-infra anyway, ship that — but ask first. Not asking risks 600 LOC of infra in service of a constraint the user wouldn't have insisted on.

### 2. v1→v2 plan pivot added ~120 LOC of self-imposed scope

The v1 plan was ~6 LOC (2 install lines added to `shift-agent-deploy.sh`). v2 pivoted to a 130-LOC standalone script citing decoupling. The decoupling argument is real — portal HTML edits shouldn't trigger the agent-deploy gauntlet — but it's a Day-2 ergonomics concern, not a Day-1 correctness concern. The portal hasn't been edited daily; current cadence wouldn't have made the coupled-deploy delay painful.

**Pattern:** reviewer-driven scope inflation is a real risk in this multi-stage pipeline. Plan reviewers caught the M1 ("decoupling missing") legitimately, but the resulting scope expansion went unchallenged because the same lens that flagged M1 ("would coupling be painful?") didn't quantify *how often it would be painful at current cadence*.

**Captured rule:** when a plan reviewer flags missing decoupling / missing infrastructure, the design phase should explicitly answer *how often does the missing piece bite at current cadence?* If the answer is "rarely or never given current usage," defer the decoupling to a follow-up triggered by observed pain — rule-of-three for deploy lanes, not just for nginx vs http.server.

### 3. The retirement tracker pattern is honest but operator-discipline-dependent

`tasks/portal-main-vps-retirement.md` describes a 5-step ≥37-day retirement plan (7-day soak → backup tarball → redirect stub → 30-day hold → retirement PR). No cron, no reminder. Past trackers in this repo do get pruned when work ships, so it's not dead-letter — but it's not self-driving either.

**Mitigation if it slips:** the redirect stub would degrade to a failed-curl on the old URL, but operators rarely browse `46.62.206.192:8080/portal/` directly anymore (the new URL is in everyone's heads after this PR). Worst case: silent dangling URL, no production impact.

**Acceptable for v0.1.** If a similar pattern recurs (next time we slow-decommission infrastructure), consider scheduling the retirement check via the agent's cron / CronCreate facility instead of relying on memory.

---

## Drift-rule + Hermes-first compliance

- ✅ Read-deployed-code: `hermes-gateway.service` (security-hardening template), `send-daily-brief.service` (Type=oneshot vs simple comparison), `shift-agent-deploy.sh` `install_artifacts()` (deploy-script convention), `web/portal/index.html` (verified 3 substantive `SMB-Agents` matches → smoke threshold ≥1 has good margin), `tools/build-deploy-tarball.sh` (confirmed it doesn't tar `web/` — validates separate-deploy-script decision).
- ✅ Drift-check tags applied to plan, design, and this retro (`extends-Hermes` — adds custom systemd unit + deploy wrapper).
- ✅ /hermes-check receipts written for both plan and design phases (2 files in `tasks/.hermes-check-receipts/`).
- ✅ Plan-stage Hermes-first checklist: 7/11 [Hermes], 4/11 [net-new] (under 50% red-flag threshold).
- ✅ Multi-vector reviewer dispatch on PR (R1 structure + R2 prod-state + R3 scope/judgment) — three orthogonal vectors, not three same-axis reviewers.
- ⚠️ **Doc gap**: the brochure-fidelity constraint (custom typography, custom palette) was never written down in plan/design. It's load-bearing for the "why not Notion/Airtable?" question but only surfaced under R3 cross-examination. Future similar work: state the fidelity constraint explicitly so the substrate question can be answered without reverse-engineering.

---

## Actions / follow-ups

| # | Item | Owner | When |
|---|---|---|---|
| 1 | Soak window: 7 days from 2026-05-10 → ≥2026-05-17 | operator | passive |
| 2 | If soak passes: execute `tasks/portal-main-vps-retirement.md` retirement steps (final tarball backup → redirect stub on main-vps → eventual `systemctl stop nginx`) | operator | 2026-05-17 onward |
| 3 | (Deferred from R1-M4) Switch SCP staging path from fixed `/tmp/triveni-portal-*` to `mktemp` per-deploy when concurrent-deploy risk emerges | follow-up PR | when 2nd operator deploys regularly |
| 4 | (Deferred from R2 punch list) Add `id shift-agent`, `command -v python3`, and `systemctl --version` ≥232 pre-flight gates to `tools/deploy-portal.sh` when targeting a NEW VPS (irrelevant on srilu) | follow-up PR | when bootstrapping a 2nd VPS |
| 5 | (Forward-looking from R3) Next "deploy static content" task: ask user upfront whether public CDN hosting is acceptable before scoping VPS-side infra | recurring rule | next similar task |

---

## One-line summary

Portal migrated to srilu via standalone systemd unit + decoupled deploy script. 3 reviewer vectors, 1 design BLOCKER (`ReadWritePaths` for `ProtectSystem=strict`) caught upstream, 3 PR MEDIUMs applied (`StartLimitBurst` no-op, port-check phantom edge, `is-active`-before-`rm` forensics). Live, dual smoke verified, main-vps portal preserved per 7-day slow-decommission. R3's GitHub-Pages alternative captured as forward-looking rule but not blocking — operator directive specified srilu.
