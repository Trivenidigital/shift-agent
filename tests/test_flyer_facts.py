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


def test_generated_item_suggestion_gate_biases_to_faithful_mode():
    from agents.flyer.facts import requests_generated_item_suggestions

    assert requests_generated_item_suggestions("Include 8 famous South Indian breakfast items")
    assert requests_generated_item_suggestions("weekend flyer, include Idli and Vada, 8 items total")
    assert requests_generated_item_suggestions("Dosa Night flyer. Include 6 dosa varieties.")
    assert not requests_generated_item_suggestions(
        "Can we do meal combo flyer with prices 49.99 for non veg combo includes "
        "2 non veg curries, 1 chicken pulav or chicken biryani and 1 dessert"
    )
    assert not requests_generated_item_suggestions(
        "Meal combo flyer: non veg combo includes 2 items total and dessert"
    )
    assert not requests_generated_item_suggestions(
        "Pick any 4 dosa combo, all items $15.99"
    )


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


def test_extract_text_facts_uses_hermes_semantic_provider_when_available(monkeypatch):
    from agents.flyer import facts as facts_module
    from agents.flyer.semantic_brief import FlyerSemanticBrief, FlyerSemanticOffer

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(
            campaign_title="Weekend Snack Box",
            pricing_structure="Any item $7.99",
            offers=[FlyerSemanticOffer("Free Masala Chai with any purchase above $12")],
        )

    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: provider)

    raw = "Create a flyer for Weekend Snack Box, any item $7.99. Free Masala Chai with any purchase above $12."
    facts = facts_module.extract_text_facts(FlyerRequestFields(), raw, profile_business_name="Lakshmi's Kitchen")
    by_id = facts_module.facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["campaign_title"].value == "Weekend Snack Box"
    assert by_id["pricing_structure"].value == "Any item $7.99"
    assert by_id["offer:0"].value == "Free Masala Chai with any purchase above $12"


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


def test_extract_text_facts_applies_generic_any_item_price_to_all_included_items():
    from agents.flyer.facts import extract_text_facts, facts_by_id

    raw_request = (
        "Create a flyer for Weekend Breakfast Specials from 8 AM to 11 AM, Friday to Sunday. "
        "Include Idli, Dosa, Vada, Pongal, Poori. Price any item $9.99."
    )
    fields = FlyerRequestFields(
        event_or_business_name="Weekend Breakfast Specials",
        contact_info="+17329837841",
        notes=raw_request,
    )

    facts = extract_text_facts(fields, raw_request, message_id="m-breakfast")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert [by_id[f"item:{idx}:name"].value for idx in range(5)] == [
        "Idli",
        "Dosa",
        "Vada",
        "Pongal",
        "Poori",
    ]
    assert [by_id[f"item:{idx}:price"].value for idx in range(5)] == ["$9.99"] * 5
    assert all("price any" not in fact.value.lower() for fact in facts)


def test_extract_text_facts_handles_compact_menu_price_shorthand():
    from agents.flyer.facts import extract_text_facts, facts_by_id

    raw_request = (
        "Create a flyer for breakfast menu Idli-$1each Dosa-$2each "
        "Upma-5plate Gaarelu-$1each Morning 8am to 10am, Monday to Friday"
    )
    fields = FlyerRequestFields(
        event_or_business_name="Breakfast Menu",
        contact_info="+19802005022",
        notes=raw_request,
    )

    facts = extract_text_facts(fields, raw_request, message_id="m-breakfast")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert [by_id[f"item:{idx}:name"].value for idx in range(4)] == [
        "Idli",
        "Dosa",
        "Upma",
        "Gaarelu",
    ]
    assert [by_id[f"item:{idx}:price"].value for idx in range(4)] == ["$1", "$2", "$5", "$1"]
    poisoned = " ".join(fact.value.lower() for fact in facts)
    assert "each dosa" not in poisoned
    assert "each upma" not in poisoned
    assert "morning 8am" not in poisoned


def test_extract_text_facts_does_not_treat_quantity_pieces_as_price():
    from agents.flyer.facts import extract_text_facts, facts_by_id

    raw_request = "Create a snacks flyer with Samosa-6pcs Chai-$2each Coffee-$3each"
    fields = FlyerRequestFields(
        event_or_business_name="Snack Menu",
        contact_info="+19802005022",
        notes=raw_request,
    )

    facts = extract_text_facts(fields, raw_request, message_id="m-snacks")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["item:0:name"].value == "Chai"
    assert by_id["item:0:price"].value == "$2"
    assert by_id["item:1:name"].value == "Coffee"
    assert by_id["item:1:price"].value == "$3"
    assert "item:2:name" not in by_id
    assert "samosa" not in " ".join(fact.value.lower() for fact in facts)


