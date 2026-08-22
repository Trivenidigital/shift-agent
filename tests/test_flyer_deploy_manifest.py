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

# `from flyer_x import y` / `import flyer_x` / `import flyer_x as z`
_FLAT_IMPORT = re.compile(r"^\s*(?:from|import)\s+(flyer_[A-Za-z0-9_]+)\b", re.MULTILINE)


def _source_for_flat_module(flat_name: str) -> Path | None:
    """Map a deployed flat name (`flyer_ttl_observe`) back to its repo source.

    Flyer modules deploy as src/agents/flyer/<mod>.py -> flyer_<mod>.py; a few
    live in src/platform/ and deploy under their own name.
    """
    stem = flat_name[len("flyer_"):]
    for candidate in (FLYER / f"{stem}.py", REPO / "src" / "platform" / f"{flat_name}.py"):
        if candidate.exists():
            return candidate
    return None


def test_every_module_a_flyer_cli_imports_is_in_the_deploy_manifest():
    deploy = DEPLOY.read_text(encoding="utf-8", errors="replace")
    scripts = sorted(p for p in SCRIPTS.iterdir() if p.is_file() and p.suffix == "")
    assert scripts, "no flyer CLIs found -- the glob is wrong, not the tree"

    missing: list[str] = []
    for script in scripts:
        body = script.read_text(encoding="utf-8", errors="replace")
        for flat_name in sorted(set(_FLAT_IMPORT.findall(body))):
            source = _source_for_flat_module(flat_name)
            if source is None:
                continue  # not one of ours; nothing to install
            rel = source.relative_to(REPO).as_posix()
            if rel not in deploy:
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
