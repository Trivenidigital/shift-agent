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
        "closed_no_send",
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
        assert "F9003" not in reply
        assert "project F" not in reply.lower()
        assert "resend" not in reply.lower()


def test_source_edit_provider_ready_reads_shift_agent_env_file(tmp_path, monkeypatch):
    from agents.flyer.workflow import source_edit_provider_ready

    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=sk-or-test\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ok, detail = source_edit_provider_ready(
        {"assets": [{"kind": "reference_image", "mime_type": "image/png", "path": "x.png"}]},
        provider={"provider": "openrouter", "model": "openai/gpt-5.4-image-2"},
        env_path=env_path,
    )

    assert ok
    assert detail == "source edit provider configured: openrouter/openai/gpt-5.4-image-2"


def test_source_edit_provider_ready_openrouter_does_not_require_openai(tmp_path, monkeypatch):
    from agents.flyer.workflow import source_edit_provider_ready

    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=sk-or-test\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ok, detail = source_edit_provider_ready(
        {"assets": [{"kind": "reference_image", "mime_type": "image/png", "path": "x.png"}]},
        provider={"provider": "openrouter", "model": "openai/gpt-5.4-image-2"},
        env_path=env_path,
    )

    assert ok is True
    assert detail == "source edit provider configured: openrouter/openai/gpt-5.4-image-2"


def test_source_edit_provider_ready_without_explicit_provider_fails_closed(tmp_path, monkeypatch):
    from agents.flyer.workflow import source_edit_provider_ready

    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=sk-or-test\nOPENAI_API_KEY=sk-test\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ok, detail = source_edit_provider_ready(
        {"assets": [{"kind": "reference_image", "mime_type": "image/png", "path": "x.png"}]},
        env_path=env_path,
    )

    assert ok is False
    assert detail == "source edit provider configured for manual review"


def test_source_edit_provider_ready_openrouter_missing_key_fails(tmp_path, monkeypatch):
    from agents.flyer.workflow import source_edit_provider_ready

    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=PLACEHOLDER-key\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ok, detail = source_edit_provider_ready(
        {"assets": [{"kind": "reference_image", "mime_type": "image/png", "path": "x.png"}]},
        provider={"provider": "openrouter", "model": "openai/gpt-5.4-image-2"},
        env_path=env_path,
    )

    assert ok is False
    assert detail == "source edit provider is not configured: OPENROUTER_API_KEY missing"


def test_source_edit_provider_ready_explicit_openai_still_requires_openai(tmp_path, monkeypatch):
    from agents.flyer.workflow import source_edit_provider_ready

    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=sk-or-test\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ok, detail = source_edit_provider_ready(
        {"assets": [{"kind": "reference_image", "mime_type": "image/png", "path": "x.png"}]},
        provider={"provider": "openai", "model": "gpt-image-1"},
        env_path=env_path,
    )

    assert ok is False
    assert detail == "source edit provider is not configured: OPENAI_API_KEY missing"


def test_read_env_value_checks_hermes_env_before_shift_agent_env(tmp_path, monkeypatch):
    """P0-5 follow-up: source-edit env lookup must mirror visual_qa's lookup
    order — Hermes-managed `/root/.hermes/.env` is checked before the
    agent-local `/opt/shift-agent/.env`. Operators who provision OPENAI_API_KEY
    via the Hermes env store should not have their key missed by source-edit.
    """
    from agents.flyer.workflow import _read_env_value

    hermes_env = tmp_path / "hermes.env"
    agent_env = tmp_path / "agent.env"
    hermes_env.write_text("OPENAI_API_KEY=sk-from-hermes\n", encoding="utf-8")
    agent_env.write_text("OPENAI_API_KEY=sk-from-agent\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HERMES_ENV_PATH", str(hermes_env))
    monkeypatch.setenv("SHIFT_AGENT_ENV_PATH", str(agent_env))

    # Hermes env wins because it's checked first.
    assert _read_env_value("OPENAI_API_KEY") == "sk-from-hermes"


