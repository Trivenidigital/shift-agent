"""PR-ε static gate: enforce ONE canonical bridge_post per send family.

Invariant: no local helper may perform bridge network I/O (urllib.request POST
to the bridge /send endpoint) directly. All text-send paths must route through
``safe_io.bridge_post`` or its 2-tuple compatibility adapter
``safe_io.bridge_post_2tuple``. Media and CTA sends have their own canonical
helpers (``bridge_send_media``, ``bridge_send_cta``) and are out of scope for
this gate — the rule is "one canonical helper PER send family," not "every
send collapses to bridge_post."

Two complementary detectors:

1. **Name-shape detector** (``test_no_local_bridge_post_definitions...``) —
   matches ``def _?bridge_post`` function definitions. Catches the historical
   catering / expense shape PR-ε consolidated. Word-anchored so it ignores
   ``bridge_post_2tuple`` (canonical adapter) and ``_bridge_post_mock``
   (allowlisted test mock).

2. **URL-shape detector** (``test_no_direct_bridge_send_url...``) — matches
   literal references to the bridge ``/send`` endpoint
   (``127.0.0.1:3000/send``) in executable source. Catches bypasses that
   skip the helper-name shape entirely — e.g. inline
   ``urllib.request.Request("http://127.0.0.1:3000/send", ...)`` calls. This
   was a real gap in the v1 gate: ``shift-agent-notify-owner`` builds its
   own fallback POST without ever defining ``_bridge_post``.

Each detector carries its own allowlist with its own per-file rationale —
allowlisting for one detector does NOT silently allowlist for the other.
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

# Allowlist for the name-shape detector (def _?bridge_post).
DEF_ALLOWLIST = {
    # Canonical chokepoint definitions live here.
    REPO / "src" / "platform" / "safe_io.py",
    # Deferred to PR-ε.1 — has bespoke timeout=15 param.
    REPO / "src" / "agents" / "shift" / "scripts" / "send-coverage-message",
    # Fallback shims inside try/except ModuleNotFoundError block.
    REPO / "src" / "agents" / "flyer" / "scripts" / "send-flyer-package",
    # Test harness mock (not a live send path).
    REPO / "tools" / "synthetic-retry-harness.py",
}

# Allowlist for the URL-shape detector (literal /send endpoint reference).
# Narrower than DEF_ALLOWLIST — only the files that legitimately reference
# the URL string in executable code.
URL_ALLOWLIST = {
    # Canonical chokepoint. safe_io.bridge_post resolves the URL from
    # HERMES_BRIDGE_URL env var with this as the default.
    REPO / "src" / "platform" / "safe_io.py",
    # Deferred to PR-ε.1 — local helper carries a bespoke timeout=15 kwarg
    # the canonical doesn't yet expose. Migrate when safe_io.bridge_post
    # grows a timeout parameter.
    REPO / "src" / "agents" / "shift" / "scripts" / "send-coverage-message",
    # OUT-OF-BAND OWNER ALERT FALLBACK. This script's whatsapp_fallback()
    # only fires when (a) Pushover, the primary out-of-band channel, has
    # failed AND (b) the bridge is the last-resort path to reach the
    # owner. Coupling it to safe_io.bridge_post would introduce a
    # circular dependency at the worst possible moment — when the agent
    # is already in a degraded state and the chokepoint may itself be
    # the failed path being alerted about. Intentional bypass with
    # explicit rationale; consider re-evaluating only if safe_io grows
    # a "raw, no-instrumentation" send variant.
    REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-notify-owner",
}

# Match function definitions named bridge_post or _bridge_post. Word-anchored
# so we do not accidentally match `bridge_post_2tuple` (the canonical adapter)
# or `bridge_post_mock` (a test mock — separately allowlisted by file).
DEF_RE = re.compile(r"^\s*def\s+_?bridge_post\b(?!_)", re.MULTILINE)

# Match the literal bridge /send endpoint. Catches it regardless of how the
# URL is spelled — string literal, constant assignment, f-string base.
BRIDGE_URL_RE = re.compile(r"127\.0\.0\.1:3000/send")


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


def _is_executable_source(p: Path) -> bool:
    """The URL-shape detector only scans executable source — .py files plus
    extensionless scripts under ``scripts/`` directories. Markdown, YAML,
    and JSON references to the URL (SKILL.md instructions, config samples,
    schema fixtures) are not live send paths; flagging them would force
    documentation into the allowlist for no behavioral reason."""
    if p.suffix == ".py":
        return True
    if p.suffix == "" and "scripts" in p.parts:
        return True
    return False


def test_no_local_bridge_post_definitions_outside_allowlist():
    """Every file under src/ and tools/ that defines a function matching
    ``_?bridge_post`` (text-send chokepoint) must be in the explicit
    allowlist. Adding a new local helper without first widening the
    allowlist (with justification in the PR description) is a regression."""
    offenders: list[tuple[Path, int, str]] = []
    for f in _iter_source_files():
        if f in DEF_ALLOWLIST:
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
            "outside DEF_ALLOWLIST. Route the send through "
            "safe_io.bridge_post (4-tuple) or safe_io.bridge_post_2tuple "
            "(legacy 2-tuple), or extend DEF_ALLOWLIST in this test with "
            "explicit justification in the PR description.\n"
            + "\n".join(lines)
        )


def test_no_direct_bridge_send_url_outside_allowlist():
    """Every executable-source file (``.py`` or ``scripts/`` extensionless)
    under src/ and tools/ that references the literal bridge ``/send``
    endpoint URL must be in URL_ALLOWLIST. Catches bypasses that build
    their own ``urllib.request.Request`` inline without ever defining a
    ``_bridge_post``-shaped helper — exactly the gap that
    ``shift-agent-notify-owner:whatsapp_fallback`` represents.

    Scope note: this detector deliberately ignores .md / .yaml / .json
    references (SKILL instruction text, sample configs). Those are
    declarative — they don't execute a POST themselves. Whether SKILLs
    that *instruct an LLM to POST directly* count as bypasses is a
    separate (broader) discipline question outside PR-ε's scope."""
    offenders: list[tuple[Path, int]] = []
    for f in _iter_source_files():
        if not _is_executable_source(f):
            continue
        if f in URL_ALLOWLIST:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in BRIDGE_URL_RE.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            offenders.append((f.relative_to(REPO), line_no))

    if offenders:
        lines = [f"  {p}:{ln}" for p, ln in offenders]
        pytest.fail(
            "PR-ε static gate violated: direct bridge /send URL reference(s) "
            "in executable source outside URL_ALLOWLIST. Route the send "
            "through safe_io.bridge_post (or bridge_send_media / "
            "bridge_send_cta for those families), or extend URL_ALLOWLIST "
            "in this test with explicit per-file justification in the PR "
            "description.\n"
            + "\n".join(lines)
        )


