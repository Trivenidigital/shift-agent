"""Repo-level invariants — tests that lock structural decisions which would
otherwise rot through inattentive merges or refactor noise.

These tests don't exercise behavior; they assert presence/absence of specific
patterns that codify "this isn't here on purpose." Adding entries here costs
~5 lines per invariant and prevents the regression class where a bad
old-branch merge silently re-introduces a removed feature.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────
# PR #20 — SHA-256 chain decoration removal
# ─────────────────────────────────────────────────────────────────


def test_no_append_sha_chain_function_in_log_decision_direct():
    """The _append_sha_chain function was removed in PR #20 (audit-log-chain
    was decoration with ~3% writer coverage and no verifier; chose Option B
    'remove decoration' over Option A 'build infrastructure'). Lock that
    decision so a future merge or refactor doesn't silently re-introduce
    the function. The string still appears in a historical-note comment
    block; this test ignores comments and only flags actual code re-introduction.
    """
    script = REPO_ROOT / "src" / "platform" / "scripts" / "log-decision-direct"
    assert script.exists(), f"log-decision-direct script missing at {script}"
    content = script.read_text(encoding="utf-8")

    # Strip Python comments + docstrings before searching. Naive but sufficient
    # for this script: line-comments start with `#` (after optional whitespace);
    # the docstring is a single triple-quoted block at the top.
    code_lines = []
    in_docstring = False
    for line in content.splitlines():
        stripped = line.lstrip()
        # Toggle docstring state on triple-quote lines
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # Single-line docstring (open + close on same line)
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                continue
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        # Drop full-line comments
        if stripped.startswith("#"):
            continue
        code_lines.append(line)

    code = "\n".join(code_lines)

    # Code-side check: no function definition AND no call site.
    assert not re.search(r"^\s*def\s+_append_sha_chain", code, re.MULTILINE), (
        "_append_sha_chain function definition re-introduced. Either complete "
        "the chokepoint move into safe_io.ndjson_append (per the deferred-"
        "until-compliance backlog entry) or remove again. See PR #20."
    )
    assert "_append_sha_chain(" not in code, (
        "_append_sha_chain call site re-introduced. See PR #20 historical note."
    )


def test_no_sha256_chain_path_in_log_decision_direct():
    """Same intent as above: the chain-file path/lock should not be referenced
    by code. Comment block in log-decision-direct documents the path for
    future re-introduction; that's expected."""
    script = REPO_ROOT / "src" / "platform" / "scripts" / "log-decision-direct"
    content = script.read_text(encoding="utf-8")

    # Strip comments (same pass as above, simplified — only line comments matter
    # for path string detection since paths aren't in docstrings here).
    code = "\n".join(
        line for line in content.splitlines()
        if not line.lstrip().startswith("#")
    )

    assert "decisions.log.sha256" not in code, (
        "decisions.log.sha256 path reappeared in code (not just a comment). "
        "See PR #20: chain was removed; if re-introducing, do it at the "
        "safe_io.ndjson_append chokepoint, not back in log-decision-direct."
    )


def test_send_path_ci_runs_dynamic_non_flyer_suite_and_agent_changes():
    workflow = (REPO_ROOT / ".github" / "workflows" / "send-path-ci.yml").read_text(encoding="utf-8")

    assert '"src/agents/**"' in workflow
    assert '"tools/**"' in workflow
    assert "find tests -maxdepth 1 -type f -name 'test_*.py' ! -name 'test_flyer*'" in workflow
    assert "test_bridge_send_harness.py \\" not in workflow
    assert "test_shift_reconcile.py" not in workflow


