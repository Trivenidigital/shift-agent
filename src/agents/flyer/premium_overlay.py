"""premium_overlay — role-based premium font loader for Fix C deterministic renderer.

This module is the foundation for the premium deterministic flyer-text renderer.
This file (Task 1) provides only the font bundle + ``_premium_font`` loader.
Later tasks add layout solver, scrims, offer seal, and the main renderer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FONT_DIR = Path(__file__).resolve().parent / "fonts"

# Maps rendering role → vendored TTF filename.
# All fonts are SIL OFL 1.1; see fonts/FONTS.md for source URLs + substitution notes.
_ROLE_FILES: dict[str, str] = {
    "masthead":    "PlayfairDisplay-Bold.ttf",
    "kicker":      "Montserrat-Bold.ttf",
    "title":       "PlayfairDisplay-Black.ttf",
    "offer_price": "PlayfairDisplay-Black.ttf",
    "menu":        "CormorantGaramond-SemiBold.ttf",
    "footer":      "Montserrat-SemiBold.ttf",
}

# Desired weight (CSS-style 100–900) per role, applied to the variable-font
# `wght` axis. The vendored fonts are variable TTFs whose default instance is
# ~400; without this, masthead/title/offer would all render at the same light
# default weight, defeating the premium typography. Static fonts / older Pillow
# ignore this gracefully (see _premium_font).
_ROLE_WEIGHT: dict[str, int] = {
    "masthead":    700,
    "kicker":      700,
    "title":       900,
    "offer_price": 900,
    "menu":        600,
    "footer":      600,
}

# System-level fallbacks for environments where the fonts/ bundle is absent.
_SYS_FALLBACKS: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _premium_font(role: str, size: int):
    """Return an ``ImageFont`` for the given rendering *role* at *size* pixels.

    Resolution order:
    1. Vendored TTF in ``_FONT_DIR`` mapped by *role*.
    2. System DejaVu fallbacks (Linux VPS).
    3. ``ImageFont.load_default(size=size)`` — always available, never raises.

    The function never raises; callers can always use the returned font object.
    """
    from PIL import ImageFont

    candidates: list[Path] = []
    fn = _ROLE_FILES.get(role)
    if fn:
        candidates.append(_FONT_DIR / fn)
    candidates += [Path(p) for p in _SYS_FALLBACKS]

    for path in candidates:
        try:
            if path.exists():
                font = ImageFont.truetype(str(path), size=size)
                w = _ROLE_WEIGHT.get(role)
                if w is not None:
                    try:
                        # Variable-font weight axis (mechanism: set_variation_by_axes).
                        font.set_variation_by_axes([w])
                    except (OSError, AttributeError, ValueError):
                        pass  # static font / unsupported Pillow -> default weight
                return font
        except OSError:
            continue

    # Final fallback — Pillow >= 10.1 supports size parameter.
    return ImageFont.load_default(size=size)


# ---------------------------------------------------------------------------
# Task 2: layout solver — pure function, no I/O
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PremiumLayout:
    menu_mode: str        # "combo" | "name_rows" | "two_col" | "two_col_compact"
    offer_mode: str       # "seal" | "inline" | "none"
    menu_font_px: int
    min_font_px: int


def plan_premium_layout(items, *, shared_price, width: int = 1080) -> PremiumLayout:
    """Map menu content metrics to a presentation spec.

    Pure function — no I/O, no PIL drawing.  Later tasks consume the returned
    ``PremiumLayout`` to drive the deterministic renderer.

    Args:
        items:        Sequence of ``(name, price)`` tuples.  ``price`` may be
                      an empty string when all items share a single price.
        shared_price: A single price string shown once (e.g. "All items $7.99")
                      or ``None`` when items carry individual prices.
        width:        Canvas width in pixels (default 1080); drives the mobile
                      legibility floor calculation.

    Returns:
        A frozen ``PremiumLayout`` dataclass with mode + font decisions.
    """
    n = len(items)
    has_item_prices = any(p for _name, p in items)
    floor = max(20, int(width * 0.020))      # mobile legibility floor (~22px @1080)

    if n <= 2:
        mode = "combo"
    elif shared_price and not has_item_prices:
        mode = "name_rows"
    elif n <= 8:
        mode = "two_col"
    else:
        mode = "two_col_compact"

    base = {"combo": 0.040, "name_rows": 0.034, "two_col": 0.030, "two_col_compact": 0.022}[mode]
    font_px = max(floor, int(width * base))

    offer = "seal" if (shared_price and not has_item_prices) else ("inline" if has_item_prices else "none")
    return PremiumLayout(menu_mode=mode, offer_mode=offer, menu_font_px=font_px, min_font_px=floor)


# ---------------------------------------------------------------------------
# Task 3: gradient text-safe-zone scrims
# ---------------------------------------------------------------------------

def compose_scrims(img, *, top_frac=0.22, bottom_frac=0.32):
    """Darken the top and bottom bands with vertical alpha gradients so overlaid
    text is legible while the centre hero imagery stays visible. Returns a new RGB image."""
    from PIL import Image
    img = img.convert("RGB")
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = overlay.load()
    top_h = int(h * top_frac)
    bot_start = int(h * (1 - bottom_frac))
    for y in range(top_h):                         # 0.82 -> 0 alpha downward
        a = int(209 * (1 - y / max(1, top_h)))
        for x in range(w):
            px[x, y] = (8, 4, 2, a)
    for y in range(bot_start, h):                  # 0 -> 0.92 alpha downward
        a = int(235 * ((y - bot_start) / max(1, h - bot_start)))
        for x in range(w):
            px[x, y] = (8, 4, 2, a)
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


# ---------------------------------------------------------------------------
# Task 4: premium offer-seal primitive
# ---------------------------------------------------------------------------

def _seal_fonts(width):
    return (_premium_font("kicker", max(20, int(width * 0.022))),
            _premium_font("offer_price", max(54, int(width * 0.072))))


def _seal_label_lines(draw, label, lf, inner_w):
    """Wrap the (upper-cased) seal label to the seal's inner width."""
    return _wrap_premium(draw, label.upper(), "kicker",
                         lf.size, inner_w) if label else []