def test_extract_text_facts_locks_all_you_can_eat_offer_price():
    from agents.flyer.facts import extract_text_facts, facts_by_id

    raw_request = (
        "Create a flyer for mid-night biryani. Include all famous biryanis, "
        "all you can eat @ $25.99"
    )
    fields = FlyerRequestFields(
        event_or_business_name="Mid-night biryani",
        contact_info="+17329837841",
        notes=raw_request,
    )

    facts = extract_text_facts(fields, raw_request, message_id="m-biryani")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["offer_price"].value == "$25.99"
    assert by_id["offer_price"].required is True
    assert by_id["offer_price"].source == "customer_text"


def test_extract_text_facts_handles_price_for_protein_biryani_items():
    from agents.flyer.facts import extract_text_facts, facts_by_id

    raw_request = (
        "Create a Special Biryani's Flyer with all famous south indian biryani's included, "
        "add Price as $16.99 for chicken and $18.99 for goat. "
        "This promotion runs on Wednesday and Thursday of every week. "
        "Use address and phone number stored."
    )
    fields = FlyerRequestFields(
        event_or_business_name="Special Biryani's",
        contact_info="+17329837841",
        notes=raw_request,
    )

    facts = extract_text_facts(fields, raw_request, message_id="m-biryani-prices")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["item:0:name"].value == "Chicken Biryani"
    assert by_id["item:0:price"].value == "$16.99"
    assert by_id["item:1:name"].value == "Goat Biryani"
    assert by_id["item:1:price"].value == "$18.99"
    assert by_id["schedule"].value == "Wednesday and Thursday every week"
    assert all("price as" not in fact.value.lower() for fact in facts)


def test_extract_text_facts_keeps_price_pairs_when_include_clause_names_category():
    from agents.flyer.facts import extract_text_facts, facts_by_id

    raw_request = (
        "Create a Special Biryani's Flyer using golden background. "
        "Include all famous south indian biryani's, add Price as $16.99 for chicken "
        "and $18.99 for goat. This promotion runs on Thursday of every week. "
        "Use address and phone number stored."
    )
    fields = FlyerRequestFields(
        event_or_business_name="Special Biryani's",
        contact_info="+17329837841",
        notes=raw_request,
    )

    facts = extract_text_facts(fields, raw_request, message_id="m-biryani-prices")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["item:0:name"].value == "Chicken Biryani"
    assert by_id["item:0:price"].value == "$16.99"
    assert by_id["item:1:name"].value == "Goat Biryani"
    assert by_id["item:1:price"].value == "$18.99"
    assert "item:2:name" not in by_id
    assert by_id["schedule"].value == "Thursday every week"


def test_profile_facts_keep_account_business_when_request_names_campaign_flyer():
    from agents.flyer.facts import extract_text_facts, facts_by_id, merge_locked_facts, profile_locked_facts
    from schemas import FlyerCustomerProfile

    raw_request = (
        "Create a Special Biryani's Flyer using golden background. "
        "Use address and phone number stored."
    )
    customer = FlyerCustomerProfile(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        public_phone="+17329837841",
        business_whatsapp_number="+17329837841",
        primary_chat_id="17329837841@s.whatsapp.net",
        business_category="Indian restaurant",
        plan_id="trial",
        status="trial",
        created_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
    )
    fields = FlyerRequestFields(
        event_or_business_name="Special Biryani's",
        contact_info="+17329837841",
        notes=raw_request,
    )

    facts = merge_locked_facts(
        profile_locked_facts(customer, raw_request=raw_request, message_id="m-biryani"),
        extract_text_facts(
            fields,
            raw_request,
            message_id="m-biryani",
            profile_business_name=customer.business_name,
            allow_text_identity=False,
        ),
    )
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["business_name"].value == "Lakshmi's Kitchen"
    assert by_id["business_name"].source == "customer_profile"
    assert by_id["campaign_title"].value == "Special Biryani's"


def test_campaign_title_strips_trailing_medium_word():
    from agents.flyer.facts import extract_text_facts, facts_by_id

    raw_request = "Create Weekend Combo Poster with warm colors."
    fields = FlyerRequestFields(
        event_or_business_name="Weekend Combo Poster",
        notes=raw_request,
    )

    facts = extract_text_facts(fields, raw_request, message_id="m-campaign-title")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["campaign_title"].value == "Weekend Combo"


