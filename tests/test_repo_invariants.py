"""Repo-level invariants — tests that lock structural decisions which would
otherwise rot through inattentive merges or refactor noise.

These tests don't exercise behavior; they assert presence/absence of specific
patterns that codify "this isn't here on purpose." Adding entries here costs
~5 lines per invariant and prevents the regression class where a bad
old-branch merge silently re-introduces a removed feature.
"""
from __future__ import annotations

import ast
import fnmatch
import re
import shlex
import subprocess
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


# ── push gates must not cancel each other ──────────────────────────────────
#
# `concurrency.group: <name>-${{ github.ref }}` + `cancel-in-progress: true` is
# the right rule for pull_request (a new commit supersedes a run nobody wants
# any more) and the wrong one for push-to-main: `github.ref` is
# `refs/heads/main` for EVERY commit, so all of them share one group and each
# merge cancels the gate still running for the merge before it.
#
# Observed, not inferred: of send-path-ci's last 100 push runs on main, 14 ended
# `cancelled`, each within 4-20s of the NEXT main push run being created — e.g.
# abf7bb02 cancelled 38s in when d6f9ba8c landed. Inter-merge gaps on main are
# p25=77s and p50=470s against multi-minute jobs, so the collision window is
# routinely hit rather than exceptional.
#
# What that costs is per-SHA attribution, not the tested content: in all 14
# cases a descendant commit's run later completed green over a tree that
# contained the cancelled commit's changes. But those 14 SHAs have no completed
# gate of their own, and "was main green at SHA X" is the question deploy
# authorization asks here — a deploy is a full-tree replace pinned to one SHA.
# It is also what a bisect over that range needs, and what a revert destroys:
# once a change is reverted, the only tree that ever contained it is one no gate
# finished on.
#
# Mechanized rather than listed because the shape is copy-pasted: four of the
# seven workflows carry the same `-${{ github.ref }}` group, and the fifth to be
# written would inherit the defect rather than the fix.


class UnsupportedWorkflowExpression(Exception):
    """A `${{ }}` construct the evaluator below does not implement.

    Raised rather than guessed: a wrong answer here reads as "this gate is
    per-commit" while the runner keeps cancelling.
    """


_EXPR_RE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)


def _eval_expression(text: str, context: dict[str, str]):
    """The `==`/`!=`, `&&`, `||`, literal subset of the Actions expression
    language, with GitHub's operand-returning `&&`/`||` semantics — `A && B ||
    C` is the documented ternary idiom, not a boolean and."""
    tokens = re.findall(r"'[^']*'|==|!=|&&|\|\||\(|\)|[A-Za-z_][A-Za-z0-9_.-]*", text)
    if "".join(tokens) != "".join(text.split()):
        raise UnsupportedWorkflowExpression(f"cannot tokenize: {text!r}")
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        return tok

    def primary():
        tok = take()
        if tok == "(":
            value = or_expr()
            if take() != ")":
                raise UnsupportedWorkflowExpression(f"unbalanced parens: {text!r}")
            return value
        if tok.startswith("'"):
            return tok[1:-1]
        if tok in ("true", "false"):
            return tok == "true"
        if tok in context:
            return context[tok]
        raise UnsupportedWorkflowExpression(
            f"unknown context reference {tok!r} in {text!r} — teach the evaluator "
            "what it resolves to per event before keying a concurrency group on it"
        )

    def comparison():
        left = primary()
        while peek() in ("==", "!="):
            op = take()
            right = primary()
            left = (left == right) if op == "==" else (left != right)
        return left

    def and_expr():
        left = comparison()
        while peek() == "&&":
            take()
            right = comparison()
            left = right if left not in (False, "", None) else left
        return left

    def or_expr():
        left = and_expr()
        while peek() == "||":
            take()
            right = and_expr()
            left = left if left not in (False, "", None) else right
        return left

    value = or_expr()
    if pos != len(tokens):
        raise UnsupportedWorkflowExpression(f"trailing tokens in {text!r}")
    return value


def _render_group(group: str, *, event_name: str, ref: str, sha: str) -> str:
    context = {
        "github.event_name": event_name,
        "github.ref": ref,
        "github.ref_name": ref.rsplit("/", 1)[-1],
        "github.sha": sha,
        "github.workflow": "workflow",
        "github.head_ref": "head",
        "github.run_id": "run-id",
        "github.repository": "owner/repo",
    }
    return _EXPR_RE.sub(
        lambda m: str(_eval_expression(m.group(1).strip(), context)), group
    )


