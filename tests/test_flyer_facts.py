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


def test_merge_locked_facts_overrides_reference_items_by_name_not_position():
    from agents.flyer.facts import merge_locked_facts

    customer = [
        FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text"),
        FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.50", source="customer_text"),
    ]
    reference = [
        FlyerLockedFact(fact_id="item:0:name", label="Item", value="Idly", source="reference_vision"),
        FlyerLockedFact(fact_id="item:0:price", label="Price", value="$7", source="reference_vision"),
        FlyerLockedFact(fact_id="item:1:name", label="Item", value="Dosa", source="reference_vision"),
        FlyerLockedFact(fact_id="item:1:price", label="Price", value="$8", source="reference_vision"),
    ]

    merged = merge_locked_facts(customer, reference)
    by_id = {fact.fact_id: fact for fact in merged}

    assert by_id["item:0:name"].value == "Dosa"
    assert by_id["item:0:price"].value == "$8.50"
    assert by_id["item:1:name"].value == "Idly"
    assert by_id["item:1:price"].value == "$7"
    assert [fact.value for fact in merged if fact.value == "Dosa"] == ["Dosa"]


def test_extract_text_facts_handles_price_first_without_prompt_prefix_pollution():
    from agents.flyer.facts import extract_text_facts, facts_by_id

    fields = FlyerRequestFields(
        event_or_business_name="Chloe Hair Studio",
        contact_info="+1 757 555 0199",
        notes="Create flyer for Chloe Hair Studio promoting the $20 men haircut, $80 perms, and $7 kids trim.",
    )

    facts = extract_text_facts(fields, fields.notes, message_id="m-1")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["item:0:name"].value == "men haircut"
    assert by_id["item:0:price"].value == "$20"
    assert by_id["item:1:name"].value == "perms"
    assert by_id["item:1:price"].value == "$80"
    assert by_id["item:2:name"].value == "kids trim"
    assert by_id["item:2:price"].value == "$7"
    assert "item:3:name" not in by_id
    assert all("Create flyer" not in fact.value for fact in facts)


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


# ---------- P0-2 missing-required-fact gate ----------

def test_missing_required_facts_detects_absent_slots():
    from agents.flyer.facts import missing_required_facts

    project = _project(locked_facts=[
        {"fact_id": "headline", "label": "Headline", "value": "Family Combo", "source": "customer_text"},
    ])

    missing = missing_required_facts(project)

    # business_name and contact_phone are required by default; neither is in locked_facts.
    assert sorted(missing) == ["business_name", "contact_phone"]


def test_missing_required_facts_empty_value_counts_as_missing():
    from agents.flyer.facts import missing_required_facts

    project = _project(locked_facts=[
        {"fact_id": "business_name", "label": "Business", "value": " ", "source": "customer_text"},
        {"fact_id": "contact_phone", "label": "Contact", "value": "+17329837841", "source": "customer_profile"},
    ])

    # whitespace-only values must not count as satisfying the requirement.
    assert missing_required_facts(project) == ["business_name"]


def test_missing_required_facts_returns_empty_when_all_slots_present():
    from agents.flyer.facts import missing_required_facts

    project = _project(locked_facts=[
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis", "source": "customer_text"},
        {"fact_id": "contact_phone", "label": "Contact", "value": "+17329837841", "source": "customer_profile"},
    ])

    assert missing_required_facts(project) == []


def test_missing_required_facts_respects_caller_override():
    from agents.flyer.facts import missing_required_facts

    project = _project(locked_facts=[
        {"fact_id": "business_name", "label": "Business", "value": "Lakshmis", "source": "customer_text"},
    ])

    # Caller specifies only business_name as required → no missing.
    assert missing_required_facts(project, required_ids=("business_name",)) == []


# ---------- P0-2 fact_value helper for renderer integration ----------

def test_fact_value_returns_locked_value_when_present():
    from agents.flyer.facts import fact_value

    project = _project(locked_facts=[
        {"fact_id": "business_name", "label": "Business", "value": "Chloe Hair Studio", "source": "customer_text"},
    ])

    assert fact_value(project, "business_name", fallback="OldName") == "Chloe Hair Studio"


def test_fact_value_falls_back_when_locked_fact_missing():
    from agents.flyer.facts import fact_value

    project = _project(locked_facts=[])  # nothing locked
    assert fact_value(project, "business_name", fallback="Lakshmis Kitchn") == "Lakshmis Kitchn"


def test_fact_value_falls_back_when_locked_value_blank():
    from agents.flyer.facts import fact_value

    project = _project(locked_facts=[
        {"fact_id": "business_name", "label": "Business", "value": "  ", "source": "customer_text"},
    ])

    assert fact_value(project, "business_name", fallback="ProfileBiz") == "ProfileBiz"


# ---------- P0-2 logo-only and reference-only assets do not invent item/price facts ----------

def test_reference_extraction_logo_role_does_not_create_item_price_facts(tmp_path, monkeypatch):
    """Pinning: reference extraction for a logo asset must NOT produce item:N facts.
    The classifier returns 'logo' and `extract_reference` short-circuits with empty
    extracted_facts. Today this is structurally true; locking it down here so a
    future broadening of the logo classifier doesn't regress.
    """
    from agents.flyer.reference_extract import classify_reference_role, extract_reference
    from schemas import FlyerAsset

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(b"fake image")
    asset = FlyerAsset(
        asset_id="A0001",
        kind="logo",
        source="whatsapp",
        path=str(logo_path),
        mime_type="image/png",
        sha256="0" * 64,
        received_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
    )
    raw_request = "Use this as the new logo"

    role = classify_reference_role(raw_request, asset)
    assert role == "logo"

    extraction = extract_reference(asset, raw_request=raw_request)
    assert extraction.role == "logo"
    assert extraction.status == "not_run"
    assert extraction.extracted_facts == []


# ---------- P0-2 typed facts override reference facts (provenance precedence) ----------

def test_typed_phone_overrides_profile_phone_in_merge():
    """When customer_text supplies a contact_phone and customer_profile supplies a
    different one, the merged locked_facts must carry the customer_text value —
    typed corrections beat stale profile data."""
    from agents.flyer.facts import merge_locked_facts

    profile_facts = [
        FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+19045550104", source="customer_profile"),
    ]
    typed_facts = [
        FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_text"),
    ]

    merged = merge_locked_facts(profile_facts, typed_facts)
    by_id = {fact.fact_id: fact for fact in merged}
    assert by_id["contact_phone"].value == "+17329837841"
    assert by_id["contact_phone"].source == "customer_text"
