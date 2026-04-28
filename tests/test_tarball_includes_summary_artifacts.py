"""Tarball-manifest assertion: deploy tarball MUST include the new
weekly-routing-summary script + 3 systemd units.

Closes pr-test-analyzer's HIGH finding from design v2 review #16.

Linux-only (depends on tar + bash).
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="tarball builder is bash + uses GNU tar",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TARBALL_BUILDER = REPO_ROOT / "tools" / "build-deploy-tarball.sh"
TARBALL_OUTPUT = REPO_ROOT / "shift-agent-deploy.tgz"


def test_tarball_builder_present():
    """If the builder is missing or moved, fail loudly rather than skipping."""
    assert TARBALL_BUILDER.exists(), (
        f"build-deploy-tarball.sh missing at {TARBALL_BUILDER}; "
        "tarball-manifest test relies on it. If the builder moved, update this test."
    )


def test_tarball_includes_routing_summary_artifacts():
    """Build a tarball (skipping pytest to avoid recursion) and verify the
    new weekly-routing-summary artifacts are present in the manifest."""
    if shutil.which("tar") is None:
        pytest.skip("tar not on PATH")

    # Build with --skip-pytest to prevent recursion (this test runs from pytest)
    proc = subprocess.run(
        ["bash", str(TARBALL_BUILDER), "--skip-pytest"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"tarball builder failed (exit {proc.returncode}). "
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert TARBALL_OUTPUT.exists(), f"builder reported success but {TARBALL_OUTPUT} not found"

    try:
        listing = subprocess.check_output(
            ["tar", "tzf", str(TARBALL_OUTPUT)], text=True,
        )
        required = [
            "src/agents/shift/scripts/send-routing-accuracy-summary",
            "src/agents/shift/systemd/send-routing-accuracy-summary.timer",
            "src/agents/shift/systemd/send-routing-accuracy-summary.service",
            "src/agents/shift/systemd/send-routing-accuracy-summary-failure.service",
        ]
        for path in required:
            assert path in listing, (
                f"tarball missing required artifact: {path}\n"
                f"manifest:\n{listing}"
            )
    finally:
        TARBALL_OUTPUT.unlink(missing_ok=True)
