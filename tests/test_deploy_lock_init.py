"""ops/approval-lock-init — canonical approval-code lock initialization in the
deploy script.

Two layers (repo static+extract style):
  * STATIC assertions on the real shift-agent-deploy.sh text — run everywhere.
  * EXTRACT-AND-RUN of the bash function in a tmp sandbox (SHIFT_AGENT_DEPLOY_
    TEST_SANDBOX=1 + APPROVAL_LOCK_* overrides, current user as expected owner) —
    Linux-only (needs bash + POSIX stat/chown + a python3 whose safe_io imports
    fcntl; GH CI runs unprivileged so root chown-to-shift-agent / runuser cannot
    run there, hence the sandbox uses the current user).

The initializer touches NO product module: it only creates/validates the lock
file and verifies acquire/release via the EXACT production safe_io.
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

_BEGIN = "# BEGIN approval-code-lock-init"
_END = "# END approval-code-lock-init"


def _func_block() -> str:
    """The BEGIN..END region containing the initialize_approval_code_lock fn."""
    s = TEXT.index(_BEGIN)
    e = TEXT.index(_END)
    return TEXT[s:e]


def _first_line(pred) -> int:
    for i, ln in enumerate(LINES, start=1):
        if pred(ln):
            return i
    raise AssertionError("no line matched")


# ════════════════════════════════════════════════════════════════════════════
# STATIC — ordering, fail-before-activation, sandbox non-inheritance, TOCTOU
# ════════════════════════════════════════════════════════════════════════════
def test_init_invoked_after_install_before_first_restart():
    """The bare call sits AFTER the forward install_artifacts and BEFORE the first
    real service restart/start/reload — i.e. every check/creation/verification the
    function performs runs before activation (item 3)."""
    install_line = _first_line(lambda l: 'install_artifacts "$STAGING"' in l)
    init_call_line = _first_line(lambda l: l.strip() == "initialize_approval_code_lock")
    restart_line = _first_line(
        lambda l: re.match(r"^\s*systemctl (restart|start|reload)\b", l))
    assert install_line < init_call_line < restart_line, (
        f"install={install_line} init_call={init_call_line} first_restart={restart_line}")


def test_init_call_is_bare_not_swallowed():
    """The call is a bare statement — never `|| true`, `if …`, `&& …` or a
    conditional context that would swallow its FATAL under `set -e`."""
    call = next(l for l in LINES if l.strip() == "initialize_approval_code_lock")
    assert call.strip() == "initialize_approval_code_lock"
    # no other occurrence wraps it in a swallow/conditional
    for l in LINES:
        if "initialize_approval_code_lock" in l and l.strip() != "initialize_approval_code_lock":
            # allowed: the definition line and comment mentions only
            assert (l.strip().startswith("#")
                    or l.strip().startswith("initialize_approval_code_lock() {")), l
            assert "|| true" not in l and "&&" not in l


def test_set_euo_pipefail_present():
    assert re.search(r"^set -euo pipefail", TEXT, re.M)


def test_no_rm_of_canonical_lock_anywhere():
    """No path (forward OR rollback) removes the canonical lock (item 5/6)."""
    offenders = [l for l in LINES if re.search(r"\brm\b.*approval-code-pools\.lock", l)]
    assert offenders == [], offenders


def test_existing_file_branch_has_no_chown_or_chmod():
    """The SAFE-existing branch leaves the file completely alone: no chown/chmod
    between `if [ -e "$LOCK" ]` and the 'left untouched' echo (item 4)."""
    fn = _func_block()
    start = fn.index('if [ -e "$LOCK" ]; then')
    untouched = fn.index("left untouched")
    # code lines only — comments legitimately NAME chown/chmod to say they're forbidden here.
    code = "\n".join(l for l in fn[start:untouched].splitlines()
                     if not l.strip().startswith("#"))
    assert "chown" not in code, "chown COMMAND in existing-file branch (forbidden repair)"
    assert "chmod" not in code, "chmod COMMAND in existing-file branch (forbidden repair)"
    assert "touch" not in code and '> "$LOCK"' not in code


def test_creation_branch_o_excl_and_mode_0660():
    """Creation uses O_EXCL semantics (noclobber `set -C` + `: > "$LOCK"` under
    umask 007 -> 0660) and re-verifies mode 660 (items 3/5)."""
    fn = _func_block()
    assert "umask 007" in fn
    assert "set -C" in fn
    assert ': > "$LOCK"' in fn
    assert "chmod 0660" in fn
    assert '"$n_mode" != "660"' in fn  # re-lstat verify pins created mode


def test_symlink_checked_before_e_or_f():
    """`[ -L ]` (lstat) is evaluated BEFORE `[ -e ]`/`[ -f ]` so a symlink is never
    followed (item 5 lstat semantics)."""
    fn = _func_block()
    l_idx = fn.index('[ -L "$LOCK" ]')
    e_idx = fn.index('[ -e "$LOCK" ]')
    assert l_idx < e_idx


def test_dual_identity_verification_with_bounded_attempts():
    """Root AND shift-agent (runuser) acquire/release via try_acquire_filelock_
    with_retry with bounded attempts (items 2/3)."""
    fn = _func_block()
    assert '_approval_lock_acquire_release "root"' in fn
    assert '_approval_lock_acquire_release "shift-agent" runuser -u shift-agent --' in fn
    assert "try_acquire_filelock_with_retry" in fn
    assert "ATTEMPTS=5" in fn and "attempts=attempts" in fn  # bounded


def test_verifier_asserts_exact_safe_io_resolution_and_sys_path_insert():
    """The verifier explicitly sys.path.insert(0, /opt/shift-agent) and ASSERTS
    safe_io.__file__ == PLATFORM_DIR/safe_io.py (never ambient CWD) (item 2)."""
    fn = _func_block()
    assert "sys.path.insert(0, platform_dir)" in fn
    assert "assert safe_io.__file__ == expected" in fn
    assert 'os.path.join(platform_dir, "safe_io.py")' in fn
    # production platform dir default is the canonical /opt/shift-agent
    assert 'PLATFORM_DIR="/opt/shift-agent"' in fn


def test_verifier_prints_interpreter_module_lock_identity():
    """Each acquisition prints interpreter + resolved module + lock + identity so
    BOTH identities' evidence lands in the deploy log (item 2)."""
    fn = _func_block()
    assert "sys.executable" in fn
    assert re.search(r"print\([^)]*safe_io\.__file__", fn), "safe_io.__file__ not printed"
    assert re.search(r"print\([^)]*\block\b", fn), "lock path not printed"
    assert "os.geteuid()" in fn and ("getpass" in fn or "pwd" in fn)


