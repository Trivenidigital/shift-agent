"""Tests for log_source.py — multi-agent decisions.log iteration. Windows-compatible (no fcntl)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from log_source import LogSource, LogReadStats, get_log_sources


@pytest.fixture
def fixture_log(tmp_path):
    """Build a decisions.log with entries spanning 3 days, mix of types."""
    log = tmp_path / "decisions.log"
    base = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    lines = [
        # day 1 (yesterday from "today" perspective at 04-28)
        {"type": "raw_inbound", "ts": (base + timedelta(hours=2)).isoformat(),
         "message_id": "m1", "sender_phone": "+19045550101", "body": "sick"},
        {"type": "proposal_created", "ts": (base + timedelta(hours=3)).isoformat(),
         "proposal_id": "p1", "candidate_employee_id": "e1", "approval_code": "#A1"},
        {"type": "proposal_status_change", "ts": (base + timedelta(hours=4)).isoformat(),
         "proposal_id": "p1", "from_status": "sent", "to_status": "accepted", "actor": "candidate"},
        {"type": "outbound_sent", "ts": (base + timedelta(hours=5)).isoformat(),
         "proposal_id": "p1", "to_phone": "+19045550102", "outbound_message_id": "m_out_1"},
        # day 2 (today)
        {"type": "raw_inbound", "ts": (base + timedelta(days=1, hours=3)).isoformat(),
         "message_id": "m2", "sender_phone": "+19045550102", "body": "thanks"},
    ]
    log.write_text("\n".join(json.dumps(L) for L in lines) + "\n", encoding="utf-8")
    return log


def test_log_source_iterates_window(fixture_log):
    src = LogSource("shift", fixture_log)
    start = datetime(2026, 4, 27, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone.utc)
    entries, stats = src.iter_entries(start, end)
    assert stats.total_lines == 5
    assert stats.parse_failures == 0
    assert stats.entries_in_window == 4  # the 5th is on day 2
    assert {e["type"] for e in entries} == {
        "raw_inbound", "proposal_created", "proposal_status_change", "outbound_sent",
    }


def test_log_source_excludes_today(fixture_log):
    src = LogSource("shift", fixture_log)
    start = datetime(2026, 4, 27, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone.utc)
    entries, _ = src.iter_entries(start, end)
    # m2 is at day 2, hour 3 — should NOT be included
    assert all(e.get("message_id") != "m2" for e in entries)


def test_log_source_handles_missing_file(tmp_path):
    src = LogSource("shift", tmp_path / "nonexistent.log")
    start = datetime(2026, 4, 27, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone.utc)
    entries, stats = src.iter_entries(start, end)
    assert entries == []
    assert stats.file_missing is True


def test_log_source_counts_parse_failures(tmp_path):
    log = tmp_path / "corrupt.log"
    log.write_text(
        '{"type": "raw_inbound", "ts": "2026-04-27T12:00:00+00:00", "message_id": "m1"}\n'
        'this is not json\n'
        '{"type": "raw_inbound", "ts": "BAD_TS", "message_id": "m2"}\n'
        '{"type": "raw_inbound"}\n'  # missing ts
        '\n'  # blank line — not counted as failure
        '{"type": "outbound_sent", "ts": "2026-04-27T15:00:00+00:00", "proposal_id": "p1"}\n',
        encoding="utf-8",
    )
    src = LogSource("shift", log)
    start = datetime(2026, 4, 27, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone.utc)
    entries, stats = src.iter_entries(start, end)
    assert stats.parse_failures == 3  # not-json + bad-ts + missing-ts
    assert stats.entries_in_window == 2


def test_log_source_handles_empty_file(tmp_path):
    log = tmp_path / "empty.log"
    log.write_text("", encoding="utf-8")
    src = LogSource("shift", log)
    start = datetime(2026, 4, 27, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone.utc)
    entries, stats = src.iter_entries(start, end)
    assert entries == []
    assert stats.parse_failures == 0


def test_get_log_sources_default():
    """Default registry has at least Shift."""
    # Clear any test override first
    os.environ.pop("SHIFT_AGENT_LOG_SOURCE_OVERRIDE", None)
    sources = get_log_sources()
    assert any(s.agent_name == "shift" for s in sources)


def test_get_log_sources_test_override(tmp_path, monkeypatch):
    fake_log = tmp_path / "test.log"
    fake_log.write_text("", encoding="utf-8")
    monkeypatch.setenv("SHIFT_AGENT_LOG_SOURCE_OVERRIDE", str(fake_log))
    sources = get_log_sources()
    assert len(sources) == 1
    assert sources[0].log_path == fake_log
