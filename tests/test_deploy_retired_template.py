"""ops/retired-template-removal — artifact-aware deletion of the retired
dead_man_alert.txt shift template in the deploy script (#627 follow-up).

Two layers (mirrors test_deploy_lock_init.py):
  * STATIC assertions on the real shift-agent-deploy.sh text — run everywhere.
  * EXTRACT-AND-RUN of the bash functions in a tmp sandbox with DISPOSABLE positional
    params (no env surface, no touching /opt) — Linux-only (needs real POSIX lstat /
    symlink / FIFO semantics + `bash`).

The logic touches NO product module and names EXACTLY one canonical literal path — no
wildcard, no template-dir rsync-delete. Rollback to a tarball that still ships the
template is restored automatically by the additive glob (proved below).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
TEXT = DEPLOY.read_text(encoding="utf-8")
LINES = TEXT.splitlines()

_BEGIN = "# BEGIN retired-template-removal"
_END = "# END retired-template-removal"
CANONICAL = "/opt/shift-agent/templates/dead_man_alert.txt"
STAGED_REL = "src/agents/shift/templates/dead_man_alert.txt"


def _func_block() -> str:
    return TEXT[TEXT.index(_BEGIN):TEXT.index(_END)]


def _code_lines(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))


def _first_line(pred) -> int:
    for i, ln in enumerate(LINES, start=1):
        if pred(ln):
            return i
    raise AssertionError("no line matched")


# ════════════════════════════════════════════════════════════════════════════
# STATIC (run everywhere)
# ════════════════════════════════════════════════════════════════════════════
def test_removal_call_after_templates_install_before_first_restart():
    install = _first_line(lambda l: "install -m 644 src/agents/shift/templates/*" in l)
    removal = _first_line(lambda l: l.strip() == "remove_retired_shift_template \\")
    restart = _first_line(lambda l: re.match(r"^\s*systemctl restart\b", l))
    assert install < removal < restart, f"install={install} removal={removal} restart={restart}"


def test_verify_call_in_verification_section_before_restart():
    install = _first_line(lambda l: "install -m 644 src/agents/shift/templates/*" in l)
    verify = _first_line(lambda l: l.strip() == "verify_retired_shift_template_absent \\")
    restart = _first_line(lambda l: re.match(r"^\s*systemctl restart\b", l))
    assert install < verify < restart, f"install={install} verify={verify} restart={restart}"


def test_removal_names_only_the_literal_canonical_path_no_wildcard():
    block = _func_block()
    # the executable logic must reference the canonical literal and carry NO glob token
    code = _code_lines(block)
    assert '"$CANONICAL"' in code and "STAGED" in code
    assert "*" not in block, "the retired-template block must contain NO wildcard token"
    # the call site passes the exact canonical + staged literals, no wildcard
    idx = _first_line(lambda l: l.strip() == "remove_retired_shift_template \\")
    call = " ".join(l.strip() for l in LINES[idx - 1:idx + 3])
    assert f'"{CANONICAL}"' in call and f'"{STAGED_REL}"' in call
    assert "*" not in call


def test_no_rsync_delete_on_templates_dir_anywhere():
    for l in LINES:
        if l.strip().startswith("#"):
            continue
        if "rsync" in l and "--delete" in l:
            assert "/opt/shift-agent/templates" not in l and "templates/" not in l, \
                f"forbidden rsync --delete against the templates dir: {l!r}"


def test_lstat_first_symlink_check_precedes_regular_file_test():
    body = _func_block()
    sym = body.index('[ -L "$CANONICAL" ]')
    reg = body.index('[ -f "$CANONICAL" ]')
    assert sym < reg, "symlink (lstat) rejection must precede the -f test that would follow it"


# ════════════════════════════════════════════════════════════════════════════
# EXTRACT-AND-RUN (Linux-only)
# ════════════════════════════════════════════════════════════════════════════
pytestmark_posix = pytest.mark.skipif(
    sys.platform == "win32",
    reason="extract-and-run needs real bash + POSIX lstat/symlink/FIFO semantics")


def _run(tmp_path: Path, fn: str, staged: Path, canonical: Path):
    """Write the extracted functions + a positional call into a tmp .sh and run it.
    Returns (returncode, stdout, stderr). Paths pass as $1/$2 (space-safe)."""
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + _func_block() + f'\n{fn} "$1" "$2"\n',
        encoding="utf-8")
    r = subprocess.run(["bash", str(script), str(staged), str(canonical)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


@pytest.fixture
def dirs(tmp_path):
    staged_dir = tmp_path / "staging" / "src" / "agents" / "shift" / "templates"
    installed = tmp_path / "opt" / "templates"
    staged_dir.mkdir(parents=True)
    installed.mkdir(parents=True)
    return {"staged": staged_dir / "dead_man_alert.txt",
            "canonical": installed / "dead_man_alert.txt",
            "installed": installed}


@pytestmark_posix
def test_staged_omits_and_installed_regular_file_is_removed(tmp_path, dirs):
    dirs["canonical"].write_text("⚕ old health alert\n", encoding="utf-8")  # lingering copy
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["canonical"])
    assert rc == 0, err
    assert not dirs["canonical"].exists(), "lingering regular file must be removed"
    assert "removed lingering" in out


@pytestmark_posix
def test_staged_omits_and_already_absent_is_idempotent_success(tmp_path, dirs):
    assert not dirs["canonical"].exists()
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["canonical"])
    assert rc == 0, err
    assert "already absent" in out


@pytestmark_posix
def test_staged_contains_rollback_shape_leaves_installed_file(tmp_path, dirs):
    dirs["staged"].write_text("⚕ *Shift Agent — Health Alert*\n", encoding="utf-8")  # artifact ships it
    dirs["canonical"].write_text("installed by the additive glob\n", encoding="utf-8")
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["canonical"])
    assert rc == 0, err
    assert dirs["canonical"].read_text(encoding="utf-8") == "installed by the additive glob\n", \
        "rollback: staged-present ⇒ deletion logic does nothing"
    assert "left as-is" in out


@pytestmark_posix
def test_symlink_at_canonical_path_is_fatal_before_restart(tmp_path, dirs):
    target = tmp_path / "elsewhere.txt"
    target.write_text("x", encoding="utf-8")
    os.symlink(target, dirs["canonical"])
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["canonical"])
    assert rc != 0 and "SYMLINK" in err
    assert os.path.islink(dirs["canonical"]), "the symlink must be left in place (never followed/removed)"
    assert target.exists(), "the symlink target must be untouched"


@pytestmark_posix
def test_directory_at_canonical_path_is_fatal(tmp_path, dirs):
    dirs["canonical"].mkdir()
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["canonical"])
    assert rc != 0 and "NOT a regular file" in err
    assert dirs["canonical"].is_dir()


@pytestmark_posix
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform has no mkfifo")
def test_fifo_at_canonical_path_is_fatal(tmp_path, dirs):
    os.mkfifo(dirs["canonical"])
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["canonical"])
    assert rc != 0 and "NOT a regular file" in err


@pytestmark_posix
def test_unrelated_templates_remain_byte_identical(tmp_path, dirs):
    other = dirs["installed"] / "coverage_message_to_candidate.txt"
    other_bytes = b"unrelated template content \xe2\x9a\x95\n"
    other.write_bytes(other_bytes)
    dirs["canonical"].write_text("lingering\n", encoding="utf-8")
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["canonical"])
    assert rc == 0, err
    assert not dirs["canonical"].exists()
    assert other.read_bytes() == other_bytes, "unrelated templates must be byte-identical"
    assert sorted(p.name for p in dirs["installed"].iterdir()) == ["coverage_message_to_candidate.txt"]


# ── lingering-detection verifier ────────────────────────────────────────────
@pytestmark_posix
def test_verify_passes_when_absent_after_removal(tmp_path, dirs):
    rc, out, err = _run(tmp_path, "verify_retired_shift_template_absent", dirs["staged"], dirs["canonical"])
    assert rc == 0, err
    assert "verified absent" in out


@pytestmark_posix
def test_verify_fatal_when_file_planted_after_removal(tmp_path, dirs):
    dirs["canonical"].write_text("re-appeared after removal\n", encoding="utf-8")  # simulate linger
    rc, out, err = _run(tmp_path, "verify_retired_shift_template_absent", dirs["staged"], dirs["canonical"])
    assert rc != 0 and "STILL PRESENT" in err


@pytestmark_posix
def test_verify_passes_when_artifact_ships_template(tmp_path, dirs):
    dirs["staged"].write_text("ships it\n", encoding="utf-8")
    dirs["canonical"].write_text("installed\n", encoding="utf-8")
    rc, out, err = _run(tmp_path, "verify_retired_shift_template_absent", dirs["staged"], dirs["canonical"])
    assert rc == 0, err  # staged-present ⇒ canonical presence is correct