def test_combo_occasion_becomes_campaign_title_not_generic_combo_labels():
    from agents.flyer.facts import extract_text_facts, facts_by_id

    raw_request = (
        "Can we do meal combo flyer for veg and non veg with prices 49.99 for non veg combo "
        "includes 2 non veg curries, 1 chicken pulav or chicken Biryani and 1 dessert. "
        "And a veg combo 39.99 includes 2 veg curries, 1 dessert on the occasion of "
        "Memorial Day weekend"
    )
    fields = FlyerRequestFields(
        event_or_business_name="Veg And Non Veg",
        notes=raw_request,
    )

    facts = extract_text_facts(fields, raw_request, message_id="m-combo-occasion")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["campaign_title"].value == "Memorial Day Weekend Meal Combos"
    assert by_id["offer:0"].value.startswith("Non Veg Combo: $49.99")
    assert by_id["offer:1"].value.startswith("Veg Combo: $39.99")


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


# ---------- Slice 1: bounded-creative-planner provenance type (inert) ----------
# These prove the two new provenance values are ACCEPTED by the type system + ranked
# correctly in the merge, while existing behavior is unchanged and NO producer exists.

import pathlib  # noqa: E402

_NEW_SOURCES = ("hermes_inferred", "customer_confirmed")


def test_new_provenance_sources_are_valid_fact_sources():
    """hermes_inferred / customer_confirmed are now valid FlyerFactSource values
    (an unknown source still raises — no widening beyond the two)."""
    for src in _NEW_SOURCES:
        fact = FlyerLockedFact.model_validate(
            {"fact_id": "headline", "label": "Headline", "value": "X", "source": src}
        )
        assert fact.source == src
    with pytest.raises(Exception):
        FlyerLockedFact.model_validate(
            {"fact_id": "headline", "label": "Headline", "value": "X", "source": "previous_project"}
        )


def test_context_isolation_accepts_new_provenance_sources():
    """A project carrying hermes_inferred / customer_confirmed facts is NOT flagged
    for an invalid source (they are in ALLOWED_NEW_PROJECT_FACT_SOURCES)."""
    from agents.flyer.facts import context_isolation_blockers

    project = _project(locked_facts=[
        {"fact_id": "item:0:name", "label": "Item", "value": "Idly", "source": "hermes_inferred"},
        {"fact_id": "headline", "label": "Headline", "value": "Fresh", "source": "customer_confirmed"},
    ])
    assert context_isolation_blockers(project) == []


def test_merge_priority_hermes_inferred_never_shadows_a_real_fact():
    """hermes_inferred is the lowest priority — it loses to every real source,
    including the previously-lowest (system)."""
    from agents.flyer.facts import merge_locked_facts

    for real in ("customer_text", "customer_confirmed", "operator", "customer_profile",
                 "reference_ocr", "reference_vision", "uploaded_asset", "system"):
        inferred = [FlyerLockedFact(fact_id="headline", label="Headline", value="GUESS", source="hermes_inferred")]
        grounded = [FlyerLockedFact(fact_id="headline", label="Headline", value="REAL", source=real)]
        # order-independent: real wins whether seen first or second
        assert {f.fact_id: f.value for f in merge_locked_facts(inferred, grounded)}["headline"] == "REAL"
        assert {f.fact_id: f.value for f in merge_locked_facts(grounded, inferred)}["headline"] == "REAL"


def test_merge_priority_customer_confirmed_below_text_above_others():
    """customer_confirmed loses to literal customer_text but beats operator/profile/
    reference/system (it is customer-validated truth for the project)."""
    from agents.flyer.facts import merge_locked_facts

    confirmed = [FlyerLockedFact(fact_id="headline", label="Headline", value="CONFIRMED", source="customer_confirmed")]
    text = [FlyerLockedFact(fact_id="headline", label="Headline", value="TEXT", source="customer_text")]
    assert {f.fact_id: f.value for f in merge_locked_facts(confirmed, text)}["headline"] == "TEXT"

    for lower in ("operator", "customer_profile", "reference_ocr", "reference_vision", "uploaded_asset", "system"):
        other = [FlyerLockedFact(fact_id="headline", label="Headline", value="OTHER", source=lower)]
        assert {f.fact_id: f.value for f in merge_locked_facts(other, confirmed)}["headline"] == "CONFIRMED"


