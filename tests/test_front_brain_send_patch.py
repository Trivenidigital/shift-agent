"""Installer-correctness tests for the Hermes-core turn-budget + front-brain patch
(tools/patch-hermes.py) and the deploy gate (tools/check-shift-agent-patch.sh).

The turn-budget ADAPTER pieces — the ``_SHIFT_DROP_SEND`` sentinel + budget screen,
the ``send()`` drop-check, and the ``edit_message()`` drop-check — each carry their
OWN marker, INDEPENDENT of ``shift-agent-front-brain-send``, so the volume cap
installs on a tree that ALREADY carries the front-brain screen (= production). That
independence is the #643 defect this fixes: the old bundled shape skipped the whole
cap when the front-brain-send marker was already present.

whatsapp.py + run.py are patched ALL-OR-NOTHING: staged in memory, validated
(ast.parse + every required marker), then written atomically with rollback — a
missing anchor or a mid-apply write fault leaves EVERY target byte-identical.

We synthesize the minimal anchor shapes patch-hermes.py keys on and assert:
independent-marker install across every prior state, byte-idempotency, the all-or-
nothing guarantees, the inserted helpers' fail-open (content screen) / fail-closed
(budget) semantics + reserve_budget threading, and the deploy-gate marker+proximity
predicates.

Pure text/ast (patch-hermes.py has no fcntl) -> Windows + Docker. The bash predicate
tests skip when a POSIX bash is unavailable.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.machinery
import importlib.util
import itertools
import os
import platform
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PATCH_HERMES = REPO / "tools" / "patch-hermes.py"
GATE_SCRIPT = REPO / "tools" / "check-shift-agent-patch.sh"

_counter = itertools.count()

# The edit_message method carrying the edit anchors (`import aiohttp` + `/edit"`).
_EDIT_METHOD = '''    async def edit_message(self, chat_id, message_id, content, *, finalize=False):
        try:
            import aiohttp
            async with self._http_session.post(
                f"http://127.0.0.1:9/edit",
                json={"chatId": chat_id, "message": content},
            ) as resp:
                return None
        except Exception as e:
            return str(e)

'''

# Minimal whatsapp.py carrying the exact anchor shapes patch-hermes.py keys on.
GOOD_SNIPPET = '''"""stub gateway adapter for patch tests."""


class BasePlatformAdapter:
    pass


class WhatsAppAdapter(BasePlatformAdapter):
    async def send(self, chat_id, content, reply_to=None, metadata=None):
        if not content or not content.strip():
            return None
        try:
            import aiohttp
            # Format and chunk the message
            formatted = self.format_message(content)
            chunks = self.truncate_message(formatted, 4096)
            return chunks
        except Exception as e:
            return str(e)

''' + _EDIT_METHOD + '''    def format_message(self, content):
        return content

    def truncate_message(self, formatted, limit):
        return [formatted]
'''

# Minimal run.py carrying the boundary anchors (`import os`, the prepare fn, the
# `if _is_shared_multi_user` user-name prefix, and pre_gateway_dispatch).
GOOD_RUN = '''"""stub gateway run.py for patch tests."""
from __future__ import annotations
import os

logger = None


class Gateway:
    async def _prepare_inbound_message_text(self, event, source, message_text):
        if _is_shared_multi_user and source.user_name:
            message_text = f"[{source.user_name}] {message_text}"
        return message_text

    async def pre_gateway_dispatch(self, event):
        return event
'''

NO_FORMAT_ANCHOR = GOOD_SNIPPET.replace(
    "            formatted = self.format_message(content)\n", ""
)
NO_CLASS_ANCHOR = GOOD_SNIPPET.replace(
    "class WhatsAppAdapter(BasePlatformAdapter):", "class SomethingElse:"
)
NO_EDIT_ANCHOR = GOOD_SNIPPET.replace(_EDIT_METHOD, "")

_WA_TURN_MARKERS = (
    "BEGIN shift-agent-turn-budget-sentinel",
    "BEGIN shift-agent-turn-budget-send-drop",
    "BEGIN shift-agent-turn-budget-edit-drop",
)


def _load_ph(home: Path):
    os.environ["HERMES_HOME"] = str(home)
    name = f"ph_under_test_{next(_counter)}"
    loader = importlib.machinery.SourceFileLoader(name, str(PATCH_HERMES))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def _write_wa(home: Path, content: str) -> Path:
    wa = home / "gateway" / "platforms" / "whatsapp.py"
    wa.parent.mkdir(parents=True, exist_ok=True)
    wa.write_text(content, encoding="utf-8")
    return wa


def _write_run(home: Path, content: str = GOOD_RUN) -> Path:
    run = home / "gateway" / "run.py"
    run.parent.mkdir(parents=True, exist_ok=True)
    run.write_text(content, encoding="utf-8")
    return run


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _full_apply(tmp_path):
    """Fresh unpatched WA+RUN → full all-or-nothing apply. Returns (wa, run)."""
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    run = _write_run(tmp_path)
    _load_ph(tmp_path)._apply_wa_run()
    return wa, run


# ── content screen helper (_shift_front_brain_screen_outbound) — fail-OPEN ─────

def _exec_content_screen(tmp_path):
    ph = _load_ph(tmp_path)
    ns: dict = {}
    exec(ph.WHATSAPP_FB_SEND_HELPER, ns)  # markers are comments; body is a def
    return ns["_shift_front_brain_screen_outbound"]


def test_content_screen_fails_open_loudly_when_safe_io_broken(tmp_path, monkeypatch, capsys):
    fn = _exec_content_screen(tmp_path)
    broken = types.ModuleType("safe_io")  # lacks front_brain_screen_gateway_send
    monkeypatch.setitem(sys.modules, "safe_io", broken)
    assert fn("chat@c.us", "hello there") == "hello there"  # original text unchanged
    # §12b: content-screen disarm must NOT be silent
    assert "front_brain_screen_disarmed reason=" in capsys.readouterr().err


def test_content_screen_routes_through_safe_io(tmp_path, monkeypatch):
    fn = _exec_content_screen(tmp_path)
    stub = types.ModuleType("safe_io")
    stub.front_brain_screen_gateway_send = lambda cid, c, reserve_budget=True: f"SCREENED::{c}"
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    assert fn("chat@c.us", "hello") == "SCREENED::hello"


def test_content_screen_threads_reserve_budget(tmp_path, monkeypatch):
    fn = _exec_content_screen(tmp_path)
    seen = {}
    stub = types.ModuleType("safe_io")

    def _screen(cid, c, reserve_budget=True):
        seen["reserve_budget"] = reserve_budget
        return c

    stub.front_brain_screen_gateway_send = _screen
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    fn("chat@c.us", "draft", reserve_budget=False)  # progressive edit draft
    assert seen["reserve_budget"] is False
    fn("chat@c.us", "final")  # send / finalized default
    assert seen["reserve_budget"] is True


# ── budget screen (_shift_turn_send_budget_screen) → not-send sentinel ─────────

def _exec_budget_screen(tmp_path):
    ph = _load_ph(tmp_path)
    ns: dict = {}
    exec(ph.WHATSAPP_TURN_BUDGET_SENTINEL, ns)
    return ns


def test_budget_screen_returns_sentinel_when_gate_suppresses(tmp_path, monkeypatch):
    ns = _exec_budget_screen(tmp_path)
    fn = ns["_shift_turn_send_budget_screen"]
    sentinel = ns["_SHIFT_DROP_SEND"]
    stub = types.ModuleType("safe_io")
    stub.turn_send_budget_gate = lambda cid, c, reserve_budget=True: False  # SUPPRESS
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    assert fn("chat@c.us", "spiral") is sentinel


def test_budget_screen_admits_and_threads_reserve_to_gate(tmp_path, monkeypatch):
    ns = _exec_budget_screen(tmp_path)
    fn = ns["_shift_turn_send_budget_screen"]
    seen = {}
    stub = types.ModuleType("safe_io")

    def _gate(cid, c, reserve_budget=True):
        seen["gate_reserve"] = reserve_budget
        return True  # ADMIT → content passes through

    stub.turn_send_budget_gate = _gate
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    assert fn("chat@c.us", "hi", reserve_budget=False) == "hi"
    assert seen["gate_reserve"] is False  # reserve_budget threaded to the gate


def test_budget_screen_gate_none_is_byte_identical_passthrough(tmp_path, monkeypatch):
    ns = _exec_budget_screen(tmp_path)
    fn = ns["_shift_turn_send_budget_screen"]
    stub = types.ModuleType("safe_io")
    stub.turn_send_budget_gate = lambda cid, c, reserve_budget=True: None  # feature off
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    assert fn("chat@c.us", "hi") == "hi"


def test_budget_screen_missing_gate_is_passthrough(tmp_path, monkeypatch):
    # An older/partial safe_io without the gate → budget absent → content unchanged.
    ns = _exec_budget_screen(tmp_path)
    fn = ns["_shift_turn_send_budget_screen"]
    monkeypatch.setitem(sys.modules, "safe_io", types.ModuleType("safe_io"))
    assert fn("chat@c.us", "hi") == "hi"


def test_budget_screen_safe_io_unimportable_is_passthrough(tmp_path, monkeypatch):
    # A TOTAL safe_io-unavailable deploy fault → pass content through, never raise
    # (the default-OFF budget cannot coherently be "on"; the content screen's own
    # §12b fail-open-loudly covers deploy-fault visibility).
    ns = _exec_budget_screen(tmp_path)
    fn = ns["_shift_turn_send_budget_screen"]
    monkeypatch.setitem(sys.modules, "safe_io", None)  # `import safe_io` raises
    assert fn("chat@c.us", "hi") == "hi"


# ── all-or-nothing apply: prior-state coverage (the reviewer's enumeration) ────

def test_fresh_tree_installs_everything_and_parses(tmp_path):
    wa, run = _full_apply(tmp_path)
    wa_src = wa.read_text(encoding="utf-8")
    ast.parse(wa_src)
    ast.parse(run.read_text(encoding="utf-8"))
    for mk in _WA_TURN_MARKERS:
        assert mk in wa_src
    assert "BEGIN shift-agent-front-brain-send" in wa_src
    assert "BEGIN shift-agent-front-brain-edit" in wa_src
    assert "BEGIN shift-agent-turn-send-budget" in run.read_text(encoding="utf-8")


def test_front_brain_send_present_still_installs_turn_budget(tmp_path):
    # THE CORE REGRESSION: a tree that already carries the front-brain-send screen
    # (= production) must STILL get the sentinel + BOTH drop-checks.
    ph = _load_ph(tmp_path)
    wa = _write_wa(tmp_path, ph._apply_wa_front_brain(GOOD_SNIPPET))
    _write_run(tmp_path)
    assert "BEGIN shift-agent-front-brain-send" in wa.read_text(encoding="utf-8")
    assert "BEGIN shift-agent-turn-budget-sentinel" not in wa.read_text(encoding="utf-8")
    ph._apply_wa_run()
    src = wa.read_text(encoding="utf-8")
    ast.parse(src)
    for mk in _WA_TURN_MARKERS:
        assert mk in src  # sentinel + send-drop + edit-drop all installed independently


def test_only_turn_boundary_present_installs_adapter_parts(tmp_path):
    # run.py carries ONLY the boundary; whatsapp.py is unpatched → apply installs the
    # whatsapp.py adapter parts (sentinel + both drop-checks).
    ph = _load_ph(tmp_path)
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    run = _write_run(tmp_path, ph._apply_run_turn_budget_boundary(GOOD_RUN))
    assert "BEGIN shift-agent-turn-send-budget" in run.read_text(encoding="utf-8")
    ph._apply_wa_run()
    src = wa.read_text(encoding="utf-8")
    ast.parse(src)
    for mk in _WA_TURN_MARKERS:
        assert mk in src


def test_partial_adapter_state_converges(tmp_path):
    # sentinel present, send-drop stripped → re-apply re-installs ONLY the send-drop
    # (idempotent per marker), no duplication, no half state.
    ph = _load_ph(tmp_path)
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    _write_run(tmp_path)
    ph._apply_wa_run()  # full
    src = wa.read_text(encoding="utf-8")
    send_drop = ph.WHATSAPP_TURN_BUDGET_SEND_DROP.lstrip("\n")
    assert send_drop in src
    wa.write_text(src.replace(send_drop, ""), encoding="utf-8")  # strip ONLY send-drop
    assert "BEGIN shift-agent-turn-budget-send-drop" not in wa.read_text(encoding="utf-8")
    ph._apply_wa_run()  # converge
    converged = wa.read_text(encoding="utf-8")
    ast.parse(converged)
    assert converged.count("BEGIN shift-agent-turn-budget-send-drop") == 1
    assert converged.count("BEGIN shift-agent-turn-budget-sentinel") == 1  # not duplicated
    assert converged.count("BEGIN shift-agent-turn-budget-edit-drop") == 1


def test_fully_patched_tree_is_byte_idempotent(tmp_path):
    wa, run = _full_apply(tmp_path)
    before = (_sha(wa), _sha(run))
    _load_ph(tmp_path)._apply_wa_run()  # re-run on the fully-patched tree
    assert (_sha(wa), _sha(run)) == before


def test_missing_format_anchor_leaves_targets_byte_identical(tmp_path):
    ph = _load_ph(tmp_path)
    wa = _write_wa(tmp_path, NO_FORMAT_ANCHOR)
    run = _write_run(tmp_path)
    wa_before, run_before = _sha(wa), _sha(run)
    with pytest.raises(ph.PatchError):
        ph._apply_wa_run()
    assert _sha(wa) == wa_before
    assert _sha(run) == run_before


def test_missing_class_anchor_leaves_targets_byte_identical(tmp_path):
    # Unsupported version (adapter class renamed) → anchors don't match → nothing
    # written to ANY target (fail closed, no partial).
    ph = _load_ph(tmp_path)
    wa = _write_wa(tmp_path, NO_CLASS_ANCHOR)
    run = _write_run(tmp_path)
    wa_before, run_before = _sha(wa), _sha(run)
    with pytest.raises(ph.PatchError):
        ph._apply_wa_run()
    assert _sha(wa) == wa_before
    assert _sha(run) == run_before


def test_missing_edit_anchor_leaves_targets_byte_identical(tmp_path):
    # send() half transforms cleanly in memory but edit_message() anchor is gone →
    # PatchError before any write; both targets byte-identical.
    ph = _load_ph(tmp_path)
    wa = _write_wa(tmp_path, NO_EDIT_ANCHOR)
    run = _write_run(tmp_path)
    wa_before, run_before = _sha(wa), _sha(run)
    with pytest.raises(ph.PatchError):
        ph._apply_wa_run()
    assert _sha(wa) == wa_before
    assert _sha(run) == run_before


def test_missing_run_anchor_leaves_targets_byte_identical(tmp_path):
    # whatsapp.py transforms cleanly in memory, but run.py's anchor is gone → NOTHING
    # is written to either target (staging + validate happen before any write).
    ph = _load_ph(tmp_path)
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    run = _write_run(tmp_path, GOOD_RUN.replace("_is_shared_multi_user", "_renamed_flag"))
    wa_before, run_before = _sha(wa), _sha(run)
    with pytest.raises(ph.PatchError):
        ph._apply_wa_run()
    assert _sha(wa) == wa_before
    assert _sha(run) == run_before


def test_write_fault_on_second_file_rolls_back_first(tmp_path, monkeypatch):
    # whatsapp.py stages + writes; the run.py write raises → whatsapp.py is rolled
    # back to its ORIGINAL bytes (all-or-nothing proof).
    ph = _load_ph(tmp_path)
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    run = _write_run(tmp_path)
    wa_before, run_before = _sha(wa), _sha(run)
    real_write = ph._write_text_atomic

    def _boom(path, text):
        if str(path).endswith("run.py"):
            raise OSError("simulated disk-full writing run.py")
        return real_write(path, text)

    monkeypatch.setattr(ph, "_write_text_atomic", _boom)
    with pytest.raises(OSError):
        ph._apply_wa_run()
    assert _sha(wa) == wa_before   # first file rolled back to original bytes
    assert _sha(run) == run_before  # second file never written


def test_final_tree_has_four_distinct_markers(tmp_path):
    wa, run = _full_apply(tmp_path)
    wa_src = wa.read_text(encoding="utf-8")
    run_src = run.read_text(encoding="utf-8")
    present = {
        "boundary": "BEGIN shift-agent-turn-send-budget" in run_src,
        "sentinel": "BEGIN shift-agent-turn-budget-sentinel" in wa_src,
        "send_drop": "BEGIN shift-agent-turn-budget-send-drop" in wa_src,
        "edit_drop": "BEGIN shift-agent-turn-budget-edit-drop" in wa_src,
    }
    assert all(present.values())
    # the four marker slugs are distinct (independent installation surfaces)
    assert len({
        "shift-agent-turn-send-budget", "shift-agent-turn-budget-sentinel",
        "shift-agent-turn-budget-send-drop", "shift-agent-turn-budget-edit-drop",
    }) == 4


def test_send_and_edit_layout_budget_before_screen(tmp_path):
    # send(): budget drop-check sits ABOVE the front-brain screen call, which sits
    # immediately before the format_message relay → budget suppression is checked
    # first, then the content screen substitutes.
    wa, _ = _full_apply(tmp_path)
    lines = wa.read_text(encoding="utf-8").splitlines()
    si = next(i for i, l in enumerate(lines) if "formatted = self.format_message(content)" in l)
    assert "END shift-agent-front-brain-send" in lines[si - 1]
    assert "content = _shift_front_brain_screen_outbound(chat_id, content)" in lines[si - 2]
    assert "BEGIN shift-agent-front-brain-send" in lines[si - 3]
    assert "END shift-agent-turn-budget-send-drop" in lines[si - 4]
    assert "return None" in lines[si - 5]
    assert "if content is _SHIFT_DROP_SEND:" in lines[si - 6]
    assert "content = _shift_turn_send_budget_screen(chat_id, content)" in lines[si - 7]
    assert "BEGIN shift-agent-turn-budget-send-drop" in lines[si - 8]
    # edit_message(): the edit drop-check reserves only on finalize and precedes the
    # front-brain-edit screen call.
    di = next(
        i for i, l in enumerate(lines)
        if "_shift_turn_send_budget_screen(chat_id, content, reserve_budget=finalize)" in l
    )
    assert "BEGIN shift-agent-turn-budget-edit-drop" in lines[di - 1]
    assert "if content is _SHIFT_DROP_SEND:" in lines[di + 1]
    assert "return None" in lines[di + 2]
    assert "END shift-agent-turn-budget-edit-drop" in lines[di + 3]
    assert "BEGIN shift-agent-front-brain-edit" in lines[di + 4]
    assert "_shift_front_brain_screen_outbound(chat_id, content, reserve_budget=finalize)" in lines[di + 5]


# ── deploy-gate wiring (the real check-shift-agent-patch.sh text) ──────────────

def test_gate_script_wires_front_brain_checks():
    gate = GATE_SCRIPT.read_text(encoding="utf-8")
    assert 'grep -q "BEGIN shift-agent-front-brain-send" "$WA"' in gate
    assert 'grep -q "BEGIN shift-agent-front-brain-edit" "$WA"' in gate
    assert "front-brain-send marker drifted from format_message anchor" in gate
    assert "front-brain-edit marker drifted from /edit anchor" in gate


def test_gate_script_wires_turn_budget_checks():
    gate = GATE_SCRIPT.read_text(encoding="utf-8")
    # run.py boundary + adapter sentinel + BOTH drop-check markers are all pinned.
    assert 'grep -q "BEGIN shift-agent-turn-send-budget" "$RUN"' in gate
    assert 'grep -q "END shift-agent-turn-send-budget" "$RUN"' in gate
    assert 'grep -q "BEGIN shift-agent-turn-budget-sentinel" "$WA"' in gate
    assert 'grep -q "END shift-agent-turn-budget-sentinel" "$WA"' in gate
    assert 'grep -q "BEGIN shift-agent-turn-budget-send-drop" "$WA"' in gate
    assert 'grep -q "END shift-agent-turn-budget-send-drop" "$WA"' in gate
    assert 'grep -q "BEGIN shift-agent-turn-budget-edit-drop" "$WA"' in gate
    assert 'grep -q "END shift-agent-turn-budget-edit-drop" "$WA"' in gate
    assert 'grep -q "_SHIFT_DROP_SEND = " "$WA"' in gate
    assert 'grep -q "content is _SHIFT_DROP_SEND" "$WA"' in gate
    assert "turn-send-budget marker drifted from _prepare_inbound_message_text anchor" in gate
    assert "turn-budget-send-drop marker drifted from format_message anchor" in gate
    assert "turn-budget-edit-drop marker drifted from /edit anchor" in gate


# ── deploy-gate predicates driven against fixture trees (mirror the gate) ──────

_TURN_BUDGET_PREDICATE = r'''
WA="$1"; RUN="$2"
grep -q "BEGIN shift-agent-turn-send-budget" "$RUN" || { echo FAIL_BOUNDARY; exit 1; }
grep -q "BEGIN shift-agent-turn-budget-sentinel" "$WA" || { echo FAIL_SENTINEL; exit 1; }
grep -q "BEGIN shift-agent-turn-budget-send-drop" "$WA" || { echo FAIL_SEND_DROP; exit 1; }
grep -q "BEGIN shift-agent-turn-budget-edit-drop" "$WA" || { echo FAIL_EDIT_DROP; exit 1; }
grep -q "_SHIFT_DROP_SEND = " "$WA" || { echo FAIL_SENTINEL_DEF; exit 1; }
grep -q "content is _SHIFT_DROP_SEND" "$WA" || { echo FAIL_DROP_CHECK; exit 1; }
echo OK
'''

bash_required = pytest.mark.skipif(
    platform.system() == "Windows" or shutil.which("bash") is None,
    reason="gate predicates need a working POSIX bash (Linux/Docker)",
)


def _run_turn_predicate(wa: Path, run: Path):
    return subprocess.run(
        ["bash", "-c", _TURN_BUDGET_PREDICATE, "_", str(wa), str(run)],
        capture_output=True, text=True,
    )


def _strip_marker_lines(wa: Path, marker: str):
    src = wa.read_text(encoding="utf-8")
    wa.write_text("\n".join(l for l in src.splitlines() if marker not in l), encoding="utf-8")


@bash_required
def test_gate_predicate_passes_on_fully_patched(tmp_path):
    wa, run = _full_apply(tmp_path)
    r = _run_turn_predicate(wa, run)
    assert r.returncode == 0 and "OK" in r.stdout


@bash_required
@pytest.mark.parametrize("marker,expect", [
    ("BEGIN shift-agent-turn-budget-sentinel", "FAIL_SENTINEL"),
    ("BEGIN shift-agent-turn-budget-send-drop", "FAIL_SEND_DROP"),
    ("BEGIN shift-agent-turn-budget-edit-drop", "FAIL_EDIT_DROP"),
])
def test_gate_predicate_rejects_each_partial_combination(tmp_path, marker, expect):
    # Drop exactly one adapter marker → the gate rejects the partial combination.
    wa, run = _full_apply(tmp_path)
    _strip_marker_lines(wa, marker)
    r = _run_turn_predicate(wa, run)
    assert r.returncode == 1 and expect in r.stdout


@bash_required
def test_gate_predicate_rejects_boundary_present_but_send_drop_absent(tmp_path):
    # The reviewer's explicit example: run.py boundary present, whatsapp.py send-drop
    # absent → FAIL (no half-capped tree ships).
    wa, run = _full_apply(tmp_path)
    _strip_marker_lines(wa, "shift-agent-turn-budget-send-drop")
    assert "BEGIN shift-agent-turn-send-budget" in run.read_text(encoding="utf-8")
    r = _run_turn_predicate(wa, run)
    assert r.returncode == 1 and "FAIL_SEND_DROP" in r.stdout


@bash_required
def test_gate_predicate_rejects_unpatched(tmp_path):
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    run = _write_run(tmp_path)
    r = _run_turn_predicate(wa, run)
    assert r.returncode == 1 and "FAIL_BOUNDARY" in r.stdout
