"""Pydantic-only tests for EOD Reconciliation schema additions. Windows-runnable."""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from datetime import datetime, timezone

from schemas import Config, EodConfig, EodSnapshot, EodPushoverSent, EodSkipped


def test_eod_time_regex_rejects_invalid():
    for bad in ["24:00", "25:00", "9:00", "abc", ""]:
        with pytest.raises(ValidationError):
            EodConfig(eod_time=bad)


def test_eod_time_regex_accepts_valid():
    for good in ["00:00", "07:00", "22:00", "23:59"]:
        EodConfig(eod_time=good)


def test_eod_config_defaults():
    c = EodConfig()
    assert c.enabled is True
    assert c.eod_time == "22:00"
    assert c.catchup_window_minutes == 120
    assert c.pushover_priority == 0
    assert c.pushover_only_if_unresolved is True


def test_eod_config_pushover_priority_bounds():
    with pytest.raises(ValidationError):
        EodConfig(pushover_priority=3)
    with pytest.raises(ValidationError):
        EodConfig(pushover_priority=-3)
    EodConfig(pushover_priority=2)
    EodConfig(pushover_priority=-2)


def test_eod_config_extra_forbid():
    with pytest.raises(ValidationError):
        EodConfig(typo=True)  # type: ignore[call-arg]


def test_eod_snapshot_validators():
    now = datetime.now(tz=timezone.utc)
    e = EodSnapshot(
        type="eod_snapshot", ts=now, eod_date="2026-04-28", snapshot_id="abc",
        sick_calls=2, proposals_created=2, proposals_resolved=1, proposals_unresolved=1,
        outbound_sent=2, outbound_send_failed=0, invariant_violations=0,
    )
    assert e.snapshot_id == "abc"
    # negative counts rejected
    with pytest.raises(ValidationError):
        EodSnapshot(
            type="eod_snapshot", ts=now, eod_date="2026-04-28", snapshot_id="abc",
            sick_calls=-1, proposals_created=0, proposals_resolved=0,
            proposals_unresolved=0, outbound_sent=0, outbound_send_failed=0,
            invariant_violations=0,
        )
    # bad date
    with pytest.raises(ValidationError):
        EodSnapshot(
            type="eod_snapshot", ts=now, eod_date="04-28-2026", snapshot_id="abc",
            sick_calls=0, proposals_created=0, proposals_resolved=0,
            proposals_unresolved=0, outbound_sent=0, outbound_send_failed=0,
            invariant_violations=0,
        )


def test_eod_pushover_sent_priority_bounds():
    now = datetime.now(tz=timezone.utc)
    EodPushoverSent(
        type="eod_pushover_sent", ts=now, eod_date="2026-04-28",
        snapshot_id="abc", unresolved_count=3, pushover_priority=2,
    )
    with pytest.raises(ValidationError):
        EodPushoverSent(
            type="eod_pushover_sent", ts=now, eod_date="2026-04-28",
            snapshot_id="abc", unresolved_count=3, pushover_priority=3,
        )


def test_eod_skipped_reason_literal():
    now = datetime.now(tz=timezone.utc)
    for r in ["already_done", "disabled", "catchup_expired", "data_unavailable"]:
        EodSkipped(type="eod_skipped", ts=now, eod_date="2026-04-28", reason=r)
    with pytest.raises(ValidationError):
        EodSkipped(type="eod_skipped", ts=now, eod_date="2026-04-28", reason="random")


def test_config_backward_compat_no_eod():
    """Existing configs (no eod block) load with defaults."""
    old = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "l", "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }
    c = Config.model_validate(old)
    assert c.eod.eod_time == "22:00"
    assert c.eod.enabled is True
