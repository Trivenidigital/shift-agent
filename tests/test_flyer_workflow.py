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
    extract_revision_patch,
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


def test_build_project_status_reply_covers_all_flyer_states():
    from agents.flyer.workflow import build_project_status_reply

    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    statuses = [
        "intake_started",
        "collecting_required_info",
        "awaiting_assets",
        "manual_edit_required",
        "generating_concepts",
        "awaiting_concept_selection",
        "revising_design",
        "awaiting_final_approval",
        "finalizing_assets",
        "delivered",
        "completed",
    ]
    for status in statuses:
        project = FlyerProject(
            project_id="F9003",
            status=status,
            customer_phone="+17329837841",
            created_at=now,
            updated_at=now,
            original_message_id="m-status",
            raw_request="Create flyer",
        )

        reply = build_project_status_reply(project)

        assert "Flyer Studio" in reply
        assert "F9003" in reply
        assert "resend" not in reply.lower()


def test_source_edit_provider_ready_reads_shift_agent_env_file(tmp_path, monkeypatch):
    from agents.flyer.workflow import source_edit_provider_ready

    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ok, detail = source_edit_provider_ready({"assets": [{"kind": "reference_image", "mime_type": "image/png", "path": "x.png"}]}, env_path=env_path)

    assert ok
    assert detail == "ready"


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


def test_extract_revision_patch_handles_price_title_phone_and_venue_changes():
    project = _project(FlyerRequestFields(
        event_or_business_name="Weekend Breakfast",
        contact_info="+1 704 324 3322",
        venue_or_location="Triveni Pineville",
        notes="Thursday Dosa Night Special. Non-veg combo $14.99. Phone: +1 704 324 3322. Location: Triveni Pineville",
    ))
    patch = extract_revision_patch(
        project,
        "This should be Thursday Dosa Night Special, not Weekend Breakfast. "
        "Change non-veg combo price from $14.99 to $16.99. "
        "Phone should be +1 980 200 5022. Change location to Lakshmi's Kitchen.",
    )
    assert patch.changed is True
    assert patch.ambiguous is False
    assert patch.field_updates["event_or_business_name"] == "Thursday Dosa Night Special"
    assert patch.field_updates["contact_info"] == "+1 980 200 5022"
    assert patch.field_updates["venue_or_location"] == "Lakshmi's Kitchen"
    assert "$16.99" in patch.notes_update
    assert "$14.99" not in patch.notes_update


def test_extract_revision_patch_flags_repeated_price_ambiguity():
    project = _project(FlyerRequestFields(
        event_or_business_name="Dosa Night",
        contact_info="+1 704 324 3322",
        notes="Veg combo $14.99. Non-veg combo $14.99.",
    ))
    patch = extract_revision_patch(project, "Change price from $14.99 to $16.99.")
    assert patch.changed is False
    assert patch.ambiguous is True
    assert "appears multiple times" in patch.unresolved_reason


def test_extract_revision_patch_flags_old_text_not_found():
    project = _project(FlyerRequestFields(
        event_or_business_name="Dosa Night",
        contact_info="+1 704 324 3322",
        notes="Veg combo $12.99.",
    ))
    patch = extract_revision_patch(project, "Change price from $14.99 to $16.99.")
    assert patch.changed is False
    assert patch.ambiguous is True
    assert "not found" in patch.unresolved_reason


def test_extract_revision_patch_handles_menu_item_swap_without_clarification():
    project = _project(FlyerRequestFields(
        event_or_business_name="Weekend Breakfast Specials",
        event_time="07:00",
        venue_or_location="90 Brybar Dr",
        contact_info="+1 732 983 7841",
        notes="Exclude Thatte Idly from original flyer. Items: Idly, Dosa with Chicken Curry, Tatte Idly.",
    ))
    patch = extract_revision_patch(
        project,
        "Design looks great! But remove Thatte/Tatte Idly. Swap Tatte Idly with Ghee Karam Idly(same price).",
    )

    assert patch.changed is True
    assert patch.ambiguous is False
    assert patch.unresolved_reason == ""
    assert "Replace menu item Tatte Idly with Ghee Karam Idly" in patch.notes_update
    assert "Do not include Tatte Idly" in patch.notes_update