def test_sandbox_flag_gates_overrides_production_defaults_hardcoded():
    """Overrides honored ONLY under SHIFT_AGENT_DEPLOY_TEST_SANDBOX=1; the else
    branch hard-codes production values (item 1)."""
    fn = _func_block()
    assert '"${SHIFT_AGENT_DEPLOY_TEST_SANDBOX:-0}" = "1"' in fn
    # production else-branch: canonical path + shift-agent + on-box python + /opt
    assert 'LOCK="/opt/shift-agent/state/approval-code-pools.lock"' in fn
    assert 'OWNER="shift-agent"' in fn and 'GROUP="shift-agent"' in fn
    assert 'PYBIN="python3"' in fn
    # overrides only referenced inside the sandbox branch (guarded by the flag)
    assert "APPROVAL_LOCK_PATH" in fn


def test_deploy_script_never_sets_the_sandbox_flag():
    """The deploy script itself must NEVER set SHIFT_AGENT_DEPLOY_TEST_SANDBOX
    (only READ it via :-0). A production run can never inherit test mode (item 1)."""
    for l in LINES:
        if l.strip().startswith("#"):
            continue  # comments describe the flag; they don't set it
        # an actual assignment is `NAME=` (the `${NAME:-0}` reads have `:` not `=`).
        assert not re.search(r"(?:export\s+)?SHIFT_AGENT_DEPLOY_TEST_SANDBOX\s*=", l), \
            f"deploy script sets the sandbox flag: {l!r}"


def test_script_never_holds_a_filelock_across_activation():
    """The deploy script (bash) never holds the canonical lock across restart/smoke
    — no bash `flock` on it; the only acquisition is the short-lived, released
    verifier subprocess (item 7)."""
    assert not re.search(r"\bflock\b[^\n]*approval-code-pools\.lock", TEXT), \
        "bash flock on the canonical lock would hold it across activation"


# ════════════════════════════════════════════════════════════════════════════
# EXTRACT-AND-RUN — the bash function in a tmp sandbox (Linux-only)
# ════════════════════════════════════════════════════════════════════════════
_LINUX = pytest.mark.skipif(
    sys.platform == "win32",
    reason="extract-and-run needs bash + POSIX stat/chown + fcntl-capable python3 "
           "(GH CI ubuntu runs these unprivileged; Windows dev box cannot)")


def _ids():
    import grp
    import pwd
    return (pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name)


