"""ops/approval-lock-init — canonical approval-code lock initialization in the
deploy script (reviewer-refined #624).

Three layers (repo static+extract style):
  * STATIC assertions on the real shift-agent-deploy.sh text — run everywhere.
  * EXTRACT-AND-RUN of the bash function in a tmp sandbox with DISPOSABLE positional
    params (no env surface) — Linux-only.
  * REAL-SCRIPT guard + ADVERSARIAL fd-interposition — Linux-only.

The initializer touches NO product module. It parameterizes every input
positionally (production call site passes canonical literals) and validates the
PRODUCTION safe_io.FileLock's OWN descriptor (fstat/lstat identity), bounded by a
deploy-side SIGALRM. Production safe_io is UNMODIFIED.
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
PLATFORM = REPO / "src" / "platform"

_BEGIN = "# BEGIN approval-code-lock-init"
_END = "# END approval-code-lock-init"


def _func_block() -> str:
    return TEXT[TEXT.index(_BEGIN):TEXT.index(_END)]


def _func_def_body() -> str:
    """Only the function DEFINITION body (excludes the BEGIN prose comment that
    legitimately names the sandbox var / chown / chmod)."""
    fn = _func_block()
    return fn[fn.index("initialize_approval_code_lock() {"):]


def _code_lines(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))


def _extract_verifier() -> str:
    """The PY_VERIFY heredoc body — the fd-identity verifier, runnable standalone
    with argv (platform_dir, lock, timeout, owner, group, modes, hook)."""
    fn = _func_block()
    s = fn.index("<<'PY_VERIFY'\n") + len("<<'PY_VERIFY'\n")
    e = fn.index("\nPY_VERIFY", s)
    return fn[s:e]


def _first_line(pred) -> int:
    for i, ln in enumerate(LINES, start=1):
        if pred(ln):
            return i
    raise AssertionError("no line matched")


# ════════════════════════════════════════════════════════════════════════════
# STATIC (run everywhere)
# ════════════════════════════════════════════════════════════════════════════
def test_init_invoked_after_install_before_first_restart():
    install = _first_line(lambda l: 'install_artifacts "$STAGING"' in l)
    init_call = _first_line(lambda l: l.strip() == "initialize_approval_code_lock \\")
    restart = _first_line(lambda l: re.match(r"^\s*systemctl (restart|start|reload)\b", l))
    assert install < init_call < restart, f"install={install} init={init_call} restart={restart}"


def test_init_call_is_not_swallowed():
    """The call (multi-line, positional literals) is a bare statement — no
    `|| true`, `if`, `&&` swallow of its FATAL under `set -e`."""
    idx = _first_line(lambda l: l.strip() == "initialize_approval_code_lock \\")
    call = "\n".join(LINES[idx - 1:idx + 2])  # the 3-line invocation
    assert "|| true" not in call and "&&" not in call
    assert not call.strip().startswith("if ")


def test_call_site_passes_canonical_literals():
    idx = _first_line(lambda l: l.strip() == "initialize_approval_code_lock \\")
    call = " ".join(l.strip() for l in LINES[idx - 1:idx + 2])
    assert '"/opt/shift-agent/state/approval-code-pools.lock"' in call
    assert '"shift-agent" "shift-agent"' in call
    assert '"python3"' in call and '"/opt/shift-agent"' in call


def test_function_takes_positional_and_reads_no_test_env():
    body = _func_def_body()
    assert 'local LOCK="$1" OWNER="$2" GROUP="$3" PYBIN="$4" PLATFORM_DIR="$5"' in body
    code = _code_lines(body)
    assert "APPROVAL_LOCK_" not in code, "function still reads APPROVAL_LOCK_* env"
    assert "SHIFT_AGENT_DEPLOY_TEST_SANDBOX" not in code, "function still reads the sandbox flag"


def test_top_guard_presence_based_and_source_gated():
    guard = _first_line(lambda l: "SHIFT_AGENT_DEPLOY_TEST_SANDBOX is forbidden" in l)
    cond = LINES[guard - 2]  # the `if [[ ... ]] && [[ -v ... ]]; then` line
    assert '[[ -v SHIFT_AGENT_DEPLOY_TEST_SANDBOX ]]' in cond, cond
    assert '"${BASH_SOURCE[0]}" == "$0"' in cond, cond


def test_guard_precedes_any_side_effect():
    guard = _first_line(lambda l: "-v SHIFT_AGENT_DEPLOY_TEST_SANDBOX" in l)
    mkdir = _first_line(lambda l: re.match(r"^\s*mkdir -p", l))
    install = _first_line(lambda l: 'install_artifacts "$STAGING"' in l)
    assert guard < mkdir < install, f"guard={guard} mkdir={mkdir} install={install}"


def test_deploy_script_never_assigns_the_sandbox_flag():
    for l in LINES:
        if l.strip().startswith("#"):
            continue
        assert not re.search(r"(?:export\s+)?SHIFT_AGENT_DEPLOY_TEST_SANDBOX\s*=", l), \
            f"deploy script assigns the sandbox flag: {l!r}"


def test_fd_battery_validates_real_filelock_descriptor():
    v = _extract_verifier()
    assert "from safe_io import FileLock" in v
    assert 'getattr(fl, "fd", None)' in v
    assert "os.fstat(fd)" in v and "os.lstat(lock)" in v
    assert "st_fd.st_dev, st_fd.st_ino) != (st_ln.st_dev, st_ln.st_ino)" in v  # dev+ino match
    assert "stat.S_ISREG(st_fd.st_mode)" in v
    assert "stat.S_ISLNK(st_ln.st_mode)" in v


def test_o_nofollow_preguard_present():
    v = _extract_verifier()
    assert "os.O_NOFOLLOW" in v
    # it is an ADDITIONAL pre-guard, not the acquisition (FileLock is the acquire)
    assert v.index("O_NOFOLLOW") < v.index("with FileLock(")


def test_signal_alarm_bound_present_and_documented():
    v = _extract_verifier()
    assert "signal.alarm(max(1, int(float(timeout_s)))" in v
    assert "signal.signal(signal.SIGALRM" in v
    assert "TIMEOUT=5" in _func_def_body()  # deploy-side bound


def test_production_passes_pre_acquire_hook_none():
    """Production call passes 'none' as the last verifier argv; the hook is
    test-only."""
    assert '"640,660" "none" <<' in _func_def_body()


def test_dual_identity_gate_on_euid_with_runuser():
    body = _func_def_body()
    assert '[ "$(id -u)" = "0" ]' in body  # gate on ACTUAL euid, not an env flag
    assert '_fd_identity_verify "root"' in body
    assert '_fd_identity_verify "gateway($OWNER)" runuser -u "$OWNER" --' in body


def test_existing_file_branch_has_no_chown_or_chmod():
    fn = _func_block()
    start = fn.index('if [ -e "$LOCK" ]; then')
    untouched = fn.index("left untouched")
    code = _code_lines(fn[start:untouched])
    assert "chown" not in code and "chmod" not in code
    assert "touch" not in code and '> "$LOCK"' not in code


def test_creation_branch_o_excl_and_mode_0660():
    fn = _func_block()
    assert "umask 007" in fn and "set -C" in fn and ': > "$LOCK"' in fn
    assert "chmod 0660" in fn and '"$n_mode" != "660"' in fn


def test_symlink_checked_before_e():
    fn = _func_block()
    assert fn.index('[ -L "$LOCK" ]') < fn.index('[ -e "$LOCK" ]')


def test_no_rm_of_canonical_lock_anywhere():
    offenders = [l for l in LINES if re.search(r"\brm\b.*approval-code-pools\.lock", l)]
    assert offenders == [], offenders


def test_no_bash_flock_across_activation():
    assert not re.search(r"\bflock\b[^\n]*approval-code-pools\.lock", TEXT)


def test_verifier_prints_fd_identity_evidence():
    v = _extract_verifier()
    assert "sys.executable" in v
    assert re.search(r"print\([^)]*safe_io\.__file__", v)
    assert re.search(r"print\([^)]*FileLock\.fd", v)
    assert "os.geteuid()" in v and "pwd.getpwuid" in v


# ════════════════════════════════════════════════════════════════════════════
# EXTRACT-AND-RUN — bash function with DISPOSABLE positional params (Linux-only)
# ════════════════════════════════════════════════════════════════════════════
_LINUX = pytest.mark.skipif(
    sys.platform == "win32",
    reason="extract-and-run needs bash + POSIX stat/chown + fcntl-capable python3 "
           "(GH CI ubuntu runs these; Windows dev box cannot)")


def _ids():
    import grp
    import pwd
    return (pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name)


def _run_init(tmp_path, *, lock, owner=None, group=None, platform_dir=None, pybin="python3"):
    user, ggroup = _ids()
    driver = tmp_path / "driver.sh"
    driver.write_text("set -euo pipefail\n" + _func_block()
                      + f'\ninitialize_approval_code_lock "{lock}" '
                        f'"{owner if owner is not None else user}" '
                        f'"{group if group is not None else ggroup}" '
                        f'"{pybin}" "{platform_dir or PLATFORM}"\n',
                      encoding="utf-8")
    return subprocess.run(["bash", str(driver)], capture_output=True, text=True)


@_LINUX
def test_absent_path_creates_owner_group_mode_0660(tmp_path):
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    r = _run_init(tmp_path, lock=lock)
    assert r.returncode == 0, r.stderr
    import grp
    import pwd
    st = os.stat(lock)
    assert not os.path.islink(lock) and os.path.isfile(lock)
    assert pwd.getpwuid(st.st_uid).pw_name == _ids()[0]
    assert grp.getgrgid(st.st_gid).gr_name == _ids()[1]
    assert oct(st.st_mode & 0o777) == oct(0o660)


@_LINUX
def test_safe_existing_left_untouched_inode_mtime_content(tmp_path):
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("preexisting")
    os.chmod(lock, 0o660)
    before, before_c = os.stat(lock), lock.read_text()
    r = _run_init(tmp_path, lock=lock)
    assert r.returncode == 0, r.stderr
    after = os.stat(lock)
    assert after.st_ino == before.st_ino and after.st_mtime == before.st_mtime
    assert lock.read_text() == before_c and "left untouched" in r.stdout


@_LINUX
def test_symlink_is_fatal(tmp_path):
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    (tmp_path / "target").write_text("x")
    os.symlink(tmp_path / "target", lock)
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
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    os.mkfifo(lock)
    r = _run_init(tmp_path, lock=lock)
    assert r.returncode != 0 and "NOT a regular file" in r.stderr


@_LINUX
def test_wrong_owner_is_fatal(tmp_path):
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.chmod(lock, 0o660)
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
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.chmod(lock, mode)
    r = _run_init(tmp_path, lock=lock)
    assert r.returncode != 0 and "mode" in r.stderr


@_LINUX
def test_acquire_verification_failure_is_fatal(tmp_path):
    """A stub FileLock whose __enter__ raises → deployment FAILS (no unlocked path)."""
    stub = tmp_path / "platform"
    stub.mkdir()
    (stub / "safe_io.py").write_text(
        "class FileLock:\n"
        "    def __init__(self, path): self.fd = None\n"
        "    def __enter__(self): raise RuntimeError('stub: cannot acquire')\n"
        "    def __exit__(self, *a): return False\n",
        encoding="utf-8")
    lock = tmp_path / "state" / "approval-code-pools.lock"
    lock.parent.mkdir(parents=True)
    r = _run_init(tmp_path, lock=lock, platform_dir=stub)
    assert r.returncode != 0
    assert "fd-identity verification FAILED" in r.stderr or "stub: cannot acquire" in (r.stderr + r.stdout)


# ════════════════════════════════════════════════════════════════════════════
# REAL-SCRIPT GUARD — sandbox var PRESENCE fails closed before any side effect
# ════════════════════════════════════════════════════════════════════════════
def _run_real_script(value):
    env = {**os.environ, "SHIFT_AGENT_DEPLOY_TEST_SANDBOX": value}
    return subprocess.run(["bash", str(DEPLOY), "deploy"], env=env,
                          capture_output=True, text=True)


@_LINUX
def test_real_script_rejects_sandbox_var_set_to_1():
    r = _run_real_script("1")
    assert r.returncode != 0
    assert "SHIFT_AGENT_DEPLOY_TEST_SANDBOX is forbidden" in r.stderr
    assert "OK: approval-code lock" not in r.stdout  # never reached lock init / install


@_LINUX
def test_real_script_rejects_sandbox_var_set_to_empty():
    r = _run_real_script("")  # presence, not value
    assert r.returncode != 0
    assert "SHIFT_AGENT_DEPLOY_TEST_SANDBOX is forbidden" in r.stderr


# ════════════════════════════════════════════════════════════════════════════
# ADVERSARIAL fd-INTERPOSITION — deterministic, NO sleeps (Linux-only)
# ════════════════════════════════════════════════════════════════════════════
def _run_verifier(tmp_path, *, lock, hook="none", platform_dir=None, owner=None, group=None):
    user, ggroup = _ids()
    vf = tmp_path / "verifier.py"
    vf.write_text(_extract_verifier(), encoding="utf-8")
    return subprocess.run(
        ["python3", str(vf), str(platform_dir or PLATFORM), str(lock), "5",
         owner if owner is not None else user, group if group is not None else ggroup,
         "640,660", hook],
        capture_output=True, text=True)


@_LINUX
def test_verifier_positive_valid_lock_passes(tmp_path):
    lock = tmp_path / "approval-code-pools.lock"
    lock.write_text("")
    os.chmod(lock, 0o660)
    r = _run_verifier(tmp_path, lock=lock, hook="none")
    assert r.returncode == 0, r.stderr
    assert "FileLock acquire/validate/release: OK" in r.stdout
    assert "FileLock.fd" in r.stdout


@_LINUX
def test_verifier_symlink_interposition_fatal(tmp_path):
    """Swap the path to a SYMLINK while the real FileLock holds its fd → refuse."""
    lock = tmp_path / "approval-code-pools.lock"
    lock.write_text("")
    os.chmod(lock, 0o660)
    (tmp_path / "elsewhere").write_text("")
    r = _run_verifier(tmp_path, lock=lock, hook=f"symlink:{tmp_path / 'elsewhere'}")
    assert r.returncode != 0
    assert "SYMLINK" in r.stderr or "inode" in r.stderr


@_LINUX
def test_verifier_swap_inode_interposition_fatal(tmp_path):
    """Swap the path to a DIFFERENT regular inode while the FileLock holds its fd
    → dev/ino mismatch → refuse (never locks the replacement)."""
    lock = tmp_path / "approval-code-pools.lock"
    lock.write_text("")
    os.chmod(lock, 0o660)
    other = tmp_path / "other-regular"
    other.write_text("")
    os.chmod(other, 0o660)
    r = _run_verifier(tmp_path, lock=lock, hook=f"swap:{other}")
    assert r.returncode != 0
    assert "inode" in r.stderr and "interposed swap" in r.stderr
