from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields


DESSERT_GRADUATION_SUFFIX_PRICE_BRIEF = (
    "Graduation is here and time to celebrate our kids \n\n"
    "We take customized orders - Desserts\n\n"
    "Mango tresleches - half tray - 75$\n"
    "Rasmalai tresleches - half tray 70$\n"
    "Apricot delight - half tray - 80$\n"
    "Butter scotch - half tray 75$\n"
    "Strawberry pastry - half 70$\n"
    "Chocolate pastry - half 70$\n"
    "Gulab jamun - 100count - 80$\n"
    "Gulabjamun fusion - half tray - 75$\n"
    "Kheer(Ramadan style) half tray - 55$\n"
    "Kadhu ki sheet - small tray - 65$\n"
    "Double ka meeta - small tray - 45$\n"
    "Kurbanika meeta - small tray - 70$\n"
    "Carrot halwa - small tray 55$\n"
    "Khalakhandh - 100 count 100$"
)


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


def test_extract_text_facts_splits_colon_item_list_into_item_facts():
    """Live F0164: a 'N items: a, b, c' colon list (no 'include' verb) must become
    individual item:N:name facts (priced by the generic 'any item' price), NOT one
    compound required offer:0 fact the referee can only exact-match."""
    from agents.flyer.facts import extract_text_facts, facts_by_id

    raw_request = (
        "Create a flyer for Weekend Specials. Any item $7.99. "
        "6 famous South Indian items: Idli, Dosa, Vada, Uttapam, Pongal, Sambar. "
        "Available Saturday & Sunday, 4 PM-8 PM. Phone: +1 732-983-7841"
    )
    fields = FlyerRequestFields(
        event_or_business_name="Weekend Specials",
        contact_info="+17329837841",
        notes=raw_request,
    )

    facts = extract_text_facts(fields, raw_request, message_id="m-f0164")
    by_id = facts_by_id(type("P", (), {"locked_facts": facts})())

    assert [by_id[f"item:{idx}:name"].value for idx in range(6)] == [
        "Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar",
    ]
    assert [by_id[f"item:{idx}:price"].value for idx in range(6)] == ["$7.99"] * 6
    # The item list must NOT also be locked as a compound offer fact.
    assert not any(f.fact_id.startswith("offer:") for f in facts), \
        [f.fact_id for f in facts if f.fact_id.startswith("offer:")]


def test_extract_text_facts_item_list_offer_is_not_locked_as_compound_offer(monkeypatch):
    """When the semantic provider returns the item list AS an offer (live F0164:
    offer='6 famous South Indian items: Idli, ...'), it must be split into item
    facts, not locked as a compound required offer:0 the model can't exact-match."""
    from agents.flyer import facts as facts_module
    from agents.flyer.semantic_brief import FlyerSemanticBrief, FlyerSemanticOffer

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(
            campaign_title="Weekend Specials",
            pricing_structure="Any item $7.99",
            offers=[FlyerSemanticOffer(
                "6 famous South Indian items: Idli, Dosa, Vada, Uttapam, Pongal, Sambar"
            )],
        )

    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: provider)

    raw = (
        "Create a flyer for Weekend Specials. Any item $7.99. "
        "6 famous South Indian items: Idli, Dosa, Vada, Uttapam, Pongal, Sambar."
    )
    facts = facts_module.extract_text_facts(
        FlyerRequestFields(contact_info="+17329837841"), raw, message_id="m-f0164b",
    )
    by_id = facts_module.facts_by_id(type("P", (), {"locked_facts": facts})())

    # No compound offer fact carrying the item list.
    assert not any(f.fact_id.startswith("offer:") for f in facts), \
        [f.fact_id for f in facts if f.fact_id.startswith("offer:")]
    # The six items are individual item facts.
    assert [by_id[f"item:{idx}:name"].value for idx in range(6)] == [
        "Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar",
    ]


