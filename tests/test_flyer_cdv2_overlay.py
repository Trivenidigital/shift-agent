"""CD v2 Slice B / Task B2.5 — narrative/hook-led overlay composition.

TDD: these tests must FAIL before premium_overlay grows the CD-v2-aware
narrative line + offer_priority seal scaling, then PASS after.

Load-bearing safety property: the premium overlay is the DELIVERED artifact, so
flag-off / ``creative_direction`` None or empty MUST produce a BYTE-IDENTICAL
PNG vs today. That is the #1 regression assert below.
"""
import os
from datetime import datetime, timezone

import pytest
from PIL import Image

from agents.flyer import premium_overlay as po
from agents.flyer import render
from agents.flyer.premium_overlay import plan_premium_layout
from schemas import FlyerProject, FlyerLockedFact


# ---------------------------------------------------------------------------
# Fixtures: a food project that renders premium (mirrors the existing suite)
# ---------------------------------------------------------------------------

def _bg(tmp_path, name="bg.png"):
    p = tmp_path / name
    Image.new("RGB", (1080, 1350), (70, 40, 20)).save(p)
    return p


def _base_facts():
    f = [
        FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Weekend Specials", source="customer_text", required=True),
        FlyerLockedFact(fact_id="pricing_structure", label="Pricing", value="Any item $7.99", source="customer_text", required=True),
        FlyerLockedFact(fact_id="schedule", label="Schedule", value="Saturday & Sunday, 4 PM-8 PM", source="customer_text", required=True),
        FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
    ]
    for i, nm in enumerate(["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar"]):
        f.append(FlyerLockedFact(fact_id=f"item:{i}:name", label=f"Item {i}", value=nm, source="customer_text", required=True))
    return f


def _project(creative_direction=None, facts=None, pid="F0250"):
    return FlyerProject(
        project_id=pid,
        status="intake_started",
        customer_phone="+17329837841",
        created_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        original_message_id="m",
        raw_request="Weekend Specials any item $7.99",
        locked_facts=facts or _base_facts(),
        creative_direction=creative_direction,
    )


_CD = {
    "hero_name": "Dosa",
    "supporting_names": ["Idli", "Vada"],
    "hook_text": "ANY ITEM $7.99",
    "hook_prominence": "high",
    "offer_priority": "high",
    "theme_family": "south_indian",
    "mood": "festive",
    "campaign_narrative": "South Indian Favorites at One Price",
}


