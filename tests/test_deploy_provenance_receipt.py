"""Invariants for the deploy provenance receipt.

`/opt/shift-agent/DEPLOY_RECEIPT.json` was, until this change, a file no code
wrote. It was hand-authored during a flyer deploy on 2026-06-05 and then simply
sat there. Verified on main-vps 2026-08-23: it still read commit `3de2663` /
`2026-06-05T18:27:53Z` while `/opt/shift-agent/.commit-hash` read
`5a216767ea62591e5be89399549d5cb09d9d6251`, written the same morning — 78 days
of drift, with nothing in the file marking it wrong. An auditor reaching for the
artifact whose name is literally DEPLOY_RECEIPT got a confidently wrong answer.

The repair is not "remember to update it". It is to make the receipt a
derivative of the provenance label the deploy already lays down, written in the
same `install_artifacts` if/else so the two cannot disagree:

  * artifact HAS `.commit-hash`  -> both are written, from the same value
  * artifact LACKS `.commit-hash` (rollback to a pre-label release) -> both are
    removed, so a rollback cannot leave the newer receipt behind claiming a
    commit that is no longer installed

That coupling is the whole property. These tests assert it structurally, on the
one shared chokepoint every deploy path routes through, because a receipt that
is only *usually* rewritten is exactly the failure this replaces.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT = REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"

RECEIPT_PATH = "/opt/shift-agent/DEPLOY_RECEIPT.json"
COMMIT_LABEL = "/opt/shift-agent/.commit-hash"


def _script() -> str:
    return DEPLOY_SCRIPT.read_text(encoding="utf-8")


def _provenance_block() -> tuple[str, str]:
    """Return (then_arm, else_arm) of the `.commit-hash` guard in
    install_artifacts. Anchored on the guard condition rather than a line
    number so ordinary edits above it do not break the test."""
    script = _script()
    start = script.find('if [ -f "$src_root/.commit-hash" ]; then')
    assert start != -1, (
        "the `.commit-hash` provenance guard is gone from "
        f"{DEPLOY_SCRIPT.name}. The receipt is written inside it; if the guard "
        "moved, move the receipt with it and re-anchor this test."
    )
    end = script.find("\n    fi\n", start)
    assert end != -1, "could not find the closing `fi` of the provenance guard"
    block = script[start:end]

    split = re.search(r"^\s*else\s*$", block, re.MULTILINE)
    assert split, "the provenance guard has no `else` arm — rollback hygiene lost"
    return block[: split.start()], block[split.end() :]


def test_install_artifacts_is_still_the_shared_chokepoint():
    """Anchors the rest: the value of writing here is that forward deploy,
    budget-bootstrap and rollback all route through this one function."""
    script = _script()
    assert "install_artifacts() {" in script
    assert script.count("install_artifacts() {") == 1, (
        "more than one install_artifacts definition — the 'single chokepoint' "
        "premise these invariants rest on no longer holds."
    )


def test_the_deploy_writes_the_receipt_itself():
    """No writer at all was the original bug. Assert one exists."""
    assert RECEIPT_PATH in _script(), (
        f"{DEPLOY_SCRIPT.name} never writes {RECEIPT_PATH}. A receipt nothing "
        "regenerates goes stale the day after it is authored — that is exactly "
        "the 78-day drift this replaces."
    )


def test_the_receipt_is_written_in_the_same_arm_that_installs_the_commit_label():
    """Coupling, not proximity. Written from the same guard on the same value,
    the receipt cannot report a different commit than `.commit-hash`."""
    then_arm, _ = _provenance_block()
    assert COMMIT_LABEL in then_arm, "sanity: the then-arm should install the label"
    assert RECEIPT_PATH in then_arm, (
        f"{RECEIPT_PATH} is not written inside the `.commit-hash` then-arm of "
        "install_artifacts. Written anywhere else it can be skipped by a path "
        "that still installs the label, and the two drift apart again."
    )


def test_a_rollback_that_drops_the_label_also_drops_the_receipt():
    """The stale-receipt failure mode, reintroduced: roll back to a release
    predating the label and the newer receipt survives, now describing a commit
    that is not installed. The else-arm must remove it."""
    _, else_arm = _provenance_block()
    assert f"rm -f {COMMIT_LABEL}" in else_arm, (
        "sanity: the else-arm should remove the stale label"
    )
    assert f"rm -f {RECEIPT_PATH}" in else_arm, (
        f"a rollback to a pre-label tarball removes {COMMIT_LABEL} but leaves "
        f"{RECEIPT_PATH} behind, still naming the commit that was rolled back. "
        "Absent provenance is honest; wrong provenance is not."
    )


def test_the_receipt_commit_is_read_from_the_artifact_not_hardcoded():
    """A literal SHA in the writer is the hand-authored receipt again, wearing
    the costume of automation."""
    then_arm, _ = _provenance_block()
    assert '"$src_root/.commit-hash"' in then_arm, (
        "the receipt must be derived from the staged artifact's own "
        "`.commit-hash`, not from anything the operator types."
    )
    hardcoded = re.findall(r"\b[0-9a-f]{7,40}\b", then_arm)
    assert not hardcoded, (
        f"literal commit-shaped values in the provenance arm: {hardcoded}. The "
        "receipt must carry the artifact's commit, never a baked-in one."
    )


def test_the_receipt_says_what_generated_it():
    """A machine-written file that does not say so invites the next operator to
    hand-edit it, which is how the last one got there."""
    then_arm, _ = _provenance_block()
    assert "generated_by" in then_arm, (
        "the receipt should carry a `generated_by` field naming the deploy "
        "script, so a reader knows it is regenerated and not hand-maintained."
    )


# ─────────────────────────────────────────────────────────────────
# Behavioural: the assertions above prove the receipt is WRITTEN. They cannot
# prove it is READABLE. A stray quote or an unescaped `$` in the heredoc yields
# a file that exists, has a plausible size, and fails json.load — which is the
# stale-receipt defect wearing a different mask. Execute the block.
# ─────────────────────────────────────────────────────────────────

requires_bash = pytest.mark.skipif(
    shutil.which("bash") is None or os.name == "nt",
    reason="executes a bash fragment; runs in CI (ubuntu-latest) and in the "
    "python:3.11-slim container, SKIPS on the Windows dev host — a green run "
    "there is not evidence for this test",
)


def _run_provenance_block(tmp_path: Path, commit: str | None) -> tuple[Path, Path]:
    """Execute the real provenance arm against a sandboxed root.

    The block is lifted verbatim from the deploy script — not retyped — so this
    exercises the shipped text. Only the install root is redirected.
    """
    script_text = _script()
    start = script_text.find('if [ -f "$src_root/.commit-hash" ]; then')
    end = script_text.find("\n    fi\n", start)
    block = script_text[start:end] + "\n    fi\n"

    live_root = tmp_path / "opt"
    src_root = tmp_path / "artifact"
    live_root.mkdir()
    src_root.mkdir()
    if commit is not None:
        (src_root / ".commit-hash").write_text(commit + "\n", encoding="utf-8")

    block = block.replace("/opt/shift-agent", str(live_root))
    program = f'set -euo pipefail\nsrc_root="{src_root}"\n{block}\n'
    result = subprocess.run(
        ["bash", "-c", program], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, (
        f"the provenance block exited {result.returncode}:\n{result.stderr}"
    )
    return live_root, src_root


@requires_bash
def test_the_written_receipt_is_valid_json_carrying_the_artifact_commit(tmp_path):
    commit = "5a216767ea62591e5be89399549d5cb09d9d6251"
    live_root, _ = _run_provenance_block(tmp_path, commit)

    receipt = live_root / "DEPLOY_RECEIPT.json"
    assert receipt.exists(), "the then-arm did not produce a receipt"

    data = json.loads(receipt.read_text(encoding="utf-8"))  # raises on malformed
    assert data["commit"] == commit, (
        f"receipt reports {data['commit']!r}, artifact carried {commit!r} — the "
        "exact disagreement this file exists to prevent."
    )
    assert (live_root / ".commit-hash").read_text(encoding="utf-8").strip() == commit
    assert data["generated_by"], "receipt does not say what generated it"


@requires_bash
def test_the_receipt_leaves_no_temp_file_behind(tmp_path):
    """The write goes through a dot-prefixed temp + rename. A crash-free run
    must not leave the temp visible next to the real receipt, where the next
    reader could pick it up."""
    live_root, _ = _run_provenance_block(tmp_path, "a" * 40)
    leftovers = sorted(
        p.name for p in live_root.iterdir() if p.name.endswith(".tmp")
    )
    assert not leftovers, f"temp files left in the live tree: {leftovers}"


@requires_bash
def test_a_rollback_to_a_pre_label_artifact_erases_both(tmp_path):
    """End-to-end version of the coupling: deploy a labelled release, then
    re-run install against an artifact that predates the label. Both provenance
    files must be gone — not one left describing the release that was undone."""
    live_root, src_root = _run_provenance_block(tmp_path, "b" * 40)
    assert (live_root / "DEPLOY_RECEIPT.json").exists()

    # Re-run against the SAME live root — proving removal, not a fresh directory
    # that never had a receipt in it.
    (src_root / ".commit-hash").unlink()
    script_text = _script()
    start = script_text.find('if [ -f "$src_root/.commit-hash" ]; then')
    end = script_text.find("\n    fi\n", start)
    block = (script_text[start:end] + "\n    fi\n").replace(
        "/opt/shift-agent", str(live_root)
    )
    subprocess.run(
        ["bash", "-c", f'set -euo pipefail\nsrc_root="{src_root}"\n{block}\n'],
        check=True, capture_output=True, text=True, timeout=60,
    )
    assert not (live_root / "DEPLOY_RECEIPT.json").exists(), (
        "rollback to a pre-label artifact left the newer receipt in place, "
        "still naming a commit that is no longer installed."
    )
    assert not (live_root / ".commit-hash").exists()


def test_nothing_points_an_operator_at_the_receipt_as_the_sole_source():
    """`flyer-deploy-smoke` used to say 'See DEPLOY_RECEIPT.json' full stop.
    Whatever cites the receipt must also cite the label it derives from, so a
    reader can cross-check rather than trust."""
    offenders: list[str] = []
    for root in (REPO_ROOT / "docs", REPO_ROOT / "src"):
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path == DEPLOY_SCRIPT:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if RECEIPT_PATH not in content:
                continue
            if COMMIT_LABEL in content:
                continue
            offenders.append(path.relative_to(REPO_ROOT).as_posix())

    assert not offenders, (
        f"these files cite {RECEIPT_PATH} without also naming {COMMIT_LABEL}: "
        f"{offenders}. Cite both so a reader can cross-check the receipt against "
        "the label it is derived from."
    )
