"""premium_overlay — role-based premium font loader for Fix C deterministic renderer.

This module is the foundation for the premium deterministic flyer-text renderer.
This file (Task 1) provides only the font bundle + ``_premium_font`` loader.
Later tasks add layout solver, scrims, offer seal, and the main renderer.
"""
from __future__ import annotations

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
