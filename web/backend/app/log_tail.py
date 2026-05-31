"""Reverse-tail iterator for NDJSON log files.

Used by /audit and /decisions to read the last N entries without loading
the whole file into memory. O(N * 8KB) reads regardless of file size.

Per design v1 review (BL-120 #8): no JSON index. The reverse-tail iterator
is fast enough up to ~5 MB; an index pays off only after that, and we will
add it when telemetry shows real latency. KISS.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

# Default block size when seeking backwards.
_BLOCK = 8192


def reverse_lines(path: Path, max_lines: int = 1000) -> Iterator[str]:
    """Yield up to `max_lines` lines from the end of `path`, newest-first.

    Empty/missing files yield nothing. Trailing blank lines are skipped.
    Lines that span block boundaries are reassembled correctly.
    Caller is responsible for parsing / filtering.
    """
    if not path.exists():
        return
    size = path.stat().st_size
    if size == 0:
        return

    yielded = 0
    leftover = b""
    pos = size

    with path.open("rb") as f:
        while pos > 0 and yielded < max_lines:
            read_size = min(_BLOCK, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + leftover

            # Split into lines; first chunk may be partial (no leading newline)
            lines = buf.split(b"\n")
            # If we haven't reached BOF, the first slice is partial — save it
            if pos > 0:
                leftover = lines[0]
                lines = lines[1:]
            else:
                leftover = b""

            # Yield in reverse (newest-first within this block)
            for line in reversed(lines):
                # Strip trailing \r so CRLF-terminated lines (Windows-edited
                # config, logrotate copytruncate on weird FS, etc.) match.
                if line.endswith(b"\r"):
                    line = line[:-1]
                if not line:
                    continue
                try:
                    yield line.decode("utf-8")
                except UnicodeDecodeError:
                    continue  # skip corrupt line
                yielded += 1
                if yielded >= max_lines:
                    return

        # Final leftover at BOF
        if leftover and yielded < max_lines:
            if leftover.endswith(b"\r"):
                leftover = leftover[:-1]
            if leftover:
                try:
                    yield leftover.decode("utf-8")
                except UnicodeDecodeError:
                    pass


def reverse_json_entries(path: Path, max_lines: int = 1000) -> Iterator[dict]:
    """reverse_lines + json.loads, dropping unparseable lines."""
    for line in reverse_lines(path, max_lines):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        yield entry
