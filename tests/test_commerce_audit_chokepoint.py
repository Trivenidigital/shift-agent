"""CI gate: commerce primitives must NOT write to decisions.log directly.

All audit writes go through commerce.audit.emit -> safe_io.ndjson_append.
This test greps src/platform/commerce/ for forbidden patterns. Per
Reviewer A MEDIUM-5 + PRD v2 §11 verification gate #4.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


COMMERCE_DIR = Path(__file__).resolve().parent.parent / "src" / "platform" / "commerce"


# Files allowed to reference "decisions.log" in comments / docstrings only.
ALLOWED_FILES = {"audit.py"}


@pytest.fixture
def commerce_py_files() -> list[Path]:
    files = sorted(COMMERCE_DIR.glob("*.py"))
    assert files, "commerce primitive package missing"
    return files


def test_no_direct_writes_to_decisions_log(commerce_py_files):
    """No commerce module may directly open/write to decisions.log.

    Forbidden patterns (anything that bypasses commerce.audit.emit):
    - open(...decisions.log...)
    - .write(...decisions.log...)
    - subprocess.run([..., 'log-decision-direct', ...])   <- allowed only via audit.py
    """
    forbidden = re.compile(r"(open|write|append).{0,80}decisions\.log", re.IGNORECASE)
    violations = []
    for f in commerce_py_files:
        if f.name in ALLOWED_FILES:
            continue
        content = f.read_text(encoding="utf-8")
        # Strip docstring / comment context — if the only mention is in a
        # comment, it's not a violation. Crude but sufficient: lines that
        # start with '#' or are inside triple-quoted strings.
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if forbidden.search(line):
                violations.append(f"{f.name}:{lineno}: {line.strip()}")
    assert not violations, (
        "commerce primitives bypassed the audit chokepoint:\n"
        + "\n".join(violations)
        + "\nAll audit writes MUST go through commerce.audit.emit "
        "which routes through safe_io.ndjson_append."
    )


def test_audit_module_uses_ndjson_append(commerce_py_files):
    """commerce.audit must route through safe_io.ndjson_append (the chokepoint)."""
    audit_py = COMMERCE_DIR / "audit.py"
    content = audit_py.read_text(encoding="utf-8")
    assert "from safe_io import ndjson_append" in content or "import safe_io" in content
    assert "ndjson_append(" in content


def test_no_module_imports_log_decision_direct_subprocess(commerce_py_files):
    """Slice 1: commerce primitives must not shell out to log-decision-direct.

    (audit.py uses safe_io.ndjson_append directly — same chokepoint as the
    deployed log-decision-direct script, just in-process.)
    """
    forbidden = re.compile(r"log-decision-direct|log_decision_direct")
    violations = []
    for f in commerce_py_files:
        content = f.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if forbidden.search(line):
                violations.append(f"{f.name}:{lineno}: {line.strip()}")
    assert not violations, (
        "commerce primitives reference log-decision-direct outside comments:\n"
        + "\n".join(violations)
    )
