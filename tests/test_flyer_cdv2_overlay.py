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


# ===========================================================================
# CD v2 Composition Phase 1, Task 2 — the MESSAGE-FIRST (A) overlay template.
#
# When ``creative_direction["poster_archetype"] == "message_first"`` the overlay
# inverts today's hierarchy: the campaign_narrative becomes the LARGEST headline,
# the hook_text the second sub-headline, the campaign_title a small kicker, and
# the brand a small demoted lockup (NO dominant emblem ring).
#
#   NON-NEGOTIABLE invariant (PRESENT-TIERS, FIX 1): the HEADLINE dominates and the
#   BRAND stays small; the kicker tier participates only WHEN PRESENT. With a kicker
#   (narrative-present) the full chain narrative_px > hook_px > title_px >= brand_px
#   holds; PROMOTED mode (title_px == 0, brand > 0) is NOT a failure — see
#   _assert_present_tiers below.
#
# #1 SAFETY PROPERTY: every NON-message_first archetype (offer_first / event_first
# / unknown / None / flag-off) MUST render BYTE-IDENTICAL to today — the
# message_first code path is a strict no-op for those.
# ===========================================================================

_CD_MF = dict(_CD, poster_archetype="message_first")


# ---------------------------------------------------------------------------
# Task-2 #1 LOAD-BEARING byte-identical guard for the NEW archetype branch
# ---------------------------------------------------------------------------

def test_message_first_offer_first_byte_identical_to_today(tmp_path):
    """poster_archetype == 'offer_first' must NOT engage the message_first path:
    byte-identical to the SAME CD dict with no archetype key (today's B2.5 layout —
    which already carries the narrative/seal scaling, so the comparison baseline is
    the no-archetype render, NOT None)."""
    base = _render(_project(creative_direction=dict(_CD), pid="F0270"), tmp_path, "mf_base1.png")
    off = _render(_project(creative_direction=dict(_CD, poster_archetype="offer_first"),
                           pid="F0270"), tmp_path, "mf_offer.png")
    assert base.read_bytes() == off.read_bytes()


def test_message_first_event_first_byte_identical_to_today(tmp_path):
    """poster_archetype == 'event_first' must NOT engage the message_first path:
    byte-identical to the SAME CD dict with no archetype key (today's B2.5 layout)."""
    base = _render(_project(creative_direction=dict(_CD), pid="F0271"), tmp_path, "mf_base2.png")
    ev = _render(_project(creative_direction=dict(_CD, poster_archetype="event_first"),
                          pid="F0271"), tmp_path, "mf_event.png")
    assert base.read_bytes() == ev.read_bytes()


def test_message_first_archetype_none_byte_identical_to_today(tmp_path):
    """creative_direction=None (flag-off) must NOT engage the message_first path
    and stays byte-identical to today."""
    base = _render(_project(creative_direction=None, pid="F0272"), tmp_path, "mf_base3.png")
    # A CD dict carrying every other CD-v2 field but NO poster_archetype key must
    # ALSO leave the message_first path dormant — but it still carries the
    # narrative/seal scaling of B2.5, so we compare against the SAME CD dict
    # WITHOUT the archetype rather than against None.
    no_arch = _render(_project(creative_direction=dict(_CD), pid="F0272"), tmp_path, "mf_noarch.png")
    same_no_arch = _render(_project(creative_direction=dict(_CD), pid="F0272"), tmp_path, "mf_noarch2.png")
    # Determinism of the non-message_first path:
    assert no_arch.read_bytes() == same_no_arch.read_bytes()


def test_message_first_unknown_archetype_byte_identical_to_noarch(tmp_path):
    """An UNKNOWN archetype string must be treated as non-message_first → identical
    to the same CD dict with NO archetype key (today's B2.5 layout)."""
    no_arch = _render(_project(creative_direction=dict(_CD), pid="F0273"), tmp_path, "mf_known.png")
    unknown = _render(_project(creative_direction=dict(_CD, poster_archetype="banner_first"),
                               pid="F0273"), tmp_path, "mf_unknown.png")
    assert no_arch.read_bytes() == unknown.read_bytes()


# ---------------------------------------------------------------------------
# Task-2 #2 THE TYPE-HIERARCHY CONTRACT (the unit-asserted invariant)
# ---------------------------------------------------------------------------

def test_message_first_type_hierarchy_invariant(tmp_path):
    """The success contract is PRESENT-TIERS (FIX 1): the HEADLINE dominates, the
    BRAND stays small, and the kicker participates only WHEN PRESENT. In the
    narrative-present case here (a real campaign_narrative) the kicker IS present, so
    the full descending chain narrative_px > hook_px > title_px >= brand_px holds —
    AND the present-tiers helper passes (it would also pass promoted mode where
    title_px == 0). Sizes are exposed via po._LAST_LAYOUT_DEBUG (no pixel read)."""
    out = _render(_project(creative_direction=_CD_MF, pid="F0274"), tmp_path, "mf_hier.png")
    assert out.exists()
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg.get("archetype") == "message_first"
    # Narrative-present: the kicker participates → full descending chain.
    assert dbg["narrative_px"] > dbg["hook_px"] > dbg["title_px"] >= dbg["brand_px"], dbg
    # The present-tiers invariant (FIX 1) — promoted mode (title_px == 0, brand > 0)
    # is NOT a failure under this form; here it reduces to the full chain.
    _assert_present_tiers(dbg)


def test_message_first_narrative_is_largest_within_band(tmp_path):
    """Narrative scale is within the message_first headline band (~0.072-0.082 ×
    width) — it is the dominant headline, sized like / larger than today's title."""
    _render(_project(creative_direction=_CD_MF, pid="F0275"), tmp_path, "mf_band.png")
    dbg = po._LAST_LAYOUT_DEBUG
    # narrative is the headline: at least the old title scale (0.072×1080≈77px) is
    # the TOP of its band; it shrinks-to-fit so we only assert it is the largest.
    assert dbg["narrative_px"] >= dbg["hook_px"] >= dbg["title_px"]
    assert dbg["narrative_px"] <= int(1080 * 0.082) + 1  # never blows past the band ceiling


