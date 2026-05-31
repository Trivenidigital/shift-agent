"""reverse-tail iterator (BL-120 #7-8)."""
from __future__ import annotations

import json
from pathlib import Path

from app.log_tail import reverse_lines, reverse_json_entries


def test_missing_file_yields_nothing(tmp_path: Path) -> None:
    p = tmp_path / "nope.log"
    assert list(reverse_lines(p)) == []
    assert list(reverse_json_entries(p)) == []


def test_empty_file_yields_nothing(tmp_path: Path) -> None:
    p = tmp_path / "empty.log"
    p.write_text("")
    assert list(reverse_lines(p)) == []


def test_single_line(tmp_path: Path) -> None:
    p = tmp_path / "one.log"
    p.write_text("hello\n")
    assert list(reverse_lines(p)) == ["hello"]


def test_reverse_order(tmp_path: Path) -> None:
    p = tmp_path / "many.log"
    p.write_text("\n".join(f"line-{i}" for i in range(5)) + "\n")
    assert list(reverse_lines(p, max_lines=3)) == ["line-4", "line-3", "line-2"]


def test_lines_spanning_block_boundary(tmp_path: Path) -> None:
    """Lines longer than the 8KB block must be reassembled correctly."""
    p = tmp_path / "long.log"
    long_line = "x" * 10_000  # > _BLOCK
    p.write_text(f"{long_line}\nfinal\n")
    got = list(reverse_lines(p, max_lines=10))
    assert got == ["final", long_line]


def test_handles_no_trailing_newline(tmp_path: Path) -> None:
    p = tmp_path / "no_nl.log"
    p.write_text("a\nb\nc")  # no trailing \n
    assert list(reverse_lines(p, max_lines=10)) == ["c", "b", "a"]


def test_skips_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "blanks.log"
    p.write_text("a\n\nb\n\n\nc\n")
    assert list(reverse_lines(p, max_lines=10)) == ["c", "b", "a"]


def test_reverse_json_entries_drops_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "ndjson.log"
    lines = [
        json.dumps({"i": 0}),
        "not-json",
        json.dumps(42),
        json.dumps(["valid", "json", "but", "not", "object"]),
        json.dumps({"i": 2}),
    ]
    p.write_text("\n".join(lines) + "\n")
    got = list(reverse_json_entries(p, max_lines=10))
    assert got == [{"i": 2}, {"i": 0}]


def test_max_lines_caps(tmp_path: Path) -> None:
    p = tmp_path / "many.log"
    p.write_text("\n".join(str(i) for i in range(100)) + "\n")
    got = list(reverse_lines(p, max_lines=7))
    assert len(got) == 7
    assert got[0] == "99"
    assert got[-1] == "93"
