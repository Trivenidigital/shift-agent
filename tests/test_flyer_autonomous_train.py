import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "flyer-autonomous-train.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "flyer_autonomous_train"


def load_module():
    spec = importlib.util.spec_from_file_location("flyer_autonomous_train", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_two_autonomous_reviewer_approvals_are_required():
    module = load_module()

    result = module.evaluate_pr(load_fixture("one_review_pr.json"))

    assert result["eligible"] is False
    assert "requires at least 2 current autonomous reviewer approvals" in result["reasons"]


def test_unresolved_high_or_medium_finding_blocks_auto_merge():
    module = load_module()

    result = module.evaluate_pr(load_fixture("high_finding_pr.json"))

    assert result["eligible"] is False
    assert "unresolved high/medium review finding" in result["reasons"]


def test_behind_origin_main_blocks_auto_merge():
    module = load_module()

    result = module.evaluate_pr(load_fixture("behind_main_pr.json"))

    assert result["eligible"] is False
    assert "branch is behind origin/main" in result["reasons"]


def test_missing_verification_blocks_auto_merge():
    module = load_module()

    result = module.evaluate_pr(load_fixture("missing_verification_pr.json"))

    assert result["eligible"] is False
    assert "missing/failing verification" in result["reasons"]


def test_stale_or_wrong_sha_review_does_not_count():
    module = load_module()

    result = module.evaluate_pr(load_fixture("stale_review_pr.json"))

    assert result["eligible"] is False
    assert "requires at least 2 current autonomous reviewer approvals" in result["reasons"]


def test_blocked_provider_posture_category_is_rejected():
    module = load_module()

    result = module.evaluate_pr(load_fixture("blocked_provider_pr.json"))

    assert result["eligible"] is False
    assert "blocked category: provider_model_posture" in result["reasons"]


def test_unsafe_changed_path_blocks_even_with_safe_category():
    module = load_module()

    result = module.evaluate_pr(load_fixture("unsafe_path_pr.json"))

    assert result["eligible"] is False
    assert "blocked path: tools/build-deploy-tarball.sh" in result["reasons"]


def test_allowed_fixture_pr_is_policy_eligible_but_advisory_only():
    module = load_module()

    result = module.evaluate_pr(load_fixture("eligible_pr.json"))

    assert result["eligible"] is True
    assert result["decision"] == "policy_eligible_no_action"
    assert result["autonomous_merge_enabled"] is False
    assert result["advisory_only"] is True
    assert result["metadata_trusted_for_merge"] is False
    assert result["would_be_auto_merge_eligible_if_live_runner_enabled"] is False


def test_cf_router_hooks_cooldown_blocks_unless_urgent_customer_visible():
    module = load_module()

    blocked = module.evaluate_pr(load_fixture("cooldown_hooks_pr.json"))
    urgent = module.evaluate_pr(load_fixture("cooldown_hooks_urgent_pr.json"))

    assert blocked["eligible"] is False
    assert "risky subsystem cooldown: cf-router hooks touched in back-to-back runs" in blocked["reasons"]
    assert urgent["eligible"] is True


def test_report_surfaces_pr137_as_merged_and_residual_source_contract_backlog(tmp_path):
    module = load_module()
    repo = tmp_path
    (repo / "tasks").mkdir()
    (repo / "tasks" / "todo.md").write_text("# Backlog\n", encoding="utf-8")
    (repo / "tasks" / "operator-decisions.md").write_text("# Operator Decisions\n", encoding="utf-8")

    report = module.render_report(load_fixture("report_state.json"), repo_root=repo, output_format="markdown")

    assert "PR #137: merged" in report
    assert "F0061/source-contract residuals" in report
    assert "do not duplicate PR #137 landed changes" in report


def test_report_json_shape_feeds_operator_brief(tmp_path):
    module = load_module()
    out = tmp_path / "nested" / "flyer-train.json"

    result = module.main([
        "report",
        "--repo-root",
        str(tmp_path),
        "--offline",
        "--state-json",
        str(FIXTURES / "report_state.json"),
        "--format",
        "json",
        "--out",
        str(out),
    ])

    assert result == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "attention"
    assert payload["merged_not_deployed"][0]["number"] == 137
    assert payload["needs_srini"]


def test_next_candidate_returns_human_decision_for_product_judgment():
    module = load_module()

    decision = module.choose_next_candidate({
        "backlog": [{"id": "provider-posture-openrouter-edits", "category": "provider_model_posture"}]
    })

    assert decision["status"] == "human_decision_required"


def test_next_candidate_does_not_select_landed_pr137_work():
    module = load_module()

    decision = module.choose_next_candidate(load_fixture("report_state.json"))

    assert decision["id"] == "golden-live-shape-fixtures"
    assert decision["id"] != "pr137-source-contract-first"


def test_offline_required_for_report_and_next_candidate():
    report = subprocess.run(
        [sys.executable, str(MODULE_PATH), "report", "--repo-root", str(REPO_ROOT)],
        check=False,
        text=True,
        capture_output=True,
    )
    next_candidate = subprocess.run(
        [sys.executable, str(MODULE_PATH), "next-candidate", "--repo-root", str(REPO_ROOT)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert report.returncode == 2
    assert next_candidate.returncode == 2
    assert "--offline is required" in report.stderr
    assert "--offline is required" in next_candidate.stderr


def test_static_guard_no_live_network_or_mutation_paths():
    source = MODULE_PATH.read_text(encoding="utf-8")
    banned = [
        "import requests",
        "from requests",
        "import urllib",
        "from urllib",
        "import http.client",
        "import subprocess",
        "gh pr",
        "git push",
        "scp ",
        "ssh ",
        "systemctl",
    ]

    assert not any(token in source for token in banned)
