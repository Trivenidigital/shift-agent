"""Premium Poster Template v1 — Slice A: deterministic composer foundation.

Builds a premium street-food/menu POSTER deterministically with Pillow from
LOCKED FACTS ONLY (fact-safe by construction — never invents an item, price,
name, date, or location). It directly targets the F0190 failures: weak
hierarchy, a tiny unreadable item list, a non-dominant offer, and poor
WhatsApp-preview readability.

Composition scope:
  fixed poster regions · brand/header band · bold dominant headline ·
  large framed offer/price badge · LARGE readable auto-columned item-list block ·
  footer/contact · warm/dark text-safe background with a deterministic fallback
  when no food image is supplied.

WIRING STATUS (2026-07): this composer IS live — `premium_poster_v1_director.
compose_best_of_n` orchestrates it and `render.render_premium_poster_v1` routes
it behind FLYER_PREMIUM_POSTER_V1 + allowlist on both the managed and bare
paths. Flag-off / not-allowlisted renders never enter the branch and stay
byte-identical legacy.

FAIL-CLOSED CONTRACT: the composer never paints a mutated, truncated, or
partial fact. Any brief it cannot represent faithfully (multi-price offer,
brand wider than the band at the floor, more items than fit the readable
zone) returns (None, report) so the caller falls through to the existing
render path, which draws full text with its own ladder.

Reuses `premium_overlay._premium_font` (vendored Playfair/Montserrat/Cormorant);
does not modify premium_overlay.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Optional, Sequence

try:  # flat (VPS, deployed as flyer_premium_overlay.py) then package (tests)
    from flyer_premium_overlay import _FONT_DIR_CANDIDATES, _premium_font  # type: ignore
except ImportError:  # pragma: no cover - src layout fallback
    from agents.flyer.premium_overlay import _FONT_DIR_CANDIDATES, _premium_font

# Readability floor (px) for item text at the 1080-wide poster. At a ~400px
# WhatsApp preview that downscales to ~13px — the line below which menu text
# becomes the unreadable F0190 failure. The composer NEVER renders items below it.
READABILITY_FLOOR_PX = 34
# Floors for the brand band and the badge label (each fits-to-width and, when the
# full text cannot fit even at the floor, the composer fails closed / omits the
# label rather than ever painting a clipped or truncated fact).
BRAND_READABILITY_FLOOR_PX = 24
BADGE_LABEL_FLOOR_PX = 18

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


def _offer_source_text(facts) -> str:
    """The single locked-fact value the offer badge is built from."""
    return _value_of("pricing_structure", facts) or _value_of("offer:0", facts) or _value_of("offer", facts)


def _offer(facts) -> tuple[str, str]:
    """(label, price) from pricing_structure or the first offer fact. Grounded.
    Only meaningful for single-price offers — compose refuses multi-price offers
    fail-closed BEFORE calling this (a badge showing the first of two prices as
    the dominant number would mutate the offer, e.g. "Was $12.99 now $8.99")."""
    text = _offer_source_text(facts)
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

# The festive-vernacular register (Workstream B) swaps the theatrical Montserrat
# headline for a BRUSH-SCRIPT hand-lettered face. Pacifico is a static
# single-weight OFL face — no wght axis to pin (unlike Montserrat).
_SCRIPT_REGISTER = "festive-vernacular"
_SCRIPT_HEADLINE_FILE = "Pacifico-Regular.ttf"


def _headline_font(size: int, register: str | None = None):
    """Bold display headline for theatrical poster punch.

    Default (``register`` unset or any non-script register): Montserrat-ExtraBold,
    a VARIABLE font whose default instance is light, so the weight axis is pinned
    to ExtraBold (800). For the ``festive-vernacular`` register the headline is a
    BRUSH-SCRIPT hand-lettered face (Pacifico, static single-weight — no wght
    axis). Static fonts raise on the weight pin and are ignored; a missing/corrupt
    TTF falls back to PlayfairDisplay-Black. Loaded directly so premium_overlay
    stays untouched."""
    from PIL import ImageFont
    is_script = register == _SCRIPT_REGISTER
    filename = _SCRIPT_HEADLINE_FILE if is_script else "Montserrat-ExtraBold.ttf"
    for base in _FONT_DIR_CANDIDATES:
        p = base / filename
        if p.exists():
            try:
                f = ImageFont.truetype(str(p), size)
                if not is_script:
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


def _fit_single_line_px(draw, text, *, font_role, max_w, max_px, min_px):
    """Largest font size (max_px down to min_px) at which ``text`` fits ``max_w``
    on one line, or None when even the floor overflows. Whole-string fit only —
    never a truncation."""
    for px in range(max_px, min_px - 1, -1):
        if _text_w(draw, text, _premium_font(font_role, px)) <= max_w:
            return px
    return None


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
    # Multi-price offers cannot be represented by a single dominant badge price
    # without mutating the offer ("Was $12.99 now $8.99" would show $12.99 as THE
    # price). Refuse fail-closed; the existing render path draws the full text.
    if len(_PRICE_RE.findall(_offer_source_text(locked_facts))) > 1:
        return None, {"eligible": False, "reason": "multi-price offer (single badge price would mutate the offer)"}

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

    # ── brand band (fit-to-width like the headline/footer: the brand band was the
    # ONLY text region with a fixed size, so a very long business name clipped at
    # the canvas edges — a brand-name mutation risk if OCR folds the clipped
    # fragment to a warn-tier "typo") ──
    brand_px = _fit_single_line_px(
        draw, business.upper(), font_role="masthead",
        max_w=int(w * 0.94), max_px=max(40, int(w * 0.052)), min_px=BRAND_READABILITY_FLOOR_PX)
    if brand_px is None:
        # Cannot fit the full business name even at the floor — never paint a
        # clipped brand; fall through to the existing render path.
        return None, {"eligible": False, "reason": "business name too wide for the brand band at the readability floor"}
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
    badge_r, badge_drawn = _draw_offer_badge(img, draw, label=label, price=price, cx=badge_cx, cy=badge_cy, w=w)
    draw = ImageDraw.Draw(img)  # badge composites a halo paste; re-bind for the rest
    regions["offer"] = (badge_cx - badge_r, badge_cy - badge_r, badge_r * 2, badge_r * 2)
    offer_px = max(READABILITY_FLOOR_PX, int(w * 0.10))  # matches the badge price font
    fonts["offer_price"] = offer_px
    placed_text.extend(badge_drawn)  # exactly what the badge drew (mirrors canvas)

    # ── item-list block (LARGE, readable, auto-columned) ──
    iz_x, iz_y = int(w * 0.07), int(h * 0.625)
    iz_w, iz_h = int(w * 0.86), int(h * 0.255)
    # panel
    _rounded_panel(img, (iz_x - 18, iz_y - 18, iz_x + iz_w + 18, iz_y + iz_h + 18), fill=_PANEL, alpha=200)
    draw = ImageDraw.Draw(img)
    item_px, ncols, rows_fit, overflow = _fit_item_block(draw, items, zone_w=iz_w, zone_h=iz_h)
    if overflow:
        # A partial menu must never ship: dropped items are customer-supplied
        # required facts (block-tier downstream), so composing them away would at
        # best burn a doomed QA cycle and at worst slip past fuzzy matching.
        # Fail closed; the existing render path owns dense menus.
        return None, {"eligible": False, "reason": "items overflow the readable item zone (partial menu never composed)"}
    itf = _premium_font("footer", item_px)
    line_h = int(item_px * 1.5)
    col_w = iz_w // ncols
    shown = items
    for idx, it in enumerate(shown):
        col = idx % ncols
        row = idx // ncols
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

    # ── footer (auto-fit to width; wrap to 2 lines only if it still overflows at
    # the readability floor — the trailing contact/phone must never clip) ──
    foot_px = max(24, int(w * 0.026))
    if footer:
        foot_px, foot_lines = _fit_footer(draw, footer, max_w=int(w * 0.94), max_px=foot_px)
        ff = _premium_font("footer", foot_px)
        line_h = int(foot_px * 1.3)
        base_cy = int(h * 0.945) - (line_h * (len(foot_lines) - 1)) // 2
        for i, line in enumerate(foot_lines):
            _draw_center(draw, line, ff, cy=base_cy + i * line_h, w=w, fill=_CREAM)
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
        "offer_badge_radius": badge_r,
        "items": list(shown),
        "item_px": item_px,
        "items_overflow": False,  # partial menus are never composed (fail-closed above)
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


# The footer packs schedule + location + contact on one line, so it is the widest
# single string on the poster. A FIXED font size overflowed the frame for dense
# footers and clipped the trailing contact/phone off the right edge — which the
# visual-QA / fact-firewall then (correctly) rejected as an unverified phone
# number. Fit the footer to the width like the headline so the concatenated
# footer (and its trailing phone) stops clipping. A phone (~12 chars) can never
# itself exceed the frame; only a pathologically long SINGLE field with no
# separator can still center over-wide at the floor, and the downstream
# `run_visual_qa` fact readback blocks that fail-closed (missing/partial
# contact_phone). Floor is footer-specific (smaller than the item-block floor).
FOOTER_READABILITY_FLOOR_PX = 22


def _fit_footer(draw, text, *, max_w, max_px, min_px=FOOTER_READABILITY_FLOOR_PX):
    """Largest footer font (down to ``min_px``) whose ``text`` fits ``max_w`` on ONE
    line; if it still overflows at the floor, wrap to 2 balanced lines at a footer
    separator boundary so the trailing contact stops clipping. Returns
    ``(font_px, lines)``. Only ever splits on the deterministic ``_footer_line``
    separator — never mid-token — so no fact is ever broken or fabricated. (A lone
    field wider than the frame at the floor still centers over-wide; unreachable
    for the phone, and the downstream `run_visual_qa` fact readback blocks it
    fail-closed.)"""
    for px in range(max_px, min_px - 1, -1):
        f = _premium_font("footer", px)
        if _text_w(draw, text, f) <= max_w:
            return px, [text]
    f = _premium_font("footer", min_px)
    sep = "  ·  "
    if sep in text:
        parts = text.split(sep)
        for cut in range(1, len(parts)):
            l1, l2 = sep.join(parts[:cut]), sep.join(parts[cut:])
            if _text_w(draw, l1, f) <= max_w and _text_w(draw, l2, f) <= max_w:
                return min_px, [l1, l2]
    return min_px, [text]


def _fit_badge_label_px(draw, text: str, *, max_w: int, max_px: int) -> Optional[int]:
    """Font size at which the WHOLE label fits the badge chord width, or None.
    Replaces the earlier whole-word-prefix truncation: a prefix of an offer is a
    FACT MUTATION ("BUY 2 GET 1 HALF OFF" -> "BUY 2 GET 1 HALF"; "50% off orders
    over $50" -> "50% OFF ORDERS" beside a dominant "$50") that poster-global
    token QA cannot reliably catch when the dropped words appear elsewhere on the
    poster. The badge now shrinks the full label to fit, and when it cannot fit
    at the floor the label is OMITTED entirely — a missing offer clause then
    fails the downstream fact QA closed and the render falls through to the
    existing path, which draws the full text."""
    return _fit_single_line_px(
        draw, text, font_role="kicker", max_w=max_w, max_px=max_px, min_px=BADGE_LABEL_FLOOR_PX)


def _draw_offer_badge(img, draw, *, label, price, cx, cy, w):
    """A large gold-framed circular price badge — the poster's offer hook. Draws
    ONLY grounded text (NEVER invents a label — fact-safety). A soft blurred dark
    halo lifts the badge off the busy food so the offer reads at WhatsApp preview
    size. Returns (radius, [text actually drawn]) so placed_text mirrors the canvas.
    Bigger + stronger than the prior badge (offer_energy was the weak critique axis)."""
    from PIL import Image, ImageDraw, ImageFilter

    r = int(w * 0.175)  # larger circle (was 0.16w) — more dominant offer
    # soft separation halo (blurred dark disc) BEHIND the badge — contrast vs food.
    # Tight + biased slightly DOWNWARD (toward the food) so the glow never bleeds up
    # into a tall 2-line headline above the badge.
    halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    hr = int(r * 1.12)
    halo_cy = cy + int(r * 0.12)
    ImageDraw.Draw(halo).ellipse([cx - hr, halo_cy - hr, cx + hr, halo_cy + hr], fill=(8, 5, 3, 150))
    halo = halo.filter(ImageFilter.GaussianBlur(int(r * 0.11)))
    img.paste(Image.alpha_composite(img.convert("RGBA"), halo).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(img)  # re-bind after the composite paste

    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(18, 11, 7))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=_GOLD, width=max(7, int(w * 0.011)))  # thicker frame
    inner = int(r * 0.87)
    draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], outline=_GOLD, width=3)
    pf = _premium_font("offer_price", max(READABILITY_FLOOR_PX, int(w * 0.10)))   # bigger price (was 0.085w)
    drawn: list[str] = []
    if label:
        lab = label.upper()
        # chord width of the inner circle at the label's vertical offset (~0.52r)
        label_max_w = int(r * 1.6)
        label_px = _fit_badge_label_px(draw, lab, max_w=label_max_w, max_px=max(22, int(w * 0.030)))
        if label_px is not None:
            lf = _premium_font("kicker", label_px)
            _draw_center_at(draw, lab, lf, cx=cx, cy=cy - int(r * 0.52), fill=_CREAM)
            drawn.append(lab)
        # label_px None -> label omitted entirely (never a prefix); the price alone
        # is drawn and downstream fact QA fail-closes on the missing offer clause.
    if price:
        # centre the price vertically when no label was DRAWN above it (either no
        # label fact, or the label was omitted because it could not fit whole)
        price_cy = cy - int(r * 0.16) if drawn else cy - int(r * 0.28)
        # drop shadow then gold for crisp contrast over any background
        _draw_center_at(draw, price, pf, cx=cx + 3, cy=price_cy + 3, fill=(0, 0, 0))
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
