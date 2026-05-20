# Autonomous Ops Control Layer Design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** offline Flyer PR metadata policy evaluator; Flyer train Markdown/JSON report; safe next-candidate selector; normalization snapshot JSON schema; offline fleet normalization payload; operator-brief sections for Flyer train and fleet normalization.

**Base:** `origin/main` at `855b161` after PR #137 merged. This design treats source-contract-first as current state and tracks residual F0061/source-contract risk; it does not duplicate PR #137 implementation.

## Goal

Build v0.1 of a safe control layer that lets Srini see what Flyer Studio and Hermes fleet automation would do, why it would do it, and where human judgment is required. This version is deterministic and report-only: no PR creation, no GitHub mutation, no deploy, no VPS mutation, no customer state mutation, no campaign sends, and no manual queue actions.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Scheduling and reminders | yes - Hermes/app automations already run daily fleet checks and operator brief flows | reuse; this PR creates CLI report inputs only |
| Fleet posture checks | yes - `tools/hermes-fleet-upgrade.py` already implements host snapshots, health classification, skill-sync, promotion-plan, and normalization report | extend the existing tool with offline normalization JSON; no parallel fleet tool |
| Operator brief | yes - `tools/operator-brief.py` and `docs/runbooks/operator-ops-brief.md` already exist | add concise sections fed by report JSON |
| Flyer workflow/quality substrate | yes - PR #137 added `FlyerSourceContract`, source-contract extraction, source-vs-new policy, locked facts, and visual QA hardening | report next risks; do not modify Flyer runtime in this slice |
| PR merge policy | none found in Hermes Skills Hub (`https://hermes-agent.nousresearch.com/docs/skills`), bundled catalog, or in-tree tools | build narrow offline policy evaluator |
| Live GitHub/VPS execution | Hermes/Shift deploy and SSH primitives exist, but they mutate state | exclude from v0.1 |

Awesome Hermes Agent ecosystem check: no existing skill/plugin provides repo-specific autonomous PR eligibility or Srilu/Main/VPIN promotion contract enforcement. Verdict: extend Hermes-backed local tooling and keep execution outside this slice.

Live VPS skill/plugin posture: prior fleet checks already report installed skills/plugins; this slice does not run new live SSH. The design relies on those existing reports and adds only offline report consumers. If a future design needs a fresh live `/root/.hermes/skills` or `/root/.hermes/plugins` inventory, it must flow through the existing report-only fleet check, not this v0.1 builder.

## Non-Negotiable Boundaries

- `tools/flyer-autonomous-train.py` must not import `requests`, shell out to `gh`, shell out to `git push`, call GitHub APIs, or open PRs.
- `normalization-report` must use `--snapshots-json` in v0.1. It must not call `probe_host` or run SSH.
- `check` and `skill-sync-report` in `tools/hermes-fleet-upgrade.py` may keep their existing live behavior; this design does not route operator brief through those live paths.
- Auto-merge is not enabled. The eligibility command may say a PR is policy-eligible, but output must include `autonomous_merge_enabled=false`.
- The only branch/PR created in this session is the human-requested implementation PR for this control layer.

## Components

### 1. Flyer Train CLI

File: `tools/flyer-autonomous-train.py`

Commands:

- `eligibility --metadata <path> [--format json]`
- `report --repo-root <path> --offline [--state-json <path>] [--format markdown|json] [--out <path>]`
- `next-candidate --repo-root <path> --offline [--state-json <path>] [--format json]`

`--offline` is required for `report` and `next-candidate` in v0.1. `--out` creates parent directories and writes UTF-8 with LF line endings.

#### PR Metadata Contract

