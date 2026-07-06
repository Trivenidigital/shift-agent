"""Premium Poster Template v1 — Slice A (deterministic composer foundation).

Structural contract tests over the composer's `layout_report` (fact-safety,
readability floors, hierarchy, eligibility, fallbacks) + flag-off / no-routing
proofs. PIL-dependent (flyer render), so named test_flyer_* — runs locally + on
the deploy smoke (send-path-ci has no PIL and excludes test_flyer*).
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")  # PIL absent in minimal CI -> skip (runs locally / VPS smoke)

from agents.flyer.premium_poster_v1 import (  # noqa: E402
    READABILITY_FLOOR_PX,
    compose_premium_poster_v1,
    poster_v1_enabled,
)

REPO = Path(__file__).resolve().parent.parent


def _fact(fact_id: str, value: str):
    return SimpleNamespace(fact_id=fact_id, value=value)


def _snack_fixture():
    # Street-snack / weekend-special class (the reference direction); 7 items.
    return [
        _fact("business_name", "Lakshmi's Kitchen"),
        _fact("campaign_title", "Weekend Snack Specials"),
        _fact("pricing_structure", "Any 2 snacks $9.99"),
        _fact("item:0:name", "Punugulu"),
        _fact("item:1:name", "Egg Bonda"),
        _fact("item:2:name", "Aloo Bonda"),
        _fact("item:3:name", "Veg Lollipop"),
        _fact("item:4:name", "Cut Mirchi"),
        _fact("item:5:name", "Onion Pakora"),
        _fact("item:6:name", "Punjabi Samosa"),
        _fact("schedule", "Saturday & Sunday"),
        _fact("location", "90 Brybar Dr St Johns FL"),
        _fact("contact_phone", "+17329837841"),
    ]


def _items(facts):
    return [f.value for f in facts if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")]


# ── eligibility + output ────────────────────────────────────────────────────

def test_composer_produces_output_for_snack_fixture():
    img, report = compose_premium_poster_v1(_snack_fixture())
    assert report["eligible"] is True
    assert img is not None
    assert img.size == (1080, 1350)


def test_ineligible_when_business_name_missing():
    facts = [f for f in _snack_fixture() if f.fact_id != "business_name"]
    img, report = compose_premium_poster_v1(facts)
    assert report["eligible"] is False
    assert img is None  # caller falls back to the existing path


def test_ineligible_when_too_few_items():
    facts = [f for f in _snack_fixture() if not f.fact_id.startswith("item:")]
    facts += [_fact("item:0:name", "Punugulu"), _fact("item:1:name", "Egg Bonda")]  # only 2
    img, report = compose_premium_poster_v1(facts)
    assert report["eligible"] is False and img is None


def test_ineligible_when_no_offer_or_price():
    facts = [f for f in _snack_fixture() if f.fact_id != "pricing_structure"]
    img, report = compose_premium_poster_v1(facts)
    assert report["eligible"] is False and img is None


# ── hierarchy + readability (the F0190 fixes) ───────────────────────────────

def test_headline_dominates_items_and_footer():
    _, r = compose_premium_poster_v1(_snack_fixture())
    f = r["fonts"]
    assert f["headline"] > f["menu"] > f["footer"]


def test_item_list_meets_readability_floor():
    _, r = compose_premium_poster_v1(_snack_fixture())
    assert r["item_px"] >= READABILITY_FLOOR_PX
    assert r["fonts"]["menu"] >= READABILITY_FLOOR_PX


def test_offer_badge_region_present_and_readable():
    _, r = compose_premium_poster_v1(_snack_fixture())
    assert "offer" in r["regions"]
    assert r["offer_price"]  # a price/offer string is placed
    assert r["fonts"]["offer_price"] >= READABILITY_FLOOR_PX


def test_required_poster_regions_present():
    _, r = compose_premium_poster_v1(_snack_fixture())
    for region in ("brand", "headline", "offer", "items", "footer"):
        assert region in r["regions"], region


# ── fact-safety (no weakening; never fabricate) ─────────────────────────────

def test_all_fixture_items_present():
    facts = _snack_fixture()
    _, r = compose_premium_poster_v1(facts)
    assert set(r["items"]) == set(_items(facts))


def test_no_fabricated_item_or_offer():
    facts = _snack_fixture()
    _, r = compose_premium_poster_v1(facts)
    fixture_items = set(_items(facts))
    assert set(r["items"]).issubset(fixture_items)  # never invents an item
    # every SEMANTIC token placed (price/word/number — ignoring cosmetic
    # separators like '·') is grounded in a locked fact value (fact-safe).
    allowed = " ".join(f.value for f in facts).casefold()
    for placed in r["placed_text"]:
        for tok in placed.casefold().split():
            if not any(ch.isalnum() for ch in tok):
                continue  # pure punctuation / separator — not a claim
            assert tok in allowed, f"ungrounded token {tok!r} in {placed!r}"


def test_price_only_offer_never_fabricates_a_label():
    # offer is a bare price with no label words -> the badge must NOT invent a
    # label like "SPECIAL". placed_text mirrors the canvas, so it must be absent.
    facts = [f for f in _snack_fixture() if f.fact_id != "pricing_structure"]
    facts.append(_fact("pricing_structure", "$9.99"))
    _, r = compose_premium_poster_v1(facts)
    assert r["eligible"] is True
    assert r["offer_price"] == "$9.99" and r["offer_label"] == ""
    # the fabricated default "SPECIAL" badge label must never be drawn. placed_text
    # mirrors the canvas, so an exact-token check catches it (a legit grounded
    # headline like "Snack Specials" is a different token and is fine).
    assert all(p.strip().upper() != "SPECIAL" for p in r["placed_text"])


def test_too_many_items_fails_closed_never_partial_menu():
    # A menu that cannot fit the readable zone at the floor must NEVER compose a
    # partial menu (dropped items are customer-supplied required facts). The
    # composer refuses -> the caller falls through to the existing render path.
    facts = [f for f in _snack_fixture() if not f.fact_id.startswith("item:")]
    facts += [_fact(f"item:{i}:name", f"Snack Item {i}") for i in range(24)]  # stress: 24 items
    img, r = compose_premium_poster_v1(facts)
    assert img is None and r["eligible"] is False
    assert "overflow" in r["reason"]


def test_fitting_item_count_still_composes_at_floor_or_above():
    facts = [f for f in _snack_fixture() if not f.fact_id.startswith("item:")]
    facts += [_fact(f"item:{i}:name", f"Item {i}") for i in range(10)]  # short names, fits 2-col
    img, r = compose_premium_poster_v1(facts)
    assert img is not None and r["eligible"] is True
    assert r["item_px"] >= READABILITY_FLOOR_PX
    assert set(r["items"]) == {f.value for f in facts if f.fact_id.startswith("item:")}
    assert r["items_overflow"] is False


def test_multi_price_offer_fails_closed():
    # "Was $12.99 now $8.99": a single dominant badge price would mutate the offer
    # (the OLD price as THE price). Must refuse, never compose.
    facts = [f for f in _snack_fixture() if f.fact_id != "pricing_structure"]
    facts.append(_fact("pricing_structure", "Was $12.99 now $8.99"))
    img, r = compose_premium_poster_v1(facts)
    assert img is None and r["eligible"] is False
    assert "multi-price" in r["reason"]


def test_two_for_price_multi_price_fails_closed():
    facts = [f for f in _snack_fixture() if f.fact_id != "pricing_structure"]
    facts.append(_fact("pricing_structure", "2 for $5 or 5 for $10"))
    img, r = compose_premium_poster_v1(facts)
    assert img is None and r["eligible"] is False


def test_long_business_name_shrinks_to_fit_never_clips():
    from PIL import Image, ImageDraw
    from agents.flyer.premium_poster_v1 import _premium_font, _text_w

    facts = [f for f in _snack_fixture() if f.fact_id != "business_name"]
    facts.append(_fact("business_name", "Sri Lakshmi Venkateswara Supermarket and Catering"))
    img, r = compose_premium_poster_v1(facts)
    assert img is not None and r["eligible"] is True
    draw = ImageDraw.Draw(Image.new("RGB", (1080, 1350)))
    name = "Sri Lakshmi Venkateswara Supermarket and Catering".upper()
    # The reported brand font must actually fit the full name inside the frame.
    assert _text_w(draw, name, _premium_font("masthead", r["fonts"]["brand"])) <= int(1080 * 0.94)
    assert name in r["placed_text"]  # the FULL name, never a clipped fragment


# ── flag + no routing (flag-off byte identity by construction) ──────────────

def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("FLYER_PREMIUM_POSTER_V1", raising=False)
    assert poster_v1_enabled() is False


def test_premium_poster_v1_dormant_by_default_in_render(monkeypatch):
    # The integration slice wires Premium Poster v1 into render.py, but it is DORMANT
    # by default: with FLYER_PREMIUM_POSTER_V1 unset, _premium_poster_v1_armed returns
    # False, so the render branch is never entered (byte-identical legacy).
    from types import SimpleNamespace
    from agents.flyer import render as render_mod
    monkeypatch.delenv("FLYER_PREMIUM_POSTER_V1", raising=False)
    assert render_mod._premium_poster_v1_armed(SimpleNamespace(customer_phone="+17329837841")) is False


def test_golden_artifact_committed():
    golden = REPO / "tests" / "fixtures" / "premium_poster_v1" / "snack_weekend_golden.png"
    assert golden.exists() and golden.stat().st_size > 0


# ── footer width-fit (regression: a dense footer clipped the trailing phone → QA
#    rejected it as an unverified phone number; 2026-07-01 live managed-path test) ─

def _long_footer_facts():
    # Dense footer: schedule + full address + full E.164 phone (the case that
    # overflowed 1080px at a fixed font size and clipped the last phone digit).
    # REPLACE the fixture's short footer fields (never append: `_value_of` returns
    # the FIRST match, so appended duplicates are dead and the dense values would
    # silently never be exercised — the 2026-07-02 review's CQ-3 finding).
    base = [f for f in _snack_fixture() if f.fact_id not in ("schedule", "location", "contact_phone")]
    return base + [
        _fact("schedule", "Saturday & Sunday, 11 AM - 8 PM, dine-in and takeout available"),
        _fact("location", "90 Brybar Drive, Suite 210, St Johns, Florida 32259"),
        _fact("contact_phone", "+17329837841"),
    ]


def test_fit_footer_dense_line_fits_width_no_clip():
    from PIL import Image, ImageDraw
    from agents.flyer.premium_poster_v1 import _fit_footer, _footer_line, _premium_font, _text_w

    draw = ImageDraw.Draw(Image.new("RGB", (1080, 1350)))
    footer = _footer_line(_long_footer_facts())
    assert "+17329837841" in footer
    max_w = int(1080 * 0.94)
    foot_px, lines = _fit_footer(draw, footer, max_w=max_w, max_px=max(24, int(1080 * 0.026)))
    # The dense footer must actually exercise the fit machinery: either it shrank
    # below the starting size or it wrapped (guards against fixture shadowing
    # silently reverting this test to the short-footer no-op it was before).
    assert foot_px < max(24, int(1080 * 0.026)) or len(lines) == 2
    # Every emitted line fits within the frame -> nothing clips off the edge.
    for line in lines:
        assert _text_w(draw, line, _premium_font("footer", foot_px)) <= max_w
    # The trailing contact/phone is never split mid-token: it lives intact on one line.
    assert any("+17329837841" in line for line in lines)
    # Fit stays readable (never below the footer floor).
    assert foot_px >= 22


def test_fit_footer_wraps_to_two_lines_at_floor_on_separator_only():
    # A footer too wide for one line even at the 22px floor must wrap into exactly
    # two lines, split ONLY at the deterministic '  ·  ' separator (never mid-token).
    from PIL import Image, ImageDraw
    from agents.flyer.premium_poster_v1 import _fit_footer, _premium_font, _text_w

    draw = ImageDraw.Draw(Image.new("RGB", (1080, 1350)))
    footer = ("Monday through Sunday, 10:30 AM - 9:30 PM, dine-in, takeout and catering"
              "  ·  4280 Southside Boulevard, Suite 1400, Jacksonville, Florida 32216"
              "  ·  +17329837841")
    max_w = int(1080 * 0.94)
    foot_px, lines = _fit_footer(draw, footer, max_w=max_w, max_px=28)
    assert len(lines) == 2
    assert foot_px == 22  # only wraps once the floor is reached
    for line in lines:
        assert _text_w(draw, line, _premium_font("footer", foot_px)) <= max_w
    # Reassembling the lines with the separator reproduces the exact footer: no
    # token was dropped, reordered, or split.
    assert "  ·  ".join(lines) == footer
    assert any("+17329837841" in line for line in lines)


def test_compose_dense_footer_phone_fully_rendered(tmp_path):
    # End-to-end: composing with a dense footer must place the FULL footer string
    # (incl. the complete phone) and report a font at which it fits the width.
    from PIL import Image, ImageDraw
    from agents.flyer.premium_poster_v1 import _footer_line, _premium_font, _text_w

    img, report = compose_premium_poster_v1(_long_footer_facts(), size=(1080, 1350))
    assert img is not None and report["eligible"] is True
    footer = _footer_line(_long_footer_facts())
    assert footer in report["placed_text"]          # full footer declared, phone intact
    draw = ImageDraw.Draw(Image.new("RGB", (1080, 1350)))
    foot_px = report["fonts"]["footer"]
    # The single-line footer (common case) must fit the frame at the reported size.
    if _text_w(draw, footer, _premium_font("footer", foot_px)) > int(1080 * 0.94):
        # Only acceptable when it wrapped: each ' · '-split segment fits.
        segs = footer.split("  ·  ")
        assert all(_text_w(draw, s, _premium_font("footer", foot_px)) <= int(1080 * 0.94) for s in segs)
