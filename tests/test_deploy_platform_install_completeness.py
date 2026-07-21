"""ops/deploy-platform-install-completeness — drift-guard proving the deploy
script's EXPLICIT per-file platform-module install list is a SUPERSET of the flat
``src/platform`` modules that installed code actually imports.

2026-07-21 incident: ``catering_recompose`` / ``catering_quote_ledger`` /
``catering_lead_sweep`` were added to ``src/platform/`` and imported by the catering
proposal + quote scripts, but never added to ``shift-agent-deploy.sh``'s
``install -m 644 src/platform/<name>.py`` list. On the flat ``/opt/shift-agent``
layout a script's ``from catering_recompose import ...`` resolves ONLY if the module
was installed there, so the first live proposal inbound raised ImportError in
production. Every presence/smoke check passed; nothing caught the gap.

This guard is PURE STATIC ANALYSIS (no subprocess, no SSH, no box), so — unlike the
Linux-only extract-and-run deploy tests (test_deploy_retired_template.py etc.) — it
runs on EVERY platform. It:

  * extracts ``installed_set`` = flat modules the deploy script installs,
  * extracts ``imported_set``  = flat ``src/platform`` modules imported by in-scope
    code (everything under ``src/agents/*/scripts/`` + ``src/platform/*.py``), by
    AST-parsing each Python file and collecting level-0 ``import`` / ``from`` targets
    whose top name resolves to a ``src/platform/<name>.py`` module,
  * asserts ``imported_set ⊆ installed_set`` — the assertion the incident tripped.

Fail-closed by construction: an unrecognized ``install ... src/platform/...`` shape,
or an in-scope Python file that will not AST-parse, FAILS loudly rather than silently
narrowing either set (a hidden install shape or a broken script could otherwise mask a
missing module). A negative self-test proves the guard flags an omitted module so it
can never be vacuously green.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
PLATFORM_DIR = REPO / "src" / "platform"
TEXT = DEPLOY.read_text(encoding="utf-8")

# The three modules the 2026-07-21 incident dropped from the install list.
INCIDENT_MODULES = ("catering_recompose", "catering_quote_ledger", "catering_lead_sweep")

# A flat platform module install: source is EXACTLY src/platform/<name>.py (no subdir).
# The destination varies (/opt/shift-agent/<name>.py, /usr/local/share/... for
# skills_manifest.py); only the SOURCE basename is captured.
_FLAT_INSTALL = re.compile(r"\binstall\s+-m\s+\d+\s+src/platform/([A-Za-z_]\w*)\.py(?=\s|$)")
# Known NON-flat install shapes under src/platform/ that legitimately do not map to a
# single flat module (subpackage glob + scripts/systemd globs). Any src/platform
# install line matching NEITHER the flat regex NOR one of these is "unclassified" → FAIL.
_EXEMPT_PLATFORM_INSTALL = ("src/platform/scripts/", "src/platform/commerce/", "src/platform/systemd/")


# ────────────────────────────────────────────────────────────────────────────
# Pure extractors (reused by the real assertions AND the negative self-tests)
# ────────────────────────────────────────────────────────────────────────────
def platform_module_names(platform_dir: Path) -> set[str]:
    """Basenames of the flat ``src/platform/*.py`` modules (the import universe)."""
    return {p.stem for p in Path(platform_dir).glob("*.py") if p.stem != "__init__"}


def installed_platform_modules(deploy_text: str) -> tuple[set[str], list[str]]:
    """(installed_set, unclassified_lines) from the deploy script text.

    ``installed_set`` = flat module names on ``install -m N src/platform/<name>.py`` lines.
    ``unclassified_lines`` = executable install lines touching src/platform/ that match
    neither the flat shape nor the exempt non-flat shapes → the fail-closed signal.
    """
    installed: set[str] = set()
    unclassified: list[str] = []
    for raw in deploy_text.splitlines():
        s = raw.strip()
        if s.startswith("#") or "src/platform/" not in s:
            continue
        if not re.search(r"\binstall\s+-m\s+\d+\s+src/platform/", s):
            continue
        m = _FLAT_INSTALL.search(s)
        if m:
            installed.add(m.group(1))
            continue
        if any(pref in s for pref in _EXEMPT_PLATFORM_INSTALL):
            continue
        unclassified.append(raw)
    return installed, unclassified


def _is_inscope_python_candidate(path: Path) -> bool:
    """True iff ``path`` is genuine in-scope Python SOURCE — decided WITHOUT decoding
    the body, so compiled bytecode never reaches the fail-closed unparseable path.

    In-scope = NOT under a ``__pycache__`` component AND either an exact ``.py`` suffix
    OR an extension-less file whose shebang names python (the deployed scripts, which
    are run as ``#!/usr/bin/env python3`` with no ``.py`` extension). A ``.pyc`` has
    suffix ``.pyc`` (neither ``.py`` nor empty) → excluded; a bash script has suffix
    ``.sh`` or a non-python shebang → excluded. The shebang peek reads BYTES, so a
    stray binary here can never raise a decode error.
    """
    if "__pycache__" in path.parts:
        return False
    suffix = path.suffix
    if suffix == ".py":
        return True
    if suffix == "":
        try:
            with path.open("rb") as fh:
                head = fh.readline(256)
        except OSError:
            return False  # unreadable / a directory — not a source candidate
        return head.startswith(b"#!") and b"python" in head
    return False


def imported_platform_modules(files, platform_modules: set[str]) -> tuple[set[str], list[Path]]:
    """(imported_set, unparseable_files) across ``files``.

    Collects level-0 ``import <name>`` / ``from <name> import ...`` targets whose top
    module name is a flat ``src/platform`` module. Only genuine in-scope Python source
    (see ``_is_inscope_python_candidate``) is considered; a candidate that then cannot be
    decoded or AST-parsed is returned in ``unparseable_files`` (fail-closed: we cannot
    prove it contains no such import). Non-Python files (bash scripts, ``.pyc`` bytecode,
    anything under ``__pycache__``) are skipped BEFORE any text read.
    """
    imported: set[str] = set()
    unparseable: list[Path] = []
    for f in files:
        path = Path(f)
        if not _is_inscope_python_candidate(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            unparseable.append(path)
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            unparseable.append(path)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in platform_modules:
                        imported.add(top)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    top = node.module.split(".")[0]
                    if top in platform_modules:
                        imported.add(top)
    return imported, unparseable


# Real-tree inputs. Scan roots per spec: every file under src/agents/*/scripts/ plus
# every src/platform/*.py.
PLATFORM_MODULES = platform_module_names(PLATFORM_DIR)
SCRIPT_FILES = sorted(p for p in (REPO / "src" / "agents").glob("*/scripts/**/*") if p.is_file())
PLATFORM_FILES = sorted(PLATFORM_DIR.glob("*.py"))
SCAN_FILES = SCRIPT_FILES + PLATFORM_FILES