def test_existing_seven_source_relative_order_unchanged():
    """No-regression: the original sources keep their relative precedence
    (customer_text > operator > customer_profile > reference_ocr > reference_vision
    > uploaded_asset > system) after the Option-B renumber."""
    from agents.flyer.facts import merge_locked_facts

    chain = ["customer_text", "operator", "customer_profile", "reference_ocr",
             "reference_vision", "uploaded_asset", "system"]
    for higher, lower in zip(chain, chain[1:]):
        hi = [FlyerLockedFact(fact_id="headline", label="Headline", value="HI", source=higher)]
        lo = [FlyerLockedFact(fact_id="headline", label="Headline", value="LO", source=lower)]
        assert {f.fact_id: f.value for f in merge_locked_facts(lo, hi)}["headline"] == "HI"


def test_no_production_producer_of_new_provenance_sources():
    """Provenance producer invariant (evolved at slice 2).

    Slice 1 had NO producer. Slice 2 introduces ONE sanctioned, firewall-gated
    producer of `hermes_inferred`: `creative_planner.materialize_inferred`. So:
      - `hermes_inferred` may be emitted ONLY in creative_planner.py.
      - `customer_confirmed` may be emitted NOWHERE yet (that producer lands with
        the revision lifecycle in slice 4).
    Any other production emission of either source fails.

    AST-based (multiline-proof): flags a `_fact(...)` / `FlyerLockedFact(...)` call
    carrying a new-source string (positional or `source=` kwarg), a
    `source = "..."` assignment, or a fact-dict `{"source": "..."}`. The inert
    plumbing (Literal, allowlist set, priority dict) is not such a call/assignment,
    so it is naturally ignored."""
    import ast

    flyer_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "agents" / "flyer"
    EMIT_FUNCS = {"_fact", "FlyerLockedFact"}
    # source -> set of filenames permitted to emit it. creative_planner.py is the
    # SOLE sanctioned emitter of both new sources: it produces hermes_inferred
    # candidates (materialize_inferred) and performs the slice-4 provenance-lifecycle
    # promotion hermes_inferred -> customer_confirmed (promote_inferred_to_confirmed,
    # on customer approval). No other production module may emit either source — the
    # consuming script only CALLS the promotion helper, it never sets the source.
    SANCTIONED = {
        "hermes_inferred": {"creative_planner.py"},
        "customer_confirmed": {"creative_planner.py"},
    }

    def _new_source(node):
        if isinstance(node, ast.Constant) and node.value in _NEW_SOURCES:
            return node.value
        return None

    def _python_sources():
        """All production Python under the flyer agent: every *.py (recursive)
        PLUS extensionless shebang-python scripts in scripts/ — the latter are the
        actual fact producers (e.g. create-flyer-project, generate-flyer-concepts),
        which a plain glob('*.py') would miss (Codex round-2 finding)."""
        seen = set()
        for p in flyer_dir.rglob("*.py"):
            seen.add(p.resolve())
            yield p
        for p in flyer_dir.rglob("*"):
            if not p.is_file() or p.suffix == ".py" or p.resolve() in seen:
                continue
            try:
                first = (p.read_text(encoding="utf-8", errors="ignore").splitlines() or [""])[0]
            except OSError:
                continue
            if first.startswith("#!") and "python" in first:
                yield p

    def _record(offenders, py, lineno, src):
        if py.name not in SANCTIONED.get(src, set()):
            offenders.append(f"{py.name}:{lineno}: emits source={src!r}")

    offenders = []
    for py in sorted(_python_sources()):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            # 1) _fact(...) / FlyerLockedFact(...) with a new-source string arg
            if isinstance(node, ast.Call):
                fn = node.func
                fname = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if fname in EMIT_FUNCS:
                    for a in list(node.args) + [kw.value for kw in node.keywords]:
                        src = _new_source(a)
                        if src:
                            _record(offenders, py, node.lineno, src)
            # 2) source = "hermes_inferred" / source = "customer_confirmed"
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if getattr(tgt, "id", None) == "source" or getattr(tgt, "attr", None) == "source":
                        src = _new_source(node.value)
                        if src:
                            _record(offenders, py, node.lineno, src)
            # 3) fact-dict literal {"source": "<new>"}
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "source":
                        src = _new_source(v)
                        if src:
                            _record(offenders, py, node.lineno, src)
    # Self-check: prove the scan actually reached the real fact-producing scripts
    # (extensionless), so the guard can't silently pass by scanning nothing.
    scanned_names = {p.name for p in _python_sources()}
    for must in ("create-flyer-project", "generate-flyer-concepts", "facts.py", "creative_planner.py"):
        assert must in scanned_names, f"producer-guard did not scan {must} (scan scope regressed)"
    assert offenders == [], (
        f"only creative_planner may emit hermes_inferred/customer_confirmed; found: {offenders}"
    )


