"""IN-4 — deterministic festival -> occasion on the legacy extraction path
(E2E audit 2026-07-13)."""
from __future__ import annotations

import pytest

from agents.flyer.extraction_seam import (
    _derive_deterministic_occasion,
    extract_text_facts_seam,
)
from schemas import FlyerRequestFields


@pytest.mark.parametrize(
    "brief,expected",
    [
        ("diwali dinner special flyer for saturday", "diwali"),
        ("deepavali sweets flyer", "diwali"),
        ("ramzan iftar special", "ramadan"),
        ("eid special menu flyer", "ramadan"),
        ("thanksgiving turkey special", "thanksgiving"),
        ("july 4th bbq flyer", "july4"),
        ("4th of july cookout", "july4"),
        # generic / ambiguous -> none
        ("weekend breakfast special flyer", "none"),
        ("grand opening celebration", "none"),
        ("independence day sale", "none"),  # Aug 15 for an Indian SMB, not July 4
    ],
)
def test_in4_derive_occasion(brief, expected):
    assert _derive_deterministic_occasion(brief) == expected, brief


def test_in4_seam_sets_occasion_on_legacy_path():
    sink = {}
    extract_text_facts_seam(
        FlyerRequestFields(), "diwali dinner special flyer", report_out=sink)
    assert sink.get("occasion") == "diwali"


def test_in4_seam_leaves_none_for_generic_brief():
    sink = {}
    extract_text_facts_seam(
        FlyerRequestFields(), "weekend combo flyer", report_out=sink)
    assert sink.get("occasion") == "none"