@pytest.mark.parametrize("offer_text", [
    "Buy 2 items - get 1 free and free chai",        # dash, not a colon menu list
    "Spend $20 on any items: get a free dessert and free chai",  # colon + benefit clause
    "Free Masala Chai with any purchase above $12",  # plain offer
])
def test_extract_text_facts_offer_benefit_clause_is_not_split_into_items(monkeypatch, offer_text):
    """Guard (Codex): an offer that merely contains 'items' + 'and' must NOT be
    mistaken for an 'items: a, b, c' menu list and split. It stays offer:0."""
    from agents.flyer import facts as facts_module
    from agents.flyer.semantic_brief import FlyerSemanticBrief, FlyerSemanticOffer

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(campaign_title="Promo", offers=[FlyerSemanticOffer(offer_text)])

    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: provider)
    facts = facts_module.extract_text_facts(
        FlyerRequestFields(), f"Create a flyer. {offer_text}.", profile_business_name="Lakshmi's Kitchen",
    )
    by_id = facts_module.facts_by_id(type("P", (), {"locked_facts": facts})())
    assert by_id["offer:0"].value == offer_text


def test_extract_text_facts_item_list_with_free_and_with_members_still_splits(monkeypatch):
    """Codex NIT: the benefit guard is per-member, so a clean menu where one dish
    contains 'free' and another contains 'with' is NOT cross-matched as an offer."""
    from agents.flyer import facts as facts_module
    from agents.flyer.semantic_brief import FlyerSemanticBrief, FlyerSemanticOffer

    def provider(_fields, _raw_request):
        return FlyerSemanticBrief(
            campaign_title="Breakfast",
            offers=[FlyerSemanticOffer("3 items: Gluten Free Dosa, Poori with Aloo, Plain Idli")],
        )

    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: provider)
    raw = "Create a flyer for Breakfast. 3 items: Gluten Free Dosa, Poori with Aloo, Plain Idli."
    facts = facts_module.extract_text_facts(FlyerRequestFields(), raw, profile_business_name="Lakshmi's Kitchen")
    by_id = facts_module.facts_by_id(type("P", (), {"locked_facts": facts})())
    assert not any(f.fact_id.startswith("offer:") for f in facts)
    assert [by_id[f"item:{idx}:name"].value for idx in range(3)] == [
        "Gluten Free Dosa", "Poori with Aloo", "Plain Idli",
    ]


def test_extract_text_facts_keeps_genuine_offer_not_an_item_list(monkeypatch):
    """Guard: a real offer that is NOT an 'items: a, b, c' menu list must still
    lock as offer:0 (the split must not swallow legitimate offers)."""
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
    assert by_id["offer:0"].value == "Free Masala Chai with any purchase above $12"


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


@pytest.mark.parametrize(
    ("price_text", "expected"),
    [
        ("75$", "$75"),
        ("75 $", "$75"),
        ("75 dollars", "$75"),
        ("$75", "$75"),
    ],
)
def test_item_price_facts_normalize_suffix_and_prefix_prices(price_text, expected):
    from agents.flyer.facts import _item_price_facts

    facts = _item_price_facts(f"Mango tresleches - half tray - {price_text}", message_id="m-dessert")
    by_id = {fact.fact_id: fact for fact in facts}

    assert by_id["item:0:name"].value == "Mango tresleches - half tray"
    assert by_id["item:0:price"].value == expected
    assert by_id["item:0:price"].source == "customer_text"
    assert by_id["item:0:price"].required is True


