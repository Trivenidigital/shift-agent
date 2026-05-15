"""Pure workflow helpers for Hermes Flyer Studio."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.workflow import (  # noqa: E402
    FLYER_INTENT_RE,
    build_missing_info_prompt,
    extract_revision_field_updates,
    next_status_for_project,
    quality_check_project,
)
from schemas import FlyerProject, FlyerRequestFields  # noqa: E402


def _project(fields: FlyerRequestFields) -> FlyerProject:
    return FlyerProject(
        project_id="F0001",
        status="collecting_required_info",
        customer_phone="+19045550123",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="wamid.flyer.1",
        raw_request="Need flyer for Bathukamma Oct 10",
        fields=fields,
    )


def test_intent_regex_catches_flyer_requests_without_generic_event():
    assert FLYER_INTENT_RE.search("Need flyer for Bathukamma Oct 10")
    assert FLYER_INTENT_RE.search("Can you make an Instagram poster?")
    assert FLYER_INTENT_RE.search("Need a social post for grand opening")
    assert not FLYER_INTENT_RE.search("Need catering for a wedding event")


def test_missing_info_prompt_groups_customer_questions():
    prompt = build_missing_info_prompt(
        ["event_time", "venue_or_location", "contact_info"],
        preferred_language="te",
    )
    assert "time" in prompt.lower()
    assert "venue" in prompt.lower()
    assert "contact" in prompt.lower()
    assert "Telugu" in prompt


def test_next_status_waits_for_required_info_then_assets_or_generation():
    partial = _project(FlyerRequestFields(event_or_business_name="Bathukamma"))
    assert next_status_for_project(partial, has_required_assets=False) == "collecting_required_info"

    complete = _project(FlyerRequestFields(
        event_or_business_name="Bathukamma",
        event_date="2026-10-10",
        event_time="18:00",
        venue_or_location="Community Hall",
        contact_info="+1 904 555 0123",
    ))
    assert next_status_for_project(complete, has_required_assets=False) == "awaiting_assets"
    assert next_status_for_project(complete, has_required_assets=True) == "generating_concepts"


def test_quality_check_flags_missing_and_ready_fields():
    incomplete = _project(FlyerRequestFields(event_or_business_name="Bathukamma"))
    result = quality_check_project(incomplete)
    assert result.ok is False
    assert "event_date" in result.blockers

    complete = _project(FlyerRequestFields(
        event_or_business_name="Bathukamma",
        event_date="2026-10-10",
        event_time="18:00",
        venue_or_location="Community Hall",
        contact_info="+1 904 555 0123",
    ))
    result = quality_check_project(complete)
    assert result.ok is True
    assert result.blockers == []


def test_extract_revision_field_updates_handles_date_and_time_change():
    project = _project(FlyerRequestFields(
        event_or_business_name="Holi Celebrations",
        event_date="2026-10-15",
        event_time="18:00",
        venue_or_location="Triveni Pineville",
        contact_info="+1 904 555 0123",
    ))
    updates = extract_revision_field_updates(
        project,
        "I'd like you to change the date from October 15 to 18. Time from 6 PM to 4 PM.",
    )
    assert updates == {
        "event_date": "2026-10-18",
        "event_time": "16:00",
    }
