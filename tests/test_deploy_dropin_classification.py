"""Invariants for how the deploy classifies a differing systemd drop-in.

`install_tracked_dropins` refuses to overwrite a drop-in whose box copy differs,
because it cannot tell a deliberate operator edit from drift. That guard is
correct and stays.

What was wrong is that it could not distinguish a CONTENT difference from a
LINE-ENDING difference, so `hermes-gateway.service.d/20-drain-timeout.conf` —
32 bytes CRLF on main-vps against the repo's 30 bytes LF, authored 2026-05-23 —
printed the same "may be a deliberate edit; resolve by hand" warning on every
single deploy for three months. Nobody can resolve it by hand from that message,
and nobody did.

The damage is not the noise. A warning that always fires trains an operator to
skim the whole channel, so a genuine drop-in drift would arrive somewhere
everyone has learned to ignore. Verified cosmetic, not functional: systemd
tolerates CRLF, and `systemctl show hermes-gateway -p TimeoutStopUSec` reports
`4min` on the box, so the 240s drain IS in effect.

Two properties are pinned here, and they are different questions:

  * the repo's own tracked drop-ins are internally well-formed (LF, no CR), so
    the repo can never be the source of this class of difference; and
  * the script classifies the CRLF-only case as its own class, keeps it out of
    the WARN count that means "a human must look", and still refuses to
    overwrite a real content difference.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT = REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
DROPIN_ROOT = REPO_ROOT / "src" / "platform" / "systemd"

requires_bash = pytest.mark.skipif(
    shutil.which("bash") is None or os.name == "nt",
    reason="executes a bash fragment; runs in CI (ubuntu-latest) and in the "
    "python:3.11-slim container, SKIPS on the Windows dev host — a green run "
    "there is not evidence for this test",
)


def _tracked_dropins() -> list[Path]:
    return sorted(DROPIN_ROOT.glob("*.service.d/*.conf"))


def test_there_are_tracked_dropins_to_check():
    """Guard against this whole file passing vacuously if the drop-in layout
    moves and the glob silently matches nothing."""
    found = _tracked_dropins()
    assert found, (
        f"no tracked drop-ins under {DROPIN_ROOT}/*.service.d/. Either the "
        "layout moved (re-anchor this file) or tracking was removed — in which "
        "case the deploy no longer installs the policy-preflight wiring, which "
        "is the failure install_tracked_dropins was written to prevent."
    )


@pytest.mark.parametrize("dropin", _tracked_dropins(), ids=lambda p: p.name)
def test_a_tracked_dropin_is_stored_with_unix_line_endings(dropin):
    """LF is the correct form for a Linux systemd unit and the repo holds the
    canonical copy. A CR committed here would make the box copy the RIGHT one
    and this classification permanently ambiguous."""
    raw = dropin.read_bytes()
    assert b"\r" not in raw, (
        f"{dropin.relative_to(REPO_ROOT).as_posix()} contains CR bytes. The repo "
        "copy is canonical and must be LF — do not normalise toward the box."
    )
    assert raw.endswith(b"\n"), (
        f"{dropin.relative_to(REPO_ROOT).as_posix()} has no trailing newline."
    )


def test_the_comment_no_longer_claims_a_crlf_difference_is_indistinguishable():
    """The script used to state that a CRLF-only difference 'still counts'. It
    no longer behaves that way. A stated rule sitting next to code that does
    something else is how the next reader learns the wrong model."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "still counts" not in script, (
        "the deploy script still says a CRLF-only difference 'still counts', "
        "but it is now classified separately. Update the comment with the code."
    )
    assert "SHIFT_AGENT_NORMALIZE_DROPIN_EOL" in script, (
        "the opt-in normalisation switch is gone; the EOL-only message promises "
        "an escape hatch that must actually exist."
    )


