"""WS2 — uniform-price verifier contract (v2 spec, accepted 2026-07-03).

Labeled failures: Leg-1 G2/G3 corpus + LIVE F0197/F0201 premium final_fails —
the composer/verifier contract conflict: extraction propagates one shared price
onto every item, the one-dominant-price + item-name-strip design (canonical
positive: the image-2 reference) correctly paints the price ONCE, and the old
per-item adjacency rule then block-failed every uniform-price flyer.

Direction of fix per the complexity budget: the VERIFIER's contract loosens to
match legitimate design; no composer machinery. Everything else stays enforced:
item-name presence (block-tier), the shared price's own visibility, foreign
price fabrication, duplicate rows.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agents.flyer.visual_qa import run_visual_qa
from schemas import FlyerLockedFact, FlyerProject


def _F(fid, value, req=True):
    return FlyerLockedFact(fact_id=fid, label=fid, value=value,
                           source="customer_text", required=req)


def _uniform_project():
    """F0201's live shape: shared $5.99 offer, per-item $5.99 propagated."""
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F9301", status="generating_concepts", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-ws2",
        raw_request="Create a flyer for Lakshmi's Kitchen weekend special $5.99 each ...",
        locked_facts=[
            _F("business_name", "Lakshmi's Kitchen"),
            _F("campaign_title", "weekend special"),
            _F("pricing_structure", "$5.99 each"),
            _F("schedule", "Saturday and Sunday only"),
            _F("item:0:name", "Idli"), _F("item:0:price", "$5.99"),
            _F("item:1:name", "Medu Vada"), _F("item:1:price", "$5.99"),
            _F("item:2:name", "Upma"), _F("item:2:price", "$5.99"),
            _F("item:3:name", "Pongal"), _F("item:3:price", "$5.99"),
        ],
    )


def _mixed_price_project():
    proj = _uniform_project()
    facts = [f for f in proj.locked_facts if f.fact_id != "item:3:price"]
    facts.append(_F("item:3:price", "$7.99"))
    return proj.model_copy(update={"locked_facts": facts})


PREMIUM_STYLE_OCR = """LAKSHMI'S KITCHEN
WEEKEND SPECIAL
$5.99 each
Idli
Medu Vada
Upma
Pongal
Saturday and Sunday only
90 Brybar Dr St Johns FL +17329837841
"""


def _artifact(tmp_path, ocr_text):
    artifact = tmp_path / "poster.png"
    artifact.write_bytes(b"sidecar-backed test artifact")
    (tmp_path / "poster.png.ocr.txt").write_text(ocr_text, encoding="utf-8")
    return artifact


def test_uniform_price_name_strip_design_passes(tmp_path):
    # THE headline fix: one dominant shared price + item name strip must PASS.
    # Pre-WS2 this block-failed with 'item price mismatch: item:0..3' (G2 corpus
    # 3/3 deterministic; F0197/F0201 live).
    report = run_visual_qa(_uniform_project(), _artifact(tmp_path, PREMIUM_STYLE_OCR),
                           output_format="concept_preview", allow_sidecar=True)
    assert not [b for b in report.blockers if "item price mismatch" in b], report.blockers
    assert report.status == "passed", (report.status, report.blockers)


def test_uniform_price_adjacent_rows_still_pass(tmp_path):
    # The integrated-render style (price beside each name) remains valid too.
    ocr = PREMIUM_STYLE_OCR.replace("Idli\n", "Idli $5.99\n").replace(
        "Medu Vada\n", "Medu Vada $5.99\n").replace("Upma\n", "Upma $5.99\n").replace(
        "Pongal\n", "Pongal $5.99\n")
    report = run_visual_qa(_uniform_project(), _artifact(tmp_path, ocr),
                           output_format="concept_preview", allow_sidecar=True)
    assert report.status == "passed", (report.status, report.blockers)