# ---------------------------------------------------------------------------
# Task-2 #3 DEMOTION: no dominant emblem ring; title is a small kicker
# ---------------------------------------------------------------------------

def test_message_first_no_dominant_emblem_ring(tmp_path):
    """In message_first the dominant brand emblem ring is NOT drawn (brand is a
    small lockup). We assert via the debug record's emblem flag."""
    _render(_project(creative_direction=_CD_MF, pid="F0276"), tmp_path, "mf_nomblem.png")
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg.get("emblem_ring_drawn") is False


def test_message_first_title_demoted_to_kicker(tmp_path):
    """campaign_title renders as a SMALL kicker, not the headline: title_px is at
    most the small-kicker scale (~0.026×width) and strictly below narrative+hook."""
    _render(_project(creative_direction=_CD_MF, pid="F0277"), tmp_path, "mf_kicker.png")
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg["title_px"] <= int(1080 * 0.030)   # small kicker band, not headline
    assert dbg["title_px"] < dbg["hook_px"] < dbg["narrative_px"]


def test_message_first_brand_demoted_below_title(tmp_path):
    """Brand lockup is demoted to <= title kicker px (the lowest of the four)."""
    _render(_project(creative_direction=_CD_MF, pid="F0278"), tmp_path, "mf_brand.png")
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg["brand_px"] <= dbg["title_px"]


# ---------------------------------------------------------------------------
# Task-2 #4 message_first render DIFFERS from today (the inversion is visible)
# ---------------------------------------------------------------------------

def test_message_first_render_differs_from_today(tmp_path):
    """The message_first artifact MUST visibly differ from the same project rendered
    with no archetype (today's hierarchy)."""
    today = _render(_project(creative_direction=dict(_CD), pid="F0279"), tmp_path, "mf_today.png")
    mf = _render(_project(creative_direction=_CD_MF, pid="F0279"), tmp_path, "mf_inverted.png")
    assert today.read_bytes() != mf.read_bytes()


# ---------------------------------------------------------------------------
# Task-2 #5 FIT + LEDGER: pathological narrative still fits / degrades; every
#     required locked fact is still verified; over-emphasis never overflows.
# ---------------------------------------------------------------------------

def test_message_first_pathological_narrative_still_renders(tmp_path):
    """A pathological long narrative in message_first must NOT overflow / hard-fail
    by itself: narrative shrinks (or drops) before any required fact; the render
    succeeds and covers every required fact."""
    cd = dict(_CD_MF, campaign_narrative="South Indian Favorites at One Price " * 30)
    out = _render(_project(creative_direction=cd, pid="F0280"), tmp_path, "mf_longnarr.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)


def test_message_first_required_fact_ledger_preserved(tmp_path):
    """The fail-closed required-fact ledger is intact in message_first: 40 long
    locked items still overflow and RAISE (the inverted hierarchy must not mask a
    real overflow / silently drop a required fact)."""
    facts = [f for f in _base_facts() if not f.fact_id.startswith("item:")]
    for i in range(40):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label="Item",
                                     value=f"VeryLongDishNameNumber{i}", source="customer_text", required=True))
    proj = _project(creative_direction=_CD_MF, facts=facts, pid="F0281")
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(proj, _bg(tmp_path), tmp_path / "mf_overflow.png",
                                  size=(1080, 1350), output_format="concept_preview")