def test_read_env_value_falls_back_to_shift_agent_env_when_hermes_missing(tmp_path, monkeypatch):
    """When the Hermes env file doesn't exist, the agent-local .env still
    works — preserves backward compatibility with VPSes that haven't yet
    provisioned the Hermes path."""
    from agents.flyer.workflow import _read_env_value

    agent_env = tmp_path / "agent.env"
    agent_env.write_text("OPENAI_API_KEY=sk-from-agent\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HERMES_ENV_PATH", str(tmp_path / "does-not-exist.env"))
    monkeypatch.setenv("SHIFT_AGENT_ENV_PATH", str(agent_env))

    assert _read_env_value("OPENAI_API_KEY") == "sk-from-agent"


def test_read_env_value_explicit_env_path_overrides_lookup_chain(tmp_path, monkeypatch):
    """When the caller explicitly passes `env_path=`, only that file is
    consulted (preserves test isolation; matches the pre-S8 contract used
    by `source_edit_provider_ready`)."""
    from agents.flyer.workflow import _read_env_value

    explicit_env = tmp_path / "explicit.env"
    explicit_env.write_text("OPENAI_API_KEY=sk-explicit\n", encoding="utf-8")
    other_env = tmp_path / "other.env"
    other_env.write_text("OPENAI_API_KEY=sk-other\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HERMES_ENV_PATH", str(other_env))
    monkeypatch.setenv("SHIFT_AGENT_ENV_PATH", str(other_env))

    assert _read_env_value("OPENAI_API_KEY", env_path=explicit_env) == "sk-explicit"


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


def test_mixed_visual_and_price_revision_is_actionable():
    project = _project(FlyerRequestFields(
        event_or_business_name="Chloe Hair Studio",
        venue_or_location="11111 Gainsborough Ct, Fairfax, VA, 22030",
        contact_info="+19803826497",
        notes=(
            "Create flyer for Chloe Hair Studio promoting men haircut $20, perms $80, "
            "and other hair services."
        ),
    ))
    text = (
        "Apply these changes to the existing flyer: change the background to rich golden color "
        "and keep the pictures if 2 male and 2 female celebrities with different hairstyles each "
        "and keep prices as $40,$60,$80,100"
    )

    patch = extract_revision_patch(project, text)

    assert patch.changed is True
    assert patch.ambiguous is False
    assert patch.requires_confirmation is False
    update = f"{patch.notes_update or ''} {patch.raw_request_update or ''}"
    assert "rich golden color" in update
    assert "2 male and 2 female celebrities" in update
    assert "different hairstyles" in update
    assert "$40, $60, $80, $100" in update


def test_layout_focus_revision_with_create_new_wording_is_actionable():
    project = _project(FlyerRequestFields(
        event_or_business_name="Chloe Hair Studio",
        venue_or_location="11111 Gainsborough Ct, Fairfax, VA, 22030",
        contact_info="+19803826497",
        notes=(
            "Create flyer for Chloe Hair Studio with service cards for men haircut, "
            "perms, beard trim, and other hair services. Show contact number and address."
        ),
    ))
    text = (
        "Create a new flyer for chloe hair studio with contact number and address look smaller "
        "and the main focus should be on the services that we provide."
    )

    patch = extract_revision_patch(project, text)

    assert patch.changed is True
    assert patch.visual_only is False
    assert patch.ambiguous is False
    assert patch.requires_confirmation is False
    update = f"{patch.notes_update or ''} {patch.raw_request_update or ''}"
    assert "contact number and address look smaller" in update
    assert "main focus should be on the services" in update


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


def test_extract_revision_patch_replaces_visible_text_without_confirmation_when_exact():
    project = _project(FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        contact_info="+17329837841",
        notes="Green badge text: Price any event - $9.99.",
    ))
    patch = extract_revision_patch(project, 'Replace "Price any event" with "Any Item".')
    assert patch.changed is True
    assert patch.ambiguous is False
    assert patch.requires_confirmation is False
    assert "Any Item" in (patch.notes_update or "")
    assert "Price any event" not in (patch.notes_update or "")


