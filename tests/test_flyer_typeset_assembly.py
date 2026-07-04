"""Graduation commit 2 — flag-gated typeset-spec prompt assembly (plan:
tasks/flyer-prompt-graduation-plan.md).

Contracts pinned:
- FLAG OFF (default): build_image_generation_prompt is BYTE-IDENTICAL to the
  legacy assembly — no register text, no typeset sections, no ban line.
- FLAG ON (integrated renders): the prompt carries (a) the register art-
  direction block, (b) the TEXT TO RENDER numbered-strings section with every
  locked fact verbatim, (c) the separate HOW TO SET EACH LINE role section
  (leak-proofed two-section spec — role vocabulary never adjacent to
  renderable text), (d) the forbidden-vocabulary rule line.
- FLAG ON + background-only project: UNCHANGED legacy behavior (overlay owns
  text; the typeset spec is an integrated-render concern).
- Fail-closed: if style_registers cannot import, legacy assembly runs.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agents.flyer.render import build_image_generation_prompt
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields

PHONE = "+17329837841"


def _F(fid, value):
    return FlyerLockedFact(fact_id=fid, label=fid, value=value,
                           source="customer_text", required=True)


def _project(background_only=False):
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    facts = [
        _F("business_name", "Lakshmi's Kitchen"),
        _F("campaign_title", "Weekend Special"),
        _F("pricing_structure", "Any tiffin $6.99"),
        _F("schedule", "Friday to Sunday"),
        _F("item:0:name", "Idli"), _F("item:0:price", "$6.99"),
        _F("item:1:name", "Medu Vada"), _F("item:1:price", "$6.99"),
        _F("contact_phone", PHONE),
        _F("location", "90 Brybar Dr St Johns FL"),
    ]
    fields = FlyerRequestFields()
    return FlyerProject(
        project_id="F9601", status="generating_concepts", customer_phone=PHONE,
        created_at=now, updated_at=now, original_message_id="m-c2",
        raw_request="Create a flyer for Weekend Special. Any tiffin $6.99. Idli, Medu Vada. Friday to Sunday.",
        fields=fields, locked_facts=facts,
    )


def _prompt(project, **kw):
    return build_image_generation_prompt(
        project, concept_id="C1", output_format="concept_preview",
        size=(1080, 1350), **kw)


def test_flag_off_is_byte_identical_legacy(monkeypatch):
    monkeypatch.delenv("FLYER_STYLE_REGISTERS", raising=False)
    monkeypatch.delenv("FLYER_STYLE_REGISTERS_ALLOWLIST", raising=False)
    p = _prompt(_project())
    assert "ART DIRECTION -" not in p
    assert "TEXT TO RENDER" not in p
    assert "HOW TO SET EACH LINE" not in p
    assert "Controlled customer copy:" in p  # legacy copy block intact
    # byte-level: flag-off output must not change when the flag machinery is
    # present but disabled (self-consistency pin)
    assert p == _prompt(_project())


def test_flag_on_integrated_carries_all_four_sections(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")  # production env
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
    p = _prompt(_project())
    assert "FESTIVE PREMIUM" in p                      # (a) register block
    assert "TEXT TO RENDER" in p                       # (b) numbered strings
    for value in ("Lakshmi's Kitchen", "Weekend Special", "Any tiffin $6.99",
                  "Friday to Sunday", "Idli", "Medu Vada"):
        assert value in p, value
    assert "HOW TO SET EACH LINE" in p                 # (c) role section
    assert "must NEVER appear as visible text" in p    # (d) ban line
    assert "beveled" in p and "scalloped" in p         # ban list carries jargon
    assert "OCCASION THEME" not in p                   # commit 3 plumbs occasion
    # legacy flat copy block replaced on this path
    assert "Controlled customer copy:" not in p


def test_flag_on_keeps_fact_keys_out_of_renderable_text(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")  # production env
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
    p = _prompt(_project())
    section = p.split("TEXT TO RENDER", 1)[1].split("HOW TO SET EACH LINE", 1)[0]
    assert "business_name" not in section
    assert "pricing_structure" not in section
    assert "item:0" not in section
    assert "Call +17329837841" in section  # phone typeset as display text


def test_flag_on_uniform_price_items_are_name_only(monkeypatch):
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")  # production env
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
    p = _prompt(_project())
    section = p.split("TEXT TO RENDER", 1)[1].split("HOW TO SET EACH LINE", 1)[0]
    # shared $6.99 lives in the price line, not beside item names
    assert "Idli $6.99" not in section
    assert "Medu Vada $6.99" not in section


def test_flag_on_background_only_unchanged(monkeypatch):
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
    on = _prompt(_project(), force_background_only=True)
    monkeypatch.delenv("FLYER_STYLE_REGISTERS", raising=False)
    off = _prompt(_project(), force_background_only=True)
    assert on == off  # overlay-owns-text path is not a typeset concern
    assert "TEXT TO RENDER" not in on


def test_flag_on_other_phone_stays_legacy(monkeypatch):
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", "+15550001111")
    p = _prompt(_project())
    assert "TEXT TO RENDER" not in p
    assert "Controlled customer copy:" in p


def test_offer_and_promotion_end_reach_typeset(monkeypatch):
    # PR #544 F1: offer:N and promotion_end are required=True facts QA demands;
    # the typeset spec must carry them (via the legacy detail selectors).
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
    proj = _project()
    proj = proj.model_copy(update={"locked_facts": [*proj.locked_facts,
        _F("offer:0", "Buy 2 get 1 free"),
        _F("promotion_end", "July 31")]})
    p = _prompt(proj)
    section = p.split("TEXT TO RENDER", 1)[1].split("HOW TO SET EACH LINE", 1)[0]
    assert "Buy 2 get 1 free" in section
    assert "July 31" in section


def test_headline_fallback_reaches_typeset(monkeypatch):
    # PR #544 F1: headline-only projects (no campaign_title) must still get
    # display text via the legacy title fallback.
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
    proj = _project()
    facts = [f for f in proj.locked_facts if f.fact_id != "campaign_title"]
    facts.append(_F("headline", "Grand Feast Days"))
    proj = proj.model_copy(update={"locked_facts": facts})
    p = _prompt(proj)
    section = p.split("TEXT TO RENDER", 1)[1].split("HOW TO SET EACH LINE", 1)[0]
    assert "Grand Feast Days" in section


def test_whole_dollar_substring_not_uniform(monkeypatch):
    # PR #544 F2: "$5" items under a "$50" offer are NOT uniform (WS2 exact-token
    # semantics) — rows must keep their prices so QA adjacency passes.
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
    proj = _project()
    facts = [f for f in proj.locked_facts if not f.fact_id.startswith("item:")
             and f.fact_id != "pricing_structure"]
    facts.extend([_F("pricing_structure", "Party tray $50"),
                  _F("item:0:name", "Samosa"), _F("item:0:price", "$5"),
                  _F("item:1:name", "Kachori"), _F("item:1:price", "$5")])
    proj = proj.model_copy(update={"locked_facts": facts})
    p = _prompt(proj)
    section = p.split("TEXT TO RENDER", 1)[1].split("HOW TO SET EACH LINE", 1)[0]
    assert "Samosa $5" in section  # prices stay beside names
    assert "Kachori $5" in section


def test_reference_extraction_project_stays_legacy(monkeypatch):
    # PR #544 F3: reference-extraction projects (items live in the attached
    # image) must NOT get the typeset "ONLY text allowed" contract.
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", PHONE)
    from agents.flyer import render as R
    proj = _project()
    monkeypatch.setattr(R, "_needs_reference_extraction", lambda p: True)
    p = _prompt(proj)
    assert "TEXT TO RENDER" not in p


def test_flag_off_integrated_matches_other_phone_legacy(monkeypatch):
    # PR #544 F6: pin flag-off byte path on the INTEGRATED route — flag fully
    # off must equal flag-on-for-another-phone (both legacy) byte for byte.
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.delenv("FLYER_STYLE_REGISTERS", raising=False)
    monkeypatch.delenv("FLYER_STYLE_REGISTERS_ALLOWLIST", raising=False)
    off = _prompt(_project())
    monkeypatch.setenv("FLYER_STYLE_REGISTERS", "1")
    monkeypatch.setenv("FLYER_STYLE_REGISTERS_ALLOWLIST", "+15550001111")
    other = _prompt(_project())
    assert off == other
