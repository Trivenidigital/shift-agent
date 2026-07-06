"""Quarantine-before-recovery chokepoint (F0197 / F0208).

When a render fails QA and a recovery rung re-renders, the failed artifact was
previously OVERWRITTEN IN PLACE (every rung re-renders to the same
``<project>-C1-preview.png``) or deleted by the rung's cleanup — destroying the
evidence its post-mortem needs. F0208: the first production register render
died on one friendly-fire blocker, the recovery overwrote it, and the verdict
stayed "very likely correct, unverifiable".

``quarantine_before_overwrite`` copies the failed artifact plus whichever
sidecars exist into ``<state>/quarantine/<project_id>/<ts>-<rung>/`` before the
rung renders or cleans up. Contract:

- BEST-EFFORT: never raises; a quarantine failure must never block the
  recovery itself (stderr + continue).
- BOUNDED: at most ``KEEP_SETS_PER_PROJECT`` quarantine sets per project;
  older sets are pruned (set-dir names sort chronologically). A rung whose
  evidence is byte-identical to the newest existing set is skipped so
  consecutive rungs in one run can't evict distinct older evidence.
- AUDITED: one ``flyer_artifact_quarantined`` decisions.log row per set that
  actually copied files (no row when there was nothing to preserve).
"""
from __future__ import annotations

import filecmp
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

KEEP_SETS_PER_PROJECT = 3
_MAX_AUDIT_FILES = 40
# Cross-project TTL sweep (grad10 A): a per-project quarantine dir whose newest set
# has aged past this many days belongs to a terminal project (no rung has fired for
# it in the window — delivered/closed/abandoned), so its evidence is well past any
# post-mortem and the whole dir is removed. Bounds total quarantine growth across the
# fleet of projects (the keep-3 bound only caps sets WITHIN one project).
SWEEP_MAX_AGE_DAYS = 14

_UNSAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]")
# Leading UTC stamp of a set-dir name: "<%Y%m%dT%H%M%S.%fZ>-<rung>[-<suffix>]".
_SET_STAMP_RE = re.compile(r"^(\d{8}T\d{6}\.\d+Z)")


def quarantine_root() -> Path:
    """Mirrors render._flyer_state_root so tests + on-box layout agree."""
    return Path(os.environ.get("FLYER_STATE_ROOT", "/opt/shift-agent/state/flyer/")) / "quarantine"


def _sidecar_candidates(path: Path) -> list[Path]:
    """The artifact + every sidecar shape a preview can carry. Superset of the
    script's _render_artifact_candidates (adds the .typeset.json marker) — the
    quarantine preserves evidence, so over-collecting beats under-collecting."""
    return [
        path,
        Path(str(path) + ".text.json"),
        Path(str(path) + ".qa.json"),
        Path(str(path) + ".ocr.txt"),
        Path(str(path) + ".typeset.json"),
        path.with_name(f"{path.stem}.raw.png"),
        path.with_suffix(path.suffix + ".raw.png"),
        Path(str(path) + ".ppv1.json"),
        Path(str(path) + ".ppv1-bg.png"),
    ]


def _sanitize(component: str) -> str:
    return _UNSAFE_COMPONENT_RE.sub("_", component)[:80] or "unknown"


def _same_as_newest_set(project_dir: Path, plan: list[Path]) -> bool:
    """True when the newest existing quarantine set holds exactly the files in
    ``plan`` with identical contents. Consecutive rungs in one run often see an
    UNCHANGED failed artifact (e.g. a repair rendered to a distinct path, then
    the fallback rung fires) — re-quarantining it would burn the keep-3 bound
    on duplicates and evict distinct evidence from earlier runs."""
    try:
        sets = sorted(p for p in project_dir.iterdir() if p.is_dir())
    except OSError:
        return False
    if not sets:
        return False
    newest = sets[-1]
    try:
        if {p.name for p in newest.iterdir()} != {c.name for c in plan}:
            return False
        return all(filecmp.cmp(c, newest / c.name, shallow=False) for c in plan)
    except OSError:
        return False


def _prune_old_sets(project_dir: Path, *, keep: int) -> int:
    """Remove all but the ``keep`` newest quarantine sets. Set-dir names start
    with a UTC microsecond stamp (%Y%m%dT%H%M%S.%fZ), so lexicographic order
    is chronological. Best-effort per set."""
    pruned = 0
    try:
        sets = sorted(p for p in project_dir.iterdir() if p.is_dir())
    except OSError:
        return 0
    for stale in sets[:-keep] if keep else sets:
        try:
            shutil.rmtree(stale)
            pruned += 1
        except OSError as exc:
            print(f"flyer-quarantine: prune failed for {stale}: {exc}", file=sys.stderr)
    return pruned


def _parse_set_timestamp(name: str) -> datetime | None:
    """Parse the leading UTC stamp (%Y%m%dT%H%M%S.%fZ) from a set-dir name, or None."""
    match = _SET_STAMP_RE.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _project_dir_newest_age_days(project_dir: Path, *, now: datetime) -> float | None:
    """Age in days of the project's NEWEST quarantine set, by its timestamp-prefixed
    dir name (falling back to the set-dir mtime when the name is unparseable). None
    when the dir has no set subdirs — nothing to date, so leave it alone."""
    try:
        sets = sorted(p for p in project_dir.iterdir() if p.is_dir())
    except OSError:
        return None
    if not sets:
        return None
    newest = sets[-1]
    stamp = _parse_set_timestamp(newest.name)
    if stamp is None:
        try:
            stamp = datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return None
    return (now - stamp).total_seconds() / 86400.0


