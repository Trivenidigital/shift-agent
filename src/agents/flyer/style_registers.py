"""Style registers — the graduated prompt-vocabulary library (data, not machinery).

Graduation commit 1 (plan: tasks/flyer-prompt-graduation-plan.md). Evidence:
the R1->R3.5 harness arc (2026-07-03/04) — festive-premium crowned by owner
judgment; occasion composition 6/6 with fail-neutral held; intensity dial
accent/full both clean at full ornamental load. Mirrors the deployed
campaign_scene_prompts pattern: pure data + deterministic selectors + tests.

Design rules baked in:
- CLOSED vocabulary, fail-closed selection: unknown register -> default;
  unknown occasion -> NO theme (wrong-festival is a customer-relationship
  injury; abstention beats a guess); unknown intensity -> accent.
- LEAK LAW (standing rule 2026-07-04): every vocabulary ships WITH its
  forbidden-substrings entries, authored together, so the QA screen exists
  before the vocabulary's first live outing.
- Style text carries NO fact-like content (no digits, prices, phones) —
  facts belong to the locked-facts layer exclusively.
- Flag semantics = ppv1 convention: empty allowlist is DISABLED (the
  premium_overlay empty=global-on semantic is the ledgered gotcha this
  module must never reproduce).
"""
from __future__ import annotations

import os

DEFAULT_REGISTER = "festive-premium"

REGISTERS: dict[str, str] = {
    "festive-premium": (
        "ART DIRECTION - FESTIVE PREMIUM register: near-black charcoal canvas, warm spotlight food "
        "photography, luxury-menu energy. Ornamental gold corner flourishes, decorative border with "
        "South Indian scrollwork, thin gold divider under the top small line, the price inside a "
        "SCALLOPED decorative gold medallion, menu items on dark badge rows with small gold food "
        "icons.\n"
        "TYPOGRAPHY: main display text in DIMENSIONAL BEVELED-GOLD lettering - deep emboss, polished "
        "highlights, dark outline, real depth, never flat - high-contrast didone/fat-face style; top "
        "small line in small caps widely spaced; every badge row one identical typeface, size, "
        "weight. ORNAMENT DISCIPLINE: decoration frames text zones but NEVER overlaps, crowds, or "
        "distorts any text."
    ),
    "pure-festive": (
        "ART DIRECTION - FESTIVE TRADITIONAL register: lavish ornamental border framing all four "
        "edges (South Indian temple-motif or paisley scrollwork in gold); each menu item presented "
        "as a decorative badge with a small food icon; the price inside a SHAPED medallion "
        "(scalloped starburst or mandala disc) with layered gold rim and drop shadow; two-tone "
        "display typography on the main text (gold outline over deep color fill); a kolam pattern "
        "strip along the bottom band; rich saturated festive palette (deep red, saffron, gold, "
        "emerald accents); confident full-canvas density. ORNAMENT DISCIPLINE: decoration frames "
        "text zones but NEVER overlaps, crowds, or distorts any text."
    ),
    "festive-modern": (
        "ART DIRECTION - FESTIVE MODERN register: clean editorial grid fused with festive warmth. "
        "Warm cream canvas with deep-red as the single accent color; geometric border frame with "
        "ONE ornamental paisley corner element; a kolam pattern strip along the bottom band; menu "
        "items as chip rows where each chip carries a small circular icon disc with a decorated "
        "rim; the price in a bold badge with a decorative scalloped ring.\n"
        "TYPOGRAPHY: main display text as a DIMENSIONAL TWO-TONE treatment - heavy grotesque forms "
        "with exactly ONE word in deep red and subtle emboss depth, never flat; slim uppercase "
        "widely-spaced top line; display text at least 3x body size; all secondary text one clean "
        "sans. ORNAMENT DISCIPLINE: ornament stays outside text zones; every string sits on clean "
        "ground."
    ),
    "clean-modern": (
        "ART DIRECTION - CLEAN MODERN register: crisp editorial layout on a warm cream canvas; a "
        "single bold accent color drawn from the cuisine; geometric border frame with one "
        "decorative corner element; menu items as rounded chip rows with minimalist line icons; "
        "the price in a flat circular badge with a thick accent ring; generous but INTENTIONAL "
        "whitespace; bottom band as a color-blocked strip.\n"
        "TYPOGRAPHY: main display text as a HEAVY-WEIGHT GROTESQUE set huge - at least 3x any body "
        "text - with exactly ONE word in the accent color; slim uppercase widely-spaced top line; "
        "all chips share one clean sans at one size. Strong size hierarchy, no decorative faces."
    ),
    "premium-dark": (
        "ART DIRECTION - PREMIUM DARK register: near-black charcoal canvas with warm spotlight "
        "food photography; thin double-line gold border with corner flourishes; menu items on slim "
        "dark badge rows with gold keylines and small icons; subtle damask texture; bottom band as "
        "a clean gold-ruled strip; luxury-menu energy, full-canvas composition.\n"
        "TYPOGRAPHY: main display text in a high-contrast DIDONE display face with a beveled "
        "metallic-gold treatment (deep shadow, polished highlights); top small line in small caps, "
        "widely spaced, thin gold rule either side; every badge row one identical typeface, size, "
        "weight. No mixed weights inside any line. ORNAMENT DISCIPLINE: decoration frames "
        "text zones but NEVER overlaps, crowds, or distorts any text."
    ),
}