def _seal_geometry(draw, *, label, price, width):
    """Compute the seal pill geometry sized from BOTH label and price.

    Returns ``(bw, bh, label_lines, lf, pf, pw, ph, pad, gap, label_h)`` where
    ``bw``/``bh`` are the pill's width/height.  The width is driven by the wider
    of the price glyph and the (wrapped) label — so a long required label can
    never overflow/clip the pill.
    """
    lf, pf = _seal_fonts(width)
    pl, pt, pr, pb = draw.textbbox((0, 0), price, font=pf)
    pw, ph = pr - pl, pb - pt
    pad_x = int(width * 0.034)
    pad_y = int(width * 0.026)
    gap = int(width * 0.010)
    # First pass: wrap the label to the price-driven inner width, then grow the
    # pill width to the WIDEST line (label or price) so nothing clips.
    inner_w = max(pw, int(width * 0.18))
    label_lines = _seal_label_lines(draw, label, lf, inner_w)
    widest = pw
    label_h = 0
    for ln in label_lines:
        ll, lt, lr, lb = draw.textbbox((0, 0), ln, font=lf)
        widest = max(widest, lr - ll)
        label_h += int(lf.size * 1.18)
    bw = widest + pad_x * 2
    bh = pad_y * 2 + label_h + (gap if label_h else 0) + ph
    return bw, bh, label_lines, lf, pf, pw, ph, pad_x, pad_y, gap, label_h


def _measure_offer_seal(draw, *, label, price, width):
    """Return the seal pill height for the given label+price (for layout)."""
    _bw, bh, *_rest = _seal_geometry(draw, label=label, price=price, width=width)
    return bh


