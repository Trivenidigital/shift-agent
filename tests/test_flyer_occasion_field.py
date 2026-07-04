"""Graduation commit 3 — interpretive occasion field (schema ruling 2026-07-04).

Structural-exemption contracts pinned:
- occasion is a SEPARATE project enum field, default "none"; old store rows
  (no key) validate untouched.
- Extraction detects it interpretively: enum-constrained, lowercased before
  lookup, unknown/ambiguous -> "none" (fail-neutral — guessing is
  unrepresentable). Never counted in items_locked/scalars_locked, never
  parity-dropped.
- The seam exposes it via a non-breaking report_out sink (facts return shape
  unchanged).
- Render: project.occasion composes the occasion theme + extends the ban list;
  "none" composes the pure register.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agents.flyer.extraction_seam import extract_text_facts_seam
from agents.flyer.extraction_v2 import extract_text_facts_v2
from agents.flyer.render import build_image_generation_prompt
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields

PHONE = "+17329837841"


def _F(fid, value):
    return FlyerLockedFact(fact_id=fid, label=fid, value=value,
                           source="customer_text", required=True)


def _fake_transport(payload: dict):
    return lambda s, u: json.dumps(payload)


BASE_PAYLOAD = {
    "business_name": None, "campaign_title": "Weekend Special",
    "pricing_structure": "Any tiffin $6.99", "schedule": "Friday to Sunday",
    "location": None, "contact_phone": None,
    "items": [{"name": "Idli", "price": None}],
}
BRIEF = "Create a flyer for Weekend Special. Any tiffin $6.99. Idli. Friday to Sunday."


def test_occasion_detected_and_reported():
    facts, report = extract_text_facts_v2(
        FlyerRequestFields(), BRIEF + " July 4th deal.",
        transport=_fake_transport({**BASE_PAYLOAD, "occasion": "july4"}))
    assert report.occasion == "july4"
    assert not any(f.fact_id == "occasion" for f in facts)  # never a fact
    assert report.scalars_locked == 3  # occasion NOT counted


def test_occasion_lowercased_before_lookup():
    _f, report = extract_text_facts_v2(
        FlyerRequestFields(), BRIEF,
        transport=_fake_transport({**BASE_PAYLOAD, "occasion": "Diwali"}))
    assert report.occasion == "diwali"


def test_unknown_and_missing_occasion_fail_neutral():
    _f, r1 = extract_text_facts_v2(
        FlyerRequestFields(), BRIEF,
        transport=_fake_transport({**BASE_PAYLOAD, "occasion": "christmas"}))
    assert r1.occasion == "none"
    _f, r2 = extract_text_facts_v2(
        FlyerRequestFields(), BRIEF, transport=_fake_transport(BASE_PAYLOAD))
    assert r2.occasion == "none"


def test_seam_report_out_sink(monkeypatch):
    monkeypatch.setenv("FLYER_EXTRACTION_V2", "1")
    import agents.flyer.extraction_v2 as x

    def fake(*a, **k):
        rep = x.V2ExtractionReport(items_locked=1, occasion="diwali")
        return [x._fact("item:0:name", "Idli")], rep

    monkeypatch.setattr("agents.flyer.extraction_v2.extract_text_facts_v2", fake)
    sink = {}
    facts = extract_text_facts_seam(FlyerRequestFields(), BRIEF, report_out=sink)
    assert [f.value for f in facts] == ["Idli"]  # return shape unchanged
    assert sink["occasion"] == "diwali"


def test_project_schema_default_and_old_rows():
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    proj = FlyerProject(
        project_id="F9801", status="generating_concepts", customer_phone=PHONE,
        created_at=now, updated_at=now, original_message_id="m",
        raw_request="x", fields=FlyerRequestFields(), locked_facts=[])
    assert proj.occasion == "none"  # old rows (no key) validate to none
    with pytest.raises(Exception):
        proj.model_copy(update={"occasion": "christmas"}).model_validate(
            proj.model_copy(update={"occasion": "christmas"}).model_dump())


def _render_project(occasion="none"):
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F9802", status="generating_concepts", customer_phone=PHONE,
        created_at=now, updated_at=now, original_message_id="m",
        raw_request=BRIEF, fields=FlyerRequestFields(), occasion=occasion,
        locked_facts=[
            _F("business_name", "Lakshmi's Kitchen"),
            _F("campaign_title", "Weekend Special"),
            _F("pricing_structure", "Any tiffin $6.99"),
            _F("item:0:name", "Idli"), _F("item:1:name", "Dosa"),
        ])


def test_render_composes_occasion_theme(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
    p = build_image_generation_prompt(
        _render_project(occasion="july4"), concept_id="C1",
        output_format="concept_preview", size=(1080, 1350))
    assert "OCCASION THEME - JULY 4TH" in p
    assert "bunting" in p  # occasion vocabulary joins the ban line
    none_p = build_image_generation_prompt(
        _render_project(occasion="none"), concept_id="C1",
        output_format="concept_preview", size=(1080, 1350))
    assert "OCCASION THEME" not in none_p


def test_bare_transient_project_carries_occasion():
    # Plumbing pin: detection must reach the constructed project (the
    # phantom-field gap) — bare path.
    from types import SimpleNamespace

    from agents.flyer.bare_render import _build_transient_project
    cust = SimpleNamespace(customer_id="CUST0001", business_whatsapp_number=PHONE)
    proj = _build_transient_project(cust, FlyerRequestFields(), [], "brief", "m1", "c@lid",
                                    occasion="diwali")
    assert proj.occasion == "diwali"
    proj2 = _build_transient_project(cust, FlyerRequestFields(), [], "brief", "m1", "c@lid",
                                     occasion="christmas")
    assert proj2.occasion == "none"  # constructor also fail-neutral