# Occasion vocabularies at both intensities. Wrong-festival is a
# customer-relationship injury: only these four keys exist; anything else
# composes NO theme.
OCCASIONS: dict[str, dict[str, str]] = {
    "july4": {
        "accent": ("OCCASION THEME - JULY 4TH at ACCENT intensity: subtle red-white-and-blue "
                   "ribbon accents and small star elements integrated with the base structure; "
                   "faint firework glow high in the corners; the register's palette stays "
                   "dominant, patriotic accents secondary."),
        "full": ("OCCASION THEME - JULY 4TH at FULL intensity (theme co-leads): the canvas shifts "
                 "to a deep NAVY with cream panels; the display text becomes a RED-AND-BLUE "
                 "two-tone dimensional treatment (keep the emboss depth and dark outline); bold "
                 "flag bunting across the top band, star-field texture in the dark areas, "
                 "prominent firework bursts in both upper corners; the price badge becomes a "
                 "STAR-SHAPED seal in red with gold rim; menu rows gain thin red-blue keylines. "
                 "Full holiday density - while ORNAMENT DISCIPLINE still holds: nothing overlaps "
                 "or crowds any text."),
    },
    "diwali": {
        "accent": ("OCCASION THEME - DIWALI at ACCENT intensity: rows of lit diya oil lamps along "
                   "the lower band, marigold garland accents at the top corners, a rangoli-detail "
                   "edge on the price element, warm amber glow over the base palette."),
        "full": ("OCCASION THEME - DIWALI at FULL intensity (theme co-leads): marigold garlands "
                 "framing top and sides, dense diya rows with visible flames along the bottom, "
                 "rangoli mandala motifs in the corners, firework sparkles in the dark areas, the "
                 "palette warmed to deep amber and saffron over the base; the price element gains "
                 "a rangoli-patterned rim. ORNAMENT DISCIPLINE still holds: nothing overlaps or "
                 "crowds any text."),
    },
    "ramadan": {
        "accent": ("OCCASION THEME - RAMADAN at ACCENT intensity: crescent-and-star accents, "
                   "elegant hanging lanterns in the upper corners, the base palette shifted "
                   "toward deep night blue with gold."),
        "full": ("OCCASION THEME - RAMADAN at FULL intensity (theme co-leads): a deep night-blue "
                 "canvas with a starry sky, ornate hanging lanterns with visible glow down both "
                 "sides, a large elegant crescent behind the display text, geometric Islamic "
                 "pattern borders in gold. ORNAMENT DISCIPLINE still holds: nothing overlaps or "
                 "crowds any text."),
    },
    "thanksgiving": {
        "accent": ("OCCASION THEME - THANKSGIVING at ACCENT intensity: autumn harvest accents "
                   "(maple leaves, small pumpkins, wheat sheaves) at the borders, the palette "
                   "warmed with amber-orange over the base."),
        "full": ("OCCASION THEME - THANKSGIVING at FULL intensity (theme co-leads): rich autumn "
                 "canvas with falling-leaf texture, pumpkin-and-gourd arrangements in the lower "
                 "corners, wheat-sheaf borders, the display text warmed to a copper-gold "
                 "dimensional treatment, cornucopia energy. ORNAMENT DISCIPLINE still holds: "
                 "nothing overlaps or crowds any text."),
    },
}

