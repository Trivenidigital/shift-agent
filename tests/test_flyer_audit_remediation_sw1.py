"""SW-1 wrong-brand remediation (E2E audit 2026-07-13).

SW-1b: the visual-QA masthead backstop must fail closed on an unexplained,
name-shaped line even when it lacks an English org-suffix word — while still
passing legitimate promotional / occasion copy.

SW-1a: a "make it look like this" upload (template / reference_image) is treated
as style-only by default, so a competitor flyer uploaded as a "theme" is not
handed to the model as the identity source.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.flyer.render import _style_only_reference_requested
from agents.flyer.semantic_brief import visible_wrong_brand_blockers
from schemas import FlyerProject


def _project(**updates):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    base = {
        "project_id": "F9001",
        "status": "intake_started",
        "customer_phone": "+17329837841",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "m-1",
        "raw_request": "weekend flyer",
        "fields": {
            "event_or_business_name": "Lakshmis Kitchen",
            "venue_or_location": "90 Brybar Dr St Johns FL",
            "contact_info": "+17329837841",
            "notes": "",
        },
        "locked_facts": [
            {
                "fact_id": "business_name",
                "label": "Business",
                "value": "Lakshmis Kitchen",
                "source": "customer_profile",
                "required": True,
            }
        ],
    }
    base.update(updates)
    return FlyerProject.model_validate(base)


# ---------------------------------------------------------------- SW-1b QA backstop

# The suffix-less masthead backstop only fires when an external reference was
# ingested (the wrong-brand vector). Inject one so the competitor-name tests run
# in the threat context.
_WITH_REFERENCE = {"reference_extractions": [{"asset_id": "A0001", "role": "inspiration"}]}


@pytest.mark.parametrize(
    "masthead",
    ["Saravana Bhavan", "SARAVANA BHAVAN", "Paradise Biryani", "Adyar Ananda Bhavan"],
)
def test_sw1b_blocks_unexplained_competitor_masthead_when_reference_ingested(masthead):
    project = _project(**_WITH_REFERENCE)
    blockers = visible_wrong_brand_blockers(project, masthead)
    assert blockers, f"expected a wrong-brand blocker for {masthead!r}, got none"


def test_sw1b_org_suffix_blocks_even_without_reference():
    # Org-suffix mastheads always block (pre-existing behavior, ungated).
    assert visible_wrong_brand_blockers(_project(), "Bombay Kitchen")


@pytest.mark.parametrize(
    "tagline",
    ["Pure Veg", "PURE VEG", "Home Delivery", "Taste Of India", "Authentic Flavors",
     "Family Recipes", "Fine Dining", "Order Online", "Dine In"],
)
def test_sw1b_pure_text_brief_never_blocks_owner_taglines(tagline):
    # CRITICAL FP fix (adversarial review 2026-07-13): with NO ingested reference,
    # a 2-3 word owner tagline must never be read as a competitor and hard-stop the
    # flyer. This is the near-universal Indian-restaurant flyer copy surface.
    project = _project()  # pure text, no reference
    blockers = visible_wrong_brand_blockers(project, tagline)
    assert blockers == [], f"pure-text brief wrongly blocked {tagline!r}: {blockers}"


@pytest.mark.parametrize(
    "line",
    ["Weekend Special", "Grand Opening", "Family Combo Feast", "Diwali Sale",
     "Fresh Daily Breakfast", "Lakshmis Kitchen"],
)
def test_sw1b_allows_legit_promo_and_owner_identity_even_with_reference(line):
    # Even in the threat context (reference ingested), promo/occasion copy and the
    # owner's own identity must pass via the offer-vocab escape + exclusions.
    project = _project(**_WITH_REFERENCE)
    blockers = visible_wrong_brand_blockers(project, line)
    assert blockers == [], f"did not expect a blocker for {line!r}, got {blockers}"


def test_sw1b_still_passes_when_line_is_the_campaign_title():
    # The exact campaign title must never be treated as a wrong brand.
    project = _project(
        locked_facts=[
            {"fact_id": "business_name", "label": "Business", "value": "Lakshmis Kitchen",
             "source": "customer_profile", "required": True},
            {"fact_id": "campaign_title", "label": "Headline", "value": "Paradise Nights",
             "source": "customer_text", "required": True},
        ],
    )
    assert visible_wrong_brand_blockers(project, "Paradise Nights") == []


# ---------------------------------------------------------------- SW-1a ingest default

def _reference_image_asset(state_root: Path, kind: str = "reference_image"):
    p = Path(state_root) / "flyer" / "ref.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG")
    return {
        "asset_id": "A0001",
        "kind": kind,
        "source": "uploaded",
        "path": str(p),
        "mime_type": "image/png",
        "sha256": "0" * 64,
        "original_message_id": "m-1",
        "received_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
    }


def test_sw1a_reference_upload_defaults_to_style_only(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _project(assets=[_reference_image_asset(tmp_path)])
    assert _style_only_reference_requested(project) is True


def test_sw1a_owner_confirmation_preserves_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _project(
        raw_request="here is my logo, use it on the flyer",
        assets=[_reference_image_asset(tmp_path)],
    )
    assert _style_only_reference_requested(project) is False


def test_sw1a_pure_text_brief_unaffected(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    project = _project(raw_request="weekend idli dosa special flyer")
    assert _style_only_reference_requested(project) is False


def test_sw1a_existing_marker_still_style_only():
    project = _project(raw_request="use as reference only, do not copy the source flyer branding")
    assert _style_only_reference_requested(project) is True
