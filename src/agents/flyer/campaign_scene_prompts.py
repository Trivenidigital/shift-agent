"""Campaign-scene prompt templates for Flyer Studio image generation.

Drift-check tag: extends-Hermes.

Hermes-first analysis:
- Hermes already owns the LLM/vision gateway, image generation, skill dispatch,
  identity, WhatsApp I/O, audit, approvals, and state conventions. None of those
  are re-implemented here.
- Net-new (allowed business logic): a flyer-business-specific render-prompt
  *content* library plus a pure-function deterministic selector over already-
  resolved ``FlyerProject`` state. The output is a single prompt block injected
  into ``render._image_prompt``.
- This is NOT a routing/intent/identity/audit/state/messaging substrate: there
  is no dispatch, no new state, no new approval/audit path. Selection is a
  deterministic function over a context string; Hermes still generates the image.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CampaignSceneTemplate:
    """A reusable image-generation scene direction.

    ``scene_block`` may contain ``{business}``, ``{offer}`` and ``{audience}``
    placeholders, all filled by :func:`render_campaign_scene_block`.
    """

    key: str
    summary: str
    scene_block: str


_FAMILY_DISCOVERY = CampaignSceneTemplate(
    key="family_discovery",
    summary="Warm family/community discovering and enjoying the offer",
    scene_block=(
        "Campaign scene direction (family discovery):\n"
        "- Show a warm, aspirational scene of a happy local family or community enjoying "
        "{business}'s offering, with the hero product/dish as the focal point.\n"
        "- Inviting, celebratory mood with natural lighting and relatable local people.\n"
        "- The scene supports the offer ({offer}) for {audience} and never covers the controlled copy."
    ),
)

_HUMAN_BILLBOARD = CampaignSceneTemplate(
    key="human_billboard",
    summary="Confident human presenter spotlighting the promotion",
    scene_block=(
        "Campaign scene direction (human billboard):\n"
        "- Feature a confident, friendly presenter or staff member spotlighting the promotion "
        "for {business}, drawing the eye straight to the headline offer.\n"
        "- High-energy, attention-grabbing composition; the person frames but never covers the "
        "controlled copy or the offer ({offer}).\n"
        "- Tuned to grab the attention of {audience}."
    ),
)

_STOREFRONT_SERVICE = CampaignSceneTemplate(
    key="storefront_service",
    summary="Clean storefront/service hero (safe default)",
    scene_block=(
        "Campaign scene direction (storefront/service):\n"
        "- Present a clean, professional storefront or service hero for {business}, with the "
        "offering as the focal point.\n"
        "- Trustworthy, polished local-business mood with category-appropriate imagery.\n"
        "- The scene complements the offer ({offer}) for {audience} and never overlaps the controlled copy."
    ),
)

CAMPAIGN_SCENE_TEMPLATES: tuple[CampaignSceneTemplate, ...] = (
    _FAMILY_DISCOVERY,
    _HUMAN_BILLBOARD,
    _STOREFRONT_SERVICE,
)

_TEMPLATES_BY_KEY: dict[str, CampaignSceneTemplate] = {t.key: t for t in CAMPAIGN_SCENE_TEMPLATES}

# Deterministic selection signal sets (lowercase). Together they cover the
# selection dimensions called for in the backlog: business type, promotion type,
# event/festival type, target audience, and conversion goal.
_FAMILY_DISCOVERY_SIGNALS: frozenset[str] = frozenset({
    # event / festival type
    "festival", "celebration", "diwali", "deepavali", "holi", "navratri", "ugadi",
    "pongal", "onam", "eid", "ramadan", "christmas", "easter", "new year",
    # target audience
    "family", "families", "kids", "children", "community", "together",
})
_HUMAN_BILLBOARD_SIGNALS: frozenset[str] = frozenset({
    # promotion type / conversion goal
    "sale", "discount", "offer", "deal", "bogo", "limited", "hurry", "today only",
    "grand opening", "launch", "announcement", "promo", "promotion", "% off", "percent off",
    "book now", "call now", "sign up", "register",
    # attention-led / service business type
    "salon", "spa", "barber", "fitness", "gym", "tutor", "class", "clinic", "service",
})


def _context_has(context: str, terms: frozenset[str]) -> bool:
    """Word-boundary-aware presence check. Multi-word / punctuation terms use
    substring; single-word terms use a ``\\bterm\\b`` match (so "spa" does not
    match "space")."""
    for term in terms:
        if " " in term or "-" in term or "%" in term:
            if term in context:
                return True
        elif re.search(rf"\b{re.escape(term)}\b", context):
            return True
    return False


def select_campaign_scene(context: str) -> CampaignSceneTemplate:
    """Deterministically choose a campaign-scene template from a context string
    (e.g. ``render._category_context(project)``, which already folds in business
    category, name, style, notes, and the raw request).

    First-match-wins tie-break order — ``family_discovery`` → ``human_billboard``
    → ``storefront_service`` fallback — so the same project state always yields
    the same template.
    """
    ctx = (context or "").lower()
    if _context_has(ctx, _FAMILY_DISCOVERY_SIGNALS):
        return _FAMILY_DISCOVERY
    if _context_has(ctx, _HUMAN_BILLBOARD_SIGNALS):
        return _HUMAN_BILLBOARD
    return _STOREFRONT_SERVICE


def render_campaign_scene_block(
    template: CampaignSceneTemplate,
    *,
    business: str = "",
    offer: str = "",
    audience: str = "",
) -> str:
    """Render the template's scene block, leaving no unresolved placeholders.
    Empty inputs fall back to safe generic phrasing."""
    return template.scene_block.format(
        business=(business or "the business").strip() or "the business",
        offer=(offer or "the featured offer").strip() or "the featured offer",
        audience=(audience or "local customers").strip() or "local customers",
    )


def campaign_scene_prompt_block(
    *,
    context: str,
    business: str = "",
    offer: str = "",
    audience: str = "",
) -> str:
    """Select + render in one call. Returns a prompt-ready campaign-scene block."""
    return render_campaign_scene_block(
        select_campaign_scene(context), business=business, offer=offer, audience=audience
    )


# ─────────────────────────────────────────────────────────────────────────────
# FOOD-POSTER scene families (Premium Poster Template v1, Slice C2A).
#
# ADDITIVE: these are a SEPARATE family set + a SEPARATE selector
# (`select_food_poster_scene`) used ONLY by the premium-poster director. The
# existing `select_campaign_scene` / `CAMPAIGN_SCENE_TEMPLATES` are UNCHANGED, so
# the deployed render path is byte-identical. Each scene_block directs a TEXTLESS,
# appetizing food/background only — the no-text contract is added by the director's
# prompt builder. These templates carry NO {business}/{offer} text placeholders
# (the food scene must never render copy); facts guide FOOD STYLE only, in the
# director, never as on-image text.
# ─────────────────────────────────────────────────────────────────────────────

_FOOD_STREET_SNACK = CampaignSceneTemplate(
    key="food_street_snack",
    summary="Golden fried Indian street snacks, chutneys, steam, warm festive light",
    scene_block=(
        "a generous platter of golden, crispy fried Indian street snacks with bowls of green "
        "and tamarind chutney, light steam rising, warm festive lighting, dark rustic wood table, "
        "shallow depth of field, premium appetizing food photography"
    ),
)
_FOOD_COMBO = CampaignSceneTemplate(
    key="food_combo",
    summary="Abundant combo meal spread, warm rim light, premium",
    scene_block=(
        "an abundant combo meal spread of freshly cooked Indian dishes on dark plates, warm rim "
        "lighting, fresh garnish, rich colours, premium restaurant food photography"
    ),
)
_FOOD_DESSERT = CampaignSceneTemplate(
    key="food_dessert",
    summary="Indian sweets / mithai, gold tones, festive bokeh",
    scene_block=(
        "an elegant arrangement of Indian sweets and mithai on an ornate plate, warm gold tones, "
        "soft festive bokeh, gentle glow, premium dessert photography"
    ),
)
_FOOD_FESTIVAL = CampaignSceneTemplate(
    key="food_festival",
    summary="Festive celebratory food spread, diya/marigold ambiance",
    scene_block=(
        "a celebratory festive Indian food spread with warm diya and marigold ambiance, abundant "
        "and inviting, rich warm colours, premium festive food photography"
    ),
)
_FOOD_GRAND_OPENING = CampaignSceneTemplate(
    key="food_grand_opening",
    summary="Welcoming premium fresh spread for a grand opening",
    scene_block=(
        "a welcoming, fresh and bright premium Indian food spread arranged invitingly, clean warm "
        "lighting, celebratory abundance, premium restaurant food photography"
    ),
)
_FOOD_GENERIC = CampaignSceneTemplate(
    key="food_generic",
    summary="Safe default: appetizing premium Indian food hero",
    scene_block=(
        "an appetizing hero plate of freshly cooked Indian food, warm inviting lighting, rich "
        "colours, shallow depth of field, premium restaurant food photography"
    ),
)

FOOD_POSTER_SCENE_TEMPLATES: tuple[CampaignSceneTemplate, ...] = (
    _FOOD_STREET_SNACK, _FOOD_COMBO, _FOOD_DESSERT, _FOOD_FESTIVAL,
    _FOOD_GRAND_OPENING, _FOOD_GENERIC,
)
FOOD_POSTER_TEMPLATES_BY_KEY: dict[str, CampaignSceneTemplate] = {
    t.key: t for t in FOOD_POSTER_SCENE_TEMPLATES
}

# Selection signals (lowercase). Precedence (see select_food_poster_scene): a
# NAMED FOOD TYPE wins (dessert → combo → street-snack), because the food is the
# poster's hero; OCCASION framing (grand-opening → festival) applies only when no
# food type is named; generic food default otherwise.
_FOOD_GRAND_OPENING_SIGNALS: frozenset[str] = frozenset({
    "grand opening", "grand-opening", "opening", "launch", "now open", "new location",
})
_FOOD_FESTIVAL_SIGNALS: frozenset[str] = frozenset({
    "festival", "celebration", "diwali", "deepavali", "holi", "navratri", "ugadi",
    "pongal", "onam", "eid", "ramadan", "sankranti", "event",
})
_FOOD_DESSERT_SIGNALS: frozenset[str] = frozenset({
    "dessert", "desserts", "sweet", "sweets", "mithai", "gulab", "jamun", "jalebi",
    "barfi", "halwa", "kheer", "rasmalai", "ladoo", "laddu", "cake", "pastry",
})
_FOOD_COMBO_SIGNALS: frozenset[str] = frozenset({
    "combo", "combos", "meal", "meals", "thali", "platter", "family pack", "bucket",
})
_FOOD_STREET_SNACK_SIGNALS: frozenset[str] = frozenset({
    "snack", "snacks", "street", "bonda", "bondas", "pakora", "pakoras", "mirchi",
    "samosa", "samosas", "punugulu", "chaat", "fried", "bhajji", "vada",
})


def select_food_poster_scene(context: str) -> CampaignSceneTemplate:
    """Deterministically choose a FOOD-POSTER scene family from a context string
    (business category + name + items + occasion + raw request). A NAMED FOOD TYPE
    wins (dessert → combo → street-snack) because the food is the poster's hero;
    OCCASION framing (grand-opening → festival) applies only when no food type is
    named; generic food default otherwise. Pure; the same context always yields the
    same template. Does NOT affect `select_campaign_scene`."""
    ctx = (context or "").lower()
    if _context_has(ctx, _FOOD_DESSERT_SIGNALS):
        return _FOOD_DESSERT
    if _context_has(ctx, _FOOD_COMBO_SIGNALS):
        return _FOOD_COMBO
    if _context_has(ctx, _FOOD_STREET_SNACK_SIGNALS):
        return _FOOD_STREET_SNACK
    if _context_has(ctx, _FOOD_GRAND_OPENING_SIGNALS):
        return _FOOD_GRAND_OPENING
    if _context_has(ctx, _FOOD_FESTIVAL_SIGNALS):
        return _FOOD_FESTIVAL
    return _FOOD_GENERIC