```json
{
  "number": 139,
  "title": "test: add flyer golden fixtures",
  "author": "codex-worker",
  "metadata_source": "offline_fixture",
  "collected_at": "2026-05-20T00:00:00Z",
  "metadata_trusted_for_merge": false,
  "base": "main",
  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "base_sha": "855b161000000000000000000000000000000000",
  "is_open": true,
  "behind_origin_main": false,
  "category": "golden_fixture_tests",
  "changed_files": ["tests/fixtures/flyer_golden/live_customer_message_shapes.json"],
  "touched_subsystems": ["flyer-golden-fixtures"],
  "run_started_at": "2026-05-20T00:00:00Z",
  "previous_run_finished_at": "2026-05-19T16:00:00Z",
  "reviewers": [
    {
      "login": "structural-reviewer",
      "role": "autonomous",
      "state": "approved",
      "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "is_stale": false,
      "is_author": false
    }
  ],
  "findings": [
    {
      "severity": "low",
      "status": "resolved",
      "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "summary": "copy nit"
    }
  ],
  "verification": [
    {
      "name": "focused pytest",
      "state": "passed",
      "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ],
  "urgent_customer_visible": false,
  "previous_run_touched_subsystems": ["cf-router-hooks"],
  "merged_not_deployed": false
}
```

#### Eligibility Rules

A PR is `eligible=true` only when all are true:

- `metadata_trusted_for_merge=true` for future live runners. In v0.1 fixtures may still produce `eligible=true`, but they must also emit `metadata_trusted_for_merge=false`, `advisory_only=true`, and `autonomous_merge_enabled=false`.
- `metadata_source` is present and `collected_at` is present.
- `is_open=true`.
- `behind_origin_main=false`.
- Category is allowed.
- Category is not blocked.
- At least two unique autonomous reviewers approved the exact `head_sha`.
- Reviewers are not the PR author, not stale, not dismissed, and not duplicate logins.
- All verification entries are `passed` and bound to `head_sha`.
- No unresolved high or medium finding exists on `head_sha`.
- Risky subsystem cooldown is clear, or `urgent_customer_visible=true`.
- Declared category matches changed-file policy. Unsafe paths block regardless of declared category.
- `touched_subsystems` is present and is used for cooldown; it may be derived from `changed_files`, but the report must expose the derived value.

Path policy:

- `golden_fixture_tests` may touch only `tests/fixtures/flyer_golden/**`, `tests/test_flyer_golden_scenarios.py`, and task/docs files.
- `customer_message_copy` may touch Flyer workflow/reply table tests and copy modules, but not deploy scripts, provider posture, payment/quota/account state, manual queue closure, or VPS scripts.
- `source_contract_visual_qa` may touch `src/agents/flyer/{facts.py,reference_extract.py,visual_qa.py,render.py}`, `src/platform/schemas.py`, and matching tests/docs. It may not touch deploy scripts or cockpit/manual-queue closure paths.
- `flyer_parser_routing` may touch narrow Flyer parser/cf-router paths. Any edit to `src/plugins/cf-router/hooks.py` must be narrow and triggers cooldown. Broad non-Flyer cf-router changes are blocked.
- Always blocked path patterns include `web/deploy/**`, `tools/build-deploy-tarball.sh`, `src/agents/flyer/manual_queue.py` close paths, payment/quota/account-state scripts, campaign senders, provider/model posture docs/code, and any path under runtime/VPS mutation scripts.

Allowed categories:

- `golden_fixture_tests`
- `flyer_parser_routing`
- `source_contract_visual_qa`
- `customer_message_copy`
- `backlog_docs_cleanup`

Blocked categories:

- `deploy_change`
- `payment_quota_account_state`
- `campaign_send`
- `provider_model_posture`
- `broad_non_flyer_cf_router`
- `manual_queue_closure`
- `customer_state_repair`
- `vps_runtime_mutation`

Output:

```json
{
  "eligible": true,
  "decision": "policy_eligible_no_action",
  "autonomous_merge_enabled": false,
  "would_be_auto_merge_eligible_if_live_runner_enabled": true,
  "advisory_only": true,
  "metadata_trusted_for_merge": false,
  "reasons": [],
  "required_reviewers": 2,
  "approvals": 2,
  "allowed_category": "golden_fixture_tests"
}
```

### 2. Flyer Train Report

The report renderer combines fixture state plus local task docs. It must surface:

