from __future__ import annotations

from datetime import datetime, timezone

import pytest

from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields


def _project(**updates):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    base = {
        "project_id": "F9001",
        "status": "intake_started",
        "customer_phone": "+17329837841",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "m-1",
        "raw_request": "Create flyer. Headline: Family Combo Feast. Tagline: Fresh food. Happy family.",
        "fields": {
            "event_or_business_name": "Lakshmis Kitchn",
            "venue_or_location": "90 Brybar Dr St Johns FL",
            "contact_info": "+17329837841",
            "notes": "Headline: Family Combo Feast. Tagline: Fresh food. Happy family. Idly $7, Dosa $8.",
        },
    }
    base.update(updates)
    return FlyerProject.model_validate(base)


def test_flyer_project_accepts_locked_facts_with_provenance():
    project = _project(locked_facts=[
        {
            "fact_id": "headline",
            "label": "Headline",
            "value": "Family Combo Feast",
            "source": "customer_text",
            "required": True,
            "source_message_id": "m-1",
        },
        {
            "fact_id": "contact_phone",
            "label": "Contact",
            "value": "+17329837841",
            "source": "customer_profile",
            "required": True,
        },
    ])

    assert project.locked_facts[0].value == "Family Combo Feast"
    assert project.locked_facts[0].source_message_id == "m-1"


def test_extract_text_facts_splits_visible_copy_from_style_instructions():
    from agents.flyer.facts import extract_text_facts, facts_by_id

    fields = FlyerRequestFields(
        event_or_business_name="Lakshmis Kitchn",
        contact_info="+17329837841",
        notes=(
            "Headline: Family Combo Feast. Tagline: Fresh food. Happy family. "
            "Idly $7, Dosa $8. Use green, gold, and warm rustic textures."
        ),
    )

    facts = extract_text_facts(fields, fields.notes, message_id="m-1")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["business_name"].value == "Lakshmis Kitchn"
    assert by_id["headline"].value == "Family Combo Feast"
    assert by_id["tagline"].value == "Fresh food. Happy family"
    assert by_id["item:0:name"].value == "Idly"
    assert by_id["item:0:price"].value == "$7"
    assert all("rustic textures" not in fact.value for fact in facts)


def test_context_isolation_blocks_stale_project_provenance():
    from agents.flyer.facts import context_isolation_blockers

    project = _project(locked_facts=[
        {
            "fact_id": "headline",
            "label": "Headline",
            "value": "Old Promo",
            "source": "system",
            "source_project_id": "F0001",
        }
    ])

    assert context_isolation_blockers(project) == ["locked fact headline carries stale project provenance F0001"]


def test_invalid_fact_source_rejected():
    with pytest.raises(Exception):
        FlyerLockedFact.model_validate({
            "fact_id": "headline",
            "label": "Headline",
            "value": "Bad",
            "source": "previous_project",
        })