def _workflow_data(workflow_name: str) -> dict:
    return yaml.load(
        (REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8"),
        Loader=_WorkflowLoader,
    )


def _concurrency_blocks(workflow_name: str) -> list[tuple[str, dict]]:
    """(where, block) for the workflow-level block and every job-level one — a
    job-level `concurrency` cancels the same way and must not escape."""
    data = _workflow_data(workflow_name)
    blocks = []
    if isinstance(data.get("concurrency"), dict):
        blocks.append(("workflow", data["concurrency"]))
    for job_name, job in (data.get("jobs") or {}).items():
        if isinstance(job, dict) and isinstance(job.get("concurrency"), dict):
            blocks.append((f"job:{job_name}", job["concurrency"]))
    return blocks


def _cancelling_blocks(workflow_name: str) -> list[tuple[str, str]]:
    """(where, group) for blocks that actually cancel. Anything but a literal
    false counts as cancelling — a group that MIGHT cancel has to survive the
    same test."""
    out = []
    for where, block in _concurrency_blocks(workflow_name):
        if str(block.get("cancel-in-progress")).strip().lower() in ("false", "none", ""):
            continue
        out.append((where, str(block["group"])))
    return out


def _gate_workflows() -> list[str]:
    """Workflows a code change can wake: those triggering on pull_request or
    push. Schedule-only workflows (hermes-drift-check) gate no merge and are
    outside every rule below."""
    return [
        path.name
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
        if isinstance(_workflow_data(path.name)["on"], dict)
        and {"pull_request", "push"} & set(_workflow_data(path.name)["on"])
    ]


GATE_WORKFLOWS = _gate_workflows()
PUSH_CANCELLING = [w for w in GATE_WORKFLOWS
                   if "push" in _workflow_data(w)["on"] and _cancelling_blocks(w)]
PR_CANCELLING = [w for w in GATE_WORKFLOWS
                 if "pull_request" in _workflow_data(w)["on"] and _cancelling_blocks(w)]


@pytest.mark.parametrize("workflow_name", PUSH_CANCELLING)
def test_a_push_gate_is_not_cancelled_by_the_next_commit_on_the_same_branch(workflow_name):
    """Two commits on one branch must land in DIFFERENT concurrency groups, or
    the second one's run cancels the first one's gate."""
    for where, group in _cancelling_blocks(workflow_name):
        first = _render_group(group, event_name="push", ref="refs/heads/main", sha="a" * 40)
        second = _render_group(group, event_name="push", ref="refs/heads/main", sha="b" * 40)
        assert first != second, (
            f"{workflow_name} ({where}) puts every push to refs/heads/main in one "
            f"concurrency group {first!r} with cancel-in-progress, so each merge cancels "
            f"the gate still running for the merge before it and that commit never gets a "
            f"completed result. Key the group per commit on push: "
            f"${{{{ github.event_name == 'push' && github.sha || github.ref }}}}"
        )


@pytest.mark.parametrize("workflow_name", PR_CANCELLING)
def test_a_superseded_pull_request_run_is_still_cancelled(workflow_name):
    """The other half of the rule, so the fix above is not applied as a blanket
    `github.sha`: on a pull_request a new commit makes the previous run
    worthless, and cancelling it is the behavior worth keeping."""
    for where, group in _cancelling_blocks(workflow_name):
        first = _render_group(group, event_name="pull_request", ref="refs/pull/7/merge", sha="a" * 40)
        second = _render_group(group, event_name="pull_request", ref="refs/pull/7/merge", sha="b" * 40)
        assert first == second, (
            f"{workflow_name} ({where}) gives two commits on the SAME pull request "
            f"different groups ({first!r} vs {second!r}), so an obsolete run is never "
            f"superseded. Key the pull_request side on the ref, not the sha."
        )
        other_pr = _render_group(group, event_name="pull_request", ref="refs/pull/8/merge", sha="a" * 40)
        assert first != other_pr, (
            f"{workflow_name} ({where}) shares one group across different pull requests, "
            f"so unrelated PRs cancel each other."
        )


def test_the_expression_evaluator_reproduces_github_ternary_semantics():
    """Guard the guard: `&&`/`||` return operands, not booleans. Were they
    boolean, the fixed group would render `w-True` for both SHAs and this
    invariant would pass on a still-cancelling workflow."""
    group = "w-${{ github.event_name == 'push' && github.sha || github.ref }}"
    assert _render_group(group, event_name="push", ref="refs/heads/main", sha="abc") == "w-abc"
    assert _render_group(group, event_name="pull_request", ref="refs/pull/1/merge", sha="abc") == (
        "w-refs/pull/1/merge"
    )
    assert _render_group("w-${{ github.ref }}", event_name="push", ref="refs/heads/main", sha="abc") == (
        "w-refs/heads/main"
    )
    with pytest.raises(UnsupportedWorkflowExpression):
        _render_group("w-${{ github.event.pull_request.number }}", event_name="push", ref="r", sha="s")


def test_concurrency_sweep_covers_every_workflow_that_cancels():
    """Derived, not listed: a workflow that grows a cancelling concurrency block
    inherits both rules the day it lands."""
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        if not _cancelling_blocks(path.name):
            continue
        triggers = _workflow_data(path.name)["on"]
        if "push" in triggers:
            assert path.name in PUSH_CANCELLING, f"{path.name} cancels push runs but escaped the sweep"
        if "pull_request" in triggers:
            assert path.name in PR_CANCELLING, f"{path.name} cancels PR runs but escaped the sweep"
    assert PUSH_CANCELLING, "no push-cancelling workflow found — parsing broke?"


# ── a gate must be woken by the tests it runs ──────────────────────────────
#
# #724 closed the case where a path was gated before merge and ungated after
# it. This closes the neighbouring one: a path that is gated by a workflow
# which does not RUN the file, while the workflow that runs it is never woken.
#
# Three files reached that state — test_flyer_intake_fields.py,
# test_flyer_source_edit_sla_watchdog.py and test_flyer_tranche2_telemetry.py.
# flyer-premium-ci executes all three by name; none appears in any workflow's
# trigger `paths`. flyer-core-ci's `tests/test_flyer*.py` filter DOES wake on
# them, which is exactly what made this invisible — it wakes, then subtracts
# them again via .github/flyer-shards/owned-elsewhere.txt because premium owns
# them. Editing one of the three alone therefore ran it in no job at all.
#
# Note the shape of the rule that follows. "Every executed test file is
# reachable from SOME workflow's paths" is the intuitive phrasing and it is
# useless here: all three files satisfy it via flyer-core-ci's glob while being
# executed by nobody the change wakes. The claim has to be per-workflow — every
# file a gate executes must wake THAT gate — plus the same for the manifests a
# gate reads to decide what to execute, since editing a shard list changes what
# runs just as surely as editing a workflow does.


class UnparseableCIConstruct(Exception):
    """A `run:` construct that cannot be resolved to a concrete file set.

    Raised, never skipped. A silent skip would recreate the exact defect this
    section exists to catch: a test executed by a gate nothing wakes, invisible
    because the analyzer could not read the line that runs it.
    """


_PYTEST_LAUNCHERS = ("pytest",)
_PYTEST_VALUE_FLAGS = {"-p", "-k", "-m", "-n", "--deselect", "--ignore", "--rootdir", "--junitxml", "--tb"}
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=\$\((?P<body>.*)\)$", re.DOTALL)
_MAPFILE_RE = re.compile(r"^mapfile\s+-t\s+([A-Za-z_][A-Za-z0-9_]*)\s*<\s*<\((?P<body>.*)\)$", re.DOTALL)
_VAR_RE = re.compile(r"^\"?\$\{?([A-Za-z_][A-Za-z0-9_]*)(?:\[@\])?\}?\"?$")
_PIP_INSTALL_RE = re.compile(r"^\S*(?:python3?\s+-m\s+)?pip\s+install\b")


def _split_top_level(text: str, separator: str) -> list[str]:
    """Split on `separator` outside quotes and outside `$( )` / `<( )`."""
    parts, buf, depth, quote = [], [], 0, None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            buf.append(ch)
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif text.startswith(("$(", "<("), i):
            depth += 1
            buf.append(text[i:i + 2])
            i += 2
            continue
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif depth == 0 and text.startswith(separator, i):
            parts.append("".join(buf))
            buf = []
            i += len(separator)
            continue
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


class _ScriptContext:
    """One `run:` block's view of the filesystem. Variables and temp files do
    not survive between steps — each `run` is its own shell — so a context is
    built per step.

    Assignments are resolved LAZILY, on use as a pytest target. A step that
    computes something unrelated (`resolved=$(git rev-parse HEAD^1)`) must not
    have to be expressible as a file list to be legal.
    """

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.variables: dict[str, str] = {}
        self.files: dict[str, str] = {}
        self.manifests: set[str] = set()

    def resolve(self, token: str) -> Path:
        return self.workdir / token

    def variable(self, name: str) -> list[str]:
        if name not in self.variables:
            raise UnparseableCIConstruct(f"pytest target ${name} was never assigned in this step")
        return _eval_pipeline(self.variables[name], self)

    def read(self, token: str) -> list[str]:
        """Read a file the script reads, and remember it as a manifest — a gate
        that decides its test list from a checked-in file has that file in its
        blast radius. A file the step itself produced is not a manifest."""
        if token in self.files:
            return _eval_pipeline(self.files[token], self)
        path = self.resolve(token)
        if not path.is_file():
            raise UnparseableCIConstruct(f"reads {token!r}, which is not a file in the repo")
        self.manifests.add(path.relative_to(REPO_ROOT).as_posix())
        return path.read_text(encoding="utf-8").splitlines()


def _find_files(tokens: list[str], ctx: _ScriptContext) -> list[str]:
    """`find <dir> [-maxdepth N] -type f -name 'G' [! -name 'G']...`"""
    root, maxdepth, includes, excludes = tokens[1], None, [], []
    i, negate = 2, False
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-maxdepth":
            maxdepth = int(tokens[i + 1]); i += 2
        elif tok == "-type" and tokens[i + 1] == "f":
            i += 2
        elif tok == "!":
            negate = True; i += 1
        elif tok == "-name":
            (excludes if negate else includes).append(tokens[i + 1])
            negate = False
            i += 2
        else:
            raise UnparseableCIConstruct(f"unsupported find operand {tok!r}")
    base = ctx.resolve(root)
    if not base.is_dir():
        raise UnparseableCIConstruct(f"find root {root!r} is not a directory")
    out = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        depth = len(path.relative_to(base).parts)
        if maxdepth is not None and depth > maxdepth:
            continue
        name = path.name
        if includes and not any(fnmatch.fnmatchcase(name, g) for g in includes):
            continue
        if any(fnmatch.fnmatchcase(name, g) for g in excludes):
            continue
        out.append((Path(root) / path.relative_to(base)).as_posix())
    return out


def _eval_stage(stage: str, stdin: list[str] | None, ctx: _ScriptContext) -> list[str]:
    stage = re.sub(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)+", "", stage.strip())
    if stage.startswith("comm "):
        subs = re.findall(r"<\((.*?)\)(?=\s|$)", stage)
        flags = stage.split()[1]
        if flags != "-23" or len(subs) != 2:
            raise UnparseableCIConstruct(f"only `comm -23 <(a) <(b)` is understood: {stage!r}")
        left, right = _eval_pipeline(subs[0], ctx), _eval_pipeline(subs[1], ctx)
        return [line for line in left if line not in set(right)]
    tokens = shlex.split(stage)
    head = tokens[0]
    if head == "find":
        return _find_files(tokens, ctx)
    if head == "cat":
        out = []
        for pattern in tokens[1:]:
            matches = sorted(ctx.workdir.glob(pattern))
            if not matches:
                raise UnparseableCIConstruct(f"`cat {pattern}` matches no file")
            for match in matches:
                out += ctx.read(match.relative_to(ctx.workdir).as_posix())
        return out
    if head == "grep":
        if tokens[1] != "-vE":
            raise UnparseableCIConstruct(f"only `grep -vE` is understood: {stage!r}")
        pattern, operands = tokens[2], tokens[3:]
        lines = stdin if not operands else [ln for f in operands for ln in ctx.read(f)]
        if lines is None:
            raise UnparseableCIConstruct(f"grep with neither stdin nor a file: {stage!r}")
        return [ln for ln in lines if not re.search(pattern, ln)]
    if head == "sort":
        if stdin is None:
            raise UnparseableCIConstruct(f"sort with no stdin: {stage!r}")
        return stdin
    if head == "tr":
        if "<" in tokens:
            return ctx.read(tokens[tokens.index("<") + 1])
        if stdin is None:
            raise UnparseableCIConstruct(f"tr with no stdin: {stage!r}")
        return stdin
    raise UnparseableCIConstruct(f"unsupported command in a file-list pipeline: {stage!r}")


def _eval_pipeline(command: str, ctx: _ScriptContext) -> list[str]:
    value: list[str] | None = None
    for stage in _split_top_level(command, "|"):
        value = _eval_stage(stage, value, ctx)
    return [ln.strip() for ln in (value or []) if ln.strip()]


def _logical_lines(script: str) -> list[str]:
    joined = re.sub(r"\\\n\s*", " ", script)
    return [ln.strip() for ln in joined.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _test_files_under(root: Path) -> list[str]:
    return [
        path.relative_to(REPO_ROOT).as_posix()
        for path in sorted(root.rglob("test_*.py"))
        if path.is_file()
        and not any(part.startswith(".") or part == "node_modules" for part in path.relative_to(root).parts)
    ]


def _pytest_targets(command: str, ctx: _ScriptContext) -> list[str]:
    """Repo-relative test files one pytest invocation executes."""
    substitutions: list[str] = []

    def stash(match):
        substitutions.append(match.group(1))
        return f"@@SUB{len(substitutions) - 1}@@"

    command = re.sub(r"\$\((.*?)\)(?=\s|$)", stash, command)
    tokens = shlex.split(command)
    while tokens and (tokens[0] in ("python", "python3", "-m") or tokens[0] == "pytest"
                      or tokens[0].endswith("/pytest")):
        if tokens[0].endswith("pytest") and tokens[0] != "-m":
            tokens = tokens[1:]
            break
        tokens = tokens[1:]

    positional: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            name = tok.split("=", 1)[0]
            if "=" not in tok and name in _PYTEST_VALUE_FLAGS:
                i += 2
                continue
            if "=" in tok or re.fullmatch(r"-[a-zA-Z]+|--[a-z-]+", tok):
                i += 1
                continue
            raise UnparseableCIConstruct(f"unrecognized pytest flag {tok!r}")
        positional.append(tok)
        i += 1

    if not positional:
        # No target: pytest collects from the working directory.
        return _test_files_under(ctx.workdir)

    out: list[str] = []
    for tok in positional:
        sub = re.fullmatch(r"@@SUB(\d+)@@", tok)
        var = _VAR_RE.match(tok)
        if sub:
            out += _eval_pipeline(substitutions[int(sub.group(1))], ctx)
        elif var:
            out += ctx.variable(var.group(1))
        elif tok.startswith("@@SUB"):
            raise UnparseableCIConstruct(f"unresolved substitution in pytest target {tok!r}")
        else:
            out.append(tok.split("::", 1)[0])

    resolved = []
    for token in out:
        path = ctx.resolve(token)
        if path.is_dir():
            resolved += _test_files_under(path)
        elif path.is_file():
            resolved.append(path.relative_to(REPO_ROOT).as_posix())
        else:
            raise UnparseableCIConstruct(f"pytest target {token!r} does not exist")
    return resolved


def _is_pytest_command(line: str) -> bool:
    tokens = line.split()
    head = tokens[0]
    if head.endswith(_PYTEST_LAUNCHERS):
        return True
    return head in ("python", "python3") and tokens[1:3] == ["-m", "pytest"]


def _analyze_step(script: str, workdir: Path) -> tuple[set[str], set[str]]:
    """(test files this step executes, manifests it reads to decide that)."""
    ctx = _ScriptContext(workdir)
    executed: set[str] = set()
    for line in _logical_lines(script):
        assign = _ASSIGN_RE.match(line)
        mapfile = _MAPFILE_RE.match(line)
        if assign:
            ctx.variables[assign.group(1)] = assign.group("body")
            continue
        if mapfile:
            ctx.variables[mapfile.group(1)] = mapfile.group("body")
            continue
        redirect = re.match(r"^(?P<cmd>[a-z]+\s.*?)\s*>\s*(?P<target>\S+)$", line)
        if redirect:
            ctx.files[redirect.group("target")] = redirect.group("cmd")
            continue
        if _is_pytest_command(line):
            executed |= set(_pytest_targets(line, ctx))
            continue
        if _PIP_INSTALL_RE.match(line):
            # `pip install ... pytest` names the package, not a run.
            continue
        if re.search(r"(^|\s)pytest(\s|$)", line):
            raise UnparseableCIConstruct(
                f"line mentions pytest but was not recognized as an invocation: {line!r}"
            )
    return executed, ctx.manifests


def _workflow_execution_surface(workflow_name: str) -> tuple[set[str], set[str]]:
    data = _workflow_data(workflow_name)
    executed: set[str] = set()
    manifests: set[str] = set()
    for job in (data.get("jobs") or {}).values():
        job_dir = ((job.get("defaults") or {}).get("run") or {}).get("working-directory", ".")
        for step in job.get("steps") or []:
            if "run" not in step:
                continue
            workdir = REPO_ROOT / step.get("working-directory", job_dir)
            step_executed, step_manifests = _analyze_step(step["run"], workdir)
            executed |= step_executed
            manifests |= step_manifests
    return executed, manifests


def _trigger_wakes(workflow_name: str, changed_path: str) -> bool:
    """Would a commit touching ONLY `changed_path` start this workflow?

    An event listed with no `paths` key matches every change; `paths-ignore`
    is refused rather than approximated.
    """
    triggers = _workflow_data(workflow_name)["on"]
    for event in ("pull_request", "push"):
        block = triggers.get(event)
        if event not in triggers:
            continue
        block = block or {}
        if "paths-ignore" in block:
            raise UnparseableCIConstruct(
                f"{workflow_name}: `paths-ignore` on {event} — the matcher implements "
                "`paths` only; implement the inverse before using it"
            )
        if "paths" not in block:
            return True
        if any(_matches_actions_glob(p, changed_path) for p in block["paths"]):
            return True
    return False


@pytest.mark.parametrize("workflow_name", GATE_WORKFLOWS)
def test_a_gate_is_woken_by_every_test_file_it_executes(workflow_name):
    """Editing a test file must start the gate that runs it. Otherwise the file
    is executed only incidentally, when some other path drags the workflow in,
    and a change confined to it is verified nowhere."""
    executed, _ = _workflow_execution_surface(workflow_name)
    assert executed, f"{workflow_name}: no pytest invocation found — parsing broke?"
    orphans = sorted(f for f in executed if not _trigger_wakes(workflow_name, f))
    assert not orphans, (
        f"{workflow_name} executes these test files but no trigger of its own wakes on "
        f"them, so a change confined to one runs it in no job: {orphans}"
    )


@pytest.mark.parametrize("workflow_name", GATE_WORKFLOWS)
def test_a_gate_is_woken_by_every_manifest_that_decides_what_it_runs(workflow_name):
    """A shard list is as load-bearing as the workflow file: moving a file
    between .github/flyer-shards/*.txt changes which job runs it, and dropping a
    line silently un-gates it. The edit has to wake the gate it reconfigures."""
    _, manifests = _workflow_execution_surface(workflow_name)
    orphans = sorted(m for m in manifests if not _trigger_wakes(workflow_name, m))
    assert not orphans, (
        f"{workflow_name} reads these files to decide which tests to run, but no trigger "
        f"of its own wakes on them — editing one changes the gate without running it: "
        f"{orphans}"
    )


def test_every_ci_pytest_invocation_is_parseable():
    """The fail-loud half. An unreadable construct must break this test rather
    than drop out of the two above, where an unparsed step reads as a step that
    executes nothing and therefore never has an orphan."""
    for workflow_name in GATE_WORKFLOWS:
        try:
            _workflow_execution_surface(workflow_name)
        except UnparseableCIConstruct as exc:
            pytest.fail(f"{workflow_name}: {exc}")


def test_the_execution_analyzer_resolves_the_indirect_constructs():
    """Guard the guard. Every one of these is load-bearing: if shard expansion
    silently returned nothing, flyer-core-ci would report zero executed files
    and pass the orphan test while gating nothing."""
    executed, manifests = _workflow_execution_surface("flyer-core-ci.yml")
    # Shard lists resolve to their contents...
    assert "tests/test_flyer_typeset_assembly.py" in executed, "render.txt shard did not expand"
    assert "tests/test_flyer_workflow.py" in executed, "lifecycle.txt shard did not expand"
    # ...and the `comm -23` complement subtracts every shard file, including the
    # owned-elsewhere list, so premium's files are NOT claimed here.
    assert "tests/test_flyer_renderer.py" not in executed, "owned-elsewhere.txt was not subtracted"
    assert {"github/flyer-shards/render.txt".replace("github/", ".github/"),
            ".github/flyer-shards/owned-elsewhere.txt"} <= manifests
    # send-path-ci's `find ... ! -name 'test_flyer*'` exclusion is honored.
    send_path, _ = _workflow_execution_surface("send-path-ci.yml")
    assert "tests/test_repo_invariants.py" in send_path
    assert "tests/test_flyer_renderer.py" not in send_path
    # A bare `pytest` with no target collects the job's working directory.
    cockpit, _ = _workflow_execution_surface("cockpit-ci.yml")
    assert "web/backend/tests/test_auth.py" in cockpit


def test_an_unreadable_pytest_line_raises_instead_of_reporting_no_tests():
    """The silent-skip failure mode, asserted directly."""
    with pytest.raises(UnparseableCIConstruct):
        _analyze_step("python -m pytest $(uname -a)", REPO_ROOT)
    with pytest.raises(UnparseableCIConstruct):
        _analyze_step("./run-the-tests.sh --with pytest", REPO_ROOT)
    with pytest.raises(UnparseableCIConstruct):
        _analyze_step("python -m pytest $UNDEFINED_LIST", REPO_ROOT)


@pytest.mark.parametrize(
    "workflow_name,path",
    [
        ("flyer-premium-ci.yml", "tests/test_flyer_intake_fields.py"),
        ("flyer-premium-ci.yml", "tests/test_flyer_source_edit_sla_watchdog.py"),
        ("flyer-premium-ci.yml", "tests/test_flyer_tranche2_telemetry.py"),
        ("flyer-core-ci.yml", ".github/flyer-shards/render.txt"),
        ("flyer-core-ci.yml", ".github/flyer-shards/lifecycle.txt"),
        ("flyer-core-ci.yml", ".github/flyer-shards/owned-elsewhere.txt"),
    ],
)
def test_the_repaired_files_are_still_both_executed_and_gated(workflow_name, path):
    """The orphan tests above are satisfiable by deleting a file from BOTH the
    workflow's run steps and its paths — which removes the coverage instead of
    restoring it. These are the files that were actually orphaned; pin each one
    on both sides so that escape is closed for them by name.
    """
    executed, manifests = _workflow_execution_surface(workflow_name)
    assert path in (executed | manifests), f"{workflow_name} no longer uses {path}"
    assert _trigger_wakes(workflow_name, path), f"{workflow_name} no longer wakes on {path}"


# ── and every test must be run by some gate ────────────────────────────────
#
# The inverse direction of the rule above, and it does not follow from it: a
# gate can execute only files its triggers wake on and still leave a test file
# that no gate executes at all. Both are needed.
#
# `tests/e2e/test_catering_conversation_e2e.py` was in that state. send-path-ci
# and flyer-core-ci both enumerate with `find tests -maxdepth 1`, which cannot
# reach `tests/e2e/`; the only recursive `pytest tests/` in the repo belongs to
# hermes-drift-check, whose triggers are `schedule` + `workflow_dispatch`, so it
# gates no merge. It is the only test file under a `tests/<subdir>/` path today
# — but `maxdepth 1` is what makes the NEXT one invisible too, which is why this
# is an invariant and not a line added to a find command.
#
# Sweeping for the class turned up a second: the VPS-only operator acceptance
# gate under tools/. Both are exempt, and both exemptions are about the test
# rather than about convenience — neither CAN run on a runner, and forcing
# either into a gate would make things worse, not better. The e2e file would
# collect and SKIP on every PR, which is the coverage-advertised-but-absent
# failure `test_every_test_file_named_in_a_workflow_collects_at_least_one_test`
# already exists to catch; the policy file would ERROR at import and turn a gate
# permanently red. An exemption here is a written claim, and the two tests below
# check the claim is still true of the file rather than taking it on trust.

EXECUTION_EXEMPT = {
    # Env-gated on OPENROUTER_API_KEY and makes REAL, billed model calls against
    # a live provider. CI does not have the key and should not: running it on
    # every PR would spend money per push, and adding it to a gate WITHOUT the
    # key adds a cell that reports `1 skipped` forever. Its deterministic half
    # (cf-router dispatch, the catering scripts incl. --recompose-from-sent) is
    # gated — test_catering_recompose.py, test_create_catering_proposal_options.py
    # and test_catering_pra_reachability.py all ride send-path-ci's glob. What is
    # genuinely ungated is the LLM-in-the-loop conversation gate, which no CI
    # arrangement short of a funded API key can cover.
    "tests/e2e/test_catering_conversation_e2e.py":
        "makes real billed OpenRouter calls; skips unconditionally without OPENROUTER_API_KEY",
    # Imports the Hermes runtime from its INSTALLED location on a customer VPS
    # (/usr/local/lib/hermes-agent). On a runner that path does not exist, so the
    # module raises ModuleNotFoundError at import — a collection ERROR, not a
    # skip. It is an operator acceptance gate run on the box during a patch port,
    # and there is no runner configuration that makes it meaningful.
    "tools/hermes-patch-port-v0191/test_shift_agent_policy.py":
        "operator acceptance gate; imports the Hermes runtime installed on a VPS, absent on a runner",
}


def _tracked_test_files() -> list[str]:
    """Test files as the REPOSITORY defines them, not as the working tree does.

    `git ls-files` rather than a filesystem walk: a scratch file in somebody's
    worktree is not a coverage gap, and in a repo where several agents share
    checkouts a walk turns another lane's temp file into everyone's red test.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise UnparseableCIConstruct(f"`git ls-files` failed: {result.stderr.strip()}")
    return sorted(
        path for path in result.stdout.split("\0")
        if path.endswith(".py") and path.rsplit("/", 1)[-1].startswith("test_")
    )


def _executed_by_any_gate() -> set[str]:
    executed: set[str] = set()
    for workflow_name in GATE_WORKFLOWS:
        executed |= _workflow_execution_surface(workflow_name)[0]
    return executed


def test_every_test_file_is_executed_by_some_gate():
    """A test file no pull_request-or-push workflow runs is a test that cannot
    fail a merge. It still reads as coverage from the file listing."""
    ungated = sorted(set(_tracked_test_files()) - _executed_by_any_gate() - set(EXECUTION_EXEMPT))
    assert not ungated, (
        "these test files are executed by no merge gate, so a regression they would "
        f"catch merges green: {ungated}. Add them to the workflow that owns their "
        "subsystem, or add an EXECUTION_EXEMPT entry saying what about the TEST "
        "makes it unrunnable in CI."
    )


def test_every_execution_exemption_names_a_real_file():
    """An exemption for a deleted file is a stale claim that hides the next file
    to take its path."""
    for path in EXECUTION_EXEMPT:
        assert (REPO_ROOT / path).is_file(), f"exempt but missing: {path}"


def test_no_execution_exemption_is_stale():
    """Once a gate does run the file, the exemption is a lie about the repo —
    and worse, it would suppress the invariant if the gate later dropped it."""
    executed = _executed_by_any_gate()
    claimed = sorted(path for path in EXECUTION_EXEMPT if path in executed)
    assert not claimed, (
        f"these files are exempted from execution but a gate runs them: {claimed}. "
        "Delete the exemption — it now hides a real regression."
    )


def test_the_exempted_files_still_carry_the_property_they_are_exempted_for():
    """The reason is checked, not trusted. If the env gate is removed the e2e
    test becomes runnable and belongs in a workflow, not on this list; if the
    policy gate stops reaching for the on-box Hermes install, likewise."""
    e2e = (REPO_ROOT / "tests/e2e/test_catering_conversation_e2e.py").read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" in e2e and "skipif" in e2e, (
        "the catering E2E no longer skips on a missing OPENROUTER_API_KEY — if it can "
        "run without billed model calls it should be gated, not exempted."
    )
    policy = (REPO_ROOT / "tools/hermes-patch-port-v0191/test_shift_agent_policy.py").read_text(
        encoding="utf-8"
    )
    assert "/usr/local/lib/hermes-agent" in policy, (
        "the policy acceptance gate no longer imports the on-box Hermes install — if it "
        "can import on a runner it should be gated, not exempted."
    )


# ── a gate that reads PR content must be woken when it changes ─────────────
#
# The event-type axis of the same family as the two rules above. #724 fixed
# pull_request-vs-push PATH parity; #726 fixed executed-vs-woken and per-commit
# identity; this is what happens when the declared ACTIVITY TYPES do not match
# what a workflow actually reads.
#
# `on: pull_request:` with no `types:` means `[opened, synchronize, reopened]`.
# `edited` is absent from that default, so editing a PR body does not re-run the
# workflow — and architecture-governance exists to read the PR body. Its green
# tick therefore attested to the body BEFORE the edit, not the one being merged.
#
# Three merged PRs had their bodies edited pre-merge and all three still pass
# the checker today, so nothing shipped broken. The reason is luck: each edit
# happened to be followed by a push (`synchronize`) or a close+reopen
# (`reopened`), which re-ran the gate as a side effect. A rule nobody knew was
# load-bearing was carrying the guarantee.
#
# The read set is DERIVED by scanning workflow text, never listed. A hardcoded
# list is how the last sweep of this kind missed a file.

# What a workflow can read -> the activity types that must therefore trigger it.
# Keyed on the reads that a human EDIT can change; `.number`, `.head.sha` and
# friends are deliberately absent because no activity type alters them.
#
# Labels map to `labeled`/`unlabeled`, NOT `edited` — a real distinction, and
# getting it wrong would add a trigger that never fires for the thing it was
# added for. Nothing reads labels today; the row exists so the first workflow
# that does gets the right answer instead of the plausible one.
PR_CONTENT_READS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("PR body", r"github\.event\.pull_request\.body\b", frozenset({"edited"})),
    ("PR title", r"github\.event\.pull_request\.title\b", frozenset({"edited"})),
    # A base-branch change fires `edited` too, and a gate that diffs against the
    # base is measuring something different afterwards.
    ("PR base", r"github\.event\.pull_request\.base\b", frozenset({"edited"})),
    # The captured body, consumed downstream by a step or a checker flag.
    ("captured PR body", r"pr-body\.md|--pr-body\b", frozenset({"edited"})),
    ("PR labels", r"github\.event\.pull_request\.labels\b",
     frozenset({"labeled", "unlabeled"})),
)

# GitHub's default when `types:` is omitted.
_DEFAULT_PR_TYPES = frozenset({"opened", "synchronize", "reopened"})

# Workflows exempted from the rule, each with a reason. As with PARITY_EXEMPT,
# an exemption is a claim someone has to write down.
PR_CONTENT_TRIGGER_EXEMPT: dict[str, str] = {}


def _workflow_text_without_comments(workflow_name: str) -> str:
    """Workflow source with YAML comments removed.

    A path named in a comment is documentation, not a read — architecture-
    governance's own rationale block discusses `pull_request.base.sha` in prose,
    and an explanation of why something is NOT read must not register as
    reading it.
    """
    kept = []
    for line in (REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(
        encoding="utf-8"
    ).splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(line.split(" #", 1)[0])
    return "\n".join(kept)


def _pull_request_types(workflow_name: str) -> frozenset[str]:
    """Activity types that actually trigger this workflow's pull_request event."""
    block = _workflow_data(workflow_name)["on"].get("pull_request") or {}
    declared = block.get("types")
    if not declared:
        return _DEFAULT_PR_TYPES
    return frozenset(declared)


def _pr_content_reads(workflow_name: str) -> list[tuple[str, frozenset[str]]]:
    """(what it reads, types it must be triggered by) for one workflow."""
    text = _workflow_text_without_comments(workflow_name)
    return [
        (label, required)
        for label, pattern, required in PR_CONTENT_READS
        if re.search(pattern, text)
    ]


def _pr_content_readers() -> list[str]:
    """Every pull_request-triggered workflow that reads editable PR content."""
    return [
        name for name in GATE_WORKFLOWS
        if "pull_request" in _workflow_data(name)["on"]
        and name not in PR_CONTENT_TRIGGER_EXEMPT
        and _pr_content_reads(name)
    ]


PR_CONTENT_READERS = _pr_content_readers()


@pytest.mark.parametrize("workflow_name", PR_CONTENT_READERS)
def test_a_workflow_that_reads_pr_content_is_triggered_when_it_changes(workflow_name):
    """Otherwise its green tick attests to content that is no longer there."""
    types = _pull_request_types(workflow_name)
    missing = []
    for label, required in _pr_content_reads(workflow_name):
        if not (required & types):
            missing.append(f"reads {label} but is not triggered by {sorted(required)}")
    assert not missing, (
        f"{workflow_name} declares pull_request types {sorted(types)}: "
        + "; ".join(missing)
        + ". A change to content the workflow reads must re-run it, or the last "
        "green result describes a version of the PR that no longer exists."
    )


def test_the_pr_content_reader_sweep_is_derived_not_hardcoded():
    """A second gate that grows a PR-body read inherits the rule that day."""
    assert "architecture-governance.yml" in PR_CONTENT_READERS, (
        "the gate whose entire job is reading the PR body must be in the swept set"
    )
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        if path.name in PR_CONTENT_TRIGGER_EXEMPT:
            continue
        triggers = _workflow_data(path.name)["on"]
        if not (isinstance(triggers, dict) and "pull_request" in triggers):
            continue
        if _pr_content_reads(path.name):
            assert path.name in PR_CONTENT_READERS, (
                f"{path.name} reads PR content but escaped the sweep"
            )


def test_the_governance_gate_both_reads_the_body_and_wakes_on_an_edit():
    """Named explicitly, because the rule above is satisfiable by DELETING the
    read instead of adding the trigger — which would remove the Capability
    Reuse Map check rather than fix its staleness. Both halves are pinned."""
    reads = dict(_pr_content_reads("architecture-governance.yml"))
    assert "captured PR body" in reads, (
        "architecture-governance no longer captures the PR body — the Capability "
        "Reuse Map check reads it; removing the read is not a way to satisfy the "
        "trigger rule"
    )
    assert "PR body" in reads, "architecture-governance no longer reads pull_request.body"
    assert "edited" in _pull_request_types("architecture-governance.yml")


def test_the_pr_content_detector_reads_code_and_not_prose():
    """Guard the guard. If the detector matched nothing the parametrized test
    would collect zero cases and pass vacuously; if it matched comments, an
    explanation of why something is not read would demand a trigger for it."""
    assert re.search(PR_CONTENT_READS[0][1], "PR_BODY: ${{ github.event.pull_request.body }}")
    assert not re.search(PR_CONTENT_READS[0][1], "github.event.pull_request.number")
    # Labels are NOT `edited` — the whole point of keeping them a separate row.
    labels_row = next(r for r in PR_CONTENT_READS if r[0] == "PR labels")
    assert labels_row[2] == frozenset({"labeled", "unlabeled"})
    assert "edited" not in labels_row[2]
    # Comments are prose: the governance file discusses base.sha in its rationale.
    text = _workflow_text_without_comments("architecture-governance.yml")
    assert "# pull_request.base.sha would therefore" not in text
    assert "github.event.pull_request.body" in text


def test_an_omitted_types_key_is_read_as_githubs_default_not_as_everything():
    """The bug in one line: no `types:` does not mean "all types". If this
    helper returned an empty set or a wildcard, every check above would pass on
    exactly the configuration that caused the problem."""
    assert _DEFAULT_PR_TYPES == frozenset({"opened", "synchronize", "reopened"})
    assert "edited" not in _DEFAULT_PR_TYPES


def test_every_pr_content_trigger_exemption_names_a_real_workflow():
    for name in PR_CONTENT_TRIGGER_EXEMPT:
        assert (REPO_ROOT / ".github" / "workflows" / name).is_file(), (
            f"exempt but missing: {name}"
        )
