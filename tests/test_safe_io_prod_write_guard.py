"""Invariant tests for the generalized safe_io deployed-tree write-guard.

fix/test-prod-path-bleed-class 2026-07-11. Standing rule: "every documented
invariant gets a test that fails if violated."

Two documented invariants are pinned here:

  (a) EVERY safe_io write chokepoint refuses an /opt/shift-agent write under
      pytest (unless SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST=1). #606 guarded only
      ndjson_append; the generalization (this branch) extends it to
      atomic_write_text (hence atomic_write_json / dump_model) and the
      notify-failed.log dead-letter append.

  (b) A future public write helper CANNOT be added guard-free. The check
      ENUMERATES the write chokepoints BY INTROSPECTION — a source-level AST
      scan of safe_io.py that flags every function whose own body performs a
      raw filesystem write (os.write / os.replace / Path.write_text /
      write_bytes / open() in a write mode) — and asserts each flagged function
      invokes _refuse_prod_write_under_pytest. There is deliberately NO
      maintained list of helper names: a name list would re-create the exact
      gap it seals (a future helper added off-list would be guard-free while
      the check reports green). Because the writer set is DERIVED from the
      source each run, a newly added writer is auto-enumerated and must carry
      the guard or the check fails. test_write_detector_is_sound self-tests the
      detector against synthetic sources so it cannot silently degrade to a
      vacuous green (e.g. detecting zero writers).

Part (b) is pure source analysis (no import), so it runs on Windows too; part
(a) imports safe_io, which pulls in fcntl (Linux only) and is skipped on Windows
— matching test_audit_prod_isolation_guard.py / test_safe_io_bridge_post.py.
"""
from __future__ import annotations

import ast
import platform
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
sys.path.insert(0, str(PLATFORM_DIR))

_SAFE_IO_SRC_PATH = PLATFORM_DIR / "safe_io.py"

# The guard function + its back-compat alias. A writer that calls either counts
# as guarded. This is the ONE symbol the invariant is expressed against; the set
# of WRITERS is never hardcoded — it is introspected from the source (see the
# module docstring, part (b)).
_GUARD_NAMES = {
    "_refuse_prod_write_under_pytest",
    "_refuse_prod_audit_write_under_pytest",
}


