"""Tests for premium_overlay font loader (Fix C Task 1).

TDD: these tests must FAIL before premium_overlay.py exists, then PASS after.
"""
from pathlib import Path
from agents.flyer import premium_overlay as po


def test_premium_font_roles_load():
    for role in ("masthead", "kicker", "title", "offer_price", "menu", "footer"):
        f = po._premium_font(role, 40)
        assert f is not None
        assert f.size == 40


def test_premium_font_falls_back_when_missing(monkeypatch):
    monkeypatch.setattr(po, "_FONT_DIR", Path("/nonexistent"))
    f = po._premium_font("title", 30)   # must not raise; falls back
    assert f is not None


def test_variable_font_weight_axis_differentiates():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (10, 10)); d = ImageDraw.Draw(img)
    masthead = po._premium_font("masthead", 80)   # Playfair 700
    title = po._premium_font("title", 80)          # Playfair 900 (same file, heavier)
    wm = d.textlength("LAKSHMI", font=masthead)
    wt = d.textlength("LAKSHMI", font=title)
    assert wt > wm, f"expected Black(900) wider than Bold(700): {wt} vs {wm}"


# ---------------------------------------------------------------------------
# Task 2: premium layout solver
# ---------------------------------------------------------------------------
from agents.flyer.premium_overlay import plan_premium_layout, PremiumLayout


def _items(n, price="$7.99"):
    return [(f"Item{i}", price) for i in range(n)]


def test_layout_two_items_uses_combo():
    L = plan_premium_layout(_items(2), shared_price=None)
    assert L.menu_mode == "combo"


def test_layout_six_shared_price_uses_namerows_and_seal():
    L = plan_premium_layout(_items(6, ""), shared_price="$7.99")
    assert L.menu_mode == "name_rows"
    assert L.offer_mode == "seal"


def test_layout_six_distinct_prices_uses_two_col():
    L = plan_premium_layout([("Dosa","$8.99"),("Idli","$5.99"),("Vada","$5.49"),
                             ("Upma","$5.99"),("Bonda","$4.99"),("Pakora","$4.49")], shared_price=None)
    assert L.menu_mode == "two_col"


def test_layout_sixteen_items_compact_and_floor_enforced():
    L = plan_premium_layout(_items(16), shared_price=None)
    assert L.menu_mode == "two_col_compact"
    assert L.menu_font_px >= L.min_font_px


# ---------------------------------------------------------------------------
# Task 3: gradient text-safe-zone scrims
# ---------------------------------------------------------------------------
from PIL import Image
from agents.flyer.premium_overlay import compose_scrims


def test_scrims_preserve_size_and_darken_bands():
    base = Image.new("RGB", (1080, 1350), (180, 180, 180))
    out = compose_scrims(base, top_frac=0.22, bottom_frac=0.32)
    assert out.size == (1080, 1350)
    cx = out.getpixel((540, 675))                 # untouched centre
    top = out.getpixel((540, 20)); bot = out.getpixel((540, 1330))
    assert sum(top) < sum(cx) and sum(bot) < sum(cx)   # bands darker than centre
