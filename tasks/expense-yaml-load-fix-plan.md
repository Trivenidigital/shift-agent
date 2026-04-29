# Expense Bookkeeper YAML-loaded-as-JSON regression fix — Plan

**Drift-check tag:** `extends-Hermes`

**Cadence:** lighter pipeline per `tasks/todo.md` matrix (<100 LOC, single-script class) — Plan → Build → PR → 3 reviews.

## Problem

Three Expense Bookkeeper scripts (PR #30 / #32) call `safe_io.load_model(CONFIG_PATH, Config)` on `/opt/shift-agent/config.yaml`. `load_model` calls `safe_load_json` which calls `json.loads(yaml_content)` → `JSONDecodeError` → `safe_load_json` rename-quarantines the file as `config.yaml.corrupt-<epoch>`. Smoke gate then fails (config missing) and deploy auto-rolls-back.

**Discovered during PR-A deploy** (PR #33, 2026-04-29). Pre-existing bug, exposed by PR-A's stricter chokepoint surfacing.

**Affected callsites:**
- `src/agents/expense_bookkeeper/scripts/extract-receipt:574`
- `src/agents/expense_bookkeeper/scripts/apply-expense-decision:820`
- `src/agents/expense_bookkeeper/scripts/prune-and-expire-expenses.py:43`

## Fix shape

Add `safe_io.load_yaml_model(path, model_cls) -> T` helper. Uses `yaml.safe_load` (correct), does NOT rename-quarantine (YAML files are operator-edited; auto-quarantine is wrong policy), raises on parse/empty/validation error.

Update the 3 expense scripts to use it.

## Build sequence (3 commits)

1. `feat(safe_io): load_yaml_model helper for YAML-validated Pydantic load` — new helper + 7 unit tests including no-rename regression guard
2. `fix(expense-bookkeeper): use load_yaml_model on config.yaml` — patch the 3 callsites
3. `chore(backlog): record PR #34` — todo.md entry

## Deploy plan

After merge: build tarball + scp + run shift-agent-deploy.sh. Smoke gate now passes (config.yaml stays intact). PR-A's changes also land as a side effect. 20-min soak.

## Out-of-scope

- Migrate existing correct YAML loaders (`create-catering-lead`, `apply-catering-owner-decision`, `lookup-prior-leads-by-phone`, smoke-test) to the new helper — DRY win, separate follow-up
- Make `safe_load_json` refuse to rename-quarantine on every JSONDecodeError — broader safe_io safety; separate refactor
