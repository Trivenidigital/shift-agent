"""BL-CI-04: enforce the pre-restart chokepoint-symbol invariants in CI, not only at deploy.

`check-safe-io-symbols` and `check-audit-helpers-symbols` are the single sources of truth for the
module attributes the catering scripts + gateway depend on; the deploy smoke gate fail-closes +
auto-rolls-back if any is missing. But nothing catches a dropped symbol in CI — a refactor could
remove one, pass CI green, and only break at deploy-time rollback (after a gateway restart attempt).
These tests read each gate's `REQUIRED_SYMBOLS` and assert every name is defined/exported in its
module, so the regression fails in CI first.

Text-based (safe_io imports fcntl → not importable on the Windows dev box), so they run
cross-platform.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "src" / "platform" / "scripts"
PLATFORM = REPO / "src" / "platform"

# (gate script → the module it imports + hasattr-checks)
_GATES = [
    (SCRIPTS / "check-safe-io-symbols", PLATFORM / "safe_io.py"),
    (SCRIPTS / "check-audit-helpers-symbols", PLATFORM / "audit_helpers.py"),
]


def _required_symbols(gate: Path) -> tuple[str, ...]:
    m = re.search(r"REQUIRED_SYMBOLS\s*=\s*\(([^)]*)\)", gate.read_text(encoding="utf-8"), re.DOTALL)
    assert m, f"REQUIRED_SYMBOLS tuple not found in {gate.name}"
    return tuple(re.findall(r'"([^"]+)"', m.group(1)))


def _module_defines(text: str, name: str) -> bool:
    n = re.escape(name)
    return any(re.search(p, text, re.MULTILINE) for p in (
        rf"^\s*def\s+{n}\b",           # function
        rf"^\s*class\s+{n}\b",         # class
        rf"^\s*{n}\s*[:=]",            # module-level assignment / annotation
        rf"\bimport\b[^\n#]*\b{n}\b",  # re-exported via import (excludes comment lines)
    ))


@pytest.mark.parametrize("gate, module", _GATES, ids=["safe_io", "audit_helpers"])
def test_gate_required_symbols_defined_in_module(gate, module):
    symbols = _required_symbols(gate)
    assert symbols, f"{gate.name} declares no REQUIRED_SYMBOLS"
    text = module.read_text(encoding="utf-8")
    missing = [s for s in symbols if not _module_defines(text, s)]
    assert not missing, (
        f"{module.name} is missing chokepoint symbol(s) {missing} required by {gate.name} — "
        f"a deploy would fail-closed + roll back at the pre-restart gate; this test catches it "
        f"in CI first (BL-CI-04)."
    )
