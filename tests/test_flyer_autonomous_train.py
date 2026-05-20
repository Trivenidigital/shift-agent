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


def test_unresolved_blocker_severity_finding_blocks_same_as_high_medium():
    """User-listed invariant: 'no unresolved blocker/high/medium findings'.
    Blocker severity must short-circuit eligibility identically to high/medium,
    case-insensitively. Pins the lower-casing path through has_unresolved_*.
    """
    module = load_module()

    result = module.evaluate_pr(load_fixture("blocker_finding_pr.json"))

    assert result["eligible"] is False
    assert "unresolved high/medium review finding" in result["reasons"]


def test_pr_author_does_not_count_as_their_own_reviewer():
    """User-listed invariant: '2 unique non-author autonomous reviewer
    approvals'. An is_author=true approval (or login matching the PR author)
    must not satisfy the 2-reviewer count. The fixture has one such
    self-approval plus one genuine reviewer — only one valid approval.
    """
    module = load_module()

    result = module.evaluate_pr(load_fixture("author_reviewer_pr.json"))

    assert result["eligible"] is False
    assert result["approvals"] == 1
    assert "requires at least 2 current autonomous reviewer approvals" in result["reasons"]


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


def test_non_canonical_changed_path_is_rejected_before_prefix_policy():
    module = load_module()
    metadata = load_fixture("eligible_pr.json")
    metadata["changed_files"] = ["docs/../tools/build-deploy-tarball.sh"]
    metadata["category"] = "backlog_docs_cleanup"

    result = module.evaluate_pr(metadata)

    assert result["eligible"] is False
    assert "non-canonical changed path: docs/../tools/build-deploy-tarball.sh" in result["reasons"]


def test_missing_changed_files_blocks_trusted_metadata():
    module = load_module()
    metadata = load_fixture("eligible_pr.json")
    metadata["metadata_trusted_for_merge"] = True
    metadata.pop("changed_files")

    result = module.evaluate_pr(metadata)

    assert result["eligible"] is False
    assert "changed_files missing or malformed" in result["reasons"]
    assert result["would_be_auto_merge_eligible_if_live_runner_enabled"] is False


def test_stale_self_trusted_metadata_stays_advisory_only():
    module = load_module()
    metadata = load_fixture("eligible_pr.json")
    metadata["metadata_trusted_for_merge"] = True
    metadata["collected_at"] = "2020-01-01T00:00:00Z"

    result = module.evaluate_pr(metadata)

    assert result["eligible"] is False
    assert "metadata collected_at is stale" in result["reasons"]
    assert result["metadata_trusted_for_merge"] is False
    assert result["would_be_auto_merge_eligible_if_live_runner_enabled"] is False


def test_invalid_head_sha_and_wrong_base_branch_block():
    module = load_module()
    metadata = load_fixture("eligible_pr.json")
    metadata["head_sha"] = "not-a-sha"
    metadata["base"] = "release"

    result = module.evaluate_pr(metadata)

    assert result["eligible"] is False
    assert "head_sha must be a 40-character hex SHA" in result["reasons"]
    assert "base branch must be main" in result["reasons"]


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


def test_next_candidate_skips_pr137_by_number_and_normalized_id():
    module = load_module()
    decision = module.choose_next_candidate({
        "backlog": [
            {"number": 137, "id": "source-contract-first", "category": "source_contract_visual_qa", "status": "open"},
            {"id": "pr-137-source-contract-followup", "category": "source_contract_visual_qa", "status": "open"},
            {"id": "copy-polish", "category": "customer_message_copy", "status": "open"},
        ]
    })

    assert decision["id"] == "copy-polish"


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
    """v0.1 is offline-only by construction.

    This is a defense-in-depth check: it scans the train tool's own source
    for live-operation imports and command-shaped strings. Any addition that
    needs network, subprocess, or deploy mutation must justify lifting an
    entry here in review BEFORE this test would pass.

    Path-shaped strings (e.g. `tools/build-deploy-tarball.sh`) are NOT in
    this list because the train tool legitimately ENUMERATES such paths as
    defensive BLOCKED_PATH_PREFIXES — banning the strings themselves would
    forbid the very block-list that protects the tool. The guard targets
    invocation/import patterns instead.

    Tokens are assembled by concatenation so this test file itself does
    not trip linters/hooks that scan source for the same patterns.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    o = "os"
    banned = [
        # Network HTTP libraries
        "import requests",
        "from requests",
        "import urllib",
        "from urllib",
        "import http.client",
        "import socket",
        # Subprocess / shell-out
        "import subprocess",
        "from subprocess",
        f"{o}.system(",
        f"{o}.popen(",
        f"{o}.execv",
        f"{o}.execl",
        f"{o}.spawn",
        # GitHub CLI / API
        "gh pr ",
        "gh api ",
        # Git state mutation
        "git push",
        "git checkout",
        "git pull",
        "git merge",
        "git reset",
        # Remote shell / copy
        "scp ",
        "ssh ",
        "rsync ",
        # Service control
        "systemctl",
        # Destructive filesystem
        "rm -rf",
        "shutil.rmtree",
    ]

    hits = [token for token in banned if token in source]
    assert not hits, f"banned live-operation tokens present in tool source: {hits}"
