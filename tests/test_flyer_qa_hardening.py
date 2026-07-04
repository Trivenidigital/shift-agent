"""Graduation commit 4 — QA hardening batch (plan:
tasks/flyer-prompt-graduation-plan.md). Exhibit-backed checks:

1. STYLE-VOCAB screen (always on, fail-open on import skew): prompt jargon
   painted into art blocks. Exhibits: "WIDELY LETTERSPACED" subhead (R2.6),
   "SCHEDULE LINE"/"MENU CHIPS" labels (R2.5), banned-entry class.
2. OFFER-QUALIFIER drift (always on): promo/offer qualifiers visible in art
   that appear in NO locked fact AND NOT in the customer's brief = invented
   claim. Exhibit class: a "COMBO" badge on a brief that never said combo.
3. NEAR-MISS SPELLING on schedule words (always on): day-word corruption at
   edit distance 1 blocks. Exhibit: "FRIDAYS AND SATURDAY" passing QA.
4. STRICT EXTRANEOUS-TOKEN screen (typeset-contract renders only — the
   FLYER_STYLE_REGISTERS prompt promises the numbered strings are the ONLY
   text): unauthorized alpha tokens >=5 chars block. Exhibits: "Degional",
   "Huge Dunchanuf", "cleary treatment".
"""
from __future__ import annotations

from datetime import datetime, timezone

from agents.flyer.visual_qa import run_visual_qa
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields

PHONE = "+17329837841"


def _F(fid, value):
    return FlyerLockedFact(fact_id=fid, label=fid, value=value,
                           source="customer_text", required=True)


def _project(raw=None):
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F9701", status="generating_concepts", customer_phone=PHONE,
        created_at=now, updated_at=now, original_message_id="m-c4",
        raw_request=raw or ("Create a flyer for Weekend Special. Any tiffin $6.99. "
                            "Idli, Medu Vada. Fridays and Saturdays."),
        fields=FlyerRequestFields(),
        locked_facts=[
            _F("business_name", "Lakshmi's Kitchen"),
            _F("campaign_title", "Weekend Special"),
            _F("pricing_structure", "Any tiffin $6.99"),
            _F("schedule", "Fridays and Saturdays"),
            _F("item:0:name", "Idli"), _F("item:0:price", "$6.99"),
            _F("item:1:name", "Medu Vada"), _F("item:1:price", "$6.99"),
        ],
    )


GOOD_OCR = """LAKSHMI'S KITCHEN
Weekend Special
Any tiffin $6.99
Idli
Medu Vada
Fridays and Saturdays
"""


def _artifact(tmp_path, ocr):
    art = tmp_path / "p.png"
    art.write_bytes(b"sidecar test artifact")
    (tmp_path / "p.png.ocr.txt").write_text(ocr, encoding="utf-8")
    return art


def _qa(tmp_path, ocr, project=None):
    return run_visual_qa(project or _project(), _artifact(tmp_path, ocr),
                         output_format="concept_preview", allow_sidecar=True)


def test_clean_render_still_passes(tmp_path):
    assert _qa(tmp_path, GOOD_OCR).status == "passed"


def test_style_vocab_leak_blocks(tmp_path):
    # R2.6 exhibit: type-instruction vocabulary painted as a subhead.
    rep = _qa(tmp_path, GOOD_OCR + "WIDELY LETTERSPACED\n")
    assert any("letterspaced" in b.lower() for b in rep.blockers), rep.blockers
    rep2 = _qa(tmp_path, GOOD_OCR + "scalloped medallion\n")
    assert any("style vocabulary" in b.lower() or "scalloped" in b.lower()
               for b in rep2.blockers), rep2.blockers


def test_invented_offer_qualifier_blocks(tmp_path):
    # Qualifier in art, absent from every locked fact AND the brief = invented
    # claim (the ships-wrong class).
    rep = _qa(tmp_path, GOOD_OCR + "COMBO $6.99\n")
    assert any("qualifier" in b.lower() or "combo" in b.lower()
               for b in rep.blockers), rep.blockers


def test_brief_backed_qualifier_passes(tmp_path):
    # OC3 correction: qualifiers the CUSTOMER wrote (in brief or facts) are
    # authorized even if extraction dropped them from facts.
    raw = ("Create a flyer for Weekend Special combo. Any tiffin $6.99. "
           "Idli, Medu Vada. Fridays and Saturdays.")
    rep = _qa(tmp_path, GOOD_OCR + "COMBO $6.99\n", project=_project(raw=raw))
    assert not any("qualifier" in b.lower() for b in rep.blockers), rep.blockers


