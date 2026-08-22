"""The deploy entrypoint an operator is told to run must come from STAGING.

`/usr/local/bin/shift-agent-deploy.sh` is written BY a deploy, so it is always
the PREVIOUS release's logic. When a tarball changes `shift-agent-deploy.sh`
itself, running the installed copy deploys the new tree with the old deploy
logic — the trap behind the 2026-08-14 failed-safe rollback.

`shift-agent-deploy.sh` already applies this rule to its own pre-restart gates
("prefer the staging source copy so the FIRST deploy that introduces the gate
still runs it; fall back to the installed copy only for rollback-tarball
compatibility"). The operator-facing entrypoints did not, in four places at
once — a printed hint, a source comment, a generated runbook line, and one
script that actually executed it. This pins the whole class rather than the
one instance that was reported.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLED = "/usr/local/bin/shift-agent-deploy.sh"
STAGING = "/opt/shift-agent/staging-new/src/agents/shift/scripts/shift-agent-deploy.sh"

# Files that tell a human, or a script, how to invoke the deploy entrypoint.
ENTRYPOINT_SOURCES = (
    "tools/build-deploy-tarball.sh",
    "tools/canary-bulk-deploy.sh",
    "tools/hermes-fleet-upgrade.py",
)


def _text(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", ENTRYPOINT_SOURCES)
def test_entrypoint_source_names_the_staging_path(rel):
    assert STAGING in _text(rel), (
        f"{rel} does not name the staging deploy entrypoint. An operator following "
        "it would run the previous release's deploy logic against the new tree."
    )


@pytest.mark.parametrize("rel", ENTRYPOINT_SOURCES)
def test_installed_path_is_never_named_without_the_staging_path(rel):
    """The installed path may appear ONLY as a documented fallback, and only in a
    file that has already named the staging path. A bare mention is the defect."""
    text = _text(rel)
    if INSTALLED not in text:
        return
    assert STAGING in text, (
        f"{rel} names {INSTALLED} but never {STAGING} — that is the bare stale hint"
    )
    for lineno, line in enumerate(text.splitlines(), 1):
        if INSTALLED not in line:
            continue
        context = "\n".join(text.splitlines()[max(0, lineno - 8):lineno + 2])
        assert re.search(r"fallback|previous release|rollback|\[ -x", context, re.I), (
            f"{rel}:{lineno} names the installed deploy script with no fallback/"
            f"previous-release framing nearby:\n  {line.strip()}"
        )


def test_the_deploy_script_still_documents_the_prefer_staging_rule():
    """Guard the justification, not just the strings. If the deploy script stops
    preferring staging for its own gates, the rule above needs re-deciding rather
    than silently continuing to be enforced on the operator entrypoints."""
    body = _text("src/agents/shift/scripts/shift-agent-deploy.sh")
    assert "Prefer the staging source copy" in body, (
        "shift-agent-deploy.sh no longer documents its prefer-staging rule; the "
        "operator-entrypoint invariant above was derived from it"
    )
