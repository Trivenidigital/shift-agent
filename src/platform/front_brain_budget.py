"""Front-brain per-chat/day budget + latency fallback (P0-4).

Generalizes the B1 shadow-LLM budget counter (cf-router actions.py) — which is
GLOBAL, so one looping chat is unbounded within a day — into a reusable
per-KEY/day counter. The key is the canonical chat key, passed as a PARAMETER so
this module stays decoupled from the canonical-identity helper being built on a
sibling branch (the caller passes chat_id today).

Two primitives:
  - reserve_chat_day_budget(chat_key, ...): reserve one LLM turn against the
    key's per-UTC-day cap. Fails toward SKIP (returns False) on exhaustion OR any
    error — never lets turns through unbounded. Caller sends a template on False.
  - run_with_timeout(fn, fallback, timeout): bound a composed call (4s default);
    return the fallback template on timeout OR failure.

Deployed-pattern notes: JSON-on-disk, best-effort flock (deployed Linux) with an
atomic os.replace fallback where fcntl is unavailable (Windows CI) — exactly the
B1 shape. The flock import is lazy + guarded so this module imports cleanly on
non-POSIX.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Tuple, TypeVar

_T = TypeVar("_T")

DEFAULT_CHAT_DAILY_CAP = 30
DEFAULT_COMPOSE_TIMEOUT_SEC = 4.0

# Deployed default; overridable via env for tests + operator tuning.
FRONT_BRAIN_CHAT_BUDGET_PATH = Path("/opt/shift-agent/state/front_brain/chat_day_budget.json")


def _budget_path() -> Path:
    return Path(os.environ.get("FRONT_BRAIN_CHAT_BUDGET_PATH") or FRONT_BRAIN_CHAT_BUDGET_PATH)


def chat_daily_cap() -> int:
    """Per-chat/day LLM-turn cap. Env FRONT_BRAIN_CHAT_DAILY_CAP (default 30).
    A malformed value falls back to the default (never unbounded)."""
    try:
        return max(0, int(os.environ.get("FRONT_BRAIN_CHAT_DAILY_CAP", str(DEFAULT_CHAT_DAILY_CAP))))
    except (TypeError, ValueError):
        return DEFAULT_CHAT_DAILY_CAP


def compose_timeout_sec() -> float:
    """Composed-call latency budget in seconds. Env FRONT_BRAIN_COMPOSE_TIMEOUT_SEC
    (default 4.0). A non-positive / malformed value falls back to the default."""
    try:
        v = float(os.environ.get("FRONT_BRAIN_COMPOSE_TIMEOUT_SEC", str(DEFAULT_COMPOSE_TIMEOUT_SEC)))
        return v if v > 0 else DEFAULT_COMPOSE_TIMEOUT_SEC
    except (TypeError, ValueError):
        return DEFAULT_COMPOSE_TIMEOUT_SEC


@contextmanager
def _budget_lock(path: Path) -> Iterator[None]:
    """flock the budget file on deployed Linux; no-op where fcntl is unavailable
    (Windows CI). safe_io imports fcntl at module load, so guard the import."""
    try:
        from safe_io import flock  # type: ignore
    except Exception:
        yield
        return
    with flock(path):
        yield


def _load_budget(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_budget(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX + Windows


def reserve_chat_day_budget(
    chat_key: str,
    *,
    cap: Optional[int] = None,
    state_path: "Optional[os.PathLike[str] | str]" = None,
    now: Optional[datetime] = None,
) -> bool:
    """Reserve one LLM turn against `chat_key`'s per-UTC-day cap.

    Returns True (and records the turn) when the day still has budget for that
    key; False when the key's cap is exhausted OR any read/write error occurs.
    Fail toward SKIP: a broken counter must NEVER let turns through unbounded —
    the caller sends a template instead.

    `chat_key` is opaque — the caller passes the canonical chat key (chat_id
    today; the identity helper is a sibling branch). `cap` defaults to
    chat_daily_cap(); `state_path` to _budget_path(); `now` to UTC now (injectable
    for day-rollover tests)."""
    effective_cap = chat_daily_cap() if cap is None else max(0, int(cap))
    key = str(chat_key or "")
    if not key:
        return False  # no stable key -> cannot bound -> fail closed
    path = Path(state_path) if state_path is not None else _budget_path()
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    try:
        with _budget_lock(path):
            doc = _load_budget(path)
            if str(doc.get("utc_day") or "") != day:
                doc = {"utc_day": day, "counts": {}}
            counts = doc.get("counts")
            if not isinstance(counts, dict):
                counts = {}
            current = int(counts.get(key) or 0)
            if current >= effective_cap:
                return False
            counts[key] = current + 1
            doc["counts"] = counts
            doc["utc_day"] = day
            _write_budget(path, doc)
            return True
    except Exception:
        return False


def run_with_timeout(
    fn: Callable[[], _T],
    *,
    fallback: _T,
    timeout: Optional[float] = None,
) -> Tuple[_T, bool]:
    """Run `fn()` with a wall-clock timeout; return (value, used_fallback).

    On success → (result, False). On timeout OR any exception raised by `fn`
    → (fallback, True) — the caller sends the fallback template rather than
    leaving the customer waiting. `timeout` defaults to compose_timeout_sec().

    Thread-based (no SIGALRM) so it is cross-platform. A timed-out worker thread
    cannot be force-killed — its (late) result is simply discarded; the executor
    is shut down without waiting."""
    t = compose_timeout_sec() if timeout is None else float(timeout)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=t), False
    except concurrent.futures.TimeoutError:
        return fallback, True
    except Exception:
        return fallback, True
    finally:
        executor.shutdown(wait=False)
