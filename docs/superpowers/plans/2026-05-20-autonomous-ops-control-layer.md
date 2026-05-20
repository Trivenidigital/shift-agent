# Autonomous Ops Control Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build v0.1 of a policy-gated, report-only autonomous operations control layer for Flyer Studio improvement work and Hermes fleet normalization/promotion readiness.

**Architecture:** Add a deterministic Flyer train CLI that evaluates backlog candidates and PR metadata offline, extend the existing Hermes fleet upgrade train instead of creating a parallel fleet tool, and lightly wire both reports into the operator brief. This first slice renders recommendations only: it must not create PRs, push branches, call `gh`, deploy, mutate VPS/customer state, send campaigns, or enable live auto-merge.

**Tech Stack:** Python 3 stdlib CLI tools, pytest, existing repo Markdown task docs, existing `tools/hermes-fleet-upgrade.py`, existing `tools/operator-brief.py`, JSON fixtures for offline PR/fleet metadata.

---

**Drift-check tag:** extends-Hermes

**Current base:** `origin/main` at `855b161` after PR #137 merged. The #137-specific seed findings must be treated as landed/residual source-contract backlog signals, not as an open-PR duplicate.

**New primitives introduced:** Flyer autonomous train policy evaluator, Flyer train report renderer, Flyer next-candidate selector, offline PR metadata fixtures, operator-brief autonomous-train section, optional offline snapshot input for Hermes normalization reports.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Scheduled execution / reminders | yes - Hermes cron/app automation already runs operator brief and fleet checks | reuse; this PR only creates deterministic report tooling, no new automation |
| PR review and merge judgment | none found for Shift Agent repo-specific policy gates | build narrow offline evaluator only; live GitHub/`gh` support is out of scope for v0.1 |
| Fleet normalization reporting | yes - existing `tools/hermes-fleet-upgrade.py` from PR #136 | extend in place; do not create a second fleet management tool |
| Operator daily brief | yes - existing `tools/operator-brief.py` and `docs/runbooks/operator-ops-brief.md` | reuse and add small autonomous/fleet sections |
| Source media / Flyer semantic quality | yes - PR #137 added source-contract-first primitives | report residual risks and next candidates; do not reimplement source-contract code in this slice |
| VPS mutation / deploy / service restart | Hermes/Shift deploy scripts exist, but this slice forbids mutation | no execute path; report-only by construction |

Evidence checked before planning: in-tree `tools/hermes-fleet-upgrade.py`, `tools/operator-brief.py`, `tasks/todo.md`, `tasks/operator-decisions.md`, PR #137's landed source-contract files, Hermes Skills Hub (`https://hermes-agent.nousresearch.com/docs/skills`), Hermes bundled skills catalog (`https://hermes-agent.nousresearch.com/docs/reference/skills-catalog`), and awesome-hermes-agent ecosystem references from existing project docs. No Hermes-native fleet promotion policy or Flyer PR auto-merge policy exists. Verdict: reuse Hermes scheduling/state/reporting substrate and build only the repo-specific offline policy layer.

## Safety Boundaries

- No deploy, no merge, no live auto-merge.
- No SSH writes, service restarts, tarball deploys, WhatsApp sends, campaign sends, customer-state repair, payment/quota/account mutation, or manual-queue closure.
- No live `gh`, GitHub API, SSH, or remote probe path is added to the Flyer train in v0.1.
- `normalization-report` v0.1 uses offline snapshot JSON. Existing live fleet `check` remains separate, but this new normalization path must not call `probe_host` unless a future human-approved mode explicitly adds that.
- The autonomous train may only report eligibility in v0.1. If a future runner acts on it, auto-merge still requires commit-bound evidence: two unique non-author autonomous reviewer approvals on the current head SHA, all verifications passing on that same SHA, no unresolved high/medium findings on that SHA, no blocked category, and no stale/dismissed review.

## File Map