def test_every_test_file_named_in_a_workflow_collects_at_least_one_test():
    """A gate that names a file which collects nothing advertises coverage it
    does not have, and pytest reports no error for it.

    This fired on `tests/test_flyer_manual_queue_cli.py`: PR #561 shipped the
    `--no-notify` silent-close CLI and, as its ONLY test change, added that file
    at 0 bytes. flyer-extended-ci listed it for ~6 weeks, so the silent-close
    path read as gated while having no coverage at all.
    """
    workflow_dir = REPO_ROOT / ".github" / "workflows"
    workflows = sorted(set(workflow_dir.glob("*.yml")) | set(workflow_dir.glob("*.yaml")))
    assert workflows, "no workflows found — path wrong?"

    named = re.compile(r"tests/(test_[A-Za-z0-9_]+\.py)")
    # Jobs may declare `defaults.run.working-directory` (cockpit-ci runs from
    # web/backend, which has its own tests/). Resolve against every root the
    # workflow mentions, plus the repo root, so a correctly-rooted reference is
    # not reported as missing.
    working_dir = re.compile(r"working-directory:\s*(\S+)")

    def strip_yaml_comments(text: str) -> str:
        """A path mentioned in a comment is documentation, not a gate — this
        test's own rationale comment in flyer-extended-ci.yml names the empty
        file it removed, and must not re-flag it."""
        kept = []
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            kept.append(line.split(" #", 1)[0])
        return "\n".join(kept)

    offenders = []
    for workflow in workflows:
        text = strip_yaml_comments(workflow.read_text(encoding="utf-8"))
        roots = [REPO_ROOT] + [REPO_ROOT / d for d in set(working_dir.findall(text))]
        for filename in sorted(set(named.findall(text))):
            candidates = [root / "tests" / filename for root in roots]
            path = next((c for c in candidates if c.exists()), None)
            if path is None:
                offenders.append(f"{workflow.name} names tests/{filename} — file does not exist")
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            has_test = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
                for node in ast.walk(tree)
            )
            if not has_test:
                offenders.append(
                    f"{workflow.name} names tests/{filename} but it defines no test_* function"
                    f" ({path.stat().st_size} bytes)"
                )

    assert not offenders, (
        "CI gates name test files that collect nothing:\n  " + "\n  ".join(offenders)
    )


def test_cockpit_ci_checks_committed_typegen_schema():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cockpit-ci.yml").read_text(encoding="utf-8")

    assert "npm run generate:types" in workflow
    assert "git diff --exit-code -- src/api/schema.ts" in workflow


# ── flyer workflow trigger parity ──────────────────────────────────────────
#
# All three Flyer gates listed strictly more paths under `pull_request` than
# under `push`, so a path could be gated on the PR and ungated once merged.
# Concretely: a push to main touching only `src/plugins/cf-router/**` or
# `tests/test_flyer_brand_asset_routing.py` did not run flyer-premium-ci, while
# the identical change on a PR did.
#
# The asymmetry existed in all three workflows in the same direction, which is
# why this is an invariant rather than three one-line edits: fixing only the
# workflow named by an incident leaves the same defect beside it.

# Workflows exempted from trigger parity, each with a specific reason. Parity is
# the default; an exemption is a claim someone has to write down.
class _WorkflowLoader(yaml.SafeLoader):
    """YAML 1.1 resolves a bare `on:` key to the boolean True, which silently
    turns a workflow's trigger block into a key no lookup by name finds. Keep
    booleans as strings so `on` stays `"on"`."""


_WorkflowLoader.add_constructor(
    "tag:yaml.org,2002:bool", lambda loader, node: loader.construct_scalar(node)
)


PARITY_EXEMPT = {
    # Its path-scoped steps are individually guarded by
    # `if: github.event_name == 'pull_request'` and consume
    # `github.event.pull_request.body` / `.base.sha`, which do not exist on a
    # push. Its pull_request trigger deliberately has NO paths filter so the
    # registry-integrity check runs on every PR.
    "architecture-governance.yml": "pull_request-only steps consume PR context",
}


def _parity_workflows() -> list[str]:
    """Every workflow gating BOTH event types, minus documented exemptions.

    Derived, never a hardcoded list: a fourth Flyer gate added next month
    inherits the rule the day it lands, which a literal tuple would not give.
    """
    out = []
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        if path.name in PARITY_EXEMPT:
            continue
        triggers = yaml.load(path.read_text(encoding="utf-8"), Loader=_WorkflowLoader)["on"]
        if isinstance(triggers, dict) and "pull_request" in triggers and "push" in triggers:
            out.append(path.name)
    return out