def _sweep_stale_project_dirs(root: Path, *, now: datetime, max_age_days: int,
                              skip_project_dir: Path | None = None) -> int:
    """Remove whole per-project quarantine dirs whose newest set is older than
    ``max_age_days``. Best-effort per dir; skips ``skip_project_dir`` (the one just
    written). Returns the count of project dirs removed."""
    swept = 0
    try:
        project_dirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return 0
    skip_name = skip_project_dir.name if skip_project_dir is not None else None
    for project_dir in project_dirs:
        if project_dir.name == skip_name:
            continue
        age = _project_dir_newest_age_days(project_dir, now=now)
        if age is None or age <= max_age_days:
            continue
        try:
            shutil.rmtree(project_dir)
            swept += 1
        except OSError as exc:
            print(f"flyer-quarantine: stale-project sweep failed for {project_dir}: {exc}", file=sys.stderr)
    return swept


def _emit_audit(audit_log_path: Path, *, project_id: str, rung: str,
                set_dir: Path, files: list[str], pruned_sets: int,
                swept_project_dirs: int = 0) -> None:
    """One decisions.log row per quarantine set. Lazy imports so an
    unavailable schemas/safe_io stack degrades to stderr, never a raise.
    FileLock + ndjson_append when available; plain append in fcntl-less test
    envs (mirrors bare_render._append_audit_line)."""
    from schemas import FlyerArtifactQuarantined  # noqa: PLC0415 — lazy: audit is best-effort

    entry = FlyerArtifactQuarantined(
        ts=datetime.now(timezone.utc),
        project_id=project_id,
        rung=rung,
        quarantine_dir=str(set_dir),
        files=files[:_MAX_AUDIT_FILES],
        pruned_sets=pruned_sets,
        swept_project_dirs=swept_project_dirs,
    )
    audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from safe_io import FileLock, ndjson_append  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — fcntl-less env
        FileLock = None  # type: ignore[assignment]
        ndjson_append = None  # type: ignore[assignment]
    if FileLock is not None and ndjson_append is not None:
        with FileLock(Path(str(audit_log_path) + ".lock")):
            ndjson_append(audit_log_path, entry.model_dump_json())
    else:
        with open(audit_log_path, "a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")


def quarantine_before_overwrite(paths: Iterable[Path | str], *, project_id: str,
                                rung: str, audit_log_path: Path | str | None = None) -> Path | None:
    """Copy each failed artifact in ``paths`` + its existing sidecars into a
    fresh quarantine set directory, prune the project's sets to
    KEEP_SETS_PER_PROJECT, and audit the copy. Returns the set directory when
    at least one file was preserved, else None (nothing to copy, evidence
    unchanged since the newest set, or quarantine failed). NEVER raises."""
    try:
        project_dir = quarantine_root() / _sanitize(project_id)
        # Microsecond stamp: set-dir names must stay unique AND lexicographically
        # chronological (prune relies on it) even when two rungs fire within the
        # same second — a coarser stamp + collision suffix can recreate a
        # previously PRUNED name and evict the newest set instead of the oldest.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        set_dir = project_dir / f"{stamp}-{_sanitize(rung)}"
        suffix = 1
        while set_dir.exists():
            suffix += 1
            set_dir = project_dir / f"{stamp}-{_sanitize(rung)}-{suffix}"
        plan: list[Path] = []
        seen: set[str] = set()
        for raw in paths:
            for candidate in _sidecar_candidates(Path(raw)):
                try:
                    if candidate.name not in seen and candidate.is_file():
                        plan.append(candidate)
                        seen.add(candidate.name)
                except OSError as exc:
                    print(f"flyer-quarantine: stat failed for {candidate}: {exc}", file=sys.stderr)
        if not plan:
            return None
        if _same_as_newest_set(project_dir, plan):
            return None  # unchanged evidence is already preserved — don't burn a set
        copied: list[str] = []
        for candidate in plan:
            try:
                set_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, set_dir / candidate.name)
                copied.append(candidate.name)
            except OSError as exc:
                print(f"flyer-quarantine: copy failed for {candidate}: {exc}", file=sys.stderr)
        if not copied:
            return None
        pruned = _prune_old_sets(project_dir, keep=KEEP_SETS_PER_PROJECT)
        # Cross-project TTL sweep (grad10 A): opportunistic hygiene at the same
        # chokepoint — drop terminal projects' quarantine dirs older than the window.
        # Its own best-effort internals never raise; wrapped so it can never turn a
        # successful quarantine into a None return.
        try:
            swept = _sweep_stale_project_dirs(
                project_dir.parent, now=datetime.now(timezone.utc),
                max_age_days=SWEEP_MAX_AGE_DAYS, skip_project_dir=project_dir)
        except Exception as exc:  # noqa: BLE001 — sweep is hygiene, never blocks the result
            print(f"flyer-quarantine: stale-project sweep skipped: {exc}", file=sys.stderr)
            swept = 0
        if audit_log_path is not None:
            try:
                _emit_audit(Path(audit_log_path), project_id=project_id, rung=rung,
                            set_dir=set_dir, files=copied, pruned_sets=pruned,
                            swept_project_dirs=swept)
            except Exception as exc:  # noqa: BLE001 — audit is observability, never blocks
                print(f"flyer-quarantine: audit emit failed ({rung}): {exc}", file=sys.stderr)
        return set_dir
    except Exception as exc:  # noqa: BLE001 — quarantine must never block the recovery
        try:
            print(f"flyer-quarantine: skipped ({rung}): {exc}", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
        return None
