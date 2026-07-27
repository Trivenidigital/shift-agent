"""PR-5 automation-control audit variants — schema registration + validation."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import schemas  # noqa: E402
from pydantic import TypeAdapter, ValidationError  # noqa: E402

_ADAPTER = TypeAdapter(schemas.LogEntry)


def _ts():
    return datetime.now(timezone.utc)


def test_both_variants_are_known_log_entry_types():
    assert "catering_automation_control_changed" in schemas._KNOWN_LOG_ENTRY_TYPES
    assert "catering_automated_send_suppressed" in schemas._KNOWN_LOG_ENTRY_TYPES


def test_control_changed_validates():
    row = _ADAPTER.validate_python({
        "type": "catering_automation_control_changed", "ts": _ts(),
        "chat_key_hash": "abc123", "from_mode": "active", "to_mode": "opted_out",
        "actor": "customer", "reason": "customer_stop", "logical_turn_id": "wamid.1",
    })
    assert isinstance(row, schemas.CateringAutomationControlChanged)
    assert row.to_mode == "opted_out"


def test_send_suppressed_validates_both_kinds():
    for kind in ("inbound_dispatch", "outbound_send"):
        row = _ADAPTER.validate_python({
            "type": "catering_automated_send_suppressed", "ts": _ts(),
            "chat_key_hash": "abc", "mode": "takeover",
            "suppressed_send_kind": kind, "logical_turn_id": "",
        })
        assert isinstance(row, schemas.CateringAutomatedSendSuppressed)
        assert row.suppressed_send_kind == kind


def test_bad_mode_rejected():
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({
            "type": "catering_automation_control_changed", "ts": _ts(),
            "from_mode": "active", "to_mode": "bogus", "actor": "customer",
        })


def test_bad_actor_rejected():
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({
            "type": "catering_automation_control_changed", "ts": _ts(),
            "from_mode": "active", "to_mode": "opted_out", "actor": "hacker",
        })


def test_exports():
    assert "CateringAutomationControlChanged" in schemas.__all__
    assert "CateringAutomatedSendSuppressed" in schemas.__all__
