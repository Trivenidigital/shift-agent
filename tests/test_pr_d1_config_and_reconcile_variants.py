"""PR-D1 commit 3: ConfigLoadFailed + CateringLeadManuallyReconciled variants.

Per design v2 §9.1 / R4-M-T1: ~14 cases across the two variants
(8 ConfigLoadFailed + 6 CateringLeadManuallyReconciled).
"""
from __future__ import annotations
from datetime import datetime, timezone
import os

import pytest
from pydantic import TypeAdapter, ValidationError

from schemas import (
    LogEntry,
    ConfigLoadFailed,
    CateringLeadManuallyReconciled,
    _KNOWN_LOG_ENTRY_TYPES,
)


_LE = TypeAdapter(LogEntry)
_CLF = TypeAdapter(ConfigLoadFailed)
_CMR = TypeAdapter(CateringLeadManuallyReconciled)


# ─────────────── ConfigLoadFailed — 8 cases ───────────────

def test_config_load_failed_minimum_fields():
    row = {
        "type": "config_load_failed",
        "ts": "2026-01-01T00:00:00+00:00",
        "path": "/opt/shift-agent/config.yaml",
        "error_class": "RuntimeError",
        "script_name": "apply-catering-owner-decision",
    }
    parsed = _LE.validate_python(row)
    assert isinstance(parsed, ConfigLoadFailed)
    assert parsed.error_detail == ""  # default


def test_config_load_failed_full_fields():
    row = {
        "type": "config_load_failed",
        "ts": "2026-01-01T00:00:00+00:00",
        "path": "/opt/shift-agent/config.yaml",
        "error_class": "ValidationError",
        "error_detail": "1 validation error for Config\ncustomer\n  field required",
        "script_name": "create-catering-lead",
    }
    parsed = _CLF.validate_python(row)
    assert "validation error" in parsed.error_detail


def test_config_load_failed_rejects_empty_path():
    with pytest.raises(ValidationError):
        _CLF.validate_python({
            "type": "config_load_failed",
            "ts": "2026-01-01T00:00:00+00:00",
            "path": "",
            "error_class": "RuntimeError",
            "script_name": "x",
        })


def test_config_load_failed_rejects_empty_error_class():
    with pytest.raises(ValidationError):
        _CLF.validate_python({
            "type": "config_load_failed",
            "ts": "2026-01-01T00:00:00+00:00",
            "path": "/x",
            "error_class": "",
            "script_name": "x",
        })


def test_config_load_failed_rejects_empty_script_name():
    with pytest.raises(ValidationError):
        _CLF.validate_python({
            "type": "config_load_failed",
            "ts": "2026-01-01T00:00:00+00:00",
            "path": "/x",
            "error_class": "RuntimeError",
            "script_name": "",
        })


def test_config_load_failed_max_length_violations():
    with pytest.raises(ValidationError):
        _CLF.validate_python({
            "type": "config_load_failed",
            "ts": "2026-01-01T00:00:00+00:00",
            "path": "/x",
            "error_class": "x" * 81,  # max=80
            "script_name": "x",
        })
    with pytest.raises(ValidationError):
        _CLF.validate_python({
            "type": "config_load_failed",
            "ts": "2026-01-01T00:00:00+00:00",
            "path": "/x",
            "error_class": "RuntimeError",
            "script_name": "x" * 81,  # max=80
        })


def test_config_load_failed_in_known_types():
    assert "config_load_failed" in _KNOWN_LOG_ENTRY_TYPES


def test_config_load_failed_extra_field_forbidden():
    with pytest.raises(ValidationError):
        _CLF.validate_python({
            "type": "config_load_failed",
            "ts": "2026-01-01T00:00:00+00:00",
            "path": "/x",
            "error_class": "RuntimeError",
            "script_name": "x",
            "rogue": "y",
        })


# ─────────────── CateringLeadManuallyReconciled — 6 cases ───────────────

def test_manually_reconciled_minimum_fields():
    row = {
        "type": "catering_lead_manually_reconciled",
        "ts": "2026-01-01T00:00:00+00:00",
        "lead_id": "L00042",
        "from_status": "OWNER_APPROVED",
        "to_status": "SENT_TO_CUSTOMER",
        "reason": "post-bridge divergence ticket #123",
        "operator_uid": 1001,
    }
    parsed = _LE.validate_python(row)
    assert isinstance(parsed, CateringLeadManuallyReconciled)
    assert parsed.operator_uid == 1001


def test_manually_reconciled_in_known_types():
    assert "catering_lead_manually_reconciled" in _KNOWN_LOG_ENTRY_TYPES


def test_manually_reconciled_rejects_invalid_status():
    """from_status / to_status must be CateringLeadStatus literals."""
    with pytest.raises(ValidationError):
        _CMR.validate_python({
            "type": "catering_lead_manually_reconciled",
            "ts": "2026-01-01T00:00:00+00:00",
            "lead_id": "L00042",
            "from_status": "WHATEVER_BOGUS",
            "to_status": "SENT_TO_CUSTOMER",
            "reason": "x",
            "operator_uid": 1001,
        })


def test_manually_reconciled_rejects_empty_reason():
    with pytest.raises(ValidationError):
        _CMR.validate_python({
            "type": "catering_lead_manually_reconciled",
            "ts": "2026-01-01T00:00:00+00:00",
            "lead_id": "L00042",
            "from_status": "OWNER_APPROVED",
            "to_status": "SENT_TO_CUSTOMER",
            "reason": "",
            "operator_uid": 1001,
        })


def test_manually_reconciled_rejects_non_int_uid():
    with pytest.raises(ValidationError):
        _CMR.validate_python({
            "type": "catering_lead_manually_reconciled",
            "ts": "2026-01-01T00:00:00+00:00",
            "lead_id": "L00042",
            "from_status": "OWNER_APPROVED",
            "to_status": "SENT_TO_CUSTOMER",
            "reason": "x",
            "operator_uid": "not-an-int",
        })


def test_manually_reconciled_extra_field_forbidden():
    with pytest.raises(ValidationError):
        _CMR.validate_python({
            "type": "catering_lead_manually_reconciled",
            "ts": "2026-01-01T00:00:00+00:00",
            "lead_id": "L00042",
            "from_status": "OWNER_APPROVED",
            "to_status": "SENT_TO_CUSTOMER",
            "reason": "x",
            "operator_uid": 1001,
            "rogue_field": "y",
        })
