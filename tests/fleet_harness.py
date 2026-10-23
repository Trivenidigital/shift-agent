"""Generic fleet test harness: run any agent script out-of-process against an
isolated state dir, then read back the audit rows it wrote. Also hosts the one
static scanner for cf-router `audit_intercepted` reason literals.

Built on existing pieces: ``conftest.sterilize_subprocess_env`` (rehearsal layers
1-3: credentials neutralised, .env fallback closed, endpoints at sinks; no
rehearsal marker) and ``fixtures_fleet.read_log_rows`` (NDJSON parsing).

Environment contract (the repo's deployed overrides, not new knobs):
  * ``SHIFT_AGENT_STATE_DIR``           -> ``<state_dir>``
  * ``SHIFT_AGENT_DECISIONS_LOG_PATH``  -> ``<state_dir>/logs/decisions.log``
  * ``SHIFT_AGENT_NOTIFY_DEDUP_STATE``  -> ``<state_dir>/notify-dedup.json``
  * ``SHIFT_AGENT_NOTIFY_FAILED_LOG``   -> ``<state_dir>/logs/notify-failed.log``
  * ``PYTHONPATH``                      -> ``src/platform`` (flat platform imports)
Pass ``extra_env`` for per-script paths (e.g. ``SHIFT_AGENT_CONFIG_PATH``); it is
applied BEFORE sterilisation, so it can never re-introduce a credential.

Scripts that hard-code ``/opt/shift-agent/...`` without an env override are not
isolated here; use ``fixtures_fleet.load_script`` + monkeypatch for those. Usage:
``proc = run_agent_script(SCRIPT, ["--help"], state_dir=tmp_path / "s")`` then
``read_audit_rows(decisions_log_path(tmp_path / "s"), type_filter="config_load_failed")``.
Platform modules import ``fcntl``: run subprocess suites on Linux (docker runner).
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional, Union

from conftest import sterilize_subprocess_env
from fixtures_fleet import read_log_rows

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"


def decisions_log_path(state_dir: Path) -> Path:
    """Where ``run_agent_script`` points the audit chokepoint for ``state_dir``."""
    return Path(state_dir) / "logs" / "decisions.log"


def build_agent_env(state_dir: Path, extra_env: Optional[dict] = None) -> dict:
    """Sterile subprocess environment rooted at ``state_dir`` (created)."""
    state_dir = Path(state_dir)
    (state_dir / "logs").mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(PLATFORM_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SHIFT_AGENT_STATE_DIR": str(state_dir),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(decisions_log_path(state_dir)),
        "SHIFT_AGENT_NOTIFY_DEDUP_STATE": str(state_dir / "notify-dedup.json"),
        "SHIFT_AGENT_NOTIFY_FAILED_LOG": str(state_dir / "logs" / "notify-failed.log"),
    }
    env.update(extra_env or {})
    return sterilize_subprocess_env(
        env,
        env_file=state_dir / ".env-sterile",
        notify_owner_bin=state_dir / "bin" / "no-harness-pushover",
    )


def run_agent_script(
    script_path: Union[str, Path],
    argv: Iterable[str],
    *,
    state_dir: Path,
    extra_env: Optional[dict] = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run ``script_path`` (Python, shebang-agnostic) with ``argv`` under the
    current interpreter in a sterile env rooted at ``state_dir``.

    Never raises on a non-zero exit — callers assert on ``returncode`` and
    stdout/stderr (text). ``subprocess.TimeoutExpired`` propagates.
    """
    return subprocess.run(
        [sys.executable, str(script_path), *[str(a) for a in argv]],
        env=build_agent_env(state_dir, extra_env),
        cwd=str(state_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def intercepted_reason_literals(source: str, func: str = "audit_intercepted") -> list[tuple[int, str]]:
    """(lineno, literal) for every string constant that can reach the `reason`
    of a `func(...)` call (keyword, else first positional): literals in the
    argument expression (incl. ternaries) and, when it is a bare name, in every
    assignment to that name in the innermost enclosing function, else in a
    module-level constant of that name. f-string
    fragments are excluded. The ONE scanner behind both the fleet conformance
    guard and the flyer reason-literal test (generalized from its regex)."""
    def _assignments(nodes) -> dict[str, list[ast.AST]]:
        found: dict[str, list[ast.AST]] = {}
        for n in nodes:
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        found.setdefault(t.id, []).append(n.value)
            elif isinstance(n, ast.AnnAssign) and n.value is not None and isinstance(n.target, ast.Name):
                found.setdefault(n.target.id, []).append(n.value)
        return found

    tree = ast.parse(source)
    module_consts = _assignments(tree.body)  # module-level `_VAR = "..."` constants
    scopes = sorted(
        (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
        key=lambda n: n.end_lineno - n.lineno,
    ) + [tree]  # innermost function first; module scope last
    claimed: set[int] = set()
    out: list[tuple[int, str]] = []
    for scope in scopes:
        local = module_consts if scope is tree else _assignments(ast.walk(scope))
        assigned = {**module_consts, **local}  # a local binding shadows the module constant
        for call in ast.walk(scope):
            if not isinstance(call, ast.Call) or id(call) in claimed:
                continue
            f = call.func
            if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)) != func:
                continue
            claimed.add(id(call))
            exprs = [k.value for k in call.keywords if k.arg == "reason"] or call.args[:1]
            for e in list(exprs):
                if isinstance(e, ast.Name):
                    exprs.extend(assigned.get(e.id, []))
            for e in exprs:
                fstr = {id(c) for j in ast.walk(e) if isinstance(j, ast.JoinedStr) for c in ast.walk(j)}
                out += [(c.lineno, c.value) for c in ast.walk(e)
                        if isinstance(c, ast.Constant) and isinstance(c.value, str) and id(c) not in fstr]
    return out


def read_audit_rows(
    decisions_log_path: Path,
    type_filter: Union[None, str, Iterable[str]] = None,
) -> list[dict]:
    """Parsed decisions.log rows ([] if absent), optionally only rows whose
    ``type`` equals ``type_filter`` (a string) or is in it (an iterable)."""
    rows = read_log_rows(Path(decisions_log_path))
    if type_filter is None:
        return rows
    wanted = {type_filter} if isinstance(type_filter, str) else set(type_filter)
    return [r for r in rows if r.get("type") in wanted]