def test_extract_text_facts_locks_exact_dessert_graduation_suffix_price_pairs(monkeypatch):
    from agents.flyer import facts as facts_module

    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: None)

    facts = facts_module.extract_text_facts(
        FlyerRequestFields(notes=DESSERT_GRADUATION_SUFFIX_PRICE_BRIEF),
        DESSERT_GRADUATION_SUFFIX_PRICE_BRIEF,
        message_id="m-dessert-graduation",
    )
    by_id = facts_module.facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["campaign_title"].value == "Graduation Dessert Specials"

    expected = [
        ("Mango tresleches - half tray", "$75"),
        ("Rasmalai tresleches - half tray", "$70"),
        ("Apricot delight - half tray", "$80"),
        ("Butter scotch - half tray", "$75"),
        ("Strawberry pastry - half", "$70"),
        ("Chocolate pastry - half", "$70"),
        ("Gulab jamun - 100 count", "$80"),
        ("Gulabjamun fusion - half tray", "$75"),
        ("Kheer(Ramadan style) half tray", "$55"),
        ("Kadhu ki sheet - small tray", "$65"),
        ("Double ka meeta - small tray", "$45"),
        ("Kurbanika meeta - small tray", "$70"),
        ("Carrot halwa - small tray", "$55"),
        ("Khalakhandh - 100 count", "$100"),
    ]
    assert [by_id[f"item:{idx}:name"].value for idx in range(len(expected))] == [
        name for name, _price in expected
    ]
    assert [by_id[f"item:{idx}:price"].value for idx in range(len(expected))] == [
        price for _name, price in expected
    ]
    assert all(by_id[f"item:{idx}:name"].source == "customer_text" for idx in range(len(expected)))
    assert all(by_id[f"item:{idx}:price"].required is True for idx in range(len(expected)))


def test_extract_text_facts_keeps_final_suffix_price_when_intake_notes_duplicate_raw(monkeypatch):
    """Bare render passes raw WhatsApp text plus intake fields. The intake extractor
    collapses notes to one line; if facts.py appends that duplicate to raw, the
    raw final suffix-price line becomes a giant segment and the last item is lost."""
    from agents.flyer import facts as facts_module

    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: None)
    collapsed_notes = " ".join(DESSERT_GRADUATION_SUFFIX_PRICE_BRIEF.split())

    facts = facts_module.extract_text_facts(
        FlyerRequestFields(notes=collapsed_notes),
        DESSERT_GRADUATION_SUFFIX_PRICE_BRIEF,
        message_id="m-dessert-graduation",
    )
    by_id = facts_module.facts_by_id(type("P", (), {"locked_facts": facts})())

    assert by_id["item:13:name"].value == "Khalakhandh - 100 count"
    assert by_id["item:13:price"].value == "$100"