PARITY_WORKFLOWS = _parity_workflows()


def _trigger_paths(workflow_name: str) -> tuple[set[str], set[str]]:
    """(pull_request paths, push paths) for one workflow, parsed structurally."""
    path = REPO_ROOT / ".github" / "workflows" / workflow_name
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_WorkflowLoader)
    triggers = data["on"]
    def paths_for(event: str) -> set[str]:
        block = triggers.get(event) or {}
        return set(block.get("paths") or [])
    return paths_for("pull_request"), paths_for("push")


@pytest.mark.parametrize("workflow_name", PARITY_WORKFLOWS)
def test_workflow_pr_and_push_path_filters_match(workflow_name):
    pr_paths, push_paths = _trigger_paths(workflow_name)
    assert pr_paths, f"{workflow_name}: no pull_request paths parsed — shape changed?"
    assert push_paths, f"{workflow_name}: no push paths parsed — shape changed?"
    pr_only = sorted(pr_paths - push_paths)
    push_only = sorted(push_paths - pr_paths)
    assert not pr_only, (
        f"{workflow_name} gates these paths on pull_request but NOT on push, so a "
        f"change to them is unverified once merged: {pr_only}"
    )
    assert not push_only, (
        f"{workflow_name} gates these paths on push but NOT on pull_request, so a "
        f"change to them is unverified before merge: {push_only}"
    )


@pytest.mark.parametrize(
    "workflow_name,required",
    [
        ("flyer-premium-ci.yml", "src/plugins/cf-router/**"),
        ("flyer-premium-ci.yml", "tests/test_flyer_brand_asset_routing.py"),
        ("flyer-premium-ci.yml", ".github/workflows/flyer-premium-ci.yml"),
        ("flyer-core-ci.yml", "tests/test_flyer*.py"),
        ("flyer-core-ci.yml", "tests/_flyer_replay_helpers.py"),
        ("flyer-extended-ci.yml", "src/plugins/cf-router/**"),
        ("flyer-extended-ci.yml", "tests/test_cf_router_flyer_routing.py"),
    ],
)
def test_representative_path_is_gated_on_both_event_types(workflow_name, required):
    """Named explicitly rather than left to set equality: these are the paths
    whose one-sided gating produced the incident, and a future edit that drops
    one from BOTH lists would satisfy parity while removing the coverage."""
    pr_paths, push_paths = _trigger_paths(workflow_name)
    assert required in pr_paths, f"{workflow_name} no longer gates {required} on pull_request"
    assert required in push_paths, f"{workflow_name} no longer gates {required} on push"


def _matches_actions_glob(pattern: str, path: str) -> bool:
    """The `*` / `**` subset of GitHub Actions path-filter matching.

    `**` crosses directory separators, a single `*` does not. `fnmatch` would
    let `*` swallow `/` and quietly report coverage the runner will not give.

    Deliberately NOT a full implementation: Actions also gives `?`, `+`, `[]`
    and a leading `!` negation their own meanings, and `!` would additionally
    break the set comparison above, which is order-insensitive while Actions
    applies negations sequentially. Rather than half-implement them,
    `test_no_parity_workflow_uses_an_unsupported_glob_feature` fails loudly if
    one ever appears — turning a latent wrong answer into a red test.
    """
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.fullmatch("".join(out), path) is not None


def _triggers(workflow_name: str, path: str) -> tuple[bool, bool]:
    pr_paths, push_paths = _trigger_paths(workflow_name)
    return (
        any(_matches_actions_glob(p, path) for p in pr_paths),
        any(_matches_actions_glob(p, path) for p in push_paths),
    )