INTENSITIES = ("accent", "full")

# Leak law: the screen ships with the vocabulary. Base list covers register
# typography/ornament vocabulary; per-occasion lists cover theme vocabulary.
# All lowercase; QA compares case-insensitively.
_BASE_FORBIDDEN = [
    "beveled", "scalloped", "dimensional", "letterspaced", "didone", "grotesque",
    "emboss", "keyline", "kicker", "medallion", "badge row", "ornament", "register",
    "intensity", "typography", "art direction", "occasion theme",
    # PR #543 review F1: distinctive vocabulary from ALL registers, not just the
    # default (kolam/paisley/mandala appear in register text, not only occasion
    # lists; damask/scrollwork/starburst/temple-motif are pure prompt jargon).
    "kolam", "paisley", "mandala", "starburst", "damask", "scrollwork",
    "temple-motif", "geometric",
]
_OCCASION_FORBIDDEN: dict[str, list[str]] = {
    "july4": ["bunting", "star-field", "keylines", "two-tone", "star-shaped"],
    "diwali": ["diya", "marigold", "rangoli", "garland", "mandala"],
    "ramadan": ["crescent", "lantern", "night-blue", "islamic"],
    "thanksgiving": ["harvest", "maple", "wheat", "sheaves", "cornucopia", "gourd"],
}


def style_prompt_block(register: str, *, occasion: str | None = None,
                       intensity: str = "accent") -> str:
    """Compose the art-direction block. Fail-closed on every axis: unknown
    register -> DEFAULT_REGISTER, unknown occasion -> no theme (never a
    guessed festival), unknown intensity -> accent."""
    base = REGISTERS.get(register) or REGISTERS[DEFAULT_REGISTER]
    theme = ""
    if occasion in OCCASIONS:
        level = intensity if intensity in INTENSITIES else "accent"
        theme = OCCASIONS[occasion][level]
    return base + ("\n\n" + theme if theme else "")


def forbidden_substrings_for(register: str, *, occasion: str | None = None) -> list[str]:
    """The leak screen for a given composition — authored with the vocabulary
    (standing rule 2026-07-04), consumed by QA / candidate selection."""
    entries = list(_BASE_FORBIDDEN)
    if occasion in _OCCASION_FORBIDDEN:
        entries.extend(_OCCASION_FORBIDDEN[occasion])
    return entries


def _normalize_phone(value: str) -> str:
    """Mirror render._normalize_sender semantics (PR #543 review F3): strip any
    @-JID suffix, drop punctuation/plus, casefold — so an allowlist entry
    "+17329837841" matches a caller passing a JID or un-plussed form instead of
    silently never firing (the phantom-lever setup)."""
    v = (value or "").strip().casefold()
    if "@" in v:
        v = v.split("@", 1)[0]
    return "".join(c for c in v if c.isalnum())


def style_registers_enabled(customer_phone: str) -> bool:
    """ppv1 allowlist semantics — fail-closed: flag on AND non-empty allowlist
    AND phone membership (both sides normalized). Empty allowlist DISABLES
    (never global-on). A literal ``*`` entry graduates the feature to EVERY
    customer (incident F0217, 2026-07-11) — an EXPLICIT opt-in, never the
    empty-list flip. ``*`` is matched on the RAW entries because
    ``_normalize_phone`` strips non-alphanumerics (which would drop the ``*``)."""
    if os.environ.get("FLYER_STYLE_REGISTERS", "") != "1":
        return False
    raw_entries = [p.strip() for p in
                   os.environ.get("FLYER_STYLE_REGISTERS_ALLOWLIST", "").split(",") if p.strip()]
    if "*" in raw_entries:
        return True
    allowlist = {_normalize_phone(p) for p in raw_entries}
    allowlist.discard("")
    if not allowlist:
        return False
    return _normalize_phone(customer_phone) in allowlist