def test_normalisation_is_opt_in_and_defaults_to_off():
    """Never automatic. The default must be the non-mutating branch."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert '"${SHIFT_AGENT_NORMALIZE_DROPIN_EOL:-0}" = "1"' in script, (
        "normalisation must be gated on an explicit opt-in defaulting to off."
    )


# ─────────────────────────────────────────────────────────────────
# Behavioural. The assertions above read the script; these run it, because the
# classification is a branch and only execution proves which arm a given pair
# of files lands in.
# ─────────────────────────────────────────────────────────────────


def _run_dropin_install(tmp_path: Path, box_bytes: bytes | None, *, normalize=False):
    """Execute the real install_tracked_dropins body against a sandbox.

    Lifted verbatim from the deploy script so this exercises shipped text.
    """
    script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script_text.find("    install_tracked_dropins() {")
    assert start != -1, "install_tracked_dropins definition not found"
    end = script_text.find("\n    }\n", start)
    assert end != -1, "could not find the end of install_tracked_dropins"
    func = script_text[start : end + len("\n    }\n")]

    src = tmp_path / "src" / "platform" / "systemd" / "hermes-gateway.service.d"
    src.mkdir(parents=True)
    (src / "20-drain-timeout.conf").write_bytes(b"[Service]\nTimeoutStopSec=240s\n")

    etc = tmp_path / "etc"
    if box_bytes is not None:
        box_dir = etc / "hermes-gateway.service.d"
        box_dir.mkdir(parents=True)
        (box_dir / "20-drain-timeout.conf").write_bytes(box_bytes)

    func = func.replace("/etc/systemd/system", str(etc))
    program = (
        "set -uo pipefail\n"
        f'cd "{tmp_path}"\n'
        f"{func}\n"
        f'SHIFT_AGENT_NORMALIZE_DROPIN_EOL={"1" if normalize else "0"} '
        "install_tracked_dropins\n"
    )
    result = subprocess.run(
        ["bash", "-c", program], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, f"exited {result.returncode}: {result.stderr}"
    return result, etc / "hermes-gateway.service.d" / "20-drain-timeout.conf"


@requires_bash
def test_a_crlf_only_box_copy_is_classified_as_line_endings_not_as_drift(tmp_path):
    """The exact main-vps case: 32 bytes CRLF against 30 bytes LF."""
    out, target = _run_dropin_install(
        tmp_path, b"[Service]\r\nTimeoutStopSec=240s\r\n"
    )
    combined = out.stdout + out.stderr

    assert "EOL-ONLY" in combined, (
        f"CRLF-only difference was not classified as such:\n{combined}"
    )
    assert "DIFFERS" not in combined, (
        f"CRLF-only difference still reported as content drift:\n{combined}"
    )
    # The load-bearing half: it must not reach the channel that means "a human
    # must look", or the operator is trained to skim that channel exactly as
    # before.
    assert "WARN:" not in out.stderr, (
        f"a cosmetic difference still raises WARN:\n{out.stderr}"
    )
    assert target.read_bytes() == b"[Service]\r\nTimeoutStopSec=240s\r\n", (
        "the box copy was modified without the opt-in"
    )


@requires_bash
def test_a_real_content_difference_still_warns_and_is_still_not_overwritten(tmp_path):
    """The guard this change must not weaken."""
    box = b"[Service]\nTimeoutStopSec=999s\n"
    out, target = _run_dropin_install(tmp_path, box)
    combined = out.stdout + out.stderr

    assert "DIFFERS" in combined, f"real drift not reported:\n{combined}"
    assert "EOL-ONLY" not in combined, (
        f"a content difference was misclassified as line endings — this is the "
        f"dangerous direction of the classification:\n{combined}"
    )
    assert "WARN:" in out.stderr, f"real drift did not reach WARN:\n{out.stderr}"
    assert target.read_bytes() == box, "a differing box copy was overwritten"


@requires_bash
def test_a_content_difference_that_also_changes_line_endings_is_still_drift(tmp_path):
    """The misclassification that would matter: CR bytes present AND content
    changed. Stripping CR must not launder it into the cosmetic bucket."""
    out, target = _run_dropin_install(
        tmp_path, b"[Service]\r\nTimeoutStopSec=999s\r\n"
    )
    combined = out.stdout + out.stderr
    assert "DIFFERS" in combined and "EOL-ONLY" not in combined, (
        f"a CRLF file with changed content was treated as line-endings-only:\n{combined}"
    )
    assert target.read_bytes() == b"[Service]\r\nTimeoutStopSec=999s\r\n"


@requires_bash
def test_the_opt_in_normalises_only_the_line_endings(tmp_path):
    out, target = _run_dropin_install(
        tmp_path, b"[Service]\r\nTimeoutStopSec=240s\r\n", normalize=True
    )
    assert "NORMALIZED" in out.stdout + out.stderr
    assert target.read_bytes() == b"[Service]\nTimeoutStopSec=240s\n", (
        "normalisation did not produce the repo's canonical LF copy"
    )


@requires_bash
def test_the_opt_in_does_not_touch_a_real_content_difference(tmp_path):
    """Opt-in normalisation must be safe by construction: it may only act where
    the content is already proven identical. Turning it on must never become a
    way to clobber a deliberate operator edit."""
    box = b"[Service]\r\nTimeoutStopSec=999s\r\n"
    out, target = _run_dropin_install(tmp_path, box, normalize=True)
    assert target.read_bytes() == box, (
        "the opt-in overwrote a box copy whose CONTENT differs — it must only "
        "ever normalise line endings."
    )
    assert "WARN:" in out.stderr


@requires_bash
def test_an_absent_dropin_is_still_installed(tmp_path):
    """Rule 1 regression check: the additive path must keep working."""
    out, target = _run_dropin_install(tmp_path, None)
    assert "INSTALLED" in out.stdout + out.stderr
    assert target.read_bytes() == b"[Service]\nTimeoutStopSec=240s\n"
