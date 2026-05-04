"""Multi-source decisions.log abstraction (Agent #4 Daily Brief).

In v0.1 there's only one log source (Shift Agent). When Agent #2/3/5 ship,
each registers its own LogSource and Daily Brief iterates them all without
re-architecting. Cost today: ~50 lines. Saves a Daily Brief rewrite later.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


@dataclass
class LogReadStats:
    """Returned by `iter_entries` so callers can detect data quality issues."""
    total_lines: int = 0
    parse_failures: int = 0
    entries_in_window: int = 0
    file_missing: bool = False
    oserror: Optional[str] = None


class LogSource:
    """Adapter for one agent's `decisions.log`.

    Designed to be iterated for a fixed time window without holding the file
    open longer than necessary. Caller is responsible for serializing concurrent
    writers via `safe_io.FileLock` if the source's owning agent could be writing
    simultaneously (Daily Brief reads only, so contention is mild).
    """

    def __init__(self, agent_name: str, log_path: Path):
        self.agent_name = agent_name
        self.log_path = Path(log_path)

    def iter_entries(
        self,
        start_ts: datetime,
        end_ts: datetime,
    ) -> tuple[list[dict], LogReadStats]:
        """Return (entries_in_window, stats). Both timestamps must be tz-aware.

        Entries are returned as dicts (not Pydantic-validated) so callers can
        decide how to handle unknown types. Entries with missing or unparseable
        `ts` are counted as parse failures, not raised.
        """
        stats = LogReadStats()
        entries: list[dict] = []

        if not self.log_path.exists():
            stats.file_missing = True
            return entries, stats

        # DoS guard — cap line read at 64KB. A malicious or corrupt writer
        # could otherwise blow memory by writing a multi-MB single line.
        MAX_LINE_BYTES = 64 * 1024
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                while True:
                    line = f.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        break
                    stats.total_lines += 1
                    if len(line.encode("utf-8", errors="replace")) > MAX_LINE_BYTES:
                        stats.parse_failures += 1
                        # Drain to next newline to avoid splitting mid-record on next read
                        while line and not line.endswith("\n"):
                            line = f.readline(MAX_LINE_BYTES + 1)
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        stats.parse_failures += 1
                        continue
                    ts_str = entry.get("ts")
                    if not ts_str or not isinstance(ts_str, str):
                        stats.parse_failures += 1
                        continue
                    try:
                        # Strip trailing 'Z' if present (rare; entries should be ISO8601 +tz)
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        stats.parse_failures += 1
                        continue
                    # E2E-BUG-1 fix (2026-05-04): legacy entries written before
                    # the v0.3 _BaseEntry tz-aware-only invariant landed may have
                    # naive timestamps. The _BaseEntry validator auto-promotes
                    # naive→UTC at READ time (schemas.py:1338-1359); mirror that
                    # behavior here so the comparison `start_ts <= ts < end_ts`
                    # never raises TypeError. A single naive entry in production
                    # broke send-daily-brief + eod-reconcile until this fix.
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if start_ts <= ts < end_ts:
                        entries.append(entry)
                        stats.entries_in_window += 1
        except OSError as e:
            stats.oserror = str(e)

        return entries, stats


# Default registry — one source per active agent.
# Path overridable via env var so tests can point at fixture files.
def _default_log_path(agent: str) -> Path:
    env_var = f"SHIFT_AGENT_{agent.upper()}_LOG_PATH"
    return Path(os.environ.get(env_var, f"/opt/{agent}-agent/logs/decisions.log"))


LOG_SOURCES: list[LogSource] = [
    LogSource("shift", _default_log_path("shift")),
]


def get_log_sources() -> list[LogSource]:
    """Return the registered log sources. Indirection helps tests inject custom registries."""
    override = os.environ.get("SHIFT_AGENT_LOG_SOURCE_OVERRIDE")
    if override:
        # Test hook: override with a single source pointing at the override path
        return [LogSource("shift", Path(override))]
    return LOG_SOURCES