@pytest.mark.parametrize(
    "workflow_name,changed_path",
    [
        # The incident paths: a brand-asset routing change lives in cf-router,
        # and its test is named directly by the premium gate.
        ("flyer-premium-ci.yml", "src/plugins/cf-router/hooks.py"),
        ("flyer-premium-ci.yml", "tests/test_flyer_brand_asset_routing.py"),
        ("flyer-premium-ci.yml", ".github/workflows/flyer-premium-ci.yml"),
        # A test-only change in each of the other two gates.
        ("flyer-core-ci.yml", "tests/test_flyer_renderer.py"),
        ("flyer-core-ci.yml", "tests/_flyer_replay_helpers.py"),
        ("flyer-extended-ci.yml", "tests/test_cf_router_flyer_routing.py"),
        ("flyer-extended-ci.yml", "src/plugins/cf-router/actions.py"),
    ],
)
def test_a_representative_change_triggers_on_pr_and_on_push(workflow_name, changed_path):
    """Membership in both lists is not the claim — matching is. A push to main
    touching only `src/plugins/cf-router/hooks.py` did not run flyer-premium-ci
    while the identical change on a PR did."""
    on_pr, on_push = _triggers(workflow_name, changed_path)
    assert on_pr, f"{changed_path} does not trigger {workflow_name} on pull_request"
    assert on_push, f"{changed_path} does not trigger {workflow_name} on push"


def test_the_glob_matcher_does_not_let_a_single_star_cross_a_separator():
    """Guard the guard: if `*` matched `/`, the parity proofs above would pass
    on filters that do not actually cover these paths."""
    assert _matches_actions_glob("src/agents/flyer/**", "src/agents/flyer/a/b.py")
    assert not _matches_actions_glob("tests/test_flyer*.py", "tests/sub/test_flyer_x.py")
    assert _matches_actions_glob("tests/test_flyer*.py", "tests/test_flyer_renderer.py")
    assert not _matches_actions_glob("src/agents/flyer/*", "src/agents/flyer/a/b.py")


def test_no_parity_workflow_uses_an_unsupported_glob_feature():
    """`_matches_actions_glob` implements the `*` / `**` subset. Actions also
    gives `?`, `+`, `[]` and a leading `!` their own meanings — and `!` would
    break the set-parity comparison too, which is order-insensitive while
    Actions applies negations sequentially. None are used today; this fails the
    day one appears, instead of silently returning a wrong answer."""
    offenders = []
    for name in PARITY_WORKFLOWS:
        pr_paths, push_paths = _trigger_paths(name)
        for pattern in sorted(pr_paths | push_paths):
            if pattern.startswith("!") or any(c in pattern for c in "?+[]"):
                offenders.append(f"{name}: {pattern}")
    assert not offenders, (
        "path filter uses a glob feature the matcher does not implement — extend "
        f"_matches_actions_glob (and revisit set-parity for `!`) before using it: {offenders}"
    )


def test_parity_workflow_list_is_derived_not_hardcoded():
    """A fourth Flyer gate must inherit the rule the day it lands."""
    assert "flyer-premium-ci.yml" in PARITY_WORKFLOWS
    assert "flyer-core-ci.yml" in PARITY_WORKFLOWS
    assert "flyer-extended-ci.yml" in PARITY_WORKFLOWS
    assert "cockpit-ci.yml" in PARITY_WORKFLOWS, "cockpit gates both events and is not exempt"
    assert "architecture-governance.yml" not in PARITY_WORKFLOWS, "documented exemption"
    # Every non-exempt workflow gating both event types is covered.
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        triggers = yaml.load(path.read_text(encoding="utf-8"), Loader=_WorkflowLoader)["on"]
        gates_both = isinstance(triggers, dict) and "pull_request" in triggers and "push" in triggers
        if gates_both and path.name not in PARITY_EXEMPT:
            assert path.name in PARITY_WORKFLOWS, f"{path.name} gates both events but escaped the sweep"


def test_every_parity_exemption_names_a_real_workflow():
    """An exemption for a deleted workflow is a stale claim that hides the next
    one to take its name."""
    for name in PARITY_EXEMPT:
        assert (REPO_ROOT / ".github" / "workflows" / name).is_file(), f"exempt but missing: {name}"
