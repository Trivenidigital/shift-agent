"""Workstream B — bright/tricolor ``festive-vernacular`` register + brush-script
font (plan: tasks/flyer-brand-style-transfer-plan.md §B).

Contracts pinned here (the register ships DORMANT — nothing selects it until the
style-transfer PR lands or ``FLYER_STYLE_REGISTER_OVERRIDE`` targets it):

- The register is present in the catalog, composes through ``style_prompt_block``
  with occasion/intensity, and DEFAULT_REGISTER is untouched.
- No-fact law (module standing rule): the register text carries no digits /
  prices / phones.
- Leak law (standing rule 2026-07-04): the register's distinctive jargon
  (brush-script / hand-lettered / tricolor / vernacular / chip row) is authored
  INTO the base screen, actually appears in the register prose, and is consumed
  by the ``visual_qa`` forbidden-substring union (end-to-end block).
- A vendored OFL brush-script face (Pacifico) loads via BOTH composer loaders
  (``premium_overlay._premium_font`` role + ``premium_poster_v1._headline_font``
  keyed to the register) and is covered by the deploy smoke font-gate, which is
  data-driven off ``_ROLE_FILES``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from agents.flyer import premium_overlay as po
from agents.flyer import premium_poster_v1 as pp
from agents.flyer import visual_qa as vq
from agents.flyer.style_registers import (
    DEFAULT_REGISTER,
    INTENSITIES,
    OCCASIONS,
    REGISTERS,
    forbidden_substrings_for,
    style_prompt_block,
)
from agents.flyer.visual_qa import run_visual_qa
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields

REGISTER = "festive-vernacular"
_NEW_JARGON = ("brush-script", "hand-lettered", "tricolor", "vernacular", "chip row")


# ── register catalog ────────────────────────────────────────────────────────

def test_festive_vernacular_registered_without_disturbing_default():
    assert REGISTER in REGISTERS
    assert DEFAULT_REGISTER == "festive-premium"  # unchanged


def test_festive_vernacular_prose_carries_its_look():
    block = style_prompt_block(REGISTER)
    low = block.lower()
    assert "festive vernacular" in low
    assert "cream" in low            # warm cream canvas
    assert "brush-script" in low     # hand-lettered headline
    assert "tricolor" in low         # tricolor green+orange accents
    assert "green" in low and "orange" in low
    assert "ORNAMENT DISCIPLINE" in block  # sibling ornament-discipline sentence


def test_festive_vernacular_composes_with_occasion_and_intensity():
    full = style_prompt_block(REGISTER, occasion="diwali", intensity="full")
    assert "FESTIVE VERNACULAR" in full
    assert "DIWALI" in full and "FULL intensity" in full
    accent = style_prompt_block(REGISTER, occasion="diwali", intensity="accent")
    assert "ACCENT intensity" in accent and accent != full


def test_festive_vernacular_obeys_no_fact_law():
    # data-only register: no digits, no $-amounts, no phone-length runs
    for occ in (None, *OCCASIONS):
        for lvl in INTENSITIES:
            block = style_prompt_block(REGISTER, occasion=occ, intensity=lvl)
            assert not re.search(r"[$]\d|\d{3,}", block), (occ, lvl)
            assert not re.search(r"\d", style_prompt_block(REGISTER)), "register base text has no digits"


# ── leak law ────────────────────────────────────────────────────────────────

def test_festive_vernacular_jargon_screened_and_in_prose():
    entries = forbidden_substrings_for(REGISTER)
    prose = style_prompt_block(REGISTER).lower()
    for word in _NEW_JARGON:
        assert word in entries, f"{word} missing from leak screen"
        assert word == word.lower(), word
        assert word in prose, f"{word} screened but never appears in the register prose"


def test_festive_vernacular_jargon_consumed_by_visual_qa_union():
    union = set(vq._style_vocab_entries())
    for word in _NEW_JARGON:
        assert word in union, f"{word} not picked up by visual_qa forbidden-substring union"


def test_brush_script_leak_blocks_end_to_end(tmp_path):
    # jargon painted into the art (OCR sidecar) must block, via the same screen
    # that catches 'letterspaced'/'scalloped' (test_flyer_qa_hardening exhibits).
    phone = "+17329837841"
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F9702", status="generating_concepts", customer_phone=phone,
        created_at=now, updated_at=now, original_message_id="m-fv",
        raw_request="Create a bright weekend flyer. Idli, Medu Vada.",
        fields=FlyerRequestFields(),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="business_name",
                            value="Lakshmi's Kitchen", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="item:0:name",
                            value="Idli", source="customer_text", required=True),
        ],
    )
    ocr = "LAKSHMI'S KITCHEN\nIdli\nMedu Vada\nbrush-script headline\n"
    art = tmp_path / "p.png"
    art.write_bytes(b"sidecar test artifact")
    (tmp_path / "p.png.ocr.txt").write_text(ocr, encoding="utf-8")
    rep = run_visual_qa(project, art, output_format="concept_preview", allow_sidecar=True)
    assert any("brush-script" in b.lower() or "style vocabulary" in b.lower()
               for b in rep.blockers), rep.blockers


# ── vendored brush-script font: both loaders + smoke gate ───────────────────

def test_script_role_loads_pacifico_via_premium_font():
    f = po._premium_font("script", 40)
    assert f is not None
    assert f.size == 40
    assert f.getname()[0] == "Pacifico"


def test_headline_font_uses_brush_script_for_festive_vernacular():
    script = pp._headline_font(60, register=REGISTER)
    assert script.getname()[0] == "Pacifico"
    # default (no register) is byte-identical to today: the theatrical Montserrat
    default = pp._headline_font(60)
    assert default.getname()[0] == "Montserrat"


def test_pacifico_in_role_files_so_smoke_gate_covers_it():
    # the deploy smoke font-gate iterates set(_ROLE_FILES.values()); a new
    # vendored face must be reachable there or it ships ungated.
    assert "Pacifico-Regular.ttf" in set(po._ROLE_FILES.values())


def test_pacifico_font_file_and_ofl_license_vendored():
    fonts_dir = Path(po.__file__).resolve().parent / "fonts"
    assert (fonts_dir / "Pacifico-Regular.ttf").is_file()
    assert (fonts_dir / "Pacifico-OFL.txt").is_file()
    fonts_md = (fonts_dir / "FONTS.md").read_text(encoding="utf-8")
    assert "Pacifico-Regular.ttf" in fonts_md
    assert "OFL" in fonts_md