def test_extract_revision_patch_flags_fuzzy_visible_text_replace_for_confirmation():
    project = _project(FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        contact_info="+17329837841",
        notes="Green badge text:\nPrice any\nevent - $9.99.",
    ))
    patch = extract_revision_patch(project, 'Replace "Price any event" with "Any Item".')
    assert patch.changed is True
    assert patch.ambiguous is False
    assert patch.requires_confirmation is True
    assert "replace text" in (patch.confirmation_reason or "").lower()


def test_extract_revision_patch_parses_replace_with_hyphen_variant():
    project = _project(FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        contact_info="+17329837841",
        notes="Green badge text: Price any event - $9.99.",
    ))
    patch = extract_revision_patch(project, 'Replace "Price any event" - with " Any Item".')
    assert patch.changed is True
    assert patch.ambiguous is False
    assert patch.requires_confirmation is False
    assert "Any Item" in (patch.notes_update or "")


def test_extract_revision_patch_parses_replace_with_mismatched_curly_quotes():
    project = _project(FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        contact_info="+17329837841",
        notes="Green badge text: Price any event - $9.99.",
    ))
    patch = extract_revision_patch(project, "Replace “Price any event -“ with “ Any Item”.")
    assert patch.changed is True
    assert patch.ambiguous is False
    assert patch.requires_confirmation is False
    assert "Any Item" in (patch.notes_update or "")


def test_extract_revision_patch_falls_back_to_instruction_when_old_text_not_in_details():
    project = _project(FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        contact_info="+17329837841",
        notes="",
    ))
    patch = extract_revision_patch(project, 'Replace "Price any event -" with "Any Item".')
    assert patch.changed is True
    assert patch.ambiguous is False
    assert patch.requires_confirmation is True
    assert "Replace visible text" in (patch.notes_update or "")


def test_extract_revision_patch_parses_replace_with_backticks():
    project = _project(FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        contact_info="+17329837841",
        notes="Green badge text: Price any event - $9.99.",
    ))
    patch = extract_revision_patch(project, "Replace `Price any event` with `Any Item`.")
    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Any Item" in (patch.notes_update or "")


def test_extract_revision_patch_parses_replace_without_quotes_confirmation_gated():
    project = _project(FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        contact_info="+17329837841",
        notes="",
    ))
    patch = extract_revision_patch(project, "Replace Price any event with Any Item.")
    assert patch.changed is True
    assert patch.ambiguous is False
    assert patch.requires_confirmation is True
    assert "Replace visible text" in (patch.notes_update or "")


def test_extract_revision_patch_handles_offer_arrow_with_price_delta():
    project = _project(FlyerRequestFields(
        event_or_business_name="Dosa Specials",
        contact_info="+17329837841",
        notes=(
            "Pick Any 3 Dosa for $20. "
            "Items: Masala Dosa $8.99, Onion Dosa $8.99, Rava Dosa $8.99."
        ),
    ))

    patch = extract_revision_patch(
        project,
        "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
    )

    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Pick Any 4 Dosa for $21" in (patch.notes_update or "")
    assert "Pick Any 3 Dosa" not in (patch.notes_update or "")
    assert "increase price" not in (patch.notes_update or "").lower()


def test_extract_revision_patch_handles_change_offer_with_price_delta_without_leaking_instruction():
    project = _project(FlyerRequestFields(
        event_or_business_name="Dosa Specials",
        contact_info="+17329837841",
        notes=(
            "Pick Any 3 Dosa for $20. "
            "Items: Masala Dosa $8.99, Onion Dosa $8.99, Rava Dosa $8.99."
        ),
    ))

    patch = extract_revision_patch(
        project,
        "Change Pick Any 3 Dosa to Pick Any 4 Dosa and increase price by $1.",
    )

    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Pick Any 4 Dosa for $21" in (patch.notes_update or "")
    assert "Pick Any 4 Dosa and increase price" not in (patch.notes_update or "")
    assert "Pick Any 3 Dosa" not in (patch.notes_update or "")