# ════════════════════════════════════════════════════════════════════════════
# The guard (all cells run everywhere — pure static analysis)
# ════════════════════════════════════════════════════════════════════════════
def test_no_unclassified_platform_install_lines():
    """Fail-closed: every executable src/platform install line is a known shape."""
    _installed, unclassified = installed_platform_modules(TEXT)
    assert unclassified == [], (
        "unrecognized `install ... src/platform/...` shape(s) — extend _FLAT_INSTALL or "
        "_EXEMPT_PLATFORM_INSTALL so the completeness guard cannot be blinded:\n  "
        + "\n  ".join(unclassified)
    )


def test_no_unparseable_inscope_python():
    """Fail-closed: every in-scope Python file AST-parses (else it could hide an import)."""
    _imported, unparseable = imported_platform_modules(SCAN_FILES, PLATFORM_MODULES)
    assert unparseable == [], "in-scope Python that failed to read/AST-parse:\n  " + "\n  ".join(
        str(p) for p in unparseable
    )


def test_imported_platform_modules_are_all_installed():
    """THE incident assertion: no flat src/platform module is imported yet uninstalled."""
    imported, unparseable = imported_platform_modules(SCAN_FILES, PLATFORM_MODULES)
    assert unparseable == [], "fail-closed on unparseable in-scope Python (see other cell)"
    installed, unclassified = installed_platform_modules(TEXT)
    assert unclassified == [], "fail-closed on unclassified install line (see other cell)"
    missing = imported - installed
    assert not missing, (
        "platform module(s) imported by installed code but MISSING from the deploy "
        "install list — the 2026-07-21 ImportError-in-prod class. Add an "
        "`install -m 644 src/platform/<name>.py /opt/shift-agent/<name>.py` line for: "
        + ", ".join(sorted(missing))
    )