def test_day_word_near_miss_blocks(tmp_path):
    # Plural-loss exhibit: "SATURDAY" for locked "Saturdays" (edit distance 1)
    # must block, not pass as fuzzy-visible.
    ocr = GOOD_OCR.replace("Fridays and Saturdays", "Fridays and Saturday")
    rep = _qa(tmp_path, ocr)
    assert any("near-miss" in b.lower() or "schedule" in b.lower()
               for b in rep.blockers), rep.blockers


def test_strict_extraneous_screen_gated_on_render_marker(tmp_path):
    # PR #545 F2: the strict screen keys on the RENDER-TIME sidecar marker,
    # never env at QA time. No marker (legacy render) -> unscreened even with
    # gibberish; marker present -> 'Degional'-class blocks.
    gib = GOOD_OCR + "Degional inide\n"
    art = _artifact(tmp_path, gib)
    from agents.flyer.visual_qa import run_visual_qa
    legacy = run_visual_qa(_project(), art, output_format="concept_preview", allow_sidecar=True)
    assert not any("extraneous" in b.lower() for b in legacy.blockers)
    (tmp_path / "p.png.typeset.json").write_text('{"typeset_contract": true}', encoding="utf-8")
    strict = run_visual_qa(_project(), art, output_format="concept_preview", allow_sidecar=True)
    assert any("degional" in b.lower() for b in strict.blockers), strict.blockers


def test_strict_screen_authorizes_system_fallback_strings(tmp_path):
    # PR #545 F1: the prompt's own instructed strings ('Specials' fallback,
    # 'Call' prefix) are never punished by the contract screen.
    ocr = GOOD_OCR + "SPECIALS\nCall now\n"
    art = _artifact(tmp_path, ocr)
    (tmp_path / "p.png.typeset.json").write_text('{"typeset_contract": true}', encoding="utf-8")
    from agents.flyer.visual_qa import run_visual_qa
    rep = run_visual_qa(_project(), art, output_format="concept_preview", allow_sidecar=True)
    assert not any("extraneous" in b.lower() for b in rep.blockers), rep.blockers


def test_pool_is_word_tokenized_not_substring(tmp_path):
    # PR #545 F3: 'peppers' in the brief must NOT authorize an invented 'PER
    # PLATE' claim; 'Freedom Sale' must not authorize 'FREE'.
    raw = ("Create a flyer for the Freedom Sale. Stuffed peppers $8.99. "
           "Fridays and Saturdays.")
    proj = _project(raw=raw)
    # Mixed prices so the uniform-price bridge cannot apply — 'per' stays an
    # invented claim here. (The original assertion used substring-in-lows and
    # was satisfied by 'oPERational' — the very bug class this test pins.)
    facts = [f for f in proj.locked_facts if f.fact_id != "item:1:price"]
    facts.append(_F("item:1:price", "$8.99"))
    proj = proj.model_copy(update={"locked_facts": facts})
    rep = _qa(tmp_path, GOOD_OCR + "PER PLATE\nFREE DELIVERY\n", project=proj)
    assert "invented offer qualifier visible: per" in rep.blockers, rep.blockers
    assert "invented offer qualifier visible: free" in rep.blockers, rep.blockers


def test_paraphrase_bridges_authorize_customer_stated_forms(tmp_path):
    # PR #545 F4: '$10/plate' brief authorizes 'PER PLATE'; 'buy one get one'
    # authorizes 'BOGO'.
    raw = ("Thali special $10/plate, buy one get one on samosas. "
           "Idli, Medu Vada. Fridays and Saturdays.")
    proj = _project(raw=raw)
    rep = _qa(tmp_path, GOOD_OCR + "PER PLATE\nBOGO\n", project=proj)
    assert not any("qualifier" in b.lower() for b in rep.blockers), rep.blockers


def test_each_authorized_on_uniform_price_design(tmp_path):
    # F0208 (C1 exhibit): brief "Any tiffin $6.99" without the literal word
    # 'each' — the render painting '$6.99 each' is a faithful restatement of
    # the locked uniform price and must NOT block (it killed the entire
    # ladder: integrated -> premium -> deterministic -> manual).
    rep = _qa(tmp_path, GOOD_OCR + "$6.99 each\n")
    assert not any("each" in b.lower() for b in rep.blockers), rep.blockers


def test_each_still_blocks_without_uniform_price(tmp_path):
    # Mixed-price project: 'each' visible with no uniform price and no brief
    # backing remains an invented claim.
    proj = _project()
    facts = [f for f in proj.locked_facts if f.fact_id != "item:1:price"]
    facts.append(_F("item:1:price", "$8.99"))
    proj = proj.model_copy(update={"locked_facts": facts})
    rep = _qa(tmp_path, GOOD_OCR + "EACH\n", project=proj)
    assert any("each" in b.lower() for b in rep.blockers), rep.blockers
