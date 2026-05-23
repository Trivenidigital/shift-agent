# Operator Ops Brief Runbook

**Drift-check tag:** extends-Hermes

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Daily reminder surface | yes - Hermes chat and app automations already provide scheduled/user-triggered readouts | Use Hermes/cron only to invoke the script; v1 does not add a messaging sender. |
| Fleet state | yes - `tools/hermes-fleet-upgrade.py` already reports fleet status | Consume a saved JSON report from that tool instead of probing hosts again. |
| Task memory | yes - `tasks/todo.md`, `tasks/operator-decisions.md`, PRs, and git remain canonical | Render a summary only; do not create a competing task database. |
| Production state mutation | yes - existing deploy/smoke tools own mutation paths | This runbook is read-only and must not deploy, merge, or edit VPS state. |

Awesome Hermes Agent ecosystem check: no external skill is required for v1. Hermes is used as the operator-facing invocation/reminder layer, while this repo keeps the source-of-truth files.

## Purpose

Use `tools/operator-brief.py` when the operator asks:

- "what am I forgetting?"
- "show blockers"
- "what needs my decision?"
- "status fleet"
- "daily ops brief"

The script renders Markdown from existing sources and is safe to run locally or from a Hermes profile checkout.

## Inputs

- `tasks/operator-decisions.md`: small human-maintained queue for decisions, waiting items, risks, and handoffs.
- `tasks/todo.md`: active backlog and unchecked operational signals.
- Optional fleet JSON from `python tools/hermes-fleet-upgrade.py check --format json`.
- Optional Flyer autonomous train JSON from `python tools/flyer-autonomous-train.py report --offline --format json`.
- Optional Flyer self-evaluation JSON from `python tools/flyer-self-evaluation.py --format json`.
- Optional fleet normalization JSON from `python tools/hermes-fleet-upgrade.py normalization-report --format json --snapshots-json <file>`.
- Optional automation configs from `$CODEX_HOME/automations` or an explicit directory.
- Optional git status/log from the checkout where the script runs.

## Manual Commands

Render from the repo checkout:

```powershell
python tools/operator-brief.py --repo-root . --out .operator-brief.md
```

Render with a saved fleet report:

```powershell
python tools/hermes-fleet-upgrade.py check --format json --timeout 15 --out .fleet-report.json
python tools/operator-brief.py --repo-root . --fleet-json .fleet-report.json --out .operator-brief.md
```

Render with offline autonomous train and fleet normalization reports:

```powershell
New-Item -ItemType Directory -Force .tmp
python tools/flyer-autonomous-train.py report --repo-root . --offline --format json --out .tmp\flyer-train.json
python tools/hermes-fleet-upgrade.py normalization-report --format json --snapshots-json tests\fixtures\hermes_fleet_normalization\blocked_snapshots.json --out .tmp\fleet-normalization.json
python tools/operator-brief.py --repo-root . --flyer-train-json .tmp\flyer-train.json --fleet-normalization-json .tmp\fleet-normalization.json --out .operator-brief.md
```

Render with Flyer self-evaluation incidents from local state:

```powershell
New-Item -ItemType Directory -Force .tmp
python tools/flyer-self-evaluation.py --format json --out .tmp\flyer-self-evaluation.json
python tools/operator-brief.py --repo-root . --flyer-evaluation-json .tmp\flyer-self-evaluation.json --out .operator-brief.md
```

Skip git state when running inside a disposable automation worktree:

```powershell
python tools/operator-brief.py --repo-root . --no-git
```

## Hermes Wiring Pattern

For a Hermes skill or scheduled automation, keep the prompt thin:

1. Pull or use a fresh repo checkout.
2. Optionally run `tools/hermes-fleet-upgrade.py check --format json` and save the JSON.
3. Optionally run `tools/flyer-autonomous-train.py report --offline --format json` and save the JSON.
4. Optionally run `tools/flyer-self-evaluation.py --format json` and save the JSON.
5. Optionally feed a previously collected fleet normalization snapshot to `tools/hermes-fleet-upgrade.py normalization-report --format json --snapshots-json ...`.
6. Run `tools/operator-brief.py`.
7. Return the Markdown to the operator chat.

Do not let the skill edit `tasks/operator-decisions.md` automatically in v1. The operator or Codex can update that file deliberately in normal PR flow.

## Boundaries

- No WhatsApp/Telegram posting is implemented by this script.
- No SSH is performed by this script.
- No deploy, merge, branch promotion, state repair, or VPS mutation is performed by this script.
- The autonomous train report is advisory. It does not create PRs, merge PRs, deploy code, or mutate GitHub/VPS/customer state.
- The Flyer self-evaluation report is advisory. It does not create fixtures, edit prompts, mutate projects, close manual queue rows, or send customer messages.
- The brief is not evidence by itself. Use the linked task docs, PRs, git history, and fleet report for evidence.

## Eligibility Command — Strict-Mode Contract

`tools/flyer-autonomous-train.py eligibility` has TWO consumer modes:

- **Default (no flag) — for humans.** Always exits `0`. Operator reads the JSON output to see verdict + reasons. Suitable for `… | jq …` pipelines that should not fail on a normal "PR not ready" verdict.
- **`--strict` — REQUIRED for automation/runners.** Exits `3` when the PR is ineligible, `0` when eligible, `2` on argparse error. A merge-gate script MUST pass `--strict` so its `if !$?; then abort; fi` actually fires when the policy rejects the PR.

If you are wiring this command into a Hermes automation or any CI/runner glue, you MUST pass `--strict`. Omitting it produces a "policy-approved" exit code for every input regardless of verdict, which silently bypasses the gate.

```powershell
# Automation / runner — strict
python tools\flyer-autonomous-train.py eligibility --metadata pr.json --strict

# Operator inspecting verdict — advisory
python tools\flyer-autonomous-train.py eligibility --metadata pr.json
```

## Promotion Criteria For Chat Posting

Only add direct chat delivery after this Markdown-only version proves useful for several runs and the operator confirms:

- Which personal chat should receive it.
- Whether the source automation checkout pulls fresh code before each run.
- Whether failed fleet probes should block delivery or appear as red brief items.
