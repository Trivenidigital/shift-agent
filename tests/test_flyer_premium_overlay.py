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