def test_incident_modules_now_installed():
    """The three modules the incident dropped are explicitly back in the install list."""
    installed, _unclassified = installed_platform_modules(TEXT)
    for m in INCIDENT_MODULES:
        assert m in installed, f"{m} must be in the deploy platform-install list"


def test_pre_restart_import_gate_loadability_smokes_catering_modules():
    """Deliverable 3: the pre-restart import gate import-tests the flat catering modules,
    so a future dropped module rolls the deploy back BEFORE the gateway restarts."""
    assert re.search(
        r"import\s+catering_recompose,\s*catering_quote_ledger,\s*catering_lead_sweep,\s*catering_amendments",
        TEXT,
    ), "pre-restart import gate must import-test catering_recompose/quote_ledger/lead_sweep/amendments"


# ════════════════════════════════════════════════════════════════════════════
# Negative self-tests — prove the guard is not vacuously green
# ════════════════════════════════════════════════════════════════════════════
def test_guard_flags_a_missing_module_negative_selftest(tmp_path):
    """A fake in-scope script imports a fake platform module the install list omits;
    the extractors must surface it as missing."""
    plat = tmp_path / "platform"
    plat.mkdir()
    (plat / "fakemod.py").write_text("VALUE = 1\n", encoding="utf-8")
    scripts = tmp_path / "agents" / "svc" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "uses-fakemod").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.path.insert(0, '/opt/shift-agent')\n"
        "from fakemod import VALUE  # noqa: E402\n",
        encoding="utf-8",
    )
    mods = platform_module_names(plat)
    assert mods == {"fakemod"}

    imported, unparseable = imported_platform_modules(sorted(scripts.glob("*")), mods)
    assert unparseable == []
    assert imported == {"fakemod"}, "the guard must detect the flat `from fakemod import`"

    installed, unclassified = installed_platform_modules("echo 'no installs here'\n")
    assert unclassified == [] and installed == set()
    assert (imported - installed) == {"fakemod"}, "an omitted-yet-imported module must be flagged"


def test_guard_fails_closed_on_unparseable_python_negative_selftest(tmp_path):
    """A syntactically broken python script is reported unparseable, not silently skipped."""
    broken = tmp_path / "broken-script"
    broken.write_text("#!/usr/bin/env python3\ndef (:\n", encoding="utf-8")
    _imported, unparseable = imported_platform_modules([broken], {"anything"})
    assert unparseable == [broken]


def test_pycache_bytecode_is_not_selected_negative_selftest(tmp_path):
    """Reproduces the PR #638 CI failure: a __pycache__/*.pyc under a scan root (present
    on Linux where prior runs compiled bytecode, absent on the Windows worktree) must be
    excluded WITHOUT being read — binary bytecode must never reach the fail-closed
    unparseable path. A real .py sibling proves selection still works alongside it."""
    scripts = tmp_path / "agents" / "svc" / "scripts"
    (scripts / "__pycache__").mkdir(parents=True)
    pyc = scripts / "__pycache__" / "foo.cpython-311.pyc"
    pyc.write_bytes(b"\x42\x0d\x0d\x0a\x00\x00\x00\x00\xff\xfe not-utf8 bytecode")
    (scripts / "real.py").write_text("import os\n", encoding="utf-8")

    assert _is_inscope_python_candidate(pyc) is False
    imported, unparseable = imported_platform_modules(sorted(scripts.rglob("*")), {"os"})
    assert unparseable == [], "bytecode/__pycache__ must not reach the unparseable path"
    assert imported == {"os"}, "the real .py sibling must still be parsed"


def test_guard_fails_closed_on_unclassified_install_line_negative_selftest():
    """An install line touching src/platform in an unrecognized shape is flagged, not ignored."""
    _installed, unclassified = installed_platform_modules(
        "    install -m 644 src/platform/weird-shape.dat /opt/shift-agent/weird\n"
    )
    assert len(unclassified) == 1