def test_extract_revision_patch_handles_prefixed_offer_arrow_with_price_delta():
    project = _project(FlyerRequestFields(
        event_or_business_name="Dosa Specials",
        contact_info="+17329837841",
        notes="Pick Any 3 Dosa for $20. Items: Masala Dosa $8.99.",
    ))

    patch = extract_revision_patch(
        project,
        "Please change Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
    )

    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Pick Any 4 Dosa for $21" in (patch.notes_update or "")
    assert "Pick Any 3 Dosa" not in (patch.notes_update or "")


def test_extract_revision_patch_handles_colon_prefixed_offer_arrow_with_price_delta():
    project = _project(FlyerRequestFields(
        event_or_business_name="Dosa Specials",
        contact_info="+17329837841",
        notes="Pick Any 3 Dosa for $20. Items: Masala Dosa $8.99.",
    ))

    patch = extract_revision_patch(
        project,
        "Can you update this: Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
    )

    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Pick Any 4 Dosa for $21" in (patch.notes_update or "")


def test_extract_revision_patch_handles_do_this_prefixed_offer_arrow_with_price_delta():
    project = _project(FlyerRequestFields(
        event_or_business_name="Dosa Specials",
        contact_info="+17329837841",
        notes="Pick Any 3 Dosa for $20. Items: Masala Dosa $8.99.",
    ))

    patch = extract_revision_patch(
        project,
        "Can you do this: Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
    )

    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Pick Any 4 Dosa for $21" in (patch.notes_update or "")


def test_extract_revision_patch_applies_price_delta_after_fuzzy_offer_match():
    project = _project(FlyerRequestFields(
        event_or_business_name="Dosa Specials",
        contact_info="+17329837841",
        notes="Pick Any 3    Dosa for $20. Items: Masala Dosa $8.99.",
    ))

    patch = extract_revision_patch(
        project,
        "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
    )

    assert patch.changed is True
    assert patch.requires_confirmation is True
    assert patch.ambiguous is False
    assert "Pick Any 4 Dosa for $21" in (patch.notes_update or "")
    assert "Pick Any 3    Dosa" not in (patch.notes_update or "")


def test_extract_revision_patch_fails_closed_when_offer_price_delta_has_no_nearby_price():
    project = _project(FlyerRequestFields(
        event_or_business_name="Dosa Specials",
        contact_info="+17329837841",
        notes="Pick Any 3 Dosa. Items: Masala Dosa $8.99, Onion Dosa $8.99.",
    ))

    patch = extract_revision_patch(
        project,
        "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
    )

    assert patch.changed is False
    assert patch.ambiguous is True
    assert "price not found near edited text" in patch.unresolved_reason


def test_extract_revision_patch_fails_closed_when_notes_have_repeated_offer_text():
    project = _project(
        FlyerRequestFields(
            event_or_business_name="Dosa Specials",
            contact_info="+17329837841",
            notes="Pick Any 3 Dosa for $20. Pick Any 3 Dosa for $20.",
        )
    ).model_copy(update={"raw_request": "Pick Any 3 Dosa for $20."})

    patch = extract_revision_patch(
        project,
        "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1.",
    )

    assert patch.changed is False
    assert patch.ambiguous is True
    assert "appears multiple times in flyer details" in patch.unresolved_reason


def test_extract_revision_patch_fails_closed_on_multiple_price_deltas():
    project = _project(FlyerRequestFields(
        event_or_business_name="Dosa Specials",
        contact_info="+17329837841",
        notes="Pick Any 3 Dosa for $20. Drink Special for $5.",
    ))

    patch = extract_revision_patch(
        project,
        "Pick any 3 Dosa -> Pick Any 4 Dosa, increase price by $1 and decrease drink price by $1.",
    )

    assert patch.changed is False
    assert patch.ambiguous is True
    assert "multiple price deltas not supported" in patch.unresolved_reason