- Open autonomous PRs.
- Merged-not-deployed items.
- Blocked candidates and reasons.
- “Needs Srini” decisions.
- Skipped candidates and skip reason.
- PR #137 as merged, not open.
- Stale task-doc items that correspond to already-landed PR #137 are never selectable as a next candidate; they appear only as residual backlog/report context.
- Residual F0061/source-contract backlog:
  - Exact source edit must never downgrade into generic reference generation.
  - Source-contract QA must verify source facts, not only business/contact facts.
  - “Any update?” must not create projects or re-enter clarification loops.
  - Real transcript shapes should continue feeding golden fixtures.

JSON report shape:

```json
{
  "status": "attention",
  "generated_at": "2026-05-20T00:00:00Z",
  "open_autonomous_prs": [],
  "merged_not_deployed": [{"number": 137, "title": "source contract first"}],
  "blocked_candidates": [{"id": "provider-posture-openrouter-edits", "reason": "human decision required"}],
  "needs_srini": ["provider/model posture changes require product decision"],
  "skipped": [{"id": "manual-queue-closure", "reason": "blocked category"}],
  "residual_backlog": ["F0061/source-contract residuals"]
}
```

### 3. Hermes Fleet Normalization Extension

File: `tools/hermes-fleet-upgrade.py`

Change only `normalization-report` behavior:

- Add `--format markdown|json`.
- Add required `--snapshots-json <path>`.
- Add offline snapshot loader.
- Do not add backup fields to `HostSnapshot`, because that would alter the live `check --format json` shape. Keep backup fields in the normalization-only raw snapshot dict/payload.
- Add JSON output with existing host health shape plus top-level `promotion_readiness`.
- Return exit code `2` if `normalization-report` is invoked without `--snapshots-json`.

Do not change live `check` or `skill-sync-report` behavior in this slice.

Input `--snapshots-json` shape:

```json
{
  "mode": "offline_snapshot",
  "generated_at": "2026-05-20T00:00:00Z",
  "hosts": [
    {
      "alias": "srilu-vps",
      "label": "Srilu",
      "role": "canary",
      "promotion_order": 1,
      "expects_whatsapp": true,
      "checked_at": "2026-05-20T00:00:00Z",
      "snapshot_source": "fixture",
      "hermes_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "gateway_status": "active",
      "cockpit_status": "active",
      "bridge_status": "listening",
      "env_symlink_status": "ok",
      "latest_shift_agent_deploy": "deploy-20260520-000000-aaaaaaaa",
      "skills_count": 10,
      "plugins_count": 4,
      "patch_gate_status": "ok",
      "backup_status": "fresh",
      "backup_age_hours": 4
    }
  ]
}
```

Required host set: Srilu, Main, and VPIN. A missing host blocks promotion readiness. Snapshot freshness: `checked_at` must be present and no older than 24 hours relative to `generated_at` in the fixture. Backup statuses: `fresh` passes, `known` is yellow/non-blocking for observation but not promotion-ready, `stale`, `missing`, `unknown`, or `backup_age_hours > 24` block promotion readiness.

Output normalization JSON shape:

```json
{
  "generated_at": "2026-05-20T00:00:00Z",
  "mode": "offline_snapshot",
  "hosts": [
    {
      "label": "Srilu",
      "alias": "srilu-vps",
      "role": "canary",
      "promotion_order": 1,
      "health": {
        "status": "red",
        "summary": "blocked",
        "blockers": ["env symlink not ok"],
        "warnings": []
      },
      "backup_status": "fresh",
      "backup_age_hours": 4
    }
  ],
  "promotion_readiness": {
    "srilu_to_main": {
      "ready": false,
      "reasons": ["Srilu must be green before Main promotion"]
    },
    "main_to_vpin": {
      "ready": false,
      "reasons": ["Main and VPIN normalization contract must be green"]
    },
    "docker_decision": {
      "status": "deferred",
      "until": [
        "normalization contract is green",
        "one clean Srilu -> Main cycle completes",
        "backup/restore story is proven"
      ]
    }
  }
}
```

Promotion readiness is computed, not hardcoded:

