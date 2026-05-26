"""PR-ε static gate: enforce ONE canonical bridge_post per send family.

Invariant: no local helper may perform bridge network I/O (urllib.request POST
to the bridge /send endpoint) directly. All text-send paths must route through
``safe_io.bridge_post`` or its 2-tuple compatibility adapter
``safe_io.bridge_post_2tuple``. Media and CTA sends have their own canonical
helpers (``bridge_send_media``, ``bridge_send_cta``) and are out of scope for
this gate — the rule is "one canonical helper PER send family," not "every
send collapses to bridge_post."

This test exists to catch regressions where a new script grows a local
``_bridge_post`` helper (the historical pattern in catering / expense before
PR-ε consolidation). Such a helper bypasses retry/observability/test-gating
discipline in safe_io.bridge_post and silently fragments the chokepoint.

Allowlist (explicit, narrow, time-bounded):
  - src/platform/safe_io.py — defines the canonical helpers.
  - src/agents/shift/scripts/send-coverage-message — has a custom
    ``timeout=15`` parameter that the canonical helper does not yet expose.
    Deferred to PR-ε.1; remove from allowlist once safe_io.bridge_post grows
    a timeout kwarg and this script is migrated.
  - src/agents/flyer/scripts/send-flyer-package — fallback stubs declared
    inside ``try/except ModuleNotFoundError`` for environments missing
    safe_io. These are not live callers when safe_io imports successfully.
  - tools/synthetic-retry-harness.py — test harness mock
    (``_bridge_post_mock``). Test-side mocks do not constitute live send
    paths.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Paths scanned by the gate. Restricted to runtime-reachable source: src/ and
# tools/. We do NOT scan tests/, evidence/, or docs/ — these contain example
# snippets that legitimately illustrate the helper shape.
SCAN_ROOTS = [REPO / "src", REPO / "tools"]

ALLOWLIST = {
    # Canonical chokepoint definitions live here.
    REPO / "src" / "platform" / "safe_io.py",
    # Deferred to PR-ε.1 — has bespoke timeout=15 param.
    REPO / "src" / "agents" / "shift" / "scripts" / "send-coverage-message",
    # Fallback shims inside try/except ModuleNotFoundError block.
    REPO / "src" / "agents" / "flyer" / "scripts" / "send-flyer-package",
    # Test harness mock (not a live send path).
    REPO / "tools" / "synthetic-retry-harness.py",
}

# Match function definitions named bridge_post or _bridge_post. Word-anchored
# so we do not accidentally match `bridge_post_2tuple` (the canonical adapter)
# or `bridge_post_mock` (a test mock — separately allowlisted by file).
DEF_RE = re.compile(r"^\s*def\s+_?bridge_post\b(?!_)", re.MULTILINE)


def _iter_source_files() -> list[Path]:
    """Yield every regular file under src/ and tools/ — both .py and
    extensionless scripts (catering/expense bins have no .py suffix)."""
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            # Skip binary-ish or generated dirs.
            if "__pycache__" in p.parts or ".pyc" in p.suffix:
                continue
            files.append(p)
    return files


def test_no_local_bridge_post_definitions_outside_allowlist():
    """Every file under src/ and tools/ that defines a function matching
    ``_?bridge_post`` (text-send chokepoint) must be in the explicit
    allowlist. Adding a new local helper without first widening the
    allowlist (with justification in the PR description) is a regression."""
    offenders: list[tuple[Path, int, str]] = []
    for f in _iter_source_files():
        if f in ALLOWLIST:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in DEF_RE.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            offenders.append((f.relative_to(REPO), line_no, m.group(0).strip()))

    if offenders:
        lines = [
            f"  {p}:{ln}  →  {snip}" for p, ln, snip in offenders
        ]
        pytest.fail(
            "PR-ε static gate violated: local bridge_post helper(s) defined "
            "outside the allowlist. Route the send through "
            "safe_io.bridge_post (4-tuple) or safe_io.bridge_post_2tuple "
            "(legacy 2-tuple), or extend ALLOWLIST in this test with "
            "explicit justification in the PR description.\n"
            + "\n".join(lines)
        )


def test_allowlist_files_actually_exist():
    """Guard against allowlist rot — every allowlisted path must exist.
    If a file is removed without updating the allowlist, the gate is
    silently weakened against unrelated future drift."""
    missing = [p for p in ALLOWLIST if not p.exists()]
    assert not missing, (
        "PR-ε allowlist references nonexistent paths "
        "(remove from ALLOWLIST or restore the file):\n"
        + "\n".join(f"  {p.relative_to(REPO)}" for p in missing)
    )


def test_canonical_helpers_present_in_safe_io():
    """Sanity check: the canonical chokepoint exposes the helpers the gate
    points consumers at. If these get renamed/removed, the gate is meaningless."""
    safe_io_text = (REPO / "src" / "platform" / "safe_io.py").read_text(encoding="utf-8")
    for fn in ("bridge_post", "bridge_post_2tuple"):
        assert re.search(rf"^def\s+{fn}\s*\(", safe_io_text, re.MULTILINE), (
            f"safe_io.py must define `{fn}` — gate is meaningless otherwise."
        )
