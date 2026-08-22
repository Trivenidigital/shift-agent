"""Every flyer CLI the deploy installs must have its modules installed too.

The deploy is asymmetric, and that asymmetry is the bug generator:

  - scripts ship by WILDCARD  -- `install -m 755 src/agents/flyer/scripts/*`
    (shift-agent-deploy.sh), so a new CLI reaches the box the moment it is
    committed, whether or not anyone thought about it;
  - modules ship by HAND      -- one explicit `install -m 644 src/agents/flyer/
    <mod>.py /opt/shift-agent/flyer_<mod>.py` line per module.

So a CLI can be live on the box while the module it imports was never
installed. Nothing fails at deploy time; the CLI fails at INVOCATION time, which
means it fails silently until somebody runs it.

Found in production 2026-08-22: `/usr/local/bin/flyer-ttl0-observe` had been
installed since 2026-08-18 and raised `ModuleNotFoundError: No module named
'agents'` on every invocation, because `ttl_observe.py` has no install line and
`/opt/shift-agent/flyer_ttl_observe.py` does not exist. The CLI's flat-import
fallback (`from agents.flyer.ttl_observe import ...`) cannot help: there is no
`agents` package on the box either.

This test closes the class rather than the instance.
"""
from __future__ import annotations

from pathlib import Path
import re

REPO = Path(__file__).resolve().parent.parent
FLYER = REPO / "src" / "agents" / "flyer"
SCRIPTS = FLYER / "scripts"
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"

# `from x import y` / `import x` / `import x as z`. Deliberately NOT limited to
# `flyer_*`: a CLI importing any flat module the deploy owns has the same
# failure mode, and _source_for_flat_module decides what is actually ours.
_FLAT_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE)


def _source_for_flat_module(flat_name: str) -> tuple[Path, str] | None:
    """Map a deployed flat name back to (repo source, installed basename).

    Flyer modules deploy as src/agents/flyer/<mod>.py -> flyer_<mod>.py; some
    live in src/platform/ and deploy under their own name. Anything resolving to
    neither is not ours to install (stdlib, third-party, a sibling CLI).
    """
    if flat_name.startswith("flyer_"):
        candidate = FLYER / f"{flat_name[len('flyer_'):]}.py"
        if candidate.exists():
            return candidate, f"{flat_name}.py"
    candidate = REPO / "src" / "platform" / f"{flat_name}.py"
    if candidate.exists():
        return candidate, f"{flat_name}.py"
    return None


def _installs(deploy: str, rel: str, installed_basename: str) -> bool:
    """Does the manifest carry a real install LINE for this source?

    Substring-matching the path is not enough: a comment mentioning the file
    (`# TODO: someday install src/agents/flyer/ttl_observe.py`) would satisfy
    that while installing nothing. Match the actual command.
    """
    pattern = re.compile(
        r"^\s*install\b[^\n]*\s"
        + re.escape(rel)
        + r"\s+/opt/shift-agent/"
        + re.escape(installed_basename)
        + r"\s*$",
        re.MULTILINE,
    )
    return bool(pattern.search(deploy))


def _flyer_clis() -> list[Path]:
    """Every file the deploy's `src/agents/flyer/scripts/*` wildcard installs.

    Not filtered to extensionless files: the wildcard installs whatever is
    there, so a `foo.py` CLI reaches the box too and must be checked.
    """
    return sorted(p for p in SCRIPTS.iterdir() if p.is_file())


def test_every_module_a_flyer_cli_imports_is_in_the_deploy_manifest():
    deploy = DEPLOY.read_text(encoding="utf-8", errors="replace")
    scripts = _flyer_clis()
    assert scripts, "no flyer CLIs found -- the glob is wrong, not the tree"

    missing: list[str] = []
    for script in scripts:
        body = script.read_text(encoding="utf-8", errors="replace")
        for flat_name in sorted(set(_FLAT_IMPORT.findall(body))):
            resolved = _source_for_flat_module(flat_name)
            if resolved is None:
                continue  # not one of ours; nothing to install
            source, installed_basename = resolved
            rel = source.relative_to(REPO).as_posix()
            if not _installs(deploy, rel, installed_basename):
                missing.append(f"{script.name} imports {flat_name}, but {rel} has no install line")

    assert not missing, (
        "flyer CLIs ship by wildcard; these would be installed on the box and "
        "crash at invocation because their module is not installed:\n  "
        + "\n  ".join(sorted(set(missing)))
    )


def test_flyer_scripts_really_do_ship_by_wildcard():
    """Guards the premise of the test above. If the deploy ever switches to
    per-script install lines, the wildcard reasoning stops holding and this
    file needs rewriting rather than silently passing."""
    deploy = DEPLOY.read_text(encoding="utf-8", errors="replace")
    assert "install -m 755 src/agents/flyer/scripts/*" in deploy


def test_a_comment_mentioning_the_path_does_not_satisfy_the_check():
    """Guards the guard.

    The first version of this test substring-matched the path against the whole
    script, so appending `# TODO: someday install src/agents/flyer/ttl_observe.py`
    to a manifest with NO install line made it pass. Reproduce that exact bypass
    and assert it is now rejected.
    """
    real = DEPLOY.read_text(encoding="utf-8", errors="replace")
    rel = "src/agents/flyer/ttl_observe.py"
    assert _installs(real, rel, "flyer_ttl_observe.py"), "expected a real install line on this branch"

    commented_only = re.sub(
        r"^\s*install\b[^\n]*" + re.escape(rel) + r"[^\n]*$",
        f"        # TODO: someday install {rel}",
        real,
        count=1,
        flags=re.MULTILINE,
    )
    assert commented_only != real, "failed to build the comment-only variant"
    assert rel in commented_only, "the bypass must still mention the path"
    assert not _installs(commented_only, rel, "flyer_ttl_observe.py"), (
        "a comment naming the path satisfies the check -- the assertion is a "
        "substring test again, not a check for the install command"
    )