- `srilu_to_main.ready=true` only if Srilu is green, Main has no blockers, both snapshots are fresh, both backups are fresh, and all required hosts are present.
- `main_to_vpin.ready=true` only if Main and VPIN have no blockers, both snapshots are fresh, both backups are fresh, and all required hosts are present.
- Docker remains `deferred` until normalization is green, one clean Srilu -> Main cycle completes, and backup/restore is proven.

### 4. Operator Brief Integration

Files:

- `tools/operator-brief.py`
- `docs/runbooks/operator-ops-brief.md`

Add optional CLI args:

- `--flyer-train-json`
- `--fleet-normalization-json`

Extend `Brief` with:

- `flyer_train_lines: list[str]`
- `fleet_normalization_lines: list[str]`

Keep existing `fleet_json_path` and `summarize_fleet_report` behavior. The new fleet normalization parser may reuse host line rendering from `summarize_fleet_report`, then append promotion readiness lines. Missing optional files remain non-blocking.

Brief sections:

- `Flyer Autonomous Train`
- `Fleet Normalization`

The daily brief must show:

- Open autonomous PRs.
- Merged-not-deployed items.
- Blocked candidates.
- Needs Srini decisions.
- Srilu/Main/VPIN normalization status.
- Promotion readiness.

## Test Strategy

### Flyer Train Tests

- Two autonomous approvals required.
- One approval blocks.
- Duplicate reviewer does not count twice.
- Author approval does not count.
- Stale/wrong-SHA approval does not count.
- High/medium unresolved finding blocks.
- Behind `origin/main` blocks.
- Missing/failing/wrong-SHA verification blocks.
- Every blocked category is rejected.
- Unsafe changed paths are rejected even when category claims to be allowed.
- Declared allowed category must match changed-file policy.
- Representative allowed categories can pass.
- `metadata_trusted_for_merge=false` produces advisory-only output and never claims live merge authority.
- Hooks cooldown blocks back-to-back non-urgent runs.
- Urgent customer-visible hooks fix can pass cooldown.
- `report --format json --out nested/path.json` creates parent directories.
- PR #137 appears only as merged/residual backlog in report fixtures.
- `next-candidate` does not select landed PR #137/source-contract task IDs.
- Product-judgment candidates return `human_decision_required`.
- Static guard: new Flyer CLI source must not import `requests`, `urllib`, `http.client`, or `subprocess`, and must not contain `gh pr`, `git push`, `scp`, `ssh`, `systemctl`, or deploy command strings.
- CLI guard: `report` and `next-candidate` require `--offline` and return code `2` without it.

### Fleet Tests

- Offline normalization report renders Main/Srilu/VPIN roles.
- CLI without `--snapshots-json` returns `2` and does not call `probe_host`.
- CLI with `--snapshots-json` does not call `probe_host`.
- Secret-like fields are not printed.
- Stale `checked_at` blocks readiness.
- Missing required host blocks readiness.
- Env symlink, bridge, patch gate, deploy marker, gateway, and stale backup can block promotion.
- Role differences are allowed only when explicit, such as `expects_whatsapp=false`.
- Green fixture produces green promotion readiness.
- Blocked fixture produces blocked promotion readiness.
- Markdown report contains no mutation commands.
- JSON report preserves `hosts[*].health` shape for operator brief.

### Operator Brief Tests

- Existing brief tests continue to pass.
- Flyer train JSON produces `Flyer Autonomous Train` section.
- Fleet normalization JSON produces `Fleet Normalization` section.
- Missing optional report files are non-blocking.

## Deferred

- Live GitHub metadata fetch.
- Live autonomous PR creation.
- Live auto-merge runner.
- Any deploy automation.
- Any VPS normalization execute mode.
- Docker canary decision.
- Provider/model posture changes, including PR #138/OpenRouter source-edit decisions.
- Customer/manual-queue repair actions.

## Self-Review

- No live execution path is introduced.
- JSON contracts are explicit and testable offline.
- PR #137 is handled as merged current state.
- Fleet work extends the existing Hermes upgrade train.
- Operator brief integration is additive and optional.