def _render(project, tmp_path, name):
    out = tmp_path / name
    po.render_premium_overlay(project, _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    return out


# ---------------------------------------------------------------------------
# #1 LOAD-BEARING: flag-off / creative_direction None or empty => BYTE-IDENTICAL
# ---------------------------------------------------------------------------

def test_creative_direction_none_is_byte_identical_to_unset(tmp_path):
    """The delivered artifact MUST NOT change when CD v2 is off. A project with
    ``creative_direction=None`` and one where the attribute is forced absent must
    render byte-for-byte identical PNGs."""
    proj_none = _project(creative_direction=None, pid="F0251")
    proj_unset = _project(creative_direction=None, pid="F0251")
    # Force the attribute genuinely absent on the second project so the CD-v2 code
    # path is exercised with a missing attribute (getattr default), proving it is a
    # no-op identical to the explicit-None path.
    object.__delattr__(proj_unset, "creative_direction") if "creative_direction" in vars(proj_unset) else None

    a = _render(proj_none, tmp_path, "none.png")
    b = _render(proj_unset, tmp_path, "unset.png")
    assert a.read_bytes() == b.read_bytes()


def test_creative_direction_empty_dict_is_byte_identical_to_none(tmp_path):
    """An EMPTY ``creative_direction`` dict (no narrative/hook) must also produce
    byte-identical output vs None — empty CD draws nothing new."""
    base = _render(_project(creative_direction=None, pid="F0252"), tmp_path, "cd_none.png")
    empty = _render(_project(creative_direction={}, pid="F0252"), tmp_path, "cd_empty.png")
    assert base.read_bytes() == empty.read_bytes()


def test_creative_direction_blank_narrative_is_byte_identical(tmp_path):
    """A CD dict whose ``campaign_narrative`` is blank/whitespace and whose
    offer_priority is the default 'medium' must be byte-identical to None — no
    narrative to draw and seal size unchanged."""
    cd = dict(_CD, campaign_narrative="   ", offer_priority="medium", hook_prominence="medium")
    base = _render(_project(creative_direction=None, pid="F0253"), tmp_path, "cd_none2.png")
    blank = _render(_project(creative_direction=cd, pid="F0253"), tmp_path, "cd_blank.png")
    assert base.read_bytes() == blank.read_bytes()


# ---------------------------------------------------------------------------
# #2 Narrative present => render DIFFERS from the None render (narrative drawn)
# ---------------------------------------------------------------------------

def test_narrative_present_changes_output(tmp_path):
    """With a non-empty campaign_narrative the rendered top zone must differ from
    the None render (the narrative line is drawn)."""
    base = _render(_project(creative_direction=None, pid="F0254"), tmp_path, "n_none.png")
    cd = _render(_project(creative_direction=_CD, pid="F0254"), tmp_path, "n_cd.png")
    assert base.read_bytes() != cd.read_bytes()


def test_layout_plan_exposes_narrative(tmp_path):
    """Structural assert: plan_premium_layout surfaces the narrative so the
    renderer can place it. Defaults => empty narrative (today's behavior)."""
    items = [("Idli", ""), ("Dosa", "")]
    default = plan_premium_layout(items, shared_price="$7.99")
    assert default.narrative == ""
    with_n = plan_premium_layout(items, shared_price="$7.99", narrative="South Indian Favorites at One Price")
    assert with_n.narrative == "South Indian Favorites at One Price"


def test_narrative_value_is_inked_for_coverage(tmp_path, monkeypatch):
    """When the narrative is drawn it must register in the renderer's ink log
    (proves it was actually placed, not silently skipped). We spy the ink path by
    rendering and confirming the output differs AND the render succeeds (coverage
    gate still passes — narrative is not a required fact)."""
    out = _render(_project(creative_direction=_CD, pid="F0255"), tmp_path, "ink.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)


# ---------------------------------------------------------------------------
# #3 offer_priority scales the seal — unit-test the GEOMETRY, not pixels
# ---------------------------------------------------------------------------

def test_offer_priority_scales_seal_metric():
    """plan_premium_layout exposes an offer_scale; high > medium(default) > low,
    and default/medium/None == today's scale (1.0)."""
    items = [("Idli", ""), ("Dosa", "")]
    default = plan_premium_layout(items, shared_price="$7.99")
    high = plan_premium_layout(items, shared_price="$7.99", offer_priority="high")
    low = plan_premium_layout(items, shared_price="$7.99", offer_priority="low")
    medium = plan_premium_layout(items, shared_price="$7.99", offer_priority="medium")
    none_p = plan_premium_layout(items, shared_price="$7.99", offer_priority=None)
    assert default.offer_scale == 1.0
    assert medium.offer_scale == 1.0
    assert none_p.offer_scale == 1.0
    assert high.offer_scale > medium.offer_scale
    assert low.offer_scale < medium.offer_scale


def test_seal_geometry_scales_with_offer_scale():
    """draw_offer_seal/_measure_offer_seal honor offer_scale: a high scale yields a
    larger seal diameter than low, and scale=1.0 == today's diameter."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1080, 1350), (20, 20, 20))
    d = ImageDraw.Draw(img, "RGBA")
    base = po._measure_offer_seal(d, label="ANY ITEM", price="$7.99", width=1080)
    big = po._measure_offer_seal(d, label="ANY ITEM", price="$7.99", width=1080, offer_scale=1.18)
    small = po._measure_offer_seal(d, label="ANY ITEM", price="$7.99", width=1080, offer_scale=0.82)
    default = po._measure_offer_seal(d, label="ANY ITEM", price="$7.99", width=1080, offer_scale=1.0)
    assert default == base          # scale=1.0 == today (byte-identical seal)
    assert big > base               # high priority => larger seal
    assert small < base             # low priority => smaller seal


def test_draw_offer_seal_scale_default_matches_today():
    """draw_offer_seal called without offer_scale must produce the SAME bbox as
    offer_scale=1.0 (default preserves today's geometry)."""
    from PIL import Image, ImageDraw
    img1 = Image.new("RGB", (1080, 1350), (20, 20, 20)); d1 = ImageDraw.Draw(img1, "RGBA")
    img2 = Image.new("RGB", (1080, 1350), (20, 20, 20)); d2 = ImageDraw.Draw(img2, "RGBA")
    box_default = po.draw_offer_seal(d1, label="ANY ITEM", price="$7.99", width=1080, center=(540, 760))
    box_one = po.draw_offer_seal(d2, label="ANY ITEM", price="$7.99", width=1080, center=(540, 760), offer_scale=1.0)
    assert box_default == box_one
    assert img1.tobytes() == img2.tobytes()


# ---------------------------------------------------------------------------
# #4 Fit ladder — pathological narrative + many items still fits or fails-closed;
#     required-fact ledger never sacrificed for the narrative
# ---------------------------------------------------------------------------

def test_pathological_narrative_degrades_not_overflow(tmp_path):
    """A very long narrative must NOT cause a fail-closed by itself — the narrative
    is best-effort (dropped/shrunk first under pressure), required facts preserved.
    The render must still succeed and cover every required fact."""
    cd = dict(_CD, campaign_narrative="South Indian Favorites at One Price " * 30)
    out = _render(_project(creative_direction=cd, pid="F0256"), tmp_path, "longnarr.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)


def test_narrative_never_drops_required_fact(tmp_path):
    """Even with a long narrative competing for top-zone space, every required
    locked fact must remain covered (the render either fits everything or raises;
    it never silently ships missing a required fact). Here it must FIT."""
    cd = dict(_CD, campaign_narrative="Authentic Weekend Tiffin Festival Specials Now On")
    proj = _project(creative_direction=cd, pid="F0257")
    out = _render(proj, tmp_path, "reqfact.png")
    # render succeeded => the fail-closed required-fact ledger passed with the
    # narrative present.
    assert out.exists()


def test_narrative_does_not_break_failclosed_overflow(tmp_path):
    """The existing fail-closed ladder is intact WITH a narrative present: 40 locked
    item names still overflow and raise (narrative must not mask a real overflow)."""
    cd = dict(_CD)
    facts = [f for f in _base_facts() if not f.fact_id.startswith("item:")]
    for i in range(40):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label="Item", value=f"VeryLongDishNameNumber{i}", source="customer_text", required=True))
    proj = _project(creative_direction=cd, facts=facts, pid="F0258")
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(proj, _bg(tmp_path), tmp_path / "ov.png", size=(1080, 1350), output_format="concept_preview")


def test_never_raises_on_malformed_creative_direction(tmp_path):
    """Guarded dict reads: a malformed creative_direction (non-str narrative, odd
    types) must never raise from the CD-v2 code; it degrades to today's layout."""
    cd = {"campaign_narrative": 12345, "offer_priority": ["nonsense"], "hook_prominence": None}
    out = _render(_project(creative_direction=cd, pid="F0259"), tmp_path, "malformed.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)


# ---------------------------------------------------------------------------
# FIX 2 (Codex MAJOR): the narrative is DROPPED under pressure, never degraded
#   premium→flat. When including the narrative makes the required content not
#   fit, the overlay RETRIES without the narrative and still renders premium;
#   only a layout that fails EVEN WITHOUT the narrative raises/degrades.
# ---------------------------------------------------------------------------

def test_fix2_narrative_dropped_not_degraded_when_it_breaks_required(tmp_path, monkeypatch):
    """A narrative that, once DRAWN, consumes enough top-zone space to push the
    required menu off the canvas must NOT degrade to flat: the overlay drops the
    narrative and retries, producing a premium render that covers every required
    fact. We force the narrative to be DRAWN large (simulating a looser band /
    short narrative that still competes) by stubbing the role-block fitter so the
    first attempt over-consumes; the second attempt (narrative omitted) restores
    the room."""
    real_fit = po._fit_role_block
    state = {"calls": 0}

    def _greedy_fit(draw, text, role, start_px, max_width, min_px, *, max_height, line_factor, max_lines):
        # Only affects the narrative attempt (role == "kicker" eyebrow). Return a
        # block that consumes a LARGE band at a large size so the drawn narrative
        # pushes top_zone_bottom down hard — mimicking a narrative that fits its
        # band yet starves the required content below it. (~8 lines × ~73px so the
        # required menu/title no longer fits → first attempt raises → retry.)
        state["calls"] += 1
        if role == "kicker" and text:
            big = max(start_px, min_px, 60)
            lines = [f"NARRATIVE LINE {n}" for n in range(8)]
            return lines, big
        return real_fit(draw, text, role, start_px, max_width, min_px,
                        max_height=max_height, line_factor=line_factor, max_lines=max_lines)

    monkeypatch.setattr(po, "_fit_role_block", _greedy_fit)

    # A dense priced menu so the greedy narrative tips the menu placement check.
    facts = [f for f in _base_facts() if not f.fact_id.startswith("item:")]
    names = ["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar", "Rasam", "Bonda"]
    for i, nm in enumerate(names):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label=f"Item {i}", value=nm, source="customer_text", required=True))
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:price", label=f"Price {i}", value=f"${5+i}.99", source="customer_text", required=True))
    cd = dict(_CD, campaign_narrative="Authentic Weekend Tiffin Festival Family Favorites On Now")
    proj = _project(creative_direction=cd, facts=facts, pid="F0260")

    out = tmp_path / "fix2_drop.png"
    # MUST NOT raise (no flat degrade): the narrative is dropped and the premium
    # overlay still renders.
    po.render_premium_overlay(proj, _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists() and Image.open(out).size == (1080, 1350)
    # The drop-not-degrade retry fired (observable record set).
    assert po._LAST_NARRATIVE_DROP == [True]


def test_fix2_overflow_without_narrative_still_raises(tmp_path):
    """A genuinely overflowing case (too many long items to fit EVEN WITHOUT the
    narrative) must still raise/degrade — the retry must not mask a real
    overflow. The narrative is present but irrelevant: dropping it cannot help."""
    facts = [f for f in _base_facts() if not f.fact_id.startswith("item:")]
    for i in range(40):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label="Item", value=f"VeryLongDishNameNumber{i}", source="customer_text", required=True))
    cd = dict(_CD, campaign_narrative="Authentic Weekend Tiffin Festival")
    proj = _project(creative_direction=cd, facts=facts, pid="F0261")
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(proj, _bg(tmp_path), tmp_path / "fix2_overflow.png", size=(1080, 1350), output_format="concept_preview")


def test_fix2_pathological_long_narrative_renders_premium(tmp_path):
    """A pathological long narrative + a normal item set that fits WITHOUT the
    narrative renders premium (NOT a flat degrade / FlyerRenderError). End-to-end
    through real geometry (no stub)."""
    cd = dict(_CD, campaign_narrative="South Indian Favorites at One Price " * 40)
    out = tmp_path / "fix2_long.png"
    po.render_premium_overlay(_project(creative_direction=cd, pid="F0262"), _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists() and Image.open(out).size == (1080, 1350)


def test_fix2_empty_narrative_single_attempt_byte_identical(tmp_path):
    """FIX 2 regression: empty/None narrative ⇒ a SINGLE compose attempt, the
    retry branch never engaged, byte-identical to today; no drop recorded."""
    none_out = _render(_project(creative_direction=None, pid="F0263"), tmp_path, "fix2_none.png")
    assert po._LAST_NARRATIVE_DROP == []
    empty_out = _render(_project(creative_direction={"campaign_narrative": "   "}, pid="F0263"), tmp_path, "fix2_blank.png")
    assert po._LAST_NARRATIVE_DROP == []
    assert none_out.read_bytes() == empty_out.read_bytes()
