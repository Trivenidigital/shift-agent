import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "operator-brief.py"


def load_module():
    spec = importlib.util.spec_from_file_location("operator_brief", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_operator_decision_doc_parser_groups_checklist_sections(tmp_path):
    module = load_module()
    decisions = tmp_path / "operator-decisions.md"
    decisions.write_text(
        """# Operator Decisions

## Needs Your Decision

- [ ] Pick Flyer source-contract scope.
- [x] Already handled decision.

## Waiting On You

- [ ] Run pilot smoke.

## Active Risks

- [ ] Fleet drift still red.

## Handoffs And Promises

- [ ] Update the automation checkout.
""",
        encoding="utf-8",
    )

    parsed = module.load_operator_decisions(decisions)

    assert parsed.needs_decision == ["Pick Flyer source-contract scope."]
    assert parsed.waiting_on_you == ["Run pilot smoke."]
    assert parsed.active_risks == ["Fleet drift still red."]
    assert parsed.handoffs == ["Update the automation checkout."]


def test_render_brief_includes_decisions_todo_fleet_automations_and_git(tmp_path):
    module = load_module()
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text(
        """# Operator Decisions

## Needs Your Decision

- [ ] Approve lean Flyer source-contract slice.

## Waiting On You

- [ ] Run production pilot WhatsApp smoke.

## Active Risks

- [ ] Srilu fleet posture is red.

## Handoffs And Promises

- [ ] Update the daily fleet-check checkout.
""",
        encoding="utf-8",
    )
    (tasks / "todo.md").write_text(
        """# Backlog

## Active - Hermes fleet upgrade train

- [ ] Normalize Srilu/VPIN runtime posture before adding execute mode.
- [ ] Phase 1 pilot proof: complete live WhatsApp smoke.
- [x] Completed item should not appear.
""",
        encoding="utf-8",
    )
    fleet_json = repo / "fleet.json"
    fleet_json.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-21T08:00:00Z",
                "hosts": [
                    {
                        "label": "Srilu",
                        "alias": "srilu-vps",
                        "health": {
                            "status": "red",
                            "summary": "blocked",
                            "blockers": ["env symlink not ok"],
                            "warnings": [],
                        },
                    },
                    {
                        "label": "Main",
                        "alias": "main-vps",
                        "health": {
                            "status": "yellow",
                            "summary": "attention",
                            "blockers": [],
                            "warnings": ["Hermes upgrade available"],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    automations = repo / "automations"
    automation_dir = automations / "daily-hermes-fleet-check"
    automation_dir.mkdir(parents=True)
    (automation_dir / "automation.toml").write_text(
        'name = "Daily Hermes fleet check"\nstatus = "ACTIVE"\n',
        encoding="utf-8",
    )

    brief = module.build_brief(
        repo_root=repo,
        decisions_path=tasks / "operator-decisions.md",
        todo_path=tasks / "todo.md",
        fleet_json_path=fleet_json,
        automations_dir=automations,
        generated_date="2026-05-21",
        include_git=False,
    )
    markdown = module.render_markdown(brief)

    assert "# Ops Brief - 2026-05-21" in markdown
    assert "Approve lean Flyer source-contract slice." in markdown
    assert "Normalize Srilu/VPIN runtime posture before adding execute mode." in markdown
    assert "Srilu: red - blocked" in markdown
    assert "env symlink not ok" in markdown
    assert "Main: yellow - attention" in markdown
    assert "Daily Hermes fleet check: ACTIVE" in markdown
    assert "Run production pilot WhatsApp smoke." in markdown


def test_missing_optional_sources_are_non_blocking(tmp_path):
    module = load_module()
    repo = tmp_path
    (repo / "tasks").mkdir()

    brief = module.build_brief(
        repo_root=repo,
        decisions_path=repo / "tasks" / "missing.md",
        todo_path=repo / "tasks" / "missing-todo.md",
        fleet_json_path=None,
        automations_dir=repo / "missing-automations",
        generated_date="2026-05-21",
        include_git=False,
    )
    markdown = module.render_markdown(brief)

    assert "No operator decisions file found." in markdown
    assert "No fleet report provided." in markdown
    assert "No automation configs found." in markdown


def test_operator_brief_includes_flyer_autonomous_train_status(tmp_path):
    module = load_module()
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")
    (tasks / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    flyer_report = repo / "flyer-train.json"
    flyer_report.write_text(
        json.dumps(
            {
                "status": "attention",
                "open_autonomous_prs": [{"number": 139, "title": "test"}],
                "merged_not_deployed": [{"number": 137, "title": "source contract first"}],
                "blocked_candidates": [{"id": "provider-posture", "reason": "human decision required"}],
                "needs_srini": ["provider posture decision"],
            }
        ),
        encoding="utf-8",
    )

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
    assert "Open autonomous PRs: #139 test" in markdown
    assert "Merged-not-deployed: #137 source contract first" in markdown
    assert "Blocked: provider-posture - human decision required" in markdown
    assert "Needs Srini: provider posture decision" in markdown


def test_operator_brief_includes_fleet_normalization_readiness(tmp_path):
    module = load_module()
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")
    (tasks / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    normalization = repo / "fleet-normalization.json"
    normalization.write_text(
        json.dumps(
            {
                "hosts": [
                    {
                        "label": "Srilu",
                        "health": {
                            "status": "red",
                            "summary": "blocked",
                            "blockers": ["env symlink not ok"],
                            "warnings": [],
                        },
                    }
                ],
                "promotion_readiness": {
                    "srilu_to_main": {"ready": False, "reasons": ["Srilu must be green before Main promotion"]},
                    "main_to_vpin": {"ready": True, "reasons": []},
                    "docker_decision": {"status": "deferred", "until": ["normalization contract is green"]},
                },
            }
        ),
        encoding="utf-8",
    )

    brief = module.build_brief(
        repo_root=repo,
        decisions_path=tasks / "operator-decisions.md",
        todo_path=tasks / "todo.md",
        fleet_json_path=None,
        fleet_normalization_json_path=normalization,
        automations_dir=repo / "missing-automations",
        generated_date="2026-05-21",
        include_git=False,
    )
    markdown = module.render_markdown(brief)

    assert "Fleet Normalization" in markdown
    assert "Srilu: red - blocked" in markdown
    assert "Srilu -> Main: blocked" in markdown
    assert "Srilu must be green before Main promotion" in markdown
    assert "Main -> VPIN: ready" in markdown
    assert "Docker: deferred" in markdown


def test_todo_signals_ignore_horizontal_rules_checked_items_and_plain_bullets(tmp_path):
    module = load_module()
    todo = tmp_path / "todo.md"
    todo.write_text(
        """# Backlog

---

- [x] Completed parent.
  - Plain detail under completed parent.
- [ ] Keep this open item.
- Plain backlog note without checkbox.
""",
        encoding="utf-8",
    )

    assert module.load_todo_signals(todo) == ["Keep this open item."]


def test_cli_writes_markdown_to_requested_output(tmp_path):
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text(
        """# Operator Decisions

## Needs Your Decision

- [ ] Decide VPIN role.
""",
        encoding="utf-8",
    )
    (tasks / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    output = repo / "brief.md"

    result = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--repo-root",
            str(repo),
            "--date",
            "2026-05-21",
            "--no-git",
            "--out",
            str(output),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Wrote" in result.stderr
    assert "Decide VPIN role." in output.read_text(encoding="utf-8")
