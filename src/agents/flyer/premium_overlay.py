"""premium_overlay — role-based premium font loader for Fix C deterministic renderer.

This module is the foundation for the premium deterministic flyer-text renderer.
This file (Task 1) provides only the font bundle + ``_premium_font`` loader.
Later tasks add layout solver, scrims, offer seal, and the main renderer.
"""
from __future__ import annotations

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
