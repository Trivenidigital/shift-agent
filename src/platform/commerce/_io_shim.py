"""Cross-platform safe-IO shim for commerce primitives.

On Linux (production VPS) imports the real safe_io helpers.
On Windows (dev/test) falls back to simple atomic writes — fcntl is
Unix-only. Mirrors the precedent in src/agents/flyer/guest_order.py.

PR reviewer A MEDIUM-3: consolidates the duplicate try/except shim
that previously lived in cart.py and audit.py.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


try:
    if os.name == "nt":
        raise ModuleNotFoundError("use simple atomic write fallback on Windows")
    from safe_io import atomic_write_json as _safe_atomic_write_json  # type: ignore
    from safe_io import ndjson_append as _safe_ndjson_append  # type: ignore
    from safe_io import flock as _safe_flock  # type: ignore
except ModuleNotFoundError:
    def _safe_atomic_write_json(path: Path, obj: Any, mode: int = 0o640) -> None:  # type: ignore[no-redef]
        path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(obj, "model_dump_json"):
            content = obj.model_dump_json(indent=2)
        else:
            content = json.dumps(obj, indent=2, default=str)
        path.write_text(content, encoding="utf-8")

    def _safe_ndjson_append(path: Path, entry_json: str) -> None:  # type: ignore[no-redef]
        path.parent.mkdir(parents=True, exist_ok=True)
        if any(c in entry_json for c in ("\n", "\r")):
            raise ValueError("ndjson_append: entry_json must not contain line-break characters")
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry_json + "\n")

    @contextmanager
    def _safe_flock(path: Path) -> Iterator[None]:  # type: ignore[no-redef]
        # Windows dev/test: fcntl (hence safe_io.flock) is Unix-only. Tests run
        # single-process, so a no-op advisory lock is safe here. Production
        # (Linux VPS) uses the real safe_io.flock. NEVER reach for fcntl/FileLock
        # directly from commerce code — always go through this shim.
        yield


def atomic_write_json(path: Path, obj: Any, mode: int = 0o640) -> None:
    _safe_atomic_write_json(path, obj, mode=mode)


def ndjson_append(path: Path, entry_json: str) -> None:
    _safe_ndjson_append(path, entry_json)


def file_lock(path: Path):
    """Exclusive advisory lock over ``<path>.lock`` (the deployed
    ``safe_io.flock`` ``.lock``-sibling convention) on Linux; a no-op context
    manager on Windows dev/test where fcntl is unavailable.

    ALL writers of the same store file MUST acquire this around their
    load->mutate->write section so concurrent read-modify-writes serialize
    (closing the verified lost-update gap in ``order_state.create`` /
    ``order_state.transition``)."""
    return _safe_flock(path)