def test_allowlist_files_actually_exist():
    """Guard against allowlist rot — every allowlisted path must exist.
    If a file is removed without updating the allowlist, the gate is
    silently weakened against unrelated future drift."""
    missing = [p for p in (DEF_ALLOWLIST | URL_ALLOWLIST) if not p.exists()]
    assert not missing, (
        "PR-ε allowlist references nonexistent paths "
        "(remove from allowlist or restore the file):\n"
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


# ── Detector 3: the e2e harness must patch a seam the script actually CALLS ──
#
# `tests/e2e/catering_conversation_harness.py` intercepts sends by setattr-ing
# the send helper on each freshly loaded script module. Two ways that goes
# silently dead, both of which end with the harness running a script
# UNINTERCEPTED — i.e. issuing a real bridge POST:
#
#   1. the script renames or drops the patched name (bare setattr happily
#      creates a dead attribute); the harness now guards this itself.
#   2. the script keeps the name IMPORTED but stops CALLING it. `hasattr` is
#      still True, so no guard fires.
#
# (2) is not hypothetical. #708 moved create-catering-lead's three sends onto
# `_bridge_post_4tuple` and left `_bridge_post` imported-but-uncalled, so the
# harness injection went dead on merge. Nothing caught it: the e2e test is
# skipped unless OPENROUTER_API_KEY is set, so it runs in no CI at all.
#
# This detector is static and collected everywhere, which is the point — it
# fails in ordinary CI the moment a converted script and the harness disagree.

E2E_HARNESS = REPO / "tests" / "e2e" / "catering_conversation_harness.py"
CATERING_SCRIPTS = REPO / "src" / "agents" / "catering" / "scripts"

# Patch keys that are data/config injections, not send seams. Only names that
# look like a send helper are required to be called.
_SEND_SEAM_RE = re.compile(r"bridge_post|bridge_send")


def _harness_patch_sites():
    """[(script_filename, [patch_key, ...]), ...] from every _load_patched call."""
    import ast
    tree = ast.parse(E2E_HARNESS.read_text(encoding="utf-8"))
    sites = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_load_patched" and len(node.args) >= 3
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[2], ast.Dict)):
            keys = [k.value for k in node.args[2].keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            sites.append((node.args[1].value, keys))
    return sites


def test_e2e_harness_patches_a_send_seam_the_script_actually_calls():
    sites = _harness_patch_sites()
    assert sites, "no _load_patched call sites found — did the harness move?"

    problems = []
    for filename, keys in sites:
        path = CATERING_SCRIPTS / filename
        if not path.exists():
            problems.append(f"{filename}: harness loads a script that does not exist")
            continue
        text = path.read_text(encoding="utf-8")
        seam_keys = [k for k in keys if _SEND_SEAM_RE.search(k)]
        if not seam_keys:
            continue
        for key in seam_keys:
            if not re.search(rf"(?<![\w.]){re.escape(key)}\s*\(", text):
                called = sorted(set(re.findall(r"(?<![\w.])(_?bridge_post\w*)\s*\(", text)))
                problems.append(
                    f"{filename}: harness patches `{key}` but the script never "
                    f"calls it (it calls {called or 'no bridge helper'}). The e2e "
                    f"harness would run this script unintercepted."
                )

    assert not problems, (
        "e2e send interception is dead for these scripts:\n"
        + "\n".join(f"  {p}" for p in problems)
    )
