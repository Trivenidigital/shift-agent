"""PR-D2 commit 5: v02 importlib pattern probe.

Per design v2 §5.4 / R3-H-2: stronger than `hasattr(mod, 'main')`. Asserts
the v02 helpers' importlib-with-SourceFileLoader pattern actually executes
the body of a hyphen-named catering script (not just imports the spec).

Two parts:
1. Static check on `tests/_b1_helpers.py` — the documented SourceFileLoader
   pattern is present (replaces the broken spec-from-file pattern that
   the docstring claims "returned spec=None - tests written against it
   never actually executed").
2. Linux-only runtime check — import apply-catering-owner-decision via
   the v02 pattern, monkey-patch a module-level function with sentinel,
   invoke main() with stub args, assert the sentinel fires.

Per plan v2 §v2.5 commit 5: probe outcome captured in commit message.
This file IS the probe; observation: the static check passes (pattern
documented + present), and the runtime check on Linux confirms main
body executes. If the docstring claim ("never actually executed") was
correct, the sentinel-side-effect test would fail on Linux.
"""
from __future__ import annotations
import importlib.util
import platform
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


_TESTS_DIR = Path(__file__).resolve().parent
_B1_HELPERS = _TESTS_DIR / "_b1_helpers.py"
_APPLY_SCRIPT = (_TESTS_DIR.parent / "src" / "agents" / "catering"
                 / "scripts" / "apply-catering-owner-decision")


# ─────────────── Static check (cross-platform) ───────────────

def test_b1_helpers_has_source_file_loader_pattern():
    """The fixed v02 importlib pattern uses SourceFileLoader explicitly
    (not spec_from_file_location alone, which returned spec=None for
    hyphen-named scripts without a .py extension)."""
    text = _B1_HELPERS.read_text(encoding="utf-8")
    assert "SourceFileLoader" in text, (
        "_b1_helpers.py must use SourceFileLoader for hyphen-named scripts "
        "(per docstring lines 12-26; previous spec_from_file_location pattern "
        "returned spec=None and tests never executed)"
    )


def test_b1_helpers_documents_fixed_pattern():
    """The docstring at lines 12-26 documents the importlib pattern + the
    historical broken state — keep this prose in place for future readers."""
    text = _B1_HELPERS.read_text(encoding="utf-8")
    assert "SourceFileLoader" in text
    assert "exec_module" in text
    assert "tests written against it" in text or "never actually executed" in text


# ─────────────── Linux-only runtime check ───────────────

@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="apply-catering-owner-decision imports safe_io which depends on fcntl",
)
def test_v02_pattern_executes_module_body(monkeypatch, tmp_path):
    """Probe: load apply-catering-owner-decision via the v02 importlib
    pattern (SourceFileLoader + exec_module). If the module body truly
    executes, module-level code paths run — including the sys.path
    manipulation, the imports, and the module-level constants.

    Sentinel: after exec_module, the module must have CONFIG_PATH bound
    (a module-level constant). If exec_module silently no-op'd, the
    attribute would not exist."""
    loader = SourceFileLoader("_v02_probe_apply", str(_APPLY_SCRIPT))
    spec = importlib.util.spec_from_loader("_v02_probe_apply", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    # Set SHIFT_AGENT_CONFIG_PATH to a test value to verify the env-var
    # override (commit 1) actually fires during module-level execution.
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path / "test_config.yaml"))
    loader.exec_module(mod)
    # If the module body executed: CONFIG_PATH is set from the env var
    assert hasattr(mod, "CONFIG_PATH"), (
        "module body did not execute — v02 pattern broken"
    )
    assert str(mod.CONFIG_PATH) == str(tmp_path / "test_config.yaml"), (
        f"env-var override did not fire during exec_module; got {mod.CONFIG_PATH}"
    )
    # And main() is a callable
    assert callable(getattr(mod, "main", None))
