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


def test_flyer_eval_summary_groups_hermes_intent_incidents(tmp_path):
    module = load_module()
    report = tmp_path / "flyer-eval.json"
    report.write_text(
        json.dumps(
            {
                "status": "yellow",
                "summary": {"incident_count": 2, "high_or_critical_count": 1},
                "incidents": [
                    {
                        "type": "hermes_intent_rejected_by_validator",
                        "severity": "high",
                        "suggested_action": "review",
                        "evidence_details": {"active_customer_risk": True},
                    },
                    {
                        "type": "hermes_intent_shadow_coverage_missing",
                        "severity": "high",
                        "suggested_action": "verify",
                        "evidence_details": {"active_customer_risk": True},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    lines = module.summarize_flyer_evaluation_report(report)

    assert any(
        line.startswith("Hermes intent:")
        and "rejected=1" in line
        and "coverage_missing=1" in line
        and "active=2" in line
        for line in lines
    )


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


def test_operator_brief_surfaces_flyer_operating_layer_next_action(tmp_path):
    module = load_module()
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")
    (tasks / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    flyer_eval = repo / "flyer-self-eval.json"
    flyer_eval.write_text(
        json.dumps(
            {
                "status": "green",
                "summary": {"incident_count": 0, "high_or_critical_count": 0},
                "incidents": [],
                "operating_layer": {
                    "status": "yellow",
                    "brand_memory": {
                        "status": "ready_for_at_least_one_customer",
                        "ready_customer_count": 1,
                        "total_customer_count": 1,
                    },
                    "source_edit": {
                        "status": "deferred",
                        "posture": "manual_review",
                    },
                    "deferred_backlog": [
                        {
                            "key": "source_edit_smoke_proof",
                            "status": "blocked",
                            "guardrail": "Run a spend-gated 5-10 case smoke.",
                        },
                        {
                            "key": "multi_format_export_truthfulness",
                            "status": "blocked",
                            "guardrail": "Instagram story/post/export claims remain blocked.",
                        },
                    ],
                    "next_action": {
                        "key": "source_edit_smoke_proof",
                        "summary": "Next: source_edit_smoke_proof - operator - Run a spend-gated 5-10 case smoke.",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    brief = module.build_brief(
        repo_root=repo,
        decisions_path=tasks / "operator-decisions.md",
        todo_path=tasks / "todo.md",
        flyer_evaluation_json_path=flyer_eval,
        automations_dir=repo / "missing-automations",
        generated_date="2026-05-21",
        include_git=False,
    )
    markdown = module.render_markdown(brief)

    assert "Operating layer: yellow; brand_memory=ready_for_at_least_one_customer (1/1); source_edit=deferred (manual_review)" in markdown
    assert "Next: source_edit_smoke_proof - operator - Run a spend-gated 5-10 case smoke." in markdown
    assert "Blocked: source_edit_smoke_proof - Run a spend-gated 5-10 case smoke." in markdown
    assert "Blocked: multi_format_export_truthfulness - Instagram story/post/export claims remain blocked." in markdown


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


def test_operator_brief_includes_flyer_self_evaluation_incidents(tmp_path):
    module = load_module()
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")
    (tasks / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    evaluation = repo / "flyer-evaluation.json"
    evaluation.write_text(
        json.dumps(
            {
                "status": "red",
                "summary": {"incident_count": 2, "high_or_critical_count": 1},
                "incidents": [
                    {
                        "type": "manual_source_edit_stale",
                        "severity": "high",
                        "project_id": "F0063",
                        "suggested_action": "Burn down manual queue row.",
                    },
                    {
                        "type": "repeated_status_checkins",
                        "severity": "medium",
                        "project_id": "F0063",
                        "suggested_action": "Review SLA copy.",
                    },
                ],
                "eval_candidates": [
                    {
                        "category": "source_edit_provider_posture",
                        "project_id": "F0063",
                        "suggested_fixture": "tests/test_flyer_source_edit_preflight.py",
                    }
                ],
                "needs_srini": ["manual_source_edit_stale F0063"],
            }
        ),
        encoding="utf-8",
    )

    brief = module.build_brief(
        repo_root=repo,
        decisions_path=tasks / "operator-decisions.md",
        todo_path=tasks / "todo.md",
        flyer_evaluation_json_path=evaluation,
        automations_dir=repo / "missing-automations",
        generated_date="2026-05-21",
        include_git=False,
    )
    markdown = module.render_markdown(brief)

    assert "Flyer Self-Evaluation" in markdown
    assert "Status: red; incidents=2; high_or_critical=1" in markdown
    assert "HIGH: manual_source_edit_stale F0063 - Burn down manual queue row." in markdown
    assert "Eval: source_edit_provider_posture F0063 -> tests/test_flyer_source_edit_preflight.py" in markdown
    assert "Needs Srini: manual_source_edit_stale F0063" in markdown


def test_operator_brief_groups_flyer_self_evaluation_and_redacts_sensitive_lines(tmp_path):
    module = load_module()
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")
    (tasks / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    evaluation = repo / "flyer-evaluation.json"
    evaluation.write_text(
        json.dumps(
            {
                "status": "red",
                "summary": {"incident_count": 6, "high_or_critical_count": 5},
                "incidents": [
                    {
                        "type": "manual_source_edit_stale",
                        "severity": "high",
                        "project_id": "F9101",
                        "suggested_action": "Burn down queue. OPENAI_API_KEY=sk-leaky +17329837841",
                        "evidence_details": {"queued_age_minutes": 91.5, "active_customer_risk": True},
                    },
                    {
                        "type": "manual_review_stale",
                        "severity": "high",
                        "project_id": "F9106",
                        "suggested_action": "Resolve stale visual QA queue row.",
                        "evidence_details": {"queued_age_minutes": 55.0, "active_customer_risk": True},
                    },
                    {
                        "type": "source_contract_missing",
                        "severity": "high",
                        "project_id": "F9102",
                        "suggested_action": "Add source contract. Bearer secret-token 17329837841@lid",
                        "evidence_details": {"has_reference_media": True, "active_customer_risk": True},
                    },
                    {
                        "type": "source_contract_locked_fact_gap",
                        "severity": "high",
                        "project_id": "F9103",
                        "suggested_action": "Check locked facts.",
                        "evidence_details": {"locked_fact_missing": ["replacement:0:new"], "active_customer_risk": False},
                    },
                    {
                        "type": "source_contract_qa_fact_gap",
                        "severity": "high",
                        "project_id": "F9104",
                        "suggested_action": "Check QA.",
                        "evidence_details": {"qa_missing_required_text": ["source_required_text:0"]},
                    },
                    {
                        "type": "repeated_status_checkins",
                        "severity": "medium",
                        "project_id": "F9105",
                        "suggested_action": "Review status loop at C:\\private\\report.json",
                        "count": 3,
                    },
                ],
                "eval_candidates": [],
                "needs_srini": ["manual_source_edit_stale F9101 +17329837841"],
            }
        ),
        encoding="utf-8",
    )

    brief = module.build_brief(
        repo_root=repo,
        decisions_path=tasks / "operator-decisions.md",
        todo_path=tasks / "todo.md",
        flyer_evaluation_json_path=evaluation,
        automations_dir=repo / "missing-automations",
        generated_date="2026-05-21",
        include_git=False,
    )
    markdown = module.render_markdown(brief)

    assert "Manual queue: stale_source_edits=2; oldest=91.5min" in markdown
    assert "Customer risk: active=3; historical_or_audit=1" in markdown
    assert "Source contracts: missing=1; locked_fact_gaps=1" in markdown
    assert "QA gaps: missing=0; fact_gaps=1; forbidden_text_hits=0" in markdown
    assert "Customer waiting: repeated_checkins=1" in markdown
    assert "OPENAI_API_KEY" not in markdown
    assert "sk-leaky" not in markdown
    assert "secret-token" not in markdown
    assert "+17329837841" not in markdown
    assert "17329837841@lid" not in markdown
    assert "C:\\private\\report.json" not in markdown
    assert "[redacted" in markdown


def test_operator_brief_orders_high_incidents_and_tolerates_malformed_details(tmp_path):
    module = load_module()
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")
    (tasks / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    evaluation = repo / "flyer-evaluation.json"
    incidents = [
        {"type": f"medium_{idx}", "severity": "medium", "project_id": f"F92{idx}", "suggested_action": "medium item"}
        for idx in range(6)
    ]
    incidents.append(
        {
            "type": "source_contract_forbidden_text_present",
            "severity": "high",
            "project_id": "F9999",
            "suggested_action": "urgent high item call 7329837841",
            "evidence_details": "malformed",
        }
    )
    evaluation.write_text(
        json.dumps({"status": "red", "summary": {"incident_count": 7, "high_or_critical_count": 1}, "incidents": incidents}),
        encoding="utf-8",
    )

    brief = module.build_brief(
        repo_root=repo,
        decisions_path=tasks / "operator-decisions.md",
        todo_path=tasks / "todo.md",
        flyer_evaluation_json_path=evaluation,
        automations_dir=repo / "missing-automations",
        generated_date="2026-05-21",
        include_git=False,
    )
    markdown = module.render_markdown(brief)

    assert "HIGH: source_contract_forbidden_text_present F9999" in markdown
    assert "7329837841" not in markdown
    assert "[redacted-phone]" in markdown


def test_operator_brief_prioritizes_active_customer_risk_in_top_incidents(tmp_path):
    module = load_module()
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")
    (tasks / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    evaluation = repo / "flyer-evaluation.json"
    incidents = [
        {
            "type": f"a_historical_{idx}",
            "severity": "high",
            "project_id": f"F93{idx}",
            "suggested_action": "historical audit item",
            "evidence_details": {"active_customer_risk": False},
        }
        for idx in range(6)
    ]
    incidents.append(
        {
            "type": "z_active_customer_waiting",
            "severity": "high",
            "project_id": "F9399",
            "suggested_action": "active customer risk",
            "evidence_details": {"active_customer_risk": True},
        }
    )
    evaluation.write_text(
        json.dumps({"status": "red", "summary": {"incident_count": 7, "high_or_critical_count": 7}, "incidents": incidents}),
        encoding="utf-8",
    )

    brief = module.build_brief(
        repo_root=repo,
        decisions_path=tasks / "operator-decisions.md",
        todo_path=tasks / "todo.md",
        flyer_evaluation_json_path=evaluation,
        automations_dir=repo / "missing-automations",
        generated_date="2026-05-21",
        include_git=False,
    )
    markdown = module.render_markdown(brief)

    assert "Customer risk: active=1; historical_or_audit=6" in markdown
    assert "HIGH: z_active_customer_waiting F9399 - active customer risk" in markdown


def test_operator_brief_groups_routing_tripwires_and_preview_final_qa(tmp_path):
    module = load_module()
    repo = tmp_path
    tasks = repo / "tasks"
    tasks.mkdir()
    (tasks / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")
    (tasks / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    evaluation = repo / "flyer-evaluation.json"
    evaluation.write_text(
        json.dumps(
            {
                "status": "red",
                "summary": {"incident_count": 4, "high_or_critical_count": 3},
                "incidents": [
                    {
                        "type": "new_flyer_routed_as_revision",
                        "severity": "high",
                        "project_id": "F9301",
                        "suggested_action": "Bypass active project routing.",
                        "evidence_details": {"active_customer_risk": True},
                    },
                    {
                        "type": "latest_request_not_reflected",
                        "severity": "high",
                        "project_id": "F9301",
                        "suggested_action": "Regenerate from latest request.",
                        "evidence_details": {"active_customer_risk": True},
                    },
                    {
                        "type": "new_flyer_routed_as_revision",
                        "severity": "medium",
                        "project_id": "F9299",
                        "suggested_action": "Audit historical routing.",
                        "evidence_details": {"active_customer_risk": False},
                    },
                    {
                        "type": "preview_approved_final_qa_failed",
                        "severity": "high",
                        "project_id": "F9303",
                        "suggested_action": "Review failed finalization.",
                        "evidence_details": {"active_customer_risk": True},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    brief = module.build_brief(
        repo_root=repo,
        decisions_path=tasks / "operator-decisions.md",
        todo_path=tasks / "todo.md",
        flyer_evaluation_json_path=evaluation,
        automations_dir=repo / "missing-automations",
        generated_date="2026-05-21",
        include_git=False,
    )
    markdown = module.render_markdown(brief)

    assert "Routing tripwires: new_as_revision=2; latest_not_reflected=1; active=2; historical_or_audit=1" in markdown
    assert "Preview/final QA: approved_then_failed=1; active=1; historical_or_audit=0" in markdown
    assert markdown.index("HIGH: latest_request_not_reflected F9301") < markdown.index("MEDIUM: new_flyer_routed_as_revision F9299")


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
