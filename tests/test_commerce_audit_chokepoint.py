"""CI gate: commerce primitives must NOT write to decisions.log directly.

All audit writes go through commerce.audit.emit -> _io_shim.ndjson_append.
This test uses ast.parse to walk every Call node and detect direct
file-write operations referencing "decisions.log" — robust to multi-line
docstrings and comments (Reviewer A MEDIUM-4 fix).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


COMMERCE_DIR = Path(__file__).resolve().parent.parent / "src" / "platform" / "commerce"

# Files allowed to reference "decisions.log" / ndjson_append directly.
ALLOWED_FILES = {"audit.py", "_io_shim.py"}


@pytest.fixture
def commerce_py_files() -> list[Path]:
    files = sorted(COMMERCE_DIR.glob("*.py"))
    assert files, "commerce primitive package missing"
    return files


def _string_constants_in_module(tree: ast.Module) -> list[tuple[str, int]]:
    """Walk AST and yield (string_value, lineno) for every str constant
    that is NOT a docstring (i.e., NOT the first Expr in a module / class /
    function body)."""
    docstring_node_ids: set[int] = set()

    def _mark_docstring(body: list[ast.stmt]) -> None:
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            docstring_node_ids.add(id(body[0].value))

    _mark_docstring(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _mark_docstring(node.body)

    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_node_ids:
                continue
            out.append((node.value, node.lineno))
    return out


def test_no_direct_writes_to_decisions_log(commerce_py_files):
    """No commerce module may construct a file path containing 'decisions.log'
    outside the audit chokepoint. AST-based detection (not line-prefix grep)
    catches multi-line docstrings, f-strings, and split-string concatenations.
    """
    pattern = re.compile(r"decisions\.log", re.IGNORECASE)
    violations: list[str] = []
    for f in commerce_py_files:
        if f.name in ALLOWED_FILES:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for value, lineno in _string_constants_in_module(tree):
            if pattern.search(value):
                violations.append(f"{f.name}:{lineno}: string-literal references decisions.log: {value!r}")
    assert not violations, (
        "commerce primitives reference decisions.log outside the audit chokepoint:\n"
        + "\n".join(violations)
        + "\nAll audit writes MUST go through commerce.audit.emit."
    )


def test_audit_module_uses_io_shim(commerce_py_files):
    """commerce.audit must route through _io_shim.ndjson_append (the chokepoint)."""
    audit_py = COMMERCE_DIR / "audit.py"
    content = audit_py.read_text(encoding="utf-8")
    assert "from ._io_shim import ndjson_append" in content or "from commerce._io_shim import ndjson_append" in content
    assert "ndjson_append(" in content


def test_io_shim_routes_through_safe_io_on_linux(commerce_py_files):
    """_io_shim.py must import safe_io.ndjson_append on the Linux production
    path (Windows fallback is for dev/test only)."""
    shim_py = COMMERCE_DIR / "_io_shim.py"
    content = shim_py.read_text(encoding="utf-8")
    assert "from safe_io import" in content
    assert "ndjson_append" in content


def test_no_module_shells_out_to_log_decision_direct(commerce_py_files):
    """Slice 1: commerce primitives must not shell out to log-decision-direct.

    Same chokepoint as the deployed log-decision-direct script, just in-process
    via safe_io.ndjson_append.
    """
    pattern = re.compile(r"log-decision-direct|log_decision_direct")
    violations: list[str] = []
    for f in commerce_py_files:
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for value, lineno in _string_constants_in_module(tree):
            if pattern.search(value):
                violations.append(f"{f.name}:{lineno}: references log-decision-direct: {value!r}")
    assert not violations, (
        "commerce primitives reference log-decision-direct outside comments:\n"
        + "\n".join(violations)
    )
