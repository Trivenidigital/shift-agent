"""PR-ζ static gate: every direct bridge_post* callsite either passes
action_context= OR lives in a file whose basename is in SAFE_IO_NULL_CONTEXT_ALLOWLIST.

Scope (per plan REV 2 + design REV 2 §F6): regression defense against NEW
direct callsites only. The AST scanner CANNOT detect:
- Indirect calls: `fn = bridge_post; fn(...)` (e.g. manual_queue.py:600,634)
- `getattr(safe_io, 'bridge_post')(...)` dynamic dispatch
- Wrapper helpers like `send_flyer_text` that call `bridge_post` internally

For those, runtime detection is the safety net: an unexpected caller
basename lands as a `regulated_send_missing_action_context` audit row at
runtime. PR-η (planned) adds a periodic audit-log report grouping
caller_script to surface indirect-call escapees post-deploy.
"""
from __future__ import annotations

import ast
import platform
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCAN_ROOTS = [REPO / "src", REPO / "tools"]
TARGET_FUNCS = {"bridge_post", "bridge_post_2tuple", "bridge_send_media", "bridge_send_cta"}
# safe_io.py is the canonical chokepoint module — its internal calls (e.g.
# bridge_post_2tuple → bridge_post) are not customer-facing bypasses and
# would otherwise trip the gate. Special-case here.
SCAN_SKIP_FILES = {"safe_io.py"}

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io uses fcntl (Linux only)",
)


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if "__pycache__" in p.parts or ".pyc" in p.suffix:
                continue
            files.append(p)
    return files


def _is_executable_source(p: Path) -> bool:
    if p.suffix == ".py":
        return True
    if p.suffix == "" and "scripts" in p.parts:
        return True
    return False


def _load_allowlist() -> frozenset[str]:
    platform_dir = REPO / "src" / "platform"
    sys.path.insert(0, str(platform_dir))
    try:
        import safe_io
        return safe_io.SAFE_IO_NULL_CONTEXT_ALLOWLIST
    finally:
        sys.path.pop(0)


def _scan_direct_callsites(text: str) -> list[tuple[int, str, bool]]:
    """Return list of (line_no, func_name, has_action_context_kwarg) for
    direct calls to bridge_post / bridge_post_2tuple / bridge_send_media /
    bridge_send_cta."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    hits: list[tuple[int, str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        else:
            continue
        if fname not in TARGET_FUNCS:
            continue
        has_ctx = any(kw.arg == "action_context" for kw in node.keywords)
        hits.append((node.lineno, fname, has_ctx))
    return hits


def test_every_direct_callsite_passes_context_or_is_allowlisted():
    """Direct calls to bridge_post* must EITHER pass action_context= OR
    live in a file whose basename is allowlisted."""
    allowlist = _load_allowlist()
    offenders: list[tuple[Path, int, str]] = []
    for f in _iter_source_files():
        if not _is_executable_source(f):
            continue
        if f.name in SCAN_SKIP_FILES:
            continue  # safe_io.py self-calls
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, fname, has_ctx in _scan_direct_callsites(text):
            if has_ctx:
                continue  # explicit context passed
            if f.name in allowlist:
                continue  # caller-file basename allowlisted
            offenders.append((f.relative_to(REPO), line_no, fname))

    if offenders:
        lines = [
            f"  {p}:{ln} → {fn}() without action_context="
            for p, ln, fn in offenders
        ]
        pytest.fail(
            "PR-ζ static gate violated: direct bridge_post* callsite(s) "
            "outside SAFE_IO_NULL_CONTEXT_ALLOWLIST and without "
            "action_context= kwarg. Either pass an ActionExecutionContext "
            "or add the file basename to the allowlist with explicit "
            "per-file justification in the PR description.\n"
            + "\n".join(lines)
        )


def test_allowlist_files_exist_and_are_unique():
    """Each allowlisted basename must resolve to exactly ONE file under the
    scan roots. Missing files weaken the gate (stale entry); duplicate
    basenames make the allowlist match ambiguous."""
    allowlist = _load_allowlist()
    all_files = [
        f for root in SCAN_ROOTS if root.exists()
        for f in root.rglob("*") if f.is_file()
    ]
    by_basename: dict[str, list[Path]] = {}
    for f in all_files:
        by_basename.setdefault(f.name, []).append(f)

    missing = [name for name in allowlist if name not in by_basename]
    assert not missing, (
        f"SAFE_IO_NULL_CONTEXT_ALLOWLIST references nonexistent files "
        f"(remove from allowlist or restore the file): {missing}"
    )

    duplicates = {
        name: [str(p.relative_to(REPO)) for p in paths]
        for name, paths in by_basename.items()
        if name in allowlist and len(paths) > 1
    }
    assert not duplicates, (
        f"SAFE_IO_NULL_CONTEXT_ALLOWLIST basename collisions (ambiguous "
        f"caller-resolution match): {duplicates}"
    )


def test_canonical_helpers_in_safe_io():
    """The chokepoint functions the allowlist points to must exist."""
    text = (REPO / "src" / "platform" / "safe_io.py").read_text(encoding="utf-8")
    for fn in TARGET_FUNCS:
        assert f"def {fn}" in text, (
            f"safe_io.py must define {fn} — gate is meaningless otherwise."
        )


def test_allowlist_is_frozenset():
    """Caller cannot mutate the allowlist at runtime."""
    allowlist = _load_allowlist()
    assert isinstance(allowlist, frozenset), (
        "SAFE_IO_NULL_CONTEXT_ALLOWLIST must be a frozenset"
    )


def test_change_plan_callsite_now_passes_context():
    """The cf-router/hooks.py change_plan dispatch must pass action_context.
    Verifies F8 commit 5 landed."""
    hooks_path = REPO / "src" / "plugins" / "cf-router" / "hooks.py"
    text = hooks_path.read_text(encoding="utf-8")
    assert "ActionExecutionContext(" in text, (
        "hooks.py must construct ActionExecutionContext for the change_plan path"
    )
    assert "flyer.billing.request_plan_change" in text, (
        "hooks.py must reference the flyer.billing.request_plan_change action_id"
    )
    assert "plan_change_requested" in text, (
        "hooks.py must check result.detail for plan_change_requested signal"
    )
