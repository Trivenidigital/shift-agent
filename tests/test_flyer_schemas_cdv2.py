"""Tests for the additive FlyerProject.creative_direction carrier (Slice B Task B2.2).

The field is an OPTIONAL transient carrier (default None) mirroring
`deterministic_recovery`: no data migration (existing rows load fine) and it
must round-trip through model_dump_json() -> model_validate_json() so it survives
into the premium-overlay subprocess.
"""
from datetime import datetime, timezone

from schemas import FlyerProject


def _project(**overrides) -> FlyerProject:
    base = dict(
        project_id="F0250",
        status="intake_started",
        customer_phone="+17329837841",
        created_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        original_message_id="m",
        raw_request="Weekend Specials any item $7.99",
    )
    base.update(overrides)
    return FlyerProject(**base)


def test_creative_direction_defaults_to_none_when_omitted():
    """Built WITHOUT creative_direction validates; field defaults to None
    (backward compatible — existing rows have no such key)."""
    p = _project()
    assert p.creative_direction is None


def test_creative_direction_round_trips_through_json():
    """Setting creative_direction round-trips through model_dump_json() ->
    model_validate_json() — proving it survives subprocess serialization."""
    cd = {
        "hero_name": "Dosa",
        "campaign_narrative": "South Indian Favorites at One Price",
        "offer_priority": "high",
    }
    p = _project(creative_direction=cd)
    restored = FlyerProject.model_validate_json(p.model_dump_json())
    assert restored.creative_direction == cd


def test_creative_direction_none_round_trips():
    """None carrier round-trips cleanly (default-key serialization, flag-off
    byte-identical behavior)."""
    p = _project()
    restored = FlyerProject.model_validate_json(p.model_dump_json())
    assert restored.creative_direction is None
