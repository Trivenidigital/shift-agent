"""Durable CD v2 rollback safety — deploy-time scrub of `creative_direction`.

Background
---------
`FlyerProject.creative_direction` is now declared `Field(exclude=True)`, so NEW
code never serializes the key into the project store (`projects.json`). But two
residual hazards remain:

  1. Rows written BEFORE the exclude=True fix may still carry the key on disk.
  2. Older `extra="forbid"` rollback code, if a deploy is rolled back, would
     reject a store that still contains the lingering key — turning a benign
     leftover into a hard load failure.

To make the rollback guarantee DURABLE rather than dependent on serialization
behavior, the deploy runs this scrub against the store BEFORE the gateway
restart. It removes ANY `creative_direction` key from each project entry.

Safety / idempotence
--------------------
With exclude=True the key is never legitimately persisted, so removing it loses
nothing. The scrub:

  - removes ONLY the `creative_direction` key (nothing else),
  - is idempotent (a second run removes 0),
  - never raises on a malformed / non-dict project entry,
  - rewrites the file ONLY when at least one key was removed.

Pure / guarded: the only side effect is the conditional atomic rewrite in
`scrub_store_file`; `strip_creative_direction` mutates the in-memory dict only.

This module is installed FLAT to /opt/shift-agent/ (alongside safe_io.py,
schemas.py, etc.) so the deploy step can `from flyer_store_maintenance import
scrub_store_file`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_KEY = "creative_direction"


def strip_creative_direction(store: dict) -> int:
    """Remove the `creative_direction` key from each entry in store["projects"].

    Returns the number of entries from which the key was removed. Idempotent: a
    second call on the same (already-stripped) store returns 0. Never raises on a
    malformed entry — non-dict entries (str / None / int / etc.) and a missing or
    non-list "projects" value are skipped. Only the `creative_direction` key is
    touched; all other keys are left intact.
    """
    if not isinstance(store, dict):
        return 0
    projects = store.get("projects")
    if not isinstance(projects, list):
        return 0
    removed = 0
    for entry in projects:
        # Tolerate malformed entries: only dicts can carry the key.
        if isinstance(entry, dict) and _KEY in entry:
            del entry[_KEY]
            removed += 1
    return removed


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically write text. Prefer safe_io.atomic_write_text (fsync + parent
    dir fsync on POSIX); fall back to tmp + os.replace if safe_io is unavailable
    (e.g. running outside the deployed flat layout)."""
    try:
        from safe_io import atomic_write_text  # type: ignore
    except Exception:
        tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
        tmp.write_text(content, encoding="utf-8")
        os.replace(str(tmp), str(path))
        return
    atomic_write_text(path, content)


def scrub_store_file(path: str) -> int:
    """Load the project store JSON at `path`, strip every `creative_direction`
    key, and atomically rewrite the file ONLY if at least one key was removed.

    Returns the number of keys removed. Returns 0 (and does NOT rewrite) when:
      - the file does not exist,
      - the file is already clean,
      - the JSON is not a dict / has no list "projects".

    Never raises on a missing file. (A genuinely corrupt JSON would raise from
    json.loads; the deploy wraps this call and treats a non-zero exit as a gate
    signal — but in practice the store is always valid JSON written by safe_io.)
    """
    p = Path(path)
    if not p.exists():
        return 0
    store = json.loads(p.read_text(encoding="utf-8"))
    removed = strip_creative_direction(store)
    if removed:
        _atomic_write_text(p, json.dumps(store, indent=2))
    return removed
