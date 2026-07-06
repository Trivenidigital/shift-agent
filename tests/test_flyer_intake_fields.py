"""Deterministic intake field extraction (`intake_fields._extract_fields`).

Regression cover for incident #537: the deterministic fallback extractor
(v2 owns new-brief extraction, but this path is still reached) over-captured a
whole multi-line offer brief into `event_or_business_name`, exceeding the
FlyerRequestFields schema max (160) and raising ValidationError before the
project was ever created. The fix drops implausibly-long names (> 100 chars) so
customer-identity hydration fills the registered business name, and clamps every
string field to its schema maximum as a last-line defense-in-depth guard.

Pure-function + PIL-independent; flyer-named (excluded from send-path-ci, listed
in flyer-premium-ci).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("pydantic")

from agents.flyer.intake_fields import _clamp_string_field, _extract_fields  # noqa: E402


def _now():
    return datetime.now(timezone.utc)


# The exact brief shape from the 2026-06-09 production incident (#537): a
# multi-line offer with no explicit business name. The old extractor captured
# 160+ chars as the name and FlyerRequestFields(...) raised ValidationError.
INCIDENT_BRIEF = (
    "Flyer for sale event as part of anniversary sale starting 6/7-6/9\n"
    "Please include the below\n"
    "Any purchase on entire store 10% off\n"
    "Customers billed over 100$ eligible for lucky draw"
)


def test_over_long_capture_does_not_crash():
    # Before the fix this raised pydantic.ValidationError (string_too_long).
    fields = _extract_fields(INCIDENT_BRIEF, now=_now())
    # The over-long capture is dropped so identity hydration fills the name.
    assert fields.event_or_business_name is None or len(fields.event_or_business_name) < 100
    # The offer content survives in notes for downstream fact extraction.
    assert "10% off" in fields.notes


def test_over_long_venue_is_clamped_to_schema_max():
    # A future producer bug that yields an over-long venue must degrade to a
    # clamped value, never a hard crash.
    brief = "Flyer for our restaurant\nLocation: " + ("x" * 500)
    fields = _extract_fields(brief, now=_now())
    if fields.venue_or_location is not None:
        assert len(fields.venue_or_location) <= 240


def test_normal_brief_still_extracts_a_name():
    brief = ("Create a flyer for Lakshmi's Kitchen\n"
             "Special: Biryani Buffet on Saturday 6pm\nContact: 555-1234")
    fields = _extract_fields(brief, now=_now())
    assert fields.event_or_business_name
    name = fields.event_or_business_name
    assert "Lakshmi" in name or "Kitchen" in name


def test_brief_without_business_name_does_not_crash():
    fields = _extract_fields("Can you suggest me some flyer ideas", now=_now())
    assert fields.event_or_business_name is None or len(fields.event_or_business_name) < 100


def test_clamp_string_field_unit():
    assert _clamp_string_field(None, 160) is None
    assert _clamp_string_field("", 160) == ""
    assert _clamp_string_field("short", 160) == "short"
    long = "y" * 300
    assert _clamp_string_field(long, 160) == "y" * 160
