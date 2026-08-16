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
