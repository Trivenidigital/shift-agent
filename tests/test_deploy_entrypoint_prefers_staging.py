"""A bare deploy invocation must run the STAGING copy, via `bash`.

`/usr/local/bin/shift-agent-deploy.sh` is written BY a deploy, so it is always
the PREVIOUS release's logic. When a tarball changes `shift-agent-deploy.sh`
itself, running the installed copy deploys the new tree with the code it was
meant to replace - the trap behind the 2026-08-14 failed-safe rollback.

Two things this file learned from review, both the hard way:

1. `bash "$S"`, never `[ -x "$S" ]`. The deploy script is tracked mode 100644.
   A tarball built on Linux carries no x-bit, so an `[ -x ]` probe silently
   selects the installed copy - the exact fallback the rule exists to prevent -
   and still exits 0. Only `install_artifacts` chmods it 755, and only at the
   destination. The first version looked correct purely because MSYS on the
   Windows dev box synthesises an x-bit from the shebang, so the bug was
   invisible from the machine that wrote it. `tasks/DEPLOY_CHECKLIST.md` and
   the deploy script's own `bash "$CLOSURE_CHECK"` were already mode-safe.

2. Assert on the LIVE INVOCATION, not on string presence. The first version
   scanned whole files for the staging path and for fallback-ish words near any
   installed-path mention. Two mutations passed it: demoting the staging path to
   a comment while restoring the bare installed invocation, and replacing the
   justification with unrelated prose containing the word "rollback" - a word
   that appears throughout deploy tooling. It pinned documentation, not
   behaviour.

`list` and `rollback <tag>` are deliberately out of scope: they administer an
existing install, and using the installed copy for them is correct.
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLED = "/usr/local/bin/shift-agent-deploy.sh"
STAGING = "/opt/shift-agent/staging-new/src/agents/shift/scripts/shift-agent-deploy.sh"

# Derived, not hardcoded. A hardcoded tuple is how docs/deploy.md - the
# highest-traffic stale hint in the repo - stayed out of scope the first time.
SEARCH_ROOTS = ("tools", "docs", "tasks", ".github")

# Historical records, deliberately exempt. These describe what someone DID,
# often the very incident that motivated this rule; rewriting them to say the
# right thing would falsify the record. The test governs INSTRUCTIONS, not
# history. Anything not matched here is in scope, so a new operational doc is
# covered the day it lands.
HISTORICAL_PREFIXES = ("tasks/audits/", "docs/reviews/", "docs/superpowers/")

# Whole hyphen-delimited TOKENS in the basename stem, not substrings anywhere. An earlier version used unanchored substrings
# (`-plan`, `-report`) matched anywhere in the path, which would silently exempt
# a future `docs/runbooks/deploy-planning.md` or `tasks/incident-reporting-
# runbook.md` the day it landed. It also carried a marker named after one
# specific file — an allowlist entry wearing a rule's clothes, and it let a LIVE
# operational procedure (`tasks/runbook-state-migration.md`, a current
# instruction to run a bare deploy) escape. An exemption must be a LOCATION or a
# naming convention, never a spelling. Token matching keeps
# `deploy-planning.md` and `incident-reporting-runbook.md` in scope while
# exempting `...-execution-plan-2026-05-19.md`.
HISTORICAL_BASENAME_TOKENS = frozenset({"plan", "report"})


def _is_historical(rel: str) -> bool:
    stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    tokens = set(stem.split("-"))
    return (rel.startswith(HISTORICAL_PREFIXES)
            or bool(tokens & HISTORICAL_BASENAME_TOKENS))

SUBCOMMAND = re.compile(r"shift-agent-deploy\.sh[`'\"]?\s+(list|rollback)\b")


def _candidate_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", *SEARCH_ROOTS],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    hits = []
    for rel in listed:
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if INSTALLED in text and not _is_historical(rel):
            hits.append(path)
    return hits


def _bare_invocation_lines(path: Path) -> list[tuple[int, str]]:
    """Lines that INVOKE the installed entrypoint with no subcommand."""
    bad = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if INSTALLED not in line:
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if SUBCOMMAND.search(line):
            continue
        if re.search(r"\|\|\s*S=", line) or stripped.startswith("S="):
            continue
        bad.append((n, line.strip()))
    return bad


def test_no_tracked_file_invokes_the_installed_entrypoint_bare():
    offenders = []
    for path in _candidate_files():
        for n, line in _bare_invocation_lines(path):
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{n}: {line}")
    assert not offenders, (
        "these invoke the PREVIOUS release's deploy logic on a new tree:\n  "
        + "\n  ".join(offenders)
    )


def test_the_deploy_script_is_executable_in_git():
    """It MUST be 100755, and the reason is the auto-rollback path.

    The script re-execs ITSELF by path on every gate-failure route:
    `"$0" rollback "$PREV_TAG"` at shift-agent-deploy.sh:1701 (reached from
    `revert_shift_tree`, referenced 25 times), :2891 and :3157. If the tracked
    mode is 644, a Linux-built tarball yields a non-executable staging copy, and
    invoking it as `bash "$S"` sets `$0` to that path — so the re-exec fails
    126 under `set -euo pipefail` and the rollback ABORTS. That happens only on
    a deploy that has already failed a gate, i.e. exactly when auto-rollback is
    the thing protecting the box, leaving artifacts installed and the tree
    un-reverted. Strictly worse than the stale-hint bug this file exists for.

    An earlier version of this test asserted 100644 and cited the `bash` prefix
    as the mitigation. It was pinning the wrong premise and would have blocked
    this fix. Recorded because "the test says so" is not evidence when the test
    encodes the mistake.
    """
    mode = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-s",
         "src/agents/shift/scripts/shift-agent-deploy.sh"],
        capture_output=True, text=True, check=True,
    ).stdout.split()[0]
    assert mode == "100755", (
        f"tracked mode is {mode}; the deploy script re-execs itself as `\"$0\" rollback`, "
        "so a non-executable staging copy breaks auto-rollback on a failed deploy"
    )


def test_fleet_script_probes_with_f_not_x():
    body = (REPO_ROOT / "tools/canary-bulk-deploy.sh").read_text(encoding="utf-8")
    assert '[ -f "$S" ]' in body, "must probe with -f; -x is false for a 644 tarball"
    assert '[ -x "$S" ]' not in body, "still probes with -x"
    assert 'bash "$S"' in body, "must invoke via bash, not execute directly"


def test_fleet_script_does_not_let_ssh_eat_the_host_list():
    """ssh inherits the loop's stdin, which IS the VPS list. Without -n the
    first host drains the rest and the loop exits 0 having deployed one VPS."""
    body = (REPO_ROOT / "tools/canary-bulk-deploy.sh").read_text(encoding="utf-8")
    for n, line in enumerate(body.splitlines(), 1):
        s = line.strip()
        if not s.startswith("ssh ") or s.startswith("#"):
            continue
        assert " -n " in line or "</dev/null" in line, (
            f"canary-bulk-deploy.sh:{n} runs ssh inside the host-list loop without "
            f"-n; it consumes the remaining hosts:\n  {s}"
        )


# POSIX-only: on Windows `bash` resolves to WSL, which cannot execute the
# Windows tmp paths this builds. The assertion is about real deploy hosts,
# which are Linux, and CI runs there.
linux_only = pytest.mark.skipif(os.name != "posix", reason="POSIX-only: needs a native bash")


@linux_only
def test_the_fleet_payload_actually_selects_staging_for_a_mode_644_copy(tmp_path):
    """Execute the real payload against a 644 staging file - the shape a
    Linux-built tarball produces. No string assertion can catch this."""
    body = (REPO_ROOT / "tools/canary-bulk-deploy.sh").read_text(encoding="utf-8")
    m = re.search(r"ssh -n \"\$vps\" '(.*?)'\s", body, re.S)
    assert m, "could not extract the ssh payload - shape changed"
    payload = textwrap.dedent(m.group(1))

    staging = tmp_path / "staging" / "shift-agent-deploy.sh"
    staging.parent.mkdir(parents=True)
    staging.write_text("#!/usr/bin/env bash\necho RAN=STAGING\n", encoding="utf-8")
    staging.chmod(0o644)                       # exactly what git archive yields
    installed = tmp_path / "installed.sh"
    installed.write_text("#!/usr/bin/env bash\necho RAN=INSTALLED\n", encoding="utf-8")
    installed.chmod(0o755)
    workdir = tmp_path / "work"
    workdir.mkdir()

    def posix(p):
        return str(p).replace("\\", "/")

    script = (payload
              .replace(STAGING, posix(staging))
              .replace(INSTALLED, posix(installed))
              .replace("cd /opt/shift-agent", "cd " + posix(workdir)))

    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert "RAN=STAGING" in r.stdout, (
        "payload did not run the staging copy for a mode-644 file - this is the "
        f"silent-fallback regression.\nstdout={r.stdout!r} stderr={r.stderr!r}"
    )

    # Positive control: with staging absent it MUST fall back, not fail.
    staging.unlink()
    r2 = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert "RAN=INSTALLED" in r2.stdout, (
        f"fallback broken when staging is absent: {r2.stdout!r} {r2.stderr!r}"
    )


def _bare_staging_invocation_lines(path: Path) -> list[tuple[int, str]]:
    """Lines that INVOKE the staging entrypoint without `bash`.

    The other half of the rule, and the half a review found blind: the suite
    pinned "do not invoke the installed copy" but not "invoke the staging copy
    correctly", so stripping `bash` from every staging invocation passed.
    Directly exec'ing staging is a `Permission denied` at the moment of deploy
    whenever the tarball was built anywhere the x-bit is not synthesised.
    """
    bad = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if STAGING not in line:
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if SUBCOMMAND.search(line):
            continue
        before = line.split(STAGING)[0]
        if before.rstrip().endswith("bash") or 'bash "$S"' in line or "$S" in before:
            continue
        bad.append((n, line.strip()))
    return bad


def test_no_live_instruction_invokes_the_staging_entrypoint_without_bash():
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", *SEARCH_ROOTS],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    offenders = []
    for rel in listed:
        if _is_historical(rel):
            continue
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if STAGING not in text:
            continue
        for n, line in _bare_staging_invocation_lines(path):
            offenders.append(f"{rel}:{n}: {line}")
    assert not offenders, (
        "these direct-exec the staging copy; it is not guaranteed executable out "
        "of every tarball, so this is Permission denied at deploy time: "
        + "; ".join(offenders)
    )
