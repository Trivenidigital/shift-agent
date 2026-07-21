"""ops/deploy-preserve-timer-state — the deploy script must preserve the
operator's PRE-DEPLOYMENT enablement state of flyer-recovery-watchdog.timer
(#634, reviewer-ruled).

Incident (2026-07-21): the config-gated unit install ran an unconditional
`systemctl enable --now flyer-recovery-watchdog.timer`, silently reversing an
operator-ruled `systemctl disable --now`. The timer's service is broken by
foreign drop-ins, so re-enabling resumed a 5-min owner-alert storm. The guard
classifies by the PRINTED token of `systemctl is-enabled` (which prints
"disabled" with rc=1 — rc alone is NOT a failure signal) and:
  * enabled/enabled-runtime → today's behavior: enable --now + is-active verify.
  * disabled                → do NOT enable/start; verify still disabled+inactive.
  * anything else / query error → FATAL BEFORE any activation (fail-closed).

Two layers (mirrors test_deploy_retired_template.py):
  * STATIC assertions on the real shift-agent-deploy.sh text — run everywhere.
  * EXTRACT-AND-RUN of the guard bash function with a PATH-shimmed `systemctl`
    stub recording every invocation — Linux-only (needs a bare-name executable
    stub resolved via PATH + real chmod semantics + bash).

Scope is EXACTLY flyer-recovery-watchdog.timer — no other unit's enable line is
touched (asserted against a frozen snapshot below).
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
TEXT = DEPLOY.read_text(encoding="utf-8")
LINES = TEXT.splitlines()

_BEGIN = "# BEGIN ops/deploy-preserve-timer-state"
_END = "# END ops/deploy-preserve-timer-state"
FUNC = "preserve_or_enable_flyer_recovery_timer"
TIMER = "flyer-recovery-watchdog.timer"
ENABLE_CALL = f"systemctl enable --now {TIMER}"
QUERY_CALL = f"systemctl is-enabled {TIMER}"

# Frozen snapshot of every OTHER unit's `enable --now` line (stripped). This PR
# must not modify any of them; a future PR that legitimately adds/edits one
# updates this list deliberately (that is the point of the guard).
EXPECTED_OTHER_ENABLES = sorted([
    "systemctl enable --now shift-agent-tail-logger.timer 2>/dev/null || true",
    "systemctl enable --now shift-agent-health.timer 2>/dev/null || true",
    "systemctl enable --now shift-agent-health-watchdog.timer 2>/dev/null || true",
    "systemctl enable --now shift-agent-proposal-sweep.timer 2>/dev/null || true",
    "systemctl enable --now shift-agent-backup.timer 2>/dev/null || true",
    "systemctl enable --now shift-agent-fsck.timer 2>/dev/null || true",
    "systemctl enable --now send-daily-brief.timer 2>/dev/null || true",
    "systemctl enable --now catering-pattern-report.timer 2>/dev/null || true",
    "systemctl enable --now catering-owner-action-watchdog.service 2>/dev/null || true",
    "systemctl enable --now eod-reconcile.timer 2>/dev/null || true",
    "systemctl enable --now send-routing-accuracy-summary.timer 2>/dev/null || true",
    "systemctl enable --now flyer-source-edit-sla-watchdog.timer 2>/dev/null || true",
    "systemctl enable --now alert-integrity-watchdog.timer 2>/dev/null || true",
    "systemctl enable --now check-corrupt-state.timer 2>/dev/null || true",
    "systemctl enable --now prune-expense-receipts.timer 2>/dev/null || true",
    "systemctl enable --now check-compliance-deadlines.timer 2>/dev/null || true",
])


def _func_block() -> str:
    return TEXT[TEXT.index(_BEGIN):TEXT.index(_END)]


def _noncomment_lines():
    """(1-based lineno, stripped) for lines that are not pure comments."""
    return [(i, l.strip()) for i, l in enumerate(LINES, start=1)
            if not l.strip().startswith("#")]


def _first(pred) -> int:
    for i, ln in enumerate(LINES, start=1):
        if pred(ln):
            return i
    raise AssertionError("no line matched")


# ════════════════════════════════════════════════════════════════════════════
# STATIC (run everywhere)
# ════════════════════════════════════════════════════════════════════════════
def test_guard_lives_between_markers_and_defines_the_function():
    assert _BEGIN in TEXT and _END in TEXT, "guard BEGIN/END markers must be present"
    assert TEXT.index(_BEGIN) < TEXT.index(_END), "BEGIN must precede END"
    block = _func_block()
    assert f"{FUNC}() {{" in block, "the guard function must be defined inside the markers"


def test_only_flyer_enable_call_is_inside_the_guarded_enabled_branch():
    # Count only NON-comment occurrences (line 157 quotes the string in a comment).
    hits = [i for i, s in _noncomment_lines() if ENABLE_CALL in s]
    assert len(hits) == 1, f"expected exactly one real enable call, got lines {hits}"
    begin = _first(lambda l: l.startswith(_BEGIN))
    end = _first(lambda l: l.startswith(_END))
    assert begin < hits[0] < end, \
        f"the enable call (line {hits[0]}) must sit inside the guard block ({begin}..{end})"


def test_state_query_precedes_the_enable_call():
    q = min(i for i, s in _noncomment_lines() if QUERY_CALL in s)
    e = min(i for i, s in _noncomment_lines() if ENABLE_CALL in s)
    assert q < e, f"is-enabled query (line {q}) must precede the enable call (line {e})"


def test_no_other_units_enable_lines_were_modified():
    others = sorted(
        s for _, s in _noncomment_lines()
        if s.startswith("systemctl enable --now ") and TIMER not in s
    )
    assert others == EXPECTED_OTHER_ENABLES, (
        "the set of other-unit `enable --now` lines drifted from the frozen "
        "snapshot — this PR must touch only flyer-recovery-watchdog.timer.\n"
        f"unexpected/missing:\n  got={others}\n  want={EXPECTED_OTHER_ENABLES}")


def test_call_site_is_after_the_definition_in_the_install_path():
    end = _first(lambda l: l.startswith(_END))
    calls = [i for i, s in _noncomment_lines() if s == FUNC]
    assert calls, "the guard function must be CALLED (not only defined)"
    assert all(i > end for i in calls), \
        f"call site(s) {calls} must be after the guard definition (END at {end})"


def test_disabled_preserve_log_line_is_verbatim():
    block = _func_block()
    assert ("[timer-state] preserving operator-disabled state: "
            "flyer-recovery-watchdog.timer remains disabled") in block


def test_before_after_evidence_line_emitted():
    block = _func_block()
    assert "[timer-state] before=" in block and "after=" in block


def test_existing_fail_messages_preserved():
    block = _func_block()
    assert "FAIL: flyer-recovery-watchdog.timer enable/start failed" in block
    assert "FAIL: flyer-recovery-watchdog.timer not active after enable" in block


def test_fatal_paths_report_token_and_rc():
    block = _func_block()
    # the fail-closed branch must surface the captured token AND rc.
    assert "token=${before:-<empty>} rc=$rc" in block


def _bash_works() -> bool:
    """A functional bash — not merely present. On Windows the PATH `bash` is
    often the WSL relay (C:\\Windows\\System32\\bash.exe) with no distro behind
    it, which cannot exec; skip there rather than false-fail on an env artifact."""
    if shutil.which("bash") is None:
        return False
    try:
        return subprocess.run(["bash", "-c", "exit 0"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _bash_works(), reason="no functional bash on PATH")
def test_deploy_script_bash_n_parses():
    r = subprocess.run(["bash", "-n", str(DEPLOY)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n failed:\n{r.stderr}"


# ════════════════════════════════════════════════════════════════════════════
# EXTRACT-AND-RUN (Linux-only — bare-name systemctl stub resolved via PATH)
# ════════════════════════════════════════════════════════════════════════════
posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub-via-PATH needs a bare-name executable + real chmod + bash")

# systemctl stub: records every invocation ("$*") to $STUB_LOG, then answers
# is-enabled/enable/is-active from env. is-enabled prints the token (unless empty)
# and exits STUB_IS_ENABLED_RC — deliberately allowing "disabled" with rc=1.
_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
case "$1" in
    is-enabled)
        [ -n "$STUB_IS_ENABLED_TOKEN" ] && printf '%s\n' "$STUB_IS_ENABLED_TOKEN"
        exit "${STUB_IS_ENABLED_RC:-0}" ;;
    enable)   exit "${STUB_ENABLE_RC:-0}" ;;
    is-active) exit "${STUB_IS_ACTIVE_RC:-0}" ;;
    *) exit 0 ;;
esac
"""