def _run_init(tmp_path, *, lock, owner=None, group=None, platform_dir=None,
              pybin="python3"):
    user, ggroup = _ids()
    func = _func_block()
    driver = tmp_path / "driver.sh"
    driver.write_text("set -euo pipefail\n" + func + "\ninitialize_approval_code_lock\n",
                      encoding="utf-8")
    env = {
        **os.environ,
        "SHIFT_AGENT_DEPLOY_TEST_SANDBOX": "1",
        "APPROVAL_LOCK_PATH": str(lock),
        "APPROVAL_LOCK_OWNER": owner if owner is not None else user,
        "APPROVAL_LOCK_GROUP": group if group is not None else ggroup,
        "APPROVAL_LOCK_PLATFORM_DIR": str(platform_dir or (REPO / "src" / "platform")),
        "APPROVAL_LOCK_PYBIN": pybin,
    }
    return subprocess.run(["bash", str(driver)], env=env, capture_output=True, text=True)


@_LINUX
def test_absent_path_creates_owner_group_mode_0660(tmp_path):
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    r = _run_init(tmp_path, lock=lock)
    assert r.returncode == 0, r.stderr
    st = os.stat(lock)
    import grp
    import pwd
    assert not os.path.islink(lock) and os.path.isfile(lock)
    assert pwd.getpwuid(st.st_uid).pw_name == _ids()[0]
    assert grp.getgrgid(st.st_gid).gr_name == _ids()[1]
    assert oct(st.st_mode & 0o777) == oct(0o660)


@_LINUX
def test_safe_existing_file_left_untouched_inode_mtime_content(tmp_path):
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("preexisting-content")
    os.chmod(lock, 0o660)
    before = os.stat(lock)
    before_content = lock.read_text()
    r = _run_init(tmp_path, lock=lock)
    assert r.returncode == 0, r.stderr
    after = os.stat(lock)
    assert after.st_ino == before.st_ino, "inode changed — file was replaced"
    assert after.st_mtime == before.st_mtime, "mtime changed — file was touched/rewritten"
    assert lock.read_text() == before_content, "content changed — file was truncated/written"
    assert "left untouched" in r.stdout


@_LINUX
def test_symlink_is_fatal(tmp_path):
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    target = tmp_path / "target"
    target.write_text("x")
    os.symlink(target, lock)
    r = _run_init(tmp_path, lock=lock)
    assert r.returncode != 0 and "SYMLINK" in r.stderr


@_LINUX
def test_directory_is_fatal(tmp_path):
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.mkdir(parents=True)
    r = _run_init(tmp_path, lock=lock)
    assert r.returncode != 0 and "NOT a regular file" in r.stderr


@_LINUX
def test_fifo_is_fatal(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("mkfifo unavailable")
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    try:
        os.mkfifo(lock)
    except (AttributeError, OSError):
        pytest.skip("mkfifo unsupported on this platform")
    r = _run_init(tmp_path, lock=lock)
    assert r.returncode != 0 and "NOT a regular file" in r.stderr


@_LINUX
def test_wrong_owner_is_fatal(tmp_path):
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.chmod(lock, 0o660)
    # expected-owner override mismatch (file is owned by the current user)
    r = _run_init(tmp_path, lock=lock, owner="definitely-not-this-user")
    assert r.returncode != 0 and "owner:group" in r.stderr


@_LINUX
def test_wrong_group_is_fatal(tmp_path):
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.chmod(lock, 0o660)
    r = _run_init(tmp_path, lock=lock, group="definitely-not-this-group")
    assert r.returncode != 0 and "owner:group" in r.stderr


@_LINUX
@pytest.mark.parametrize("mode", [0o666, 0o602, 0o600, 0o060])
def test_unsafe_mode_is_fatal(tmp_path, mode):
    """0666 world-writable, 0602/0600 missing group-rw, 0060 missing owner-rw —
    all rejected by the {640,660} gate."""
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.chmod(lock, mode)
    r = _run_init(tmp_path, lock=lock)
    assert r.returncode != 0 and "mode" in r.stderr


@_LINUX
def test_acquire_verification_failure_is_fatal(tmp_path):
    """Point the verifier's platform dir at a stub safe_io whose
    try_acquire_filelock_with_retry raises → deployment FAILS (no unlocked path)."""
    stub = tmp_path / "platform"
    stub.mkdir()
    (stub / "safe_io.py").write_text(
        "from contextlib import contextmanager\n"
        "@contextmanager\n"
        "def try_acquire_filelock_with_retry(*a, **k):\n"
        "    raise RuntimeError('stub: acquire refused')\n"
        "    yield\n",
        encoding="utf-8",
    )
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    r = _run_init(tmp_path, lock=lock, platform_dir=stub)
    assert r.returncode != 0
    assert "acquire/release FAILED" in r.stderr or "stub: acquire refused" in (r.stderr + r.stdout)
