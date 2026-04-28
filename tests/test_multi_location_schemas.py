"""Tests for Multi-Location Coordinator schemas (Agent #3). Windows-runnable."""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from datetime import datetime, timezone

from schemas import (
    Config, LocationEntry, MultiLocationConfig,
    CrossLocationQuery, InterLocationTransferProposed,
)


def test_location_entry_required_fields():
    LocationEntry(id="loc_jax", name="Jacksonville", timezone="America/New_York")
    with pytest.raises(ValidationError):
        LocationEntry(id="", name="X", timezone="UTC")
    with pytest.raises(ValidationError):
        LocationEntry(id="loc1", name="", timezone="UTC")


def test_location_entry_invalid_timezone():
    with pytest.raises(ValidationError):
        LocationEntry(id="loc1", name="X", timezone="Mars/Olympus")


def test_multi_location_config_defaults():
    c = MultiLocationConfig()
    assert c.enabled is True
    assert c.locations == []
    assert c.require_owner_approval_for_transfers is True


def test_multi_location_unique_ids_enforced():
    locs = [
        LocationEntry(id="loc_a", name="A", timezone="UTC"),
        LocationEntry(id="loc_a", name="B", timezone="UTC"),  # duplicate id
    ]
    with pytest.raises(ValidationError):
        MultiLocationConfig(locations=locs)


def test_multi_location_extra_forbid():
    with pytest.raises(ValidationError):
        MultiLocationConfig(typo=True)  # type: ignore[call-arg]


def test_cross_location_query_validators():
    now = datetime.now(tz=timezone.utc)
    e = CrossLocationQuery(
        type="cross_location_query", ts=now, query_id="q1",
        raw_query="who's at houston tomorrow?",
        location_ids_resolved=["loc_hou_01"], answer_summary="3 shifts scheduled",
    )
    assert e.query_id == "q1"
    with pytest.raises(ValidationError):
        CrossLocationQuery(
            type="cross_location_query", ts=now, query_id="",
            raw_query="x", location_ids_resolved=[],
        )


def test_inter_location_transfer_validators():
    now = datetime.now(tz=timezone.utc)
    InterLocationTransferProposed(
        type="inter_location_transfer_proposed", ts=now, transfer_id="t1",
        from_location_id="loc_a", to_location_id="loc_b",
        employee_id="e1", proposed_date="2026-04-29",
    )
    with pytest.raises(ValidationError):
        InterLocationTransferProposed(
            type="inter_location_transfer_proposed", ts=now, transfer_id="t1",
            from_location_id="loc_a", to_location_id="loc_b",
            employee_id="e1", proposed_date="04-29-2026",  # bad pattern
        )


def test_config_backward_compat_no_multi_location():
    """Existing configs (no multi_location block) load with empty locations default."""
    old = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "l", "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }
    c = Config.model_validate(old)
    assert c.multi_location.locations == []
    assert c.multi_location.enabled is True