def test_generic_item_price_captures_connector_less_flat_price():
    """Oracle F0130b regression: a bare flat price 'any item $8.99' (no at/for/priced)
    must be captured, like the connector forms; a % discount must NOT be."""
    from agents.flyer.facts import _generic_item_price
    assert _generic_item_price("8 famous items, any item $8.99") == "$8.99"          # bare (the fix)
    assert _generic_item_price("every breakfast item $7.50") == "$7.50"              # bare + word
    assert _generic_item_price("any item at $8.99") == "$8.99"                       # connector form still works
    assert _generic_item_price("all items 10% off") == ""                           # discount, not a flat price
    assert _generic_item_price("weekend special flyer") == ""                        # no price


# ── extractor truth fix: garbage offers never become required offer:N facts ──
# Live combo incident (2026-06-06): facts.extract_text_facts locked two oversized
# REQUIRED offer:N customer_text facts (a request echo + an invented $99 + prose),
# which the firewall rejected or render overflowed. The provider-grounded brief
# must yield only bounded faithful offers; nothing > 180 chars (render's
# _clean_fact_text hard-fail boundary), no invented $99, no generated prose.

def test_extract_text_facts_combo_drops_garbage_offer_facts(monkeypatch):
    from agents.flyer import facts as facts_module
    from agents.flyer.semantic_brief import FlyerSemanticBrief, FlyerSemanticOffer

    raw = (
        "Can we do meal combo flyer for veg and non veg with prices 49.99 "
        "for non veg combo include"
    )
    echo_offer = (
        "Non Veg Combo: $49.99 includes Veg And Non Veg Can we do meal combo flyer "
        "for veg and non veg with prices 49.99 for non veg combo include"
    )
    invented_offer = (
        "Non Veg Combo: $99 includes professional local food menu flyer with "
        "appetizing photography, strong promotional"
    )

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(
            offers=[FlyerSemanticOffer(echo_offer), FlyerSemanticOffer(invented_offer)]
        )

    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: provider)

    facts = facts_module.extract_text_facts(
        FlyerRequestFields(notes=raw), raw, message_id="m-combo"
    )

    offer_facts = [f for f in facts if f.fact_id.startswith("offer:")]
    assert offer_facts == []  # both garbage offers dropped — no offer:N locked
    blob = " ".join(f.value for f in facts)
    assert "$99" not in blob                       # invented price never enters facts
    assert "appetizing photography" not in blob    # generated prose never enters facts
    # No REQUIRED fact exceeds the render's 180-char _clean_fact_text limit.
    assert all(len(f.value) <= 180 for f in facts if f.required)


def test_extract_text_facts_keeps_faithful_offer_fact(monkeypatch):
    """A short, grounded, faithful offer is still locked as offer:0 (no over-rejection)."""
    from agents.flyer import facts as facts_module
    from agents.flyer.semantic_brief import FlyerSemanticBrief, FlyerSemanticOffer

    raw = (
        "Create a flyer for evening snacks sale, any item $7.99. "
        "Free Masala Chai with any purchase above $12."
    )

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(
            offers=[FlyerSemanticOffer("Free Masala Chai with any purchase above $12")]
        )

    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: provider)

    facts = facts_module.extract_text_facts(FlyerRequestFields(notes=raw), raw, message_id="m-snack")
    by_id = facts_module.facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["offer:0"].value == "Free Masala Chai with any purchase above $12"


def test_creative_planner_hallucinated_item_is_not_required():
    """Requirement #2: an inferred/ungrounded planner item that survives the firewall is
    materialized as ADVISORY (required=False) and source=hermes_inferred — never a
    required customer-visible truth. A claim-bearing candidate is rejected outright."""
    from agents.flyer.creative_planner import CreativeCandidate, materialize_inferred
    from agents.flyer.creative_firewall import CreativeFirewall

    candidates = [
        CreativeCandidate(kind="item", value="Veg Manchurian"),   # benign inferred item
        CreativeCandidate(kind="item", value="Free Delivery"),    # claim disguised as item
    ]

    facts = materialize_inferred(candidates, firewall=CreativeFirewall())

    # The claim is dropped by the firewall; the benign item survives but is advisory.
    assert [f.value for f in facts] == ["Veg Manchurian"]
    assert all(f.required is False for f in facts)
    assert all(f.source == "hermes_inferred" for f in facts)