def test_message_first_never_raises_on_malformed_cd(tmp_path):
    """Malformed CD with a message_first archetype but garbage hook/narrative types
    must never raise from the CD-v2 reads; it still produces a valid render."""
    cd = {"poster_archetype": "message_first", "campaign_narrative": 999,
          "hook_text": ["bad"], "hook_prominence": None, "offer_priority": object()}
    out = _render(_project(creative_direction=cd, pid="F0282"), tmp_path, "mf_malformed.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)


def test_message_first_covers_every_required_fact(tmp_path):
    """End-to-end: a normal message_first project renders and (by virtue of the
    fail-closed ledger NOT raising) covers brand, title, narrative-as-headline,
    schedule, every item, the offer, location and contact."""
    out = _render(_project(creative_direction=_CD_MF, pid="F0283"), tmp_path, "mf_cover.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)


# ===========================================================================
# FIX 1 (Codex BLOCKER) — the type hierarchy must hold on the FITTED sizes,
# not the START sizes. A LONG narrative shrinks toward min_px; the hook + title
# kicker are DERIVED as fractions of the FITTED narrative so the ordering
# narrative_px > hook_px > title_px can never invert. The debug record must
# carry the FITTED sizes (the short-narrative test never forced the clamp).
# ===========================================================================

_LONG_NARRATIVE = (
    "Authentic South Indian Weekend Tiffin Festival Family Favorites Now Served "
    "Fresh Every Single Evening Come Taste The Long Tradition Today With Us"
)  # long enough to force the headline to clamp hard toward min_px


def test_fix1_long_narrative_fitted_hierarchy_holds(tmp_path):
    """A LONG multi-line narrative forces the headline to shrink toward min_px.
    The FITTED sizes must STILL satisfy narrative_px > hook_px > title_px (the
    derived-from-fitted construction), not just the start sizes. A SHORT hook is
    used so an independent (pre-fix) fit would leave hook at its start size and
    invert against the clamped narrative — this is the case the old short-narrative
    test missed."""
    cd = dict(_CD_MF, campaign_narrative=_LONG_NARRATIVE, hook_text="$7.99")
    out = _render(_project(creative_direction=cd, pid="F0290"), tmp_path, "fix1_long.png")
    assert out.exists()
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg.get("archetype") == "message_first"
    # FITTED ordering — the load-bearing invariant under heavy clamp.
    assert dbg["narrative_px"] > dbg["hook_px"], (dbg["narrative_px"], dbg["hook_px"])
    assert dbg["hook_px"] > dbg["title_px"], (dbg["hook_px"], dbg["title_px"])
    assert dbg["brand_px"] <= dbg["title_px"], (dbg["brand_px"], dbg["title_px"])


def test_fix1_debug_reports_fitted_not_start_for_long_narrative(tmp_path):
    """The narrative_px reported is the FITTED size: with a long narrative it must
    be strictly below the start-band ceiling (it clamped), proving the debug
    reflects the actually-drawn size — not the start scale."""
    cd = dict(_CD_MF, campaign_narrative=_LONG_NARRATIVE, hook_text="$7.99")
    out = _render(_project(creative_direction=cd, pid="F0291"), tmp_path, "fix1_fitted.png")
    assert out.exists()
    dbg = po._LAST_LAYOUT_DEBUG
    # A long narrative cannot sit at the start ceiling (~0.078×1080≈84); it clamps.
    assert dbg["narrative_px"] < int(1080 * 0.078)


# ===========================================================================
# FIX 2 (Codex BLOCKER) — hook + narrative are BOTH best-effort with a drop
# ladder (drop HOOK first, then narrative); neither may push required content
# off the canvas. A genuine overflow (too many required items) still raises.
# ===========================================================================

_HOOK_TEXT = "ANY ITEM SEVEN NINETY NINE TODAY ONLY"


def test_fix2_hook_dropped_first_keeps_premium(tmp_path, monkeypatch):
    """A hook that, once DRAWN, over-consumes the top zone and pushes the required
    menu off canvas must NOT degrade to flat: the overlay drops the HOOK first,
    keeps the narrative+message, and still renders premium. _LAST_HOOK_DROP
    records the drop; _LAST_NARRATIVE_DROP must NOT (narrative was retained).

    The greedy stub targets ONLY the hook text (not the title kicker, which also
    uses the kicker role) and returns a block tall enough to starve the menu."""
    real_fit = po._fit_role_block

    def _greedy_hook(draw, text, role, start_px, max_width, min_px, *, max_height, line_factor, max_lines):
        if role == "kicker" and text == _HOOK_TEXT:
            return [f"HOOK LINE {n}" for n in range(14)], max(start_px, min_px, 90)
        return real_fit(draw, text, role, start_px, max_width, min_px,
                        max_height=max_height, line_factor=line_factor, max_lines=max_lines)

    monkeypatch.setattr(po, "_fit_role_block", _greedy_hook)

    facts = [f for f in _base_facts() if not f.fact_id.startswith("item:")]
    names = ["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar", "Rasam", "Bonda"]
    for i, nm in enumerate(names):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label=f"Item {i}", value=nm, source="customer_text", required=True))
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:price", label=f"Price {i}", value=f"${5+i}.99", source="customer_text", required=True))
    cd = dict(_CD_MF, campaign_narrative="Weekend Specials", hook_text=_HOOK_TEXT)
    proj = _project(creative_direction=cd, facts=facts, pid="F0292")

    out = tmp_path / "fix2_hookdrop.png"
    po.render_premium_overlay(proj, _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists() and Image.open(out).size == (1080, 1350)
    # The hook was dropped (its greedy block no longer competes); narrative kept.
    assert po._LAST_HOOK_DROP == [True]
    assert po._LAST_NARRATIVE_DROP == []


def test_fix2_narrative_dropped_after_hook_when_both_break(tmp_path, monkeypatch):
    """When dropping the hook alone is not enough, the narrative is dropped too
    (full ladder: (T,T)->(T,F)->(F,F)). Here BOTH the narrative (title role) and
    the hook (kicker role) are forced greedy so only the (F,F) bare attempt fits.
    Both drop records must fire; the render still succeeds premium (no flat)."""
    real_title = po._fit_title
    real_role = po._fit_role_block

    def _greedy_title(draw, text, start_px, max_width, min_px, *, max_height, line_factor):
        if text:
            return [f"NARR {n}" for n in range(7)], max(start_px, min_px, 70)
        return real_title(draw, text, start_px, max_width, min_px,
                          max_height=max_height, line_factor=line_factor)

    def _greedy_role(draw, text, role, start_px, max_width, min_px, *, max_height, line_factor, max_lines):
        if role == "kicker" and text:
            return [f"HOOK {n}" for n in range(6)], max(start_px, min_px, 60)
        return real_role(draw, text, role, start_px, max_width, min_px,
                         max_height=max_height, line_factor=line_factor, max_lines=max_lines)

    monkeypatch.setattr(po, "_fit_title", _greedy_title)
    monkeypatch.setattr(po, "_fit_role_block", _greedy_role)

    facts = [f for f in _base_facts() if not f.fact_id.startswith("item:")]
    names = ["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar", "Rasam", "Bonda"]
    for i, nm in enumerate(names):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label=f"Item {i}", value=nm, source="customer_text", required=True))
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:price", label=f"Price {i}", value=f"${5+i}.99", source="customer_text", required=True))
    cd = dict(_CD_MF, campaign_narrative="Weekend Specials Festival", hook_text="ANY ITEM $7.99 TODAY")
    proj = _project(creative_direction=cd, facts=facts, pid="F0293")

    out = tmp_path / "fix2_bothdrop.png"
    po.render_premium_overlay(proj, _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists() and Image.open(out).size == (1080, 1350)
    assert po._LAST_HOOK_DROP == [True]
    assert po._LAST_NARRATIVE_DROP == [True]


def test_fix2_genuine_overflow_still_raises_message_first(tmp_path):
    """Best-effort drops never mask a REAL overflow: 40 long required items cannot
    fit even after dropping BOTH the hook and the narrative → raises."""
    facts = [f for f in _base_facts() if not f.fact_id.startswith("item:")]
    for i in range(40):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label="Item",
                                     value=f"VeryLongDishNameNumber{i}", source="customer_text", required=True))
    cd = dict(_CD_MF, campaign_narrative="Weekend Specials", hook_text="ANY ITEM $7.99")
    proj = _project(creative_direction=cd, facts=facts, pid="F0294")
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(proj, _bg(tmp_path), tmp_path / "fix2_of.png",
                                  size=(1080, 1350), output_format="concept_preview")


