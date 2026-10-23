"""Pre-restart plugin compile gate in shift-agent-deploy.sh covers EVERY plugin.

The gateway imports every plugin under /root/.hermes/plugins at startup
(cf-router, shift-agent-policy, shift-agent-read). The gate used to compile
only cf-router's actions.py/hooks.py, so a syntax error in shift-agent-policy
or shift-agent-read reached the restart. These tests extract the gate's Python
heredoc from deploy.sh and run it against a temp plugin tree (target + mutant).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
PLUGINS = REPO / "src" / "plugins"
INSTALLED = "/root/.hermes/plugins"


def _gate_block() -> str:
    text = DEPLOY.read_text(encoding="utf-8")
    fn = text[text.index("post_install_gates_and_restart() {"):]
    start = fn.index("# Pre-restart plugin compile gate")
    return fn[start:fn.index("# Pre-restart cf-router enabled-state gate", start)]


def _gate_python() -> str:
    m = re.search(r"<<'PY' > /dev/null; then\n(.*?)\nPY\n", _gate_block(), re.DOTALL)
    assert m, "compile-gate heredoc not found"
    return m.group(1)


def test_gate_iterates_staged_plugin_dirs():
    block = _gate_block()
    assert '"$VENV_PY" - "$STAGING/src/plugins" <<\'PY\'' in block
    assert "rglob('*.py')" in _gate_python()
    # Every plugin shipped today is a directory under the staged src/plugins/.
    names = {d.name for d in PLUGINS.iterdir() if d.is_dir() and d.name != "__pycache__"}
    assert {"cf-router", "shift-agent-policy", "shift-agent-read"} <= names


def _run_gate(tmp_path: Path, staged: Path, installed: Path) -> subprocess.CompletedProcess:
    original = _gate_python()
    assert f"Path('{INSTALLED}')" in original
    script = original.replace(f"Path('{INSTALLED}')", f"Path({str(installed)!r})")
    return subprocess.run([sys.executable, "-", str(staged)], input=script,
                          capture_output=True, text=True)


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    staged = tmp_path / "staged"
    installed = tmp_path / "installed"
    for root in (staged, installed):
        for name in ("cf-router", "shift-agent-policy", "shift-agent-read"):
            (root / name).mkdir(parents=True)
            (root / name / "__init__.py").write_text("X = 1\n", encoding="utf-8")
        (root / "cf-router" / "actions.py").write_text("A = 1\n", encoding="utf-8")
        (root / "cf-router" / "hooks.py").write_text("H = 1\n", encoding="utf-8")
    return staged, installed


def test_gate_passes_clean_tree(tmp_path):
    staged, installed = _tree(tmp_path)
    r = _run_gate(tmp_path, staged, installed)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("plugin, rel", [
    ("cf-router", "hooks.py"),
    ("shift-agent-policy", "policy.py"),
    ("shift-agent-read", "equipment_tool.py"),
])
def test_gate_fails_on_syntax_error_in_any_plugin(tmp_path, plugin, rel):
    staged, installed = _tree(tmp_path)
    (installed / plugin / rel).write_text("def broken(:\n", encoding="utf-8")
    r = _run_gate(tmp_path, staged, installed)
    assert r.returncode != 0
    assert "SyntaxError" in r.stderr and plugin in r.stderr


def test_gate_fails_when_staged_plugin_not_installed(tmp_path):
    staged, installed = _tree(tmp_path)
    for f in (installed / "shift-agent-read").iterdir():
        f.unlink()
    (installed / "shift-agent-read").rmdir()
    r = _run_gate(tmp_path, staged, installed)
    assert r.returncode != 0
    assert "shift-agent-read" in r.stderr