def test_extract_revision_patch_handles_extra_time_removal_and_item_add():
    project = _project(FlyerRequestFields(
        event_or_business_name="Weekend Breakfast Specials",
        event_time="08:00",
        venue_or_location="90 Brybar Dr",
        contact_info="+1 732 983 7841",
        notes="Weekend Breakfast Specials. Timings 8 AM to 11 AM. Extra 08:00 appears in the template.",
    ))

    patch = extract_revision_patch(project, "Remove that extra 08:00. Add Any Item for $9.99.")

    assert patch.changed is True
    assert patch.ambiguous is False
    assert 'Remove duplicate/extra time text "08:00"' in patch.notes_update
    assert "Add menu item Any Item for $9.99" in patch.notes_update
    assert 'Remove duplicate/extra time text "08:00"' in patch.raw_request_update


def test_extract_revision_patch_does_not_parse_later_price_as_extra_time():
    project = _project(FlyerRequestFields(
        event_or_business_name="Weekend Breakfast Specials",
        event_time="08:00",
        venue_or_location="90 Brybar Dr",
        contact_info="+1 732 983 7841",
        notes="Weekend Breakfast Specials. Extra 08:00 appears in the template.",
    ))

    patch = extract_revision_patch(project, "Remove extra 08:00 and add Any Item for $9.99.")

    assert patch.changed is True
    assert 'Remove duplicate/extra time text "08:00"' in patch.notes_update
    assert 'Remove duplicate/extra time text "9"' not in patch.notes_update


def test_extract_revision_patch_handles_item_swap_with_price():
    project = _project(FlyerRequestFields(
        event_or_business_name="Weekend Breakfast Specials",
        contact_info="+1 732 983 7841",
        notes="Items: Poori $8.99, Kheema Dosa $12.99.",
    ))

    patch = extract_revision_patch(project, "Swap Kheema Dosa with Any Item for $9.99.")

    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Replace menu item Kheema Dosa with Any Item for $9.99" in patch.notes_update
    assert "Do not include Kheema Dosa" in patch.notes_update


def test_extract_revision_patch_handles_remove_and_add_item_same_price():
    project = _project(FlyerRequestFields(
        event_or_business_name="Weekend Breakfast Specials",
        contact_info="+1 732 983 7841",
        notes="Items: Tatte Idly $8.99, Poori $8.99.",
    ))

    patch = extract_revision_patch(project, "Remove Tatte Idly and add Ghee Karam Idly same price.")

    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Remove menu item Tatte Idly" in patch.notes_update
    assert "Add menu item Ghee Karam Idly same price" in patch.notes_update


def test_extract_revision_patch_handles_item_specific_price_to_new_price():
    project = _project(FlyerRequestFields(
        event_or_business_name="Weekend Breakfast Specials",
        contact_info="+1 732 983 7841",
        notes="Items: Poori with Chicken $14.99, Kheema Dosa $12.99, Vada $8.99.",
    ))

    patch = extract_revision_patch(project, "Change Kheema Dosa price to $9.99.")

    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Kheema Dosa $9.99" in patch.notes_update
    assert "Kheema Dosa $12.99" not in patch.notes_update
    assert "Poori with Chicken $14.99" in patch.notes_update


def test_extract_revision_patch_replaces_decimal_price_before_period():
    project = _project(FlyerRequestFields(
        event_or_business_name="Weekend Breakfast Specials",
        contact_info="+1 732 983 7841",
        notes="Items: Poori with Chicken $14.99; Kheema Dosa $12.99. Thursday to Sunday.",
    ))

    patch = extract_revision_patch(project, "Change Kheema Dosa price to $13.99.")

    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Kheema Dosa $13.99." in patch.notes_update
    assert "$13.99.99" not in patch.notes_update
    assert "Kheema Dosa $12.99" not in patch.notes_update


def test_extract_revision_patch_flags_item_specific_price_without_adjacent_price():
    project = _project(FlyerRequestFields(
        event_or_business_name="Weekend Breakfast Specials",
        contact_info="+1 732 983 7841",
        notes="Items: Kheema Dosa, Poori $8.99.",
    ))

    patch = extract_revision_patch(project, "Change Kheema Dosa price to $9.99.")

    assert patch.changed is False
    assert patch.ambiguous is True
    assert "Kheema Dosa" in patch.unresolved_reason


def test_location_from_to_revision_does_not_change_title():
    project = _project(FlyerRequestFields(
        event_or_business_name="Weekend Breakfast",
        contact_info="+1 704 324 3322",
        venue_or_location="Triveni Pineville",
        notes="Breakfast menu",
    ))
    patch = extract_revision_patch(project, "In the flyer, change location from Triveni Pineville to Lakshmi's Kitchen.")
    assert patch.field_updates == {"venue_or_location": "Lakshmi's Kitchen"}
