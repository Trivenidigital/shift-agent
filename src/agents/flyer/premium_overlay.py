"""premium_overlay — role-based premium font loader for Fix C deterministic renderer.

This module is the foundation for the premium deterministic flyer-text renderer.
This file (Task 1) provides only the font bundle + ``_premium_font`` loader.
Later tasks add layout solver, scrims, offer seal, and the main renderer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Font-bundle search path. The repo layout keeps the TTFs in a ``fonts/``
# package directory next to this module; the deployed VPS layout FLATTENS this
# module to ``/opt/shift-agent/flyer_premium_overlay.py`` and installs the
# bundle alongside it at ``/opt/shift-agent/fonts/`` (see
# shift-agent-deploy.sh). ``_FONT_DIR`` resolves to the first candidate that
# actually exists so the loader works in BOTH layouts; ``_premium_font`` also
# re-scans these candidates per call as a belt-and-suspenders fallback.
_FONT_DIR_CANDIDATES = (
    Path(__file__).resolve().parent / "fonts",   # repo / tests (package layout)
    Path("/opt/shift-agent/fonts"),              # deployed VPS (flat layout)
)


def _resolve_font_dir() -> Path:
    for candidate in _FONT_DIR_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return _FONT_DIR_CANDIDATES[0]


_FONT_DIR = _resolve_font_dir()

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
        # Primary: the module-level ``_FONT_DIR`` (honored first so tests that
        # monkeypatch it keep working). Then every other known bundle location
        # so a flat-layout box still finds the TTF even if ``_FONT_DIR`` was
        # resolved before the bundle was installed. De-dupe while preserving
        # order.
        seen: set[str] = set()
        for base in (_FONT_DIR, *_FONT_DIR_CANDIDATES):
            path = base / fn
            key = str(path)
            if key not in seen:
                seen.add(key)
                candidates.append(path)
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

# CD v2 (Slice B, B2.5): offer_priority → seal-scale multiplier. "medium" (and
# any unknown / None value) maps to 1.0 so the DEFAULT path is byte-identical to
# pre-CD-v2 output; "high" enlarges, "low" shrinks the seal.
_OFFER_PRIORITY_SCALE: dict[str, float] = {"high": 1.18, "medium": 1.0, "low": 0.82}


def _offer_priority_scale(offer_priority) -> float:
    """Map an ``offer_priority`` string to a seal-size multiplier.

    Guarded: any value that is not a recognised priority (including ``None``,
    the default ``"medium"``, or malformed input) yields ``1.0`` — the byte-
    identical-to-today scale.  Never raises."""
    try:
        return _OFFER_PRIORITY_SCALE.get((offer_priority or "").strip().lower(), 1.0)
    except (AttributeError, TypeError):
        return 1.0


@dataclass(frozen=True)
class PremiumLayout:
    menu_mode: str        # "combo" | "name_rows" | "two_col" | "two_col_compact"
    offer_mode: str       # "seal" | "inline" | "none"
    menu_font_px: int
    min_font_px: int
    # CD v2 (Slice B, B2.5): offer-energy + message-clarity levers. Defaults
    # (1.0 / "") reproduce the pre-CD-v2 PremiumLayout EXACTLY so flag-off /
    # creative_direction-absent renders are byte-identical.
    offer_scale: float = 1.0   # 1.0 == today's seal size (medium/None/default)
    narrative: str = ""        # campaign_narrative to lead with, "" == none


def plan_premium_layout(
    items, *, shared_price, width: int = 1080,
    offer_priority=None, narrative: str = "",
) -> PremiumLayout:
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
        offer_priority: CD v2 offer-energy lever ("high"/"medium"/"low" or
                      ``None``).  Maps to ``offer_scale``; default/medium/None
                      => 1.0 (byte-identical seal).
        narrative:    CD v2 campaign_narrative to lead with as a prominent top
                      element.  "" (default) => no narrative (byte-identical).

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
    return PremiumLayout(
        menu_mode=mode, offer_mode=offer, menu_font_px=font_px, min_font_px=floor,
        offer_scale=_offer_priority_scale(offer_priority),
        narrative=(narrative or "").strip() if isinstance(narrative, str) else "",
    )


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


def _seal_radius(bw, bh, width, offer_scale=1.0):
    """The seal circle radius from the sized pill + a min-pad.

    Single source of truth shared by ``_measure_offer_seal`` and
    ``draw_offer_seal`` so the reserved band and the drawn circle never drift.
    ``offer_scale`` (CD v2, B2.5) scales the radius: 1.0 (default) is BYTE-
    IDENTICAL to the pre-CD-v2 formula; >1.0 enlarges, <1.0 shrinks.  When the
    scale is exactly 1.0 the original integer expression is used verbatim so the
    seal geometry is unchanged to the pixel."""
    base = max(bw, bh) // 2 + max(8, int(width * 0.010))
    if offer_scale == 1.0:
        return base
    return int(round(base * offer_scale))


def _measure_offer_seal(draw, *, label, price, width, offer_scale=1.0):
    """Return the seal DIAMETER for the given label+price (for layout).

    The seal is now a circle whose radius is ``max(bw, bh) // 2 + pad``;
    this function returns the full diameter (2 × sr) so that callers
    (``render_premium_overlay``) reserve the correct vertical band for the
    seal and correctly position ``seal_cy`` and ``title_anchor``.  ``offer_scale``
    (CD v2) scales the diameter; 1.0 == today (byte-identical)."""
    bw, bh, *_rest = _seal_geometry(draw, label=label, price=price, width=width)
    return _seal_radius(bw, bh, width, offer_scale) * 2


def draw_offer_seal(draw, *, label, price, width, center, offer_scale=1.0):
    """Draw a prominent maroon+gold circular seal (label / price / "EACH") centred
    at ``center`` — the Editorial Luxury focal element (Fix C v2).

    Geometry: a circle whose radius is derived from the existing ``_seal_geometry``
    sizing (keeps the label+price fit invariant).  ``offer_scale`` (CD v2, B2.5)
    scales the circle: 1.0 (default) is BYTE-IDENTICAL to the pre-CD-v2 seal;
    "high" offer_priority passes >1.0 (larger/bolder), "low" passes <1.0.  Palette
    mirrors compose_A() in fixc-v2-mockup-generator.py: maroon fill (120,24,28),
    GOLD ring (208,178,110), IVORY price text (244,240,232).

    Three text lines inside the circle, top→bottom:
      • label   — letter-spaced small-caps (Cormorant / kicker font)  GOLD
      • price   — large display (Playfair-Black / offer_price font)   IVORY
      • "EACH"  — letter-spaced small-caps (Cormorant / kicker font)  GOLD

    Returns the seal bounding box ``(x0, y0, x1, y1)``."""
    _SEAL_MAROON = (120, 24, 28, 255)        # maroon fill — compose_A reference
    _SEAL_GOLD   = (208, 178, 110, 255)      # GOLD — compose_A reference
    _SEAL_IVORY  = (244, 240, 232, 255)      # IVORY — compose_A reference
    _SEAL_SHADOW = (0, 0, 0, 120)            # drop shadow behind the circle

    bw, bh, label_lines, lf, pf, pw, ph, pad_x, pad_y, gap, label_h = _seal_geometry(
        draw, label=label, price=price, width=width
    )
    cx, cy = center
    # Radius: half the larger dimension of the sized pill, with a minimum so
    # the circle never collapses to a tiny dot.  The bounding box is square
    # (2r × 2r) — larger than the pill when the pill is taller than it is wide.
    # ``offer_scale`` scales it (1.0 == byte-identical to today).  The text
    # offsets below are all derived from ``sr`` so they scale proportionally and
    # the label+price always stays inside the (now larger/smaller) circle.
    sr = _seal_radius(bw, bh, width, offer_scale)

    x0, y0 = cx - sr, cy - sr
    x1, y1 = cx + sr, cy + sr

    # Drop shadow (offset 5,5 so the circle lifts off the food background).
    draw.ellipse((x0 + 5, y0 + 5, x1 + 5, y1 + 5), fill=_SEAL_SHADOW)
    # Maroon fill + gold ring — compose_A: fill=(120,24,28) outline=GOLD width=4
    draw.ellipse((x0, y0, x1, y1), fill=_SEAL_MAROON, outline=_SEAL_GOLD, width=4)

    # --- text layout inside the circle ---
    # Vertical rhythm (mirrors compose_A offsets scaled from the 1080 reference):
    #   label  at  cy - 58   (scaled: ~0.054 × height_1350)
    #   price  at  cy - 28   (scaled: ~0.026 × height_1350)
    #   "EACH" at  cy + 46   (scaled: ~0.034 × height_1350)
    # We reproduce the offsets proportionally from the half-radius so they always
    # sit inside the circle regardless of the actual sr.
    label_cy_off  = int(sr * 0.60)   # label  top above cy
    price_cy_off  = int(sr * 0.29)   # price  top above cy
    each_cy_off   = int(sr * 0.48)   # "EACH" top below  cy

    # Letter-spaced label (character-by-character, like center_spaced in compose_A).
    def _draw_letter_spaced(text, font, fill, y_top, extra=4):
        """Draw letter-spaced text centred at cx; returns nothing."""
        chars = list(text.upper())
        total_w = sum(
            (draw.textbbox((0, 0), ch, font=font)[2] - draw.textbbox((0, 0), ch, font=font)[0]) + extra
            for ch in chars
        ) - extra
        x = cx - total_w // 2
        for ch in chars:
            bl, bt, br, bb = draw.textbbox((0, 0), ch, font=font)
            # shadow
            draw.text((x + 1 - bl, y_top + 1 - bt), ch, font=font, fill=_SEAL_SHADOW)
            draw.text((x - bl, y_top - bt), ch, font=font, fill=fill)
            x += (br - bl) + extra

    # Label — letter-spaced, GOLD, above price.
    for i, ln in enumerate(label_lines):
        _draw_letter_spaced(ln, lf, _SEAL_GOLD,
                            cy - label_cy_off + i * int(lf.size * 1.18))

    # Price — centred, IVORY, large Playfair-Black.
    pl, pt, pr, pb = draw.textbbox((0, 0), price, font=pf)
    _pw, _ph = pr - pl, pb - pt
    price_x = cx - _pw // 2 - pl
    price_y = cy - price_cy_off - pt
    draw.text((price_x + 2, price_y + 2), price, font=pf, fill=_SEAL_SHADOW)
    draw.text((price_x, price_y), price, font=pf, fill=_SEAL_IVORY)

    # "EACH" — letter-spaced, GOLD, below price.
    each_font = lf   # same kicker/Cormorant font at the same size
    _draw_letter_spaced("EACH", each_font, _SEAL_GOLD, cy + each_cy_off)

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
    # Try the FLAT deployed module names first (the VPS installs these modules
    # to /opt/shift-agent/ as flyer_render.py / flyer_visual_qa.py), then fall
    # back to the package layout used by the repo + tests. Mirrors the
    # try/except convention in generate-flyer-concepts + render.py. Without the
    # flat branch, the package import raises ImportError on the box and the
    # FLYER_PREMIUM_OVERLAY path would never run in production.
    try:
        import flyer_render as render            # box (flat layout)
        # Reuse the referee's OWN matching helpers so the fail-closed contract
        # is identical to visual_qa by construction.
        import flyer_visual_qa as vqa
    except ImportError:
        from agents.flyer import render          # repo / tests (package layout)
        from agents.flyer import visual_qa as vqa

    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = size

    # CD v2 (Slice B, B2.5): when a resolved creative direction is carried on the
    # project (flag-on only; serialized FlyerProject field that survives into the
    # /usr/bin/python3 overlay subprocess), lead with the MARKETING MESSAGE.
    # ALL reads are guarded — a missing/None/malformed carrier yields the
    # byte-identical-to-today values (narrative="", scale 1.0, no new pixels).
    cd = getattr(project, "creative_direction", None)
    cd_narrative = ""
    cd_offer_priority = None
    if isinstance(cd, dict):
        _raw_narr = cd.get("campaign_narrative")
        if isinstance(_raw_narr, str):
            cd_narrative = _raw_narr.strip()
        _raw_pri = cd.get("offer_priority")
        if isinstance(_raw_pri, str):
            cd_offer_priority = _raw_pri

    # FIX 2 observability: reset the per-call narrative-drop record (see the
    # _LAST_NARRATIVE_DROP module note + the dispatch at the end of this fn).
    _LAST_NARRATIVE_DROP.clear()

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
        offer_priority=cd_offer_priority,
        narrative=cd_narrative,
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

    # ===================================================================
    # CD v2 (Slice B, FIX 2 — Codex MAJOR): the campaign_narrative is BEST-EFFORT.
    # Including it must NEVER degrade the premium overlay to flat. The whole
    # compose→fit→draw→verify→save sequence is wrapped in ``_compose`` so it can
    # be attempted WITH the narrative and, if the REQUIRED content (title/menu/
    # seal/footer + required-fact ledger) does not fit WITH it, RETRIED WITHOUT
    # the narrative before degrading. Each attempt rebuilds a fresh image + ink
    # log from scratch, so a failed first attempt leaves no partial pixels.
    #
    # Flag-off / empty narrative ⇒ ``cd_narrative == ""`` ⇒ the narrative block is
    # a no-op on the FIRST attempt AND the retry is never engaged (only triggered
    # when a narrative was actually present) ⇒ a SINGLE attempt byte-identical to
    # today. The required-fact ledger is verified on EVERY attempt unchanged.
    # ===================================================================
    def _compose(include_narrative: bool) -> None:
        # ``ink`` accumulates the RAW text of every string actually rendered, one
        # entry per visual line/row, so the final coverage check is grounded in real
        # pixels AND so the referee's line-wise item name+price pairing works.
        # Rebuilt per attempt so a retry never inherits the dropped attempt's text.
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
        # TOP ZONE — editorial brand lockup: emblem ring + monogram + brand
        #   Mirrors compose_A() in fixc-v2-mockup-generator.py:
        #     • gold ellipse ring (cx±34, top 56→124 in a 1080×1350 canvas)
        #     • monogram (brand initials) centred in the ring — Playfair-Black
        #     • brand name below in letter-spaced small-caps — Cormorant/masthead
        #   Palette: GOLD=(208,178,110,255)  IVORY=(244,240,232,255) (approved mockup)
        #   Replaces old kicker + hairline; brand is still `_ink`'d (coverage kept).
        # ===================================================================
        _EMBLEM_GOLD  = (208, 178, 110, 255)   # approved mockup gold (≠ legacy _GOLD)
        _EMBLEM_IVORY = (244, 240, 232, 255)   # approved mockup ivory

        cx_top = width // 2
        # Scale ring geometry from the 1080-wide mockup reference.
        ring_half = max(28, int(width * 0.0315))   # ≈34px @1080
        ring_top  = int(height * 0.0415)           # ≈56px @1350
        ring_bot  = ring_top + ring_half * 2       # ≈124px @1350 (height=68px)

        draw.ellipse(
            (cx_top - ring_half, ring_top, cx_top + ring_half, ring_bot),
            outline=_EMBLEM_GOLD,
            width=3,
        )

        # Monogram: Playfair-Black at ~38px; centred vertically inside the ring.
        mono_px  = max(min_px, int(width * 0.0352))   # ≈38px @1080
        mono_font = _premium_font("title", mono_px)    # "title" role → PlayfairDisplay-Black
        monogram  = _brand_monogram(business) if business else ""
        if monogram:
            mono_cy = ring_top + (ring_bot - ring_top - mono_px) // 2
            _draw_centered(draw, monogram, mono_font,
                           cy=mono_cy, width=width,
                           fill=_EMBLEM_GOLD, shadow=None)

        # Brand name: letter-spaced small-caps below the ring.
        y = ring_bot + max(10, int(height * 0.010))
        if business:
            brand_px   = max(min_px, int(width * 0.0315))   # ≈34px @1080
            brand_font = _premium_font("masthead", brand_px)
            brand_text = _spaced_caps(business)
            # Inline letter-spacing: interleave extra thin spaces between characters
            # to replicate the `center_spaced(..., extra=6)` effect in compose_A.
            brand_spaced = " ".join(brand_text)        # thin space ≈ CSS letter-spacing
            brand_lines  = _wrap_premium(draw, brand_spaced, "masthead", brand_px, safe_w)
            for ln in brand_lines:
                _draw_centered(draw, ln, brand_font,
                               cy=y, width=width,
                               fill=_EMBLEM_IVORY, shadow_dy=2)
                y += int(brand_px * 1.18)
            # INVARIANT: log the ORIGINAL business value so the coverage ledger
            # (_covered / _value_present_in) sees the locked fact regardless of the
            # display transformation (upper-casing, thin-space insertion, wrapping).
            _ink(business)
        top_zone_bottom = y

        # ===================================================================
        # CD v2 (Slice B, B2.5) — CAMPAIGN NARRATIVE eyebrow.
        #
        # The message-clarity lever: when a resolved creative direction carries a
        # ``campaign_narrative``, render it as a PROMINENT enlarged kicker/eyebrow
        # directly below the brand lockup so the customer reads the MARKETING MESSAGE
        # first.  Montserrat-Bold (kicker role), tracked small-caps, GOLD — visually
        # distinct from the cream Playfair title below it.
        #
        # Best-effort + flag-off byte-identical:
        #   • ``cd_narrative == ""`` (carrier absent / blank) ⇒ this whole block is a
        #     no-op (NO pixels, top_zone_bottom unchanged) → byte-identical to today.
        #   • The narrative is NEVER a required fact: if it cannot fit its bounded
        #     band (shrink-to-floor fails), it is DROPPED — never raised, never
        #     allowed to push a required fact off the canvas.
        # ===================================================================
        if cd_narrative and include_narrative:
            _narr_gap = max(8, int(height * 0.010))
            _narr_top = top_zone_bottom + _narr_gap
            # Bounded band: keep the narrative in the upper ~26% so the Playfair
            # title still has its own band below it. The narrative shrinks/drops
            # before it would intrude on the title zone.
            _narr_ceiling = max(_narr_top, int(height * 0.26))
            _narr_px = max(min_px, int(width * 0.030))   # enlarged eyebrow (~32px @1080)
            _narr_lines, _narr_px = _fit_role_block(
                draw, cd_narrative, "kicker", _narr_px, safe_w, min_px,
                max_height=max(0, _narr_ceiling - _narr_top),
                line_factor=1.22, max_lines=3,
            )
            if _narr_lines:
                _narr_font = _premium_font("kicker", _narr_px)
                ny = _narr_top
                for ln in _narr_lines:
                    _draw_centered(draw, ln, _narr_font, cy=ny, width=width,
                                   fill=_EMBLEM_GOLD, shadow_dy=2)
                    ny += int(_narr_px * 1.22)
                # Log the ORIGINAL narrative so any future coverage check sees it;
                # the narrative itself is optional, so this never gates fail-closed.
                _ink(cd_narrative)
                top_zone_bottom = ny

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
        # TITLE — Playfair-Black headline, anchored in the UPPER zone just
        # below the brand/emblem block.  Mirrors compose_A() in the reference
        # mockup where the title sits at y≈210 (upper third of a 1350-tall
        # canvas), well above the food hero mid-section.
        #
        # Upper band: title_top just below top_zone_bottom; max_height gives
        # about 32% of canvas height for the headline + rules, keeping the
        # seal + menu free to float/fill the lower two-thirds.
        # ===================================================================
        _title_gap   = max(8, int(height * 0.012))         # gap below brand block
        title_top    = top_zone_bottom + _title_gap         # where title block starts
        _upper_limit = title_top + max(0, int(height * 0.32) - title_top)  # upper band ceiling
        title_bottom = title_top  # will be updated below if title is drawn
        if title:
            title_px = max(min_px, int(width * 0.072))
            # Shrink-to-fit within the upper band so the headline never overruns
            # into the food-hero middle section reserved for the seal.
            title_lines, title_px = _fit_title(
                draw, title, title_px, safe_w, min_px,
                max_height=_upper_limit - title_top - 8,
                line_factor=1.0,
            )
            if title_lines is None:
                raise render.FlyerRenderError("premium overlay does not fit")
            title_font = _premium_font("title", title_px)
            ty = title_top
            for ln in title_lines:
                _draw_centered(draw, ln, title_font, cy=ty, width=width,
                               fill=_TITLE_CREAM, shadow_dy=4)
                ty += int(title_px * 1.0)
            _ink(title)
            title_bottom = ty  # bottom of last title line

            # ---------------------------------------------------------------
            # Decorative gold rules flanking the title (Fix C v2 Editorial).
            # Mirrors compose_A() in fixc-v2-mockup-generator.py:
            #   d.line((cx-250,rule_y, cx-90,rule_y), fill=GOLD, width=2)
            #   d.line((cx+90, rule_y, cx+250,rule_y), fill=GOLD, width=2)
            #   d.ellipse((cx-4,rule_y-4, cx+4,rule_y+4), fill=GOLD)
            # Scale gap (90px) and reach (250px) from the 1080-wide reference.
            # Placed 8px below the last title line so the rules sit just
            # beneath the headline.
            # _EMBLEM_GOLD and cx_top are defined in the TOP ZONE block above.
            # ---------------------------------------------------------------
            _rule_gap   = max(40, int(width * 90 / 1080))   # ≈90px @1080
            _rule_reach = max(80, int(width * 250 / 1080))  # ≈250px @1080
            _rule_y = title_bottom + 8  # 8px below last title line
            _dot_r = 4        # 4px radius dot (8×8 bounding box)
            if _rule_y < height - margin:   # safety: don't draw outside canvas
                # left rule
                draw.line(
                    (cx_top - _rule_reach, _rule_y, cx_top - _rule_gap, _rule_y),
                    fill=_EMBLEM_GOLD, width=2,
                )
                # right rule
                draw.line(
                    (cx_top + _rule_gap, _rule_y, cx_top + _rule_reach, _rule_y),
                    fill=_EMBLEM_GOLD, width=2,
                )
                # center dot
                draw.ellipse(
                    (cx_top - _dot_r, _rule_y - _dot_r,
                     cx_top + _dot_r, _rule_y + _dot_r),
                    fill=_EMBLEM_GOLD,
                )
            title_bottom = _rule_y + _dot_r + 4  # include the rule+dot height

        # ===================================================================
        # OFFER SEAL — gold circle floating in the right-middle over the food
        # hero, between the title block (upper) and the menu (lower).
        #
        # Change 1: seal_planned is True whenever shared_offer_price is
        # non-empty, regardless of offer_mode.  The "Any item $7.99" hook must
        # appear as the prominent visual accent even when items carry per-item
        # prices (offer_mode=="inline").
        #
        # Change 2: seal floats at the vertical MIDPOINT of the gap between
        # title_bottom (upper anchor) and menu_top (lower anchor), horizontally
        # right-of-centre — mirroring compose_A() sx=W-175, sy=600.
        #
        # Fail-closed: if the seal cannot fit its band (box out-of-bounds), it
        # is SKIPPED and the offer coverage falls through to the secondary line
        # (Change 4).  FlyerRenderError is raised only if NEITHER the seal
        # NOR the secondary line can cover the pricing_structure fact (Change 5).
        # ===================================================================
        seal_label = (shared_offer_label or "OFFER").strip()
        # Change 1: show seal whenever there is a shared offer price — not
        # gated on layout.offer_mode so "inline" briefs also get the seal.
        seal_planned = bool(shared_offer_price)
        seal_drawn = False   # set True only when draw_offer_seal succeeds in bounds
        seal_h = 0
        seal_cy = 0
        if seal_planned:
            # CD v2 (B2.5): offer_priority scales the seal (1.0 == today). Sizing,
            # band reservation and the drawn circle all use the SAME scale so they
            # never drift.
            seal_h = _measure_offer_seal(draw, label=seal_label, price=shared_offer_price,
                                         width=width, offer_scale=layout.offer_scale)
            # Change 2: float vertically in the gap between title_bottom and menu_top.
            seal_cy = (title_bottom + menu_top) // 2

        # Draw the seal AFTER the title so its gold border sits cleanly on top.
        # Placement: right side, mirroring compose_A() which places the seal at
        # sx=W-175, sy=600 on a 1080×1350 canvas.  We offset cx so the right
        # edge sits at (width - margin), keeping it inside the canvas.
        if seal_h:
            # seal_h is already the circle diameter (2×sr) returned by
            # _measure_offer_seal, so the radius is simply half of that.
            _seal_r_est = seal_h // 2
            _seal_cx = width - margin - _seal_r_est
            # Safety clamp: never let the seal overlap the left half of the canvas.
            _seal_cx = max(width // 2 + _seal_r_est + margin, _seal_cx)
            # PREFLIGHT (Codex BLOCKER fix): compute the seal's bounding box from its
            # centre + radius and verify it fits BEFORE drawing any pixels. Drawing
            # first and rejecting after would leave stray seal pixels on the flyer
            # while the secondary line ALSO draws the offer (double-draw). The seal is
            # a circle of radius _seal_r_est centred at (_seal_cx, seal_cy); a small
            # pad covers the drop shadow.
            _shadow_pad = max(4, int(width * 0.006))
            _pf = (
                _seal_cx - _seal_r_est,
                seal_cy - _seal_r_est,
                _seal_cx + _seal_r_est + _shadow_pad,
                seal_cy + _seal_r_est + _shadow_pad,
            )
            # Fit: fully below title_bottom, above menu_top, within the canvas.
            if (_pf[1] >= title_bottom and _pf[3] <= menu_top
                    and _pf[0] >= 0 and _pf[2] <= width):
                draw_offer_seal(draw, label=seal_label, price=shared_offer_price,
                                width=width, center=(_seal_cx, seal_cy),
                                offer_scale=layout.offer_scale)
                # Ink the label, the price, AND their combined form (the circle
                # stacks label directly above price, so the combined "label price"
                # string is visibly present) — this covers a ``pricing_structure``
                # fact whose value is the whole "Any item ... $7.99" phrase.
                _ink(seal_label)
                _ink(shared_offer_price)
                _ink(f"{seal_label} {shared_offer_price}")
                if shared_offer_text:
                    _ink(shared_offer_text)
                seal_drawn = True
            # If the seal did NOT fit, NO seal pixels were drawn and seal_drawn stays
            # False — the secondary line path will place the offer (fail-closed
            # fallback), with no stray seal and no double-draw.

        # ===================================================================
        # SECONDARY LINES — facts that no region above placed (offer-without-seal,
        # promotion_end, taglines, source_required_text, any other required fact ID
        # this renderer doesn't have a dedicated region for) PLUS best-effort
        # optional extras.  Each line is preflighted as a whole block (the MINOR
        # fix): we draw it only if ALL its wrapped lines fit the band — never
        # partially.  Required lines that cannot fit are caught by the coverage
        # check below (fail-closed); optional extras are simply skipped.
        #
        # Change 3: secondary lines now occupy the band BELOW the title block
        # (title_bottom + gap) up to menu_top.  The seal floats right-of-centre
        # in the same zone; secondary text is centred and typically short, so
        # visual overlap is rare and the editorial composition is preserved.
        # ===================================================================
        secondary_y = title_bottom + max(6, int(height * 0.008))
        sec_px = max(min_px, int(width * 0.020))
        sec_font = _premium_font("footer", sec_px)
        sec_line_h = int(sec_px * 1.4)

        def _draw_secondary(text, *, fill) -> bool:
            """Draw a centred secondary block ONLY if the whole wrapped block fits
            the band between title_bottom and menu_top; returns True (and inks it)
            if drawn, else False with no pixels mutated."""
            nonlocal secondary_y
            if not text:
                return False
            lines = _wrap_premium(draw, text, "footer", sec_px, safe_w)
            if not lines:
                return False
            block_h = len(lines) * sec_line_h
            if secondary_y + block_h > menu_top:
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
        # check fails closed.
        #
        # Change 4: offer-class facts are SKIPPED when the seal already drew
        # them (seal_drawn=True) to avoid a duplicate "Any item $7.99" line.
        # When the seal DID NOT draw (seal_drawn=False), the offer falls through
        # to this secondary path as the coverage fallback (Change 5).
        #
        # Item facts are excluded — they belong to the menu region and their
        # coverage/pairing is enforced by the final gate.
        _offer_norm = render._normalize_fact_text(shared_offer_text or f"{seal_label} {shared_offer_price}")
        for fid, label, value in required_facts:
            if re.match(r"^item:\d+:(name|price)$", fid):
                continue
            if _covered(fid, label, value):
                continue
            is_offer = fid == "pricing_structure" or fid.startswith("offer:") \
                or render._normalize_fact_text(value) == _offer_norm
            # Change 4: if the seal drew the offer, skip the secondary offer line
            # to avoid duplication.  If the seal did NOT draw (seal_drawn=False),
            # allow the secondary to handle it so the offer stays covered.
            if is_offer and seal_drawn:
                continue  # seal already covers this; do NOT double-draw
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

    # ===================================================================
    # FIX 2 dispatch — narrative is best-effort, never a flat-degrade trigger.
    #   • No narrative (flag-off / blank) ⇒ a SINGLE attempt, byte-identical to
    #     today (the retry branch is never reached because cd_narrative is "").
    #   • Narrative present ⇒ attempt WITH it; if the REQUIRED content does not
    #     fit (any FlyerRenderError from the menu/title/seal fit or the required-
    #     fact ledger), DROP the narrative and retry WITHOUT it. Only if the
    #     layout fails EVEN WITHOUT the narrative does the error propagate (the
    #     existing raise/degrade-to-flat path, unchanged). Each attempt rebuilds
    #     a fresh image, so the dropped attempt leaves no partial pixels.
    # ===================================================================
    if not cd_narrative:
        _compose(include_narrative=False)
        return
    try:
        _compose(include_narrative=True)
    except render.FlyerRenderError:
        # The required content did not fit WITH the narrative. Retry without it
        # before degrading to flat — the narrative is never a required fact.
        _LAST_NARRATIVE_DROP.append(True)
        _compose(include_narrative=False)


# Observability for FIX 2 (CD v2 Slice B): records, per render_premium_overlay
# call, whether the campaign_narrative had to be DROPPED on retry because the
# required content did not fit with it. A list (append-only within a call) so a
# test can assert the drop-not-degrade retry actually fired; the production
# caller does not depend on it. Cleared at the top of each call.
_LAST_NARRATIVE_DROP: list[bool] = []


# --- Template-A draw/measure helpers ---------------------------------------

def _brand_monogram(business: str) -> str:
    """Return the 1–2 capital initials used in the emblem ring above the brand name.

    Takes the first letter of each of the first two words (after stripping
    non-alpha characters) in upper-case.  Single-word brands get a single
    initial.  Short noise fragments from apostrophe-stripping (e.g. "s" in
    "Lakshmi's") are excluded by requiring words of 2+ characters.

    Examples:
        "Lakshmi's Kitchen" → "LK"
        "Dosa"              → "D"
        "Taj Mahal Grill"   → "TM"
    """
    import re as _re
    words = [w for w in _re.sub(r"[^A-Za-z ]", " ", business or "").split()
             if len(w) >= 2]
    if not words:
        return ((business or "").strip()[:1] or "·").upper()
    return "".join(w[0] for w in words[:2]).upper()


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


def _fit_role_block(draw, text, role, start_px, max_width, min_px, *, max_height, line_factor, max_lines):
    """Role-generic shrink-to-fit for a short text block (CD v2 narrative eyebrow).

    Like ``_fit_title`` but wraps/measures with the GIVEN *role*'s font (so a
    kicker eyebrow is measured with the kicker font, not the title font).  Returns
    ``(lines, font_px)`` if the wrapped block fits *max_width* × *max_height* in
    ≤ *max_lines* lines, else ``(None, start_px)``.  Best-effort by contract — the
    caller DROPS the block on ``None`` (the narrative is never a required fact)."""
    if not text or max_height <= 0:
        return None, start_px
    px = start_px
    while px >= min_px:
        lines = _wrap_premium(draw, text, role, px, max_width)
        if lines and len(lines) <= max_lines and len(lines) * int(px * line_factor) <= max_height:
            return lines, px
        px -= 2
    lines = _wrap_premium(draw, text, role, min_px, max_width)
    if lines and len(lines) <= max_lines and len(lines) * int(min_px * line_factor) <= max_height:
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

    # two_col / two_col_compact: editorial dot-leader layout (Fix C v2).
    # Name: Cormorant SemiBold (menu role) in IVORY; price: Playfair Bold
    # (masthead role) in GOLD — right-aligned.  Dot leaders fill the gap.
    # Mirrors compose_A() in fixc-v2-mockup-generator.py.
    _EDL_IVORY = (244, 240, 232, 255)  # IVORY — approved mockup
    _EDL_GOLD  = (208, 178, 110, 255)  # GOLD  — approved mockup
    _EDL_DOT   = (150, 140, 120, 255)  # muted gold dot leader

    cols = 2
    rows_n = (len(items) + cols - 1) // cols
    col_gap = int(safe_w * 0.06)
    col_w = (safe_w - col_gap) // cols

    # Preflight: shrink one shared font size until EVERY row's name + price fits
    # its column width with both fonts (name: Cormorant; price: Playfair Bold).
    # Price font is scaled proportionally (price_px = px * 34/40 per mockup ratio).
    px = menu_px
    while True:
        name_font = _premium_font("menu", px)
        price_px  = max(min_px, int(px * 34 / 40))
        price_font = _premium_font("masthead", price_px)
        ok = True
        max_name_lines = 1
        for name, price in items:
            price_w = _text_w(draw, price, price_font) if price else 0
            # gap between last name char and price: name_end + 14px + dots + 14px + price
            leader_gap = 28 if price else 0  # 14px each side
            name_budget = col_w - (price_w + leader_gap if price else 0)
            if name_budget <= int(col_w * 0.3):
                ok = False
                break
            name_lines = _wrap_premium(draw, name, "menu", px, name_budget)
            if not name_lines or len(name_lines) > 2:
                ok = False
                break
            if any(_text_w(draw, ln, name_font) > name_budget for ln in name_lines):
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

    # Row height: name font drives height; add a dot-leader row below the name
    # baseline (mirrors compose_A yy2 = yy+44 for a 40px font).
    name_row_h  = int(px * 1.4)
    dot_row_off = max(30, int(px * 44 / 40))  # ≈44px @40px font
    used_line_h = name_row_h * max_name_lines
    block_h     = rows_n * used_line_h

    def _render(draw, *, x_left, y_top, width, safe_w):
        # Recompute fonts inside the closure (captured px, price_px are correct).
        nf = _premium_font("menu", px)
        pf = _premium_font("masthead", price_px)
        out: list[str] = []
        for idx, (name, price) in enumerate(items):
            col = idx % cols
            row = idx // cols
            cx0      = x_left + col * (col_w + col_gap)
            col_right = cx0 + col_w
            cy       = y_top + row * used_line_h
            price_w  = _text_w(draw, price, pf) if price else 0
            leader_gap = 28 if price else 0
            name_budget = col_w - (price_w + leader_gap if price else 0)
            name_lines  = _wrap_premium(draw, name, "menu", px, name_budget)

            # Paint the name in IVORY (Cormorant); verify each line stays within budget.
            name_painted = bool(name_lines)
            ny = cy
            last_name_w = 0
            for ln in name_lines:
                lw = _text_w(draw, ln, nf)
                if lw > name_budget:
                    name_painted = False
                    break
                draw.text((cx0 + 2, ny + 2 - _top(draw, ln, nf)), ln, font=nf, fill=_SHADOW)
                draw.text((cx0, ny - _top(draw, ln, nf)), ln, font=nf, fill=_EDL_IVORY)
                last_name_w = lw
                ny += name_row_h

            # Paint the price in GOLD (Playfair Bold); right-aligned within column.
            price_painted = False
            if price:
                px_x = col_right - price_w
                if px_x >= cx0 + name_budget and col_right <= x_left + safe_w + 1:
                    draw.text((px_x + 2, cy + 2 - _top(draw, price, pf)), price, font=pf, fill=_SHADOW)
                    draw.text((px_x, cy - _top(draw, price, pf)), price, font=pf, fill=_EDL_GOLD)
                    price_painted = True

            # Dot leader: rendered at (cy + dot_row_off) between name end and price.
            # Only when BOTH name and price were painted (mirrors compose_A logic).
            if name_painted and price_painted:
                lx = cx0 + last_name_w + 14   # 14px gap after name
                rx = col_right - price_w - 14  # 14px gap before price
                yy2 = cy + dot_row_off
                if lx < rx and 0 < yy2 < (y_top + block_h + dot_row_off + 4):
                    x = lx
                    while x < rx:
                        draw.ellipse(
                            (x, yy2, x + 2, yy2 + 2),
                            fill=_EDL_DOT,
                        )
                        x += 12

            # INK ONLY WHAT WAS ACTUALLY PAINTED — name when painted, and the
            # combined "name price" string ONLY when BOTH painted (so the
            # _item_price_pair_blockers pairing check passes per-row without
            # relying on a separately-inked price string).
            if name_painted:
                out.append(name)
            if name_painted and price_painted:
                out.append(f"{name} {price}")
        return out

    return block_h, _render


def _top(draw, text, font):
    _l, t, _r, _b = draw.textbbox((0, 0), text, font=font)
    return t
