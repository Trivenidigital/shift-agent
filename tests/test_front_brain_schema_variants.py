"""Front-brain Phase-1 schema variants — pure pydantic (Windows + Docker).

Covers the two LogEntry additions this phase makes:
  - cf_router_intercepted reason `front_brain_yielded` (item 2 marker row)
  - FrontBrainRequestQueued variant `front_brain_request_queued` (item 5 queue)
Both must validate THROUGH the discriminated LogEntry union so the audit
chokepoint (safe_io._emit_audit_row) can emit them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "platform"))

from schemas import (  # noqa: E402
    CfRouterIntercepted,
    FrontBrainRequestQueued,
    LogEntry,
)
from pydantic import TypeAdapter, ValidationError  # noqa: E402

ADAPTER = TypeAdapter(LogEntry)


def test_front_brain_yielded_reason_validates_through_union():
    row = ADAPTER.validate_python(
        {
            "type": "cf_router_intercepted",
            "ts": "2026-07-12T00:00:00Z",
            "reason": "front_brain_yielded",
            "chat_id": "17329837841@c.us",
            "subprocess_rc": 0,
            "detail": "intercept=vague_start; message_id=m1; front_brain=converse",
        }
    )
    assert isinstance(row, CfRouterIntercepted)
    assert row.reason == "front_brain_yielded"


def test_request_queued_validates_through_union():
    row = ADAPTER.validate_python(
        {
            "type": "front_brain_request_queued",
            "ts": "2026-07-12T00:00:00Z",
            "chat_key_hash": "a" * 32,
            "request_kind": "theme_change",
            "request_preview": "make it look festive with diyas",
            "queue_size": 3,
        }
    )
    assert isinstance(row, FrontBrainRequestQueued)
    assert row.request_kind == "theme_change"
    assert row.queue_size == 3


TS = "2026-07-12T00:00:00Z"


def test_request_queued_defaults_and_kind_enum():
    row = FrontBrainRequestQueued(type="front_brain_request_queued", ts=TS)
    assert row.request_kind == "other"
    assert row.request_preview == ""
    assert row.queue_size == 0
    with pytest.raises(ValidationError):
        FrontBrainRequestQueued(
            type="front_brain_request_queued", ts=TS, request_kind="not_a_kind"
        )


def test_request_preview_capped_at_280():
    with pytest.raises(ValidationError):
        FrontBrainRequestQueued(
            type="front_brain_request_queued", ts=TS, request_preview="x" * 281
        )