def draw_offer_seal(draw, *, label, price, width, center):
    """Draw a gold-bordered offer pill (label over price) centred at `center`.

    The pill is sized from BOTH the label and the price (widest of the two,
    label wrapped as needed) so a long label is never clipped.  Returns the
    seal bounding box ``(x0, y0, x1, y1)``."""
    bw, bh, label_lines, lf, pf, pw, ph, pad_x, pad_y, gap, label_h = _seal_geometry(
        draw, label=label, price=price, width=width
    )
    cx, cy = center
    x0, y0 = cx - bw // 2, cy - bh // 2
    x1, y1 = x0 + bw, y0 + bh
    draw.rounded_rectangle((x0 + 5, y0 + 5, x1 + 5, y1 + 5), radius=22, fill=(0, 0, 0, 90))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=22, fill=(20, 8, 4, 150), outline=(236, 200, 115, 255), width=3)
    ly = y0 + pad_y
    for ln in label_lines:
        ll, lt, lr, lb = draw.textbbox((0, 0), ln, font=lf)
        draw.text((cx - (lr - ll) // 2, ly), ln, font=lf, fill=(240, 226, 196, 255))
        ly += int(lf.size * 1.18)
    draw.text((cx - pw // 2, y1 - ph - pad_y), price, font=pf, fill=(255, 233, 184, 255))
    return (x0, y0, x1, y1)


# ---------------------------------------------------------------------------
# Task 5: render_premium_overlay — Template A (Editorial)
# ---------------------------------------------------------------------------

# Editorial palette (mirrors the approved fixc-A mockup CSS).
_GOLD = (236, 200, 115, 255)        # #ecc873 rule / seal border / kicker accent
_CREAM = (255, 248, 236, 255)       # #fff8ec body / masthead / menu
_CREAM_SOFT = (243, 231, 205, 255)  # #f3e7cd footer
_TITLE_CREAM = (255, 247, 234, 255) # title fill
_ACCENT_RED = (184, 31, 44, 255)    # #b81f2c accent (inline price chips)
_SHADOW = (0, 0, 0, 180)            # drop shadow for legibility over imagery


def _draw_centered(draw, text, font, *, cy, width, fill, shadow=_SHADOW, shadow_dy=3):
    """Center *text* horizontally at vertical top *cy*; returns the line height drawn."""
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    x = (width - tw) // 2 - l
    if shadow is not None:
        draw.text((x + 2, cy + shadow_dy - t), text, font=font, fill=shadow)
    draw.text((x, cy - t), text, font=font, fill=fill)
    return th


def _draw_gold_rule(draw, *, cy, width, frac=0.62):
    """Center a 1px gold hairline that fades at both ends (mirrors the CSS rule)."""
    rule_w = int(width * frac)
    x0 = (width - rule_w) // 2
    steps = 60
    for i in range(steps):
        seg_x0 = x0 + int(rule_w * i / steps)
        seg_x1 = x0 + int(rule_w * (i + 1) / steps)
        # triangular alpha: 0 at the ends, full in the middle
        a = int(220 * (1 - abs(i - steps / 2) / (steps / 2)))
        draw.line((seg_x0, cy, seg_x1, cy), fill=(_GOLD[0], _GOLD[1], _GOLD[2], a), width=2)


def render_premium_overlay(project, source, target, *, size, output_format):
    """Render the Template-A (Editorial) premium text-over-imagery flyer.

    Composition (top→bottom), all over a textless food hero with gradient scrims:
      • top zone   — kicker (Montserrat, tracked) + gold hairline rule + masthead
                     (Playfair brand name)
      • title      — big Playfair-Black campaign headline, anchored low-centre
      • offer      — gold "ANY ITEM $7.99" seal when the layout chose ``seal``
      • menu       — Cormorant rows (combo / name_rows / two_col / two_col_compact)
      • footer     — Montserrat: schedule | location | contact

    Fail-closed: EVERY required visible fact (brand, title, schedule, every menu
    item name, the shared offer or per-item prices, location, contact, plus any
    other detail clause) must be drawn within its zone at a font ≥ the layout's
    ``min_font_px``.  If anything cannot fit, ``render.FlyerRenderError`` is raised
    (mirrors ``apply_critical_text_overlay`` → routes the project to manual review)
    rather than silently shipping an incomplete concept.

    The fact assembly is reused from ``render._menu_overlay_payload`` — the SAME
    helper ``apply_critical_text_overlay`` consumes and the SAME set visual QA
    checks — so the two renderers can never drift on which facts are drawn.
    """
    from PIL import Image, ImageDraw
    # Lazy import to avoid an import cycle (render imports flyer modules).
    from agents.flyer import render
    # Reuse the referee's OWN matching helpers so the fail-closed contract is
    # identical to visual_qa by construction (never stricter, never looser):
    # value-and-occurrence-aware text/phone/address/schedule/price matching plus
    # per-row item name+price pairing.
    from agents.flyer import visual_qa as vqa

    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = size

    payload = render._menu_overlay_payload(project)
    business = str(payload.get("business") or "").strip()
    title = str(payload.get("title") or "").strip()
    schedule = str(payload.get("schedule") or "").strip()
    location = str(payload.get("location") or "").strip()
    contact = str(payload.get("contact") or "").strip()
    raw_items = list(payload.get("items") or [])
    shared_offer_label = str(payload.get("shared_offer_label") or "").strip()
    shared_offer_price = str(payload.get("shared_offer_price") or "").strip()
    shared_offer_text = str(payload.get("shared_offer_text") or "").strip()
    extras = [str(e).strip() for e in (payload.get("extras") or []) if str(e).strip()]

    # Split items into (name, price); reuse render's parser for consistency.
    items = [render._split_item_price(it) for it in raw_items]
    has_item_prices = any(price for _name, price in items)

    layout = plan_premium_layout(
        items,
        shared_price=shared_offer_price or None,
        width=width,
    )
    min_px = layout.min_font_px

    # ------------------------------------------------------------------
    # Required-fact ledger — EVERY ``required=True`` locked fact with a
    # non-empty value, keyed by its ORIGINAL ``fact_id``.  No allowlist: this
    # mirrors ``visual_qa.run_visual_qa`` (visual_qa.py:1694), which iterates
    # every required locked fact and emits ``missing required visible fact:
    # <id>`` for any whose value is absent.  Coverage is proven the SAME way the
    # referee proves it — by VALUE: a fact is satisfied only when its normalized
    # value appears in the text this renderer actually drew (the ``ink`` log),
    # not when a region "bucket" was touched.  Distinct facts that share a
    # region (e.g. ``campaign_title`` + ``headline``; duplicate item prices) are
    # therefore each checked independently against the real drawn text.
    required_facts: list[tuple[str, str, str]] = []  # (fact_id, label, value)
    for fact in project.locked_facts:
        if not getattr(fact, "required", False):
            continue
        fid = str(getattr(fact, "fact_id", "") or "")
        label = str(getattr(fact, "label", "") or "")
        value = str(getattr(fact, "value", "") or "").strip()
        if fid and value:
            required_facts.append((fid, label, value))

    # ``ink`` accumulates the RAW text of every string actually rendered, one
    # entry per visual line/row, so the final coverage check is grounded in real
    # pixels AND so the referee's line-wise item name+price pairing works.
    ink: list[str] = []

    def _ink(text: str) -> None:
        if text and text.strip():
            ink.append(text.strip())

    def _rendered_text() -> str:
        return "\n".join(ink)

    def _fact_flags(fid: str, label: str, value: str) -> dict:
        """The EXACT per-fact flags the referee passes to ``_value_present_in``
        (visual_qa.run_visual_qa locked-fact loop, visual_qa.py ~1710-1717).
        Mirrors them character-for-character — fact_id-equality OR the keyword in
        the LABEL ONLY (casefolded), never the fact_id — so the renderer's
        matching is neither over- nor under-broad relative to the referee."""
        label_cf = label.casefold()
        return {
            "phone_match": vqa._locked_fact_uses_phone_match(fact_id=fid, label=label, value=value),
            "address_match": fid == "location" or "address" in label_cf or "location" in label_cf,
            "schedule_match": fid == "schedule" or "schedule" in label_cf,
            "price_match": fid.endswith(":price") or "price" in label_cf,
        }

    def _covered(fid: str, label: str, value: str) -> bool:
        """A fact is covered iff the referee's own presence check passes against
        the text we actually drew — eliminating substring boundary false
        positives ("Vada"≠"Vadai", "$7.99"≠"$7.999") and using phone/price/
        address/schedule semantics identical to visual_qa."""
        normalized = vqa._normalize_text_for_match(_rendered_text())
        return vqa._value_present_in(normalized, value, **_fact_flags(fid, label, value))

    # ---- compose background + scrims ------------------------------------
    with Image.open(source) as bg:
        img = bg.convert("RGB")
        if img.size != size:
            img = img.resize(size)
    img = compose_scrims(img, top_frac=0.22, bottom_frac=0.40)
    draw = ImageDraw.Draw(img, "RGBA")

    margin = max(28, int(width * 0.05))
    safe_w = width - margin * 2

    # ===================================================================
    # TOP ZONE — kicker + gold rule + masthead (brand)
    # ===================================================================
    y = int(height * 0.045)
    kicker_px = max(min_px, int(width * 0.020))
    kicker_font = _premium_font("kicker", kicker_px)
    # Decorative tracked all-caps kicker (never a required fact — required facts
    # are drawn in their own zones). Uses the project category cue if present.
    kicker_text = _editorial_kicker(project, render)
    if kicker_text:
        spaced = "  ".join(kicker_text.upper().split())
        _draw_centered(draw, spaced, kicker_font, cy=y, width=width,
                       fill=_GOLD, shadow_dy=2)
        _ink(kicker_text)
        y += int(kicker_px * 1.5)

    _draw_gold_rule(draw, cy=y, width=width)
    y += max(10, int(height * 0.012))

    if business:
        mast_px = max(min_px, int(width * 0.046))
        mast_lines = _wrap_premium(draw, _spaced_caps(business), "masthead", mast_px, safe_w)
        # Masthead is uppercased + letter-spaced; log the ORIGINAL value so the
        # coverage check matches the locked fact regardless of case/spacing.
        for ln in mast_lines:
            _draw_centered(draw, ln, _premium_font("masthead", mast_px),
                           cy=y, width=width, fill=_CREAM)
            y += int(mast_px * 1.18)
        _ink(business)
    top_zone_bottom = y

    # ===================================================================
    # FOOTER — schedule | location | contact (anchored to the bottom)
    # Drawn first so the menu/title/seal can use the space above it.
    # ===================================================================
    footer_px = max(min_px, int(width * 0.0185))
    footer_parts = [p for p in (schedule, location, contact) if p]
    footer_text = "   |   ".join(footer_parts)
    footer_font = _premium_font("footer", footer_px)
    footer_y = height - margin
    if footer_text:
        footer_lines = _wrap_premium(draw, footer_text, "footer", footer_px, safe_w)
        footer_block_h = len(footer_lines) * int(footer_px * 1.35)
        footer_y = height - max(margin, int(height * 0.022)) - footer_block_h
        fy = footer_y
        for ln in footer_lines:
            _draw_centered(draw, ln, footer_font, cy=fy, width=width,
                           fill=_CREAM_SOFT, shadow_dy=2)
            _ink(ln)
            fy += int(footer_px * 1.35)
    bottom_limit = footer_y - max(14, int(height * 0.012))

    # ===================================================================
    # MENU — Cormorant rows just above the footer.  Fully preflighted:
    # ``_plan_menu_block`` wraps/shrinks every row to fit its column and raises
    # if a row cannot fit at ``min_px``; the returned ``render_fn`` only paints
    # rows that were proven to fit, and returns the exact strings it drew.
    # ===================================================================
    menu_px = max(min_px, layout.menu_font_px)
    menu_block_h, menu_render = _plan_menu_block(
        draw, items, layout, menu_px, min_px, safe_w, has_item_prices, render,
    )
    menu_top = bottom_limit - menu_block_h
    if items and menu_top < top_zone_bottom:
        raise render.FlyerRenderError("premium overlay does not fit")
    for drawn_text in menu_render(draw, x_left=margin, y_top=menu_top, width=width, safe_w=safe_w):
        _ink(drawn_text)

    # ===================================================================
    # OFFER SEAL — gold pill between the title and the menu (seal mode).
    # Sized from BOTH label and price; fails over to a secondary line if the
    # zone is too small.  Only inks label+price when both are fully rendered.
    # ===================================================================
    seal_label = (shared_offer_label or "OFFER").strip()
    seal_planned = layout.offer_mode == "seal" and bool(shared_offer_price)
    seal_h = 0
    seal_cy = 0
    if seal_planned:
        seal_h = _measure_offer_seal(draw, label=seal_label, price=shared_offer_price, width=width)
        seal_cy = menu_top - max(16, int(height * 0.018)) - seal_h // 2

    # ===================================================================
    # TITLE — Playfair-Black headline, anchored above the seal/menu.
    # ===================================================================
    title_anchor = (seal_cy - seal_h // 2 if seal_h else menu_top) - max(14, int(height * 0.016))
    if title:
        title_px = max(min_px, int(width * 0.072))
        # Shrink-to-fit so the headline never collides with the top zone.
        title_lines, title_px = _fit_title(
            draw, title, title_px, safe_w, min_px,
            max_height=title_anchor - top_zone_bottom - 8,
            line_factor=1.0,
        )
        if title_lines is None:
            raise render.FlyerRenderError("premium overlay does not fit")
        title_font = _premium_font("title", title_px)
        block_h = len(title_lines) * int(title_px * 1.0)
        ty = title_anchor - block_h
        if ty < top_zone_bottom:
            raise render.FlyerRenderError("premium overlay does not fit")
        for ln in title_lines:
            _draw_centered(draw, ln, title_font, cy=ty, width=width,
                           fill=_TITLE_CREAM, shadow_dy=4)
            ty += int(title_px * 1.0)
        _ink(title)

    # Draw the seal AFTER the title so its gold border sits cleanly on top.
    if seal_h:
        box = draw_offer_seal(draw, label=seal_label, price=shared_offer_price,
                              width=width, center=(width // 2, seal_cy))
        if box[1] < top_zone_bottom or box[3] > menu_top or box[0] < 0 or box[2] > width:
            raise render.FlyerRenderError("premium overlay does not fit")
        # Ink the label, the price, AND their combined form (the pill stacks
        # label directly above price, so the combined "label price" string is
        # visibly present) — this covers a ``pricing_structure`` fact whose value
        # is the whole "Any item ... $7.99" phrase.
        _ink(seal_label)
        _ink(shared_offer_price)
        _ink(f"{seal_label} {shared_offer_price}")
        if shared_offer_text:
            _ink(shared_offer_text)

    # ===================================================================
    # SECONDARY LINES — facts that no region above placed (offer-without-seal,
    # promotion_end, taglines, source_required_text, any other required fact ID
    # this renderer doesn't have a dedicated region for) PLUS best-effort
    # optional extras.  Each line is preflighted as a whole block (the MINOR
    # fix): we draw it only if ALL its wrapped lines fit the band — never
    # partially.  Required lines that cannot fit are caught by the coverage
    # check below (fail-closed); optional extras are simply skipped.
    # ===================================================================
    secondary_y = top_zone_bottom + max(6, int(height * 0.008))
    sec_px = max(min_px, int(width * 0.020))
    sec_font = _premium_font("footer", sec_px)
    sec_line_h = int(sec_px * 1.4)

    def _draw_secondary(text, *, fill) -> bool:
        """Draw a centred secondary block ONLY if the whole wrapped block fits
        the band above the title; returns True (and inks it) if drawn, else
        False with no pixels mutated."""
        nonlocal secondary_y
        if not text:
            return False
        lines = _wrap_premium(draw, text, "footer", sec_px, safe_w)
        if not lines:
            return False
        block_h = len(lines) * sec_line_h
        if secondary_y + block_h > title_anchor:
            return False  # whole block does not fit → draw nothing (no partial)
        for ln in lines:
            _draw_centered(draw, ln, sec_font, cy=secondary_y, width=width, fill=fill, shadow_dy=2)
            _ink(ln)
            secondary_y += sec_line_h
        return True

    # Required facts not yet covered by a region above get drawn here as
    # must-fit lines.  This is what makes BLOCKER 1 safe: an unknown required
    # fact id (tagline / source_required_text:* / replacement:*:new / future)
    # is rendered rather than silently skipped; if it can't fit, the coverage
    # check fails closed.  Offer-without-seal and promotion_end flow through the
    # same path.  Use gold for offer-class facts, cream otherwise.  Item facts
    # are excluded — they belong to the menu region and their coverage/pairing
    # is enforced by the final gate (a stray secondary item line would corrupt
    # the editorial layout).
    _offer_norm = render._normalize_fact_text(shared_offer_text or f"{seal_label} {shared_offer_price}")
    for fid, label, value in required_facts:
        if re.match(r"^item:\d+:(name|price)$", fid):
            continue
        if _covered(fid, label, value):
            continue
        is_offer = fid == "pricing_structure" or fid.startswith("offer:") \
            or render._normalize_fact_text(value) == _offer_norm
        _draw_secondary(value, fill=_GOLD if is_offer else _CREAM_SOFT)

    # Best-effort optional extras: assembly detail clauses that are NOT required
    # locked facts (e.g. the raw-request echo).  POLISH: suppress any extra that
    # adds NO new information vs the text already drawn, so a redundant echo
    # ("Weekend Specials any item $7.99") or a reformatted duplicate of an
    # already-shown fact (request-body "+1 732-983-7841" vs locked
    # "+17329837841") never prints.  Word tokens must be present in the drawn
    # word-token set; digit tokens must appear (as a run) in the drawn digit
    # string — so phone/number reformatting is recognized as redundant.  An
    # extra is drawn only if it adds a genuinely new token AND fits (best-effort,
    # never fail-closed).
    _drawn_tokens: set[str] = set()
    for line in ink:
        _drawn_tokens.update(_tokens(line))
    _drawn_digits = "".join(re.findall(r"\d+", " ".join(ink)))

    def _adds_new(extra: str) -> bool:
        for tok in _tokens(extra):
            if tok.isdigit():
                if tok not in _drawn_digits:
                    return True
            elif tok not in _drawn_tokens:
                return True
        return False

    for extra in extras:
        etoks = _tokens(extra)
        if not etoks or not _adds_new(extra):
            continue  # adds nothing new → skip (suppresses redundant echoes)
        if _draw_secondary(extra, fill=_CREAM_SOFT):
            _drawn_tokens.update(etoks)
            # MINOR: keep the digit ledger current so a LATER extra that repeats
            # these digits (in a different format) is also suppressed.
            _drawn_digits += "".join(re.findall(r"\d+", extra))

    # ===================================================================
    # FAIL-CLOSED — run the referee's OWN checks against the text we drew, so
    # the contract is identical to visual_qa by construction:
    #   (1) every required locked fact present via _value_present_in (text/
    #       phone/address/schedule/price semantics, value-and-boundary-aware);
    #   (2) every priced item present as a name+price PAIR per row
    #       (_item_price_pair_blockers) so duplicate prices can't collapse and
    #       an item:N:price isn't satisfied by the offer price elsewhere.
    # ===================================================================
    rendered = _rendered_text()
    normalized = vqa._normalize_text_for_match(rendered)
    missing: set[str] = set()
    for fid, label, value in required_facts:
        if not vqa._value_present_in(normalized, value, **_fact_flags(fid, label, value)):
            missing.add(fid)
    # Per-row item name+price pairing (BLOCKER + NOT-FIXED#2): the referee's own
    # pair check — a priced item must appear with ITS price on a row; duplicate
    # prices are matched per-row, never collapsed.
    pair_blockers = vqa._item_price_pair_blockers(project, rendered)
    if missing or pair_blockers:
        detail = ", ".join(sorted(missing) + list(pair_blockers))
        raise render.FlyerRenderError(
            "premium overlay does not fit (missing required visible fact: " + detail + ")"
        )

    img.convert("RGB").save(target, format="PNG", optimize=True)


# --- Template-A draw/measure helpers ---------------------------------------

def _spaced_caps(text: str) -> str:
    """Upper-case + single-space normalize for masthead letter-spacing feel."""
    return " ".join(text.upper().split())


def _tokens(text: str) -> set[str]:
    """Set of lower-case alphanumeric tokens in *text* (≥2 chars, or any digit
    run).  Used to decide whether an optional extra adds NEW information vs an
    already-drawn fact (POLISH: suppress fully-redundant raw-request echoes)."""
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").casefold()) if len(t) >= 2 or t.isdigit()}


def _editorial_kicker(project, render) -> str:
    """A short decorative kicker drawn above the masthead (e.g. the mockup's
    "SOUTH INDIAN FAVOURITES").

    The kicker is purely decorative — it is NEVER a required fact and is never
    relied on to satisfy the fail-closed contract.  It is sourced only from the
    optional ``fields.style_preference`` when that is a short, clean phrase; if
    absent the top zone is still editorial via the gold hairline rule + serif
    masthead.  Returns "" when there is no clean short source."""
    fields = getattr(project, "fields", None)
    cue = (getattr(fields, "style_preference", "") or "").strip()
    # Only a short, single-clause descriptor reads as a kicker; anything longer
    # or containing list/price punctuation is left out (decorative, not a fact).
    if cue and len(cue) <= 32 and not any(ch in cue for ch in "$|:0123456789"):
        return cue
    return ""


def _wrap_premium(draw, text, role, size, max_width):
    """Word-wrap *text* for a premium role at *size*; falls back to char-wrap for
    a single over-long token (mirrors render._wrap semantics)."""
    import textwrap as _tw
    font = _premium_font(role, size)
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        l, _t, r, _b = draw.textbbox((0, 0), candidate, font=font)
        if (r - l) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) == 1:
        l, _t, r, _b = draw.textbbox((0, 0), lines[0], font=font)
        if (r - l) > max_width and len(lines[0]) > 8:
            return _tw.wrap(lines[0], width=max(8, int(len(lines[0]) * max_width / max(1, r - l))))
    return lines


def _fit_title(draw, text, start_px, max_width, min_px, *, max_height, line_factor):
    """Shrink the title font until it fits within *max_width* and *max_height*.
    Returns (lines, font_px) or (None, start_px) if it cannot fit at min_px."""
    px = start_px
    while px >= min_px:
        lines = _wrap_premium(draw, text, "title", px, max_width)
        block_h = len(lines) * int(px * line_factor)
        if block_h <= max_height and len(lines) <= 4:
            return lines, px
        px -= 2
    # Last attempt at min_px:
    lines = _wrap_premium(draw, text, "title", min_px, max_width)
    if len(lines) * int(min_px * line_factor) <= max_height and len(lines) <= 4:
        return lines, min_px
    return None, start_px


def _text_w(draw, text, font):
    l, _t, r, _b = draw.textbbox((0, 0), text, font=font)
    return r - l


def _plan_menu_block(draw, items, layout, menu_px, min_px, safe_w, has_item_prices, render):
    """Preflight the menu block; return ``(height, render_fn)``.

    Every row is measured and wrapped/shrunk to fit BEFORE any pixel is drawn;
    if a row still cannot fit at ``min_px`` the function raises
    ``render.FlyerRenderError`` (fail-closed → manual).  ``render_fn(draw, *,
    x_left, y_top, width, safe_w) -> list[str]`` paints only the proven-fit rows
    and returns the exact strings it drew (for the caller's value-coverage ink
    log).  Modes mirror the layout solver: combo / name_rows / two_col /
    two_col_compact.  Cormorant, gold middle-dot separators.
    """
    if not items:
        return 0, (lambda draw, **_k: [])

    mode = layout.menu_mode
    line_h = int(menu_px * 1.4)
    sep = "   ·   "  # gold middle-dot separator

    if mode in ("combo", "name_rows"):
        names = [n for n, _p in items if n]
        # NOT-FIXED#2: if any item carries its own price (possible in combo,
        # n≤2), render each priced item as a "name — price" row so the price is
        # VISIBLY rendered and pair-matched per row — never assumed covered by a
        # shared/offer price elsewhere.  Name-only items stay as dot-joined rows.
        if any(p for _n, p in items):
            rows = [f"{n} — {p}" if p else n for n, p in items if n]
        elif len(names) <= 3:
            rows = [sep.join(names)]
        else:
            mid = (len(names) + 1) // 2
            rows = [sep.join(names[:mid]), sep.join(names[mid:])]
        # Shrink the menu font until EVERY name (and price, when present) appears
        # intact on a single wrapped line (a name/price split across a wrap would
        # be unreadable AND fail coverage).  Raise if even ``min_px`` can't hold.
        px = menu_px
        while True:
            font = _premium_font("menu", px)
            wrapped: list[str] = []
            for r in rows:
                wrapped.extend(_wrap_premium(draw, r, "menu", px, safe_w))
            norm_lines = [render._normalize_fact_text(ln) for ln in wrapped]
            all_intact = all(
                any(render._normalize_fact_text(n) in nl for nl in norm_lines)
                for n in names
            )
            # When an item has a price, require name AND price together on one
            # rendered line (per-row pairing the final gate will re-verify).
            pairs_intact = all(
                any(render._normalize_fact_text(f"{n} {p}") in render._normalize_fact_text(ln)
                    or (render._normalize_fact_text(n) in render._normalize_fact_text(ln)
                        and render._normalize_fact_text(p) in render._normalize_fact_text(ln))
                    for ln in wrapped)
                for n, p in items if n and p
            )
            widest_ok = all(_text_w(draw, ln, font) <= safe_w for ln in wrapped)
            if all_intact and pairs_intact and widest_ok:
                break
            if px <= min_px:
                raise render.FlyerRenderError(
                    f"menu overlay cannot fit all {len(names)} items"
                )
            px = max(min_px, px - 2)
        used_line_h = int(px * 1.4)
        block_h = len(wrapped) * used_line_h

        def _render(draw, *, x_left, y_top, width, safe_w):
            y = y_top
            for ln in wrapped:
                _draw_centered(draw, ln, font, cy=y, width=width, fill=_CREAM, shadow_dy=2)
                y += used_line_h
            return list(wrapped)

        return block_h, _render

    # two_col / two_col_compact: name (left) ... price (right) per column.
    cols = 2
    rows_n = (len(items) + cols - 1) // cols
    col_gap = int(safe_w * 0.06)
    col_w = (safe_w - col_gap) // cols

    # Preflight: shrink one shared font until EVERY row's name + price fits its
    # column width (name wrapped to ≤2 lines, with room reserved for the price).
    px = menu_px
    while True:
        font = _premium_font("menu", px)
        ok = True
        max_name_lines = 1
        for name, price in items:
            price_w = _text_w(draw, price, font) if price else 0
            name_budget = col_w - (price_w + int(col_w * 0.06) if price else 0)
            if name_budget <= int(col_w * 0.3):
                ok = False
                break
            name_lines = _wrap_premium(draw, name, "menu", px, name_budget)
            if not name_lines or len(name_lines) > 2:
                ok = False
                break
            if any(_text_w(draw, ln, font) > name_budget for ln in name_lines):
                ok = False
                break
            max_name_lines = max(max_name_lines, len(name_lines))
        if ok:
            break
        if px <= min_px:
            raise render.FlyerRenderError(
                f"menu overlay cannot fit all {len(items)} items"
            )
        px = max(min_px, px - 2)

    used_line_h = int(px * 1.4) * max_name_lines
    block_h = rows_n * used_line_h

    def _render(draw, *, x_left, y_top, width, safe_w):
        out: list[str] = []
        for idx, (name, price) in enumerate(items):
            col = idx % cols
            row = idx // cols
            cx0 = x_left + col * (col_w + col_gap)
            col_right = cx0 + col_w
            cy = y_top + row * used_line_h
            price_w = _text_w(draw, price, font) if price else 0
            name_budget = col_w - (price_w + int(col_w * 0.06) if price else 0)
            name_lines = _wrap_premium(draw, name, "menu", px, name_budget)
            # Paint the name; verify each line stays within its name budget.
            name_painted = bool(name_lines)
            ny = cy
            for ln in name_lines:
                if _text_w(draw, ln, font) > name_budget:
                    name_painted = False
                    break
                draw.text((cx0 + 2, ny + 2 - _top(draw, ln, font)), ln, font=font, fill=_SHADOW)
                draw.text((cx0, ny - _top(draw, ln, font)), ln, font=font, fill=_CREAM)
                ny += int(px * 1.4)
            # Paint the price right-aligned; verify it stays inside the column.
            price_painted = False
            if price:
                px_x = col_right - price_w
                if px_x >= cx0 + name_budget and col_right <= x_left + safe_w + 1:
                    draw.text((px_x, cy - _top(draw, price, font)), price, font=font, fill=_GOLD)
                    price_painted = True
            # INK ONLY WHAT WAS ACTUALLY PAINTED (ISSUE 2): the name when painted,
            # and the single-row "name price" pair ONLY when BOTH were painted on
            # this row (name and price share one visual row, so the combined line
            # is faithful).  If the price didn't paint, do NOT ink the pair — the
            # final _item_price_pair_blockers / coverage gate then fails closed.
            if name_painted:
                out.append(name)
            if name_painted and price_painted:
                out.append(f"{name} {price}")
        return out

    return block_h, _render


def _top(draw, text, font):
    _l, t, _r, _b = draw.textbbox((0, 0), text, font=font)
    return t
