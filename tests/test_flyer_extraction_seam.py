"""WS1b seam-swap contract: flag-gated, fail-closed to legacy, identity-safe.

DO-NOT-MERGE gate (v2 spec + operator sequencing): this PR merges only after
the WS1a golden CI gate is green on main AND the A4 shadow watcher has compared
at least two live briefs. These tests pin the swap's safety contract.
"""
from __future__ import annotations

import json

import pytest

from agents.flyer import extraction_seam, extraction_v2, facts as facts_mod
from agents.flyer.extraction_seam import extract_text_facts_seam
from schemas import FlyerRequestFields

BRIEF = ("Create a flyer for Lakshmi's Kitchen Breakfast Combo. Any item $6.49 this week. "
         "Include idli, dosa, vada, pongal, upma, and pesarattu. Monday to Friday, 7 AM - 11 AM.")


@pytest.fixture(autouse=True)
def _flag_clean(monkeypatch):
    monkeypatch.delenv("FLYER_EXTRACTION_V2", raising=False)


def _dump(facts):
    return json.dumps([(f.fact_id, f.value, f.source, f.required) for f in facts])


def test_flag_off_is_byte_identical_legacy_and_silent():
    audits = []
    seam = extract_text_facts_seam(FlyerRequestFields(), BRIEF, message_id="m1",
                                   audit=lambda e, p: audits.append(e))
    legacy = facts_mod.extract_text_facts(FlyerRequestFields(), BRIEF, message_id="m1")
    assert _dump(seam) == _dump(legacy)
    assert audits == []  # zero rows when the flag is off


def test_flag_on_routes_through_v2_and_audits(monkeypatch):
    monkeypatch.setenv("FLYER_EXTRACTION_V2", "1")
    fake_facts = [extraction_v2._fact("item:0:name", "idli")]
    fake_report = extraction_v2.V2ExtractionReport(items_locked=1, scalars_locked=0)
    monkeypatch.setattr("agents.flyer.extraction_v2.extract_text_facts_v2",
                        lambda *a, **k: (fake_facts, fake_report))
    audits = []
    out = extract_text_facts_seam(FlyerRequestFields(), BRIEF,
                                  audit=lambda e, p: audits.append((e, p)))
    assert [f.value for f in out] == ["idli"]
    assert audits and audits[0][0] == "extraction_v2_used"
    assert audits[0][1].items_locked == 1


def test_flag_on_v2_failure_falls_back_to_legacy_and_audits(monkeypatch):
    monkeypatch.setenv("FLYER_EXTRACTION_V2", "1")

    def boom(*a, **k):
        raise extraction_v2.ExtractionV2Error("transport down")

    monkeypatch.setattr("agents.flyer.extraction_v2.extract_text_facts_v2", boom)
    audits = []
    out = extract_text_facts_seam(FlyerRequestFields(), BRIEF, message_id="m1",
                                  audit=lambda e, p: audits.append((e, str(p))))
    legacy = facts_mod.extract_text_facts(FlyerRequestFields(), BRIEF, message_id="m1")
    assert _dump(out) == _dump(legacy)  # fail-closed: legacy result, not empty
    assert audits and audits[0][0] == "extraction_v2_fallback"
    assert "ExtractionV2Error" in audits[0][1]


def test_identity_suppressed_when_profile_owns_it(monkeypatch):
    monkeypatch.setenv("FLYER_EXTRACTION_V2", "1")
    fake_facts = [extraction_v2._fact("business_name", "Lakshmi's Kitchen"),
                  extraction_v2._fact("contact_phone", "+17329837841"),
                  extraction_v2._fact("location", "90 Brybar Dr"),
                  extraction_v2._fact("campaign_title", "Breakfast Combo"),
                  extraction_v2._fact("item:0:name", "idli")]
    monkeypatch.setattr("agents.flyer.extraction_v2.extract_text_facts_v2",
                        lambda *a, **k: (fake_facts, extraction_v2.V2ExtractionReport()))
    out = extract_text_facts_seam(FlyerRequestFields(), BRIEF, allow_text_identity=False)
    ids = {f.fact_id for f in out}
    assert "business_name" not in ids and "contact_phone" not in ids and "location" not in ids
    assert "campaign_title" in ids and "item:0:name" in ids  # non-identity facts kept


def test_audit_failure_never_blocks(monkeypatch):
    monkeypatch.setenv("FLYER_EXTRACTION_V2", "1")
    monkeypatch.setattr("agents.flyer.extraction_v2.extract_text_facts_v2",
                        lambda *a, **k: ([extraction_v2._fact("item:0:name", "idli")],
                                         extraction_v2.V2ExtractionReport(items_locked=1)))

    def audit_boom(e, p):
        raise RuntimeError("audit chokepoint down")

    out = extract_text_facts_seam(FlyerRequestFields(), BRIEF, audit=audit_boom)
    assert [f.value for f in out] == ["idli"]


def test_outcome_schema_round_trips_log_entry_union():
    from datetime import datetime, timezone

    import schemas as platform_schemas
    from pydantic import TypeAdapter

    entry = platform_schemas.FlyerExtractionV2Outcome(
        ts=datetime.now(timezone.utc), seam="managed_create",
        event="extraction_v2_used", items_locked=6, scalars_locked=4)
    parsed = TypeAdapter(platform_schemas.LogEntry).validate_json(entry.model_dump_json())
    assert parsed.type == "flyer_extraction_v2_outcome" and parsed.items_locked == 6


def test_deploy_script_installs_the_new_modules():
    from pathlib import Path
    script = (Path(__file__).resolve().parent.parent / "src" / "agents" / "shift" /
              "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    for line in ("install -m 644 src/agents/flyer/extraction_v2.py /opt/shift-agent/flyer_extraction_v2.py",
                 "install -m 644 src/agents/flyer/extraction_seam.py /opt/shift-agent/flyer_extraction_seam.py"):
        assert line in script
