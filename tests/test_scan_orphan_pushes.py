"""Tests for audit_helpers.scan_expense_orphan_pushes — platform-helpers
consolidation commit 3 of 4.

Validates the deduplicated orphan-detection helper that replaces inline
near-mirrors in:
- extract-receipt._check_extract_orphans
- apply-expense-decision._check_orphans

Linux-only: audit_helpers transitively imports safe_io which imports fcntl.

Helper signature is expense-specific by design (PR #41 review fix — H1+H2):
constants for pending_status / completion_types / id_attr / timestamp_attr /
tail_lines are baked in to match the two deployed callers; YAGNI-revert
from a previous schema-agnostic kwarg-driven version.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("fcntl")

from audit_helpers import scan_expense_orphan_pushes


def _lead(*, expense_id: str, status: str = "APPROVED_PENDING_PUSH",
          approved_at=None, reconcile_required: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        expense_id=expense_id,
        status=status,
        owner_approval_received_at=approved_at,
        reconcile_required=reconcile_required,
    )


def _stale_ts() -> datetime:
    """Approved 5 minutes ago — well past the 60s default stale threshold."""
    return datetime.now(timezone.utc) - timedelta(seconds=300)


def _fresh_ts() -> datetime:
    """Approved 10 seconds ago — under the 60s stale threshold."""
    return datetime.now(timezone.utc) - timedelta(seconds=10)


def test_no_pending_leads_returns_empty(tmp_path):
    flagged, ids = scan_expense_orphan_pushes(
        [_lead(expense_id="E1", status="PUSHED")],
        tmp_path / "decisions.log",
    )
    assert flagged == []
    assert ids == []


def test_stale_pending_no_audit_match_is_orphan(tmp_path):
    log = tmp_path / "decisions.log"
    log.write_text("")  # empty audit log
    leads = [_lead(expense_id="E1", approved_at=_stale_ts())]
    flagged, ids = scan_expense_orphan_pushes(leads, log)
    assert ids == ["E1"]
    assert len(flagged) == 1
    assert flagged[0].reconcile_required is True


def test_fresh_pending_is_not_orphan(tmp_path):
    """Under-the-stale-window leads are legit-in-flight, not orphans."""
    log = tmp_path / "decisions.log"
    log.write_text("")
    leads = [_lead(expense_id="E2", approved_at=_fresh_ts())]
    _, ids = scan_expense_orphan_pushes(leads, log)
    assert ids == []
    assert leads[0].reconcile_required is False


def test_already_reconcile_required_is_skipped(tmp_path):
    """Don't re-flag what's already flagged (avoids double-audit)."""
    log = tmp_path / "decisions.log"
    log.write_text("")
    leads = [_lead(expense_id="E3", approved_at=_stale_ts(), reconcile_required=True)]
    _, ids = scan_expense_orphan_pushes(leads, log)
    assert ids == []


def test_audit_match_excludes_from_orphan_set(tmp_path):
    """Stale pending lead WITH a completion entry in audit log is NOT an orphan."""
    log = tmp_path / "decisions.log"
    entry = {"type": "expense_pushed", "expense_id": "E4", "ts": "2026-04-30T00:00:00Z"}
    log.write_text(json.dumps(entry) + "\n")
    leads = [_lead(expense_id="E4", approved_at=_stale_ts())]
    _, ids = scan_expense_orphan_pushes(leads, log)
    assert ids == []  # excluded by audit match
    assert leads[0].reconcile_required is False


def test_audit_match_handles_push_failed_too(tmp_path):
    log = tmp_path / "decisions.log"
    entry = {"type": "expense_push_failed", "expense_id": "E5"}
    log.write_text(json.dumps(entry) + "\n")
    leads = [_lead(expense_id="E5", approved_at=_stale_ts())]
    _, ids = scan_expense_orphan_pushes(leads, log)
    assert ids == []  # excluded by audit match (failed push, but completed)


def test_missing_timestamp_treated_as_orphan_defensively(tmp_path):
    """No approved_at timestamp → can't gauge age; flag defensively."""
    log = tmp_path / "decisions.log"
    log.write_text("")
    leads = [_lead(expense_id="E6", approved_at=None)]
    _, ids = scan_expense_orphan_pushes(leads, log)
    assert ids == ["E6"]


def test_iso_string_timestamp_parses(tmp_path):
    """Lead with str timestamp (e.g. from Pydantic JSON dump) parses OK."""
    log = tmp_path / "decisions.log"
    log.write_text("")
    iso = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    leads = [_lead(expense_id="E7", approved_at=iso)]
    _, ids = scan_expense_orphan_pushes(leads, log)
    assert ids == ["E7"]


def test_corrupt_audit_lines_are_skipped(tmp_path):
    """Malformed JSON in audit log doesn't break the helper."""
    log = tmp_path / "decisions.log"
    log.write_text(
        "not valid json\n"
        '{"type":"expense_pushed","expense_id":"E8"}\n'
        "{partial json...\n"
    )
    leads = [_lead(expense_id="E8", approved_at=_stale_ts())]
    _, ids = scan_expense_orphan_pushes(leads, log)
    assert ids == []  # E8 excluded by valid completion entry


def test_missing_log_file_treats_all_stale_as_orphans(tmp_path):
    """If audit log doesn't exist (clean install), no completion entries → all stale orphans."""
    log = tmp_path / "nonexistent.log"
    leads = [_lead(expense_id="E9", approved_at=_stale_ts())]
    _, ids = scan_expense_orphan_pushes(leads, log)
    assert ids == ["E9"]


def test_stale_seconds_kwarg_override(tmp_path):
    """Caller can override the 60s default — exercised by test fixtures
    that don't want to wait 60 seconds for a lead to age."""
    log = tmp_path / "decisions.log"
    log.write_text("")
    # 30 seconds ago — under default 60s, but past a 10s override
    ts = datetime.now(timezone.utc) - timedelta(seconds=30)
    leads = [_lead(expense_id="E10", approved_at=ts)]
    _, ids_default = scan_expense_orphan_pushes(leads, log)
    assert ids_default == []  # under default 60s stale → not orphan

    leads2 = [_lead(expense_id="E10", approved_at=ts)]
    _, ids_short = scan_expense_orphan_pushes(leads2, log, stale_seconds=10)
    assert ids_short == ["E10"]  # over 10s threshold → orphan


def test_other_status_audit_entries_are_ignored(tmp_path):
    """Audit entries whose type isn't expense_pushed/expense_push_failed
    must NOT exclude orphans (e.g., expense_owner_decision is a different
    event class)."""
    log = tmp_path / "decisions.log"
    log.write_text(json.dumps({"type": "expense_owner_decision", "expense_id": "E11"}) + "\n")
    leads = [_lead(expense_id="E11", approved_at=_stale_ts())]
    _, ids = scan_expense_orphan_pushes(leads, log)
    assert ids == ["E11"]  # decision row is not a completion type