def test_uniform_price_duplicate_rows_still_block(tmp_path):
    # Duplicate same-row output is still wrong under the loosened contract.
    ocr = PREMIUM_STYLE_OCR + "Idli $5.99\nIdli $5.99\n"
    report = run_visual_qa(_uniform_project(), _artifact(tmp_path, ocr),
                           output_format="concept_preview", allow_sidecar=True)
    assert any("duplicate item price visible" in b for b in report.blockers), report.blockers


def test_uniform_price_foreign_price_still_blocks(tmp_path):
    # A visible price backed by NO locked fact stays block-tier (fabrication net
    # untouched — the adjacency loosening must not open the money door).
    ocr = PREMIUM_STYLE_OCR + "Vada combo $9.99\n"
    report = run_visual_qa(_uniform_project(), _artifact(tmp_path, ocr),
                           output_format="concept_preview", allow_sidecar=True)
    assert any("$9.99" in b or "price" in b.lower() for b in report.blockers), report.blockers
    assert report.status == "failed"


def test_uniform_price_missing_item_name_still_blocks(tmp_path):
    # Item-NAME presence is unchanged: dropping a name from the poster still fails.
    ocr = PREMIUM_STYLE_OCR.replace("Pongal\n", "")
    report = run_visual_qa(_uniform_project(), _artifact(tmp_path, ocr),
                           output_format="concept_preview", allow_sidecar=True)
    assert any("item:3:name" in b or "Pongal" in b for b in report.blockers), report.blockers


def test_mixed_prices_keep_strict_adjacency(tmp_path):
    # NON-uniform menus keep the original contract: a name-strip poster (prices
    # not beside names) must still block on the pair rule.
    report = run_visual_qa(_mixed_price_project(),
                           _artifact(tmp_path, PREMIUM_STYLE_OCR + "$7.99\n"),
                           output_format="concept_preview", allow_sidecar=True)
    assert any("item price mismatch" in b for b in report.blockers), report.blockers


def test_shared_item_price_absent_from_offer_keeps_strict_path():
    # Items share a price that does NOT appear in the offer statement -> not the
    # uniform-price design contract; strict adjacency applies.
    from agents.flyer.visual_qa import _uniform_shared_price
    proj = _uniform_project()
    facts = [f for f in proj.locked_facts if f.fact_id != "pricing_structure"]
    facts.append(_F("pricing_structure", "Weekend deal"))
    proj = proj.model_copy(update={"locked_facts": facts})
    records = {0: {"name": "Idli", "price": "$5.99"}, 1: {"name": "Upma", "price": "$5.99"}}
    assert _uniform_shared_price(proj, records) is False


def test_uniform_detector_unit():
    from agents.flyer.visual_qa import _uniform_shared_price
    proj = _uniform_project()
    records = {i: {"name": n, "price": "$5.99"}
               for i, n in enumerate(["Idli", "Medu Vada", "Upma", "Pongal"])}
    assert _uniform_shared_price(proj, records) is True
    records[3] = {"name": "Pongal", "price": "$7.99"}
    assert _uniform_shared_price(proj, records) is False       # mixed -> strict
    assert _uniform_shared_price(proj, {0: {"name": "Idli"}}) is False  # no prices -> strict path (no-op anyway)


def test_substring_price_never_activates_loosened_path():
    # Dollarless "5.99" items under a "$15.99" offer must NOT count as
    # uniform-price. This is the EXACT exploit the pre-fix substring check
    # allowed ("5.99" in "Familyplatter$15.99" is True; reviewer-verified this
    # test FAILS on the old code and passes on the exact-token gate).
    from agents.flyer.visual_qa import _uniform_shared_price
    proj = _uniform_project()
    facts = [f for f in proj.locked_facts if f.fact_id != "pricing_structure"]
    facts.append(_F("pricing_structure", "Family platter $15.99"))
    proj = proj.model_copy(update={"locked_facts": facts})
    records = {i: {"name": n, "price": "5.99"}
               for i, n in enumerate(["Idli", "Medu Vada", "Upma", "Pongal"])}
    assert _uniform_shared_price(proj, records) is False