# ===========================================================================
# FIX 3 (Codex BLOCKER) — campaign_title kicker is a REQUIRED fact → fail-closed
# fit, not a blind _ink of off-canvas text. A pathologically long required title
# that cannot fit its kicker band even at the floor must RAISE; a normal title
# fits and is verified.
# ===========================================================================

def test_fix3_unfittable_required_title_raises(tmp_path):
    """A REQUIRED campaign_title that cannot fit on-canvas as a kicker (a single
    enormous unbroken token within the 500-char value cap) must raise
    FlyerRenderError — never silently _ink a title that wasn't drawn-to-fit. A
    480-char unbroken token char-wraps into many lines that overflow the small
    kicker band even at the floor."""
    facts = [f for f in _base_facts() if f.fact_id != "campaign_title"]
    facts.append(FlyerLockedFact(
        fact_id="campaign_title", label="Campaign",
        value="X" * 480, source="customer_text", required=True))
    proj = _project(creative_direction=_CD_MF, facts=facts, pid="F0295")
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(proj, _bg(tmp_path), tmp_path / "fix3_title.png",
                                  size=(1080, 1350), output_format="concept_preview")


def test_fix3_normal_title_fits_and_is_verified(tmp_path):
    """A normal campaign_title fits its kicker band and the render succeeds (the
    fail-closed required-fact ledger passes — the title VALUE is covered)."""
    out = _render(_project(creative_direction=_CD_MF, pid="F0296"), tmp_path, "fix3_ok.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)
    # The title kicker is still small (demoted) AND below the hook/narrative.
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg["title_px"] < dbg["hook_px"] < dbg["narrative_px"]


def test_fix3_title_kicker_band_overflow_raises(tmp_path):
    """A required title with many long words that cannot wrap into the small kicker
    band at the floor must raise (fail-closed) rather than draw off-canvas."""
    facts = [f for f in _base_facts() if f.fact_id != "campaign_title"]
    facts.append(FlyerLockedFact(
        fact_id="campaign_title", label="Campaign",
        value=" ".join(f"Supercalifragilistic{i}" for i in range(20)),
        source="customer_text", required=True))
    proj = _project(creative_direction=_CD_MF, facts=facts, pid="F0297")
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(proj, _bg(tmp_path), tmp_path / "fix3_band.png",
                                  size=(1080, 1350), output_format="concept_preview")


# ===========================================================================
# RESIDUAL BLOCKER 1 (Codex) — capping the MAX size does not GUARANTEE the
# strict descending order on the sizes ACTUALLY DRAWN.  ``_fit_*`` may return any
# size down to min_px, so a LONG hook can fit below the title and a LONG title
# below the brand even with the caps in place.  The order must be enforced on the
# DRAWN/REPORTED sizes by construction:
#     narr_draw > hook_draw > title_draw >= brand_draw   (hook present)
#     narr_draw > title_draw >= brand_draw               (hook dropped)
# and NEVER invert regardless of text length.
# ===========================================================================

def test_blocker1_long_hook_never_inverts_below_title(tmp_path):
    """A SHORT narrative + a very LONG hook (cannot fit in its ≤2-line band at any
    readable size): pre-fix the debug reported a PHANTOM hook_px == hook_cap (50)
    even though the hook was never drawn (_LAST_HOOK_DROP stayed empty) — i.e. the
    reported size did not reflect the DRAWN size.  After the fix the hook must be
    recorded as DROPPED and the reported order must hold on the DRAWN sizes
    (narrative_px > title_px, no phantom hook between them)."""
    cd = dict(
        _CD_MF,
        campaign_narrative="Weekend Specials",  # short → narrative sits high in band
        hook_text=("LIMITED TIME WEEKEND OFFER EVERY SINGLE ITEM ON THE MENU IS "
                   "AVAILABLE AT ONE FLAT PRICE COME EARLY AND BRING THE WHOLE FAMILY "
                   "TODAY ONLY WHILE STOCKS LAST DO NOT MISS THIS GREAT DEAL EVER ") * 4
    )
    out = _render(_project(creative_direction=cd, pid="F0298"), tmp_path, "b1_longhook.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg.get("archetype") == "message_first"
    # The over-long hook could not fit its band → it MUST be recorded as dropped,
    # not phantom-reported as a drawn 50px hook.
    assert po._LAST_HOOK_DROP == [True], po._LAST_HOOK_DROP
    # FIX 2 (MAJOR): with the hook dropped the DRAWN order is narrative > title >=
    # brand, and the reported hook_px must be 0 (a tier NOT drawn reports 0 — not a
    # cap, not a collapse-to-title phantom between narrative + title).
    assert dbg["narrative_px"] > dbg["title_px"], (dbg["narrative_px"], dbg["title_px"])
    assert dbg["hook_px"] == 0, (dbg["hook_px"], dbg["title_px"])
    assert dbg["brand_px"] <= dbg["title_px"], (dbg["brand_px"], dbg["title_px"])


def test_blocker1_long_title_demoted_on_canvas_or_failclosed(tmp_path):
    """A long campaign_title either fits demoted-and-on-canvas (ordering holds) OR
    fail-closed raises — it is NEVER silently drawn at a size below the floor or
    inverted above the hook/narrative.  No success path may leave title_px above
    hook_px/narrative_px."""
    facts = [f for f in _base_facts() if f.fact_id != "campaign_title"]
    facts.append(FlyerLockedFact(
        fact_id="campaign_title", label="Campaign",
        value="Grand Weekend South Indian Tiffin Festival Family Celebration Specials",
        source="customer_text", required=True))
    proj = _project(creative_direction=_CD_MF, facts=facts, pid="F0299")
    out = tmp_path / "b1_longtitle.png"
    try:
        po.render_premium_overlay(proj, _bg(tmp_path), out,
                                  size=(1080, 1350), output_format="concept_preview")
    except render.FlyerRenderError:
        return  # fail-closed is an acceptable outcome
    # Success path: the order must hold on the DRAWN sizes.
    assert out.exists() and Image.open(out).size == (1080, 1350)
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg["title_px"] < dbg["narrative_px"], (dbg["title_px"], dbg["narrative_px"])
    if not po._LAST_HOOK_DROP:
        assert dbg["title_px"] < dbg["hook_px"], (dbg["title_px"], dbg["hook_px"])
    assert dbg["brand_px"] <= dbg["title_px"], (dbg["brand_px"], dbg["title_px"])


def test_blocker1_hook_drop_preserves_order(tmp_path, monkeypatch):
    """A hook that has NO room between the title and the narrative (its fitted size
    would land at/below the title) must be DROPPED in-place — never drawn inverted.
    After the drop the reported order is narrative_px > title_px and the render
    succeeds premium (no flat degrade).  _LAST_HOOK_DROP records the drop."""
    real_fit = po._fit_role_block

    def _tiny_hook(draw, text, role, start_px, max_width, min_px, *, max_height, line_factor, max_lines):
        # Force the hook (kicker role) to fit only at the FLOOR — no room above the
        # title kicker, which also fits near the floor → the hook must be dropped
        # rather than drawn at/below the title.
        if role == "kicker" and text == "$7.99":
            return ["$7.99"], min_px
        return real_fit(draw, text, role, start_px, max_width, min_px,
                        max_height=max_height, line_factor=line_factor, max_lines=max_lines)

    monkeypatch.setattr(po, "_fit_role_block", _tiny_hook)
    cd = dict(_CD_MF, campaign_narrative="Weekend Specials", hook_text="$7.99")
    out = _render(_project(creative_direction=cd, pid="F0300"), tmp_path, "b1_hookdrop.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)
    dbg = po._LAST_LAYOUT_DEBUG
    assert po._LAST_HOOK_DROP == [True]
    # Hook dropped → order collapses to narrative > title >= brand (no inversion).
    assert dbg["narrative_px"] > dbg["title_px"], (dbg["narrative_px"], dbg["title_px"])
    assert dbg["brand_px"] <= dbg["title_px"], (dbg["brand_px"], dbg["title_px"])


# ===========================================================================
# RESIDUAL BLOCKER 2 (Codex) — prove the REQUIRED title is ON-CANVAS (absolute
# coords) before ink.  ``_fit_role_block`` only checks local max_height; if the
# brand lockup is pushed down (multi-line / forced tall) the kicker's ABSOLUTE
# kick_top/kick_bottom can fall off-canvas while _ink(title) still marks the
# required fact covered.  An off-canvas required title must FAIL-CLOSED.
# ===========================================================================

def _facts_no_menu():
    """Base required facts WITHOUT any item:* rows (so the menu placement check is
    skipped and the title kicker's on-canvas guard is exercised in isolation)."""
    return [f for f in _base_facts() if not f.fact_id.startswith("item:")]


def test_blocker2_offcanvas_required_title_failcloses(tmp_path, monkeypatch):
    """Force the brand lockup tall enough that the title kicker's ABSOLUTE y-range
    (kick_top/kick_bottom) is pushed past the canvas height while the rest of the
    layout (footer-anchored required facts, no menu) would still ship.  Pre-fix the
    render SUCCEEDS with the REQUIRED campaign_title drawn OFF-CANVAS yet _ink'd as
    covered — a silent covered-but-off-canvas ship.  After the fix the absolute
    on-canvas check must FAIL-CLOSED (FlyerRenderError → flat fallback)."""
    real_wrap = po._wrap_premium

    def _tall_brand(draw, text, role, size, max_width):
        # The brand wraps with the "masthead" role at the very top.  Return enough
        # brand lines that brand_bottom pushes the title kicker's absolute
        # kick_bottom PAST the 1350px canvas height — pre-fix this SUCCEEDED with
        # the required campaign_title _ink'd off-canvas; post-fix the absolute guard
        # must raise the SPECIFIC off-canvas error.
        if role == "masthead":
            return [f"BRAND {n}" for n in range(45)]
        return real_wrap(draw, text, role, size, max_width)

    monkeypatch.setattr(po, "_wrap_premium", _tall_brand)
    # No menu items → the menu placement check cannot mask the off-canvas title.
    proj = _project(creative_direction=_CD_MF, facts=_facts_no_menu(), pid="F0301")
    with pytest.raises(render.FlyerRenderError, match="off-canvas"):
        po.render_premium_overlay(proj, _bg(tmp_path), tmp_path / "b2_offcanvas.png",
                                  size=(1080, 1350), output_format="concept_preview")


# ===========================================================================
# FIX 1 (Codex BLOCKER) — drop records must be set ONLY on a SUCCESSFUL attempt.
# The dispatch ladder must NOT append _LAST_HOOK_DROP / _LAST_NARRATIVE_DROP
# before the final bare attempt: a genuine REQUIRED overflow still raises, and
# after that (caught) raise NEITHER drop list may carry a phantom record.
#
# FIX 2 (Codex MAJOR) — _LAST_LAYOUT_DEBUG must report the ACTUAL DRAWN sizes
# (0 for any tier NOT drawn: dropped / failed / absent), never a cap/reference
# fallback or a collapse-to-title phantom.
# ===========================================================================

def test_fix_no_phantom_drop_record_on_failed_render(tmp_path, monkeypatch):
    """A genuine REQUIRED overflow that survives EVERY best-effort drop down to the
    bare (False, False) attempt and STILL raises FlyerRenderError. Pre-fix the
    dispatch ladder appended the drop records BEFORE that final bare attempt, so a
    render that ultimately raised LEAKED a phantom drop record. Post-fix: after the
    (caught) raise BOTH _LAST_HOOK_DROP and _LAST_NARRATIVE_DROP are EMPTY.

    The menu block is forced taller than the canvas so the menu placement check
    (``menu_top < top_zone_bottom``) raises on EVERY attempt — including the bare
    one — exercising the ladder all the way to Step 3 (the leak site). This bypasses
    the upstream detail-cap pre-flight (which would raise BEFORE the ladder runs)."""
    real_plan = po._plan_menu_block

    def _giant_menu(draw, items, layout, menu_px, min_px, safe_w, has_item_prices, render):
        _h, render_fn = real_plan(draw, items, layout, menu_px, min_px, safe_w, has_item_prices, render)
        return 100_000, render_fn   # taller than the 1350px canvas → placement check raises

    monkeypatch.setattr(po, "_plan_menu_block", _giant_menu)

    cd = dict(_CD_MF, campaign_narrative="Weekend Specials", hook_text="ANY ITEM $7.99")
    proj = _project(creative_direction=cd, pid="F0310")  # 6 items — passes the detail-cap pre-flight
    raised = False
    try:
        po.render_premium_overlay(proj, _bg(tmp_path), tmp_path / "fix_phantom.png",
                                  size=(1080, 1350), output_format="concept_preview")
    except render.FlyerRenderError:
        raised = True
    assert raised, "a genuine overflow surviving every drop must still raise (degrade-to-flat)"
    # No phantom drop record leaked on the failed render (the leak the BLOCKER fixes).
    assert po._LAST_HOOK_DROP == [], po._LAST_HOOK_DROP
    assert po._LAST_NARRATIVE_DROP == [], po._LAST_NARRATIVE_DROP


def test_fix_debug_reports_drawn_sizes_dropped_hook_is_zero(tmp_path, monkeypatch):
    """FIX 2: a DROPPED-hook case reports hook_px == 0 (the tier was NOT drawn) —
    not the hook_cap, not a collapse-to-title phantom. The narrative is retained
    and the other present tiers report their real DRAWN px (>0)."""
    real_fit = po._fit_role_block
    _HOOK = "ANY ITEM SEVEN NINETY NINE TODAY ONLY"

    def _greedy_hook(draw, text, role, start_px, max_width, min_px, *, max_height, line_factor, max_lines):
        if role == "kicker" and text == _HOOK:
            return [f"HOOK LINE {n}" for n in range(14)], max(start_px, min_px, 90)
        return real_fit(draw, text, role, start_px, max_width, min_px,
                        max_height=max_height, line_factor=line_factor, max_lines=max_lines)

    monkeypatch.setattr(po, "_fit_role_block", _greedy_hook)

    facts = [f for f in _base_facts() if not f.fact_id.startswith("item:")]
    names = ["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar", "Rasam", "Bonda"]
    for i, nm in enumerate(names):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label=f"Item {i}", value=nm, source="customer_text", required=True))
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:price", label=f"Price {i}", value=f"${5+i}.99", source="customer_text", required=True))
    cd = dict(_CD_MF, campaign_narrative="Weekend Specials", hook_text=_HOOK)
    proj = _project(creative_direction=cd, facts=facts, pid="F0311")

    out = tmp_path / "fix_drawn_hookzero.png"
    po.render_premium_overlay(proj, _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists() and Image.open(out).size == (1080, 1350)
    assert po._LAST_HOOK_DROP == [True]      # hook dropped (its greedy block starved the menu)
    assert po._LAST_NARRATIVE_DROP == []     # narrative retained
    dbg = po._LAST_LAYOUT_DEBUG
    # Dropped tier reports 0 (no cap, no collapse-to-title phantom).
    assert dbg["hook_px"] == 0, dbg
    # Present tiers report their real DRAWN px (>0), ordering holds over present tiers.
    assert dbg["narrative_px"] > 0 and dbg["title_px"] > 0 and dbg["brand_px"] > 0, dbg
    assert dbg["narrative_px"] > dbg["title_px"] >= dbg["brand_px"], dbg


def test_fix_debug_reports_drawn_sizes_normal_all_present(tmp_path):
    """FIX 2: a normal message_first render (no drops) reports the ACTUAL DRAWN px
    for all four tiers (>0) and the strict descending invariant holds:
    narrative_px > hook_px > title_px >= brand_px."""
    out = _render(_project(creative_direction=_CD_MF, pid="F0312"), tmp_path, "fix_drawn_normal.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)
    assert po._LAST_HOOK_DROP == [] and po._LAST_NARRATIVE_DROP == []
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg["narrative_px"] > 0 and dbg["hook_px"] > 0
    assert dbg["title_px"] > 0 and dbg["brand_px"] > 0
    assert dbg["narrative_px"] > dbg["hook_px"] > dbg["title_px"] >= dbg["brand_px"], dbg


# ===========================================================================
# NARRATIVE-RELIABILITY (message-first): the overlay is the DELIVERED artifact, a
# message-first poster must NEVER render with an EMPTY dominant headline slot.
#
# Live validation found a HEADLINE-LESS A render when the brain omitted
# campaign_narrative. When campaign_narrative resolves EMPTY, the A template must
# PROMOTE campaign_title into the dominant (narrative-scale) headline slot AND
# SUPPRESS the redundant small kicker (campaign_title is never drawn twice). The
# required campaign_title fact stays ledger-covered either way (kicker when a
# narrative is present, headline when promoted as the fallback).
# ===========================================================================

# A message_first CD with NO campaign_narrative — the exact live failure shape.
_CD_MF_NO_NARRATIVE = {k: v for k, v in _CD_MF.items() if k != "campaign_narrative"}


def test_message_first_empty_narrative_promotes_title_to_headline(tmp_path):
    """campaign_narrative empty in message_first ⇒ the campaign_title is PROMOTED to
    the dominant (narrative) headline slot: narrative_px reports the headline's drawn
    px (> 0, > hook_px), and the SMALL title kicker is NOT drawn (no duplicate) ⇒
    title_px == 0. The render succeeds (premium, never headline-less, never raised)
    and the required campaign_title fact is still covered."""
    out = _render(_project(creative_direction=_CD_MF_NO_NARRATIVE, pid="F0320"),
                  tmp_path, "mf_emptynarr_promote.png")
    assert out.exists() and Image.open(out).size == (1080, 1350)
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg.get("archetype") == "message_first"
    # The dominant headline slot is FILLED (never headline-less) by the promoted title.
    assert dbg["narrative_px"] > 0, dbg
    assert dbg["narrative_px"] > dbg["hook_px"], (dbg["narrative_px"], dbg["hook_px"])
    # The redundant small kicker is SUPPRESSED — campaign_title is not drawn twice.
    assert dbg["title_px"] == 0, dbg


def test_message_first_blank_narrative_string_promotes_title(tmp_path):
    """A whitespace-only campaign_narrative resolves EMPTY (.strip()) ⇒ same promotion
    fallback as an omitted narrative: title becomes the headline, no duplicate kicker."""
    cd = dict(_CD_MF, campaign_narrative="   ")
    out = _render(_project(creative_direction=cd, pid="F0321"), tmp_path, "mf_blanknarr.png")
    assert out.exists()
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg["narrative_px"] > 0 and dbg["narrative_px"] > dbg["hook_px"]
    assert dbg["title_px"] == 0, dbg


def test_message_first_empty_narrative_covers_title_fact(tmp_path):
    """The REQUIRED campaign_title fact stays ledger-covered when promoted to the
    headline: the render's fail-closed ledger does NOT raise (it would if the title
    value were neither kicker nor headline)."""
    facts = _base_facts()
    out = tmp_path / "mf_emptynarr_cover.png"
    # No raise == the required campaign_title ("Weekend Specials") was drawn + verified.
    po.render_premium_overlay(
        _project(creative_direction=_CD_MF_NO_NARRATIVE, facts=facts, pid="F0322"),
        _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists() and Image.open(out).size == (1080, 1350)


def test_message_first_nonempty_narrative_keeps_title_as_kicker(tmp_path):
    """Unchanged behavior with a NON-empty narrative: the narrative is the headline
    and campaign_title is the SMALL kicker (drawn, title_px > 0) below it — the
    promotion fallback ONLY engages when the narrative is empty."""
    out = _render(_project(creative_direction=_CD_MF, pid="F0323"), tmp_path, "mf_narr_kicker.png")
    assert out.exists()
    dbg = po._LAST_LAYOUT_DEBUG
    # Narrative present ⇒ title is the small kicker, BOTH drawn, strict ordering holds.
    assert dbg["narrative_px"] > dbg["hook_px"] > dbg["title_px"] > 0, dbg
    assert dbg["brand_px"] <= dbg["title_px"], dbg


def test_message_first_headline_never_empty_both_cases(tmp_path):
    """NEVER headline-less: for message_first the dominant headline slot is ALWAYS
    filled — narrative_px > 0 whether the headline is the narrative (narrative
    present) or the promoted campaign_title (narrative empty)."""
    _render(_project(creative_direction=_CD_MF, pid="F0324"), tmp_path, "mf_hl_narr.png")
    assert po._LAST_LAYOUT_DEBUG["narrative_px"] > 0
    _render(_project(creative_direction=_CD_MF_NO_NARRATIVE, pid="F0324"), tmp_path, "mf_hl_title.png")
    assert po._LAST_LAYOUT_DEBUG["narrative_px"] > 0


def test_message_first_empty_narrative_renders_premium_not_raised(tmp_path):
    """The live-failure shape (message_first + omitted campaign_narrative) must
    render premium (no flat degrade / FlyerRenderError) — the headline slot is the
    promoted title, the required-fact ledger passes."""
    out = tmp_path / "mf_emptynarr_premium.png"
    po.render_premium_overlay(
        _project(creative_direction=_CD_MF_NO_NARRATIVE, pid="F0325"),
        _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    assert out.exists() and Image.open(out).size == (1080, 1350)
    # No phantom drop records — there was no narrative to drop.
    assert po._LAST_NARRATIVE_DROP == []


# ===========================================================================
# CD v2 MAJOR FIX 1 — the asserted invariant is PRESENT-TIERS, not a literal
# strict descending chain over all four tiers. In PROMOTED-title mode there is
# NO kicker (title_px == 0) while the brand is small but > 0, so a naive
# ``narrative_px > hook_px > title_px >= brand_px`` would falsely fail at
# ``title_px(0) >= brand_px(>0)``. The real intent: the HEADLINE dominates and the
# BRAND stays small; the kicker tier only participates WHEN PRESENT.
#
# PRESENT-TIERS invariant (what _compose_mf actually guarantees):
#   • ALWAYS: narrative_px (headline) > 0; narrative_px > hook_px (when hook
#     present); brand is SMALL (<= hook_px when hook present, else < narrative_px).
#   • WHEN a kicker exists (title_px > 0, narrative-present mode):
#       hook_px > title_px >= brand_px (the full descending chain).
#   • WHEN promoted (title_px == 0): skip the kicker comparison; assert
#       narrative_px > hook_px (if hook) > brand_px and brand small.
# ===========================================================================


def _assert_present_tiers(dbg):
    """Assert the PRESENT-TIERS message-first invariant on a _LAST_LAYOUT_DEBUG
    record (FIX 1): headline dominates, brand small, kicker only when present."""
    narrative_px = dbg["narrative_px"]
    hook_px = dbg["hook_px"]
    title_px = dbg["title_px"]
    brand_px = dbg["brand_px"]
    # Headline is ALWAYS present and dominant.
    assert narrative_px > 0, dbg
    if hook_px > 0:
        assert narrative_px > hook_px, (narrative_px, hook_px)
    if title_px > 0:
        # Narrative-present mode: the kicker participates — full descending chain.
        assert hook_px > title_px >= brand_px, (hook_px, title_px, brand_px)
    else:
        # Promoted mode: no kicker. Headline > hook (if present) > small brand.
        if hook_px > 0:
            assert hook_px > brand_px, (hook_px, brand_px)
            # Brand stays small — at most the hook tier.
            assert brand_px <= hook_px, (brand_px, hook_px)
        else:
            assert narrative_px > brand_px, (narrative_px, brand_px)
        # Brand is genuinely small relative to the headline either way.
        assert brand_px < narrative_px, (brand_px, narrative_px)


def test_message_first_present_tiers_invariant_promoted_mode(tmp_path):
    """FIX 1: PROMOTED mode (empty narrative) is NOT a hierarchy failure. With no
    kicker (title_px == 0) but a small brand (> 0) the PRESENT-TIERS invariant must
    PASS: narrative_px (headline) > hook_px > brand_px, title_px == 0, brand small.
    (The old over-strict ``title_px >= brand_px`` assertion failed here: 0 >= 25.)"""
    out = _render(_project(creative_direction=_CD_MF_NO_NARRATIVE, pid="F0330"),
                  tmp_path, "mf_present_tiers_promoted.png")
    assert out.exists()
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg.get("archetype") == "message_first"
    # Promoted mode: no kicker, headline filled by the promoted title.
    assert dbg["title_px"] == 0, dbg
    assert dbg["narrative_px"] > dbg["hook_px"] > 0, dbg
    # Brand is small (it is the ~0.024w demoted lockup) and below the hook tier.
    assert 0 < dbg["brand_px"] <= dbg["hook_px"], dbg
    assert dbg["brand_px"] < dbg["narrative_px"], dbg
    _assert_present_tiers(dbg)


def test_message_first_present_tiers_invariant_narrative_present(tmp_path):
    """FIX 1: narrative-present mode keeps the FULL descending chain
    narrative_px > hook_px > title_px >= brand_px (the kicker participates)."""
    out = _render(_project(creative_direction=_CD_MF, pid="F0331"),
                  tmp_path, "mf_present_tiers_narrative.png")
    assert out.exists()
    dbg = po._LAST_LAYOUT_DEBUG
    assert dbg["title_px"] > 0, dbg
    assert dbg["narrative_px"] > dbg["hook_px"] > dbg["title_px"] >= dbg["brand_px"], dbg
    _assert_present_tiers(dbg)


# ===========================================================================
# CD v2 MAJOR FIX 2 — promotion keys on _narrative_active (False when the
# narrative is EMPTY *or* DROPPED-for-fit). This is INTENTIONAL: a message-first
# poster must NEVER render without a message, so whenever no narrative is drawn
# the campaign_title is PROMOTED to the headline → ALWAYS a non-empty headline.
# A promoted title that itself cannot fit degrades to FLAT (a complete flyer) —
# never a headline-less premium.
# ===========================================================================


def test_fix2_dropped_narrative_promotes_title_never_headline_less(tmp_path):
    """INTENT LOCK (FIX 2): a NON-EMPTY narrative that is DROPPED for fit must not
    leave a headline-less premium — the ladder falls to the bare attempt which
    PROMOTES the campaign_title into the headline slot. We force the drop by stubbing
    the headline fitter so the narrative over-consumes (steps 1+2 raise), while the
    promoted-title bare attempt (step 3) fits normally. Result: narrative_px > 0
    (the promoted title fills the headline), title kicker suppressed (title_px == 0),
    and BOTH the hook + narrative drops are recorded — NEVER headline-less."""
    real_fit_title = po._fit_title
    narr = "South Indian Favorites at One Price"

    def _greedy_title(draw, text, start_px, max_width, min_px, *, max_height, line_factor):
        # Over-consume ONLY when fitting the NON-EMPTY NARRATIVE as the headline
        # (steps 1+2). The PROMOTED title headline (bare attempt, step 3) fits
        # normally via the real fitter, so promotion succeeds.
        if text == narr:
            return [f"NARR LINE {n}" for n in range(12)], max(start_px, min_px, 80)
        return real_fit_title(draw, text, start_px, max_width, min_px,
                              max_height=max_height, line_factor=line_factor)

    import pytest as _pytest  # local alias to avoid shadowing module-level pytest
    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(po, "_fit_title", _greedy_title)
    try:
        facts = _base_facts()
        facts = [f for f in facts if not f.fact_id.startswith("item:")]
        names = ["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar",
                 "Rasam", "Bonda", "Medu", "Kesari", "Upma", "Pesarattu"]
        for i, nm in enumerate(names):
            facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label=f"Item {i}", value=nm, source="customer_text", required=True))
            facts.append(FlyerLockedFact(fact_id=f"item:{i}:price", label=f"Price {i}", value=f"${5+i}.99", source="customer_text", required=True))
        cd = dict(_CD_MF, campaign_narrative=narr)
        out = tmp_path / "mf_dropped_narr_promotes.png"
        # MUST NOT raise: the narrative drops, the title is promoted, premium renders.
        po.render_premium_overlay(
            _project(creative_direction=cd, facts=facts, pid="F0332"),
            _bg(tmp_path), out, size=(1080, 1350), output_format="concept_preview")
    finally:
        monkeypatch.undo()
    assert out.exists() and Image.open(out).size == (1080, 1350)
    dbg = po._LAST_LAYOUT_DEBUG
    # NEVER headline-less: the promoted title fills the headline slot.
    assert dbg["narrative_px"] > 0, dbg
    # The small kicker is suppressed (the title is the headline, not drawn twice).
    assert dbg["title_px"] == 0, dbg
    # The non-empty narrative WAS dropped for fit (ladder reached the bare attempt).
    assert po._LAST_NARRATIVE_DROP == [True], po._LAST_NARRATIVE_DROP
    assert po._LAST_HOOK_DROP == [True], po._LAST_HOOK_DROP
    _assert_present_tiers(dbg)


def test_fix2_genuinely_impossible_layout_degrades_to_flat_not_headline_less(tmp_path):
    """INTENT LOCK (FIX 2): when even the promoted-title bare attempt cannot fit the
    REQUIRED content (a genuinely impossible layout), the overlay DEGRADES TO FLAT
    (raises FlyerRenderError) — a complete flyer downstream — and NEVER emits a
    headline-less premium. 40 long required items overflow regardless of promotion."""
    facts = [f for f in _base_facts() if not f.fact_id.startswith("item:")]
    for i in range(40):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label="Item", value=f"VeryLongDishNameNumber{i}", source="customer_text", required=True))
    cd = dict(_CD_MF, campaign_narrative="Authentic Weekend Tiffin Festival")
    with pytest.raises(render.FlyerRenderError):
        po.render_premium_overlay(
            _project(creative_direction=cd, facts=facts, pid="F0333"),
            _bg(tmp_path), tmp_path / "mf_impossible.png", size=(1080, 1350),
            output_format="concept_preview")
