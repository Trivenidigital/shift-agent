"""First-budget transactional bootstrap — rollback-proof + frozen-regression suite.

Covers `src/agents/shift/scripts/shift-agent-deploy.sh` actions `budget-bootstrap`
/ `un-budget-bootstrap` (design-v2 `first-budget-bootstrap-design-v2.md` "## Tests").

Two layers:

  * IN-PROCESS (cross-platform) — the frozen production-faithful marker regression
    and the C2 positive OFF-delivery lock, driven by the REAL `tools/patch-hermes.py`
    transforms (no bash). These lock the "installed budget code is byte-behaviour a
    no-op while the flag is unset" invariant and the exact pre/post marker shape.

  * DYNAMIC (Linux/bash) — the 10 rollback-proof scenarios drive the REAL
    `budget-bootstrap` ACTION branch end-to-end against temp Hermes + Shift roots via
    the `SHIFT_AGENT_BOOTSTRAP_TEST_SANDBOX` interface. Everything load-bearing is
    REAL: the Phase-0..8 orchestration, `patch-hermes._apply_wa_run`, the Hermes
    snapshot / sentinel / `revert_shift_tree` dual-tree rollback, the `rollback)`
    sub-transaction, and `bootstrap_verify_pre_budget_state`. Only the two steps that
    hardcode `/opt/shift-agent` + `/root/.hermes` (design §S7) are stubbed at their
    injection points — `install_artifacts` (a function override that models the Shift
    flat-safe_io re-lay) and `post_install_gates_and_restart` (an override that keeps
    the REAL failure contract: each gate failure runs the SAME `revert_shift_tree` +
    evict + `exit 1` cascade). `check-shift-agent-patch.sh`,
    `verify-artifact-runtime-closure.sh`, the smoke test, and `systemctl` are stubbed
    at their real call sites (staged files / PATH), each pass/fail-on-demand.

Every dynamic scenario is mutation-sensitive: the Hermes-side restore is REAL and the
assertions compare pre/post sha256 of the Hermes core, so neutering
`bootstrap_restore_hermes_snapshot` (the Hermes half of the dual-tree rollback) leaves
budget markers behind and fails the scenario — proven in the mutation harness (see the
module docstring's companion report). Scenarios that abort in Phase 1 assert NO
snapshot / NO sentinel / byte-identical Hermes (no mutation at all).
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.machinery
import importlib.util
import itertools
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()  # let safe_io import off-Linux (fcntl shim)

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
PATCH_HERMES = REPO / "tools" / "patch-hermes.py"

import safe_io  # noqa: E402  (conftest puts src/platform + src on sys.path)

CHAT = "15550100001@c.us"
_counter = itertools.count()


# ════════════════════════════════════════════════════════════════════════════
# Shared: load the REAL patch-hermes with HERMES_HOME bound; synthetic sources
# whose anchors the patcher requires (mirrors tests/test_turn_send_budget.py).
# ════════════════════════════════════════════════════════════════════════════
def _load_ph(home: Path):
    os.environ["HERMES_HOME"] = str(home)
    name = f"ph_bb_{next(_counter)}"
    loader = importlib.machinery.SourceFileLoader(name, str(PATCH_HERMES))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


# A minimal but faithful pre-patch adapter carrying every anchor patch-hermes needs:
# class ...Platform...:, `async def send(` + `formatted = self.format_message(content)`,
# `async def edit_message(` + `import aiohttp`.
_WA_BARE = '''"""synthetic gateway/platforms/whatsapp.py for the budget bootstrap tests."""


class BasePlatformAdapter:
    pass


class WhatsAppAdapter(BasePlatformAdapter):
    def __init__(self):
        self.relayed = []

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        if not content or not content.strip():
            return None
        try:
            import aiohttp
            formatted = self.format_message(content)
            self.relayed.append(("send", formatted))
            return {"status": "sent", "id": len(self.relayed)}
        except Exception as e:
            return str(e)

    async def edit_message(self, chat_id, message_id, content, *, finalize=False):
        try:
            import aiohttp
            _url = "http://127.0.0.1:9/edit"  # /edit relay anchor
            self.relayed.append(("edit", content, finalize))
            return {"status": "edited", "url": _url}
        except Exception as e:
            return str(e)

    def format_message(self, content):
        return content
'''

# Minimal gateway/run.py carrying: `import os`, `async def _prepare_inbound_message_text`,
# `if _is_shared_multi_user`.
_RUN_BARE = '''"""synthetic gateway/run.py for the budget bootstrap tests."""
import os


class _Gateway:
    async def _prepare_inbound_message_text(self, event, source, message_text):
        _is_shared_multi_user = False
        if _is_shared_multi_user and source.user_name:
            message_text = f"[{source.user_name}] {message_text}"
        return message_text
'''

_BRIDGE_JS = "// synthetic whatsapp-bridge/bridge.js (pre-budget; never touched)\n"

# RC safe_io carries the enforcing gate (Phase-1 F2 companion pre-check requires it).
_SAFE_IO_RC = (
    "# RC platform safe_io (carries the enforcing gate)\n"
    "def turn_send_budget_gate(chat_id, content, reserve_budget=True):\n"
    "    return None\n"
)
# Pre-budget safe_io must NOT define the gate (rollback / un-bootstrap guards key on it).
_SAFE_IO_PRE = "# pre-budget platform safe_io (no gate)\nVERSION = 'pre-budget'\n"


def _build_pre_budget_sources():
    """Return (wa_pre, run_pre): the REAL pre-budget Hermes shape — sender-id +
    front-brain applied, NO budget markers — built by the REAL patcher transforms."""
    ph = _load_ph(REPO)  # transforms operate on the passed text; home only names errors
    wa_pre = ph._apply_wa_front_brain(ph._apply_wa_sender_id(_WA_BARE))
    run_pre = ph._apply_run_sender_id(_RUN_BARE)
    return wa_pre, run_pre


def _marker_counts(text: str) -> dict:
    keys = ["sender-id", "front-brain-send", "front-brain-edit", "turn-send-budget",
            "turn-budget-sentinel", "turn-budget-send-drop", "turn-budget-edit-drop"]
    return {k: text.count("BEGIN shift-agent-" + k) for k in keys}


# ════════════════════════════════════════════════════════════════════════════
# IN-PROCESS (cross-platform): frozen production-faithful regression + C2
# ════════════════════════════════════════════════════════════════════════════
def test_frozen_pre_budget_shape_is_production_faithful():
    """Pre-budget Hermes shape == the frozen production counts (sender-id=2 in run.py,
    front-brain-send=2 / front-brain-edit=1 in whatsapp.py, budget=0), parses clean."""
    wa_pre, run_pre = _build_pre_budget_sources()
    wa = _marker_counts(wa_pre)
    run = _marker_counts(run_pre)
    assert run["sender-id"] == 2                 # flag block + inject
    assert wa["front-brain-send"] == 2           # module helper + send() inject
    assert wa["front-brain-edit"] == 1           # edit_message() inject
    assert wa["sender-id"] == 1                   # whatsapp helpers block
    # budget = 0 everywhere (this is a PRE-budget box)
    for m in ("turn-send-budget", "turn-budget-sentinel",
              "turn-budget-send-drop", "turn-budget-edit-drop"):
        assert wa[m] == 0 and run[m] == 0
    ast.parse(wa_pre)
    ast.parse(run_pre)


def test_frozen_bootstrap_patch_installs_budget_markers_off_preserving_front_brain():
    """Frozen regression (design "## Tests"): from the real pre-budget shape, the
    bootstrap's patch (`_transform_*`, the exact transform `_apply_wa_run` runs)
    installs ALL budget markers, preserves sender-id + front-brain byte-for-byte, and
    parses — the installed-but-OFF end state."""
    ph = _load_ph(REPO)
    wa_pre, run_pre = _build_pre_budget_sources()
    wa_post = ph._transform_whatsapp_py(wa_pre)
    run_post = ph._transform_run_py(run_pre)

    wa = _marker_counts(wa_post)
    run = _marker_counts(run_post)
    # every adapter budget marker landed...
    assert wa["turn-budget-sentinel"] == 1
    assert wa["turn-budget-send-drop"] == 1
    assert wa["turn-budget-edit-drop"] == 1
    # ...and the run.py boundary...
    assert run["turn-send-budget"] == 2
    # ...while sender-id + front-brain are byte-preserved (idempotent no-ops)...
    assert wa["front-brain-send"] == 2 and wa["front-brain-edit"] == 1 and wa["sender-id"] == 1
    assert run["sender-id"] == 2
    # the whole pre-budget front-brain helper text survives verbatim inside wa_post
    fb_helper = wa_pre[wa_pre.index("# BEGIN shift-agent-front-brain-send"):
                       wa_pre.index("# END shift-agent-front-brain-send")]
    assert fb_helper in wa_post
    ast.parse(wa_post)
    ast.parse(run_post)
    # OFF-passthrough of the enforcing gate (the installed code is a no-op while unset)
    os.environ.pop("GATEWAY_TURN_SEND_BUDGET_ENABLED", None)
    assert safe_io.turn_send_budget_gate(CHAT, "hi") is None


def _build_patched_adapter_class():
    """Apply the full bootstrap transform (`_transform_whatsapp_py`) in memory and exec
    it — no file write, no print (avoids the ✓ console-encode crash on win32). Returns
    the patched WhatsAppAdapter class whose send()/edit_message() carry the drop-check."""
    ph = _load_ph(REPO)
    src = ph._transform_whatsapp_py(_WA_BARE)
    ast.parse(src)
    ns: dict = {}
    exec(compile(src, "<patched-wa>", "exec"), ns)
    return ns["WhatsAppAdapter"]


def test_c2_positive_off_delivery_send_and_edit_are_relayed(monkeypatch):
    """C2 positive OFF-delivery lock: with GATEWAY_TURN_SEND_BUDGET_ENABLED unset, the
    patched adapter path DELIVERS (gate -> None -> content relayed), never drops. Locks
    the cross-component (patch x safe_io) OFF invariant end-to-end."""
    monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    monkeypatch.delenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", raising=False)
    monkeypatch.delenv("FRONT_BRAIN_OUTBOUND_ENFORCE", raising=False)
    monkeypatch.setattr(safe_io, "notify_owner_with_fallback", lambda *a, **k: True)
    adapter = _build_patched_adapter_class()()

    async def _run():
        # begin is a no-op when off; no per-turn context is set
        assert safe_io.begin_inbound_turn_send_budget() is None
        sends = [await adapter.send(CHAT, "reply") for _ in range(6)]
        edit = await adapter.edit_message(CHAT, "m", "draft", finalize=True)
        return sends, edit

    sends, edit = asyncio.run(_run())
    assert all(r is not None for r in sends), "OFF: every send must be delivered, not dropped"
    assert edit is not None, "OFF: the finalized edit must be delivered, not dropped"
    assert len(adapter.relayed) == 7, "transport relayed all 6 sends + 1 edit (nothing suppressed)"


# ════════════════════════════════════════════════════════════════════════════
# DYNAMIC (Linux/bash): the 10 rollback-proof scenarios + the success regression
# ════════════════════════════════════════════════════════════════════════════
def _tooling_ok() -> bool:
    # win32 CI (a bare Windows box) has no POSIX shell → skip cleanly. A Git-Bash /
    # MSYS dev box CAN drive these (bash + coreutils present); BOOTSTRAP_TESTS_FORCE_POSIX
    # opts such a box in for local verification (CI never sets it). Everything else keys
    # off actual exe presence, so Linux CI just runs them.
    if sys.platform == "win32" and os.environ.get("BOOTSTRAP_TESTS_FORCE_POSIX") != "1":
        return False
    import shutil
    # Use BOOTSTRAP_TEST_BASH for BOTH the presence probe and the exec probe (the real
    # scenario run already does). On win32 a bare "bash" resolves to WSL via System32,
    # which may be a broken relay stub — so a Git-Bash dev box that sets
    # BOOTSTRAP_TEST_BASH to its bash.exe is probed through that same bash, not WSL.
    bash_bin = os.environ.get("BOOTSTRAP_TEST_BASH", "bash")
    for exe in ("git", "tar", "sha256sum", "cp", "grep"):
        if shutil.which(exe) is None:
            return False
    if shutil.which(bash_bin) is None and not os.path.isfile(bash_bin):
        return False
    try:
        return subprocess.run([bash_bin, "-c", "exit 0"], capture_output=True).returncode == 0
    except OSError:
        return False


def _posix(p) -> str:
    """A forward-slash path string usable by both a POSIX shell and (on a Git-Bash dev
    box) the win32 Python invoked as the patch venv. Identity on Linux."""
    return Path(p).as_posix()


posix_only = pytest.mark.skipif(
    not _tooling_ok(),
    reason="dynamic bootstrap scenarios need bash + git + tar + sha256sum (GH CI ubuntu)")


# ── stub scripts (real call sites) ───────────────────────────────────────────
_SYSTEMCTL_STUB = r'''#!/usr/bin/env bash
# test systemctl: resolves `$SYSTEMCTL show -p Environment` (budget-off probe),
# `$SYSTEMCTL show -p MainPID --value` (B1 /proc probe — no running gateway in tests),
# and the literal `systemctl restart hermes-gateway` (fail-once on demand).
# list-unit-files returns non-zero so the cockpit block self-skips.
if [ "$1" = "show" ] && [ "$3" = "MainPID" ]; then
    echo ""   # no running gateway under test → the /proc/<pid>/environ check skips
    exit 0
fi
if [ "$1" = "show" ]; then
    if [ -n "${BOOTSTRAP_CTL:-}" ] && [ -f "$BOOTSTRAP_CTL/.ctl-budget-on" ]; then
        echo "Environment=GATEWAY_TURN_SEND_BUDGET_ENABLED=1"
    else
        echo "Environment="
    fi
    exit 0
fi
if [ "$1" = "restart" ] && [ "$2" = "hermes-gateway" ]; then
    if [ -n "${BOOTSTRAP_CTL:-}" ] && [ -f "$BOOTSTRAP_CTL/.ctl-restart-fail-once" ]; then
        rm -f "$BOOTSTRAP_CTL/.ctl-restart-fail-once"
        echo "TEST-STUB systemctl: restart hermes-gateway forced fail" >&2
        exit 1
    fi
    exit 0
fi
if [ "$1" = "list-unit-files" ]; then
    exit 1
fi
exit 0
'''

_SMOKE_STUB = r'''#!/usr/bin/env bash
if [ -n "${BOOTSTRAP_CTL:-}" ] && [ -f "$BOOTSTRAP_CTL/.ctl-smoke-fail-once" ]; then
    rm -f "$BOOTSTRAP_CTL/.ctl-smoke-fail-once"
    echo "TEST-STUB smoke: forced fail" >&2
    exit 1
fi
exit 0
'''

_NOTIFY_STUB = r'''#!/usr/bin/env bash
{ echo "NOTIFY $*"; } >> "${BOOTSTRAP_CTL:-.}/notify.log" 2>/dev/null || true
exit 0
'''

# Staged pin gate (Phase 5): a REAL consistency check — asserts the budget markers
# actually landed in the patched Hermes (design §S7 stub, but a truthful one).
_CHECK_PATCH_STUB = r'''#!/usr/bin/env bash
# rv-marker (6-scope): assert EVERY budget marker actually landed — the run.py boundary
# AND all three adapter markers AND the _SHIFT_DROP_SEND sentinel — so a partial-subset
# patch (which the real check-shift-agent-patch.sh rejects) fails this Phase-5 gate too.
H="${HERMES_HOME:-/root/.hermes/hermes-agent}"
RUN="$H/gateway/run.py"
WA="$H/gateway/platforms/whatsapp.py"
grep -q "BEGIN shift-agent-turn-send-budget" "$RUN" \
    || { echo "STUB pin-gate: run.py missing turn-send-budget marker" >&2; exit 1; }
for mk in turn-budget-sentinel turn-budget-send-drop turn-budget-edit-drop; do
    grep -q "BEGIN shift-agent-$mk" "$WA" \
        || { echo "STUB pin-gate: whatsapp.py missing $mk marker" >&2; exit 1; }
done
grep -q "_SHIFT_DROP_SEND = " "$WA" \
    || { echo "STUB pin-gate: whatsapp.py missing _SHIFT_DROP_SEND sentinel" >&2; exit 1; }
exit 0
'''

# Staged artifact-runtime closure gate (invoked by the post-install double exactly as
# the real function does: `bash <helper> <staging> <shift-root>`). Fail-once on demand.
_CLOSURE_STUB = r'''#!/usr/bin/env bash
if [ -n "${BOOTSTRAP_CTL:-}" ] && [ -f "$BOOTSTRAP_CTL/.ctl-closure-fail" ]; then
    rm -f "$BOOTSTRAP_CTL/.ctl-closure-fail"
    echo "TEST-STUB closure: forced fail" >&2
    exit 1
fi
exit 0
'''

# Injected BEFORE `case "$ACTION" in` so these definitions win over the real ones.
_INSTRUMENTATION = r'''
# ===== TEST INSTRUMENTATION (tests/test_budget_bootstrap.py) — design §S7 =====
# install_artifacts + post_install_gates_and_restart hardcode /opt/shift-agent,
# /root/.hermes, the Hermes venv, and /usr/local/bin gate scripts absent off-box, so
# both are overridden here. EVERYTHING ELSE is the real script: the budget-bootstrap /
# rollback / un-budget-bootstrap orchestration, patch-hermes, the Hermes snapshot /
# sentinel / revert_shift_tree dual-tree rollback, and bootstrap_verify_pre_budget_state.
install_artifacts() {
    local src_root="$1"
    if [ -f "$BOOTSTRAP_CTL/.ctl-install-fail-once" ]; then
        rm -f "$BOOTSTRAP_CTL/.ctl-install-fail-once"
        echo "TEST-STUB install_artifacts: forced failure" >&2
        return 1
    fi
    if [ -f "$BOOTSTRAP_CTL/.ctl-install-sigkill" ]; then
        rm -f "$BOOTSTRAP_CTL/.ctl-install-sigkill"
        echo "TEST-STUB install_artifacts: SIGKILL mid-transaction" >&2
        kill -KILL $$
    fi
    # Model the Shift-tree re-lay: install the staged RC (or rolled-back pre-budget)
    # safe_io flat into $SHIFT_ROOT so bootstrap_verify_pre_budget_state has a real
    # gate/no-gate signal to check.
    if [ -f "$src_root/src/platform/safe_io.py" ]; then
        cp -f "$src_root/src/platform/safe_io.py" "$SHIFT_ROOT/safe_io.py"
    fi
    echo "TEST-STUB install_artifacts: laid safe_io flat from $src_root"
    return 0
}
post_install_gates_and_restart() {
    # Faithful double: the real gate preamble hardcodes prod paths; this keeps the
    # REAL failure CONTRACT — every gate failure runs the SAME cascade the real gates
    # run: revert_shift_tree (the real dual-tree rollback) + evict tarball + exit 1.
    _tb_gate_fail() {
        echo "FAIL: post-install gate ($1) — refusing to restart hermes-gateway" >&2
        if [ "$PREV_TAG" != "none" ] && [ -f "$DEPLOYS_DIR/${PREV_TAG}.tgz" ]; then
            revert_shift_tree
            rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
        else
            rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"
        fi
        exit 1
    }
    # (5) compile/import gate
    if [ -f "$BOOTSTRAP_CTL/.ctl-import-fail" ]; then
        rm -f "$BOOTSTRAP_CTL/.ctl-import-fail"
        _tb_gate_fail import
    fi
    # (4) artifact-runtime closure gate — invoke the staged helper as the real fn does
    if [ -f "$STAGING/tools/verify-artifact-runtime-closure.sh" ]; then
        if ! bash "$STAGING/tools/verify-artifact-runtime-closure.sh" "$STAGING" "$SHIFT_ROOT"; then
            _tb_gate_fail closure
        fi
    fi
    # (6) restart — mirror the real function's restart sequence: the bootstrap-mode
    # restart_pending stamp (6-scope #1) then the BARE restart (point of no return; a
    # restart failure aborts under set -e WITHOUT inline rollback, leaving the sentinel
    # restart_pending so Phase-0 FINALIZES on the next run).
    if [ "$POST_INSTALL_MODE" = bootstrap ]; then
        bootstrap_stamp_restart_pending
    fi
    systemctl restart hermes-gateway
    # (7) smoke gate — the real function rolls back on smoke failure
    if ! "${TESTBIN}/shift-agent-smoke-test.sh"; then
        _tb_gate_fail smoke
    fi
    return 0
}
# ===== END TEST INSTRUMENTATION =====
'''


def _instrument(text: str) -> str:
    """Retarget the two prod-path steps + the venv/bin refs onto test-controlled paths,
    then inject the function overrides so they win over the real definitions."""
    text = text.replace("/usr/local/lib/hermes-agent/venv/bin/python", "${TEST_VENV_PY}")
    text = text.replace("/usr/local/bin/", "${TESTBIN}/")
    text = text.replace('case "$ACTION" in', _INSTRUMENTATION + '\ncase "$ACTION" in', 1)
    return text


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _wn(path: Path, text: str) -> Path:
    """Write with LF newlines (no OS translation) so byte-identity sha checks hold on
    a Git-Bash dev box too; identity with the default on Linux CI. bash cp round-trips
    the exact bytes, so a snapshot/restore of an LF fixture stays LF."""
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _write_exec(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


class Sandbox:
    """A temp Hermes tree (git repo, pre-budget shape) + Shift root wired for the
    budget-bootstrap test interface."""

    RC_COMMIT = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"  # staged .commit-hash / authorized

    def __init__(self, base: Path):
        self.base = base
        self.hermes = base / "hermes"
        self.shift = base / "shift"
        self.staging = self.shift / "staging-new"
        self.deploys = self.shift / "deploys"
        self.state = self.shift / "state"
        self.ctl = base / "ctl"
        self.testbin = base / "testbin"
        self.stubdir = base / "stubdir"
        self.prev_tag = "deploy-20260720-000000-prebudg"
        self._build()

    # ---- construction -------------------------------------------------------
    def _build(self):
        wa_pre, run_pre = _build_pre_budget_sources()
        self.wa_pre, self.run_pre = wa_pre, run_pre

        # Hermes core tree (git repo at the pinned commit).
        (self.hermes / "gateway" / "platforms").mkdir(parents=True)
        (self.hermes / "scripts" / "whatsapp-bridge").mkdir(parents=True)
        _wn(self.hermes / "gateway" / "run.py", run_pre)
        _wn(self.hermes / "gateway" / "platforms" / "whatsapp.py", wa_pre)
        bridge = _wn(self.hermes / "scripts" / "whatsapp-bridge" / "bridge.js", _BRIDGE_JS)
        self._git_init(self.hermes)
        self.pin_commit = self._git_head(self.hermes)
        self.bridge_sha = _sha(bridge)  # from on-disk bytes (== what sha256sum sees)

        # Shift root.
        self.state.mkdir(parents=True)
        self.deploys.mkdir(parents=True)
        self.ctl.mkdir(parents=True)
        self.testbin.mkdir(parents=True)
        self.stubdir.mkdir(parents=True)
        # business + timer state (must survive every transaction untouched)
        self.biz_file = self.state / "business-data.json"
        self.biz_file.write_text('{"orders": 3, "sacred": true}\n', encoding="utf-8")
        self.timer_file = self.state / "flyer-recovery-watchdog.state"
        self.timer_file.write_text("timer=armed\n", encoding="utf-8")
        # installed flat safe_io starts pre-budget (no gate) — the running box shape
        _wn(self.shift / "safe_io.py", _SAFE_IO_PRE)

        # Staging RC.
        (self.staging / "src" / "platform").mkdir(parents=True)
        (self.staging / "tools").mkdir(parents=True)
        _wn(self.staging / "src" / "platform" / "safe_io.py", _SAFE_IO_RC)
        (self.staging / ".commit-hash").write_text(self.RC_COMMIT + "\n", encoding="utf-8")
        # real patch-hermes + an env-gated mid-apply fault seam (inert unless
        # BOOTSTRAP_PATCH_FAULT=1 — the documented _write_text_atomic monkeypatch seam).
        staged_ph = PATCH_HERMES.read_text(encoding="utf-8") + _PATCH_FAULT_SEAM + _PATCH_S9_FAULT_SEAM
        (self.staging / "tools" / "patch-hermes.py").write_text(staged_ph, encoding="utf-8")
        _write_exec(self.staging / "tools" / "check-shift-agent-patch.sh", _CHECK_PATCH_STUB)
        _write_exec(self.staging / "tools" / "verify-artifact-runtime-closure.sh", _CLOSURE_STUB)
        (self.staging / "tools" / "hermes-patch-baseline.txt").write_text(
            f"HERMES_COMMIT={self.pin_commit}\nBRIDGE_POST_PATCH_SHA256={self.bridge_sha}\n",
            encoding="utf-8")

        # Pre-budget rollback tarball (Shift-tree revert anchor): src + tools + .commit-hash.
        prev_root = self.base / "prev_root"
        (prev_root / "src" / "platform").mkdir(parents=True)
        (prev_root / "tools").mkdir(parents=True)
        _wn(prev_root / "src" / "platform" / "safe_io.py", _SAFE_IO_PRE)
        _wn(prev_root / ".commit-hash", "prebudget0000\n")
        subprocess.run(["tar", "czf", str(self.deploys / f"{self.prev_tag}.tgz"),
                        "-C", str(prev_root), "src", "tools", ".commit-hash"], check=True)

        # Stub bins.
        _write_exec(self.stubdir / "systemctl", _SYSTEMCTL_STUB)
        _write_exec(self.testbin / "shift-agent-smoke-test.sh", _SMOKE_STUB)
        _write_exec(self.testbin / "shift-agent-notify-owner", _NOTIFY_STUB)

        # Instrumented deploy script.
        self.deploy = self.base / "shift-agent-deploy.sh"
        _write_exec(self.deploy, _instrument(DEPLOY.read_text(encoding="utf-8")))

    def _git_init(self, repo: Path):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        for cmd in (["git", "init", "-q"],
                    ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
                    ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                     "commit", "-q", "-m", "pre-budget baseline"]):
            subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)

    def _git_head(self, repo: Path) -> str:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()

    def bump_hermes_commit(self) -> str:
        """Advance the temp Hermes git HEAD (models an upstream Hermes upgrade after the
        pre-budget snapshot was captured). Returns the new HEAD."""
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "hermes upgrade"],
                       cwd=self.hermes, env=env, check=True, capture_output=True)
        return self._git_head(self.hermes)

    # ---- control markers ----------------------------------------------------
    def arm(self, name: str):
        (self.ctl / name).write_text("1", encoding="utf-8")

    # ---- run ----------------------------------------------------------------
    def run(self, *action, extra_env=None, baseline_pin=None):
        env = {
            **os.environ,
            "SHIFT_AGENT_BOOTSTRAP_TEST_SANDBOX": "1",
            "SHIFT_AGENT_BOOTSTRAP_TEST_ROOT": _posix(self.shift),
            "HERMES_HOME": _posix(self.hermes),
            "SHIFT_AGENT_SYSTEMCTL": _posix(self.stubdir / "systemctl"),
            "SHIFT_AGENT_BOOTSTRAP_STAGING": _posix(self.staging),
            "SHIFT_AGENT_BOOTSTRAP_DEPLOYS_DIR": _posix(self.deploys),
            # The C/D cross-boundary guards grep the CANONICAL Hermes run.py; point it at
            # the temp Hermes (marker-gated override) so those guards are testable.
            "SHIFT_AGENT_BOOTSTRAP_CANONICAL_HERMES_RUN": _posix(self.hermes / "gateway" / "run.py"),
            "BUDGET_BOOTSTRAP_AUTHORIZED_COMMIT": self.RC_COMMIT,
            "TEST_VENV_PY": _posix(sys.executable),
            "TESTBIN": _posix(self.testbin),
            "BOOTSTRAP_CTL": _posix(self.ctl),
            "PATH": _posix(self.stubdir) + os.pathsep + os.environ.get("PATH", ""),
            # so the win32 patch-venv emits utf-8 (its final ✓ line); no-op on Linux
            "PYTHONIOENCODING": "utf-8",
        }
        # Git-Bash portability: the in-script tar operands are Windows drive paths
        # (C:/…) which GNU tar's rsh heuristic reads as host=C. TAR_OPTIONS is honored by
        # GNU tar as leading options, so --force-local applies without disturbing the
        # old-style `czf` operand order. win32-only — Linux CI paths carry no drive letter.
        if sys.platform == "win32":
            env["TAR_OPTIONS"] = "--force-local"
        env.pop("GATEWAY_TURN_SEND_BUDGET_ENABLED", None)
        if baseline_pin is not None:  # scenario 8: sabotage the recorded pin
            (self.staging / "tools" / "hermes-patch-baseline.txt").write_text(
                f"HERMES_COMMIT={baseline_pin}\nBRIDGE_POST_PATCH_SHA256={self.bridge_sha}\n",
                encoding="utf-8")
        if extra_env:
            env.update(extra_env)
        # "bash" on Linux CI; a Git-Bash dev box overrides to its bash.exe absolute path
        # (Windows resolves a bare "bash" to WSL from System32 before PATH).
        bash_bin = os.environ.get("BOOTSTRAP_TEST_BASH", "bash")
        return subprocess.run([bash_bin, _posix(self.deploy), *action],
                              env=env, capture_output=True, text=True, timeout=120)

    # ---- state readers ------------------------------------------------------
    @property
    def run_py(self) -> Path:
        return self.hermes / "gateway" / "run.py"

    @property
    def wa_py(self) -> Path:
        return self.hermes / "gateway" / "platforms" / "whatsapp.py"

    @property
    def sentinel(self) -> Path:
        return self.state / ".budget-bootstrap-inprogress.json"

    @property
    def snapshot_dir(self) -> Path:
        return self.state / "budget-prebudget-hermes-snapshot"

    def sentinel_json(self) -> dict:
        import json
        return json.loads(self.sentinel.read_text(encoding="utf-8"))

    def sentinel_state(self) -> str:
        return self.sentinel_json().get("state", "")

    def sentinel_new_tag(self) -> str:
        return self.sentinel_json().get("new_tag", "")

    def hermes_is_pre_budget(self) -> bool:
        run_t = self.run_py.read_text(encoding="utf-8")
        wa_t = self.wa_py.read_text(encoding="utf-8")
        return (
            _sha(self.run_py) == hashlib.sha256(self.run_pre.encode("utf-8")).hexdigest()
            and _sha(self.wa_py) == hashlib.sha256(self.wa_pre.encode("utf-8")).hexdigest()
            and "BEGIN shift-agent-turn-send-budget" not in run_t
            and "BEGIN shift-agent-turn-budget-sentinel" not in wa_t
            and "BEGIN shift-agent-front-brain-send" in wa_t      # front-brain intact
            and "BEGIN shift-agent-sender-id" in run_t            # sender-id intact
        )

    def assert_clean_rollback(self):
        """Both trees restored to EXACT pre-budget, no partial markers, no mixed tree,
        budget OFF, front-brain intact, sentinel cleared, business/timer state pristine."""
        assert self.hermes_is_pre_budget(), "Hermes core not byte-restored to pre-budget"
        assert not self.sentinel.exists(), "in-progress sentinel not cleared after rollback"
        # Shift flat safe_io reverted to the pre-budget (no-gate) shape
        assert "def turn_send_budget_gate" not in (self.shift / "safe_io.py").read_text(encoding="utf-8")
        self.assert_state_untouched()

    def assert_no_mutation(self):
        """Phase-1 abort: no snapshot, no sentinel, Hermes byte-identical pre-budget."""
        assert not self.sentinel.exists(), "sentinel written despite a Phase-1 abort"
        assert not self.snapshot_dir.exists(), "Hermes snapshot taken despite a Phase-1 abort"
        assert _sha(self.run_py) == hashlib.sha256(self.run_pre.encode("utf-8")).hexdigest()
        assert _sha(self.wa_py) == hashlib.sha256(self.wa_pre.encode("utf-8")).hexdigest()
        self.assert_state_untouched()

    def assert_state_untouched(self):
        assert self.biz_file.read_text(encoding="utf-8") == '{"orders": 3, "sacred": true}\n'
        assert self.timer_file.read_text(encoding="utf-8") == "timer=armed\n"


# The env-gated mid-apply write-fault seam appended to the STAGED patch-hermes.py. It
# re-binds the module's `_write_text_atomic` (which `_apply_wa_run` looks up as a global
# at call time) to fail AFTER the first target is written — the exact "patch fails
# midway" fault the patcher's own all-or-nothing restore is built for (per its docstring).
# Inert unless BOOTSTRAP_PATCH_FAULT=1, so all other scenarios run the pure real patcher.
_PATCH_FAULT_SEAM = '''

# ---- TEST-ONLY env-gated mid-apply fault seam (tests/test_budget_bootstrap.py) ----
if os.environ.get("BOOTSTRAP_PATCH_FAULT") == "1":
    _bootstrap_real_wta = _write_text_atomic
    _bootstrap_fault_n = [0]

    def _write_text_atomic(path, text):  # noqa: F811
        _bootstrap_fault_n[0] += 1
        _bootstrap_real_wta(path, text)
        if _bootstrap_fault_n[0] == 1:
            raise OSError("BOOTSTRAP_PATCH_FAULT: simulated mid-apply write fault after first target")
'''


# S9 seam: drive the patcher's `PatchError: rollback INCOMPLETE` branch (the hardened
# except that surfaces a restore that itself fails). WA is written + APPENDED to
# `written`, the SECOND write (RUN) faults, then the all-or-nothing restore of WA is
# blocked so `restore_failures` is non-empty and _apply_wa_run raises the INCOMPLETE
# PatchError. The bootstrap's Phase-3 snapshot restore then reverts the half-patched
# Hermes. Inert unless BOOTSTRAP_PATCH_S9_FAULT=1.
_PATCH_S9_FAULT_SEAM = '''

# ---- TEST-ONLY env-gated S9 restore-incomplete seam (tests/test_budget_bootstrap.py) ----
if os.environ.get("BOOTSTRAP_PATCH_S9_FAULT") == "1":
    import pathlib as _s9_pl
    _s9_real_wta = _write_text_atomic
    _s9_n = [0]

    def _write_text_atomic(path, text):  # noqa: F811
        _s9_n[0] += 1
        if _s9_n[0] == 1:
            _s9_real_wta(path, text)   # WA written + appended to `written`
            return
        # 2nd target (RUN): fault BEFORE writing so `written` == [WA] and the restore runs
        raise OSError("BOOTSTRAP_PATCH_S9_FAULT: simulated write fault on RUN")

    _s9_real_write_text = _s9_pl.Path.write_text

    def _s9_write_text(self, *a, **k):
        # once the RUN fault has fired, the all-or-nothing restore calls WA.write_text —
        # block THAT so restore_failures is non-empty and the INCOMPLETE branch fires.
        if _s9_n[0] >= 2:
            raise OSError("BOOTSTRAP_PATCH_S9_FAULT: restore write blocked")
        return _s9_real_write_text(self, *a, **k)

    _s9_pl.Path.write_text = _s9_write_text
'''


@pytest.fixture
def sandbox(tmp_path):
    if not _tooling_ok():
        pytest.skip("dynamic bootstrap scenarios need bash + git + tar + sha256sum")
    return Sandbox(tmp_path)


# ── the frozen production-faithful SUCCESS regression (dynamic) ──────────────
@posix_only
def test_dynamic_bootstrap_success_installs_budget_off(sandbox):
    r = sandbox.run("budget-bootstrap")
    assert r.returncode == 0, f"bootstrap should succeed\nSTDOUT:{r.stdout}\nSTDERR:{r.stderr}"
    run_t = sandbox.run_py.read_text(encoding="utf-8")
    wa_t = sandbox.wa_py.read_text(encoding="utf-8")
    # all budget markers installed
    assert wa_t.count("BEGIN shift-agent-turn-budget-sentinel") == 1
    assert wa_t.count("BEGIN shift-agent-turn-budget-send-drop") == 1
    assert wa_t.count("BEGIN shift-agent-turn-budget-edit-drop") == 1
    assert run_t.count("BEGIN shift-agent-turn-send-budget") == 2
    # front-brain + sender-id preserved
    assert wa_t.count("BEGIN shift-agent-front-brain-send") == 2
    assert "BEGIN shift-agent-sender-id" in run_t
    ast.parse(run_t)
    ast.parse(wa_t)
    # RC safe_io installed flat (the enforcing gate present) — installed-but-OFF
    assert "def turn_send_budget_gate" in (sandbox.shift / "safe_io.py").read_text(encoding="utf-8")
    # sentinel cleared; pre-budget snapshot RETAINED for un-budget-bootstrap
    assert not sandbox.sentinel.exists()
    assert sandbox.snapshot_dir.exists()
    sandbox.assert_state_untouched()


# ── Scenario 1 — patch fails midway (Phase 3) → Hermes snapshot restore ──────
@posix_only
def test_scenario_01_patch_fails_midway_restores_hermes(sandbox):
    r = sandbox.run("budget-bootstrap", extra_env={"BOOTSTRAP_PATCH_FAULT": "1"})
    assert r.returncode != 0
    # The fault fires mid-FIRST-write (after WA is written, before it is appended to
    # `written`), so patch-hermes's own all-or-nothing restore loop has nothing to undo —
    # the bootstrap Phase-3 SNAPSHOT restore is what reverts the half-written Hermes to
    # byte-identical pre-budget. The Shift tree was never touched.
    assert sandbox.hermes_is_pre_budget(), r.stdout + r.stderr
    assert not sandbox.sentinel.exists()
    sandbox.assert_state_untouched()


# ── Scenario 2 — partial budget markers already present → Phase-1 refusal ────
@posix_only
def test_scenario_02_partial_markers_refused_no_mutation(sandbox):
    # a prior crashed patch left a partial budget marker in run.py
    partial = sandbox.run_pre + "\n# BEGIN shift-agent-turn-send-budget\n# END shift-agent-turn-send-budget\n"
    sandbox.run_py.write_text(partial, encoding="utf-8")
    before = _sha(sandbox.run_py)
    r = sandbox.run("budget-bootstrap")
    assert r.returncode != 0
    assert "already carries the budget patch" in (r.stdout + r.stderr)
    # refused before any mutation: run.py unchanged, no NEW markers, no sentinel/snapshot
    assert _sha(sandbox.run_py) == before
    assert sandbox.run_py.read_text(encoding="utf-8").count("BEGIN shift-agent-turn-send-budget") == 1
    assert not sandbox.sentinel.exists()
    assert not sandbox.snapshot_dir.exists()
    sandbox.assert_state_untouched()


# ── Scenario 3 — install_artifacts fails (Phase 4) → dual-tree rollback ──────
@posix_only
def test_scenario_03_install_fails_dual_tree_rollback(sandbox):
    sandbox.arm(".ctl-install-fail-once")
    r = sandbox.run("budget-bootstrap")
    assert r.returncode != 0
    assert sandbox.hermes_is_pre_budget(), r.stdout + r.stderr
    sandbox.assert_clean_rollback()


# ── Scenario 4 — closure gate fails (Phase 5/6) → dual-tree rollback ─────────
@posix_only
def test_scenario_04_closure_gate_fails_dual_tree_rollback(sandbox):
    sandbox.arm(".ctl-closure-fail")
    r = sandbox.run("budget-bootstrap")
    assert r.returncode != 0
    assert "closure" in (r.stdout + r.stderr)
    sandbox.assert_clean_rollback()


# ── Scenario 5 — compile/import gate fails → dual-tree rollback ──────────────
@posix_only
def test_scenario_05_import_gate_fails_dual_tree_rollback(sandbox):
    sandbox.arm(".ctl-import-fail")
    r = sandbox.run("budget-bootstrap")
    assert r.returncode != 0
    assert "import" in (r.stdout + r.stderr)
    sandbox.assert_clean_rollback()


# ── Scenario 6 — gateway restart fails (PONR) → restart_pending → Phase-0 FINALIZES ──
@posix_only
def test_scenario_06_restart_fails_stamps_restart_pending_then_phase0_finalizes(sandbox):
    """6-scope #1: a restart-COMMAND fault at the point of no return leaves the tree
    fully installed + consistent (post-budget, budget OFF) with the sentinel stamped
    restart_pending — NOT rewound. Re-running budget-bootstrap FINALIZES (completes the
    deferred restart), it does NOT rewind a healthy deploy."""
    sandbox.arm(".ctl-restart-fail-once")
    r = sandbox.run("budget-bootstrap")
    assert r.returncode != 0, r.stdout
    # Restart fault: tree post-budget-consistent, sentinel stamped restart_pending.
    assert sandbox.sentinel.exists(), "restart-fault must leave the sentinel"
    assert sandbox.sentinel_state() == "restart_pending", sandbox.sentinel_json()
    assert "BEGIN shift-agent-turn-send-budget" in sandbox.run_py.read_text(encoding="utf-8")
    assert "def turn_send_budget_gate" in (sandbox.shift / "safe_io.py").read_text(encoding="utf-8")
    assert "RESTART_PENDING" in (r.stdout + r.stderr) or "restart_pending" in (r.stdout + r.stderr)
    # Re-run → Phase-0 FINALIZES (the fail-once seam is spent, so the restart now succeeds).
    r2 = sandbox.run("budget-bootstrap")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "finalized" in (r2.stdout + r2.stderr).lower()
    assert not sandbox.sentinel.exists(), "finalize must clear the sentinel"
    # The healthy deploy was NOT rewound: budget markers + gate + front-brain all remain.
    run_t = sandbox.run_py.read_text(encoding="utf-8")
    assert "BEGIN shift-agent-turn-send-budget" in run_t
    assert "BEGIN shift-agent-front-brain-send" in sandbox.wa_py.read_text(encoding="utf-8")
    assert "def turn_send_budget_gate" in (sandbox.shift / "safe_io.py").read_text(encoding="utf-8")
    sandbox.assert_state_untouched()


# ── Scenario 7 — smoke fails (Phase 6) → dual-tree rollback ──────────────────
@posix_only
def test_scenario_07_smoke_fails_dual_tree_rollback(sandbox):
    sandbox.arm(".ctl-smoke-fail-once")
    r = sandbox.run("budget-bootstrap")
    assert r.returncode != 0
    assert "smoke" in (r.stdout + r.stderr)
    sandbox.assert_clean_rollback()


# ── Scenario 8 — wrong Hermes pin → Phase-1 refusal, no mutation ────────────
@posix_only
def test_scenario_08_wrong_hermes_pin_refused_no_mutation(sandbox):
    r = sandbox.run("budget-bootstrap", baseline_pin="0" * 40)
    assert r.returncode != 0
    assert "pinned commit" in (r.stdout + r.stderr)
    sandbox.assert_no_mutation()


# ── Scenario 9 — stale / missing staged safe_io → Phase-1 refusal ───────────
@posix_only
def test_scenario_09a_stale_staged_safe_io_refused_no_mutation(sandbox):
    # staged safe_io lacks the enforcing gate (stale RC)
    (sandbox.staging / "src" / "platform" / "safe_io.py").write_text(_SAFE_IO_PRE, encoding="utf-8")
    r = sandbox.run("budget-bootstrap")
    assert r.returncode != 0
    assert "turn_send_budget_gate" in (r.stdout + r.stderr)
    sandbox.assert_no_mutation()


@posix_only
def test_scenario_09b_missing_staged_safe_io_refused_no_mutation(sandbox):
    (sandbox.staging / "src" / "platform" / "safe_io.py").unlink()
    r = sandbox.run("budget-bootstrap")
    assert r.returncode != 0
    sandbox.assert_no_mutation()


# ── Scenario 10 — SIGKILL mid-transaction → Phase-0 REWIND restores both (+ #2 evict) ──
@posix_only
def test_scenario_10_sigkill_midtransaction_phase0_rewinds_and_evicts_new_tag(sandbox):
    sandbox.arm(".ctl-install-sigkill")
    r = sandbox.run("budget-bootstrap")
    # hard-killed mid Phase-4 (BEFORE the restart stamp): Hermes PATCHED, sentinel present
    # with state=in_progress and new_tag recorded (stamped just before install).
    assert r.returncode != 0
    assert sandbox.sentinel.exists(), "SIGKILL must leave the in_progress sentinel"
    assert sandbox.sentinel_state() == "in_progress", sandbox.sentinel_json()
    assert "BEGIN shift-agent-turn-send-budget" in sandbox.run_py.read_text(encoding="utf-8")
    # #2: the abandoned post-budget RC tarball is on disk + recorded in the sentinel.
    new_tag = sandbox.sentinel_new_tag()
    assert new_tag, sandbox.sentinel_json()
    new_tarball = sandbox.deploys / f"{new_tag}.tgz"
    assert new_tarball.exists(), "the RC tarball should exist after the killed Phase 4"
    # Re-run → Phase-0 REWINDS both trees to pre-budget AND evicts the abandoned tarball.
    r2 = sandbox.run("budget-bootstrap")
    assert r2.returncode != 0
    assert "rewind" in (r2.stdout + r2.stderr).lower() or "recover" in (r2.stdout + r2.stderr).lower()
    sandbox.assert_clean_rollback()
    assert not new_tarball.exists(), "#2: the abandoned post-budget RC tarball must be evicted on rewind"


# ── un-budget-bootstrap coupled reverse-transaction (design §S6/1c) ─────────
@posix_only
def test_un_budget_bootstrap_couples_the_reverse_transaction(sandbox):
    assert sandbox.run("budget-bootstrap").returncode == 0
    assert "BEGIN shift-agent-turn-send-budget" in sandbox.run_py.read_text(encoding="utf-8")
    r = sandbox.run("un-budget-bootstrap", sandbox.prev_tag)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    # Hermes reverted to pre-budget from the retained snapshot; Shift rolled back.
    assert sandbox.hermes_is_pre_budget(), r.stdout + r.stderr
    assert "def turn_send_budget_gate" not in (sandbox.shift / "safe_io.py").read_text(encoding="utf-8")
    sandbox.assert_state_untouched()


# ════════════════════════════════════════════════════════════════════════════
# 6-scope review fix batch — added coverage
# ════════════════════════════════════════════════════════════════════════════

# ── rv-patch: S9 `PatchError: rollback INCOMPLETE` branch ────────────────────
@posix_only
def test_scenario_s9_patch_restore_incomplete_surfaces_then_snapshot_reverts(sandbox):
    """The patcher's hardened restore fails (a restore write is blocked), so
    `_apply_wa_run` raises `PatchError: rollback INCOMPLETE` and surfaces the
    half-patched target to stderr (never swallowed). The bootstrap Phase-3 snapshot
    restore then reverts Hermes to byte-identical pre-budget."""
    r = sandbox.run("budget-bootstrap", extra_env={"BOOTSTRAP_PATCH_S9_FAULT": "1"})
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "rollback INCOMPLETE" in out, out          # the S9 branch fired
    assert "left in PATCHED state" in out or "left PATCHED" in out, out
    assert sandbox.hermes_is_pre_budget(), out         # snapshot restore reverted both files
    assert not sandbox.sentinel.exists()
    sandbox.assert_state_untouched()


# ── B1: budget flag armed in .env (EnvironmentFile) → Phase-1 refusal, no mutation ──
@posix_only
def test_b1_budget_armed_in_env_file_refused_no_mutation(sandbox):
    """`systemctl show -p Environment` does NOT expand EnvironmentFile=, so the flag is
    armed in $SHIFT_ROOT/.env. bootstrap_assert_budget_off must still catch it and
    refuse in Phase 1 BEFORE any mutation."""
    (sandbox.shift / ".env").write_text(
        "SOME_OTHER=x\nGATEWAY_TURN_SEND_BUDGET_ENABLED=1\n", encoding="utf-8")
    r = sandbox.run("budget-bootstrap")
    assert r.returncode != 0
    assert "GATEWAY_TURN_SEND_BUDGET_ENABLED=1" in (r.stdout + r.stderr) \
        or "budget" in (r.stdout + r.stderr).lower()
    sandbox.assert_no_mutation()


# ── C: deploy) onto a budget-patched box with a stale pre-gate artifact → refusal ──
@posix_only
def test_c_deploy_stale_pregate_artifact_onto_budget_box_refused(sandbox):
    """A plain `deploy` of a PRE-gate artifact (staged safe_io lacks the gate) onto a
    box whose canonical Hermes already carries the budget patch would strand new-Shift
    (no gate) against the budget-patched adapter — a mixed tree. The deploy) guard (fix
    C) must refuse, keyed on the CANONICAL Hermes run.py."""
    # canonical (temp) Hermes carries the budget patch
    sandbox.run_py.write_text(
        sandbox.run_pre + "\n# BEGIN shift-agent-turn-send-budget\n# END shift-agent-turn-send-budget\n",
        encoding="utf-8")
    # staged artifact is stale/pre-gate (safe_io lacks the enforcing gate)
    (sandbox.staging / "src" / "platform" / "safe_io.py").write_text(_SAFE_IO_PRE, encoding="utf-8")
    r = sandbox.run("deploy")
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "mixed tree" in out and "un-budget-bootstrap" in out, out


# ── rv-rollback #1: rollback) refusal path (pre-budget tag while Hermes is patched) ──
@posix_only
def test_rollback_refuses_pre_budget_tag_while_hermes_patched(sandbox):
    """After a successful bootstrap the canonical Hermes carries budget markers; a plain
    `rollback` to a PRE-budget tarball (whose safe_io lacks the gate) would create a
    mixed tree, so the rollback) guard (fix D) must refuse and point at un-budget-bootstrap."""
    assert sandbox.run("budget-bootstrap").returncode == 0
    assert "BEGIN shift-agent-turn-send-budget" in sandbox.run_py.read_text(encoding="utf-8")
    r = sandbox.run("rollback", sandbox.prev_tag)
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "predates the budget boundary" in out or "MIXED tree" in out, out
    assert "un-budget-bootstrap" in out, out


# ── A: test-sandbox marker must not target production paths ──────────────────
@posix_only
def test_a_marker_targeting_production_paths_is_fatal(tmp_path):
    """The SHIFT_AGENT_BOOTSTRAP_TEST_SANDBOX marker must drive TEMP roots only. If it is
    set while SHIFT_ROOT (or HERMES_HOME) still resolves to a production path, the script
    FATALs before any dispatch — so an accidentally-set marker can never drive a real box."""
    bash_bin = os.environ.get("BOOTSTRAP_TEST_BASH", "bash")
    env = {
        **os.environ,
        "SHIFT_AGENT_BOOTSTRAP_TEST_SANDBOX": "1",
        # HERMES_HOME points at a temp path so ONLY the SHIFT_ROOT==/opt/shift-agent
        # condition triggers (SHIFT_AGENT_BOOTSTRAP_TEST_ROOT is deliberately NOT set).
        "HERMES_HOME": _posix(tmp_path / "hermes"),
    }
    for k in ("SHIFT_AGENT_BOOTSTRAP_TEST_ROOT", "SHIFT_AGENT_BOOTSTRAP_STAGING",
              "SHIFT_AGENT_BOOTSTRAP_DEPLOYS_DIR", "SHIFT_AGENT_SYSTEMCTL",
              "SHIFT_AGENT_BOOTSTRAP_CANONICAL_HERMES_RUN"):
        env.pop(k, None)
    r = subprocess.run([bash_bin, _posix(DEPLOY), "list"],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "must target temp roots" in (r.stdout + r.stderr), r.stdout + r.stderr


# ── #4: un-budget-bootstrap refuses to restore a snapshot onto an upgraded Hermes ──
@posix_only
def test_un_budget_refuses_when_hermes_upgraded_since_snapshot(sandbox):
    """The retained pre-budget snapshot was captured against a specific Hermes commit; if
    Hermes is upgraded before un-budget-bootstrap runs, restoring the stale run.py/whatsapp.py
    would DOWNGRADE the gateway — so un-budget must refuse (pin-check), leaving Hermes as-is."""
    assert sandbox.run("budget-bootstrap").returncode == 0
    new_head = sandbox.bump_hermes_commit()   # simulate an upstream Hermes upgrade
    r = sandbox.run("un-budget-bootstrap", sandbox.prev_tag)
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "upgraded since" in out or "DOWNGRADE" in out, out
    # Hermes was NOT downgraded: HEAD is still the upgraded commit; budget markers remain.
    assert sandbox._git_head(sandbox.hermes) == new_head
    assert "BEGIN shift-agent-turn-send-budget" in sandbox.run_py.read_text(encoding="utf-8")


# ── #6: budget-bootstrap Phase-5 wires the shared env-symlink gate to the coupled revert ──
def test_bootstrap_phase5_runs_env_symlink_integrity_gate_wired_to_revert():
    """The budget-bootstrap Phase-5 sequence runs the SAME env-symlink integrity check
    deploy) runs (shared `check_env_symlink_integrity`), fail-closing to the coupled
    dual-tree revert. Static/cross-platform: MSYS cannot create the real symlink the gate
    needs, so the sandbox skips the gate at runtime; this asserts the wiring itself."""
    text = DEPLOY.read_text(encoding="utf-8")
    assert "check_env_symlink_integrity()" in text                       # shared function exists
    # deploy) reuses it (factored, not duplicated)
    assert 'check_env_symlink_integrity "/opt/shift-agent/.env" "/root/.hermes/.env" || exit 1' in text
    # bootstrap Phase-5 slice: env-check wired to revert_shift_tree on failure
    i = text.index("budget-bootstrap Phase 5: env symlink integrity gate")
    seg = text[i:i + 500]
    assert "check_env_symlink_integrity" in seg
    assert "revert_shift_tree" in seg