- Create `tools/flyer-autonomous-train.py`: CLI with `report`, `eligibility`, and `next-candidate`.
- Create `tests/test_flyer_autonomous_train.py`: offline policy and report tests.
- Create `tests/fixtures/flyer_autonomous_train/*.json`: representative PR/run metadata.
- Modify `tools/hermes-fleet-upgrade.py`: add fixture-backed/read-only `normalization-report --format json|markdown --snapshots-json ...`, backup freshness fields, and promotion readiness summary.
- Modify `tests/test_hermes_fleet_upgrade.py`: add normalization contract/promotion-readiness/report-only/secret-redaction coverage.
- Modify `tools/operator-brief.py`: optionally ingest Flyer train JSON and fleet normalization JSON, render concise sections.
- Modify `tests/test_operator_brief.py`: pin new brief sections.
- Modify `docs/runbooks/operator-ops-brief.md`: document daily brief additions.
- Modify `tasks/todo.md`: add/update “Active - Flyer Studio autonomous improvement train” and tighten fleet pending state without duplicating existing backlog.
- Create `tasks/autonomous-ops-control-layer-2026-05-20.md`: concise spec/backlog with Drift-check + Hermes-first sections.

## Policy Model

Flyer PR metadata JSON shape:

```json
{
  "number": 139,
  "title": "test: add source-contract golden fixtures",
  "branch": "codex/flyer-golden-fixtures",
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
  "reviewers": [
    {"login": "structural-reviewer", "role": "autonomous", "state": "approved", "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "is_stale": false, "is_author": false},
    {"login": "truthfulness-reviewer", "role": "autonomous", "state": "approved", "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "is_stale": false, "is_author": false}
  ],
  "findings": [],
  "verification": [{"name": "focused pytest", "state": "passed", "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],
  "merged_not_deployed": false,
  "urgent_customer_visible": false,
  "previous_run_touched_subsystems": ["cf-router-hooks"]
}
```

Eligibility output shape:

```json
{
  "eligible": true,
  "decision": "policy_eligible_no_action",
  "autonomous_merge_enabled": false,
  "advisory_only": true,
  "metadata_trusted_for_merge": false,
  "would_be_auto_merge_eligible_if_live_runner_enabled": false,
  "reasons": [],
  "required_reviewers": 2,
  "approvals": 2,
  "blocked_categories": [],
  "allowed_category": "source_contract_visual_qa"
}
```

Blocked categories:

- `deploy_change`
- `payment_quota_account_state`
- `campaign_send`
- `provider_model_posture`
- `broad_non_flyer_cf_router`
- `manual_queue_closure`
- `customer_state_repair`
- `vps_runtime_mutation`

Allowed categories:

- `golden_fixture_tests`
- `flyer_parser_routing`
- `source_contract_visual_qa`
- `customer_message_copy`
- `backlog_docs_cleanup`

Risky subsystem cooldown:

- If `changed_files` includes `src/plugins/cf-router/hooks.py`, the next autonomous run touching the same subsystem is blocked unless `urgent_customer_visible=true`.
- The cooldown is advisory for human-reviewed/manual sessions but blocking for autonomous auto-merge eligibility.
- PR #137 is never used as an open/eligible fixture. It appears only in report fixtures as `merged` with residual F0061/source-contract backlog items.
- Eligibility also validates changed-file policy. Unsafe paths block regardless of the declared category, and v0.1 metadata remains advisory unless `metadata_trusted_for_merge=true` is present from a future trusted collector.

## Task 1: Write Flyer Train RED Tests

**Files:**
- Create: `tests/test_flyer_autonomous_train.py`
- Create fixtures under: `tests/fixtures/flyer_autonomous_train/`

- [ ] **Step 1: Add fixture files**

Create these JSON fixtures:

```text
tests/fixtures/flyer_autonomous_train/eligible_pr.json
tests/fixtures/flyer_autonomous_train/one_review_pr.json
tests/fixtures/flyer_autonomous_train/high_finding_pr.json
tests/fixtures/flyer_autonomous_train/blocked_provider_pr.json
tests/fixtures/flyer_autonomous_train/behind_main_pr.json
tests/fixtures/flyer_autonomous_train/missing_verification_pr.json
tests/fixtures/flyer_autonomous_train/cooldown_hooks_pr.json
tests/fixtures/flyer_autonomous_train/cooldown_hooks_urgent_pr.json
tests/fixtures/flyer_autonomous_train/stale_review_pr.json
tests/fixtures/flyer_autonomous_train/report_state.json
```

