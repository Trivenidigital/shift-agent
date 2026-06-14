"""TDD tests for widened _integrated_poster_eligible eligibility (Slice 1 Architecture A).

All 8 cases: cases 1-4 FAIL against the current narrow function and PASS after the
TARGET function is applied; cases 5-8 are regression guards that pass both before and
after.

Run with:
    python -m pytest tests/test_flyer_integrated_eligibility.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

import agents.flyer.render as render_module  # noqa: E402
from schemas import (  # noqa: E402
    FlyerAsset,
    FlyerLockedFact,
    FlyerProject,
    FlyerReferenceExtraction,
    FlyerRequestFields,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 14, tzinfo=timezone.utc)


def _base_food_project(**overrides) -> FlyerProject:
    """Minimal English food project that passes every gate except the ones under test."""
    defaults = dict(
        project_id="F9900",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=_NOW,
        updated_at=_NOW,
        original_message_id="wamid.eligibility-test",
        raw_request="Dosa $6.99; Idli $5.99; Vada $4.99",
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="en",
            notes="Dosa $6.99; Idli $5.99; Vada $4.99",
        ),
        locked_facts=[
            FlyerLockedFact(
                fact_id="business_name",
                label="Business",
                value="Lakshmi's Kitchen",
                source="customer_profile",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="contact_phone",
                label="Contact",
                value="+17329837841",
                source="customer_profile",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="location",
                label="Location",
                value="90 Brybar Dr St Johns FL",
                source="customer_profile",
                required=True,
            ),
        ],
    )
    defaults.update(overrides)
    return FlyerProject(**defaults)


# ---------------------------------------------------------------------------
# Case 1: English food project with 8 items → eligible (True)
# ---------------------------------------------------------------------------

def test_case1_english_food_8_items_eligible(monkeypatch):
    """Widened: item count no longer caps eligibility (removed >10 gate)."""
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    notes = (
        "Dosa $6.99; Idli $5.99; Vada $4.99; Sambar Rice $7.99; "
        "Pongal $5.99; Upma $4.99; Curd Rice $5.49; Lemon Rice $5.49"
    )
    project = _base_food_project(
        raw_request=notes,
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="en",
            notes=notes,
        ),
    )
    assert render_module._integrated_poster_eligible(project) is True


# ---------------------------------------------------------------------------
# Case 2: English food project with 16 items (dense) → eligible (True)
# ---------------------------------------------------------------------------

def test_case2_english_food_dense_menu_eligible(monkeypatch):
    """Widened: dense menus (>10 and >12) no longer excluded.

    Uses locked item:N:name facts (non-hermes_inferred source) so
    _compact_menu_overlay_allowed returns True — same pattern as the
    production dessert graduation project. This is the only way to get
    >10 items past _detail_clauses without raising FlyerRenderError.
    """
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    # 16 menu items locked as structured facts (customer_text source, not hermes_inferred)
    item_names = [
        "Dosa", "Idli", "Vada", "Sambar Rice", "Pongal",
        "Upma", "Curd Rice", "Lemon Rice", "Poha", "Pesarattu",
        "Medu Vada", "Rava Dosa", "Onion Uttapam", "Masala Dosa",
        "Set Dosa", "Mysore Masala Dosa",
    ]
    item_facts = []
    for idx, name in enumerate(item_names):
        item_facts.append(FlyerLockedFact(
            fact_id=f"item:{idx}:name",
            label="Item",
            value=name,
            source="customer_text",
            required=True,
        ))
        item_facts.append(FlyerLockedFact(
            fact_id=f"item:{idx}:price",
            label="Price",
            value=f"${idx + 5}.99",
            source="customer_text",
            required=True,
        ))
    project = _base_food_project(
        raw_request="Lakshmi's Kitchen weekend menu",
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="en",
            notes="-",
        ),
        locked_facts=[
            FlyerLockedFact(
                fact_id="business_name",
                label="Business",
                value="Lakshmi's Kitchen",
                source="customer_profile",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="contact_phone",
                label="Contact",
                value="+17329837841",
                source="customer_profile",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="location",
                label="Location",
                value="90 Brybar Dr St Johns FL",
                source="customer_profile",
                required=True,
            ),
            *item_facts,
        ],
    )
    # Sanity-check the fixture has >10 items and compact mode is enabled
    items = render_module._menu_item_lines(project)
    assert len(items) == 16, f"expected 16 items, got {len(items)}"
    assert render_module._compact_menu_overlay_allowed(project, items) is True
    assert render_module._integrated_poster_eligible(project) is True


# ---------------------------------------------------------------------------
# Case 3: Telugu/regional-script food project → eligible (True)
# ---------------------------------------------------------------------------

def test_case3_telugu_food_project_eligible(monkeypatch):
    """Widened: language/regional-script exclusion removed; referee catches glyph failures."""
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    project = _base_food_project(
        raw_request="Lakshmi's Kitchen specials. Use Telugu language. Dosa $6.99",
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="te",
            notes="Dosa $6.99; Idli $5.99; Use Telugu language.",
        ),
    )
    # Sanity: confirm the language is non-English so we know the old gate would have excluded it
    assert (project.fields.preferred_language or "en").strip().lower() != "en"
    assert render_module._integrated_poster_eligible(project) is True


# ---------------------------------------------------------------------------
# Case 4: Reference-menu project with materialized facts → eligible (True)
# ---------------------------------------------------------------------------

def test_case4_reference_menu_materialized_facts_eligible(monkeypatch):
    """Widened: reference-menu (style-only + materialized facts) no longer excluded.

    _reference_menu is True only when:
      - _style_only_reference_requested is True  (request contains "use as reference")
      - _has_materialized_reference_menu_facts is True (locked_facts with reference_ source
        or reference_extractions with status=ok containing menu facts)

    In this state the items are already in locked_facts — the overlay CAN render them,
    and the integrated path has them in the prompt too. No extraction-pending risk.
    """
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    # _style_only_reference_requested requires one of the marker phrases
    raw = "Same as the attached flyer, use as reference, Lakshmi's Kitchen theme."
    locked_facts = [
        FlyerLockedFact(
            fact_id="business_name",
            label="Business",
            value="Lakshmi's Kitchen",
            source="customer_profile",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="contact_phone",
            label="Contact",
            value="+17329837841",
            source="customer_profile",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="location",
            label="Location",
            value="90 Brybar Dr St Johns FL",
            source="customer_profile",
            required=True,
        ),
        # Materialized reference menu fact — source starts with "reference_"
        FlyerLockedFact(
            fact_id="pricing_structure",
            label="Pricing",
            value="Any 2 Snacks $9.99",
            source="reference_vision",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="item:0:name",
            label="Item",
            value="Punugulu",
            source="reference_vision",
            required=True,
        ),
        FlyerLockedFact(
            fact_id="item:1:name",
            label="Item",
            value="Egg Bonda",
            source="reference_vision",
            required=True,
        ),
    ]
    project = _base_food_project(
        raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="en",
            notes="use as reference",
            style_preference="Lakshmi's Kitchen theme",
        ),
        locked_facts=locked_facts,
    )
    # Sanity-check the predicates this case depends on
    assert render_module._style_only_reference_requested(project) is True
    assert render_module._has_materialized_reference_menu_facts(project) is True
    # No reference_image ASSET so has_reference_image is False — avoids the
    # "raw reference image without materialized facts" exclusion path.
    assert render_module._integrated_poster_eligible(project) is True


# ---------------------------------------------------------------------------
# Case 5: Source-edit project → NOT eligible (False)
# ---------------------------------------------------------------------------

def test_case5_source_edit_excluded(monkeypatch):
    """Source-edit stays excluded — _is_source_edit_project gate remains."""
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    project = _base_food_project(
        raw_request="edit uploaded flyer/source artwork - change the price to $8.99",
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="en",
            notes="Dosa $8.99",
        ),
    )
    assert render_module._is_source_edit_project(project) is True
    assert render_module._integrated_poster_eligible(project) is False


# ---------------------------------------------------------------------------
# Case 6: Reference-extraction-pending project → NOT eligible (False)
# ---------------------------------------------------------------------------

def test_case6_reference_extraction_pending_excluded(monkeypatch, tmp_path):
    """Reference-extraction-pending stays excluded — _needs_reference_extraction gate remains.

    _needs_reference_extraction is True when:
      - a reference_image asset exists (with a path that exists on disk)
      - AND _request_asks_reference_extraction is True (request contains extraction keywords)
      - AND _has_materialized_reference_menu_facts is False
    """
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))

    # Create a real file for the asset path validator
    ref_img = tmp_path / "assets" / "ref.png"
    ref_img.parent.mkdir(parents=True, exist_ok=True)
    ref_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    reference_asset = FlyerAsset(
        asset_id="A0001",
        kind="reference_image",
        source="whatsapp",
        path=str(ref_img),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="wamid.ref",
        received_at=_NOW,
    )
    raw = "Create a flyer and extract items and prices from this sample flyer."
    project = _base_food_project(
        raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="en",
            notes=raw,
        ),
        assets=[reference_asset],
    )
    # Sanity: the predicate that drives this exclusion is True
    assert render_module._needs_reference_extraction(project) is True
    assert render_module._integrated_poster_eligible(project) is False


# ---------------------------------------------------------------------------
# Case 7: Non-food project → NOT eligible (False)
# ---------------------------------------------------------------------------

def test_case7_non_food_excluded(monkeypatch):
    """Non-food stays excluded — _is_food_or_grocery_project gate remains."""
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    project = _base_food_project(
        raw_request="Grand opening haircut promotion",
        fields=FlyerRequestFields(
            event_or_business_name="Glamour Salon",
            contact_info="+17329837841",
            venue_or_location="123 Main St",
            preferred_language="en",
            notes="Haircut $25; Blowdry $35; Perm $80",
        ),
        locked_facts=[
            FlyerLockedFact(
                fact_id="business_name",
                label="Business",
                value="Glamour Salon",
                source="customer_profile",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="contact_phone",
                label="Contact",
                value="+17329837841",
                source="customer_profile",
                required=True,
            ),
        ],
    )
    assert render_module._is_food_or_grocery_project(project) is False
    assert render_module._integrated_poster_eligible(project) is False


# ---------------------------------------------------------------------------
# Case 8: Flag off → NOT eligible (False)
# ---------------------------------------------------------------------------

def test_case8_flag_off_excluded(monkeypatch):
    """Without FLYER_ALLOW_INTEGRATED_POSTER=1 the function always returns False."""
    monkeypatch.delenv("FLYER_ALLOW_INTEGRATED_POSTER", raising=False)
    project = _base_food_project()
    assert render_module._integrated_poster_eligible(project) is False


# ---------------------------------------------------------------------------
# FIX A: integrated prompt honors regional-language CONTENT
# ---------------------------------------------------------------------------

def test_integrated_telugu_content_prompt_uses_regional_instruction(monkeypatch):
    """An integrated-eligible project whose CONTENT is actual Telugu script must
    get a RENDER-FAITHFULLY regional instruction in its prompt — NOT the
    English-only line, and NOT the background-only "do NOT render any text /
    composited separately" wording (the model renders the text on the integrated
    path, so suppressing it would ship a textless Telugu flyer).

    Branching on regional-script-in-content (not preferred_language) is what
    keeps the English-content-with-te-profile case English while flipping the
    genuinely-Telugu-content case to the regional instruction.
    """
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    # Real Telugu script in the menu facts: "దోస" (dosa), "ఇడ్లీ" (idli)
    project = _base_food_project(
        raw_request="లక్ష్మీ కిచెన్ మెనూ - దోస $6.99; ఇడ్లీ $5.99",
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="te",
            notes="దోస $6.99; ఇడ్లీ $5.99",
        ),
        locked_facts=[
            FlyerLockedFact(
                fact_id="business_name",
                label="Business",
                value="Lakshmi's Kitchen",
                source="customer_profile",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="contact_phone",
                label="Contact",
                value="+17329837841",
                source="customer_profile",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="location",
                label="Location",
                value="90 Brybar Dr St Johns FL",
                source="customer_profile",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="item:0:name",
                label="Item",
                value="దోస",
                source="customer_text",
                required=True,
            ),
        ],
    )
    # Sanity: this project is integrated-eligible AND its content has regional script
    assert render_module._integrated_poster_eligible(project) is True
    assert render_module._has_regional_script(project.raw_request) is True

    prompt = render_module._image_prompt(
        project, concept_id="C1", output_format="concept_preview", size=(1080, 1350)
    )

    # Must NOT be the English-only instruction.
    assert "Use English text only" not in prompt
    # Must NOT be the background-only suppression wording (the integrated model
    # renders the text — suppressing it would ship a textless Telugu flyer).
    assert "do NOT render any text" not in prompt
    assert "composited separately" not in prompt
    # Must instruct rendering Telugu faithfully.
    assert "Telugu" in prompt
    assert (
        "primary flyer language" in prompt
        or "valid Telugu script" in prompt
        or "faithfully" in prompt
    )


# ---------------------------------------------------------------------------
# FIX B: eligibility never raises on a dense plain-notes project
# ---------------------------------------------------------------------------

def test_dense_plain_notes_project_is_ineligible_and_does_not_raise(monkeypatch):
    """A dense (>10 items) PLAIN-NOTES project (no structured item:N facts) used to
    overflow _detail_clauses' MAX_DETAIL_FACTS cap and raise FlyerRenderError from
    inside _integrated_poster_eligible. The predicate must instead return False
    (falls back to background-only) and never throw.
    """
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    notes_16 = (
        "Dosa $6.99; Idli $5.99; Vada $4.99; Sambar Rice $7.99; Pongal $5.99; "
        "Upma $4.99; Curd Rice $5.49; Lemon Rice $5.49; Poha $4.99; Pesarattu $5.49; "
        "Medu Vada $4.49; Rava Dosa $7.99; Onion Uttapam $7.49; Masala Dosa $7.99; "
        "Set Dosa $6.49; Mysore Masala Dosa $8.49"
    )
    project = _base_food_project(
        raw_request=notes_16,
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen",
            contact_info="+17329837841",
            venue_or_location="90 Brybar Dr St Johns FL",
            preferred_language="en",
            notes=notes_16,
        ),
    )
    # Sanity: items come from plain notes only (no structured item:N facts), >10,
    # and compact overlay is NOT allowed (so _detail_clauses would raise).
    items = render_module._menu_item_lines(project)
    assert len(items) >= 16
    assert render_module._compact_menu_overlay_allowed(project, items) is False

    # Must return False cleanly — not raise.
    assert render_module._integrated_poster_eligible(project) is False