def _run(tmp_path, *, token="enabled", is_enabled_rc=0, enable_rc=0,
         is_active_rc=0, n_calls=1):
    """Extract the guard function, call it n_calls times under `set -euo pipefail`
    with the systemctl stub on PATH. Returns (rc, stdout, stderr, invocations)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "systemctl"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "systemctl.log"
    runner = tmp_path / "run.sh"
    body = "\n".join([FUNC] * n_calls)
    runner.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + _func_block() + "\n" + body + "\n",
        encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    env["STUB_LOG"] = str(log)
    env["STUB_IS_ENABLED_TOKEN"] = token
    env["STUB_IS_ENABLED_RC"] = str(is_enabled_rc)
    env["STUB_ENABLE_RC"] = str(enable_rc)
    env["STUB_IS_ACTIVE_RC"] = str(is_active_rc)
    r = subprocess.run(["bash", str(runner)], capture_output=True, text=True, env=env)
    invocations = log.read_text(encoding="utf-8") if log.exists() else ""
    return r.returncode, r.stdout, r.stderr, invocations


def _subcommands(invocations: str):
    return [ln.split()[0] for ln in invocations.splitlines() if ln.strip()]


@posix_only
def test_enabled_before_enables_once_and_verifies(tmp_path):
    rc, out, err, inv = _run(tmp_path, token="enabled", is_enabled_rc=0,
                             enable_rc=0, is_active_rc=0)
    assert rc == 0, err
    subs = _subcommands(inv)
    assert subs.count("enable") == 1, f"enable must be called exactly once: {subs}"
    assert "is-active" in subs, "is-active verification must run"
    assert "[timer-state] before=enabled after=enabled" in out


@posix_only
def test_disabled_before_preserves_no_enable_no_start(tmp_path):
    rc, out, err, inv = _run(tmp_path, token="disabled", is_enabled_rc=1,
                             is_active_rc=1)  # is_active_rc=1 => inactive
    assert rc == 0, err
    subs = _subcommands(inv)
    assert "enable" not in subs, f"enable must NEVER be called when disabled: {subs}"
    assert "start" not in subs, f"start must NEVER be called when disabled: {subs}"
    assert ("[timer-state] preserving operator-disabled state: "
            "flyer-recovery-watchdog.timer remains disabled") in out
    assert "[timer-state] before=disabled after=disabled" in out


@posix_only
def test_disabled_but_active_is_fatal(tmp_path):
    # Defensive clause: is-enabled=disabled but the unit is ACTIVE → FATAL.
    rc, out, err, inv = _run(tmp_path, token="disabled", is_enabled_rc=1,
                             is_active_rc=0)  # is_active_rc=0 => active
    assert rc != 0
    assert "is ACTIVE" in err
    assert "enable" not in _subcommands(inv), "no activation before the fatal exit"


@posix_only
def test_missing_unit_is_fatal_before_activation(tmp_path):
    rc, out, err, inv = _run(tmp_path, token="not-found", is_enabled_rc=4)
    assert rc != 0
    assert "token=not-found rc=4" in err
    subs = _subcommands(inv)
    assert subs == ["is-enabled"], f"only the state query may run before FATAL: {subs}"


@posix_only
def test_masked_token_is_fatal_before_activation(tmp_path):
    rc, out, err, inv = _run(tmp_path, token="masked", is_enabled_rc=1)
    assert rc != 0
    assert "token=masked rc=1" in err
    assert "enable" not in _subcommands(inv)


@posix_only
def test_state_query_rc127_empty_output_is_fatal(tmp_path):
    # systemctl invocation error (command-not-found-shape): empty stdout, rc 127.
    rc, out, err, inv = _run(tmp_path, token="", is_enabled_rc=127)
    assert rc != 0
    assert "token=<empty> rc=127" in err
    assert _subcommands(inv) == ["is-enabled"]


@posix_only
def test_state_query_garbage_output_is_fatal(tmp_path):
    rc, out, err, inv = _run(tmp_path, token="garbage-output", is_enabled_rc=0)
    assert rc != 0
    assert "token=garbage-output rc=0" in err
    assert "enable" not in _subcommands(inv)


@posix_only
def test_start_failure_fires_existing_fail_path(tmp_path):
    # enabled-before, but enable --now fails → today's FAIL path, no silent pass.
    rc, out, err, inv = _run(tmp_path, token="enabled", is_enabled_rc=0, enable_rc=1)
    assert rc != 0
    assert "FAIL: flyer-recovery-watchdog.timer enable/start failed" in err
    assert _subcommands(inv).count("enable") == 1, "the enable was attempted then failed"


@posix_only
def test_rollback_shape_disabled_is_idempotent(tmp_path):
    # Re-running the install block a second time with a disabled timer still preserves.
    rc, out, err, inv = _run(tmp_path, token="disabled", is_enabled_rc=1,
                             is_active_rc=1, n_calls=2)
    assert rc == 0, err
    assert "enable" not in _subcommands(inv), "idempotent: never enables across reruns"
    assert out.count("preserving operator-disabled state") == 2
    assert out.count("[timer-state] before=disabled after=disabled") == 2
