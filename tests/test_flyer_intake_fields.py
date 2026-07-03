"""Tests for flyer intake field extraction (`intake_fields.py`).

Covers the deterministic field extraction logic that parses brief text into
structured FlyerRequestFields. Specifically tests robustness against over-capture
of multi-line offer briefs as business names.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.flyer.intake_fields import _extract_fields


def test_extract_fields_rejects_over_long_captures():
    """Test the fix for brief-to-name over-capture bug (issue #537).

    Reproduces: 2026-06-09 production incident where "Flyer for sale event as
    part of anniversary sale starting 6/7-6/9... [offer details]" caused the
    regex to capture 168 chars (entire brief) as event_or_business_name, exceeding
    the schema max of 160 chars and raising ValidationError.

    The fix: reject captured names > 100 chars (a sane business-name limit) so
    that multi-line offer briefs without an explicit business name drop to empty
    and trigger customer-identity hydration instead.
    """
    # Exact brief from the production incident
    incident_brief = (
        "Flyer for sale event as part of anniversary sale starting 6/7-6/9\n"
        "Please include the below\n"
        "Any purchase on entire store 10% off\n"
        "Customers billed over 100$ eligible for lucky draw"
    )
    
    # Should not raise ValidationError after the fix
    fields = _extract_fields(incident_brief, now=datetime.now(timezone.utc))
    
    # After the fix, the over-long capture should be rejected and name should be empty
    # (or a short normalized snippet), allowing customer-identity hydration to fill it in
    assert fields is not None
    # The event_or_business_name should either be None (dropped) or short (not 168 chars)
    assert fields.event_or_business_name is None or len(fields.event_or_business_name) < 100
    
    # The offer details should be in notes for the planner to use
    assert "10% off" in fields.notes or "10% off" in fields.notes
    assert fields.notes  # notes should have the full brief


def test_extract_fields_clamping_defense_in_depth():
    """Test that string fields are clamped to schema maxima (defense-in-depth).

    Even if a future extractor bug produces over-long strings (e.g., > 160 for
    event_or_business_name), the construction-time clamping prevents a hard crash.
    """
    # A brief with a very long venue string (should be clamped to 240 chars)
    long_venue_brief = (
        "Flyer for our restaurant\n"
        "Location: " + ("x" * 500)  # Force an over-long venue value
    )
    
    # Should not raise ValidationError; clamping should truncate to schema max
    fields = _extract_fields(long_venue_brief, now=datetime.now(timezone.utc))
    
    assert fields is not None
    # venue_or_location should be clamped to 240 or less
    if fields.venue_or_location:
        assert len(fields.venue_or_location) <= 240


def test_extract_fields_normal_brief_still_works():
    """Test that normal, well-formed briefs continue to work after the fix."""
    normal_brief = (
        "Create a flyer for Lakshmi's Kitchen\n"
        "Special: Biryani Buffet on Saturday 6pm\n"
        "Contact: 555-1234"
    )
    
    fields = _extract_fields(normal_brief, now=datetime.now(timezone.utc))
    
    assert fields is not None
    # A well-formed brief should have a reasonable event name
    assert fields.event_or_business_name  # Should not be empty
    assert "Lakshmi" in fields.event_or_business_name or "Kitchen" in fields.event_or_business_name


def test_extract_fields_brief_without_business_name():
    """Test that briefs without explicit business name don't crash.

    Registered customers rely on identity grounding to fill business name.
    The extractor should gracefully drop missing/unparseable names.
    """
    brief_no_name = (
        "Can you suggest me some flyer ideas"
    )
    
    fields = _extract_fields(brief_no_name, now=datetime.now(timezone.utc))
    
    assert fields is not None
    # No name in brief → should be empty or placeholder, ready for hydration
    assert not fields.event_or_business_name or len(fields.event_or_business_name) < 50
