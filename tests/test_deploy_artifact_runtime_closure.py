"""ops/deploy-artifact-runtime-closure — the deploy must leave the flat
/opt/shift-agent runtime CLOSED over the approved artifact: every flat runtime
module byte-identical to a file the artifact ships, and every imported flat
dependency present + current. (PR-2; F-8 / DGT-21 / SP-GO-10, 2026-07-26 review.)

Incident (stale-flat-copy, discovered 2026-07-26): live `flyer_brief_validator.py`
does `from creative_firewall import ...`, but the installer wrote ONLY the
flyer_-prefixed `flyer_creative_firewall.py`. The bare `creative_firewall.py` the
code actually loads was never refreshed, so a stale 2026-06-05 copy (retaining a
class deleted by #605) survived across deploys — the deployed runtime was NOT
byte-identical to the approved release.

The fix has two parts, both covered here:
  * the installer now lays down the bare `creative_firewall.py` from the artifact on
    every deploy (refresh), and
  * a fail-closed post-install gate (`tools/verify-artifact-runtime-closure.sh`)
    re-derives the flat-module set FROM THE ARTIFACT and aborts+rolls back on any
    stale / orphan / missing / stale-import module.

Two layers (mirrors test_deploy_timer_state_preservation.py):
  * STATIC assertions on the real shift-agent-deploy.sh + helper text — run
    everywhere; prove the install-line fix, the rollback-safe gate wiring, and the
    gate's placement between install_artifacts and the gateway restart.
  * EXTRACT-AND-RUN of the standalone closure helper against a temp artifact +
    runtime tree that reproduces the incident — Linux-only (needs bash + sha256sum +
    coreutils `install`).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
HELPER = REPO / "tools" / "verify-artifact-runtime-closure.sh"
DEPLOY_TEXT = DEPLOY.read_text(encoding="utf-8")
DEPLOY_LINES = DEPLOY_TEXT.splitlines()

CF_SRC = "src/agents/flyer/creative_firewall.py"


def _first(pred) -> int:
    for i, ln in enumerate(DEPLOY_LINES, start=1):
        if pred(ln):
            return i
    raise AssertionError("no line matched")


# ════════════════════════════════════════════════════════════════════════════
# STATIC — installer refresh fix (run everywhere)
# ════════════════════════════════════════════════════════════════════════════
def test_helper_exists_and_is_referenced_by_the_installer():
    assert HELPER.is_file(), f"closure helper missing at {HELPER}"
    assert "tools/verify-artifact-runtime-closure.sh" in DEPLOY_TEXT, \
        "installer must invoke the closure helper"


def test_creative_firewall_installs_the_bare_imported_name():
    # The name live code imports (`from creative_firewall import ...`) MUST be
    # installed from the artifact — this is the direct F-8/DGT-21 refresh fix.
    assert f"install -m 644 {CF_SRC} /opt/shift-agent/creative_firewall.py" in DEPLOY_TEXT, \
        "installer must lay down the bare creative_firewall.py that live code imports"


def test_creative_firewall_still_installs_the_smoke_probe_name():
    # The flyer_-prefixed name is what the deploy smoke module-import probe loads;
    # keep it so the smoke gate is unchanged.
    assert f"install -m 644 {CF_SRC} /opt/shift-agent/flyer_creative_firewall.py" in DEPLOY_TEXT, \
        "installer must keep flyer_creative_firewall.py for the smoke-test probe"


def test_creative_firewall_rollback_removes_both_flat_names():
    # On rollback to a tarball that predates the source, BOTH flat copies are torn
    # down so no stale copy can linger under either name.
    assert "rm -f /opt/shift-agent/creative_firewall.py /opt/shift-agent/flyer_creative_firewall.py" \
        in DEPLOY_TEXT, "rollback branch must remove both flat creative_firewall names"


# ════════════════════════════════════════════════════════════════════════════
# STATIC — closure gate wiring (run everywhere)
# ════════════════════════════════════════════════════════════════════════════
def test_closure_gate_is_rollback_safe_guarded():
    # Mirrors the skills-manifest gate: a pre-gate rollback tarball lacking the
    # helper must WARN-skip, never hard-fail.
    assert '[ -f "$CLOSURE_CHECK" ]' in DEPLOY_TEXT, \
        "closure gate must be guarded on helper presence (rollback-safe)"
    assert "skipping artifact-runtime closure gate" in DEPLOY_TEXT, \
        "the guard's else-branch must WARN-skip on a pre-gate tarball"


def test_closure_gate_hooks_the_rollback_cascade():
    # On failure the gate must roll back onto the prior tarball and evict the broken
    # one — the same cascade the other pre-restart gates use. The behavior-preserving
    # post-install refactor moved the inline `"$0" rollback "$PREV_TAG"` into the
    # shared `revert_shift_tree` seam (deploy mode == that exact command; bootstrap
    # mode couples the Hermes-snapshot restore in front of it), so the invariant is
    # now that the closure-gate failure path invokes revert_shift_tree.
    idx = DEPLOY_TEXT.index("pre-restart artifact-runtime closure gate")
    block = DEPLOY_TEXT[idx:idx + 2000]
    assert "revert_shift_tree" in block, "gate failure must trigger the rollback cascade"
    assert 'rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"' in block, "gate failure must evict the broken tarball"
    assert "exit 1" in block, "gate failure must abort the deploy"


def test_closure_gate_runs_after_install_and_before_gateway_restart():
    # It must run AFTER all flat copies are installed and BEFORE the gateway restarts
    # so a failure rolls back onto the still-running old code. The refactor moved the
    # closure gate + the gateway restart into the shared post_install_gates_and_restart()
    # function (textually ABOVE the deploy) branch), so the ordering invariant now has
    # two parts: (1) within that function, closure gate < gateway restart; (2) the
    # deploy) branch runs install_artifacts before delegating to the function (so all
    # flat copies are laid down before the closure gate re-derives them at runtime).
    fn_start = _first(lambda l: l.strip() == "post_install_gates_and_restart() {")
    case_start = _first(lambda l: l.strip() == 'case "$ACTION" in')
    gate_call = _first(lambda l: 'bash "$CLOSURE_CHECK"' in l)
    restart = _first(lambda l: l.strip() == "systemctl restart hermes-gateway")
    assert fn_start < gate_call < restart < case_start, (
        f"closure gate (line {gate_call}) must sit between the function start "
        f"(line {fn_start}) and the gateway restart (line {restart}), all inside "
        f"post_install_gates_and_restart() (before the case at line {case_start})")
    # deploy) installs artifacts before calling the shared post-install function.
    deploy_start = DEPLOY_TEXT.index("\n    deploy)")
    install_pos = DEPLOY_TEXT.index('if ! install_artifacts "$STAGING"', deploy_start)
    fn_call_pos = DEPLOY_TEXT.index("post_install_gates_and_restart", install_pos)
    assert install_pos < fn_call_pos, (
        "deploy) must run install_artifacts before post_install_gates_and_restart")


def test_helper_invoked_with_artifact_and_runtime_roots():
    assert 'bash "$CLOSURE_CHECK" "$STAGING" /opt/shift-agent' in DEPLOY_TEXT, \
        "helper must be called with the staging artifact root and the flat runtime dir"


# ════════════════════════════════════════════════════════════════════════════
# EXTRACT-AND-RUN (Linux-only — needs bash + sha256sum + coreutils install)
# ════════════════════════════════════════════════════════════════════════════
def _tooling_ok() -> bool:
    if sys.platform == "win32":
        return False
    for exe in ("bash", "sha256sum", "install"):
        if shutil.which(exe) is None:
            return False
    try:
        return subprocess.run(["bash", "-c", "exit 0"], capture_output=True).returncode == 0
    except OSError:
        return False


posix_only = pytest.mark.skipif(
    not _tooling_ok(), reason="needs a functional bash + sha256sum + coreutils install")


# The CURRENT-release firewall + a live consumer that imports it by the BARE name
# (with the dotted package-style fallback the real validator carries) + a couple of
# other flat modules so the closure set is non-trivial.
_CF_CURRENT = "def is_hard_fact_claim(x):\n    return True  # CURRENT release\n"
_CF_STALE = ("def is_hard_fact_claim(x):\n    return True\n"
             "class RemovedByPR605:  # deleted upstream; only a STALE copy still has it\n    pass\n")
_VALIDATOR = (
    "try:\n"
    "    from creative_firewall import is_hard_fact_claim  # flat-on-VPS\n"
    "except ImportError:\n"
    "    from agents.flyer.creative_firewall import is_hard_fact_claim  # package fallback\n"
    "from schemas import Config\n"
    "import re, os\n"
)
_SCHEMAS = "class Config:\n    pass\n"


def _build_tree(tmp_path):
    """Create an artifact tree and the flat runtime tree as the PRE-FIX installer
    left it: the flyer_-prefixed copy installed, but a STALE bare creative_firewall.py
    lingering (the incident). Returns (artifact_dir, runtime_dir)."""
    art = tmp_path / "artifact"
    (art / "src" / "agents" / "flyer").mkdir(parents=True)
    (art / "src" / "platform").mkdir(parents=True)
    (art / "src" / "agents" / "flyer" / "creative_firewall.py").write_text(_CF_CURRENT, encoding="utf-8")
    (art / "src" / "agents" / "flyer" / "flyer_brief_validator.py").write_text(_VALIDATOR, encoding="utf-8")
    (art / "src" / "platform" / "schemas.py").write_text(_SCHEMAS, encoding="utf-8")

    rt = tmp_path / "runtime"
    rt.mkdir()
    # Flat install per the PRE-FIX installer: validator + schemas current, the
    # prefixed firewall current, but the BARE name is a stale orphan.
    (rt / "flyer_brief_validator.py").write_text(_VALIDATOR, encoding="utf-8")
    (rt / "schemas.py").write_text(_SCHEMAS, encoding="utf-8")
    (rt / "flyer_creative_firewall.py").write_text(_CF_CURRENT, encoding="utf-8")
    (rt / "creative_firewall.py").write_text(_CF_STALE, encoding="utf-8")  # the incident
    return art, rt


def _run(art, rt):
    r = subprocess.run(["bash", str(HELPER), str(art), str(rt)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def _apply_installer_refresh(art, rt):
    """Model the PR-2 install lines: install the artifact firewall to BOTH flat
    names via the same `install -m 644` the deploy script runs."""
    src = art / "src" / "agents" / "flyer" / "creative_firewall.py"
    for dst in ("creative_firewall.py", "flyer_creative_firewall.py"):
        subprocess.run(["install", "-m", "644", str(src), str(rt / dst)], check=True)


@posix_only
def test_incident_reproduced_stale_bare_copy_fails_closed(tmp_path):
    art, rt = _build_tree(tmp_path)
    rc, out, err = _run(art, rt)
    assert rc == 1, f"stale bare copy must fail the gate\nSTDOUT:{out}\nSTDERR:{err}"
    assert "CLOSURE-FAIL[stale-or-orphan]" in err
    assert "creative_firewall.py" in err


@posix_only
def test_installer_refresh_closes_the_incident(tmp_path):
    art, rt = _build_tree(tmp_path)
    assert _run(art, rt)[0] == 1  # incident present
    _apply_installer_refresh(art, rt)  # the deploy's refresh
    rc, out, err = _run(art, rt)
    assert rc == 0, f"gate must pass after the installer refreshes the bare copy\nSTDERR:{err}"
    assert "CLOSURE-OK" in out


@posix_only
def test_missing_flat_dependency_fails_closed(tmp_path):
    art, rt = _build_tree(tmp_path)
    _apply_installer_refresh(art, rt)
    (rt / "creative_firewall.py").unlink()  # imported flat module entirely absent
    rc, out, err = _run(art, rt)
    assert rc == 1
    assert "CLOSURE-FAIL[missing-import]" in err
    assert "creative_firewall" in err


@posix_only
def test_extra_orphan_module_fails_closed(tmp_path):
    art, rt = _build_tree(tmp_path)
    _apply_installer_refresh(art, rt)
    (rt / "rogue_module.py").write_text("x = 'not shipped by the artifact'\n", encoding="utf-8")
    rc, out, err = _run(art, rt)
    assert rc == 1
    assert "CLOSURE-FAIL[stale-or-orphan]" in err
    assert "rogue_module.py" in err


@posix_only
def test_stale_imported_dependency_fails_closed(tmp_path):
    # Bare copy present but not byte-identical to the artifact (content drift on the
    # imported name specifically) → stale-import.
    art, rt = _build_tree(tmp_path)
    _apply_installer_refresh(art, rt)
    (rt / "creative_firewall.py").write_text(_CF_STALE, encoding="utf-8")
    rc, out, err = _run(art, rt)
    assert rc == 1
    assert "CLOSURE-FAIL[stale-import]" in err or "CLOSURE-FAIL[stale-or-orphan]" in err


@posix_only
def test_clean_refreshed_tree_passes(tmp_path):
    art, rt = _build_tree(tmp_path)
    _apply_installer_refresh(art, rt)
    rc, out, err = _run(art, rt)
    assert rc == 0, err
    assert "CLOSURE-OK" in out


@posix_only
def test_dotted_package_fallback_import_is_not_required_flat(tmp_path):
    # A module that imports ONLY via the dotted package path must NOT create a
    # flat-module requirement (those resolve to a package, not a flat file).
    art, rt = _build_tree(tmp_path)
    _apply_installer_refresh(art, rt)
    dotted = "from agents.flyer.creative_firewall import is_hard_fact_claim\n"
    (art / "src" / "agents" / "flyer" / "dotted_only.py").write_text(dotted, encoding="utf-8")
    (rt / "flyer_dotted_only.py").write_text(dotted, encoding="utf-8")
    rc, out, err = _run(art, rt)
    assert rc == 0, f"dotted-only import must not be treated as a missing flat dep\nSTDERR:{err}"


@posix_only
def test_missing_helper_args_is_usage_error(tmp_path):
    r = subprocess.run(["bash", str(HELPER)], capture_output=True, text=True)
    assert r.returncode == 2
    assert "usage" in r.stderr.lower()