The eligible fixture uses a synthetic future PR number (`139`, not landed PR #137), category `golden_fixture_tests`, two unique non-author autonomous approvals bound to `head_sha`, passing verification bound to the same `head_sha`, no findings, and `behind_origin_main=false`.

- [ ] **Step 2: Add failing eligibility tests**

```python
def test_two_autonomous_reviewer_approvals_are_required():
    result = evaluate_pr(load_fixture("one_review_pr.json"))
    assert result["eligible"] is False
    assert "requires at least 2 autonomous reviewer approvals" in result["reasons"]


def test_unresolved_high_or_medium_finding_blocks_auto_merge():
    result = evaluate_pr(load_fixture("high_finding_pr.json"))
    assert result["eligible"] is False
    assert "unresolved high/medium review finding" in result["reasons"]


def test_behind_origin_main_blocks_auto_merge():
    result = evaluate_pr(load_fixture("behind_main_pr.json"))
    assert result["eligible"] is False
    assert "branch is behind origin/main" in result["reasons"]


def test_blocked_provider_posture_category_is_rejected():
    result = evaluate_pr(load_fixture("blocked_provider_pr.json"))
    assert result["eligible"] is False
    assert "blocked category: provider_model_posture" in result["reasons"]


def test_allowed_source_contract_pr_can_pass():
    result = evaluate_pr(load_fixture("eligible_pr.json"))
    assert result["eligible"] is True
    assert result["decision"] == "policy_eligible_no_action"
    assert result["autonomous_merge_enabled"] is False


def test_missing_verification_blocks_auto_merge():
    result = evaluate_pr(load_fixture("missing_verification_pr.json"))
    assert result["eligible"] is False
    assert "missing/failing verification" in result["reasons"]


def test_stale_or_wrong_sha_review_does_not_count():
    result = evaluate_pr(load_fixture("stale_review_pr.json"))
    assert result["eligible"] is False
    assert "requires at least 2 current autonomous reviewer approvals" in result["reasons"]


def test_cf_router_hooks_cooldown_blocks_unless_urgent_customer_visible():
    blocked = evaluate_pr(load_fixture("cooldown_hooks_pr.json"))
    urgent = evaluate_pr(load_fixture("cooldown_hooks_urgent_pr.json"))
    assert blocked["eligible"] is False
    assert "risky subsystem cooldown: cf-router hooks touched in back-to-back runs" in blocked["reasons"]
    assert urgent["eligible"] is True
```

- [ ] **Step 3: Add failing report and next-candidate tests**

```python
def test_report_surfaces_pr137_as_merged_and_residual_source_contract_backlog(tmp_path):
    repo = tmp_path
    (repo / "tasks").mkdir()
    (repo / "tasks" / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    (repo / "tasks" / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")
    report = render_report(load_fixture("report_state.json"), repo_root=repo)
    assert "PR #137: merged" in report
    assert "F0061/source-contract residuals" in report
    assert "do not duplicate PR #137 landed changes" in report


def test_next_candidate_returns_human_decision_for_product_judgment():
    decision = choose_next_candidate({
        "backlog": [{"id": "provider-posture-openrouter-edits", "category": "provider_model_posture"}]
    })
    assert decision["status"] == "human_decision_required"
```

- [ ] **Step 4: Run RED**

Run:

```powershell
python -m pytest tests\test_flyer_autonomous_train.py -q
```

Expected: fails because `tools/flyer-autonomous-train.py` does not exist.

## Task 2: Implement Flyer Train CLI

**Files:**
- Create: `tools/flyer-autonomous-train.py`
- Modify: `tests/test_flyer_autonomous_train.py`

- [ ] **Step 1: Implement policy constants and evaluator**

Add:

```python
ALLOWED_CATEGORIES = {
    "golden_fixture_tests",
    "flyer_parser_routing",
    "source_contract_visual_qa",
    "customer_message_copy",
    "backlog_docs_cleanup",
}

BLOCKED_CATEGORIES = {
    "deploy_change",
    "payment_quota_account_state",
    "campaign_send",
    "provider_model_posture",
    "broad_non_flyer_cf_router",
    "manual_queue_closure",
    "customer_state_repair",
    "vps_runtime_mutation",
}
```

Implement:

```python
def evaluate_pr(metadata: dict[str, object]) -> dict[str, object]:
    reasons: list[str] = []
    head_sha = str(metadata.get("head_sha") or "")
    approvals = count_current_autonomous_approvals(
        metadata.get("reviewers", []),
        head_sha=head_sha,
        author=str(metadata.get("author") or ""),
    )
    if approvals < 2:
        reasons.append("requires at least 2 current autonomous reviewer approvals")
    if not metadata.get("is_open", True):
        reasons.append("PR is not open")
    if metadata.get("behind_origin_main"):
        reasons.append("branch is behind origin/main")
    if has_unresolved_high_or_medium(metadata.get("findings", []), head_sha=head_sha):
        reasons.append("unresolved high/medium review finding")
    if has_missing_or_failing_verification(metadata.get("verification", []), head_sha=head_sha):
        reasons.append("missing/failing verification")
    category = str(metadata.get("category") or "")
    if category in BLOCKED_CATEGORIES:
        reasons.append(f"blocked category: {category}")
    elif category not in ALLOWED_CATEGORIES:
        reasons.append(f"category requires human decision: {category or 'missing'}")
    if violates_risky_subsystem_cooldown(metadata):
        reasons.append("risky subsystem cooldown: cf-router hooks touched in back-to-back runs")
    eligible = not reasons
    return {
        "eligible": eligible,
        "decision": "policy_eligible_no_action" if eligible else "blocked",
        "autonomous_merge_enabled": False,
        "advisory_only": True,
        "metadata_trusted_for_merge": metadata.get("metadata_trusted_for_merge") is True,
        "would_be_auto_merge_eligible_if_live_runner_enabled": eligible and metadata.get("metadata_trusted_for_merge") is True,
        "reasons": reasons,
        "required_reviewers": 2,
        "approvals": approvals,
        "allowed_category": category if category in ALLOWED_CATEGORIES else "",
    }
```

- [ ] **Step 2: Implement `report`, `eligibility`, and `next-candidate` commands**

`eligibility --metadata <file>` loads JSON, calls `evaluate_pr`, prints JSON.

`report --repo-root . --offline [--state-json <file>] [--format markdown|json] [--out <path>]` reads the fixture if provided; otherwise reads `tasks/todo.md` and `tasks/operator-decisions.md` for known headings and prints Markdown/JSON. It creates the parent directory for `--out`.

`next-candidate --repo-root . --offline [--state-json <file>] [--format json]` returns one safe candidate or `human_decision_required`.

Static safety guard: add a test that reads `tools/flyer-autonomous-train.py` and fails if it contains banned live-operation imports/strings: `requests`, `urllib`, `http.client`, `subprocess`, `gh pr`, `git push`, `scp`, `ssh`, `systemctl`, or deploy command strings.

- [ ] **Step 3: Run GREEN**

Run:

```powershell
python -m pytest tests\test_flyer_autonomous_train.py -q
python tools\flyer-autonomous-train.py eligibility --metadata tests\fixtures\flyer_autonomous_train\eligible_pr.json
python tools\flyer-autonomous-train.py report --repo-root . --offline --state-json tests\fixtures\flyer_autonomous_train\report_state.json
python tools\flyer-autonomous-train.py report --repo-root . --offline --state-json tests\fixtures\flyer_autonomous_train\report_state.json --format json --out .tmp\flyer-train.json
```

Expected: tests pass; CLI emits JSON/Markdown without network access.

## Task 3: Extend Hermes Fleet Normalization Reporting Offline

**Files:**
- Modify: `tools/hermes-fleet-upgrade.py`
- Modify: `tests/test_hermes_fleet_upgrade.py`
- Create: `tests/fixtures/hermes_fleet_normalization/blocked_snapshots.json`
- Create: `tests/fixtures/hermes_fleet_normalization/green_snapshots.json`

- [ ] **Step 1: Add RED tests**

Add tests that assert:

```python
def test_normalization_report_accepts_offline_snapshots_and_renders_roles():
    snapshots = load_snapshots("blocked_snapshots.json")
    report = render_normalization_markdown(snapshots)
    assert "Srilu" in report
    assert "Main" in report
    assert "VPIN" in report
    assert "canary" in report


def test_promotion_readiness_requires_srilu_green_before_main_or_vpin():
    payload = normalization_payload(load_snapshots("blocked_snapshots.json"))
    assert payload["promotion_readiness"]["srilu_to_main"]["ready"] is False
    assert "Srilu must be green" in payload["promotion_readiness"]["srilu_to_main"]["reasons"]


def test_promotion_readiness_turns_green_when_contract_is_green():
    payload = normalization_payload(load_snapshots("green_snapshots.json"))
    assert payload["promotion_readiness"]["srilu_to_main"]["ready"] is True
    assert payload["promotion_readiness"]["main_to_vpin"]["ready"] is True
    assert payload["promotion_readiness"]["docker_decision"]["status"] == "deferred"


def test_normalization_report_does_not_include_mutation_commands():
    report = render_normalization_markdown(load_snapshots("blocked_snapshots.json"))
    forbidden = ["systemctl restart", "scp ", "rsync", "deploy.sh", "git checkout", "hermes update"]
    assert not any(token in report for token in forbidden)


def test_normalization_report_cli_requires_snapshots_json(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "probe_host", lambda *_args, **_kwargs: pytest.fail("must not SSH in normalization-report v0.1"))
    assert module.main(["normalization-report", "--format", "json"]) == 2


def test_normalization_report_cli_uses_snapshot_fixture_without_ssh(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setattr(module, "probe_host", lambda *_args, **_kwargs: pytest.fail("must not SSH when --snapshots-json is provided"))
    out = tmp_path / "nested" / "fleet.json"
    code = module.main([
        "normalization-report",
        "--format",
        "json",
        "--snapshots-json",
        str(FIXTURES / "blocked_snapshots.json"),
        "--out",
        str(out),
    ])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "hosts" in payload
    assert "promotion_readiness" in payload
```

- [ ] **Step 2: Add normalization-only snapshot helpers**

Do not add backup fields to `HostSnapshot`; that would change live `check --format json` output. Add helpers that load raw normalization snapshot dictionaries, construct temporary `HostSnapshot` values only for `classify_snapshot(...)`, and keep `backup_status`, `backup_age_hours`, `checked_at`, and `snapshot_source` in the normalization-only payload. Ensure JSON rendering redacts secret-like keys and never prints `.env` values.

- [ ] **Step 3: Add offline JSON input**

Add CLI args to `normalization-report`:

```python
normalize.add_argument("--format", choices=["markdown", "json"], default="markdown")
normalize.add_argument("--snapshots-json", help="offline normalization snapshot payload; required for this command in v0.1")
```

For v0.1, `normalization-report` requires `--snapshots-json`, but do not use `argparse(required=True)` because tests call `main(...)` directly. If the arg is missing, `run_normalization_report` prints a clear error and returns exit code `2` instead of probing SSH. The older live `check` command remains unchanged; this new normalization contract path is offline-only.

- [ ] **Step 4: Add promotion readiness payload**

Compute:

```json
{
  "hosts": [
    {
      "label": "Srilu",
      "alias": "srilu-vps",
      "role": "canary",
      "health": {"status": "red", "summary": "blocked", "blockers": ["env symlink not ok"], "warnings": []},
      "backup_status": "fresh"
    }
  ],
  "promotion_readiness": {
    "srilu_to_main": {"ready": false, "reasons": ["Srilu must be green before Main promotion"]},
    "main_to_vpin": {"ready": false, "reasons": ["Main and VPIN normalization contract must be green"]},
    "docker_decision": {"status": "deferred", "until": ["normalization green", "one clean Srilu -> Main cycle", "backup/restore proven"]}
  }
}
```

The JSON output must preserve the existing operator-brief-compatible host shape: `hosts[*].health.status`, `summary`, `blockers`, and `warnings`. Add `promotion_readiness` as a top-level key; do not replace the `hosts` contract.

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest tests\test_hermes_fleet_upgrade.py -q
python tools\hermes-fleet-upgrade.py normalization-report --format markdown --snapshots-json tests\fixtures\hermes_fleet_normalization\blocked_snapshots.json
python tools\hermes-fleet-upgrade.py normalization-report --format json --snapshots-json tests\fixtures\hermes_fleet_normalization\blocked_snapshots.json
```

Expected: tests pass; no SSH/network is used in fixture mode.

## Task 4: Integrate Operator Brief

**Files:**
- Modify: `tools/operator-brief.py`
- Modify: `tests/test_operator_brief.py`
- Modify: `docs/runbooks/operator-ops-brief.md`

- [ ] **Step 1: Add RED tests**

```python
def test_operator_brief_includes_flyer_autonomous_train_status(tmp_path):
    module = load_module()
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")
    (tasks / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    flyer_report = tmp_path / "flyer-train.json"
    flyer_report.write_text(json.dumps({
        "status": "attention",
        "open_autonomous_prs": [{"number": 139, "title": "test"}],
        "merged_not_deployed": [{"number": 137}],
        "blocked_candidates": [{"id": "provider-posture", "reason": "human decision required"}],
        "needs_srini": ["provider posture decision"]
    }), encoding="utf-8")
    brief = module.build_brief(
        repo_root=repo,
        decisions_path=tasks / "operator-decisions.md",
        todo_path=tasks / "todo.md",
        fleet_json_path=None,
        flyer_train_json_path=flyer_report,
        automations_dir=repo / "missing-automations",
        generated_date="2026-05-21",
        include_git=False,
    )
    markdown = module.render_markdown(brief)
    assert "Flyer Autonomous Train" in markdown
    assert "Open autonomous PRs" in markdown
    assert "Merged-not-deployed" in markdown
    assert "Needs Srini" in markdown
```

- [ ] **Step 2: Add CLI option**

Add:

```python
parser.add_argument("--flyer-train-json", help="optional JSON output from tools/flyer-autonomous-train.py report")
parser.add_argument("--fleet-normalization-json", help="optional JSON output from tools/hermes-fleet-upgrade.py normalization-report")
```

Add `flyer_train_json_path` and `fleet_normalization_json_path` keyword args to `build_brief(...)`, extend the `Brief` dataclass with `flyer_train_lines` and `fleet_normalization_lines`, and render concise sections if files are provided. Keep the existing `fleet_json_path` behavior for daily fleet checks; the normalization JSON parser may reuse `summarize_fleet_report` for host lines and append promotion-readiness lines from the top-level `promotion_readiness` key.

- [ ] **Step 3: Update runbook**

Document:

```powershell
python tools\flyer-autonomous-train.py report --repo-root . --offline --format json --out .tmp\flyer-train.json
python tools\hermes-fleet-upgrade.py normalization-report --format json --snapshots-json tests\fixtures\hermes_fleet_normalization\blocked_snapshots.json --out .tmp\fleet-normalization.json
python tools\operator-brief.py --repo-root . --flyer-train-json .tmp\flyer-train.json --fleet-normalization-json .tmp\fleet-normalization.json
```

State that live automation remains report-only and no deploy automation is enabled. The tools should create parent directories for `--out`; the runbook should also show `New-Item -ItemType Directory -Force .tmp` for operators who want to inspect the directory manually.

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests\test_operator_brief.py -q
```

Expected: tests pass.

## Task 5: Task Docs and Spec

**Files:**
- Modify: `tasks/todo.md`
- Create: `tasks/autonomous-ops-control-layer-2026-05-20.md`

- [ ] **Step 1: Add active Flyer autonomous train section**

Add a concise `tasks/todo.md` section:

```markdown
## Active - Flyer Studio autonomous improvement train (2026-05-20)

**Drift-check tag:** extends-Hermes

Hermes-first summary: reuse Hermes scheduling, repo-backed task docs, existing Flyer golden tests, reviewer-first PR flow, and operator brief. Net-new scope is deterministic policy/report tooling; no deploy, VPS mutation, customer mutation, or live auto-merge runner is enabled.

- [ ] v0.1 policy/spec + offline report/eligibility tooling.
- [ ] Daily/8-hour runner only after report output is stable and reviewed.
- [ ] Auto-merge runner only after two-reviewer policy gates are proven offline.
- [ ] No autonomous deploy.
```

- [ ] **Step 2: Add spec/backlog doc**

The spec must include:

- Drift-check tag.
- Hermes-first table.
- Flyer cadence: every 8 hours, max 1 PR/run, max 3 PRs/24h.
- Allowed/blocked categories.
- Two autonomous reviewer requirement.
- Fleet sequence: observe -> normalize contract -> controlled promotion -> Docker later.
- Stop conditions.
- Residual source-contract/F0061 backlog after PR #137 landed.

- [ ] **Step 3: Run Markdown sanity**

Run:

```powershell
rg -n "TBD|TODO|fill in later" docs\superpowers\plans\2026-05-20-autonomous-ops-control-layer.md tasks\autonomous-ops-control-layer-2026-05-20.md
git diff --check
```

Expected: no placeholder hits; diff check clean.

## Task 6: Full Verification and PR

**Files:**
- All changed files.

- [ ] **Step 1: Focused tests**

Run:

```powershell
python -m pytest tests\test_flyer_autonomous_train.py tests\test_hermes_fleet_upgrade.py tests\test_operator_brief.py -q
```

Expected: all pass.

- [ ] **Step 2: Compile tools**

Run:

```powershell
python -m py_compile tools\flyer-autonomous-train.py tools\hermes-fleet-upgrade.py tools\operator-brief.py
```

Expected: no output and exit code 0.

- [ ] **Step 3: CLI smoke**

Run:

```powershell
python tools\flyer-autonomous-train.py report --repo-root . --offline
python tools\flyer-autonomous-train.py eligibility --metadata tests\fixtures\flyer_autonomous_train\eligible_pr.json
python tools\flyer-autonomous-train.py next-candidate --repo-root . --offline
python tools\hermes-fleet-upgrade.py normalization-report --format markdown --snapshots-json tests\fixtures\hermes_fleet_normalization\blocked_snapshots.json
```

Expected: all commands exit 0 and print deterministic output.

- [ ] **Step 4: Diff check**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only intentional files changed.

- [ ] **Step 5: Commit and PR**

Commit after verification:

```powershell
git add docs\runbooks\operator-ops-brief.md docs\superpowers\plans\2026-05-20-autonomous-ops-control-layer.md tasks\autonomous-ops-control-layer-2026-05-20.md tasks\todo.md tools\flyer-autonomous-train.py tools\hermes-fleet-upgrade.py tools\operator-brief.py tests\test_flyer_autonomous_train.py tests\test_hermes_fleet_upgrade.py tests\test_operator_brief.py tests\fixtures\flyer_autonomous_train tests\fixtures\hermes_fleet_normalization
git commit -m "feat(ops): add autonomous Flyer and fleet control reports"
git push -u origin codex/autonomous-ops-control-layer
```

Open PR with explicit statements:

- Files changed.
- Tests run.
- Risks.
- Deferred items.
- “No deploy performed.”
- “Autonomous merge is policy-gated only; no live production deploy automation enabled.”

## Self-Review Checklist

- Spec coverage: Flyer train, fleet train, operator brief, tests, docs, no-deploy boundary, two-reviewer gate, cooldown, blocked categories, and PR #137 merged-state handling are covered.
- Placeholder scan: no intentional placeholders in the implementation docs; command examples use concrete paths.
- Type consistency: PR metadata uses `category`, `reviewers`, `findings`, `verification`, `behind_origin_main`, `changed_files`, and `previous_run_touched_subsystems` consistently.
- Scope: no frontend/backend app changes; no runtime mutation; no deployment scripts altered.
