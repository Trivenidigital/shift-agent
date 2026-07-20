"""ops/retired-template-removal — dir_fd-ANCHORED deletion of the retired
dead_man_alert.txt shift template in the deploy script (#627, reviewer-revised #629).

The parent /opt/shift-agent/templates is runtime-writable (box-verified
shift-agent:shift-agent 0755), so a protected-parent pathname `rm` would be
TOCTOU-exposed. The removal opens the parent ONCE (O_DIRECTORY | O_NOFOLLOW → a
symlinked parent is FATAL), anchors lstat/unlink to that directory INODE via dir_fd,
and unlink NEVER follows the leaf — any NON-directory object at the retired name is
unlinked; a directory is FATAL; absent is idempotent success.

Two layers (mirrors test_deploy_lock_init.py):
  * STATIC assertions on the real shift-agent-deploy.sh text — run everywhere.
  * EXTRACT-AND-RUN of the bash functions (which invoke a python3 heredoc) in a tmp
    sandbox with DISPOSABLE positional params — Linux-only (needs real POSIX
    O_DIRECTORY / dir_fd / lstat / symlink / FIFO semantics + bash + python3).

The logic touches NO product module and makes NO ownership change; it names EXACTLY one
parent + one entry name — no wildcard, no template-dir rsync-delete.
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
NAME = "dead_man_alert.txt"
STAGED_REL = "src/agents/shift/templates/dead_man_alert.txt"
PARENT_LIT = "/opt/shift-agent/templates"


def _func_block() -> str:
    return TEXT[TEXT.index(_BEGIN):TEXT.index(_END)]


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


def test_block_is_dir_fd_anchored_no_wildcard_no_pathname_rm():
    block = _func_block()
    assert "os.O_DIRECTORY | os.O_NOFOLLOW" in block, "parent must be opened O_DIRECTORY|O_NOFOLLOW"
    assert "os.fstat(pfd)" in block, "parent must be validated via fstat on the opened fd"
    assert "os.lstat(name, dir_fd=pfd)" in block, "entry type must be lstat'd anchored to the parent fd"
    assert "os.unlink(name, dir_fd=pfd)" in block, "removal must be a dir_fd-anchored unlink"
    assert "*" not in block, "the retired-template block must contain NO wildcard token"
    assert "rm -f" not in block, "must NOT rm by pathname — dir_fd-anchored unlink only"


def test_call_sites_pass_parent_and_name_literals_no_wildcard():
    idx = _first_line(lambda l: l.strip() == "remove_retired_shift_template \\")
    call = " ".join(l.strip() for l in LINES[idx - 1:idx + 4])
    assert f'"{PARENT_LIT}"' in call and f'"{NAME}"' in call and f'"{STAGED_REL}"' in call
    assert "*" not in call


def test_no_rsync_delete_on_templates_dir_anywhere():
    for l in LINES:
        if l.strip().startswith("#"):
            continue
        if "rsync" in l and "--delete" in l:
            assert "/opt/shift-agent/templates" not in l and "templates/" not in l, \
                f"forbidden rsync --delete against the templates dir: {l!r}"


def test_symlinked_parent_rejected_via_o_nofollow_in_source():
    block = _func_block()
    assert "O_NOFOLLOW" in block
    assert block.index("os.O_DIRECTORY | os.O_NOFOLLOW") < block.index("os.lstat(name, dir_fd=pfd)")


# ════════════════════════════════════════════════════════════════════════════
# EXTRACT-AND-RUN (Linux-only — needs O_DIRECTORY / dir_fd / real lstat)
# ════════════════════════════════════════════════════════════════════════════
posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="dir_fd-anchored removal needs POSIX O_DIRECTORY/dir_fd + bash + python3")


def _run(tmp_path: Path, fn: str, staged: Path, parent: Path, name: str):
    """Write the extracted functions + a positional call into a tmp .sh and run it.
    Returns (rc, stdout, stderr). staged/parent/name pass as $1/$2/$3 (pybin defaults)."""
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + _func_block() + f'\n{fn} "$1" "$2" "$3"\n',
        encoding="utf-8")
    r = subprocess.run(["bash", str(script), str(staged), str(parent), name],
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


@pytest.fixture
def dirs(tmp_path):
    staged_dir = tmp_path / "staging" / "src" / "agents" / "shift" / "templates"
    parent = tmp_path / "opt" / "templates"
    staged_dir.mkdir(parents=True)
    parent.mkdir(parents=True)
    return {"staged": staged_dir / "dead_man_alert.txt", "parent": parent,
            "entry": parent / "dead_man_alert.txt"}


@posix_only
def test_staged_omits_and_installed_regular_file_is_removed(tmp_path, dirs):
    dirs["entry"].write_text("⚕ old health alert\n", encoding="utf-8")
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["parent"], NAME)
    assert rc == 0, err
    assert not dirs["entry"].exists(), "lingering regular file must be unlinked"
    assert "removed lingering" in err and "(regular)" in err


@posix_only
def test_staged_omits_and_already_absent_is_idempotent(tmp_path, dirs):
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["parent"], NAME)
    assert rc == 0, err
    assert "already absent" in err


@posix_only
def test_staged_contains_rollback_shape_leaves_installed_file(tmp_path, dirs):
    dirs["staged"].write_text("⚕ *Shift Agent — Health Alert*\n", encoding="utf-8")
    dirs["entry"].write_text("installed by the additive glob\n", encoding="utf-8")
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["parent"], NAME)
    assert rc == 0, err
    assert dirs["entry"].read_text(encoding="utf-8") == "installed by the additive glob\n", \
        "rollback: staged-present ⇒ removal does nothing"
    assert "left as-is" in out


@posix_only
def test_directory_at_entry_is_fatal(tmp_path, dirs):
    dirs["entry"].mkdir()
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["parent"], NAME)
    assert rc != 0 and "is a DIRECTORY" in err
    assert dirs["entry"].is_dir(), "a directory at the name must be left intact (never recursively removed)"


@posix_only
def test_symlinked_parent_is_fatal(tmp_path, dirs):
    real_parent = tmp_path / "real_templates"
    real_parent.mkdir()
    link_parent = tmp_path / "link_templates"
    os.symlink(real_parent, link_parent)
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], link_parent, NAME)
    assert rc != 0 and "non-symlink directory" in err
    assert os.path.islink(link_parent), "the symlinked parent must be untouched"


@posix_only
def test_swapped_symlink_entry_is_unlinked_target_untouched(tmp_path, dirs):
    # A symlink swapped in at the retired name: unlink removes the LINK, never the target.
    target = tmp_path / "victim.txt"
    target.write_text("must survive\n", encoding="utf-8")
    os.symlink(target, dirs["entry"])
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["parent"], NAME)
    assert rc == 0, err
    assert not os.path.lexists(dirs["entry"]), "the swapped-in symlink must be unlinked"
    assert target.read_text(encoding="utf-8") == "must survive\n", \
        "unlink must NOT follow the symlink — the target file survives untouched"
    assert "(symlink)" in err


@posix_only
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform has no mkfifo")
def test_fifo_at_entry_is_unlinked(tmp_path, dirs):
    os.mkfifo(dirs["entry"])
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["parent"], NAME)
    assert rc == 0, err
    assert not dirs["entry"].exists(), "a FIFO at the retired name is non-directory → unlinked"
    assert "(fifo)" in err


@posix_only
def test_unrelated_templates_remain_byte_identical(tmp_path, dirs):
    other = dirs["parent"] / "coverage_message_to_candidate.txt"
    other_bytes = b"unrelated template content \xe2\x9a\x95\n"
    other.write_bytes(other_bytes)
    dirs["entry"].write_text("lingering\n", encoding="utf-8")
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["parent"], NAME)
    assert rc == 0, err
    assert not dirs["entry"].exists()
    assert other.read_bytes() == other_bytes, "unrelated templates must be byte-identical"
    assert sorted(p.name for p in dirs["parent"].iterdir()) == ["coverage_message_to_candidate.txt"]


@posix_only
def test_parent_anchor_evidence_line_reports_owner_group_mode_inode(tmp_path, dirs):
    dirs["entry"].write_text("x\n", encoding="utf-8")
    rc, out, err = _run(tmp_path, "remove_retired_shift_template", dirs["staged"], dirs["parent"], NAME)
    assert rc == 0, err
    m = re.search(
        r"\[retired-template\] parent .* anchored: owner=(\S+) group=(\S+) mode=(\S+) dev=(\S+) ino=(\S+)", err)
    assert m, f"missing parent-anchor evidence line in:\n{err}"
    st = os.stat(dirs["parent"])
    assert m.group(4) == str(st.st_dev) and m.group(5) == str(st.st_ino), \
        "reported dev/ino must be the anchored parent's (fstat==realpath inode)"


# ── verifier (same dir_fd-anchored lstat, read-only) ────────────────────────
@posix_only
def test_verify_passes_when_absent(tmp_path, dirs):
    rc, out, err = _run(tmp_path, "verify_retired_shift_template_absent", dirs["staged"], dirs["parent"], NAME)
    assert rc == 0, err
    assert "verified absent" in err


@posix_only
def test_verify_fatal_when_entry_planted(tmp_path, dirs):
    dirs["entry"].write_text("re-appeared\n", encoding="utf-8")
    rc, out, err = _run(tmp_path, "verify_retired_shift_template_absent", dirs["staged"], dirs["parent"], NAME)
    assert rc != 0 and "STILL PRESENT" in err


@posix_only
def test_verify_passes_when_artifact_ships_template(tmp_path, dirs):
    dirs["staged"].write_text("ships it\n", encoding="utf-8")
    dirs["entry"].write_text("installed\n", encoding="utf-8")
    rc, out, err = _run(tmp_path, "verify_retired_shift_template_absent", dirs["staged"], dirs["parent"], NAME)
    assert rc == 0, err  # staged-present ⇒ presence is correct
