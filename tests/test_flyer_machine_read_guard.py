"""TDD tests for machine-read fact guard in _integrated_poster_eligible (Task 7).

Machine-read elements (QR codes, barcodes) must be pixel-exact to scan — an
AI model cannot render a scannable QR.  Guard rule: any project carrying a
machine-read fact MUST be ineligible for the integrated path.

No QR fact type exists in the schema today (YAGNI); this is a forward safety
guard only.

Run with:
    python -m pytest tests/test_flyer_machine_read_guard.py -v
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
    FlyerLockedFact,
    FlyerProject,
    FlyerRequestFields,
)

# ---------------------------------------------------------------------------
# Shared fixture helper — mirrors test_flyer_integrated_eligibility._base_food_project
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 14, tzinfo=timezone.utc)


def _base_food_project(**overrides) -> FlyerProject:
    """Minimal English food project that is integrated-eligible by default."""
    defaults = dict(
        project_id="F9901",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=_NOW,
        updated_at=_NOW,
        original_message_id="wamid.mr-guard-test",
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
# Sanity: baseline eligible project is eligible (no machine-read facts)
# ---------------------------------------------------------------------------

def test_baseline_food_project_eligible(monkeypatch):
    """An ordinary English food project with no machine-read facts remains eligible."""
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    project = _base_food_project()
    assert render_module._integrated_poster_eligible(project) is True


# ---------------------------------------------------------------------------
# Main guard: adding a qr_code fact makes the project ineligible
# ---------------------------------------------------------------------------

def test_machine_read_fact_forces_ineligible(monkeypatch):
    """A project that IS integrated-eligible becomes ineligible when a machine-read
    fact (fact_id='qr_code') is present — QR codes must be composited
    deterministically; the image model cannot render a scannable QR.
    """
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")

    eligible_project = _base_food_project()

    # Sanity: without a QR fact the project is eligible
    assert render_module._integrated_poster_eligible(eligible_project) is True

    # Add a machine-read fact
    eligible_project.locked_facts.append(
        FlyerLockedFact(
            fact_id="qr_code",
            label="QR",
            value="https://pay.example/abc",
            source="customer_profile",
            required=True,
        )
    )

    # Now it must be ineligible
    assert render_module._integrated_poster_eligible(eligible_project) is False


# ---------------------------------------------------------------------------
# Guard covers all three recognised machine-read fact_id variants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fact_id", ["qr", "qr_code", "barcode"])
def test_all_machine_read_fact_ids_force_ineligible(monkeypatch, fact_id):
    """fact_id values 'qr', 'qr_code', and 'barcode' all trigger the guard."""
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")

    project = _base_food_project()
    project.locked_facts.append(
        FlyerLockedFact(
            fact_id=fact_id,
            label="Machine-read",
            value="https://pay.example/123",
            source="customer_profile",
            required=True,
        )
    )
    assert render_module._integrated_poster_eligible(project) is False


# ---------------------------------------------------------------------------
# Guard is case-insensitive (QR_CODE, Barcode, etc.)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fact_id", ["QR_CODE", "QR", "Barcode", "BARCODE"])
def test_machine_read_guard_is_case_insensitive(monkeypatch, fact_id):
    """The guard normalises fact_id to lowercase before matching."""
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")

    project = _base_food_project()
    project.locked_facts.append(
        FlyerLockedFact(
            fact_id=fact_id,
            label="Machine-read",
            value="scan-me",
            source="customer_profile",
            required=True,
        )
    )
    assert render_module._integrated_poster_eligible(project) is False