def _iter_defs(tree: ast.Module):
    """Yield (name, FunctionDef) for module-level functions + class methods."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{node.name}.{sub.name}", sub


def _open_is_write_mode(call: ast.Call) -> bool:
    """True if an open()/`.open()` call requests a write/append/create mode.

    Scans every string-literal positional arg AND a mode= keyword, so it works
    for BOTH builtin ``open(path, "w")`` (mode at arg 1) and ``path.open("a")``
    (mode at arg 0) without assuming a position. A file-mode string is short and
    made only of mode chars; requiring that shape avoids mistaking a path
    literal for a mode while still catching 'w'/'a'/'x'/'+' variants."""
    candidates: list[str] = []
    for a in call.args:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            candidates.append(a.value)
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            candidates.append(kw.value.value)
    for m in candidates:
        if m and set(m) <= set("rwaxbt+U") and any(c in m for c in "wax+"):
            return True
    return False


def _writes_bytes(func: ast.AST) -> bool:
    """True if the function body performs a raw filesystem content-write.

    Detects: os.write(...), os.replace(...) (atomic rename used by atomic
    writers), <path>.write_text(...) / .write_bytes(...), builtin
    ``open(path, "w"/"a"/"x"/...)``, and ``<path>.open(mode)`` in a write mode.
    Deliberately does NOT count os.open (lock-file creation, no content),
    .rename (corrupt quarantine), .unlink, or reads — so delegating helpers
    (atomic_write_json, dump_model, _notify_dedup_record, _emit_audit_row) are
    not flagged; the guard fires transitively through the primitive they call.
    Errs toward over-flagging (safe: forces a guard) rather than under-flagging
    (dangerous: a silent guard-free writer)."""
    for n in ast.walk(func):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        # builtin open(path, "w"/...) — func is a bare Name, not an Attribute.
        if isinstance(f, ast.Name) and f.id == "open":
            if _open_is_write_mode(n):
                return True
            continue
        if isinstance(f, ast.Attribute):
            attr = f.attr
            val = f.value
            if attr in ("write", "replace") and isinstance(val, ast.Name) and val.id == "os":
                return True
            if attr in ("write_text", "write_bytes"):
                return True
            if attr == "open" and _open_is_write_mode(n):
                return True
    return False


def _calls_guard(func: ast.AST) -> bool:
    for n in ast.walk(func):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
        if name in _GUARD_NAMES:
            return True
    return False


@pytest.fixture(scope="module")
def raw_writers() -> dict[str, ast.AST]:
    """Every safe_io function that performs a raw filesystem content-write."""
    tree = ast.parse(_SAFE_IO_SRC_PATH.read_text(encoding="utf-8"))
    return {name: node for name, node in _iter_defs(tree) if _writes_bytes(node)}


class TestGuardInvokedByEveryWriter:
    """Source-level lint — runs on every platform (no safe_io import).

    Enumeration is by INTROSPECTION, never a maintained name list: raw_writers
    is derived from the current source each run, so a newly added write helper
    is auto-included and must carry the guard.
    """

    def test_every_raw_writer_invokes_the_guard(self, raw_writers):
        # Non-vacuous guard: if the detector ever finds zero writers (e.g. a
        # regression in _writes_bytes), the offenders check below would pass
        # trivially — so first assert the introspection actually found writers.
        # test_write_detector_is_sound proves the detector distinguishes a
        # guardless writer from a guarded / delegating one.
        assert raw_writers, (
            "introspection found ZERO safe_io raw writers — the detector is "
            "broken; a real regression here would let this invariant pass "
            "vacuously. Fix _writes_bytes."
        )
        offenders = [name for name, node in raw_writers.items() if not _calls_guard(node)]
        assert not offenders, (
            "these safe_io write chokepoints write to disk without invoking "
            f"_refuse_prod_write_under_pytest: {offenders}. Every function whose "
            "body writes to a path MUST call the guard (directly, or delegate "
            "its write to a helper that does)."
        )

    def test_write_detector_is_sound(self):
        """Self-test the introspection so it cannot silently degrade to a
        vacuous green. Feeds synthetic sources through the SAME _writes_bytes /
        _calls_guard predicates used against safe_io, asserting a guardless
        writer is flagged, a guarded writer is cleared, and a delegating
        non-writer is not flagged."""
        src = (
            "import os\n"
            "def guardless_writer(path, data):\n"
            "    with open(path, 'w') as f:\n"
            "        f.write(data)\n"
            "def guarded_writer(path, data):\n"
            "    _refuse_prod_write_under_pytest(path, helper='guarded_writer')\n"
            "    os.write(2, data)\n"
            "def delegating_helper(path, obj):\n"
            "    guarded_writer(path, obj)\n"
            "def pure_reader(path):\n"
            "    return path.read_text()\n"
        )
        defs = dict(_iter_defs(ast.parse(src)))
        writers = {name for name, node in defs.items() if _writes_bytes(node)}
        # Detector flags the two functions that write bytes, and only those.
        assert writers == {"guardless_writer", "guarded_writer"}, writers
        # And it correctly separates guarded from guardless among the writers.
        assert _calls_guard(defs["guarded_writer"]) is True
        assert _calls_guard(defs["guardless_writer"]) is False


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io uses fcntl (Linux only)",
)
class TestWritersRefuseProdPathUnderPytest:
    """Behavioral — each chokepoint raises on a deployed-root write.

    The tests point _PROD_AUDIT_ROOT at a per-test tmp dir and write to a FRESH
    path under it. That keeps them order-independent: asserting `not exists`
    against the real /opt/shift-agent/logs/decisions.log would flake if an
    earlier test in the run created it (e.g. via a subprocess that does not
    inherit PYTEST_CURRENT_TEST). test_guard_covers_real_prod_default separately
    pins that the REAL default root is the one being guarded.
    """

    @pytest.fixture
    def safe_io_module(self):
        import importlib
        import safe_io
        importlib.reload(safe_io)
        return safe_io

    @pytest.fixture
    def fake_prod_root(self, safe_io_module, monkeypatch, tmp_path):
        """Point the guard's root at a fresh tmp dir so 'a write under the
        deployed root' is reproducible without touching the real box."""
        monkeypatch.delenv("SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST", raising=False)
        assert __import__("os").environ.get("PYTEST_CURRENT_TEST")  # premise
        root = tmp_path / "opt" / "shift-agent"
        monkeypatch.setattr(safe_io_module, "_PROD_AUDIT_ROOT", str(root))
        return root

    def test_atomic_write_text_refuses(self, safe_io_module, fake_prod_root):
        target = fake_prod_root / "state" / "probe.json"
        with pytest.raises(RuntimeError) as exc:
            safe_io_module.atomic_write_text(target, "hi")
        assert "atomic_write_text refused" in str(exc.value)
        assert not target.exists()

    def test_atomic_write_json_refuses_via_delegation(self, safe_io_module, fake_prod_root):
        target = fake_prod_root / "state" / "probe.json"
        with pytest.raises(RuntimeError) as exc:
            safe_io_module.atomic_write_json(target, {"a": 1})
        # atomic_write_json delegates to atomic_write_text — guard fires there.
        assert "atomic_write_text refused" in str(exc.value)
        assert not target.exists()

    def test_dump_model_refuses_via_delegation(self, safe_io_module, fake_prod_root):
        from pydantic import BaseModel

        class _M(BaseModel):
            x: int = 1

        target = fake_prod_root / "state" / "probe.json"
        with pytest.raises(RuntimeError) as exc:
            safe_io_module.dump_model(target, _M())
        assert "atomic_write_text refused" in str(exc.value)
        assert not target.exists()

    def test_ndjson_append_refuses(self, safe_io_module, fake_prod_root):
        target = fake_prod_root / "logs" / "decisions.log"
        with pytest.raises(RuntimeError) as exc:
            safe_io_module.ndjson_append(target, "{}")
        assert "ndjson_append refused" in str(exc.value)
        assert not target.exists()

    def test_notify_owner_fallback_write_refuses(self, safe_io_module, fake_prod_root):
        """A pytest send whose Pushover bin fails would append to the deployed
        notify-failed.log dead-letter — the guard makes that a loud raise."""
        target = fake_prod_root / "logs" / "notify-failed.log"
        with pytest.raises(RuntimeError) as exc:
            safe_io_module.notify_owner_with_fallback(
                "title", "message", source="prod-write-guard-test",
                notify_owner_bin="/nonexistent/shift-agent-notify-owner",
                notify_failed_log=target,
                dedup_enabled=False,
            )
        assert "notify_owner_with_fallback refused" in str(exc.value)
        assert not target.exists()

    def test_guard_covers_real_prod_default(self, safe_io_module, monkeypatch):
        """Pin that the REAL /opt/shift-agent default root is what's guarded —
        independent of any file's existence (assert only that it raises)."""
        monkeypatch.delenv("SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST", raising=False)
        assert safe_io_module._PROD_AUDIT_ROOT == "/opt/shift-agent"
        with pytest.raises(RuntimeError):
            safe_io_module._refuse_prod_write_under_pytest(
                Path("/opt/shift-agent/logs/decisions.log"), helper="probe"
            )

    def test_tmp_paths_still_write(self, safe_io_module, monkeypatch, tmp_path):
        """Regression: the guard fires ONLY under the deployed root; ordinary
        tmp writes are unaffected."""
        monkeypatch.delenv("SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST", raising=False)
        target = tmp_path / "state" / "ok.json"
        safe_io_module.atomic_write_json(target, {"ok": 1})
        assert target.exists()
        log = tmp_path / "logs" / "decisions.log"
        safe_io_module.ndjson_append(log, '{"ok": 2}')
        assert log.read_text(encoding="utf-8").strip() == '{"ok": 2}'

    def test_bypass_env_allows_prod_root_write(self, safe_io_module, monkeypatch, tmp_path):
        """SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST=1 lets an on-box smoke write
        through every chokepoint (root monkeypatched to a tmp dir)."""
        root = tmp_path / "opt" / "shift-agent"
        monkeypatch.setattr(safe_io_module, "_PROD_AUDIT_ROOT", str(root))
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST", "1")
        target = root / "state" / "ok.json"
        safe_io_module.atomic_write_json(target, {"ok": 1})
        assert target.exists()

    def test_backcompat_alias_points_at_generalized_guard(self, safe_io_module):
        assert (
            safe_io_module._refuse_prod_audit_write_under_pytest
            is safe_io_module._refuse_prod_write_under_pytest
        )
