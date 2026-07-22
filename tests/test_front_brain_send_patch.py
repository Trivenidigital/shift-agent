"""Item 3 + 7 — Hermes-side front-brain egress patch (patch-hermes.py) + gate.

Covers BOTH text-egress paths: send() and edit_message(). We do NOT vendor Hermes
source in-repo; each test SYNTHESIZES the minimal anchor shape patch-hermes.py
keys on, applies the real patch function, and verifies markers, placement,
idempotency, parse, fail-closed on missing anchors, the inserted helper's
fail-open-loudly (§12b) + reserve_budget threading, and the deploy-gate
marker+proximity predicates. The full check-shift-agent-patch.sh runs at deploy
time (needs the live git/bridge tree); here we exercise its front-brain
predicates verbatim and assert the script wires them.

Pure text/ast (patch-hermes.py has no fcntl) -> Windows + Docker. The bash
predicate tests skip when a POSIX bash is unavailable.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import itertools
import os
import platform
import shutil
import subprocess
import sys
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

# Minimal file carrying the exact anchor shapes patch-hermes.py keys on.
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

NO_FORMAT_ANCHOR = GOOD_SNIPPET.replace(
    "            formatted = self.format_message(content)\n", ""
)
NO_CLASS_ANCHOR = GOOD_SNIPPET.replace(
    "class WhatsAppAdapter(BasePlatformAdapter):", "class SomethingElse:"
)
NO_EDIT_ANCHOR = GOOD_SNIPPET.replace(_EDIT_METHOD, "")


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


# ── patch application: send + edit ────────────────────────────────────────────

def test_patch_applies_idempotently_and_parses(tmp_path):
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    ph = _load_ph(tmp_path)
    ph._patch_whatsapp_py_front_brain_send()
    ph._patch_whatsapp_py_front_brain_send()  # idempotent

    src = wa.read_text(encoding="utf-8")
    ast.parse(src)
    assert src.count("BEGIN shift-agent-front-brain-send") == 2  # helper + send inject
    assert src.count("BEGIN shift-agent-front-brain-edit") == 1
    assert "def _shift_front_brain_screen_outbound(chat_id, content, reserve_budget=True):" in src
    assert src.index("def _shift_front_brain_screen_outbound(") < src.index(
        "class WhatsAppAdapter(BasePlatformAdapter):"
    )
    # not-send sentinel is defined module-level, before the class
    assert "_SHIFT_DROP_SEND = " in src
    assert src.index("_SHIFT_DROP_SEND = ") < src.index(
        "class WhatsAppAdapter(BasePlatformAdapter):"
    )
    lines = src.splitlines()
    # send inject sits immediately before the format/relay, followed by the
    # sentinel drop-check that returns a no-op (None) so the transport is skipped.
    si = next(i for i, l in enumerate(lines) if "formatted = self.format_message(content)" in l)
    assert "content = _shift_front_brain_screen_outbound(chat_id, content)" in lines[si - 4]
    assert "if content is _SHIFT_DROP_SEND:" in lines[si - 3]
    assert "return None" in lines[si - 2]
    # edit inject reserves budget only on finalize, then the same drop-check
    ei = next(i for i, l in enumerate(lines) if "reserve_budget=finalize" in l)
    assert "_shift_front_brain_screen_outbound(chat_id, content, reserve_budget=finalize)" in lines[ei]
    assert "if content is _SHIFT_DROP_SEND:" in lines[ei + 1]
    assert "return None" in lines[ei + 2]


def test_patch_failclosed_on_missing_class_anchor(tmp_path):
    _write_wa(tmp_path, NO_CLASS_ANCHOR)
    with pytest.raises(SystemExit) as e:
        _load_ph(tmp_path)._patch_whatsapp_py_front_brain_send()
    assert e.value.code == 1


def test_patch_failclosed_on_missing_format_anchor(tmp_path):
    _write_wa(tmp_path, NO_FORMAT_ANCHOR)
    with pytest.raises(SystemExit) as e:
        _load_ph(tmp_path)._patch_whatsapp_py_front_brain_send()
    assert e.value.code == 1


def test_patch_failclosed_on_missing_edit_anchor(tmp_path):
    _write_wa(tmp_path, NO_EDIT_ANCHOR)
    with pytest.raises(SystemExit) as e:
        _load_ph(tmp_path)._patch_whatsapp_py_front_brain_send()
    assert e.value.code == 1


# ── inserted helper: fail-open-loudly (§12b) + reserve_budget threading ───────

def _exec_helper(tmp_path):
    ph = _load_ph(tmp_path)
    ns: dict = {}
    exec(ph.WHATSAPP_FB_SEND_HELPER, ns)  # markers are comments; body is a def
    return ns["_shift_front_brain_screen_outbound"]


def test_inserted_helper_fails_open_loudly_when_safe_io_broken(tmp_path, monkeypatch, capsys):
    fn = _exec_helper(tmp_path)
    import types
    broken = types.ModuleType("safe_io")  # lacks front_brain_screen_gateway_send
    monkeypatch.setitem(sys.modules, "safe_io", broken)
    assert fn("chat@c.us", "hello there") == "hello there"  # original text unchanged
    # §12b: disarm must NOT be silent
    assert "front_brain_screen_disarmed reason=" in capsys.readouterr().err


def test_inserted_helper_routes_through_safe_io(tmp_path, monkeypatch):
    fn = _exec_helper(tmp_path)
    import types
    stub = types.ModuleType("safe_io")
    stub.front_brain_screen_gateway_send = lambda cid, c, reserve_budget=True: f"SCREENED::{c}"
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    assert fn("chat@c.us", "hello") == "SCREENED::hello"


def test_inserted_helper_threads_reserve_budget(tmp_path, monkeypatch):
    fn = _exec_helper(tmp_path)
    import types
    seen = {}
    stub = types.ModuleType("safe_io")

    def _screen(cid, c, reserve_budget=True):
        seen["reserve_budget"] = reserve_budget
        return c

    stub.front_brain_screen_gateway_send = _screen
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    fn("chat@c.us", "draft", reserve_budget=False)  # edit progressive-draft call
    assert seen["reserve_budget"] is False
    fn("chat@c.us", "final")  # send / finalized default
    assert seen["reserve_budget"] is True


# ── inserted helper: per-turn budget gate → not-send sentinel ─────────────────

def _exec_helper_ns(tmp_path):
    ph = _load_ph(tmp_path)
    ns: dict = {}
    exec(ph.WHATSAPP_FB_SEND_HELPER, ns)
    return ns


def test_inserted_helper_returns_drop_sentinel_when_budget_gate_suppresses(tmp_path, monkeypatch):
    ns = _exec_helper_ns(tmp_path)
    fn = ns["_shift_front_brain_screen_outbound"]
    sentinel = ns["_SHIFT_DROP_SEND"]
    import types
    screened = []
    stub = types.ModuleType("safe_io")
    stub.turn_send_budget_gate = lambda cid, c, reserve_budget=True: False  # SUPPRESS
    stub.front_brain_screen_gateway_send = lambda cid, c, reserve_budget=True: screened.append(c) or c
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    # Gate False → the drop sentinel is returned BEFORE the content screen runs.
    assert fn("chat@c.us", "spiral") is sentinel
    assert screened == []  # budget gate suppresses AROUND the content screen


def test_inserted_helper_admits_and_threads_reserve_to_gate(tmp_path, monkeypatch):
    ns = _exec_helper_ns(tmp_path)
    fn = ns["_shift_front_brain_screen_outbound"]
    import types
    seen = {}
    stub = types.ModuleType("safe_io")

    def _gate(cid, c, reserve_budget=True):
        seen["gate_reserve"] = reserve_budget
        return True  # ADMIT → the content screen runs

    stub.turn_send_budget_gate = _gate
    stub.front_brain_screen_gateway_send = lambda cid, c, reserve_budget=True: "SCREENED::" + c
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    assert fn("chat@c.us", "hi", reserve_budget=False) == "SCREENED::hi"
    assert seen["gate_reserve"] is False  # reserve_budget threaded to the gate too


def test_inserted_helper_gate_none_is_byte_identical_passthrough(tmp_path, monkeypatch):
    # gate returns None (feature off) → the #641 content screen runs exactly as
    # before (byte-identical): the budget layer adds nothing.
    ns = _exec_helper_ns(tmp_path)
    fn = ns["_shift_front_brain_screen_outbound"]
    import types
    stub = types.ModuleType("safe_io")
    stub.turn_send_budget_gate = lambda cid, c, reserve_budget=True: None
    stub.front_brain_screen_gateway_send = lambda cid, c, reserve_budget=True: "SCREENED::" + c
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    assert fn("chat@c.us", "hi") == "SCREENED::hi"


# ── deploy-gate front-brain predicates (mirror check-shift-agent-patch.sh) ────

_GATE_PREDICATE = r'''
WA="$1"
grep -q "BEGIN shift-agent-front-brain-send" "$WA" || { echo FAIL_SEND_MARKER; exit 1; }
grep -q "BEGIN shift-agent-front-brain-edit" "$WA" || { echo FAIL_EDIT_MARKER; exit 1; }
FBB=$(grep -n "BEGIN shift-agent-front-brain-send" "$WA" | tail -1 | cut -d: -f1)
FBA=$(grep -n "formatted = self.format_message(content)" "$WA" | head -1 | cut -d: -f1)
[ -n "$FBB" ] && [ -n "$FBA" ] || { echo FAIL_SEND_ANCHOR; exit 1; }
D=$(( FBB > FBA ? FBB - FBA : FBA - FBB )); [ "$D" -le 10 ] || { echo FAIL_SEND_PROXIMITY; exit 1; }
FEB=$(grep -n "BEGIN shift-agent-front-brain-edit" "$WA" | head -1 | cut -d: -f1)
FEA=$(grep -n '/edit"' "$WA" | head -1 | cut -d: -f1)
[ -n "$FEB" ] && [ -n "$FEA" ] || { echo FAIL_EDIT_ANCHOR; exit 1; }
D2=$(( FEB > FEA ? FEB - FEA : FEA - FEB )); [ "$D2" -le 10 ] || { echo FAIL_EDIT_PROXIMITY; exit 1; }
echo OK
'''

bash_required = pytest.mark.skipif(
    platform.system() == "Windows" or shutil.which("bash") is None,
    reason="gate predicates need a working POSIX bash (Linux/Docker)",
)


def _run_predicate(wa: Path):
    return subprocess.run(
        ["bash", "-c", _GATE_PREDICATE, "_", str(wa)], capture_output=True, text=True
    )


@bash_required
def test_gate_predicate_passes_on_patched(tmp_path):
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    _load_ph(tmp_path)._patch_whatsapp_py_front_brain_send()
    r = _run_predicate(wa)
    assert r.returncode == 0 and "OK" in r.stdout


@bash_required
def test_gate_predicate_fails_on_unpatched(tmp_path):
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    r = _run_predicate(wa)
    assert r.returncode == 1 and "FAIL_SEND_MARKER" in r.stdout


@bash_required
def test_gate_predicate_fails_on_missing_edit_marker(tmp_path):
    # Helper + send present, edit absent (partial patch) -> edit marker check fires.
    ph = _load_ph(tmp_path)
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    ph._patch_whatsapp_py_front_brain_send()
    src = wa.read_text(encoding="utf-8")
    # strip the edit inject block -> edit marker gone, send intact
    src = src.replace(ph.WHATSAPP_FB_EDIT_INJECT, "")
    wa.write_text(src, encoding="utf-8")
    r = _run_predicate(wa)
    assert r.returncode == 1 and "FAIL_EDIT_MARKER" in r.stdout


@bash_required
def test_gate_predicate_fails_on_mangled_send_proximity(tmp_path):
    # Full patch, then remove ONLY the send inject block -> the last send BEGIN is
    # the module-level helper, far from format_message -> send proximity fails.
    ph = _load_ph(tmp_path)
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    ph._patch_whatsapp_py_front_brain_send()
    src = wa.read_text(encoding="utf-8")
    src = src.replace(ph.WHATSAPP_FB_SEND_INJECT.lstrip("\n"), "")
    wa.write_text(src, encoding="utf-8")
    r = _run_predicate(wa)
    assert r.returncode == 1 and "FAIL_SEND_PROXIMITY" in r.stdout


def test_gate_script_wires_front_brain_checks():
    gate = GATE_SCRIPT.read_text(encoding="utf-8")
    assert 'grep -q "BEGIN shift-agent-front-brain-send" "$WA"' in gate
    assert 'grep -q "BEGIN shift-agent-front-brain-edit" "$WA"' in gate
    assert "front-brain-send marker drifted from format_message anchor" in gate
    assert "front-brain-edit marker drifted from /edit anchor" in gate


def test_gate_script_wires_turn_send_budget_checks():
    gate = GATE_SCRIPT.read_text(encoding="utf-8")
    # run.py boundary marker + adapter-seam sentinel + drop-check are all pinned.
    assert 'grep -q "BEGIN shift-agent-turn-send-budget" "$RUN"' in gate
    assert 'grep -q "END shift-agent-turn-send-budget" "$RUN"' in gate
    assert 'grep -q "_SHIFT_DROP_SEND = " "$WA"' in gate
    assert 'grep -q "content is _SHIFT_DROP_SEND" "$WA"' in gate
    assert "turn-send-budget marker drifted from _prepare_inbound_message_text anchor" in gate
