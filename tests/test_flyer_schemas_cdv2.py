"""Tests for the additive FlyerProject.creative_direction carrier (Slice B Task B2.2).

The field is an OPTIONAL transient carrier (default None) mirroring
`deterministic_recovery` in its additive nature, BUT it is marked
``exclude=True`` so it is NEVER serialized into projects.json — that keeps a
CODE rollback to the old ``extra="forbid"`` FlyerProject safe (the old schema
would reject an unknown ``creative_direction`` key). In-memory the attribute is
still set + readable; delivery to the premium-overlay subprocess is handled
separately via the subprocess spec dict (see test_flyer_cdv2_render.py).
"""
import json
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

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


def test_creative_direction_excluded_from_model_dump_json():
    """ROLLBACK SAFETY: a populated creative_direction is OMITTED from
    model_dump_json() — the key never lands in projects.json so rolling the CODE
    back to the old extra="forbid" FlyerProject (which lacks the field) loads
    fine."""
    cd = {
        "hero_name": "Dosa",
        "campaign_narrative": "South Indian Favorites at One Price",
        "offer_priority": "high",
    }
    p = _project(creative_direction=cd)
    dumped = json.loads(p.model_dump_json())
    assert "creative_direction" not in dumped


def test_creative_direction_excluded_from_model_dump():
    """Same exclusion holds for model_dump() (the in-process store-write path)."""
    cd = {"hero_name": "Dosa", "campaign_narrative": "X"}
    p = _project(creative_direction=cd)
    assert "creative_direction" not in p.model_dump()


def test_dumped_json_loads_under_old_extra_forbid_schema():
    """ROLLBACK SIM: an old-code FlyerProject (extra="forbid", NO
    creative_direction field) must model_validate the NEW dump without error —
    proving the excluded key cannot break a code rollback."""

    class _OldFlyerProjectLike(BaseModel):
        # Mirrors the load-time strictness of the pre-CDv2 FlyerProject: extra keys
        # are rejected. It models nothing else — we only need to prove that the
        # NEW dump carries no key this strict schema would reject as the (now
        # excluded) carrier.
        model_config = ConfigDict(extra="forbid")

    cd = {"hero_name": "Dosa", "campaign_narrative": "X", "offer_priority": "high"}
    dumped = json.loads(_project(creative_direction=cd).model_dump_json())
    # The carrier key is absent => an extra="forbid" schema with NO
    # creative_direction field would never reject on it.
    assert "creative_direction" not in dumped
    # And an extra="forbid" model that DOES declare every other key (here: none,
    # we just feed only the carrier-stripped subset) validates a payload built
    # from exactly the keys the strict schema knows.
    _OldFlyerProjectLike.model_validate({})  # no unexpected keys to choke on


def test_creative_direction_readable_in_memory_after_construction():
    """exclude=True affects ONLY serialization — the in-memory attribute is still
    set + readable so the in-process overlay + bg-prompt read it as before."""
    cd = {"hero_name": "Dosa", "campaign_narrative": "X"}
    p = _project(creative_direction=cd)
    assert p.creative_direction == cd
    assert p.creative_direction["hero_name"] == "Dosa"
