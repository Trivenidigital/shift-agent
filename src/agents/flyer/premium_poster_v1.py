"""Premium Poster Template v1 — Slice A: deterministic composer foundation.

Builds a premium street-food/menu POSTER deterministically with Pillow from
LOCKED FACTS ONLY (fact-safe by construction — never invents an item, price,
name, date, or location). It directly targets the F0190 failures: weak
hierarchy, a tiny unreadable item list, a non-dominant offer, and poor
WhatsApp-preview readability.

Slice A scope (flag-gated, default OFF, NO production routing):
  fixed poster regions · brand/header band · bold dominant headline ·
  large framed offer/price badge · LARGE readable auto-columned item-list block ·
  footer/contact · warm/dark text-safe background with a deterministic fallback
  when no food image is supplied.

NOT in Slice A: accents (chili/fire/smoke), routing into render.py, rollout,
model-policy changes, Hermes critique. The existing render path is untouched and
byte-identical when the flag is off (nothing wires this composer yet).

Reuses `premium_overlay._premium_font` (vendored Playfair/Montserrat/Cormorant);
does not modify premium_overlay.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Optional, Sequence

from agents.flyer.premium_overlay import _FONT_DIR_CANDIDATES, _premium_font

# Readability floor (px) for item text at the 1080-wide poster. At a ~400px
# WhatsApp preview that downscales to ~13px — the line below which menu text
# becomes the unreadable F0190 failure. The composer NEVER renders items below it.
READABILITY_FLOOR_PX = 34

_FLAG = "FLYER_PREMIUM_POSTER_V1"
_PRICE_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")

# Warm high-contrast palette (deterministic).
_BG_TOP = (42, 20, 16)
_BG_BOTTOM = (12, 6, 4)
_GLOW = (150, 86, 30)
_WHITE = (255, 255, 255)
_GOLD = (242, 193, 78)
_CREAM = (255, 233, 176)
_PANEL = (18, 10, 8)


def poster_v1_enabled() -> bool:
    """Whether Premium Poster Template v1 is enabled. Default OFF. Slice A does
    NOT consult this in render.py (no routing) — it exists for later slices."""
    return os.environ.get(_FLAG, "0").strip().lower() in ("1", "true", "yes", "on")


# ── fact reads (duck-typed, like CCA) ───────────────────────────────────────

def _val(fact) -> str:
    v = getattr(fact, "value", "")
    return v.strip() if isinstance(v, str) else ""


def _fid(fact) -> str:
    f = getattr(fact, "fact_id", "")
    return f if isinstance(f, str) else ""


def _value_of(fact_id: str, facts) -> str:
    for f in facts or ():
        if _fid(f) == fact_id:
            return _val(f)
    return ""


def _item_names(facts) -> list[str]:
    out = []
    for f in facts or ():
        fid = _fid(f)
        if fid.startswith("item:") and fid.endswith(":name") and _val(f):
            out.append(_val(f))
    return out


def _offer(facts) -> tuple[str, str]:
    """(label, price) from pricing_structure or the first offer fact. Grounded."""
    text = _value_of("pricing_structure", facts) or _value_of("offer:0", facts) or _value_of("offer", facts)
    if not text:
        return ("", "")
    m = _PRICE_RE.search(text)
    price = m.group(0).replace(" ", "") if m else ""
    label = (text[:m.start()] + text[m.end():] if m else text).strip(" -–—:·,")
    return (label, price)


def _headline(facts) -> str:
    return _value_of("campaign_title", facts) or _value_of("pricing_structure", facts) or _value_of("business_name", facts)


def _footer_line(facts) -> str:
    parts = [_value_of("schedule", facts), _value_of("location", facts), _value_of("contact_phone", facts)]
    return "  ·  ".join(p for p in parts if p)


# ── fonts ───────────────────────────────────────────────────────────────────

def _headline_font(size: int):
    """Bold display headline (Montserrat-ExtraBold) for theatrical poster punch.
    The vendored Montserrat is a VARIABLE font whose default instance is light,
    so the weight axis is pinned to ExtraBold (800); static fonts raise and are
    ignored. Loaded directly so premium_overlay stays untouched."""
    from PIL import ImageFont
    for base in _FONT_DIR_CANDIDATES:
        p = base / "Montserrat-ExtraBold.ttf"
        if p.exists():
            try:
                f = ImageFont.truetype(str(p), size)
                try:
                    f.set_variation_by_axes([800])  # pin wght=ExtraBold
                except Exception:
                    pass
                return f
            except Exception:
                break
    return _premium_font("title", size)  # PlayfairDisplay-Black fallback


def _text_w(draw, text, font) -> int:
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0]


def _text_h(draw, text, font) -> int:
    b = draw.textbbox((0, 0), text, font=font)
    return b[3] - b[1]


# ── background ──────────────────────────────────────────────────────────────

def _fallback_background(size):
    """Deterministic warm/dark gradient + soft center glow (NOT flat black) — the
    text-safe background used when no food hero image is supplied."""
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new("RGB", size, _BG_BOTTOM)
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(_BG_TOP[0] * (1 - t) + _BG_BOTTOM[0] * t)
        g = int(_BG_TOP[1] * (1 - t) + _BG_BOTTOM[1] * t)
        b = int(_BG_TOP[2] * (1 - t) + _BG_BOTTOM[2] * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    # soft radial warm glow, upper-centre
    glow = Image.new("L", size, 0)
    gd = ImageDraw.Draw(glow)
    cx, cy, rad = w // 2, int(h * 0.34), int(w * 0.62)
    gd.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=120)
    glow = glow.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(rad // 3))
    warm = Image.new("RGB", size, _GLOW)
    img = Image.composite(warm, img, glow.point(lambda v: int(v * 0.5)))
    return img


def _load_food(food_image_path, size):
    from PIL import Image
    img = Image.open(str(food_image_path)).convert("RGB")
    # cover-fit
    w, h = size
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    img = img.resize((int(iw * scale), int(ih * scale)))
    left = (img.size[0] - w) // 2
    top = (img.size[1] - h) // 2
    return img.crop((left, top, left + w, top + h))


def _scrim(img):
    """Darken top + bottom for text legibility over any background."""
    from PIL import Image, ImageDraw
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(h):
        a = 0
        if y < h * 0.30:
            a = int(150 * (1 - y / (h * 0.30)))
        elif y > h * 0.52:
            a = int(190 * ((y - h * 0.52) / (h * 0.48)))
        od.line([(0, y), (w, y)], fill=(0, 0, 0, min(210, a)))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


# ── item-list block (the readability fix) ───────────────────────────────────

def _fit_item_block(draw, items, *, zone_w, zone_h):
    """Pick the LARGEST font (down to the floor) + column count so the items fit
    the zone. Never below READABILITY_FLOOR_PX. Returns (font_px, ncols, rows_fit,
    overflow)."""
    for px in range(48, READABILITY_FLOOR_PX - 1, -2):
        font = _premium_font("footer", px)  # Montserrat-SemiBold — clean + readable
        line_h = int(px * 1.5)
        for ncols in (1, 2):
            col_w = zone_w // ncols - int(px * 1.2)  # room for the bullet
            longest = max((_text_w(draw, "●  " + it, font) for it in items), default=0)
            if longest > col_w:
                continue
            rows_needed = (len(items) + ncols - 1) // ncols
            if rows_needed * line_h <= zone_h:
                return px, ncols, rows_needed, False
    # Floor reached and still doesn't fit: use floor + 2 cols, render as many rows
    # as fit (overflow) — NEVER below the floor, NEVER fabricate.
    px = READABILITY_FLOOR_PX
    font = _premium_font("footer", px)
    line_h = int(px * 1.5)
    rows_fit = max(1, zone_h // line_h)
    return px, 2, rows_fit, (rows_fit * 2 < len(items))


# ── composer ────────────────────────────────────────────────────────────────

def compose_premium_poster_v1(
    locked_facts: Sequence[object],
    *,
    food_image_path: Optional[str] = None,
    textless_check: Optional[Callable] = None,
    size: tuple = (1080, 1350),
):
    """Compose the v1 poster. Returns (PIL.Image | None, layout_report).

    Ineligible (missing business name / offer / <3 items) → (None, {eligible:False})
    so the caller falls back to the existing path. Never raises on a fact read.

    A supplied food image is used as the hero ONLY when it passes the
    `textless_check` (Slice C textless-safety gate): a callable(PIL.Image) → bool
    that is True when the image carries NO text/logo/price. Slice C1 is offline,
    so the real OCR-based check is INJECTED (the C2 caller wires the existing
    visual_qa OCR). FAIL-SAFE: a missing image, a load error, a check that
    returns False, OR a check that raises → the deterministic warm fallback
    background. Text/facts are ALWAYS composed deterministically from locked
    facts; nothing is ever read from the image.
    """
    from PIL import Image, ImageDraw

    business = _value_of("business_name", locked_facts)
    label, price = _offer(locked_facts)
    items = _item_names(locked_facts)
    headline = _headline(locked_facts)
    footer = _footer_line(locked_facts)

    if not business or not (price or label) or len(items) < 3:
        return None, {"eligible": False, "reason": "missing required facts (business / offer / >=3 items)"}

    w, h = size
    food_fallback_reason = ""
    if food_image_path:
        try:
            food = _load_food(food_image_path, size)
            safe = True
            check_error = False
            if textless_check is not None:
                try:
                    safe = bool(textless_check(food))
                except Exception:
                    safe = False        # cannot verify textless -> do NOT trust the image
                    check_error = True  # ...but distinguish an infra failure from text-found
            if safe:
                img = _scrim(food)
                background = "food"
            else:
                img = _fallback_background(size)
                background = "fallback"
                food_fallback_reason = "check_error" if check_error else "image_has_text"
        except Exception:
            img = _fallback_background(size)
            background = "fallback"
            food_fallback_reason = "image_load_failed"
    else:
        img = _fallback_background(size)
        background = "fallback"
        food_fallback_reason = "no_image"

    draw = ImageDraw.Draw(img)
    placed_text: list[str] = []
    regions: dict = {}
    fonts: dict = {}

    # ── brand band ──
    brand_px = max(40, int(w * 0.052))
    bf = _premium_font("masthead", brand_px)
    regions["brand"] = (0, int(h * 0.015), w, int(h * 0.085))
    _draw_center(draw, business.upper(), bf, cy=int(h * 0.058), w=w, fill=_GOLD)
    fonts["brand"] = brand_px
    placed_text.append(business.upper())

    # ── headline (bold, dominant, auto-fit up to 2 lines) ──
    head_px, head_lines = _fit_headline(draw, headline, max_w=int(w * 0.92), max_px=int(w * 0.115))
    hf = _headline_font(head_px)
    hy = int(h * 0.135)
    line_h = int(head_px * 1.06)
    for ln in head_lines:
        _draw_center(draw, ln, hf, cy=hy, w=w, fill=_WHITE, shadow=True)
        hy += line_h
    regions["headline"] = (0, int(h * 0.115), w, line_h * len(head_lines) + int(h * 0.02))
    fonts["headline"] = head_px
    placed_text.extend(head_lines)

    # ── offer badge (framed, large, dominant) ──
    badge_cx, badge_cy = int(w * 0.5), int(h * 0.50)
    badge_r, badge_drawn = _draw_offer_badge(draw, label=label, price=price, cx=badge_cx, cy=badge_cy, w=w)
    regions["offer"] = (badge_cx - badge_r, badge_cy - badge_r, badge_r * 2, badge_r * 2)
    offer_px = max(READABILITY_FLOOR_PX, int(w * 0.085))
    fonts["offer_price"] = offer_px
    placed_text.extend(badge_drawn)  # exactly what the badge drew (mirrors canvas)

    # ── item-list block (LARGE, readable, auto-columned) ──
    iz_x, iz_y = int(w * 0.07), int(h * 0.625)
    iz_w, iz_h = int(w * 0.86), int(h * 0.255)
    # panel
    _rounded_panel(img, (iz_x - 18, iz_y - 18, iz_x + iz_w + 18, iz_y + iz_h + 18), fill=_PANEL, alpha=200)
    draw = ImageDraw.Draw(img)
    item_px, ncols, rows_fit, overflow = _fit_item_block(draw, items, zone_w=iz_w, zone_h=iz_h)
    itf = _premium_font("footer", item_px)
    line_h = int(item_px * 1.5)
    col_w = iz_w // ncols
    shown = items[: rows_fit * ncols] if overflow else items
    for idx, it in enumerate(shown):
        col = idx // rows_fit if overflow else (idx % ncols)
        row = idx % rows_fit if overflow else (idx // ncols)
        cx = iz_x + col * col_w
        cy = iz_y + row * line_h
        # filled-circle bullet (drawn, not a glyph — robust across fonts)
        br = max(5, int(item_px * 0.16))
        bcy = cy + int(item_px * 0.55)
        draw.ellipse([cx, bcy - br, cx + 2 * br, bcy + br], fill=_GOLD)
        draw.text((cx + int(item_px * 0.9), cy), it, font=itf, fill=_CREAM)
        placed_text.append(it)
    regions["items"] = (iz_x, iz_y, iz_w, iz_h)
    fonts["menu"] = item_px

    # ── footer ──
    foot_px = max(24, int(w * 0.026))
    ff = _premium_font("footer", foot_px)
    if footer:
        _draw_center(draw, footer, ff, cy=int(h * 0.945), w=w, fill=_CREAM)
        placed_text.append(footer)
    regions["footer"] = (0, int(h * 0.90), w, int(h * 0.10))
    fonts["footer"] = foot_px

    report = {
        "eligible": True,
        "size": size,
        "background": background,
        "food_fallback_reason": food_fallback_reason,
        "regions": regions,
        "fonts": fonts,
        "headline": " ".join(head_lines),
        "offer_label": label,
        "offer_price": price,
        "items": list(shown),
        "item_px": item_px,
        "items_overflow": bool(overflow),
        "placed_text": placed_text,
    }
    return img, report


# ── drawing helpers ─────────────────────────────────────────────────────────

def _draw_center(draw, text, font, *, cy, w, fill, shadow=False):
    tw = _text_w(draw, text, font)
    x = (w - tw) // 2
    if shadow:
        draw.text((x + 3, cy + 3), text, font=font, fill=(0, 0, 0))
    draw.text((x, cy), text, font=font, fill=fill)


def _fit_headline(draw, text, *, max_w, max_px, min_px=58):
    """Largest font (down to min) that fits in <=2 lines within max_w."""
    words = text.split()
    for px in range(max_px, min_px - 1, -2):
        f = _headline_font(px)
        if _text_w(draw, text, f) <= max_w:
            return px, [text]
        # try 2 lines: split near the middle by words
        for cut in range(1, len(words)):
            l1, l2 = " ".join(words[:cut]), " ".join(words[cut:])
            if _text_w(draw, l1, f) <= max_w and _text_w(draw, l2, f) <= max_w:
                return px, [l1, l2]
    f = _headline_font(min_px)
    return min_px, [text]


def _draw_offer_badge(draw, *, label, price, cx, cy, w):
    """A large gold-framed circular price badge. Draws ONLY grounded text — it
    NEVER invents a label (fact-safety). Returns (radius, [text actually drawn])
    so the caller's placed_text mirrors the canvas exactly."""
    r = int(w * 0.16)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(20, 12, 8))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=_GOLD, width=max(5, int(w * 0.008)))
    inner = int(r * 0.86)
    draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], outline=_GOLD, width=2)
    lf = _premium_font("kicker", max(20, int(w * 0.026)))
    pf = _premium_font("offer_price", max(READABILITY_FLOOR_PX, int(w * 0.085)))
    drawn: list[str] = []
    if label:
        lab = label.upper()[:18]
        _draw_center_at(draw, lab, lf, cx=cx, cy=cy - int(r * 0.55), fill=_CREAM)
        drawn.append(lab)
    if price:
        # centre the price vertically when there is no label above it
        price_cy = cy - int(r * 0.18) if label else cy - int(r * 0.30)
        _draw_center_at(draw, price, pf, cx=cx, cy=price_cy, fill=_GOLD)
        drawn.append(price)
    return r, drawn


def _draw_center_at(draw, text, font, *, cx, cy, fill):
    tw = _text_w(draw, text, font)
    draw.text((cx - tw // 2, cy), text, font=font, fill=fill)


def _rounded_panel(img, box, *, fill, alpha):
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(box, radius=28, fill=(fill[0], fill[1], fill[2], alpha))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))


__all__ = [
    "READABILITY_FLOOR_PX",
    "poster_v1_enabled",
    "compose_premium_poster_v1",
]