def test_extract_revision_patch_does_not_double_apply_when_new_contains_old():
    project = _project(FlyerRequestFields(
        event_or_business_name="Happy Hour",
        contact_info="+17329837841",
        notes="Happy Hour Special.",
    )).model_copy(update={"raw_request": "Happy Hour Special."})

    patch = extract_revision_patch(
        project,
        'Replace "Happy Hour" with "Happy Hour Special".',
    )

    assert patch.changed is False
    assert patch.ambiguous is False
    assert patch.already_applied is True
    assert patch.notes_update is None
    assert patch.raw_request_update is None


def test_extract_revision_patch_does_not_double_apply_whitespace_variant_when_new_contains_old():
    project = _project(FlyerRequestFields(
        event_or_business_name="Happy Hour",
        contact_info="+17329837841",
        notes="Happy   Hour Special.",
    )).model_copy(update={"raw_request": "Happy Hour   Special."})

    patch = extract_revision_patch(
        project,
        'Replace "Happy Hour" with "Happy Hour Special".',
    )

    assert patch.changed is False
    assert patch.ambiguous is False
    assert patch.already_applied is True
    assert patch.notes_update is None
    assert patch.raw_request_update is None


def test_extract_revision_patch_fails_closed_on_whitespace_ambiguous_old_and_new():
    project = _project(FlyerRequestFields(
        event_or_business_name="Happy Hour",
        contact_info="+17329837841",
        notes="Happy   Hour and Happy    Hour Special.",
    )).model_copy(update={"raw_request": "Happy   Hour and Happy    Hour Special."})

    patch = extract_revision_patch(
        project,
        'Replace "Happy Hour" with "Happy Hour Special".',
    )

    assert patch.changed is False
    assert patch.ambiguous is True
    assert "appears multiple times in flyer details" in patch.unresolved_reason
    assert patch.notes_update is None
    assert patch.raw_request_update is None


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


def test_extract_revision_patch_handles_visible_time_text_before_duplicate_marker():
    project = _project(FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        event_time="16:00",
        venue_or_location="90 Brybar Dr",
        contact_info="+1 732 983 7841",
        notes="Evening Snacks. Schedule 4 PM to 7 PM. Preview also shows Time: 16:00.",
    ))

    patch = extract_revision_patch(
        project,
        "Time: 16:00 is duplicated. I'd like you to remove this.",
    )

    assert patch.changed is True
    assert patch.ambiguous is False
    assert 'Remove duplicate/extra time text "16:00"' in patch.notes_update
    assert patch.unresolved_reason == ""


def test_extract_revision_patch_handles_remove_time_without_duplicate_marker():
    project = _project(FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        event_time="16:00",
        venue_or_location="90 Brybar Dr",
        contact_info="+1 732 983 7841",
        notes="Evening Snacks. Schedule 4 PM to 7 PM. Preview also shows Time: 16:00.",
    ))
    patch = extract_revision_patch(project, "Why that 16:00 in the flyer. Please remove 16:00.")
    assert patch.changed is True
    assert patch.ambiguous is False
    assert 'Remove time text "16:00"' in patch.notes_update


def test_extract_revision_patch_handles_remove_time_with_ampm_without_duplicate_marker():
    project = _project(FlyerRequestFields(
        event_or_business_name="Evening Snacks",
        event_time="16:00",
        venue_or_location="90 Brybar Dr",
        contact_info="+1 732 983 7841",
        notes="Evening Snacks. Schedule 4 PM to 7 PM. Preview also shows Time: 4 PM.",
    ))
    patch = extract_revision_patch(project, "Why that 4 PM in the flyer. Please remove 4 PM.")
    assert patch.changed is True
    assert patch.ambiguous is False
    assert 'Remove time text "4 PM"' in patch.notes_update


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


