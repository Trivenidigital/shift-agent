"""Item 3 — Hermes-side front-brain outbound-send patch (patch-hermes.py) + gate.

We do NOT vendor Hermes source in-repo. Instead each test SYNTHESIZES the minimal
anchor shape (class WhatsAppAdapter + async def send + format_message assignment)
that patch-hermes.py anchors on, applies the real patch function against it, and
verifies: markers, placement, idempotency, it still parses, fail-closed on a
missing anchor, the inserted helper's fail-open contract, and the deploy-gate
marker+proximity predicates. The full check-shift-agent-patch.sh runs at deploy
time (needs the live git/bridge tree); here we exercise its front-brain
predicates verbatim and assert the script wires them.

Pure text/ast (patch-hermes.py has no fcntl) -> Windows + Docker. The bash
predicate tests skip when bash is unavailable.
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

    def format_message(self, content):
        return content

    def truncate_message(self, formatted, limit):
        return [formatted]
'''

# Class anchor present but the format_message assignment inside send() removed.
NO_FORMAT_ANCHOR = GOOD_SNIPPET.replace(
    "            formatted = self.format_message(content)\n", ""
)

# No adapter class at all.
NO_CLASS_ANCHOR = GOOD_SNIPPET.replace(
    "class WhatsAppAdapter(BasePlatformAdapter):", "class SomethingElse:"
)


def _load_ph(home: Path):
    """Load patch-hermes.py fresh with HERMES_HOME -> `home` (WA is computed at
    import from HERMES_HOME)."""
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


# ── patch application ─────────────────────────────────────────────────────────

def test_patch_applies_idempotently_and_parses(tmp_path):
    wa = _write_wa(tmp_path, GOOD_SNIPPET)
    ph = _load_ph(tmp_path)
    ph._patch_whatsapp_py_front_brain_send()
    ph._patch_whatsapp_py_front_brain_send()  # idempotent

    src = wa.read_text(encoding="utf-8")
    ast.parse(src)  # still valid Python
    assert src.count("BEGIN shift-agent-front-brain-send") == 2  # helper + inject
    assert src.count("END shift-agent-front-brain-send") == 2
    assert "def _shift_front_brain_screen_outbound(" in src
    # helper is module-level, before the adapter class
    assert src.index("def _shift_front_brain_screen_outbound(") < src.index(
        "class WhatsAppAdapter(BasePlatformAdapter):"
    )
    # the screen call sits immediately before the format/relay
    lines = src.splitlines()
    idx = next(i for i, l in enumerate(lines) if "formatted = self.format_message(content)" in l)
    assert "content = _shift_front_brain_screen_outbound(chat_id, content)" in lines[idx - 2]


def test_patch_failclosed_on_missing_class_anchor(tmp_path):
    _write_wa(tmp_path, NO_CLASS_ANCHOR)
    ph = _load_ph(tmp_path)
    with pytest.raises(SystemExit) as e:
        ph._patch_whatsapp_py_front_brain_send()
    assert e.value.code == 1


def test_patch_failclosed_on_missing_format_anchor(tmp_path):
    _write_wa(tmp_path, NO_FORMAT_ANCHOR)
    ph = _load_ph(tmp_path)
    with pytest.raises(SystemExit) as e:
        ph._patch_whatsapp_py_front_brain_send()
    assert e.value.code == 1


# ── inserted helper: fail-open / routes through safe_io ──────────────────────

def _exec_helper(tmp_path):
    ph = _load_ph(tmp_path)
    ns: dict = {}
    exec(ph.WHATSAPP_FB_SEND_HELPER, ns)  # markers are comments; body is a def
    return ns["_shift_front_brain_screen_outbound"]


def test_inserted_helper_fails_open_when_safe_io_broken(tmp_path, monkeypatch):
    fn = _exec_helper(tmp_path)
    import types
    broken = types.ModuleType("safe_io")  # lacks front_brain_screen_gateway_send
    monkeypatch.setitem(sys.modules, "safe_io", broken)
    assert fn("chat@c.us", "hello there") == "hello there"  # original text unchanged


def test_inserted_helper_routes_through_safe_io(tmp_path, monkeypatch):
    fn = _exec_helper(tmp_path)
    import types
    stub = types.ModuleType("safe_io")
    stub.front_brain_screen_gateway_send = lambda cid, c: f"SCREENED::{c}"
    monkeypatch.setitem(sys.modules, "safe_io", stub)
    assert fn("chat@c.us", "hello") == "SCREENED::hello"


# ── deploy-gate front-brain predicates (mirror check-shift-agent-patch.sh) ────

_GATE_PREDICATE = r'''
WA="$1"
grep -q "BEGIN shift-agent-front-brain-send" "$WA" || { echo FAIL_MARKER; exit 1; }
grep -q "END shift-agent-front-brain-send" "$WA" || { echo FAIL_MARKER; exit 1; }
FBB=$(grep -n "BEGIN shift-agent-front-brain-send" "$WA" | tail -1 | cut -d: -f1)
FBA=$(grep -n "formatted = self.format_message(content)" "$WA" | head -1 | cut -d: -f1)
[ -n "$FBB" ] && [ -n "$FBA" ] || { echo FAIL_ANCHOR; exit 1; }
DIFF4=$(( FBB > FBA ? FBB - FBA : FBA - FBB ))
[ "$DIFF4" -le 10 ] || { echo FAIL_PROXIMITY; exit 1; }
echo OK
'''

bash_required = pytest.mark.skipif(
    platform.system() == "Windows" or shutil.which("bash") is None,
    reason="gate predicates need a working POSIX bash (Linux/Docker)",
)


def _run_predicate(wa: Path):
    return subprocess.run(
        ["bash", "-c", _GATE_PREDICATE, "_", str(wa)],
        capture_output=True, text=True,
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
    assert r.returncode == 1 and "FAIL_MARKER" in r.stdout


@bash_required
def test_gate_predicate_fails_on_mangled_proximity(tmp_path):
    # Helper marker present near the top but the send-site inject is absent, so
    # the last BEGIN marker is far from format_message -> proximity fails.
    ph = _load_ph(tmp_path)
    mangled = ph.WHATSAPP_FB_SEND_HELPER + "\n\n\n" + GOOD_SNIPPET
    wa = _write_wa(tmp_path, mangled)
    r = _run_predicate(wa)
    assert r.returncode == 1 and "FAIL_PROXIMITY" in r.stdout


def test_gate_script_wires_front_brain_checks():
    gate = GATE_SCRIPT.read_text(encoding="utf-8")
    assert 'grep -q "BEGIN shift-agent-front-brain-send" "$WA"' in gate
    assert 'grep -q "END shift-agent-front-brain-send" "$WA"' in gate
    assert "front-brain-send marker drifted from format_message anchor" in gate
