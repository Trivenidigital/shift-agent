"""WS1 golden-set gate: extraction_v2 must lock 10/10 briefs with truth-parity.

Deterministic in CI: replays LIVE-CAPTURED LLM responses (fixtures recorded on
the box, 2026-07-03, gpt-4o-mini temp 0) through the real parity-guard/mapping
code. The live-drift half of the gate is the on-box golden eval + the shadow
watcher (customer's-path rule) — CI validates the deterministic layers.

Acceptance (v2 spec WS1): 10/10 truth-parity — every expected item locked,
correctly numbered, with no fabricated items. The four live-failure shapes
(price-colon B02, bare-lines B05, pairs B06, prose B09) are all in the set;
the deployed regex layer scores 5/10 on this same corpus (Leg 2).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.flyer.extraction_v2 import (
    ExtractionV2Error,
    extract_text_facts_v2,
    value_has_source_parity,
)
from schemas import FlyerRequestFields

FIXTURES = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "extraction_v2" / "golden_fixtures.json")
    .read_text(encoding="utf-8"))


def _run_fixture(bid):
    meta = FIXTURES[bid]
    facts, report = extract_text_facts_v2(
        FlyerRequestFields(), meta["brief"],
        transport=lambda s, u: meta["llm_response"])
    return meta, facts, report


@pytest.mark.parametrize("bid", sorted(FIXTURES))
def test_golden_truth_parity(bid):
    meta, facts, report = _run_fixture(bid)
    locked = [f.value.lower() for f in facts
              if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")]
    # every expected item is locked (allow case/containment tolerance both ways)
    for exp in meta["expected_items"]:
        assert any(exp in li or li in exp for li in locked), \
            f"{bid}: expected item {exp!r} not locked; locked={locked}"
    # no fabricated items: everything locked satisfies source parity by construction,
    # but assert it end-to-end anyway (the invariant, not the implementation)
    brief_lower = meta["brief"].lower()
    for li in locked:
        assert value_has_source_parity(li, brief_lower), f"{bid}: non-verbatim item {li!r}"


def test_golden_set_is_ten_out_of_ten():
    passed = 0
    for bid in FIXTURES:
        meta, facts, _ = _run_fixture(bid)
        locked = [f.value.lower() for f in facts
                  if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")]
        if all(any(e in li or li in e for li in locked) for e in meta["expected_items"]):
            passed += 1
    assert passed == 10, f"golden truth-parity {passed}/10 — release gate is 10/10"


def test_item_numbering_dense_and_ordered():
    _, facts, _ = _run_fixture("B08-countcolon")
    names = [f.fact_id for f in facts if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")]
    assert names == [f"item:{i}:name" for i in range(len(names))]


# ── parity guard (the load-bearing invariant) ────────────────────────────────

def test_parity_guard_drops_fabricated_scalar_and_item():
    fake = json.dumps({
        "business_name": "Lakshmi's Kitchen", "campaign_title": "Mega Feast Fiesta",
        "pricing_structure": "Any item $7.99", "schedule": None, "location": None,
        "contact_phone": None,
        "items": [{"name": "idli", "price": "$7.99"},
                  {"name": "chicken tikka pizza", "price": "$9.99"}],
    })
    brief = "Create a flyer. Any item $7.99. Include idli at Lakshmi's Kitchen."
    facts, report = extract_text_facts_v2(FlyerRequestFields(), brief,
                                          transport=lambda s, u: fake)
    ids = {f.fact_id: f.value for f in facts}
    assert ids.get("item:0:name") == "idli"
    assert "item:1:name" not in ids                      # fabricated item dropped
    assert "campaign_title" not in ids                   # fabricated title dropped
    assert any(x.startswith("campaign_title=") for x in report.dropped_by_parity)
    assert any(x.startswith("item:chicken") for x in report.dropped_by_parity)


def test_parity_guard_drops_fabricated_price_keeps_item():
    fake = json.dumps({"items": [{"name": "idli", "price": "$9.99"}]})
    brief = "Flyer please: idli special this week."
    facts, _ = extract_text_facts_v2(FlyerRequestFields(), brief,
                                     transport=lambda s, u: fake)
    ids = {f.fact_id: f.value for f in facts}
    assert ids.get("item:0:name") == "idli"
    assert "item:0:price" not in ids


# ── fail-closed contract ─────────────────────────────────────────────────────

def test_transport_failure_raises_never_silent():
    def boom(s, u):
        raise ExtractionV2Error("transport down")
    with pytest.raises(ExtractionV2Error):
        extract_text_facts_v2(FlyerRequestFields(), "some brief", transport=boom)


def test_parse_failure_raises_never_silent():
    with pytest.raises(ExtractionV2Error):
        extract_text_facts_v2(FlyerRequestFields(), "some brief",
                              transport=lambda s, u: "not json at all {")


def test_empty_brief_returns_empty_without_calling_llm():
    def boom(s, u):
        raise AssertionError("must not be called")
    facts, report = extract_text_facts_v2(FlyerRequestFields(), "   ", transport=boom)
    assert facts == [] and report.items_locked == 0


# ── interface compatibility with the legacy seam ─────────────────────────────

def test_locked_fact_interface_matches_legacy():
    _, facts, _ = _run_fixture("B03-shapeC")
    f = facts[0]
    assert f.source == "customer_text" and f.required is True
    assert isinstance(f.fact_id, str) and isinstance(f.value, str) and f.label


# ── A3: empty-image transport retry (labeled failure: Leg 2 B02) ─────────────

def test_transport_retry_retries_empty_image_then_succeeds(monkeypatch):
    from agents.flyer import render
    calls = {"n": 0}

    def fake(project, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise render.FlyerRenderError("OpenRouter image response had no images: {}")
        return b"PNGBYTES"

    monkeypatch.setattr(render, "_openrouter_image_bytes", fake)
    monkeypatch.setattr(render.time, "sleep", lambda s: None)
    out = render.openrouter_image_with_transport_retry(object(), transport_attempts=2)
    assert out == b"PNGBYTES" and calls["n"] == 2


def test_transport_retry_exhausts_then_raises(monkeypatch):
    import pytest as _pytest
    from agents.flyer import render
    monkeypatch.setattr(render, "_openrouter_image_bytes",
                        lambda project, **kw: (_ for _ in ()).throw(
                            render.FlyerRenderError("OpenRouter image response had no images: {}")))
    monkeypatch.setattr(render.time, "sleep", lambda s: None)
    with _pytest.raises(render.FlyerRenderError):
        render.openrouter_image_with_transport_retry(object(), transport_attempts=2)


def test_transport_retry_real_errors_raise_immediately(monkeypatch):
    import pytest as _pytest
    from agents.flyer import render
    calls = {"n": 0}

    def fake(project, **kw):
        calls["n"] += 1
        raise render.FlyerRenderError("OpenRouter image connection failed: auth")

    monkeypatch.setattr(render, "_openrouter_image_bytes", fake)
    with _pytest.raises(render.FlyerRenderError):
        render.openrouter_image_with_transport_retry(object(), transport_attempts=3)
    assert calls["n"] == 1  # not retried — only empty-response transients retry


# -- F2 regression: word-boundary parity (proven exploit class, PR #535 review) --

def test_parity_guard_blocks_subtoken_price_fabrication():
    # "$9.99" must NOT pass against a brief containing "$4.99" (digit substrings)
    fake = json.dumps({"items": [{"name": "masala dosa", "price": "$9.99"}]})
    brief = "Grand opening special! Masala dosa just $4.99 this Sunday."
    facts, report = extract_text_facts_v2(FlyerRequestFields(), brief,
                                          transport=lambda s, u: fake)
    ids = {f.fact_id: f.value for f in facts}
    assert ids.get("item:0:name") == "masala dosa"
    assert "item:0:price" not in ids  # fabricated money fact blocked at the producer


def test_parity_guard_blocks_subtoken_item_fabrication():
    # "rice" must NOT pass against a brief containing "price"
    fake = json.dumps({"items": [{"name": "rice", "price": None}]})
    brief = "Any item one price this weekend."
    facts, _ = extract_text_facts_v2(FlyerRequestFields(), brief,
                                     transport=lambda s, u: fake)
    assert not [f for f in facts if f.fact_id.startswith("item:")]


# -- F3 regression: null content is fail-closed, never a raw TypeError --

def test_null_transport_content_raises_extraction_error():
    with pytest.raises(ExtractionV2Error):
        extract_text_facts_v2(FlyerRequestFields(), "some brief",
                              transport=lambda s, u: None)
