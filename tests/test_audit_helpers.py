"""PR-D1 commit 4: audit_helpers.py — best-effort emission tests.

Verifies the swallow-all-errors contract: helpers NEVER raise even when
the audit log path is unwritable. Test override pattern uses
log_path=tmp_path / "decisions.log" per design v2 §14.3 R5-M-2.
"""
from __future__ import annotations
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

# audit_helpers transitively imports safe_io → fcntl (Linux-only).
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="audit_helpers transitively depends on fcntl via safe_io",
)

if platform.system() != "Windows":
    from audit_helpers import (  # noqa: E402
        log_config_load_failed_best_effort,
        log_quote_sent_lead_missing_best_effort,
    )


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ─────────────── log_config_load_failed_best_effort ───────────────

def test_config_load_failed_writes_one_row(tmp_path: Path):
    log_path = tmp_path / "decisions.log"
    cfg_path = tmp_path / "config.yaml"
    log_config_load_failed_best_effort(
        config_path=cfg_path,
        exc=RuntimeError("yaml parse error: unexpected token"),
        log_path=log_path,
    )
    rows = _read_lines(log_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "config_load_failed"
    assert row["path"] == str(cfg_path)
    assert row["error_class"] == "RuntimeError"
    assert "yaml parse error" in row["error_detail"]
    assert row["script_name"]  # not empty


def test_config_load_failed_uses_utc_ts(tmp_path: Path):
    log_path = tmp_path / "decisions.log"
    log_config_load_failed_best_effort(
        config_path=tmp_path / "x.yaml",
        exc=FileNotFoundError("x.yaml"),
        log_path=log_path,
    )
    row = _read_lines(log_path)[0]
    ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
    assert ts.tzinfo is not None
    # UTC offset is 0
    assert ts.utcoffset().total_seconds() == 0.0


def test_config_load_failed_truncates_long_detail(tmp_path: Path):
    log_path = tmp_path / "decisions.log"
    long_msg = "x" * 5000
    log_config_load_failed_best_effort(
        config_path=tmp_path / "y.yaml",
        exc=ValueError(long_msg),
        log_path=log_path,
    )
    row = _read_lines(log_path)[0]
    assert len(row["error_detail"]) <= 2000


def test_config_load_failed_swallows_audit_write_failure(tmp_path: Path, monkeypatch):
    """If ndjson_append itself raises, the helper must NOT propagate."""
    # Point log_path at an unwritable location
    bad_path = tmp_path / "nonexistent_dir" / "subdir_we_cannot_create" / "log"
    # Force the parent-mkdir to fail by making the path's parent a file
    blocker = tmp_path / "nonexistent_dir"
    blocker.write_text("blocking")  # exists as a file, mkdir parents=True will fail
    log_path = blocker / "subdir" / "log"

    # Helper must return None (no exception)
    result = log_config_load_failed_best_effort(
        config_path=tmp_path / "x.yaml",
        exc=RuntimeError("oops"),
        log_path=log_path,
    )
    assert result is None


def test_config_load_failed_handles_no_argv(tmp_path: Path, monkeypatch):
    """Edge case: sys.argv empty — script_name should fall back gracefully."""
    monkeypatch.setattr(sys, "argv", [])
    log_path = tmp_path / "decisions.log"
    log_config_load_failed_best_effort(
        config_path=tmp_path / "x.yaml",
        exc=RuntimeError("e"),
        log_path=log_path,
    )
    row = _read_lines(log_path)[0]
    assert row["script_name"] == "<unknown>"


# ─────────────── log_quote_sent_lead_missing_best_effort ───────────────

def test_quote_sent_lead_missing_writes_one_row(tmp_path: Path):
    log_path = tmp_path / "decisions.log"
    log_quote_sent_lead_missing_best_effort(
        lead_id="L00042",
        original_message_id="m_orig",
        customer_phone_at_approve="+15555550100",
        outbound_message_id="mb_42",
        detail="post-bridge re-load lost lead",
        log_path=log_path,
    )
    rows = _read_lines(log_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "catering_quote_sent_lead_missing"
    assert row["lead_id"] == "L00042"
    assert row["customer_phone_at_approve"] == "+15555550100"
    assert row["outbound_message_id"] == "mb_42"
    assert row["detail"] == "post-bridge re-load lost lead"


def test_quote_sent_lead_missing_truncates_detail(tmp_path: Path):
    log_path = tmp_path / "decisions.log"
    log_quote_sent_lead_missing_best_effort(
        lead_id="L00042",
        original_message_id="m_orig",
        customer_phone_at_approve="+15555550100",
        outbound_message_id="mb_42",
        detail="x" * 1000,  # > max 500
        log_path=log_path,
    )
    row = _read_lines(log_path)[0]
    assert len(row["detail"]) <= 500


def test_quote_sent_lead_missing_swallows_invalid_phone(tmp_path: Path):
    """If phone fails E164 validation, helper returns silently — caller's
    primary error path proceeds without secondary failure."""
    log_path = tmp_path / "decisions.log"
    result = log_quote_sent_lead_missing_best_effort(
        lead_id="L00042",
        original_message_id="m_orig",
        customer_phone_at_approve="not-a-phone",  # invalid E.164
        outbound_message_id="mb_42",
        log_path=log_path,
    )
    assert result is None
    # No row written (helper raised internally; was caught)
    if log_path.exists():
        assert _read_lines(log_path) == []


def test_config_load_failed_with_newline_in_error_detail(tmp_path: Path):
    """R2-MED-1: ndjson_append rejects raw newlines in the on-the-wire line.
    Pydantic's JSON serializer escapes \\n inside string values to \\\\n, so
    multi-line error messages survive. Pin this contract — a future refactor
    that pre-serializes detail or bypasses Pydantic would silently break the
    swallow contract (the row is lost; outer except swallows)."""
    log_path = tmp_path / "decisions.log"
    log_config_load_failed_best_effort(
        config_path=tmp_path / "x.yaml",
        exc=RuntimeError("line1\nline2\nline3"),
        log_path=log_path,
    )
    rows = _read_lines(log_path)
    assert len(rows) == 1
    assert rows[0]["error_detail"] == "line1\nline2\nline3"


def test_config_load_failed_default_log_path_kwarg(tmp_path: Path, monkeypatch):
    """R2-MED-2: exercise the default-kwarg wiring. Patches _LOG_PATH_DEFAULT
    to a tmp path then calls the helper WITHOUT explicit log_path so a
    refactor renaming the kwarg surfaces here."""
    import audit_helpers
    # This case pins the module-constant fallback, so isolate from the
    # conftest-set SHIFT_AGENT_DECISIONS_LOG_PATH env override (census C1).
    monkeypatch.delenv("SHIFT_AGENT_DECISIONS_LOG_PATH", raising=False)
    custom_default = tmp_path / "default_decisions.log"
    monkeypatch.setattr(audit_helpers, "_LOG_PATH_DEFAULT", custom_default)
    audit_helpers.log_config_load_failed_best_effort(
        config_path=tmp_path / "x.yaml",
        exc=RuntimeError("default-path-test"),
    )
    rows = _read_lines(custom_default)
    assert len(rows) == 1
    assert rows[0]["error_detail"] == "default-path-test"


def test_quote_sent_lead_missing_default_log_path_module_constant():
    """Sanity: module constant points at the deployed VPS path. Tests
    that need to override pass log_path explicitly."""
    from audit_helpers import _LOG_PATH_DEFAULT
    assert str(_LOG_PATH_DEFAULT).endswith("decisions.log")
    assert "shift-agent" in str(_LOG_PATH_DEFAULT)