def test_extract_revision_patch_handles_category_price_to_new_price():
    project = _project(FlyerRequestFields(
        event_or_business_name="Mid-Night Biryani",
        contact_info="+1 732 983 7841",
        notes=(
            "Create a flyer for mid-night biryani. Include all famous biryanis, "
            "all you can eat @ $25.99"
        ),
    ))

    patch = extract_revision_patch(project, "Update Prices of any biryani to $22.99")

    assert patch.changed is True
    assert patch.ambiguous is False
    assert "Set all biryani prices to $22.99" in patch.notes_update
    assert "all you can eat @ $25.99" in patch.notes_update


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


def test_extract_revision_patch_updates_day_range_without_corrupting_business_name():
    project = _project(FlyerRequestFields(
        event_or_business_name="MK kitchen",
        contact_info="+1 571 383 0763",
        venue_or_location="23596 prosperity ridge pl Ashburn Va 20148",
        notes=(
            "Create a professional flyer for MK kitchen. Evening snacks from 4 PM to 7 PM, "
            "Wednesday to Saturday. Include samosa, mirchi bajji, punugulu, masala vada, and tea."
        ),
    ))

    patch = extract_revision_patch(
        project,
        "Can you add the prices and make changes to the backdrop. Also change it to Tuesday to Sunday",
    )

    assert patch.changed is True
    assert patch.ambiguous is False
    assert patch.field_updates == {}
    assert "Use schedule Tuesday to Sunday" in (patch.notes_update or "")
    assert "Do not use Wednesday to Saturday" in (patch.notes_update or "")
    assert "MK kitchen" in (patch.notes_update or "")
    assert "kTuesday to Sundaychen" not in (patch.notes_update or "")


def _rev_project(event_date: str, created: str = "2026-06-02") -> FlyerProject:
    now = datetime.fromisoformat(created + "T00:00:00+00:00")
    return FlyerProject(
        project_id="F9060", status="awaiting_final_approval", customer_phone="+10000000000",
        created_at=now, updated_at=now, original_message_id="m", raw_request="flyer",
        locked_facts=[],
        fields=FlyerRequestFields(event_or_business_name="Lakshmis Kitchen", event_date=event_date),
    )


def test_month_day_edit_rolls_past_date_forward_to_next_year():
    # P1-2 roll-forward: created 2026-06-02; "March 15" this year is past -> next year.
    patch = extract_revision_patch(_rev_project("2026-06-10"), "change the date to March 15")
    assert patch.field_updates["event_date"] == "2027-03-15"


def test_month_day_edit_keeps_future_date_unchanged():
    patch = extract_revision_patch(_rev_project("2026-06-10"), "change the date to December 25")
    assert patch.field_updates["event_date"] == "2026-12-25"


def test_day_only_edit_rolls_past_day_forward_to_next_month():
    # created 2026-06-02, current month June; "the 1st" (June 1) is past -> July 1.
    patch = extract_revision_patch(_rev_project("2026-06-20"), "change the date to 1st")
    assert patch.field_updates["event_date"] == "2026-07-01"


def test_day_only_edit_keeps_future_day_unchanged():
    patch = extract_revision_patch(_rev_project("2026-06-10"), "change the date to 25th")
    assert patch.field_updates["event_date"] == "2026-06-25"


def test_day_only_edit_skips_month_without_that_day_and_rolls_to_future():
    # current month Feb; "the 30th" is invalid in Feb -> roll forward; Feb/Mar/Apr/May are
    # all before created 2026-06-02 -> first valid future occurrence is June 30 (crash-safe).
    patch = extract_revision_patch(_rev_project("2026-02-10"), "change the date to 30th")
    assert patch.field_updates["event_date"] == "2026-06-30"


def test_invalid_day_edit_records_unresolved_without_emitting_invalid_date():
    # Codex must-fix: "March 32" / "99th" are not valid calendar days -> never emit an invalid
    # date string (which would crash schema validation downstream); skip the event_date update
    # and record an unresolved edit so the customer is asked to clarify.
    for text in ("change the date to March 32", "change the date to 99th"):
        patch = extract_revision_patch(_rev_project("2026-06-10"), text)
        assert "event_date" not in patch.field_updates, text
        assert "not a valid calendar date" in patch.unresolved_reason, text