def test_extract_text_facts_no_price_thali_request_creates_no_price_facts(monkeypatch):
    from agents.flyer import facts as facts_module

    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: None)
    raw_request = (
        "Create a daily thali flyer for Lakshmi's Kitchen with rice, dal, curry, "
        "pickle, and weekend style. Use address and phone number stored."
    )

    facts = facts_module.extract_text_facts(FlyerRequestFields(notes=raw_request), raw_request, message_id="m-thali")

    assert [fact for fact in facts if re.match(r"^item:\d+:price$", fact.fact_id)] == []


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
    """Provenance producer invariant (current: creative_planner removed).

    creative_planner — the former sole sanctioned emitter of `hermes_inferred` —
    was removed in graduation commit 6. Current invariant:
      - `hermes_inferred` may be emitted by NO production module.
      - `customer_confirmed` may be emitted ONLY by facts.py (the relocated
        promote_inferred_to_confirmed provenance-lifecycle helper).
    Any other production emission of either source fails.

    AST-based (multiline-proof): flags a `_fact(...)` / `FlyerLockedFact(...)` call
    carrying a new-source string (positional or `source=` kwarg), a
    `source = "..."` assignment, or a fact-dict `{"source": "..."}`. The inert
    plumbing (Literal, allowlist set, priority dict) is not such a call/assignment,
    so it is naturally ignored."""
    import ast

    flyer_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "agents" / "flyer"
    EMIT_FUNCS = {"_fact", "FlyerLockedFact"}
    # source -> set of filenames permitted to emit it. No module may emit
    # hermes_inferred (creative_planner, its former sole emitter, was removed in
    # graduation commit 6); customer_confirmed's sole sanctioned emitter is the
    # relocated promote_inferred_to_confirmed in facts.py. The consuming script only
    # CALLS the promotion helper, it never sets the source.
    SANCTIONED = {
        # creative_planner removed (graduation commit 6): NOTHING may emit
        # hermes_inferred anymore; customer_confirmed's sole sanctioned emitter
        # is the relocated promote_inferred_to_confirmed in facts.py.
        "hermes_inferred": set(),
        "customer_confirmed": {"facts.py"},
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
    for must in ("create-flyer-project", "generate-flyer-concepts", "facts.py"):
        assert must in scanned_names, f"producer-guard did not scan {must} (scan scope regressed)"
    assert offenders == [], (
        "hermes_inferred may be emitted by NO production module (creative_planner "
        "removed, graduation commit 6); customer_confirmed only by facts.py; "
        f"found: {offenders}"
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


def test_hermes_inferred_source_never_outranks_customer_text():
    """Successor to the removed planner-materialization pin (graduation commit
    6): the PROPERTY that survives the producer is merge priority —
    hermes_inferred can never shadow a customer_text fact of the same id."""
    from agents.flyer.facts import merge_locked_facts
    from schemas import FlyerLockedFact

    customer = FlyerLockedFact(fact_id="item:0:name", label="I", value="Idli",
                               source="customer_text", required=True)
    inferred = FlyerLockedFact(fact_id="item:0:name", label="I", value="Hakka Noodles",
                               source="hermes_inferred", required=False)
    merged = merge_locked_facts([inferred, customer])
    winner = [f for f in merged if f.fact_id == "item:0:name"]
    assert len(winner) == 1 and winner[0].value == "Idli"
    assert winner[0].source == "customer_text"

def _items_map(facts):
    """{name: price} from item:N facts."""
    names, prices = {}, {}
    for f in facts:
        if f.fact_id.startswith("item:") and f.fact_id.endswith(":name"):
            names[f.fact_id.split(":")[1]] = f.value
        elif f.fact_id.startswith("item:") and f.fact_id.endswith(":price"):
            prices[f.fact_id.split(":")[1]] = f.value
    return {names[i]: prices.get(i) for i in names}


def test_item_price_facts_en_dash_pairs_correctly():
    from agents.flyer.facts import _item_price_facts
    txt = "Gulab Jamun – $7.99 Rasmalai Tres Leches – $9.99 Apricot Delight – $8.99"
    m = _items_map(_item_price_facts(txt, message_id="m"))
    assert m.get("Gulab Jamun") == "$7.99"
    assert m.get("Rasmalai Tres Leches") == "$9.99"
    assert m.get("Apricot Delight") == "$8.99"


def test_item_price_facts_em_dash_pairs_correctly():
    from agents.flyer.facts import _item_price_facts
    m = _items_map(_item_price_facts("Dosa — $6.99, Idli — $5.99", message_id="m"))
    assert m.get("Dosa") == "$6.99" and m.get("Idli") == "$5.99"


def test_no_price_phrase_does_not_become_priced_item():
    from agents.flyer.facts import _item_price_facts
    txt = "Gulab Jamun - $7.99 Rasmalai Tres Leches - $9.99 Apricot Delight - $8.99 Limited Weekend Special"
    m = _items_map(_item_price_facts(txt, message_id="m"))
    assert m.get("Gulab Jamun") == "$7.99"
    assert m.get("Rasmalai Tres Leches") == "$9.99"
    assert m.get("Apricot Delight") == "$8.99"
    assert "Limited Weekend Special" not in m
    assert all("$8.99" != p or n == "Apricot Delight" for n, p in m.items())


def test_price_first_brief_still_extracts_via_fallback():
    from agents.flyer.facts import _item_price_facts
    m = _items_map(_item_price_facts("$20 men haircut, $80 perms, $7 kids trim", message_id="m"))
    assert m.get("men haircut") == "$20" and m.get("perms") == "$80" and m.get("kids trim") == "$7"


def _lf(fid, value, source="customer_text", required=True):
    from schemas import FlyerLockedFact
    # hermes_inferred facts are advisory by schema rule (cannot be required).
    if source == "hermes_inferred":
        required = False
    return FlyerLockedFact(fact_id=fid, label="L", value=value, source=source, required=required)


def _ids(facts):
    return {f.fact_id: f.value for f in facts}


def test_reconcile_dessert_drops_duplicate_offers_keeps_items():
    from agents.flyer.facts import reconcile_priced_facts
    src = "Gulab Jamun - $7.99 Rasmalai Tres Leches - $9.99 Apricot Delight - $8.99 Limited Weekend Special"
    facts = [
        _lf("business_name", "Lakshmi's Kitchen"),
        _lf("offer:0", "Gulab Jamun - $7.99"),
        _lf("offer:1", "Rasmalai Tres Leches - $9.99"),
        _lf("offer:2", "Apricot Delight - $8.99"),
        _lf("item:0:name", "Gulab Jamun"), _lf("item:0:price", "$7.99"),
        _lf("item:1:name", "Rasmalai Tres Leches"), _lf("item:1:price", "$9.99"),
        _lf("item:2:name", "Apricot Delight"), _lf("item:2:price", "$8.99"),
    ]
    out = _ids(reconcile_priced_facts(facts, src))
    assert "offer:0" not in out and "offer:1" not in out and "offer:2" not in out
    assert out.get("item:0:name") == "Gulab Jamun" and out.get("item:0:price") == "$7.99"
    assert out.get("item:1:name") == "Rasmalai Tres Leches" and out.get("item:1:price") == "$9.99"
    assert out.get("item:2:name") == "Apricot Delight" and out.get("item:2:price") == "$8.99"
    assert out.get("business_name") == "Lakshmi's Kitchen"


def test_reconcile_combo_keeps_offer_drops_derived_item():
    from agents.flyer.facts import reconcile_priced_facts
    src = ("Veg Combo - $39.99: includes 2 veg curries and dessert. "
           "Non-Veg Combo - $49.99: includes 2 non-veg curries, biryani, dessert.")
    facts = [
        _lf("offer:0", "Veg Combo - $39.99: includes 2 veg curries and dessert"),
        _lf("offer:1", "Non-Veg Combo - $49.99: includes 2 non-veg curries, biryani, dessert"),
        _lf("item:0:name", "Non-Veg Combo Biryani"), _lf("item:0:price", "$12.99"),
    ]
    out = _ids(reconcile_priced_facts(facts, src))
    assert out.get("offer:0", "").startswith("Veg Combo")
    assert out.get("offer:1", "").startswith("Non-Veg Combo")
    assert "item:0:name" not in out and "item:0:price" not in out


def test_reconcile_flat_priced_items_survive():
    from agents.flyer.facts import reconcile_priced_facts
    src = "Any item $7.99. Idli, Dosa, Vada, Uttapam, Pongal, Sambar."
    facts = [
        _lf("pricing_structure", "Any item $7.99"),
        _lf("offer:0", "Idli, Dosa, Vada, Uttapam, Pongal, Sambar"),
        _lf("item:0:name", "Idli"), _lf("item:0:price", "$7.99"),
        _lf("item:1:name", "Dosa"), _lf("item:1:price", "$7.99"),
    ]
    out = _ids(reconcile_priced_facts(facts, src))
    assert out.get("pricing_structure") == "Any item $7.99"
    assert out.get("offer:0", "").startswith("Idli")
    assert out.get("item:0:name") == "Idli" and out.get("item:0:price") == "$7.99"
    assert out.get("item:1:name") == "Dosa" and out.get("item:1:price") == "$7.99"


def test_reconcile_never_mutates_prices_and_drops_conflict():
    from agents.flyer.facts import reconcile_priced_facts
    src = "Gulab Jamun - $7.99"
    facts = [
        _lf("item:0:name", "Gulab Jamun"), _lf("item:0:price", "$7.99"),
        _lf("item:1:name", "Gulab Jamun"), _lf("item:1:price", "$3.49"),
    ]
    out = _ids(reconcile_priced_facts(facts, src))
    assert out.get("item:0:price") == "$7.99"
    assert "$3.49" not in out.values()


def test_category_suffix_not_appended_to_combo_names():
    from agents.flyer.facts import _item_price_facts
    txt = "Veg Combo - $39.99 Non-Veg Combo - $49.99 with biryani"
    m = _items_map(_item_price_facts(txt, message_id="m"))
    assert "Veg Combo" in m and m["Veg Combo"] == "$39.99"
    assert "Non-Veg Combo" in m and m["Non-Veg Combo"] == "$49.99"
    assert "Veg Combo Biryani" not in m and "Non-Veg Combo Biryani" not in m


def test_reconcile_preserves_source_backed_name_only_items():
    from agents.flyer.facts import reconcile_priced_facts
    src = "Gluten Free Dosa, Poori with Aloo, Plain Idli"
    facts = [_lf("item:0:name", "Gluten Free Dosa"), _lf("item:1:name", "Poori with Aloo"), _lf("item:2:name", "Plain Idli")]
    out = _ids(reconcile_priced_facts(facts, src))
    assert out.get("item:0:name") == "Gluten Free Dosa"
    assert out.get("item:1:name") == "Poori with Aloo"
    assert out.get("item:2:name") == "Plain Idli"


def test_reconcile_keeps_name_only_items_regardless_of_source():
    # reconcile polices PRICED facts only; name-only items (incl. planner "famous"
    # expansions not literally in the brief) pass through untouched.
    from agents.flyer.facts import reconcile_priced_facts
    facts = [
        _lf("item:0:name", "Veg Manchurian"),
        _lf("item:1:name", "Gobi Manchurian"),
        _lf("item:2:name", "Plain Idli"),
    ]
    out = _ids(reconcile_priced_facts(facts, "Flyer, include 5 famous indo-chinese items"))
    assert out.get("item:0:name") == "Veg Manchurian"
    assert out.get("item:1:name") == "Gobi Manchurian"
    assert out.get("item:2:name") == "Plain Idli"


def test_reconcile_combo_live_shaped_drops_derived_items_keeps_rich_offers():
    from agents.flyer.facts import reconcile_priced_facts
    src = ("Veg Combo - $39.99: includes 2 veg curries and dessert. "
           "Non-Veg Combo - $49.99: includes 2 non-veg curries, biryani, dessert.")
    facts = [
        _lf("offer:0", "Veg Combo - $39.99: includes 2 veg curries and dessert"),
        _lf("offer:1", "Non-Veg Combo - $49.99: includes 2 non-veg curries, biryani, dessert"),
        _lf("item:0:name", "Veg Combo"), _lf("item:0:price", "$39.99"),
        _lf("item:1:name", "Non-Veg Combo"), _lf("item:1:price", "$49.99"),
        _lf("item:2:name", "Non-Veg Combo Biryani"), _lf("item:2:price", "$12.99"),
    ]
    out = _ids(reconcile_priced_facts(facts, src))
    assert out.get("offer:0", "").startswith("Veg Combo")
    assert out.get("offer:1", "").startswith("Non-Veg Combo")
    # ALL combo items suppressed (derived from rich priced offers / not source-backed)
    assert not any(k.startswith("item:") for k in out)


def test_extract_text_facts_dessert_end_to_end_reconciled():
    from agents.flyer.facts import extract_text_facts
    from schemas import FlyerRequestFields
    brief = ("Create a flyer for Festival Dessert Specials. Gulab Jamun – $7.99 "
             "Rasmalai Tres Leches – $9.99 Apricot Delight – $8.99 Limited Weekend Special. "
             "Available Friday through Sunday. Phone: +1 732-983-7841")
    fields = FlyerRequestFields(event_or_business_name="Lakshmi's Kitchen", preferred_language="en", notes=brief)
    facts = extract_text_facts(fields, brief, message_id="m", profile_business_name="Lakshmi's Kitchen", allow_text_identity=False)
    m = _items_map(facts)
    assert m.get("Gulab Jamun") == "$7.99"
    assert m.get("Rasmalai Tres Leches") == "$9.99"
    assert m.get("Apricot Delight") == "$8.99"
    assert "Limited Weekend Special" not in m
    simple_dups = [f for f in facts if f.fact_id.startswith("offer:") and "$" in f.value
                   and any(n.lower() in f.value.lower() for n in ("Gulab Jamun", "Rasmalai Tres Leches", "Apricot Delight"))]
    assert simple_dups == []


def test_reconcile_exempts_hermes_inferred_items():
    from agents.flyer.facts import reconcile_priced_facts
    # inferred items are NOT in the brief by design; reconcile must keep them.
    src = "Create a flyer for breakfast specials"
    facts = [
        _lf("item:0:name", "Idli", source="hermes_inferred"),
        _lf("item:0:price", "$8.99", source="hermes_inferred"),
        _lf("item:1:name", "Masala Dosa", source="hermes_inferred"),
    ]
    out = _ids(reconcile_priced_facts(facts, src))
    assert out.get("item:0:name") == "Idli" and out.get("item:0:price") == "$8.99"
    assert out.get("item:1:name") == "Masala Dosa"


def test_reconcile_keeps_alacarte_item_named_inside_a_combo_offer():
    from agents.flyer.facts import reconcile_priced_facts
    # "Dosa $7.99" is a real standalone priced item; "dosa" also appears as a
    # COMPONENT inside a combo offer. The standalone item must survive.
    src = "Family Platter - $49.99: includes 4 dosa, sambar. Dosa - $7.99."
    facts = [
        _lf("offer:0", "Family Platter - $49.99: includes 4 dosa, sambar"),
        _lf("item:0:name", "Dosa"), _lf("item:0:price", "$7.99"),
    ]
    out = _ids(reconcile_priced_facts(facts, src))
    assert out.get("offer:0", "").startswith("Family Platter")
    assert out.get("item:0:name") == "Dosa" and out.get("item:0:price") == "$7.99"


def test_reconcile_flat_price_from_source_when_no_pricing_structure_fact():
    # "any item $X" briefs attach the flat price per item but may emit NO
    # pricing_structure fact. reconcile must still recognize these as
    # source-backed (name in brief + price == the flat price stated in source).
    from agents.flyer.facts import reconcile_priced_facts
    src = ("Include Idlie, Medhu Vada, Kheema Dosa, Poori. "
           "Any item price is at $8.99. Saturday and Sunday only.")
    facts = [
        _lf("item:0:name", "Idlie"), _lf("item:0:price", "$8.99"),
        _lf("item:1:name", "Medhu Vada"), _lf("item:1:price", "$8.99"),
        _lf("item:2:name", "Kheema Dosa"), _lf("item:2:price", "$8.99"),
        _lf("item:3:name", "Poori"), _lf("item:3:price", "$8.99"),
    ]
    out = _items_map(reconcile_priced_facts(facts, src))
    assert set(out) == {"Idlie", "Medhu Vada", "Kheema Dosa", "Poori"}
    assert all(p == "$8.99" for p in out.values())


def test_reconcile_flat_price_from_source_still_drops_conflicting_price():
    # Safety: even with a source flat price, an item at a DIFFERENT (non-source)
    # price is NOT source-backed -> suppressed (never rewritten).
    from agents.flyer.facts import reconcile_priced_facts
    src = "Include Idlie, Poori. Any item price is at $8.99."
    facts = [
        _lf("item:0:name", "Idlie"), _lf("item:0:price", "$8.99"),
        _lf("item:1:name", "Poori"), _lf("item:1:price", "$14.50"),  # conflicting, not in source
    ]
    out = _items_map(reconcile_priced_facts(facts, src))
    assert "Idlie" in out
    assert "Poori" not in out  # conflicting price suppressed, not rewritten


def test_reconcile_flat_price_keeps_priced_expansion_items_not_in_brief():
    # "N famous items, any item $X": the system expands item NAMES not literally
    # in the brief, each at the flat price. The flat price applies to "any item",
    # so these priced items are source-backed by price (name need not be in brief).
    from agents.flyer.facts import reconcile_priced_facts
    src = ("Create a flyer for Indo-Chinese specials. "
           "Include 8 famous Indo-Chinese items. Any item priced at $9.99.")
    facts = [
        _lf("item:0:name", "Veg Manchurian"), _lf("item:0:price", "$9.99"),
        _lf("item:1:name", "Hakka Noodles"), _lf("item:1:price", "$9.99"),
        _lf("item:2:name", "Manchow Soup"), _lf("item:2:price", "$9.99"),
    ]
    out = _items_map(reconcile_priced_facts(facts, src))
    assert set(out) == {"Veg Manchurian", "Hakka Noodles", "Manchow Soup"}
    assert all(p == "$9.99" for p in out.values())


def test_reconcile_rule1_matches_offer_headline_not_priced_components():
    # A rich offer prices its components inline. A standalone item matching a
    # COMPONENT (not the offer's headline subject) must NOT be suppressed by rule (1).
    from agents.flyer.facts import reconcile_priced_facts
    src = ("Family Platter - $49.99: includes Samosa $2 and Chai $1. "
           "Samosa - $2.")
    facts = [
        _lf("offer:0", "Family Platter - $49.99: includes Samosa $2 and Chai $1"),
        _lf("item:0:name", "Samosa"), _lf("item:0:price", "$2"),
    ]
    out = _items_map(reconcile_priced_facts(facts, src))
    assert out.get("Samosa") == "$2"  # component-named standalone item survives


def test_reconcile_rule1_still_suppresses_combo_headline_duplicate():
    # Regression guard: a combo item duplicating the offer's HEADLINE subject is
    # still suppressed (offer canonical).
    from agents.flyer.facts import reconcile_priced_facts
    src = "Veg Combo - $39.99: Includes 2 curries and dessert."
    facts = [
        _lf("offer:0", "Veg Combo - $39.99: Includes 2 curries and dessert"),
        _lf("item:0:name", "Veg Combo"), _lf("item:0:price", "$39.99"),
    ]
    out = _items_map(reconcile_priced_facts(facts, src))
    assert "Veg Combo" not in out  # headline duplicate suppressed; offer canonical


def test_item_price_no_phantom_when_name_first_match_rejected():
    # A name-first pattern matches a stopword name ("any item") that add_item
    # rejects; the price_before_name fallback must NOT then bind the trailing
    # phrase ("Free Gift") as a phantom priced item.
    from agents.flyer.facts import _item_price_facts
    names = [f.value for f in _item_price_facts("any item $5 Free Gift", message_id="m")
             if f.fact_id.endswith(":name")]
    assert "Free Gift" not in names


def test_item_price_no_phantom_for_prompt_prefixed_flat_subject():
    # "Create a flyer with any item $5 Free Gift": add_item strips the prompt
    # prefix and recognizes "any item" as a flat-price subject; the fallback must
    # NOT then bind the trailing "Free Gift" as a phantom priced item.
    from agents.flyer.facts import _item_price_facts
    names = [f.value for f in _item_price_facts("Create a flyer with any item $5 Free Gift", message_id="m")
             if f.fact_id.endswith(":name")]
    assert "Free Gift" not in names


def test_item_price_no_phantom_when_duplicate_name_claims_segment():
    # A repeated real item ("Samosa $2" again) is a duplicate, not garbage; it
    # claims the segment's price, so the fallback must NOT mine the trailing
    # "Free Gift" as a phantom priced item.
    from agents.flyer.facts import _item_price_facts
    facts = _item_price_facts("Samosa $2, Samosa $2 Free Gift", message_id="m")
    names = [f.value for f in facts if f.fact_id.endswith(":name")]
    assert "Free Gift" not in names
    assert names.count("Samosa") == 1  # the duplicate is deduped, not re-added
